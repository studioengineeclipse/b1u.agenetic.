"""The work runner: one task, from proposal to verified effect.

This is where B1's layers become a system. Everything below it decides, records
or refuses; this joins them into a single path a task travels:

    propose -> tournament -> adjudicate -> authority -> permit
            -> execute -> read back -> consume -> journal

Each arrow is a place something can refuse, and every refusal leaves the world
and the journal untouched.

What makes this more than plumbing
----------------------------------
The state digest is computed by ``executor.observe()`` from the **real**
filesystem, and the authority envelope is bound to it. So:

* Authorize a write against a file, let something else change that file, and
  the gate refuses at effect time — not because a flag was set, but because the
  world no longer matches what was authorized.
* The postcondition is established by reading the file back, not by the write
  call returning.

Both are tested. ``test_a_file_changed_after_authorization_refuses_the_effect``
is the one worth reading: it is the whole design in fifteen lines.

Authorization is not automatic
------------------------------
``run`` will not grant authority to itself. A caller supplies an
:class:`AuthorityDecision`, and the default decision is to refuse. A runner
that manufactured its own authorization would make the gate decorative, and the
easiest way to end up with that is to let "the user said go" mean "every
effect this task implies is approved".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from b1_authority import (
    AuthorityEnvelope,
    AuthorityStale,
    CommitGate,
    GateError,
    TransitionProof,
)
from b1_policy import Capability, CapabilityPolicy
from b1_protocol.canonical import digest_value
from b1_state import RootJournal
from b1_tournament import TaskClass, Tournament, Verdict

from .effects import Attempt, EffectError, EffectExecutor

__all__ = [
    "AuthorityDecision",
    "WorkOutcome",
    "WorkRunner",
    "always_refuse",
]


@dataclass(frozen=True, slots=True)
class AuthorityDecision:
    """A human's answer to one specific authorization request."""

    granted: bool
    reason: str = ""
    # Bounded by default. An authorization with no expiry outlives the
    # circumstances that justified it, and the epoch is the cheapest bound
    # available that does not need a clock.
    expires_after_epochs: int | None = 16


def always_refuse(envelope: AuthorityEnvelope) -> AuthorityDecision:
    """The default. An effect nobody approved does not happen."""
    return AuthorityDecision(
        granted=False,
        reason=(
            "no authorization decision was supplied, so the runner refused. "
            "This is the default on purpose: a runner that granted authority to "
            "itself would make the gate decorative."
        ),
    )


@dataclass
class WorkOutcome:
    """Everything that happened, in the order it happened."""

    task: str
    verdict: str
    epistemic_status: str
    effect_attempted: bool = False
    effect_outcome: str | None = None
    receipt_status: str | None = None
    postcondition_verified: bool | None = None
    refusal: str | None = None
    winner: str | None = None
    journal_head: str | None = None
    journal_records: int = 0
    steps: list[str] = field(default_factory=list)

    def note(self, step: str) -> None:
        self.steps.append(step)


class WorkRunner:
    """Runs one task all the way through, or refuses somewhere along it."""

    def __init__(
        self,
        *,
        journal: RootJournal,
        gate: CommitGate,
        tournament: Tournament,
        executor: EffectExecutor,
        decide: Callable[[AuthorityEnvelope], AuthorityDecision] = always_refuse,
    ) -> None:
        self.journal = journal
        self.gate = gate
        self.tournament = tournament
        self.executor = executor
        self.decide = decide
        self._counter = 0

    def _event_id(self, label: str) -> str:
        self._counter += 1
        return f"{label}-{self._counter:04d}"

    def run(
        self,
        *,
        task: str,
        target: str,
        task_class: TaskClass = TaskClass.T4_REVERSIBLE_EFFECT,
        objective: str,
        scope: tuple[str, ...],
        persistence_class: str = "REVERSIBLE",
        role: str = "generalist",
    ) -> WorkOutcome:
        outcome = WorkOutcome(task=task, verdict="", epistemic_status="")

        # -- propose. Analysis is not an effect, so this is never gated. -----
        self.gate.propose(
            event_id=self._event_id("propose"),
            proposer="b1-work-runner",
            action=self.executor.action,
            target=target,
            rationale=objective,
        )
        outcome.note("proposed")

        # -- decide what to do, without doing it -----------------------------
        adjudication = self.tournament.run(task=task, task_class=task_class, role=role)
        outcome.verdict = adjudication.verdict.value
        outcome.epistemic_status = adjudication.epistemic_status
        outcome.winner = (
            adjudication.winner.candidate_id if adjudication.winner else None
        )
        outcome.note(f"tournament: {adjudication.verdict.value}")

        if adjudication.winner is None:
            outcome.refusal = adjudication.reason
            return self._finish(outcome)

        if adjudication.verdict not in (
            Verdict.ACCEPT,
            Verdict.ACCEPT_WITH_CAVEATS,
            Verdict.AUTHORIZATION_REQUIRED,
        ):
            outcome.refusal = f"tournament did not produce an actionable result: {adjudication.reason}"
            return self._finish(outcome)

        payload = adjudication.winner.text

        # -- bind an authority envelope to the world as it is right now ------
        # observe() reads the real filesystem. That is what makes staleness a
        # property of the world rather than a flag.
        state_digest = self.executor.observe(target)
        plan_digest = digest_value(
            {"task": task, "payload": payload, "action": self.executor.action}
        )
        epoch = max(0, self.journal.head()[2])

        # -- ask the standing policy before asking a person ------------------
        # A capability the policy forbids is refused here, before an envelope is
        # ever put in front of someone. Presenting an approval prompt for
        # something that would be refused anyway is how approval prompts stop
        # being read.
        policy_decision = self.gate.policy.decide(
            self.executor.action, target, persistence_class
        )
        if not policy_decision.allowed:
            outcome.refusal = policy_decision.reason
            outcome.note("policy: denied")
            return self._finish(outcome)
        outcome.note(f"policy: permitted ({policy_decision.matched})")

        envelope = AuthorityEnvelope(
            authority_id=f"auth-{digest_value(task + target)[:12]}",
            proposed_action=self.executor.action,
            target=target,
            scope=scope,
            expected_effect=f"{target} contains the adjudicated answer",
            state_digest=state_digest,
            plan_digest=plan_digest,
            policy_digest=self.gate.policy.digest(),
            causal_objective=objective,
            persistence_class=persistence_class,
            granted_at_epoch=epoch,
        )

        decision = self.decide(envelope)
        if not decision.granted:
            outcome.refusal = decision.reason or "authorization refused"
            outcome.note("authorization refused")
            return self._finish(outcome)

        if decision.expires_after_epochs is not None:
            from dataclasses import replace

            envelope = replace(
                envelope, expires_at_epoch=epoch + decision.expires_after_epochs
            )

        authority_digest = self.gate.grant(
            envelope, event_id=self._event_id("grant"), granted_by="user"
        )
        outcome.note("authority granted")

        # -- claim a one-time fenced permit ----------------------------------
        effect_identity = f"{self.executor.action}:{target}:{plan_digest[:12]}"
        try:
            permit = self.gate.claim_permit(
                permit_id=f"permit-{self._counter:04d}-{plan_digest[:8]}",
                authority_digest=authority_digest,
                effect_identity=effect_identity,
                action=self.executor.action,
                target=target,
                observed_state_digest=self.executor.observe(target),
                observed_plan_digest=plan_digest,
                executor="b1-work-runner",
                event_id=self._event_id("claim"),
            )
        except (GateError, AuthorityStale) as exc:
            outcome.refusal = f"permit refused: {exc}"
            outcome.note("permit refused")
            return self._finish(outcome)
        outcome.note(f"permit issued (fence {permit.fencing_epoch})")

        # Observe once more, immediately before acting. This value — not the
        # one captured at grant time — is what consume revalidates against.
        #
        # The distinction is the whole point of the second check, and it is
        # easy to get wrong in a way that quietly disables it. Passing the
        # grant-time digest to consume would always match, so the check would
        # pass unconditionally. Re-observing *after* the write would never
        # match, because B1's own effect changed the file, so the check would
        # fail unconditionally. Neither reads the property that matters.
        #
        # What matters is: was the world still as authorized at the instant
        # before B1 acted? That is this digest, and it catches the window a
        # claim-time-only check leaves open — something else changing the
        # target between the permit issuing and the effect landing.
        pre_effect_digest = self.executor.observe(target)

        # -- act. This is the only line in the method that touches the world.
        outcome.effect_attempted = True
        try:
            attempt: Attempt = self.executor.apply(target, payload)
        except EffectError as exc:
            # Refused before touching anything, so the world is unchanged and
            # the permit stays live for a corrected retry.
            outcome.refusal = f"effect refused: {exc}"
            outcome.note("effect refused before execution")
            return self._finish(outcome)
        outcome.receipt_status = attempt.status
        outcome.note(f"receipt: {attempt.status}")

        # -- read back. Only this can say the objective holds. ---------------
        holds, observed_digest = self.executor.verify(target, payload)
        outcome.postcondition_verified = holds
        outcome.note(f"postcondition: {'holds' if holds else 'does not hold'}")

        proof = TransitionProof(
            permit_id=permit.permit_id,
            permit_digest=permit.digest(),
            effect_identity=effect_identity,
            target=target,
            fencing_epoch=permit.fencing_epoch,
            receipt_status=attempt.status,
            postcondition_verified=holds,
            observed_effect_digest=observed_digest,
            postcondition_evidence=(f"readback:{target}",) if holds else (),
        )

        # -- consume. Authority is revalidated here, against the world now. --
        try:
            effect_outcome, status = self.gate.consume(
                proof,
                observed_state_digest=pre_effect_digest,
                observed_plan_digest=plan_digest,
                executor="b1-work-runner",
                event_id=self._event_id("report"),
            )
        except (GateError, AuthorityStale) as exc:
            outcome.refusal = f"effect not recorded as authorized: {exc}"
            outcome.note("consume refused")
            return self._finish(outcome)

        outcome.effect_outcome = effect_outcome
        outcome.epistemic_status = status
        outcome.note(f"recorded: {effect_outcome} / {status}")
        return self._finish(outcome)

    def _finish(self, outcome: WorkOutcome) -> WorkOutcome:
        head, count, _ = self.journal.head()
        outcome.journal_head = head
        outcome.journal_records = count
        return outcome


def workspace_notes_policy(scope: tuple[str, ...] = ("notes/**",)) -> CapabilityPolicy:
    """The narrowest policy that lets the demo do its one job.

    Reversible file writes, under one directory, and nothing else. Offered as a
    named function rather than a default argument so that what B1 is permitted
    to do is something a reader can look up, and something a caller has to pass.
    """
    return CapabilityPolicy(
        policy_id="workspace-notes",
        allow=(
            Capability(
                action="fs.write",
                scope=scope,
                max_persistence_class="REVERSIBLE",
                reason="the runner's only job is writing an adjudicated answer to a note",
            ),
        ),
        description="Reversible note writes inside one directory. Nothing else.",
    )


def workspace_runner(
    *,
    root: Path,
    journal_path: Path,
    tournament: Tournament,
    decide: Callable[[AuthorityEnvelope], AuthorityDecision] = always_refuse,
    policy: CapabilityPolicy | None = None,
) -> tuple[WorkRunner, RootJournal]:
    """Assemble a runner over a workspace directory.

    Returns the journal too, because the caller owns closing it — a runner that
    closed the journal it was handed would break a caller that wanted to inspect
    history afterwards, which is exactly when history matters most.
    """
    from .effects import FileWriteEffect

    journal = RootJournal(journal_path)
    gate = CommitGate(journal, policy or workspace_notes_policy())
    runner = WorkRunner(
        journal=journal,
        gate=gate,
        tournament=tournament,
        executor=FileWriteEffect(root),
        decide=decide,
    )
    return runner, journal
