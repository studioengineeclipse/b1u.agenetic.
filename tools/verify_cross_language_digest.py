#!/usr/bin/env python3
"""Verify that the Rust and Python peers agree, byte for byte.

B1's root journal has two computational peers writing history that must be
identical. "Both implement the same spec" is a working assumption. This verifier
is what turns it into an observation: it runs the Rust peer as a separate
process, has it emit canonical bytes and digests for every committed vector, and
compares them against what Python produces in this process.

It compares *bytes*, not digests. Two implementations that disagree about
whitespace but agree about SHA-256 do not exist, but a diff of bytes tells you
where the disagreement is, whereas a diff of digests only tells you that there
is one.

Requires `cargo`. If cargo is absent the result is UNKNOWN, not PASS: an
unverifiable claim is not a verified one.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_protocol import Envelope  # noqa: E402
from b1_protocol.canonical import digest_bytes  # noqa: E402

VECTORS = ROOT / "conformance" / "vectors"


def rust_emit(timeout: int) -> tuple[dict[str, dict[str, object]] | None, str]:
    """Run the Rust peer's emitter and return its report."""
    if not shutil.which("cargo"):
        return None, "cargo is not installed; the Rust peer cannot be executed"
    try:
        completed = subprocess.run(
            ["cargo", "run", "--quiet", "-p", "b1-protocol", "--bin", "b1-emit-vectors"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None, f"the Rust peer did not finish within {timeout}s"
    if completed.returncode != 0:
        return None, f"the Rust peer failed: {(completed.stdout + completed.stderr).strip()}"
    try:
        return json.loads(completed.stdout), ""
    except json.JSONDecodeError as exc:
        return None, f"the Rust peer emitted unparseable output: {exc}"


def verify(timeout: int) -> dict[str, object]:
    names = sorted(p.stem for p in VECTORS.glob("*.canonical"))
    errors: list[str] = []
    compared: dict[str, str] = {}

    if not names:
        return {
            "status": "FAIL",
            "errors": ["no canonical vectors found; run tools/build_vectors.py"],
            "vectors": {},
        }

    rust, limitation = rust_emit(timeout)
    if rust is None:
        return {
            "status": "UNKNOWN",
            "limitation": limitation,
            "errors": [],
            "vectors": {name: "UNKNOWN" for name in names},
            "note": "Python-side conformance is still checked by "
                    "python/tests/test_protocol.py; only the cross-language claim is unverified.",
        }

    for name in names:
        source = json.loads((VECTORS / f"{name}.json").read_text(encoding="utf-8"))
        committed = (VECTORS / f"{name}.canonical").read_bytes()
        python_bytes = Envelope.from_dict(source).canonical_bytes()

        if python_bytes != committed:
            errors.append(f"{name}: Python no longer reproduces its own committed vector")
            compared[name] = "PYTHON_DRIFT"
            continue

        if name not in rust:
            errors.append(f"{name}: the Rust peer did not emit this vector")
            compared[name] = "MISSING_FROM_RUST"
            continue

        rust_bytes = bytes.fromhex(str(rust[name]["canonical_hex"]))
        if rust_bytes != python_bytes:
            errors.append(
                f"{name}: canonical bytes differ.\n"
                f"    python: {python_bytes!r}\n"
                f"    rust:   {rust_bytes!r}"
            )
            compared[name] = "BYTE_MISMATCH"
            continue

        if str(rust[name]["digest"]) != digest_bytes(python_bytes):
            errors.append(f"{name}: bytes agree but digests do not, which should be impossible")
            compared[name] = "DIGEST_MISMATCH"
            continue

        compared[name] = "AGREED"

    extra = sorted(set(rust) - set(names))
    if extra:
        errors.append(f"the Rust peer emitted vectors Python does not know about: {extra}")

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "vectors": compared,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    result = verify(args.timeout)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"cross-language canonical agreement: {result['status']}")
        for name, verdict in sorted(result["vectors"].items()):
            print(f"  {name}: {verdict}")
        if result.get("limitation"):
            print(f"  LIMITATION {result['limitation']}")
            print(f"  {result['note']}")
        for message in result["errors"]:
            print(f"  ERROR {message}")

    # UNKNOWN is not success, but it is not a defect in the code under test
    # either. It exits 2 so a caller can tell "disagreed" from "could not look".
    return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}[str(result["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
