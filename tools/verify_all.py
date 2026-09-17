#!/usr/bin/env python3
"""Run every B1 verifier and print one status table.

The table distinguishes four outcomes, and the last two are the reason this
script exists rather than a shell one-liner:

  PASS      the property was checked and holds
  FAIL      the property was checked and does not hold
  PARTIAL   the check ran and found nothing wrong, but did not observe everything
  UNKNOWN   the property could not be checked here at all

Neither PARTIAL nor UNKNOWN is a softer PASS. A missing toolchain, an absent
cargo, or an uninstalled dependency means nobody looked, and a report that
renders "nobody looked" the same as "it holds" is the specific failure this
whole project is built to avoid.

PARTIAL exists because the fourteen-language harness passes when it correctly
reports a missing toolchain as UNKNOWN -- the harness is behaving, but four
languages went unobserved, and rolling that up as a clean PASS would hide it at
exactly the altitude a reader is most likely to stop at.

The exit code says which happened: 0 all checked and holding, 1 something
failed, 2 nothing failed but something was not observed.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(argv: list[str], timeout: int) -> tuple[int, str, str]:
    try:
        completed = subprocess.run(
            argv, cwd=ROOT, text=True, capture_output=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return 124, "", f"exceeded {timeout}s"
    except OSError as exc:
        return 127, "", str(exc)
    return completed.returncode, completed.stdout, completed.stderr


def summarise_python_tests(timeout: int) -> dict[str, object]:
    code, out, err = run(
        [sys.executable, "-m", "unittest", "discover", "-s", "python/tests"], timeout
    )
    text = out + err
    ran = next(
        (line for line in text.splitlines() if line.startswith("Ran ")), "Ran ? tests"
    )
    return {
        "check": "python unit tests",
        "status": "PASS" if code == 0 else "FAIL",
        "detail": ran.strip(),
    }


def summarise_cargo(timeout: int) -> dict[str, object]:
    if not shutil.which("cargo"):
        return {
            "check": "rust unit and vector tests",
            "status": "UNKNOWN",
            "detail": "cargo is not installed, so the Rust peer could not be tested here",
        }
    code, out, err = run(["cargo", "test", "--quiet", "--workspace"], timeout)
    text = out + err
    passed = sum(
        int(line.split()[3])
        for line in text.splitlines()
        if line.startswith("test result: ok.") and len(line.split()) > 3
    )
    if code != 0:
        failing = [line for line in text.splitlines() if "FAILED" in line or "panicked" in line]
        return {
            "check": "rust unit and vector tests",
            "status": "FAIL",
            "detail": "; ".join(failing[:3]) or f"cargo exited {code}",
        }
    return {
        "check": "rust unit and vector tests",
        "status": "PASS",
        "detail": f"{passed} tests passed",
    }


def summarise_json_tool(
    label: str, script: str, timeout: int, extract=None
) -> dict[str, object]:
    code, out, err = run([sys.executable, f"tools/{script}", "--json"], timeout)
    try:
        report = json.loads(out)
    except json.JSONDecodeError:
        return {
            "check": label,
            "status": "FAIL",
            "detail": f"{script} produced no parseable report: {(out + err).strip()[:200]}",
        }
    status = str(report.get("status", "FAIL"))
    detail = extract(report) if extract else ""
    if status == "FAIL" and report.get("errors"):
        detail = "; ".join(map(str, report["errors"]))[:300]
    if status == "UNKNOWN" and report.get("limitation"):
        detail = str(report["limitation"])

    # A report that passed while leaving entries unobserved is PARTIAL, not
    # PASS. The harness behaved; the property was not fully established.
    if status == "PASS" and report.get("unknown"):
        status = "PARTIAL"

    return {"check": label, "status": status, "detail": detail}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    checks: list[dict[str, object]] = [
        summarise_json_tool(
            "provenance record",
            "verify_provenance.py",
            args.timeout,
            lambda r: f"{r['derived_file_count']} derived files declared; "
                      f"publication {r['publication']}",
        ),
        summarise_python_tests(args.timeout),
        summarise_cargo(args.timeout),
        summarise_json_tool(
            "rust/python canonical bytes agree",
            "verify_cross_language_digest.py",
            args.timeout,
            lambda r: ", ".join(f"{k}={v}" for k, v in sorted(r["vectors"].items())),
        ),
        summarise_json_tool(
            "rust/python journal history agrees",
            "verify_cross_language_journal.py",
            args.timeout,
            lambda r: f"head after {r.get('events', '?')} events: {str(r.get('head', ''))[:16]}...",
        ),
        summarise_json_tool(
            "rust/python gate commits one effect",
            "verify_cross_language_gate.py",
            args.timeout,
            lambda r: "; ".join(
                f"{k}={v}" for k, v in (r.get("steps") or {}).items()
                if k != "final_head"
            ),
        ),
        summarise_json_tool(
            "fourteen-language participation",
            "verify_polyglot.py",
            args.timeout,
            lambda r: f"{r['counts']['postcondition_verified']} POSTCONDITION_VERIFIED, "
                      f"{r['counts']['unknown']} UNKNOWN of {r['counts']['expected']}"
                      + (f" (unknown: {', '.join(r['unknown'])})" if r["unknown"] else ""),
        ),
        summarise_json_tool(
            "fourteen-language checks actually bite",
            "verify_polyglot_mutations.py",
            args.timeout,
            lambda r: f"{r['counts']['refused']} REFUSED, {r['counts']['unknown']} UNKNOWN "
                      f"of {r['counts']['expected']}",
        ),
    ]

    failed = [c for c in checks if c["status"] == "FAIL"]
    unknown = [c for c in checks if c["status"] == "UNKNOWN"]
    partial = [c for c in checks if c["status"] == "PARTIAL"]

    if args.json:
        print(json.dumps({"checks": checks, "failed": len(failed),
                          "unknown": len(unknown), "partial": len(partial)}, indent=2))
    else:
        width = max(len(str(c["check"])) for c in checks)
        print()
        for check in checks:
            print(f"  {str(check['check']):<{width}}  {str(check['status']):<8} {check['detail']}")
        print()
        if failed:
            print(f"  {len(failed)} check(s) FAILED.")
        if unknown:
            print(
                f"  {len(unknown)} check(s) UNKNOWN: could not be checked here at all, "
                f"and not to be reported as passing."
            )
        if partial:
            print(
                f"  {len(partial)} check(s) PARTIAL: nothing wrong was found, but not "
                f"everything was observed on this machine."
            )
        if not failed and not unknown and not partial:
            print("  All checks verified on this machine.")
        print()

    if failed:
        return 1
    return 2 if unknown or partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
