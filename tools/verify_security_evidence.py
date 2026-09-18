#!/usr/bin/env python3
"""Check the two halves of the security exit criterion, on this repository.

    Security findings enter the tournament/evidence graph and block unsafe
    candidates without independently granting mutation authority.

Both halves, and the second is the one worth running a verifier for:

1. **A finding blocks.** An unsafe candidate goes into a real tournament and
   comes out REJECTed, with the rule that eliminated it named. A safe candidate
   goes in and survives -- without that control, a verifier that rejected
   everything would pass the first check.

2. **A finding cannot authorise.** Checked three ways: the package's import
   graph in a fresh interpreter (no `b1_authority` anywhere, including
   transitively), a tightening that leaves the allow list identical, and a
   probe set showing nothing refused before is permitted after.

It then scans this repository's own Python and prints the manifest, because a
security layer that has never been pointed at real code is a security layer
whose rules have never been wrong about anything. Findings here are reported,
not treated as failures: this tool verifies the *layer*, and B1's own code is
reviewed by people.

    python3 tools/verify_security_evidence.py
    python3 tools/verify_security_evidence.py --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_models import DeterministicProvider, ModelRegistry  # noqa: E402
from b1_policy import Capability, CapabilityPolicy  # noqa: E402
from b1_security import (  # noqa: E402
    RULES,
    SecurityVerifier,
    scan_targets,
    tighten,
)
from b1_tournament import TaskClass, Tournament, Verdict  # noqa: E402

UNSAFE = "import subprocess\nsubprocess.run(cmd, shell=True)\n"
SAFE = "import subprocess\nsubprocess.run([binary, argument])\n"

PROBES = (
    ("fs.write", "src/a.py", "REVERSIBLE"),
    ("fs.write", "src/secrets/key", "REVERSIBLE"),
    ("fs.write", "elsewhere/a.py", "REVERSIBLE"),
    ("net.send", "src/a.py", "REVERSIBLE"),
    ("fs.write", "src/a.py", "IRREVERSIBLE"),
    ("fs.write", "src/a.py", "UNKNOWN"),
)


def tournament_for(answer: str) -> Tournament:
    return Tournament(
        registry=ModelRegistry(),
        verifiers=(SecurityVerifier(),),
        deterministic_participant=DeterministicProvider(answers={"write it": answer}),
    )


def check_blocking() -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    steps: dict[str, str] = {}

    rejected = tournament_for(UNSAFE).run(
        task="write it", task_class=TaskClass.T4_REVERSIBLE_EFFECT
    )
    steps["unsafe_candidate"] = rejected.verdict.value
    if rejected.verdict is not Verdict.REJECT or rejected.winner is not None:
        errors.append(f"an unsafe candidate was not rejected: {rejected.verdict}")
    cited = " ".join(detail for _, detail in rejected.eliminated)
    steps["rule_cited"] = "B1-SEC-001-shell-true" if "B1-SEC-001" in cited else "NOT CITED"
    if "B1-SEC-001" not in cited:
        errors.append(f"the eliminating rule was not named: {cited[:160]}")

    survived = tournament_for(SAFE).run(
        task="write it", task_class=TaskClass.T4_REVERSIBLE_EFFECT
    )
    steps["safe_candidate"] = survived.verdict.value
    if survived.winner is None:
        errors.append(
            "a safe candidate was rejected too, so the check above proves nothing "
            "about findings -- a verifier that refuses everything passes it"
        )
    return steps, errors


def check_cannot_authorise() -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    steps: dict[str, str] = {}

    code = (
        "import sys; sys.path.insert(0, %r);"
        "import b1_security;"
        "print('b1_authority' in sys.modules)" % str(ROOT / "python")
    )
    result = subprocess.run([sys.executable, "-c", code], text=True,
                            capture_output=True, timeout=120)
    reached = result.stdout.strip() != "False" or result.returncode != 0
    steps["imports_authority"] = "YES" if reached else "no"
    if reached:
        errors.append(
            f"b1_security reaches b1_authority: {result.stdout.strip()} "
            f"{result.stderr.strip()[:200]}"
        )

    base = CapabilityPolicy(
        policy_id="verify",
        allow=(Capability("fs.write", ("src/**",)),),
        deny=(Capability("fs.write", ("src/secrets/**",), reason="never"),),
    )
    report = tighten(base, scan_targets({"src/a.py": UNSAFE}))
    steps["allow_unchanged"] = "yes" if report.allow_unchanged else "CHANGED"
    if not report.allow_unchanged:
        errors.append("tightening changed the allow list")

    widened = [
        probe for probe in PROBES
        if not base.decide(*probe).allowed and report.after.decide(*probe).allowed
    ]
    steps["widened_probes"] = str(len(widened))
    if widened:
        errors.append(f"tightening permitted something previously refused: {widened}")

    steps["narrowed"] = "yes" if not report.after.decide(
        "fs.write", "src/a.py", "REVERSIBLE"
    ).allowed else "NO"
    if report.after.decide("fs.write", "src/a.py", "REVERSIBLE").allowed:
        errors.append("the finding's own target is still permitted after tightening")
    return steps, errors


def scan_this_repository() -> dict[str, object]:
    """Point the rules at real code. Reported, never a failure."""
    targets: dict[str, str | None] = {}
    for path in sorted((ROOT / "python").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(ROOT))
        try:
            targets[relative] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            targets[relative] = None

    manifest = scan_targets(targets)
    # Where the findings actually are, because a count with no shape is noise.
    # Nothing is suppressed: an exclusion list is how coverage silently shrinks,
    # and `Coverage` exists in this package precisely to stop that.
    own = sum(
        1 for f in manifest.findings
        if f.target.startswith("python/b1_security/") or f.target.startswith("python/tests/")
    )
    return {
        "summary": manifest.summary(),
        "self_referential": own,
        "note": (
            f"{own} of {len(manifest.findings)} are in the scanner's own rule table or in "
            f"its tests -- a regex scanner matches its own patterns, and the fixtures "
            f"deliberately contain unsafe code. They are reported rather than excluded: an "
            f"exclusion list is how coverage silently shrinks. Read the rest first."
        ),
        "epistemic_status": manifest.epistemic_status,
        "rules": len(RULES),
        "findings": [
            {"severity": f.severity, "rule_id": f.rule_id, "target": f.target,
             "line": f.line, "epistemic_status": f.epistemic_status}
            for f in manifest.findings
        ],
    }


def verify() -> dict[str, object]:
    blocking_steps, blocking_errors = check_blocking()
    authority_steps, authority_errors = check_cannot_authorise()
    errors = blocking_errors + authority_errors
    return {
        "status": "FAIL" if errors else "PASS",
        "errors": errors,
        "blocks": blocking_steps,
        "cannot_authorise": authority_steps,
        "self_scan": scan_this_repository(),
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
        print(f"security evidence: {report['status']}")
        print("  a finding blocks:")
        for key, value in report["blocks"].items():
            print(f"    {key}: {value}")
        print("  a finding cannot authorise:")
        for key, value in report["cannot_authorise"].items():
            print(f"    {key}: {value}")
        scan = report["self_scan"]
        print(f"  this repository, under {scan['rules']} rules:")
        print(f"    {scan['summary']}")
        print(f"    {scan['note']}")
        for finding in scan["findings"][:10]:
            print(f"    {finding['severity']:<8} {finding['rule_id']:<26} "
                  f"{finding['target']}:{finding['line']} [{finding['epistemic_status']}]")
        if len(scan["findings"]) > 10:
            print(f"    ... and {len(scan['findings']) - 10} more")
        for message in report["errors"]:
            print(f"  ERROR {message}")

    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
