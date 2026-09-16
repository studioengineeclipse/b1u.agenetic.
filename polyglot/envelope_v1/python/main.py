#!/usr/bin/env python3
"""B1 fourteen-language conformance: Python.

Invariant owned: payload_contains_no_float

Python owns this one because Python is where the hazard starts. ``json.dumps``
renders a float through ``repr()``; Rust's ``serde_json`` renders it through
Ryu. Both produce a shortest round-tripping form, but not always the same one,
so a single float anywhere in a payload can give two peers different bytes for
the same record. B1's canonical form therefore carries no floats at all, and
this consumer checks the committed vector holds to that.

digest_verification: STDLIB. ``hashlib`` provides SHA-256, so this consumer also
verifies that the vector's bytes hash to the declared digest.

Usage: main.py <contract.json> <vector.canonical> <expected.json>
"""
from __future__ import annotations

import hashlib
import json
import re
import sys

INVARIANT = "payload_contains_no_float"

# A JSON number token that carries a fraction or an exponent. Anchored so that a
# digit sequence inside a string (a digest, an identifier) cannot match: the
# scanner below only offers it text found outside string literals.
FLOATING = re.compile(r"-?\d+(?:\.\d+|[eE][+-]?\d+)")


def outside_strings(text: str) -> str:
    """Return the text with every string literal removed.

    Digests are 64 hex characters and identifiers contain digits, so scanning
    raw canonical bytes for a number token would produce false positives. Only
    the structure between strings can carry a numeric literal.
    """
    out: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        out.append(char)
    return "".join(out)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(
            "usage: main.py <contract.json> <vector.canonical> <expected.json>",
            file=sys.stderr,
        )
        return 2

    with open(argv[1], encoding="utf-8") as handle:
        contract = json.load(handle)
    with open(argv[2], "rb") as handle:
        vector_bytes = handle.read()
    with open(argv[3], encoding="utf-8") as handle:
        expected = json.load(handle)

    # The contract must actually assert this invariant. A consumer checking a
    # property nobody declared is testing its own opinion, not B1's contract.
    if contract.get("invariants", {}).get(INVARIANT) is not True:
        print(f"Python: contract does not assert {INVARIANT}", file=sys.stderr)
        return 4

    structure = outside_strings(vector_bytes.decode("utf-8"))
    match = FLOATING.search(structure)
    if match:
        print(
            f"Python: found the non-integer numeric token {match.group()!r}. "
            f"Python renders floats via repr() and Rust serde_json via Ryu; these "
            f"can disagree, which would give two peers different digests for one record.",
            file=sys.stderr,
        )
        return 5

    declared = expected.get("canonical_sha256")
    actual = hashlib.sha256(vector_bytes).hexdigest()
    if actual != declared:
        print(
            f"Python: digest mismatch; declared {declared} but the bytes hash to {actual}",
            file=sys.stderr,
        )
        return 5

    print(f"Python:POSTCONDITION:{INVARIANT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
