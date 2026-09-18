"""The work runner end to end: task in, verified effect out, or a refusal.

The test worth reading first is
``test_a_file_changed_after_authorization_refuses_the_effect``. It is the whole
design in a few lines: authority is bound to a digest of the *real* file, so
changing that file between authorization and execution makes the authorization
stale — not because a flag was set, but because the world no longer matches
what was approved.

Stdlib unittest only. No model server is contacted.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from b1_models import DeterministicProvider, ModelRegistry  # noqa: E402
from b1_tournament import (  # noqa: E402
    Candidate,
    TaskClass,
    Tournament,
    Verifier,
    VerifierResult,
)
from b1_work import (  # noqa: E402
    AuthorityDecision,
    FileWriteEffect,
    OutsideScope,
    WorkRunner,
    always_refuse,
    workspace_runner,
)

TASK = "write the phrase the project needs"
ANSWER = "canonical bytes are the contract"


class AcceptsAnything(Verifier):
    """A conclusive check that passes. Present so the tournament has *some*
    deterministic evidence; what it checks is not the point of these tests."""

    verifier_id = "accepts-anything"

    def check(self, candidate: Candidate, task: str) -> VerifierResult:
        return VerifierResult(
            verifier_id=self.verifier_id,
            candidate_id=candidate.candidate_id,
            passed=True,
        )


def tournament() -> Tournament:
    return Tournament(
        registry=ModelRegistry(),
        verifiers=(AcceptsAnything(),),
        deterministic_participant=DeterministicProvider(answers={TASK: ANSWER}),
    )


def approve(envelope) -> AuthorityDecision:
    return AuthorityDecision(granted=True, reason="approved for the test")


class WorkTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name) / "workspace"
        self.root.mkdir()
        self.journal_path = Path(self._dir.name) / "root.db"
        self.journal = None

    def tearDown(self) -> None:
        if self.journal is not None:
            self.journal.close()
        self._dir.cleanup()

    def runner(self, decide=approve, policy=None) -> WorkRunner:
        runner, journal = workspace_runner(
            root=self.root,
            journal_path=self.journal_path,
            tournament=tournament(),
            decide=decide,
            policy=policy,
        )
        self.journal = journal
        return runner

    def run_task(self, runner: WorkRunner, target: str = "notes/answer.md", **overrides):
        kwargs = dict(
            task=TASK,
            target=target,
            objective="record the adjudicated answer",
            scope=("notes/**",),
        )
        kwargs.update(overrides)
        return runner.run(**kwargs)


class HappyPath(WorkTestCase):
    def test_a_task_becomes_a_verified_effect_on_disk(self):
        outcome = self.run_task(self.runner())

        self.assertTrue(outcome.effect_attempted)
        self.assertEqual(outcome.receipt_status, "SUCCEEDED")
        self.assertTrue(outcome.postcondition_verified)
        self.assertEqual(outcome.effect_outcome, "VERIFIED")
        self.assertEqual(outcome.epistemic_status, "VERIFIED")

        # The world actually changed, which is the point.
        written = (self.root / "notes/answer.md").read_text(encoding="utf-8")
        self.assertEqual(written, ANSWER)
        self.assertTrue(self.journal.verify().ok)

    def test_the_effect_record_cites_the_authority_that_permitted_it(self):
        self.run_task(self.runner())
        effects = [
            r.envelope for r in self.journal.read_all()
            if r.envelope.effect_identity is not None
        ]
        self.assertEqual(len(effects), 1)
        self.assertIsNotNone(effects[0].authority_envelope_digest)
        self.assertEqual(effects[0].persistence_class, "REVERSIBLE")
        self.assertIn("readback:notes/answer.md", effects[0].evidence_refs)

    def test_every_stage_is_recorded_in_order(self):
        outcome = self.run_task(self.runner())
        self.assertEqual(
            outcome.steps[:4],
            [
                "proposed",
                "tournament: AUTHORIZATION_REQUIRED",
                "policy: permitted (allow:fs.write)",
                "authority granted",
            ],
        )
        self.assertIn("recorded: VERIFIED / VERIFIED", outcome.steps)


class NothingHappensWithoutAuthorization(WorkTestCase):
    def test_the_default_decision_refuses_and_writes_nothing(self):
        outcome = self.run_task(self.runner(decide=always_refuse))

        self.assertFalse(outcome.effect_attempted)
        self.assertIn("would make the gate decorative", outcome.refusal)
        self.assertFalse((self.root / "notes/answer.md").exists())

    def test_a_refusal_still_leaves_a_verifiable_journal(self):
        # The proposal and the tournament happened; only the effect did not.
        self.run_task(self.runner(decide=always_refuse))
        self.assertTrue(self.journal.verify().ok)
        self.assertGreater(self.journal.head()[1], 0)

    def test_no_effect_record_exists_for_a_refused_task(self):
        self.run_task(self.runner(decide=always_refuse))
        effects = [
            r for r in self.journal.read_all()
            if r.envelope.effect_identity is not None
        ]
        self.assertEqual(effects, [])


class StalenessIsAPropertyOfTheWorld(WorkTestCase):
    def test_a_file_changed_after_authorization_refuses_the_effect(self):
        """The design in a few lines.

        Authority is bound to a digest of the real file. Something else edits
        that file between the grant and the consume. The digest no longer
        matches, so the gate refuses to record the effect as authorized —
        because the authorization no longer describes the world it was given
        for.
        """
        target = "notes/answer.md"
        mutated: list[str] = []

        def approve_then_someone_else_edits(envelope) -> AuthorityDecision:
            # Between authorization and execution, another actor touches the
            # exact file this authority was bound to.
            path = self.root / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("someone else got here first", encoding="utf-8")
            mutated.append("yes")
            return AuthorityDecision(granted=True, reason="approved")

        outcome = self.run_task(
            self.runner(decide=approve_then_someone_else_edits), target=target
        )

        self.assertEqual(mutated, ["yes"])
        self.assertIsNotNone(outcome.refusal)
        self.assertIn("stale", outcome.refusal.lower())
        # The other actor's content survives: B1 did not overwrite it.
        self.assertEqual(
            (self.root / target).read_text(encoding="utf-8"),
            "someone else got here first",
        )

    def test_a_change_between_permit_and_effect_is_caught_at_consume(self):
        """The window a claim-time-only check leaves open.

        Here the world is untouched when the permit issues, so the claim
        succeeds. Something else then edits the target *after* the permit and
        *before* B1 acts. Only the second revalidation, at consume time, can
        see that — and it is the addition ADR-0006 claims over upstream.

        Writing this test found a real bug: the runner was passing the
        grant-time digest to consume, which matches unconditionally, so the
        second check was running and proving nothing.
        """
        target = "notes/answer.md"
        interfered: list[str] = []

        class InterferedWith(FileWriteEffect):
            """Another actor edits the target after the permit is issued.

            The runner observes three times: once to bind the authority, once
            to claim the permit, and once immediately before acting. The
            interference lands on that third call, which is the realistic
            window — the permit was validly issued against an unchanged world,
            and the world moved before the effect did.
            """

            observations = 0

            def observe(inner, path: str) -> str:  # noqa: N805
                inner.observations += 1
                if inner.observations == 3 and not interfered:
                    interfered.append("yes")
                    full = self.root / path
                    full.parent.mkdir(parents=True, exist_ok=True)
                    full.write_text("changed after the permit", encoding="utf-8")
                return super().observe(path)

        runner, journal = workspace_runner(
            root=self.root, journal_path=self.journal_path,
            tournament=tournament(), decide=approve,
        )
        self.journal = journal
        runner.executor = InterferedWith(self.root)

        outcome = self.run_task(runner, target=target)

        self.assertEqual(interfered, ["yes"])
        self.assertTrue(outcome.effect_attempted, "the permit must have been granted")
        self.assertIsNotNone(outcome.refusal)
        self.assertIn("between claim and consume", outcome.refusal)
        self.assertIn("consume refused", outcome.steps)
        # The effect is not recorded as authorized, even though it happened.
        # That is the honest outcome: B1 acted, and cannot claim it was allowed
        # to, so the discrepancy is visible rather than smoothed over.
        self.assertIsNone(outcome.effect_outcome)

    def test_an_unchanged_world_does_not_look_stale(self):
        # The control. Without it, a runner that always refused would pass the
        # test above.
        outcome = self.run_task(self.runner())
        self.assertIsNone(outcome.refusal)
        self.assertEqual(outcome.effect_outcome, "VERIFIED")

    def test_observing_absence_differs_from_observing_content(self):
        executor = FileWriteEffect(self.root)
        absent = executor.observe("notes/x.md")
        (self.root / "notes").mkdir(parents=True, exist_ok=True)
        (self.root / "notes/x.md").write_text("now it exists", encoding="utf-8")
        present = executor.observe("notes/x.md")
        self.assertNotEqual(absent, present)


class ScopeIsEnforcedOnResolvedPaths(WorkTestCase):
    def test_a_traversal_escape_is_refused_before_anything_is_written(self):
        executor = FileWriteEffect(self.root)
        with self.assertRaises(OutsideScope) as caught:
            executor.apply("../escaped.md", "should never be written")
        self.assertIn("outside the workspace root", str(caught.exception))
        self.assertFalse((self.root.parent / "escaped.md").exists())

    def test_a_symlink_pointing_out_of_the_workspace_is_refused(self):
        # The case a string check misses: the path looks contained and is not.
        outside = Path(self._dir.name) / "outside.md"
        outside.write_text("original", encoding="utf-8")
        link = self.root / "looks-fine.md"
        link.symlink_to(outside)

        executor = FileWriteEffect(self.root)
        with self.assertRaises(OutsideScope):
            executor.apply("looks-fine.md", "should never be written")
        self.assertEqual(outside.read_text(encoding="utf-8"), "original")

    def test_a_target_the_standing_policy_forbids_never_reaches_a_person(self):
        """An internally valid envelope is still refused by the policy above it.

        The envelope would be well-formed: it admits its own target, its scope
        is coherent, the tournament produced an answer. Every per-effect check
        would pass. It is refused anyway, because the standing policy permits
        writes under `notes/` and nowhere else -- and it is refused *before* the
        approval callback runs, so nobody is asked to approve something that
        would have been refused regardless of their answer.

        Before this layer existed, the same call wrote the file.
        """
        seen: list[str] = []

        def record_and_approve(envelope) -> AuthorityDecision:
            seen.append(envelope.target)
            return AuthorityDecision(granted=True, reason="approved")

        outcome = self.run_task(
            self.runner(decide=record_and_approve),
            target="secrets/key.txt",
            scope=("secrets/**",),
        )

        self.assertFalse((self.root / "secrets/key.txt").exists())
        self.assertIn("policy: denied", outcome.steps)
        self.assertIn("does not permit it", outcome.refusal)
        self.assertEqual(seen, [], "the approval callback was reached despite the policy")

    def test_the_same_target_is_written_when_the_policy_permits_it(self):
        """The control. Without it the test above passes for any broken runner."""
        from b1_policy import Capability, CapabilityPolicy

        wide = CapabilityPolicy(
            policy_id="also-secrets",
            allow=(Capability(action="fs.write", scope=("secrets/**",)),),
        )
        outcome = self.run_task(
            self.runner(policy=wide),
            target="secrets/key.txt",
            scope=("secrets/**",),
        )
        self.assertTrue((self.root / "secrets/key.txt").exists())
        self.assertEqual(outcome.effect_outcome, "VERIFIED")


class ReceiptIsNotProof(WorkTestCase):
    def test_a_write_reporting_success_still_requires_a_read_back(self):
        """A receipt that lies must not produce VERIFIED."""

        class LyingExecutor(FileWriteEffect):
            action = "fs.write"

            def apply(self, target: str, payload: str):
                from b1_work.effects import Attempt

                # Reports success without doing anything.
                return Attempt(status="SUCCEEDED", detail="claims to have written")

        runner, journal = workspace_runner(
            root=self.root, journal_path=self.journal_path,
            tournament=tournament(), decide=approve,
        )
        self.journal = journal
        runner.executor = LyingExecutor(self.root)

        outcome = self.run_task(runner)

        self.assertEqual(outcome.receipt_status, "SUCCEEDED")
        self.assertFalse(outcome.postcondition_verified)
        # The read-back caught it: success plus unverified postcondition is
        # IN_DOUBT, never VERIFIED.
        self.assertEqual(outcome.effect_outcome, "IN_DOUBT")
        self.assertEqual(outcome.epistemic_status, "IN_DOUBT")
        self.assertFalse((self.root / "notes/answer.md").exists())

    def test_an_unknown_receipt_is_recorded_as_needing_reconciliation(self):
        class UncertainExecutor(FileWriteEffect):
            def apply(self, target: str, payload: str):
                from b1_work.effects import Attempt

                # It may or may not have written. Nobody knows.
                super().apply(target, payload)
                return Attempt(status="UNKNOWN", detail="process died mid-write")

        runner, journal = workspace_runner(
            root=self.root, journal_path=self.journal_path,
            tournament=tournament(), decide=approve,
        )
        self.journal = journal
        runner.executor = UncertainExecutor(self.root)

        outcome = self.run_task(runner)
        self.assertEqual(outcome.effect_outcome, "IN_DOUBT")

        effect_id = next(
            r.envelope.effect_identity for r in journal.read_all()
            if r.envelope.effect_identity
        )
        self.assertTrue(runner.gate.effect_outcome(effect_id)[2],
                        "an unknown receipt must flag the effect for reconciliation")


class Recovery(WorkTestCase):
    def test_a_write_can_be_rolled_back_to_the_captured_prior_state(self):
        target = "notes/answer.md"
        (self.root / "notes").mkdir(parents=True, exist_ok=True)
        (self.root / target).write_text("before", encoding="utf-8")

        executor = FileWriteEffect(self.root)
        executor.apply(target, "after")
        self.assertEqual((self.root / target).read_text(encoding="utf-8"), "after")

        self.assertTrue(executor.rollback(target))
        self.assertEqual((self.root / target).read_text(encoding="utf-8"), "before")

    def test_rolling_back_a_created_file_removes_it(self):
        executor = FileWriteEffect(self.root)
        executor.apply("notes/new.md", "created")
        self.assertTrue((self.root / "notes/new.md").exists())
        self.assertTrue(executor.rollback("notes/new.md"))
        self.assertFalse((self.root / "notes/new.md").exists())

    def test_rolling_back_something_never_written_reports_nothing_restored(self):
        executor = FileWriteEffect(self.root)
        self.assertFalse(executor.rollback("notes/never-touched.md"))


if __name__ == "__main__":
    unittest.main()
