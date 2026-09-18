"""Concurrent conflicting attempts must produce at most one authoritative effect.

This is Phase B's exit criterion, and it is the claim the dual-peer design
stands or falls on. Everything else in the gate is checkable by one caller at a
time; this is the property that only shows up when two of them race.

Each test below runs two *separate connections* to one database file, because
that is what two peers actually are. A single connection shared between threads
would serialise inside the driver and prove nothing about the gate.

What "exactly one wins" must mean here is stronger than "no crash": the loser
has to lose *by the gate's own rule*, with the message the rule carries, not by
a database lock error. A gate that only worked because SQLite happened to return
SQLITE_BUSY would be relying on an implementation detail rather than on a
design.

Stdlib unittest and threading only.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from b1_authority import (  # noqa: E402
    AuthorityEnvelope,
    CommitGate,
    GateError,
    PermitSpent,
    TransitionProof,
)
from b1_policy import Capability, CapabilityPolicy  # noqa: E402
from b1_protocol.canonical import digest_value  # noqa: E402
from b1_state import RootJournal  # noqa: E402

STATE = digest_value({"tree": "clean"})
PLAN = digest_value({"phase": "B"})
OBSERVED = digest_value({"bytes": 12})

# Long enough that a losing writer waits for the winner's commit rather than
# giving up. Losing on a timeout would look like the right answer for the wrong
# reason, so the tests assert on the refusal message instead of the exit path.
BUSY_TIMEOUT_MS = 20_000

# Both peers must be handed the *same* policy: opening the gate under a
# different one now raises, which is itself the point of the policy layer.
POLICY = CapabilityPolicy(
    policy_id="concurrency-tests",
    allow=(Capability(action="fs.write", scope=("docs/**",)),),
)


def authority() -> AuthorityEnvelope:
    return AuthorityEnvelope(
        authority_id="auth-1",
        proposed_action="fs.write",
        target="docs/x.md",
        scope=("docs/**",),
        expected_effect="docs/x.md contains the plan",
        state_digest=STATE,
        plan_digest=PLAN,
        policy_digest=POLICY.digest(),
        causal_objective="prove the gate linearises",
        persistence_class="REVERSIBLE",
        granted_at_epoch=0,
    )


class PeerConnection:
    """One peer: its own connection, its own gate, one shared database file."""

    def __init__(self, path: Path) -> None:
        self.journal = RootJournal(path)
        self.journal.connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        self.gate = CommitGate(self.journal, POLICY)

    def close(self) -> None:
        self.journal.close()


class ConcurrencyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "root.db"
        setup = PeerConnection(self.path)
        self.authority_digest = setup.gate.grant(
            authority(), event_id="evt-grant", granted_by="user"
        )
        setup.close()

    def tearDown(self) -> None:
        self._dir.cleanup()

    def race(self, work) -> list[tuple[str, object]]:
        """Run ``work`` on two peers at once and collect what each got.

        A barrier is used so both peers reach the contended operation together
        rather than one finishing before the other starts, which would make the
        test pass without ever contending.
        """
        results: list[tuple[str, object]] = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def run(name: str) -> None:
            peer = PeerConnection(self.path)
            try:
                barrier.wait(timeout=30)
                try:
                    outcome: tuple[str, object] = (name, work(peer, name))
                except Exception as exc:  # noqa: BLE001 - the refusal is the result
                    outcome = (name, exc)
            finally:
                peer.close()
            with lock:
                results.append(outcome)

        threads = [
            threading.Thread(target=run, args=(f"peer-{i}",), daemon=True)
            for i in (1, 2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
            self.assertFalse(thread.is_alive(), "a peer thread did not finish")
        self.assertEqual(len(results), 2)
        return results

    def final_state(self) -> tuple[RootJournal, CommitGate]:
        peer = PeerConnection(self.path)
        self.addCleanup(peer.close)
        return peer.journal, peer.gate


class ConcurrentClaims(ConcurrencyTestCase):
    def test_two_peers_claiming_one_target_yield_exactly_one_permit(self):
        def work(peer: PeerConnection, name: str):
            return peer.gate.claim_permit(
                permit_id=f"permit-{name}",
                authority_digest=self.authority_digest,
                effect_identity="fs-write-1",
                action="fs.write",
                target="docs/x.md",
                observed_state_digest=STATE,
                observed_plan_digest=PLAN,
                executor=name,
                event_id=f"evt-claim-{name}",
            )

        results = self.race(work)
        granted = [r for _, r in results if not isinstance(r, Exception)]
        refused = [r for _, r in results if isinstance(r, Exception)]

        self.assertEqual(len(granted), 1, f"expected one permit, got {results}")
        self.assertEqual(len(refused), 1)

        # The loser must lose by the gate's rule, not by a lock error. If this
        # ever reads "database is locked", the gate is relying on SQLite's
        # contention behaviour instead of on its own check.
        loser = refused[0]
        self.assertIsInstance(loser, GateError, f"lost by the wrong mechanism: {loser!r}")
        self.assertIn("split-brain", str(loser))
        self.assertNotIn("locked", str(loser).lower())

        journal, _ = self.final_state()
        self.assertTrue(journal.verify().ok)

    def test_the_winning_permit_is_the_only_active_one(self):
        def work(peer: PeerConnection, name: str):
            return peer.gate.claim_permit(
                permit_id=f"permit-{name}",
                authority_digest=self.authority_digest,
                effect_identity="fs-write-1",
                action="fs.write",
                target="docs/x.md",
                observed_state_digest=STATE,
                observed_plan_digest=PLAN,
                executor=name,
                event_id=f"evt-claim-{name}",
            )

        self.race(work)
        journal, _ = self.final_state()
        active = journal.connection.execute(
            "SELECT COUNT(*) AS c FROM permits WHERE state='ACTIVE'"
        ).fetchone()["c"]
        self.assertEqual(active, 1)


class ConcurrentConsumption(ConcurrencyTestCase):
    """The case that would double an effect if the gate got it wrong."""

    def setUp(self) -> None:
        super().setUp()
        peer = PeerConnection(self.path)
        try:
            self.permit = peer.gate.claim_permit(
                permit_id="permit-1",
                authority_digest=self.authority_digest,
                effect_identity="fs-write-1",
                action="fs.write",
                target="docs/x.md",
                observed_state_digest=STATE,
                observed_plan_digest=PLAN,
                executor="setup",
                event_id="evt-claim",
            )
            self.permit_digest = self.permit.digest()
            self.fence = self.permit.fencing_epoch
        finally:
            peer.close()

    def test_two_peers_consuming_one_permit_commit_exactly_one_effect(self):
        def work(peer: PeerConnection, name: str):
            proof = TransitionProof(
                permit_id="permit-1",
                permit_digest=self.permit_digest,
                effect_identity="fs-write-1",
                target="docs/x.md",
                fencing_epoch=self.fence,
                receipt_status="SUCCEEDED",
                postcondition_verified=True,
                observed_effect_digest=OBSERVED,
                postcondition_evidence=(f"readback:{name}",),
            )
            return peer.gate.consume(
                proof,
                observed_state_digest=STATE,
                observed_plan_digest=PLAN,
                executor=name,
                event_id=f"evt-report-{name}",
            )

        results = self.race(work)
        committed = [r for _, r in results if not isinstance(r, Exception)]
        refused = [r for _, r in results if isinstance(r, Exception)]

        self.assertEqual(len(committed), 1, f"expected one commit, got {results}")
        self.assertEqual(committed[0], ("VERIFIED", "VERIFIED"))
        self.assertEqual(len(refused), 1)
        self.assertIsInstance(
            refused[0], PermitSpent, f"lost by the wrong mechanism: {refused[0]!r}"
        )

        journal, gate = self.final_state()
        self.assertEqual(gate.permit_state("permit-1"), "CONSUMED")

        # The decisive assertion: one effect in history, not two. A duplicated
        # effect record here would mean the same action was authoritatively
        # recorded twice.
        effects = [
            r for r in journal.read_all() if r.envelope.effect_identity == "fs-write-1"
        ]
        self.assertEqual(len(effects), 1, "an effect was committed twice")
        self.assertTrue(journal.verify().ok)

    def test_the_journal_chain_survives_the_race(self):
        # Two peers appending through one lease must still produce a contiguous,
        # verifiable chain. A torn chain would mean the lease is not actually
        # serialising the appends.
        def work(peer: PeerConnection, name: str):
            try:
                proof = TransitionProof(
                    permit_id="permit-1",
                    permit_digest=self.permit_digest,
                    effect_identity="fs-write-1",
                    target="docs/x.md",
                    fencing_epoch=self.fence,
                    receipt_status="SUCCEEDED",
                    postcondition_verified=True,
                    observed_effect_digest=OBSERVED,
                    postcondition_evidence=(f"readback:{name}",),
                )
                return peer.gate.consume(
                    proof, observed_state_digest=STATE, observed_plan_digest=PLAN,
                    executor=name, event_id=f"evt-report-{name}",
                )
            except Exception:
                # Whichever peer loses still records a proposal, so both peers
                # touch the journal during the race.
                peer.gate.propose(
                    event_id=f"evt-fallback-{name}", proposer=name,
                    action="fs.write", target="docs/x.md",
                    rationale="lost the race; re-proposing",
                )
                raise

        self.race(work)
        journal, _ = self.final_state()
        report = journal.verify()
        self.assertTrue(report.ok, report.findings)
        self.assertEqual(report.record_count, journal.head()[1])


class LeaseIsolation(ConcurrencyTestCase):
    def test_a_second_writer_cannot_interleave_inside_a_lease(self):
        """A held lease must exclude another connection's write, not merge with it."""
        other = PeerConnection(self.path)
        self.addCleanup(other.close)
        other.journal.connection.execute("PRAGMA busy_timeout=200")

        holder = PeerConnection(self.path)
        self.addCleanup(holder.close)

        with holder.journal.lease():
            holder.journal.connection.execute(
                "INSERT INTO gate_meta(key,value) VALUES('probe','held')"
            )
            with self.assertRaises(sqlite3.OperationalError) as caught:
                with other.journal.lease():
                    other.journal.connection.execute(
                        "INSERT INTO gate_meta(key,value) VALUES('probe2','other')"
                    )
            self.assertIn("locked", str(caught.exception).lower())

        # The holder's write survived; the excluded one left nothing behind.
        rows = {
            str(r["key"])
            for r in holder.journal.connection.execute(
                "SELECT key FROM gate_meta"
            ).fetchall()
        }
        self.assertIn("probe", rows)
        self.assertNotIn("probe2", rows)


if __name__ == "__main__":
    unittest.main()
