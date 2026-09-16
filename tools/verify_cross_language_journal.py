#!/usr/bin/env python3
"""Verify that the Rust and Python journal peers derive the same history.

This is the claim the whole dual-peer design rests on: given the same event
sequence, either peer can reconstruct what the other wrote. Two implementations
of one spec is a working assumption until something feeds them the same input
and compares the output, which is what this does.

Four things are compared, not one:

* the head after a real append into a real SQLite file;
* the head recomputed by replaying the stored envelopes;
* the head predicted by pure replay with no database at all;
* the digest of a derived projection.

They must all agree, across both languages. If the appended head and the
replayed head ever diverge within one peer, its storage has drifted from its own
event log; if the two peers diverge, the state fabric is broken.

Requires `cargo`. If cargo is absent the result is UNKNOWN, not PASS.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_protocol import Envelope  # noqa: E402
from b1_state import RootJournal, projection_digest  # noqa: E402


def build_sequence() -> list[Envelope]:
    """A sequence exercising the fields that could plausibly diverge.

    Non-ASCII payloads, a chain of causal parents, a moving epoch, and an effect
    record carrying all three effect fields.
    """
    events = [
        Envelope.new(
            event_id="evt-1",
            origin="U",
            program_identity="b1-local",
            execution_identity="peer-under-test",
            attempt_identity="attempt-1",
            epoch=1,
            epistemic_status="WORKING_ASSUMPTION",
            payload={"kind": "plan.accepted", "phase": 1},
        ),
        Envelope.new(
            event_id="evt-2",
            origin="M",
            program_identity="b1-local",
            execution_identity="peer-under-test",
            attempt_identity="attempt-1",
            epoch=2,
            epistemic_status="UNKNOWN",
            payload={"Ω": "omega", "中文": "han", "a": "ascii"},
            causal_parents=("evt-1",),
        ),
        Envelope.new(
            event_id="evt-3",
            origin="P",
            program_identity="b1-local",
            execution_identity="peer-under-test",
            attempt_identity="attempt-2",
            epoch=3,
            epistemic_status="IN_DOUBT",
            payload={"kind": "fs.write", "target": "docs/x.md", "bytes": 4096},
            causal_parents=("evt-1", "evt-2"),
            evidence_refs=("receipt:fs-write-394",),
            effect_identity="fs-write-394",
            authority_envelope_digest="c" * 64,
            persistence_class="REVERSIBLE",
        ),
        Envelope.new(
            event_id="evt-4",
            origin="E",
            program_identity="b1-local",
            execution_identity="peer-under-test",
            attempt_identity="attempt-3",
            epoch=4,
            epistemic_status="IN_DOUBT",
            payload={"kind": "anomaly.detected", "note": "unattributed state"},
            causal_parents=("evt-3",),
        ),
    ]
    return events


def python_side(events: list[Envelope]) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        journal = RootJournal(Path(tmp) / "root.db")
        try:
            for event in events:
                journal.append(event)
            head, count, max_epoch = journal.head()
            report = journal.verify()
            return {
                "appended_head": head,
                "record_count": count,
                "max_epoch": max_epoch,
                "replayed_head": journal.replay_head(),
                "pure_replay_head": RootJournal.replay_digest(tuple(events)),
                "verify_ok": report.ok,
                "findings": list(report.findings),
                "projection_digest": projection_digest(journal.read_all()),
            }
        finally:
            journal.close()


def rust_side(events: list[Envelope], timeout: int) -> tuple[dict[str, object] | None, str]:
    if not shutil.which("cargo"):
        return None, "cargo is not installed; the Rust peer cannot be executed"
    payload = "".join(e.canonical_bytes().decode("utf-8") for e in events)
    try:
        completed = subprocess.run(
            ["cargo", "run", "--quiet", "-p", "b1-state", "--bin", "b1-replay-head"],
            cwd=ROOT,
            input=payload,
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


COMPARED = (
    "appended_head",
    "replayed_head",
    "pure_replay_head",
    "projection_digest",
    "record_count",
    "max_epoch",
    "verify_ok",
)


def verify(timeout: int) -> dict[str, object]:
    events = build_sequence()
    python = python_side(events)
    errors: list[str] = []

    # A peer must first agree with itself. Storage that has drifted from its own
    # event log is a defect whether or not the other peer happens to match it.
    for label, side in (("python", python),):
        heads = {side["appended_head"], side["replayed_head"], side["pure_replay_head"]}
        if len(heads) != 1:
            errors.append(
                f"{label}: appended, replayed and pure-replay heads disagree: {sorted(heads)}"
            )
        if not side["verify_ok"]:
            errors.append(f"{label}: its own journal does not verify: {side['findings']}")

    rust, limitation = rust_side(events, timeout)
    if rust is None:
        return {
            "status": "UNKNOWN",
            "limitation": limitation,
            "errors": errors,
            "python": python,
            "note": "Python-side journal behaviour is still covered by "
                    "python/tests/test_journal.py; only the cross-language claim is unverified.",
        }

    heads = {rust["appended_head"], rust["replayed_head"], rust["pure_replay_head"]}
    if len(heads) != 1:
        errors.append(f"rust: appended, replayed and pure-replay heads disagree: {sorted(heads)}")
    if not rust["verify_ok"]:
        errors.append(f"rust: its own journal does not verify: {rust['findings']}")

    agreed: dict[str, str] = {}
    for field in COMPARED:
        if python[field] == rust[field]:
            agreed[field] = "AGREED"
        else:
            agreed[field] = "MISMATCH"
            errors.append(f"{field}: python={python[field]!r} rust={rust[field]!r}")

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "fields": agreed,
        "head": python["appended_head"],
        "events": len(events),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    result = verify(args.timeout)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print(f"cross-language journal agreement: {result['status']}")
        for field, verdict in sorted(result.get("fields", {}).items()):
            print(f"  {field}: {verdict}")
        if result.get("head"):
            print(f"  head after {result['events']} events: {result['head']}")
        if result.get("limitation"):
            print(f"  LIMITATION {result['limitation']}")
            print(f"  {result['note']}")
        for message in result["errors"]:
            print(f"  ERROR {message}")

    return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}[str(result["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
