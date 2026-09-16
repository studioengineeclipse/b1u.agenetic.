#!/usr/bin/env python3
"""Verify meaningful fourteen-language participation in the B1 envelope contract.

Design-derived-from: b1mu-omega13.9:tools/verify_omega13_9_polyglot.py

Kept from upstream, because it is the right shape:

* A language is ``POSTCONDITION_VERIFIED`` only when its own toolchain builds
  its consumer, runs it against the canonical contract, and the observed output
  matches the declared output exactly.
* A missing toolchain yields ``UNKNOWN``. It is never promoted from the mere
  existence of a source file. This is the single most important property here:
  a source file existing is not evidence that a language participates.
* Responsibilities and observable contributions must be unique across the
  fourteen, so no two languages can quietly do the same job twice.

Three defects in the upstream verifier are fixed, and they are worth naming
because the third is the one that matters:

1.  Upstream validated each manifest's ``build_command`` and ``run_command`` and
    then executed *hardcoded* commands from an internal table instead. The
    declared commands were decorative -- in a harness whose entire purpose is
    preventing decorative participation. B1 executes the declared templates, so
    a manifest that lies about how its language is built now fails.
2.  Upstream exempted ``build_command`` from its own non-empty check
    (``if field != "build_command" and ...``). B1 does not; a language with no
    build step says so by declaring its check command.
3.  Upstream targeted ``csc`` plus ``mono`` for C#. B1 targets ``dotnet``.

Executing commands out of a data file is a code-execution path, so it is
constrained rather than trusted: templates are split as argument vectors with no
shell, only known placeholders expand, the executable must equal the manifest's
own declared ``required_tool`` or appear in a helper allowlist, and every path
must resolve inside the repository root.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

EXPECTED_LANGUAGES = {
    "C", "C++", "Java", "Python", "JavaScript", "Go", "Rust",
    "TypeScript", "Kotlin", "Swift", "PHP", "Ruby", "C#", "Dart",
}

REQUIRED_FIELDS = (
    "language", "responsibility", "invariant", "source", "input_contract",
    "input_vector", "output_contract", "interface", "required_tool", "helper_tools",
    "digest_verification", "build_command", "run_command", "expected_output",
    "observable_contribution", "dependencies", "test_command",
)

# Fields that must be present but are allowed to be empty or null, with the
# reason. Everything else must be non-empty; unlike upstream, build_command is
# not on this list.
MAY_BE_EMPTY = {
    "helper_tools": "a language may need no helper beyond its own toolchain",
    "input_expected": "a NOT_IN_STDLIB language verifies no digest and needs no expected file",
}

DIGEST_TIERS = ("STDLIB", "NOT_IN_STDLIB")

PLACEHOLDERS = ("{source}", "{sourcedir}", "{out}", "{outdir}", "{contract}",
                "{vector}", "{expected}")


class ManifestError(Exception):
    """A manifest is not usable as declared."""


def load_manifests(root: Path) -> list[tuple[Path, dict[str, object]]]:
    out: list[tuple[Path, dict[str, object]]] = []
    for path in sorted((root / "polyglot" / "envelope_v1").glob("*/manifest.json")):
        out.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return out


def inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def validate_manifest(manifest: dict[str, object], root: Path) -> list[str]:
    errors: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in manifest:
            errors.append(f"missing {field}")
            continue
        if field in MAY_BE_EMPTY:
            continue
        if manifest[field] in (None, "", (), [], {}):
            errors.append(f"{field} must be non-empty")

    if manifest.get("input_contract") != "contracts/b1-envelope-v1.json":
        errors.append("input_contract must bind the canonical b1-envelope-v1 contract")
    if manifest.get("interface") != "FILE_JSON_V1":
        errors.append("interface must be FILE_JSON_V1")
    if manifest.get("output_contract") != "UTF8_LINE_POSTCONDITION_V1":
        errors.append("output_contract must be UTF8_LINE_POSTCONDITION_V1")

    tier = manifest.get("digest_verification")
    if tier not in DIGEST_TIERS:
        errors.append(f"digest_verification must be one of {DIGEST_TIERS}, got {tier!r}")

    # The invariant must actually be declared in the contract, and the expected
    # output must name it. Otherwise a consumer could claim a postcondition the
    # contract never asserts.
    invariant = str(manifest.get("invariant", ""))
    contract_path = root / "contracts" / "b1-envelope-v1.json"
    if not contract_path.is_file():
        errors.append("canonical input contract is missing")
    elif invariant:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        if contract.get("invariants", {}).get(invariant) is not True:
            errors.append(f"contract does not assert invariant {invariant!r}")
        if invariant not in contract.get("why_each_matters", {}):
            errors.append(f"contract does not explain why {invariant!r} matters")
    expected_output = str(manifest.get("expected_output", ""))
    if invariant and not expected_output.endswith(f":POSTCONDITION:{invariant}"):
        errors.append(
            f"expected_output {expected_output!r} does not name the owned invariant "
            f"{invariant!r}; a consumer must not claim a postcondition it does not own"
        )

    # Every declared path must exist and stay inside the repository.
    for field in ("source", "input_vector", "input_expected"):
        value = manifest.get(field)
        if not value:
            continue
        candidate = root / str(value)
        if not inside(root, candidate):
            errors.append(f"{field} must resolve inside the repository root")
        elif not candidate.is_file():
            errors.append(f"{field} does not exist: {value}")

    if manifest.get("digest_verification") == "STDLIB" and not manifest.get("input_expected"):
        errors.append("a STDLIB language must declare input_expected to verify a digest against")
    if manifest.get("digest_verification") == "NOT_IN_STDLIB" and manifest.get("input_expected"):
        errors.append(
            "a NOT_IN_STDLIB language verifies no digest, so declaring input_expected "
            "overstates what it checks"
        )

    for dependency in manifest.get("dependencies", []) or []:
        if not (root / str(dependency)).is_file():
            errors.append(f"declared dependency does not exist: {dependency}")

    # The command templates are executed, so they are checked before use.
    tool = str(manifest.get("required_tool", ""))
    helpers = {str(h) for h in (manifest.get("helper_tools") or [])}
    for field in ("build_command", "run_command"):
        template = manifest.get(field)
        if not isinstance(template, list) or not template:
            errors.append(f"{field} must be a non-empty list of argument tokens")
            continue
        if any(not isinstance(token, str) for token in template):
            errors.append(f"{field} tokens must all be strings")
            continue
        head = template[0]
        # A run_command may invoke the built artifact itself, which is a
        # placeholder rather than a named tool.
        if head.startswith("{"):
            if head not in ("{out}",):
                errors.append(f"{field} may only invoke {{out}} as a placeholder, got {head!r}")
        elif head != tool and head not in helpers:
            errors.append(
                f"{field} invokes {head!r}, which is neither the declared required_tool "
                f"{tool!r} nor a declared helper_tool; the manifest must declare what it runs"
            )
        for token in template:
            for fragment in token.split("{")[1:]:
                name = "{" + fragment.split("}")[0] + "}"
                if name not in PLACEHOLDERS:
                    errors.append(f"{field} uses the unknown placeholder {name}")

    return errors


def expand(template: list[str], substitutions: dict[str, str]) -> list[str]:
    """Expand only the known placeholders. No shell, ever."""
    out: list[str] = []
    for token in template:
        for name, value in substitutions.items():
            token = token.replace(name, value)
        out.append(token)
    return out


# Environment banners that some toolchains print to stderr unasked. They are not
# the consumer's output, and left in place they push the consumer's own message
# out of a truncated failure report -- which is how a test starts passing for
# the wrong reason without anyone noticing.
STDERR_NOISE = ("Picked up JAVA_TOOL_OPTIONS:", "Picked up _JAVA_OPTIONS:")


def strip_noise(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines()
        if not line.startswith(STDERR_NOISE)
    )


def run_command(argv: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    # Give toolchains that insist on a writable cache one inside the sandbox,
    # so a missing HOME does not masquerade as a language failure.
    env.setdefault("HOME", str(cwd))
    env.setdefault("GOCACHE", str(cwd / "gocache"))
    env.setdefault("GOPATH", str(cwd / "gopath"))
    env.setdefault("DOTNET_CLI_TELEMETRY_OPTOUT", "1")
    env.setdefault("DOTNET_NOLOGO", "1")
    return subprocess.run(argv, cwd=cwd, text=True, capture_output=True, timeout=timeout, env=env)


def verify_one(manifest: dict[str, object], root: Path, timeout: int) -> dict[str, object]:
    language = str(manifest.get("language", ""))
    expected_output = str(manifest.get("expected_output", ""))
    tool = str(manifest.get("required_tool", ""))

    result: dict[str, object] = {
        "status": "UNKNOWN",
        "invariant": manifest.get("invariant"),
        "required_tool": tool,
        "digest_verification": manifest.get("digest_verification"),
        "expected_output": expected_output,
        "observed_output": None,
        "limitations": [],
        "responsibility": manifest.get("responsibility"),
        "observable_contribution": manifest.get("observable_contribution"),
    }

    errors = validate_manifest(manifest, root)
    if errors:
        result["limitations"] = errors
        result["verification_error"] = True
        return result

    # A missing toolchain is UNKNOWN, never a pass and never a failure of the
    # code. This is the property that keeps the whole report honest.
    missing = [t for t in [tool, *(manifest.get("helper_tools") or [])] if not shutil.which(str(t))]
    if missing:
        result["limitations"] = [
            f"{language} toolchain executable(s) not installed: {', '.join(map(str, missing))}"
        ]
        return result

    with tempfile.TemporaryDirectory() as tmp:
        build_dir = Path(tmp)
        (build_dir / "out").mkdir()
        source = root / str(manifest["source"])
        substitutions = {
            "{source}": str(source),
            "{sourcedir}": str(source.parent),
            "{out}": str(build_dir / "consumer"),
            "{outdir}": str(build_dir / "out"),
            "{contract}": str(root / str(manifest["input_contract"])),
            "{vector}": str(root / str(manifest["input_vector"])),
            "{expected}": str(root / str(manifest["input_expected"]))
            if manifest.get("input_expected") else "",
        }

        for phase in ("build_command", "run_command"):
            argv = expand([str(t) for t in manifest[phase]], substitutions)  # type: ignore[index]
            try:
                completed = run_command(argv, build_dir, timeout)
            except subprocess.TimeoutExpired:
                result["limitations"] = [f"{phase} exceeded {timeout}s"]
                result["verification_error"] = True
                return result
            except OSError as exc:
                result["limitations"] = [f"{phase} could not be executed: {exc}"]
                result["verification_error"] = True
                return result

            if phase == "run_command":
                result["observed_output"] = completed.stdout.strip()

            if completed.returncode != 0:
                detail = (completed.stdout + strip_noise(completed.stderr)).strip()
                result["limitations"] = [
                    f"{phase} failed with exit {completed.returncode}: {detail[:600]}"
                ]
                result["verification_error"] = True
                return result

        observed = str(result["observed_output"])
        if observed != expected_output:
            result["limitations"] = [
                f"postcondition mismatch: expected {expected_output!r}, observed {observed!r}"
            ]
            result["verification_error"] = True
            return result

    result["status"] = "POSTCONDITION_VERIFIED"
    return result


def verify(root: Path, only: str | None, timeout: int) -> dict[str, object]:
    manifests = load_manifests(root)
    errors: list[str] = []

    languages = [str(m.get("language", "")) for _, m in manifests]
    if set(languages) != EXPECTED_LANGUAGES or len(languages) != len(EXPECTED_LANGUAGES):
        missing = sorted(EXPECTED_LANGUAGES - set(languages))
        extra = sorted(set(languages) - EXPECTED_LANGUAGES)
        errors.append(
            f"expected exactly the fourteen required languages; missing {missing}, extra {extra}"
        )

    # Uniqueness is what stops two languages quietly doing the same job, which
    # is how a fourteen-language tree becomes decorative one entry at a time.
    for field in ("responsibility", "observable_contribution", "invariant", "expected_output"):
        values = [str(m.get(field, "")) for _, m in manifests]
        duplicates = sorted({v for v in values if values.count(v) > 1})
        if duplicates:
            errors.append(f"{field} must be unique across languages; repeated: {duplicates}")

    selected = manifests
    if only:
        selected = [(p, m) for p, m in manifests if str(m.get("language")) == only]
        if not selected:
            errors.append(f"unknown requested language {only!r}")

    results: dict[str, dict[str, object]] = {}
    for _, manifest in selected:
        language = str(manifest.get("language", ""))
        outcome = verify_one(manifest, root, timeout)
        results[language] = outcome
        if outcome.get("verification_error"):
            errors.append(f"{language}: {'; '.join(map(str, outcome['limitations']))}")

    verified = sorted(k for k, v in results.items() if v["status"] == "POSTCONDITION_VERIFIED")
    unknown = sorted(k for k, v in results.items() if v["status"] == "UNKNOWN")

    return {
        "schema": "b1-polyglot-participation-1",
        "status": "PASS" if not errors else "FAIL",
        "languages": results,
        "errors": errors,
        "postcondition_verified": verified,
        "unknown": unknown,
        "counts": {
            "postcondition_verified": len(verified),
            "unknown": len(unknown),
            "expected": len(EXPECTED_LANGUAGES),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--language")
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    result = verify(Path(args.root).resolve(), args.language, args.timeout)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        for language, outcome in sorted(result["languages"].items()):
            line = f"  {language:<11} {outcome['status']}"
            if outcome["limitations"]:
                line += f" -- {'; '.join(map(str, outcome['limitations']))}"
            print(line)
        counts = result["counts"]
        print(
            f"fourteen-language participation: {counts['postcondition_verified']} "
            f"POSTCONDITION_VERIFIED, {counts['unknown']} UNKNOWN, "
            f"of {counts['expected']} required"
        )
        if result["unknown"]:
            print(
                "  UNKNOWN means the toolchain is absent here, so participation could not be "
                "observed. It is not a pass and not a failure."
            )
        for message in result["errors"]:
            print(f"  ERROR {message}")

    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
