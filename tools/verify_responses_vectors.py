#!/usr/bin/env python3
"""Check the Responses-shim vectors, and check that they check something.

Two halves, and only the second one is interesting.

**The reference translation must pass every vector.** That is a regression
fence. It is not evidence, because the expected outputs were generated from the
same translation (`build_responses_vectors.py` says so in its own docstring).

**Every vector must fail the translator that breaks its invariant.** For each
invariant in `contracts/b1-responses-shim-v1.json` there is a deliberately
lossy translator in `python/b1_shim/lossy.py` that violates exactly that
invariant. A vector that passes both the reference and its lossy counterpart is
checking nothing, and is reported here as `DECORATIVE` -- the same word, and the
same failure, as a polyglot consumer that accepts a vector violating its own
rule.

The cross-check is the third thing, and the one that catches a vector that is
merely *broad*: a lossy translator must fail its own vector and **pass** the
other nine. One that fails several is not proving that its vector is precise;
it is proving that the translator is clumsy, and the report says which.

    python3 tools/verify_responses_vectors.py
    python3 tools/verify_responses_vectors.py --json

Standing limitation, reported on every run rather than left in a docstring: the
field spellings in these vectors are WORKING_ASSUMPTION. The Codex archive is
not present in this environment, so they were not re-derived from source in this
pass. What is VERIFIED (ADR-0002) is that Codex speaks only `/v1/responses`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_shim import (  # noqa: E402
    Untranslatable,
    chat_stream_to_events,
    chat_to_response,
    request_to_chat,
    stable_json,
)
from b1_shim.lossy import LOSSY_TRANSLATORS  # noqa: E402

VECTORS = ROOT / "conformance" / "responses"
CONTRACT = ROOT / "contracts" / "b1-responses-shim-v1.json"
RESPONSE_ID = "resp_b1_vector"

BASIS = (
    "field spellings are WORKING_ASSUMPTION: the Codex archive is not present here, so "
    "they were not re-derived from source in this pass. ADR-0002's wire-api finding is "
    "VERIFIED; these key names are not."
)


def reference(kind: str, payload):
    if kind == "request":
        return request_to_chat(payload)
    if kind == "response":
        return chat_to_response(payload, response_id=RESPONSE_ID)
    return chat_stream_to_events(payload, response_id=RESPONSE_ID)


def run(translator, kind: str, payload):
    """Run a translator of either shape, reference or lossy."""
    if kind == "stream":
        return translator(payload, response_id=RESPONSE_ID)
    if kind == "response":
        return translator(payload, response_id=RESPONSE_ID)
    return translator(payload)


def matches(vector: dict, result) -> tuple[bool, str]:
    """Does a translation match what the vector requires?"""
    if vector["expect"] == "refusal":
        return False, "the translation succeeded where the vector requires a refusal"

    if vector["kind"] == "stream":
        actual = stable_json(result)
        expected = stable_json(vector["expected_events"])
        if actual != expected:
            return False, "event sequence differs"
        return True, ""

    if stable_json(result.payload) != stable_json(vector["expected"]):
        return False, "payload differs"

    # Notes are part of the contract, not commentary. R5 and R7 are satisfied
    # *by* the caller being told, so a translation with the right payload and
    # the wrong notes has not honoured them.
    actual_notes = sorted((n.invariant, n.severity) for n in result.notes)
    expected_notes = sorted(
        (n["invariant"], n["severity"]) for n in vector.get("expected_notes", [])
    )
    if actual_notes != expected_notes:
        return False, f"translation notes differ: {actual_notes} vs {expected_notes}"
    return True, ""


def check(vector: dict, translator) -> tuple[bool, str]:
    """(passes, why not). A refusal vector passes when the translator refuses."""
    try:
        result = run(translator, vector["kind"], vector["input"])
    except Untranslatable as exc:
        if vector["expect"] == "refusal":
            needle = vector.get("refusal_contains", "")
            if needle and needle not in str(exc):
                return False, f"refused for a different reason: {str(exc)[:120]}"
            return True, ""
        return False, f"refused a vector it should translate: {str(exc)[:120]}"
    except Exception as exc:  # noqa: BLE001 - a crash is a failure, not an error here
        return False, f"{type(exc).__name__}: {str(exc)[:120]}"
    return matches(vector, result)


def verify() -> dict[str, object]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    declared = [inv["id"] for inv in contract["invariants"]]

    vectors = []
    for path in sorted(VECTORS.glob("*.json")):
        vectors.append(json.loads(path.read_text(encoding="utf-8")))

    errors: list[str] = []
    results: dict[str, dict[str, object]] = {}

    covered = {v["invariant"] for v in vectors}
    missing = sorted(set(declared) - covered)
    if missing:
        errors.append(
            f"invariants with no vector: {missing}. A contract clause nothing checks is "
            f"a clause that will drift."
        )
    orphan = sorted(covered - set(declared))
    if orphan:
        errors.append(f"vectors for invariants the contract does not declare: {orphan}")

    for vector in vectors:
        invariant = vector["invariant"]
        entry: dict[str, object] = {"vector": vector["vector"], "kind": vector["kind"]}

        ok, why = check(vector, {"request": request_to_chat,
                                 "response": chat_to_response,
                                 "stream": chat_stream_to_events}[vector["kind"]])
        entry["reference"] = "PASS" if ok else "FAIL"
        if not ok:
            errors.append(f"{vector['vector']}: the reference translation fails it -- {why}")

        # Does it catch the translator that breaks its own invariant?
        if invariant not in LOSSY_TRANSLATORS:
            entry["discriminates"] = "NO LOSSY TRANSLATOR"
            errors.append(f"{invariant}: no lossy translator, so the vector proves nothing")
        else:
            name, lossy, _shape = LOSSY_TRANSLATORS[invariant]
            caught, _ = check(vector, lossy)
            entry["lossy"] = name
            entry["discriminates"] = "DECORATIVE" if caught else "CATCHES"
            if caught:
                errors.append(
                    f"{vector['vector']}: passes {name}() as well as the reference, so it "
                    f"is DECORATIVE -- it does not establish {invariant}"
                )

        # And does it catch *only* that one? A vector that every lossy
        # translator fails is broad rather than precise, and a broad vector
        # makes a real regression harder to locate, not easier.
        collateral = []
        for other, (name, lossy, shape) in LOSSY_TRANSLATORS.items():
            if other == invariant or shape != vector["kind"]:
                continue
            passes, _ = check(vector, lossy)
            if not passes:
                collateral.append(other)
        entry["also_fails"] = collateral
        results[invariant] = entry

    status = "FAIL" if errors else "PASS"
    return {
        "status": status,
        "errors": errors,
        "invariants": len(declared),
        "vectors": len(vectors),
        "results": results,
        "basis": BASIS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = verify()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"responses shim vectors: {report['status']}")
        print(f"  {report['vectors']} vectors for {report['invariants']} invariants")
        width = max(len(str(r["vector"])) for r in report["results"].values())
        for invariant in sorted(report["results"], key=lambda k: (len(k), k)):
            entry = report["results"][invariant]
            extra = f"  also fails {entry['also_fails']}" if entry.get("also_fails") else ""
            print(f"  {invariant:<4} {str(entry['vector']):<{width}}  "
                  f"reference {entry['reference']}, {entry['discriminates']}"
                  f" vs {entry.get('lossy', '—')}{extra}")
        print(f"\n  BASIS: {report['basis']}")
        for message in report["errors"]:
            print(f"  ERROR {message}")

    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
