#!/usr/bin/env python3
"""Prove each language consumer's check actually bites.

`verify_polyglot.py` shows that fourteen consumers pass against a correct
vector. That is only half an argument. A consumer that printed its
postcondition unconditionally would pass too, which is exactly the decorative
participation the fourteen-language invariant exists to prevent.

So this verifier breaks the thing each language owns, one language at a time,
and requires that language to refuse. A consumer that still passes against a
vector violating its own invariant is reported as `DECORATIVE`, which is a
failure.

One detail makes the difference between a real test and a test that passes for
the wrong reason. Nine of the fourteen consumers also verify the vector's
SHA-256 against a declared digest. Mutating the vector would break that digest,
so those consumers would refuse -- but for the digest, not for the invariant,
and the invariant would remain untested. Each mutation therefore *recomputes*
the declared digest so the digest check still passes and the owned invariant is
the only thing left to fail.

Requires the same toolchains as `verify_polyglot.py`. A language whose toolchain
is absent is reported `UNKNOWN`, never assumed sound.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Each mutation: the vector file to damage, and a function damaging the bytes in
# the one way that language's invariant forbids. Nothing else may change.
MUTATIONS: dict[str, tuple[str, str]] = {
    "C": ("envelope-001", "insert a space after the opening brace"),
    "C++": ("envelope-001", "append a second trailing newline"),
    "Java": ("envelope-001", "swap two adjacent top-level keys out of order"),
    "Python": ("envelope-001", "turn an integer payload value into a float"),
    "JavaScript": ("envelope-001", "replace a payload value with a bare null"),
    "Go": ("envelope-001", "set origin to a value outside U, M, P, E"),
    "Rust": ("envelope-002-unicode-keys", "escape a raw non-ASCII character as \\uXXXX"),
    "TypeScript": ("envelope-001", "bump schema_version to an unrecognised value"),
    "Kotlin": ("envelope-001", "set epoch to a negative number"),
    "Swift": ("envelope-001", "upper-case the payload_digest hex"),
    "PHP": ("envelope-003-effect", "remove authority_envelope_digest from an effect record"),
    "Ruby": ("envelope-003-effect", "remove persistence_class from an effect record"),
    "C#": ("envelope-001", "set epistemic_status to a grade outside the closed set of four"),
    "Dart": ("envelope-002-unicode-keys", "swap two non-ASCII payload keys out of order"),
}


def drop_field(text: str, key: str) -> str:
    """Remove one top-level string field, leaving valid JSON behind.

    Canonical form has no insignificant whitespace, so the field runs from its
    opening quote to the comma after its closing quote. Removing both keeps the
    surrounding object well formed.
    """
    marker = f'"{key}":"'
    start = text.index(marker)
    value_end = text.index('"', start + len(marker))
    # Consume the separating comma so the object does not end up with two.
    cut = value_end + 1
    if cut < len(text) and text[cut] == ",":
        cut += 1
    return text[:start] + text[cut:]


def mutate(language: str, text: str) -> str:
    """Return the vector text damaged in exactly the way ``language`` forbids."""
    if language == "C":
        return text.replace("{", "{ ", 1)
    if language == "C++":
        return text + "\n"
    if language == "Java":
        # attempt_identity sorts before causal_parents; swapping the key names
        # leaves valid JSON whose top-level order is descending at that point.
        return text.replace('"attempt_identity"', '"zzz_attempt_identity"', 1)
    if language == "Python":
        return text.replace('"phase":1', '"phase":1.5', 1)
    if language == "JavaScript":
        return text.replace('"kind":"plan.accepted"', '"kind":null', 1)
    if language == "Go":
        return text.replace('"origin":"U"', '"origin":"X"', 1)
    if language == "Rust":
        return text.replace("Ω", "\\u03a9", 1)
    if language == "TypeScript":
        return text.replace('"b1-event-envelope-1"', '"b1-event-envelope-99"', 1)
    if language == "Kotlin":
        return text.replace('"epoch":1', '"epoch":-1', 1)
    if language == "Swift":
        marker = '"payload_digest":"'
        at = text.index(marker) + len(marker)
        end = text.index('"', at)
        return text[:at] + text[at:end].upper() + text[end:]
    # Both effect mutations drop one field and the comma that follows it, so the
    # result is still valid JSON. A mutation that produced malformed JSON would
    # make the consumer refuse at its parse step, leaving the invariant it owns
    # entirely untested -- a test passing for the wrong reason.
    if language == "PHP":
        return drop_field(text, "authority_envelope_digest")
    if language == "Ruby":
        return drop_field(text, "persistence_class")
    if language == "C#":
        return text.replace('"epistemic_status":"WORKING_ASSUMPTION"',
                            '"epistemic_status":"PROBABLY"', 1)
    if language == "Dart":
        # 'a' must sort before 'z'; renaming 'a' to a code point above the Greek
        # key that follows it puts the payload keys out of ascending order.
        return text.replace('"a":"ascii-first"', '"丮":"ascii-first"', 1)
    raise ValueError(f"no mutation defined for {language}")


def check(language: str, timeout: int) -> dict[str, object]:
    vector_name, description = MUTATIONS[language]
    manifest_dir = {
        "C": "c", "C++": "cpp", "Java": "java", "Python": "python",
        "JavaScript": "javascript", "Go": "go", "Rust": "rust",
        "TypeScript": "typescript", "Kotlin": "kotlin", "Swift": "swift",
        "PHP": "php", "Ruby": "ruby", "C#": "csharp", "Dart": "dart",
    }[language]
    manifest = json.loads(
        (ROOT / "polyglot" / "envelope_v1" / manifest_dir / "manifest.json")
        .read_text(encoding="utf-8")
    )

    tools = [str(manifest["required_tool"]), *(manifest.get("helper_tools") or [])]
    missing = [t for t in tools if not shutil.which(t)]
    if missing:
        return {
            "status": "UNKNOWN",
            "mutation": description,
            "limitation": f"toolchain executable(s) not installed: {', '.join(missing)}",
        }

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "repo"
        # Copy only what the harness reads. Copying target/ or .git would make
        # every mutation check pay for the whole tree.
        work.mkdir(parents=True)
        for item in ("contracts", "conformance", "polyglot", "tools"):
            shutil.copytree(ROOT / item, work / item)

        vector_path = work / "conformance" / "vectors" / f"{vector_name}.canonical"
        original = vector_path.read_text(encoding="utf-8")
        damaged = mutate(language, original)
        if damaged == original:
            return {
                "status": "MUTATION_NOT_APPLIED",
                "mutation": description,
                "limitation": "the mutation changed nothing, so it proves nothing; "
                              "the vector's shape has drifted from this mutation's assumption",
            }
        vector_path.write_text(damaged, encoding="utf-8")

        # Recompute the declared digest so a STDLIB consumer refuses because of
        # its invariant rather than because of the digest. Without this the test
        # would pass for the wrong reason.
        expected_path = work / "conformance" / "vectors" / f"{vector_name}.expected.json"
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        raw = vector_path.read_bytes()
        expected["canonical_sha256"] = hashlib.sha256(raw).hexdigest()
        expected["canonical_byte_length"] = len(raw)
        expected_path.write_text(
            json.dumps(expected, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        completed = subprocess.run(
            [sys.executable, "tools/verify_polyglot.py", "--root", ".", "--json",
             "--language", language, "--timeout", str(timeout)],
            cwd=work, text=True, capture_output=True, timeout=timeout + 60,
        )
        try:
            report = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return {
                "status": "HARNESS_ERROR",
                "mutation": description,
                "limitation": f"verifier output was unparseable: "
                              f"{(completed.stdout + completed.stderr)[:400]}",
            }

        outcome = report["languages"].get(language, {})
        if outcome.get("status") == "POSTCONDITION_VERIFIED":
            return {
                "status": "DECORATIVE",
                "mutation": description,
                "limitation": f"{language} still passed against a vector violating its own "
                              f"invariant {manifest['invariant']!r}; its check does not bite",
            }
        return {
            "status": "REFUSED",
            "mutation": description,
            "refusal": "; ".join(map(str, outcome.get("limitations", [])))[:300],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--language")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    languages = [args.language] if args.language else sorted(MUTATIONS)
    unknown = [lang for lang in languages if lang not in MUTATIONS]
    results = {lang: check(lang, args.timeout) for lang in languages if lang in MUTATIONS}

    errors = [
        f"{lang}: {outcome.get('limitation')}"
        for lang, outcome in results.items()
        if outcome["status"] in ("DECORATIVE", "MUTATION_NOT_APPLIED", "HARNESS_ERROR")
    ]
    errors += [f"unknown requested language {lang!r}" for lang in unknown]

    refused = sorted(k for k, v in results.items() if v["status"] == "REFUSED")
    unverifiable = sorted(k for k, v in results.items() if v["status"] == "UNKNOWN")

    payload = {
        "schema": "b1-polyglot-mutation-1",
        "status": "PASS" if not errors else "FAIL",
        "languages": results,
        "errors": errors,
        "refused": refused,
        "unknown": unverifiable,
        "counts": {"refused": len(refused), "unknown": len(unverifiable),
                   "expected": len(MUTATIONS)},
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for language, outcome in sorted(results.items()):
            line = f"  {language:<11} {outcome['status']:<21} {outcome['mutation']}"
            print(line)
            if outcome.get("limitation"):
                print(f"              {outcome['limitation']}")
        counts = payload["counts"]
        print(
            f"mutation resistance: {counts['refused']} REFUSED, {counts['unknown']} UNKNOWN, "
            f"of {counts['expected']} languages"
        )
        for message in errors:
            print(f"  ERROR {message}")

    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
