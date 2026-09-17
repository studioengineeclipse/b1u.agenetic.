#!/usr/bin/env python3
"""Race a Rust peer against a Python peer through one commit gate.

This is Phase B's exit criterion, tested the way the design will actually be
used: *"concurrent conflicting attempts produce at most one authoritative effect
commit; a stale executor is rejected."*

`python/tests/test_gate_concurrency.py` already proves this for two Python
connections. That is necessary but not sufficient, because two Python
connections share an implementation. Here the two peers are separate processes
running separate *languages* against one SQLite file, which is what "Rust and
Python as equal computational peers" actually means.

Four things are established, in order, because each one depends on the last:

1.  **Both peers compute the same authority digest.** If they disagree here,
    nothing downstream means anything: an effect authorized under one peer
    would be unauthorized under the other. Checked on bytes, not just digests.
2.  **Exactly one permit issues** when both peers claim the same target.
3.  **Exactly one effect lands in history** when both peers report the same
    permit.
4.  **The loser loses by the gate's rule**, not by a database lock. A gate that
    only worked because SQLite returned SQLITE_BUSY would be relying on an
    implementation detail rather than on a design, so the refusal *kind* is
    asserted, not just the failure.

Requires `cargo`. Without it the result is UNKNOWN, not PASS.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_authority import (  # noqa: E402
    AuthorityEnvelope,
    CommitGate,
    GateError,
    PermitSpent,
    TransitionProof,
)
from b1_protocol.canonical import digest_value  # noqa: E402
from b1_state import RootJournal  # noqa: E402

STATE = digest_value({"tree": "clean"})
PLAN = digest_value({"phase": "B"})
OBSERVED = digest_value({"bytes": 12})
BUSY_TIMEOUT_MS = 20_000

AUTHORITY = dict(
    authority_id="auth-xlang",
    proposed_action="fs.write",
    target="docs/x.md",
    scope=("docs/**",),
    expected_effect="docs/x.md contains the plan",
    state_digest=STATE,
    plan_digest=PLAN,
    causal_objective="prove the gate linearises across languages",
    persistence_class="REVERSIBLE",
    granted_at_epoch=0,
)


def python_peer(db: Path) -> tuple[RootJournal, CommitGate]:
    journal = RootJournal(db)
    journal.connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return journal, CommitGate(journal)


def rust(db: Path, *args: str, timeout: int) -> dict[str, object]:
    completed = subprocess.run(
        ["cargo", "run", "--quiet", "-p", "b1-authority", "--bin", "b1-gate-peer",
         "--", str(db), *args],
        cwd=ROOT, text=True, capture_output=True, timeout=timeout,
    )
    if completed.returncode != 0:
        return {
            "status": "ERROR",
            "message": (completed.stdout + completed.stderr).strip()[:500],
        }
    try:
        return json.loads(completed.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"status": "ERROR", "message": f"unparseable: {completed.stdout[:300]}"}


def prebuild(timeout: int) -> str:
    """Compile the peer before racing, so build time is not part of the race."""
    completed = subprocess.run(
        ["cargo", "build", "--quiet", "-p", "b1-authority", "--bin", "b1-gate-peer"],
        cwd=ROOT, text=True, capture_output=True, timeout=timeout,
    )
    if completed.returncode != 0:
        return (completed.stdout + completed.stderr).strip()[:500]
    return ""


def check_digest_agreement(tmp: Path, timeout: int) -> tuple[bool, str]:
    """Step 1: both peers must compute the same authority digest, byte for byte."""
    envelope = AuthorityEnvelope(**AUTHORITY)
    path = tmp / "authority.json"
    path.write_text(
        json.dumps(envelope.to_canonical_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )

    result = rust(tmp / "unused.db", "digest-authority", str(path), timeout=timeout)
    if result.get("status") != "OK":
        return False, f"the Rust peer could not digest the envelope: {result.get('message')}"

    if str(result["digest"]) != envelope.digest():
        return False, (
            f"authority digests disagree: python={envelope.digest()} "
            f"rust={result['digest']}"
        )
    rust_bytes = bytes.fromhex(str(result["canonical_hex"]))
    if rust_bytes != envelope.canonical_bytes():
        return False, (
            f"authority canonical bytes disagree:\n"
            f"    python: {envelope.canonical_bytes()!r}\n"
            f"    rust:   {rust_bytes!r}"
        )
    return True, ""


def race(python_work, rust_work) -> list[tuple[str, object]]:
    """Run both peers at once, releasing them from a barrier together."""
    results: list[tuple[str, object]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def run(name: str, work) -> None:
        try:
            barrier.wait(timeout=60)
            outcome: tuple[str, object] = (name, work())
        except Exception as exc:  # noqa: BLE001 - a refusal is the result
            outcome = (name, exc)
        with lock:
            results.append(outcome)

    threads = [
        threading.Thread(target=run, args=("python", python_work), daemon=True),
        threading.Thread(target=run, args=("rust", rust_work), daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    return results


def classify(name: str, value: object) -> tuple[str, str, str]:
    """Return ``(peer, verdict, kind)`` for one peer's result."""
    if isinstance(value, Exception):
        kind = {
            PermitSpent: "PERMIT_SPENT",
            GateError: "REFUSED",
        }.get(type(value), type(value).__name__)
        # PermitSpent subclasses GateError, so check it first.
        if isinstance(value, PermitSpent):
            kind = "PERMIT_SPENT"
        return name, "REFUSED", kind
    if isinstance(value, dict):
        if value.get("status") == "OK":
            return name, "GRANTED", ""
        return name, "REFUSED", str(value.get("kind", value.get("status", "?")))
    return name, "GRANTED", ""


def verify(timeout: int) -> dict[str, object]:
    if not shutil.which("cargo"):
        return {
            "status": "UNKNOWN",
            "limitation": "cargo is not installed; the Rust peer cannot be executed",
            "errors": [],
            "note": "python/tests/test_gate_concurrency.py still proves single-effect "
                    "linearisation for two Python connections; only the cross-language "
                    "claim is unverified.",
        }

    build_error = prebuild(timeout)
    if build_error:
        return {
            "status": "FAIL",
            "errors": [f"the Rust peer did not build: {build_error}"],
        }

    errors: list[str] = []
    steps: dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        # Step 1 -- agreement on what an authorization even is.
        ok, reason = check_digest_agreement(tmp, timeout)
        steps["authority_digest_agreement"] = "AGREED" if ok else "MISMATCH"
        if not ok:
            errors.append(reason)
            return {"status": "FAIL", "errors": errors, "steps": steps}

        db = tmp / "root.db"
        journal, gate = python_peer(db)
        try:
            authority_digest = gate.grant(
                AuthorityEnvelope(**AUTHORITY), event_id="evt-grant", granted_by="user"
            )
        finally:
            journal.close()

        # Step 2 -- two peers claim one target. Exactly one permit may issue.
        claim_request = {
            "permit_id": "permit-rust",
            "authority_digest": authority_digest,
            "effect_identity": "fs-write-xlang",
            "action": "fs.write",
            "target": "docs/x.md",
            "observed_state_digest": STATE,
            "observed_plan_digest": PLAN,
            "executor": "rust-peer",
            "event_id": "evt-claim-rust",
        }
        request_path = tmp / "claim.json"
        request_path.write_text(json.dumps(claim_request), encoding="utf-8")

        def python_claim():
            journal, gate = python_peer(db)
            try:
                return gate.claim_permit(
                    permit_id="permit-python",
                    authority_digest=authority_digest,
                    effect_identity="fs-write-xlang",
                    action="fs.write",
                    target="docs/x.md",
                    observed_state_digest=STATE,
                    observed_plan_digest=PLAN,
                    executor="python-peer",
                    event_id="evt-claim-python",
                )
            finally:
                journal.close()

        claim_results = race(
            python_claim, lambda: rust(db, "claim", str(request_path), timeout=timeout)
        )
        if len(claim_results) != 2:
            errors.append(f"a peer did not finish the claim race: {claim_results}")
            return {"status": "FAIL", "errors": errors, "steps": steps}

        classified = [classify(name, value) for name, value in claim_results]
        granted = [c for c in classified if c[1] == "GRANTED"]
        refused = [c for c in classified if c[1] == "REFUSED"]

        steps["concurrent_claim"] = f"{len(granted)} granted, {len(refused)} refused"
        if len(granted) != 1:
            errors.append(
                f"expected exactly one permit from the claim race, got {classified}"
            )
        else:
            loser_kind = refused[0][2]
            if loser_kind != "REFUSED":
                errors.append(
                    f"the losing peer lost by the wrong mechanism: {loser_kind}. "
                    f"A gate relying on SQLITE_BUSY rather than its own check is not a gate."
                )
            steps["claim_loser_kind"] = loser_kind

        # Step 3 -- both peers report the SAME permit. Exactly one effect may land.
        winner = granted[0][0] if granted else None
        if winner is None:
            return {"status": "FAIL", "errors": errors, "steps": steps}

        if winner == "python":
            permit = dict(claim_results)["python"]
            permit_id, permit_digest, fence = (
                permit.permit_id, permit.digest(), permit.fencing_epoch,
            )
        else:
            payload = dict(claim_results)["rust"]
            permit_id = str(payload["permit_id"])
            permit_digest = str(payload["permit_digest"])
            fence = int(payload["fencing_epoch"])  # type: ignore[arg-type]

        consume_request = {
            "permit_id": permit_id,
            "permit_digest": permit_digest,
            "effect_identity": "fs-write-xlang",
            "target": "docs/x.md",
            "fencing_epoch": fence,
            "receipt_status": "SUCCEEDED",
            "postcondition_verified": True,
            "observed_effect_digest": OBSERVED,
            "postcondition_evidence": ["readback:rust"],
            "observed_state_digest": STATE,
            "observed_plan_digest": PLAN,
            "executor": "rust-peer",
            "event_id": "evt-report-rust",
        }
        consume_path = tmp / "consume.json"
        consume_path.write_text(json.dumps(consume_request), encoding="utf-8")

        def python_consume():
            journal, gate = python_peer(db)
            try:
                proof = TransitionProof(
                    permit_id=permit_id,
                    permit_digest=permit_digest,
                    effect_identity="fs-write-xlang",
                    target="docs/x.md",
                    fencing_epoch=fence,
                    receipt_status="SUCCEEDED",
                    postcondition_verified=True,
                    observed_effect_digest=OBSERVED,
                    postcondition_evidence=("readback:python",),
                )
                return gate.consume(
                    proof, observed_state_digest=STATE, observed_plan_digest=PLAN,
                    executor="python-peer", event_id="evt-report-python",
                )
            finally:
                journal.close()

        consume_results = race(
            python_consume, lambda: rust(db, "consume", str(consume_path), timeout=timeout)
        )
        classified = [classify(name, value) for name, value in consume_results]
        committed = [c for c in classified if c[1] == "GRANTED"]
        rejected = [c for c in classified if c[1] == "REFUSED"]

        steps["concurrent_consume"] = f"{len(committed)} committed, {len(rejected)} refused"
        if len(committed) != 1:
            errors.append(
                f"expected exactly one effect commit from the consume race, got {classified}"
            )
        else:
            loser_kind = rejected[0][2]
            if loser_kind != "PERMIT_SPENT":
                errors.append(
                    f"the losing peer lost by the wrong mechanism: {loser_kind}; expected "
                    f"PERMIT_SPENT from the one-time permit check"
                )
            steps["consume_loser_kind"] = loser_kind

        # Step 4 -- history itself. One effect, and a chain both peers verify.
        inspection = rust(db, "inspect", "fs-write-xlang", timeout=timeout)
        journal, gate = python_peer(db)
        try:
            python_report = journal.verify()
            effects = [
                r for r in journal.read_all()
                if r.envelope.effect_identity == "fs-write-xlang"
            ]
            python_head = journal.head()
        finally:
            journal.close()

        if len(effects) != 1:
            errors.append(
                f"history holds {len(effects)} records for one effect identity; an effect "
                f"was committed twice"
            )
        steps["effect_records_in_history"] = str(len(effects))

        if not python_report.ok:
            errors.append(f"the Python peer cannot verify the chain: {python_report.findings}")
        if inspection.get("status") != "OK":
            errors.append(f"the Rust peer could not inspect: {inspection.get('message')}")
        else:
            if not inspection.get("verify_ok"):
                errors.append(f"the Rust peer cannot verify the chain: {inspection['findings']}")
            if str(inspection.get("head_digest")) != python_head[0]:
                errors.append(
                    f"peers disagree on the head after the race: "
                    f"python={python_head[0]} rust={inspection.get('head_digest')}"
                )
            else:
                steps["head_agreement"] = "AGREED"
            steps["final_head"] = python_head[0]

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "steps": steps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()

    result = verify(args.timeout)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print(f"cross-language commit gate: {result['status']}")
        for step, value in (result.get("steps") or {}).items():
            print(f"  {step}: {value}")
        if result.get("limitation"):
            print(f"  LIMITATION {result['limitation']}")
            print(f"  {result['note']}")
        for message in result.get("errors", []):
            print(f"  ERROR {message}")

    return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}[str(result["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
