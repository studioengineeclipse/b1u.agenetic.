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
    # Drop unittest's elapsed time. It changes on every run, and a figure that
    # changes on every run cannot be quoted anywhere without going stale --
    # including in this project's own README, which quotes this table.
    ran = ran.split(" in ")[0].strip()
    return {
        "check": "python unit tests",
        "status": "PASS" if code == 0 else "FAIL",
        "detail": ran,
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


def summarise_tournament(timeout: int) -> dict[str, object]:
    """Does the tournament run end to end on this machine, and with what?

    Deliberately reports PARTIAL when no model server is reachable. The
    tournament genuinely works without one — the deterministic participant is a
    real entrant — but "ran with no models" and "ran against competing models"
    are different claims, and only one of them is the multi-model system.
    """
    code, out, err = run([sys.executable, "tools/run_tournament.py"], timeout)
    if code != 0:
        return {
            "check": "tournament runs end to end",
            "status": "FAIL",
            "detail": (out + err).strip()[:300],
        }

    probe_code, probe_out, _ = run([sys.executable, "tools/run_tournament.py", "--probe"], timeout)
    reachable = probe_code == 0 and "-> " in probe_out and "not reachable" not in probe_out

    verdict = next(
        (line.split(":", 1)[1].strip() for line in out.splitlines()
         if line.startswith("verdict")), "?"
    )
    if reachable:
        return {
            "check": "tournament runs end to end",
            "status": "PASS",
            "detail": f"verdict={verdict}; a local model server is reachable",
        }
    return {
        "check": "tournament runs end to end",
        "status": "PARTIAL",
        "detail": f"verdict={verdict}; no model server here, see docs/RUNNING.md",
    }


def summarise_agent(timeout: int) -> dict[str, object]:
    """Does a task travel the whole path, and does each gate refuse correctly?

    Five runs, because one success proves less than one success plus four
    refusals. A runner that wrote the file unconditionally would pass the first
    and fail the rest, and each refusal comes from a different layer: the
    standing policy, the approval, the staleness check, and the deterministic
    verifier.
    """
    cases = [
        ([], "does not exist", "unapproved writes nothing"),
        (["--approve"], "It proves a record was altered", "approved writes"),
        (["--approve", "--interfere"], "another actor wrote here first",
         "stale authority refuses"),
        (["--approve", "--bad-answer"], "does not exist",
         "failed verification refuses"),
        # Approved *and* well-answered, and still refused: the policy is above
        # the approval, so the run never reaches a person.
        (["--approve", "--forbidden-target"], "Approval was never the question",
         "policy refuses"),
    ]
    observed: list[str] = []
    for flags, expected, label in cases:
        code, out, err = run([sys.executable, "tools/run_agent.py", *flags], timeout)
        if code != 0:
            return {
                "check": "one task travels the whole path",
                "status": "FAIL",
                "detail": f"{label}: exited {code}: {(out + err).strip()[:200]}",
            }
        if expected not in out:
            return {
                "check": "one task travels the whole path",
                "status": "FAIL",
                "detail": f"{label}: expected {expected!r} in the output and it was absent",
            }
        observed.append(label)
    writes = sum(1 for label in observed if label == "approved writes")
    return {
        "check": "one task travels the whole path",
        "status": "PASS",
        "detail": f"{len(observed)} runs: {writes} writes, {len(observed) - writes} refuse correctly",
        "runs": observed,
    }


def summarise_agreement(key: str, noun: str):
    """Roll a report's per-entry agreement map into one line, naming dissent.

    `n/n AGREED` is only allowed to be printed when every entry agreed. Anything
    else names the entries that did not, because "12/14 agreed" read at a glance
    looks like a pass and the two that disagreed are the entire finding.
    """
    def extract(report: dict[str, object]) -> str:
        entries = report.get(key) or {}
        if not isinstance(entries, dict) or not entries:
            return f"no {noun} reported"
        disagreed = sorted(k for k, v in entries.items() if v != "AGREED")
        if disagreed:
            return (
                f"{len(entries) - len(disagreed)}/{len(entries)} {noun} AGREED; "
                f"DISAGREED: {', '.join(disagreed)}"
            )
        return f"{len(entries)}/{len(entries)} {noun} AGREED"

    return extract


def summarise_gate(report: dict[str, object]) -> str:
    """One line for the race: how many permits, how many effects, how the
    losers lost, and whether the two peers ended on the same head.

    The loser kinds are in the summary on purpose. A gate that only worked
    because SQLite returned SQLITE_BUSY would show the same permit and effect
    counts as one that worked by its own rule, and only the loser kind tells
    the two apart.
    """
    steps = report.get("steps") or {}
    if not isinstance(steps, dict):
        return "no steps reported"
    claim = str(steps.get("concurrent_claim", "?"))
    permits = claim.split(" granted", 1)[0] if " granted" in claim else "?"
    denied = (
        "both refuse denied"
        if steps.get("rust_denied_capability") == "CAPABILITY_DENIED"
        and steps.get("python_denied_capability") == "CapabilityDenied"
        else "DENIAL NOT ENFORCED"
    )
    return (
        f"{permits} permit, {steps.get('effect_records_in_history', '?')} effect, "
        f"losers {steps.get('claim_loser_kind', '?')}/"
        f"{steps.get('consume_loser_kind', '?')}, {denied}, heads "
        f"{steps.get('head_agreement', '?')}"
    )


def summarise_projection(report: dict[str, object]) -> str:
    """Two claims on one line: modes agree, and the two peers agree.

    Both are named even when the second is unobservable, because "3 modes
    AGREED" on its own reads like the whole property and it is half of it.
    """
    modes = report.get("modes") or {}
    mode_count = len(modes) if isinstance(modes, dict) else 0
    cross = report.get("cross_language")
    if not isinstance(cross, dict) or not cross:
        return f"{mode_count} modes {report.get('cross_mode', '?')}; peers not compared here"
    agreed = sum(1 for verdict in cross.values() if verdict == "AGREED")
    return (
        f"{mode_count} modes {report.get('cross_mode', '?')}, "
        f"{agreed}/{len(cross)} rust/python AGREED"
    )


def summarise_security(report: dict[str, object]) -> str:
    """Both halves of the exit criterion, and the self-scan's shape.

    The second half is stated as what did *not* happen, because that is the
    claim: no authority import, no widened probe. A line reporting only that a
    finding blocked would describe a security layer that had passed half its
    criterion.
    """
    blocks = report.get("blocks") or {}
    cannot = report.get("cannot_authorise") or {}
    scan = report.get("self_scan") or {}
    if not isinstance(blocks, dict) or not isinstance(cannot, dict):
        return "no steps reported"
    findings = len(scan.get("findings") or []) if isinstance(scan, dict) else 0
    own = scan.get("self_referential", "?") if isinstance(scan, dict) else "?"
    return (
        f"unsafe {blocks.get('unsafe_candidate', '?')}, safe survives; "
        f"no authority import, {cannot.get('widened_probes', '?')} widened; "
        f"self-scan {findings} findings ({own} self-referential)"
    )


def summarise_shim(report: dict[str, object]) -> str:
    """How many vectors exist, and how many of them actually catch something.

    Both numbers, always. "10 vectors" on its own is the claim a suite makes
    about itself; "10 catch a lossy translator" is the one worth printing, and
    printing only the first would be the decorative-participation failure the
    polyglot harness already exists to prevent.
    """
    results = report.get("results") or {}
    if not isinstance(results, dict):
        return "no results reported"
    catching = sum(1 for r in results.values() if r.get("discriminates") == "CATCHES")
    return (
        f"{report.get('vectors', '?')} vectors for {report.get('invariants', '?')} "
        f"invariants, {catching} catch a lossy translator; spellings ASSUMED"
    )


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

    # The table detail is a summary, so the underlying report travels with it
    # in --json. Otherwise the compact form would be the only machine-readable
    # record, and a summary is not evidence.
    return {"check": label, "status": status, "detail": detail, "report": report}


def render_table(checks: list[dict[str, object]]) -> list[str]:
    """The status table, as lines.

    Factored out so `verify_readme_transcript.py` can render the same lines
    from a saved report and compare them to what README.md claims this tool
    prints. A README that paraphrases its verifier is a check drifted from its
    substance, and this is how that stays checkable rather than remembered.
    """
    width = max(len(str(c["check"])) for c in checks)
    return [
        f"  {str(c['check']):<{width}}  {str(c['status']):<8} {c['detail']}"
        for c in checks
    ]


def render_footer(checks: list[dict[str, object]]) -> list[str]:
    failed = [c for c in checks if c["status"] == "FAIL"]
    unknown = [c for c in checks if c["status"] == "UNKNOWN"]
    partial = [c for c in checks if c["status"] == "PARTIAL"]
    lines: list[str] = []
    if failed:
        lines.append(f"  {len(failed)} check(s) FAILED.")
    if unknown:
        lines.append(
            f"  {len(unknown)} check(s) UNKNOWN: could not be checked here at all, "
            f"and not to be reported as passing."
        )
    if partial:
        lines.append(
            f"  {len(partial)} check(s) PARTIAL: nothing wrong was found, but not "
            f"everything was observed on this machine."
        )
    if not lines:
        lines.append("  All checks verified on this machine.")
    return lines


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
            summarise_agreement("vectors", "vectors"),
        ),
        summarise_json_tool(
            "rust/python journal history agrees",
            "verify_cross_language_journal.py",
            args.timeout,
            lambda r: summarise_agreement("fields", "fields")(r)
                      + f" after {r.get('events', '?')} events",
        ),
        summarise_json_tool(
            "rust/python gate commits one effect",
            "verify_cross_language_gate.py",
            args.timeout,
            summarise_gate,
        ),
        summarise_json_tool(
            "three deployment modes, one truth",
            "verify_cross_language_projection.py",
            args.timeout,
            summarise_projection,
        ),
        summarise_json_tool(
            "a finding blocks but cannot authorize",
            "verify_security_evidence.py",
            args.timeout,
            summarise_security,
        ),
        summarise_json_tool(
            "shim vectors define and discriminate",
            "verify_responses_vectors.py",
            args.timeout,
            summarise_shim,
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
        summarise_tournament(args.timeout),
        summarise_agent(args.timeout),
    ]

    failed = [c for c in checks if c["status"] == "FAIL"]
    unknown = [c for c in checks if c["status"] == "UNKNOWN"]
    partial = [c for c in checks if c["status"] == "PARTIAL"]

    if args.json:
        print(json.dumps({"checks": checks, "failed": len(failed),
                          "unknown": len(unknown), "partial": len(partial)}, indent=2))
    else:
        print()
        for line in render_table(checks):
            print(line)
        print()
        for line in render_footer(checks):
            print(line)
        print()

    if failed:
        return 1
    return 2 if unknown or partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
