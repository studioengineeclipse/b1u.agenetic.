#!/usr/bin/env python3
"""Scan the tree for what it would reveal if it were published.

An **independent** publication blocker. It can object; a clean run is the
absence of an objection and never an authorisation. `verify_provenance.py`
blocks publication for a licensing reason, this blocks it for a privacy reason,
and **neither clears the other** -- a tree can be perfectly private and still
unlawful to publish, which is exactly the current state of this repository.

That independence is why this script never prints the word AUTHORIZED. The
strongest thing it says is `NO_OBJECTION`, and it says what it looked for so
that a clean line can be read as narrow rather than as reassurance.

Two checks, and the second is the one people forget
---------------------------------------------------
**Content.** Ten rules over every text file: key blocks, cloud credentials,
credentials in URLs, JWTs, email addresses, absolute home paths, MAC addresses,
routable IPs, session URLs, secret-shaped assignments.

**Placement.** Some files are a problem by existing, whatever is in them. A
`*.db` in the tree is user state; the root journal is the likely one, and it is
the single most revealing artifact B1 produces -- every task, target and payload
a user ever handed it, in a file with an innocuous name that sits in the
workspace and is exactly what you want to send someone when something is wrong.

    python3 tools/verify_privacy.py
    python3 tools/verify_privacy.py --json

Findings that are redacted are redacted here too: a report that quoted the key
it found would be a second copy of the key.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_privacy import (  # noqa: E402
    B1_HOME_VARIABLE,
    PRIVACY_RULES,
    PRIVATE_LAYER,
    SELF_REFERENTIAL,
    forbidden_reason,
    scan_tree,
    self_referential_reason,
)

# Directories that are build output or version control rather than the tree
# anyone would publish. Listed rather than pattern-matched, and reported in the
# output, because an exclusion nobody can see is how coverage silently shrinks.
NOT_THE_TREE = ("target", ".git", "__pycache__", "node_modules", ".cargo")

# Read as text up to this size. Larger files are recorded as not scanned with a
# reason, never skipped -- see the Coverage discipline in b1_security.
MAX_BYTES = 2_000_000


def tracked_files() -> tuple[list[str], str]:
    """Everything a `git add -A` would carry: tracked *and* untracked-not-ignored.

    The first version of this listed tracked files only, and its own docstring
    named the gap -- "the difference is precisely the untracked file someone is
    about to `git add -A`" -- while the code took the narrower option anyway. It
    was blind at the exact moment a privacy scan matters, because a file that is
    about to be added is not yet tracked. `--others --exclude-standard` closes
    it: untracked files are included, and gitignored ones are not, which is the
    set publication would actually carry.
    """
    try:
        completed = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, text=True, capture_output=True, timeout=60,
        )
        if completed.returncode == 0:
            names = sorted({n for n in completed.stdout.split("\0") if n})
            return names, "git ls-files --cached --others (what `git add -A` would carry)"
    except (OSError, subprocess.SubprocessError):
        pass
    names = [
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("*")
        if p.is_file() and not any(part in NOT_THE_TREE for part in p.parts)
    ]
    return names, "a filesystem walk (git was unavailable, so untracked files are included)"


def verify() -> dict[str, object]:
    names, basis = tracked_files()

    placement: list[dict[str, str]] = []
    targets: dict[str, str | None] = {}
    for name in names:
        reason = forbidden_reason(name)
        if reason:
            placement.append({"target": name, "reason": reason})
        # The two files that define the rules contain one example of each, so
        # scanning them would report the rule table as a pile of secrets. They
        # go to `None`, which puts them in `not_scanned` with a reason rather
        # than making them vanish -- and which keeps coverage permanently
        # incomplete, so this scan reports WORKING_ASSUMPTION and never
        # VERIFIED. That is the correct status for a scan that cannot read its
        # own rule table.
        if self_referential_reason(name) is not None:
            targets[name] = None
            continue

        path = ROOT / name
        try:
            if path.stat().st_size > MAX_BYTES:
                targets[name] = None
                continue
            targets[name] = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            targets[name] = None

    manifest = scan_tree(targets)
    blocking = manifest.blocking

    objections: list[str] = []
    for entry in placement:
        objections.append(f"placement: {entry['reason']}")
    for finding in blocking:
        objections.append(
            f"content: {finding.severity} {finding.rule_id} at {finding.target}:{finding.line} "
            f"-- {finding.message}"
        )

    return {
        # PASS here means "no objection raised", never "publishable".
        "status": "FAIL" if objections else "PASS",
        "publication_contribution": "OBJECTION" if objections else "NO_OBJECTION",
        "independent_of": (
            "the licensing blocker in verify_provenance.py. Neither clears the other, and a "
            "tree can be entirely private and still unlawful to publish"
        ),
        "basis": basis,
        "rules_applied": [r.rule_id for r in PRIVACY_RULES],
        "self_referential": [{"target": n, "why": w} for n, w in SELF_REFERENTIAL],
        "private_layer": [{"pattern": p, "why": w} for p, w in PRIVATE_LAYER],
        "objections": objections,
        "placement": placement,
        "coverage": manifest.coverage.to_canonical_dict(),
        "epistemic_status": manifest.epistemic_status,
        "summary": manifest.summary(),
        "findings": [
            {"severity": f.severity, "rule_id": f.rule_id, "target": f.target,
             "line": f.line, "epistemic_status": f.epistemic_status,
             "evidence": f.evidence}
            for f in manifest.findings
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = verify()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "PASS" else 1

    print(f"privacy scan: {report['status']}")
    print(f"  PUBLICATION: {report['publication_contribution']} "
          f"-- this is not an authorisation")
    print(f"  independent of {report['independent_of']}")
    print(f"  basis: {report['basis']}")
    print(f"  {report['summary']}")
    print(f"  looked for {len(report['rules_applied'])} rule(s); "
          f"{B1_HOME_VARIABLE} holds {len(report['private_layer'])} declared "
          f"kinds of private state")
    print(f"  epistemic status: {report['epistemic_status']}")
    for entry in report["self_referential"]:
        print(f"    not scanned: {entry['target']} -- {entry['why']}")

    findings = report["findings"]
    if findings:
        print("  findings (a redacted rule shows no evidence, on purpose):")
        for finding in findings[:12]:
            evidence = f"  {finding['evidence'][:70]}" if finding["evidence"] else "  [redacted]"
            print(f"    {finding['severity']:<8} {finding['rule_id']:<30} "
                  f"{finding['target']}:{finding['line']} [{finding['epistemic_status']}]")
            print(f"      {evidence.strip()}")
        if len(findings) > 12:
            print(f"    ... and {len(findings) - 12} more")

    for objection in report["objections"]:
        print(f"  OBJECTION {objection}")
    if not report["objections"]:
        print("  No objection raised. That is the absence of a finding over these rules,")
        print("  on the files listed above. It is not a statement that the tree is safe.")

    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
