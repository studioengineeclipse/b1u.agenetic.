"""The Dual-Core Commit Gate: what it allows, and what it refuses.

The refusal cases are the point of this file. A gate that has only ever been
shown a well-formed effect is an untested gate, so each test below tries to get
an unauthorised effect through by one specific route and requires the gate to
say no, by name.

Two of them are the ones that would matter in production:

* ``test_authority_that_goes_stale_between_claim_and_consume_is_refused``
  -- planning-time approval is not permanently sufficient.
* ``test_an_unknown_outcome_blocks_a_retry_until_reconciled``
  -- retrying an unknown effect is how one attempt becomes two.

Stdlib unittest only.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from b1_authority import (  # noqa: E402
    AuthorityEnvelope,
    AuthorityError,
    AuthorityStale,
    CommitGate,
    GateError,
    PermitSpent,
    ReconciliationRequired,
    TransitionProof,
    project_outcome,
)
from b1_protocol.canonical import digest_value  # noqa: E402
from b1_state import RootJournal  # noqa: E402

STATE_A = digest_value({"tree": "clean"})
STATE_B = digest_value({"tree": "dirty"})
PLAN_A = digest_value({"phase": "B"})
PLAN_B = digest_value({"phase": "C"})
OBSERVED = digest_value({"bytes": 12})


def authority(**overrides) -> AuthorityEnvelope:
    kwargs = dict(
        authority_id="auth-1",
        proposed_action="fs.write",
        target="docs/x.md",
        scope=("docs/**",),
        expected_effect="docs/x.md contains the plan",
        state_digest=STATE_A,
        plan_digest=PLAN_A,
        causal_objective="ship phase B",
        persistence_class="REVERSIBLE",
        granted_at_epoch=0,
    )
    kwargs.update(overrides)
    return AuthorityEnvelope(**kwargs)


class GateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.journal = RootJournal(Path(self._dir.name) / "root.db")
        self.gate = CommitGate(self.journal)
        self._event = 0

    def tearDown(self) -> None:
        self.journal.close()
        self._dir.cleanup()

    def event(self) -> str:
        self._event += 1
        return f"evt-{self._event}"

    def granted(self, **overrides) -> str:
        return self.gate.grant(
            authority(**overrides), event_id=self.event(), granted_by="user"
        )

    def claim(self, digest: str, **overrides):
        kwargs = dict(
            permit_id="permit-1",
            authority_digest=digest,
            effect_identity="fs-write-1",
            action="fs.write",
            target="docs/x.md",
            observed_state_digest=STATE_A,
            observed_plan_digest=PLAN_A,
            executor="python-core-1",
            event_id=self.event(),
        )
        kwargs.update(overrides)
        return self.gate.claim_permit(**kwargs)

    def proof(self, permit, **overrides) -> TransitionProof:
        kwargs = dict(
            permit_id=permit.permit_id,
            permit_digest=permit.digest(),
            effect_identity=permit.effect_identity,
            target=permit.target,
            fencing_epoch=permit.fencing_epoch,
            receipt_status="SUCCEEDED",
            postcondition_verified=True,
            observed_effect_digest=OBSERVED,
            postcondition_evidence=("readback:docs/x.md",),
        )
        kwargs.update(overrides)
        return TransitionProof(**kwargs)

    def consume(self, proof, **overrides):
        kwargs = dict(
            observed_state_digest=STATE_A,
            observed_plan_digest=PLAN_A,
            executor="python-core-1",
            event_id=self.event(),
        )
        kwargs.update(overrides)
        return self.gate.consume(proof, **kwargs)


class HappyPath(GateTestCase):
    def test_the_full_cycle_records_a_verified_effect(self):
        self.gate.propose(
            event_id=self.event(), proposer="python-core-1",
            action="fs.write", target="docs/x.md", rationale="because",
        )
        digest = self.granted()
        permit = self.claim(digest)
        outcome, status = self.consume(self.proof(permit))

        self.assertEqual((outcome, status), ("VERIFIED", "VERIFIED"))
        self.assertEqual(self.gate.permit_state("permit-1"), "CONSUMED")
        self.assertTrue(self.journal.verify().ok)

    def test_the_effect_record_carries_its_authority_and_recovery_class(self):
        digest = self.granted()
        permit = self.claim(digest)
        self.consume(self.proof(permit))

        effects = [
            r.envelope for r in self.journal.read_all()
            if r.envelope.effect_identity is not None
        ]
        self.assertEqual(len(effects), 1)
        self.assertEqual(effects[0].authority_envelope_digest, digest)
        self.assertEqual(effects[0].persistence_class, "REVERSIBLE")

    def test_proposing_needs_no_authority(self):
        # Analysis is not a persistent effect, so the gate must not gate it.
        self.gate.propose(
            event_id=self.event(), proposer="rust-core-1",
            action="fs.delete", target="/etc/passwd",
            rationale="considering something it will never be allowed to do",
        )
        self.assertTrue(self.journal.verify().ok)
        self.assertIsNone(self.gate.effect_outcome("fs-write-1"))

    def test_regranting_an_identical_envelope_is_idempotent(self):
        first = self.granted()
        second = self.gate.grant(authority(), event_id=self.event(), granted_by="user")
        self.assertEqual(first, second)


class AuthorityBinding(GateTestCase):
    def test_an_effect_with_no_authority_is_refused(self):
        with self.assertRaises(GateError) as caught:
            self.claim("0" * 64)
        self.assertIn("never seen", str(caught.exception))

    def test_a_different_action_is_refused(self):
        digest = self.granted()
        with self.assertRaises(GateError) as caught:
            self.claim(digest, action="fs.delete")
        self.assertIn("authorizes action", str(caught.exception))

    def test_a_different_target_is_refused(self):
        digest = self.granted()
        with self.assertRaises(GateError) as caught:
            self.claim(digest, target="docs/y.md")
        self.assertIn("does not authorize another", str(caught.exception))

    def test_a_target_outside_scope_cannot_even_be_authorized(self):
        # The envelope must admit its own target, so a mismatched scope is
        # refused at grant time rather than becoming a live authority that
        # nothing can use.
        with self.assertRaises(AuthorityError) as caught:
            authority(target="secrets/key", scope=("docs/**",))
        self.assertIn("outside its own scope", str(caught.exception))

    def test_scope_does_not_let_a_star_cross_a_directory_boundary(self):
        # The reason fnmatch is not used: its * crosses /, so docs/* would
        # admit docs/secrets/key.
        auth = authority(target="docs/a.md", scope=("docs/**",))
        ok, _ = auth.admits("fs.write", "docs/a.md")
        self.assertTrue(ok)
        ok, reason = auth.admits("fs.write", "secrets/key")
        self.assertFalse(ok)

    def test_widening_an_authority_in_place_is_refused(self):
        self.granted()
        with self.assertRaises(GateError) as caught:
            self.gate.grant(
                authority(scope=("**",)), event_id=self.event(), granted_by="user"
            )
        self.assertIn("requires a new id, not an edit", str(caught.exception))

    def test_an_expired_authority_is_refused(self):
        digest = self.granted(granted_at_epoch=0, expires_at_epoch=1)
        with self.assertRaises(AuthorityStale) as caught:
            self.claim(digest)
        self.assertIn("expired", str(caught.exception))


class Staleness(GateTestCase):
    def test_changed_state_makes_the_authority_stale_at_claim_time(self):
        digest = self.granted()
        with self.assertRaises(AuthorityStale) as caught:
            self.claim(digest, observed_state_digest=STATE_B)
        self.assertIn("relevant state changed", str(caught.exception))
        self.assertIn("gate has closed again", str(caught.exception))

    def test_changed_plan_makes_the_authority_stale_at_claim_time(self):
        digest = self.granted()
        with self.assertRaises(AuthorityStale) as caught:
            self.claim(digest, observed_plan_digest=PLAN_B)
        self.assertIn("plan changed", str(caught.exception))

    def test_authority_that_goes_stale_between_claim_and_consume_is_refused(self):
        """The case a claim-time-only check would miss.

        The permit was validly issued. The world then moved while the executor
        was working. Recording that effect as authorized would be recording an
        authorization that no longer describes what happened.
        """
        digest = self.granted()
        permit = self.claim(digest)
        with self.assertRaises(AuthorityStale) as caught:
            self.consume(self.proof(permit), observed_state_digest=STATE_B)
        self.assertIn("went stale between claim and consume", str(caught.exception))

        # And the refusal changed nothing: the permit is still live, so the
        # caller can re-authorize rather than having silently lost it.
        self.assertEqual(self.gate.permit_state("permit-1"), "ACTIVE")
        self.assertIsNone(self.gate.effect_outcome("fs-write-1"))
        self.assertTrue(self.journal.verify().ok)


class PermitDiscipline(GateTestCase):
    def test_a_permit_cannot_be_spent_twice(self):
        digest = self.granted()
        permit = self.claim(digest)
        self.consume(self.proof(permit))
        with self.assertRaises(PermitSpent) as caught:
            self.consume(self.proof(permit))
        self.assertIn("one-time", str(caught.exception))

    def test_a_second_live_permit_for_one_target_is_refused(self):
        digest = self.granted()
        self.claim(digest)
        with self.assertRaises(GateError) as caught:
            self.claim(digest, permit_id="permit-2", effect_identity="fs-write-2")
        self.assertIn("split-brain", str(caught.exception))

    def test_reporting_without_a_permit_is_refused(self):
        digest = self.granted()
        permit = self.claim(digest)
        forged = self.proof(permit, permit_id="permit-never-issued")
        with self.assertRaises(GateError) as caught:
            self.consume(forged)
        self.assertIn("never authorized", str(caught.exception))

    def test_a_proof_describing_a_different_permit_is_refused(self):
        digest = self.granted()
        permit = self.claim(digest)
        with self.assertRaises(GateError) as caught:
            self.consume(self.proof(permit, permit_digest="a" * 64))
        self.assertIn("does not describe the permit it claims", str(caught.exception))

    def test_a_superseded_fence_is_refused(self):
        digest = self.granted()
        permit = self.claim(digest)
        with self.assertRaises(GateError) as caught:
            self.consume(self.proof(permit, fencing_epoch=permit.fencing_epoch + 1))
        self.assertIn("must not report", str(caught.exception))

    def test_a_reused_permit_id_is_refused(self):
        digest = self.granted()
        permit = self.claim(digest)
        self.consume(self.proof(permit))
        with self.assertRaises(GateError) as caught:
            self.claim(digest, effect_identity="fs-write-2")
        self.assertIn("already been issued", str(caught.exception))

    def test_the_domain_fence_increases_with_each_permit(self):
        digest = self.granted()
        permit = self.claim(digest)
        self.consume(self.proof(permit))
        self.assertEqual(self.gate.domain_fence("docs/x.md"), 1)

        second = self.claim(
            digest, permit_id="permit-2", effect_identity="fs-write-2"
        )
        self.assertEqual(second.fencing_epoch, 2)


class ReceiptIsNotProof(GateTestCase):
    """Execution receipt != observed effect != objective postcondition."""

    def test_a_successful_receipt_without_a_verified_postcondition_is_in_doubt(self):
        # The single most common way a system comes to believe something it has
        # not established.
        digest = self.granted()
        permit = self.claim(digest)
        outcome, status = self.consume(
            self.proof(
                permit,
                receipt_status="SUCCEEDED",
                postcondition_verified=False,
                observed_effect_digest=None,
                postcondition_evidence=(),
            )
        )
        self.assertEqual(outcome, "IN_DOUBT")
        self.assertEqual(status, "IN_DOUBT")
        self.assertTrue(self.gate.effect_outcome("fs-write-1")[2])

    def test_claiming_a_verified_postcondition_without_an_observation_is_refused(self):
        with self.assertRaises(AuthorityError) as caught:
            TransitionProof(
                permit_id="permit-1", permit_digest="a" * 64,
                effect_identity="fs-write-1", target="docs/x.md", fencing_epoch=1,
                receipt_status="SUCCEEDED", postcondition_verified=True,
                observed_effect_digest=None, postcondition_evidence=("x",),
            )
        self.assertIn("without having observed", str(caught.exception))

    def test_claiming_a_verified_postcondition_without_evidence_is_refused(self):
        with self.assertRaises(AuthorityError) as caught:
            TransitionProof(
                permit_id="permit-1", permit_digest="a" * 64,
                effect_identity="fs-write-1", target="docs/x.md", fencing_epoch=1,
                receipt_status="SUCCEEDED", postcondition_verified=True,
                observed_effect_digest=OBSERVED, postcondition_evidence=(),
            )
        self.assertIn("must say what establishes it", str(caught.exception))

    def test_a_reported_failure_alone_does_not_prove_nothing_happened(self):
        digest = self.granted()
        permit = self.claim(digest)
        outcome, status = self.consume(
            self.proof(
                permit, receipt_status="FAILED", postcondition_verified=False,
                observed_effect_digest=None, postcondition_evidence=(),
            )
        )
        self.assertEqual(outcome, "IN_DOUBT")
        self.assertTrue(self.gate.effect_outcome("fs-write-1")[2])

    def test_a_failure_whose_state_was_read_back_is_a_known_non_effect(self):
        digest = self.granted()
        permit = self.claim(digest)
        outcome, status = self.consume(
            self.proof(
                permit, receipt_status="FAILED", postcondition_verified=False,
                observed_effect_digest=OBSERVED, postcondition_evidence=(),
            )
        )
        self.assertEqual(outcome, "NO_EFFECT")
        self.assertEqual(status, "VERIFIED")
        self.assertFalse(self.gate.effect_outcome("fs-write-1")[2])

    def test_projection_is_a_pure_function_of_the_proof(self):
        # Spelled out as a table so the discipline is readable in one place.
        cases = [
            ("SUCCEEDED", True, OBSERVED, ("e",), ("VERIFIED", "VERIFIED", False)),
            ("SUCCEEDED", False, None, (), ("IN_DOUBT", "IN_DOUBT", True)),
            ("FAILED", False, OBSERVED, (), ("NO_EFFECT", "VERIFIED", False)),
            ("FAILED", False, None, (), ("IN_DOUBT", "IN_DOUBT", True)),
            ("UNKNOWN", False, None, (), ("IN_DOUBT", "IN_DOUBT", True)),
            # An UNKNOWN receipt stays IN_DOUBT even with an observation: the
            # observation and the receipt may describe different attempts.
            ("UNKNOWN", True, OBSERVED, ("e",), ("IN_DOUBT", "IN_DOUBT", True)),
        ]
        for receipt, verified, observed, evidence, expected in cases:
            with self.subTest(receipt=receipt, verified=verified):
                proof = TransitionProof(
                    permit_id="permit-1", permit_digest="a" * 64,
                    effect_identity="e-1", target="t", fencing_epoch=1,
                    receipt_status=receipt, postcondition_verified=verified,
                    observed_effect_digest=observed, postcondition_evidence=evidence,
                )
                self.assertEqual(project_outcome(proof), expected)


class UnknownEffectReconciliation(GateTestCase):
    def test_an_unknown_outcome_blocks_a_retry_until_reconciled(self):
        """Retrying an unknown effect is how one attempt becomes two."""
        digest = self.granted()
        permit = self.claim(digest)
        outcome, _ = self.consume(
            self.proof(
                permit, receipt_status="UNKNOWN", postcondition_verified=False,
                observed_effect_digest=None, postcondition_evidence=(),
            )
        )
        self.assertEqual(outcome, "IN_DOUBT")

        with self.assertRaises(ReconciliationRequired) as caught:
            self.claim(digest, permit_id="permit-2")
        self.assertIn("one attempt becomes two", str(caught.exception))

    def test_reconciling_clears_the_flag_and_allows_a_retry(self):
        digest = self.granted()
        permit = self.claim(digest)
        self.consume(
            self.proof(
                permit, receipt_status="UNKNOWN", postcondition_verified=False,
                observed_effect_digest=None, postcondition_evidence=(),
            )
        )
        resolved = self.gate.reconcile(
            effect_identity="fs-write-1",
            observed_effect_digest=OBSERVED,
            effect_happened=False,
            evidence=("readback:docs/x.md absent",),
            executor="python-core-1",
            event_id=self.event(),
        )
        self.assertEqual(resolved, "NO_EFFECT")
        self.assertFalse(self.gate.effect_outcome("fs-write-1")[2])
        self.claim(digest, permit_id="permit-2")  # now allowed

    def test_reconciliation_requires_cited_evidence(self):
        digest = self.granted()
        permit = self.claim(digest)
        self.consume(
            self.proof(
                permit, receipt_status="UNKNOWN", postcondition_verified=False,
                observed_effect_digest=None, postcondition_evidence=(),
            )
        )
        with self.assertRaises(GateError) as caught:
            self.gate.reconcile(
                effect_identity="fs-write-1", observed_effect_digest=OBSERVED,
                effect_happened=True, evidence=(), executor="python-core-1",
                event_id=self.event(),
            )
        self.assertIn("cannot be resolved by assertion", str(caught.exception))

    def test_reconciling_a_settled_effect_is_refused(self):
        digest = self.granted()
        permit = self.claim(digest)
        self.consume(self.proof(permit))
        with self.assertRaises(GateError) as caught:
            self.gate.reconcile(
                effect_identity="fs-write-1", observed_effect_digest=OBSERVED,
                effect_happened=True, evidence=("x",), executor="python-core-1",
                event_id=self.event(),
            )
        self.assertIn("already", str(caught.exception))


class RefusalsLeaveNothingBehind(GateTestCase):
    """Every refusal must be atomic. A half-applied refusal is worse than none."""

    def test_every_refusal_leaves_the_journal_verifiable_and_unchanged(self):
        digest = self.granted()
        before_head, before_count, _ = self.journal.head()

        refusals = [
            (GateError, lambda: self.claim("0" * 64)),
            (GateError, lambda: self.claim(digest, action="fs.delete")),
            (GateError, lambda: self.claim(digest, target="docs/y.md")),
            (AuthorityStale, lambda: self.claim(digest, observed_state_digest=STATE_B)),
            (AuthorityStale, lambda: self.claim(digest, observed_plan_digest=PLAN_B)),
        ]
        for error, attempt in refusals:
            with self.subTest(error=error.__name__):
                with self.assertRaises(error):
                    attempt()

        head, count, _ = self.journal.head()
        self.assertEqual((head, count), (before_head, before_count))
        self.assertTrue(self.journal.verify().ok)
        self.assertIsNone(self.gate.permit_state("permit-1"))

    def test_an_unknown_gate_schema_is_refused_rather_than_migrated(self):
        conn = self.journal.connection
        with self.journal.lease():
            conn.execute("UPDATE gate_meta SET value='99' WHERE key='schema_version'")
        with self.assertRaises(GateError) as caught:
            CommitGate(self.journal)
        self.assertIn("will not guess at a migration", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
