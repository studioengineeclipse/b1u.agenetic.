"""The hybrid specialist + competitor tournament, with layered adjudication.

The one rule everything else serves
-----------------------------------
    A model may synthesise an answer, but it cannot overrule failed objective
    evidence.

So adjudication runs in three layers, in this order and no other:

    1. Deterministic verification eliminates candidates. Not "penalises" —
       eliminates. A candidate a compiler rejects is out, however many models
       preferred it.
    2. Model synthesis chooses among what survives. Never among what did not.
    3. The authority layer decides what may actually happen, which is a
       different question from what is true.

Consensus is not evidence
-------------------------
Five models agreeing is five samples from correlated training distributions. It
raises a hypothesis; it settles nothing. `Adjudication.agreement` is recorded
because it is *interesting*, and it is never an input to elimination. The test
`a_unanimous_but_invalid_candidate_loses_to_one_deterministic_rejection` exists
to keep that true.

Risk-adaptive depth
-------------------
Tournamenting everything would be slower without being better, and on a 16 GB
machine it would also be infeasible. Task classes T0-T6 and TX set how much
machinery a task earns, per the handoff's §9.3. The interesting end is TX:
an unclassifiable task fails closed rather than getting a default depth.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from b1_models.provider import Completion, ProviderError

__all__ = [
    "TaskClass",
    "Verdict",
    "Candidate",
    "VerifierResult",
    "Critique",
    "Adjudication",
    "Verifier",
    "Tournament",
    "TournamentError",
    "depth_for",
]


class TournamentError(Exception):
    """The tournament could not be run as configured."""


class TaskClass(Enum):
    """How consequential a task is, which sets how much machinery it earns."""

    T0_DETERMINISTIC = "T0"
    T1_ORDINARY = "T1"
    T2_HARD_REASONING = "T2"
    T3_TOOL_EXECUTION = "T3"
    T4_REVERSIBLE_EFFECT = "T4"
    T5_COMPENSATABLE_EFFECT = "T5"
    T6_IRREVERSIBLE_EFFECT = "T6"
    TX_UNKNOWN = "TX"


class Verdict(Enum):
    """What a tournament may conclude. There is deliberately no forced winner."""

    ACCEPT = "ACCEPT"
    ACCEPT_WITH_CAVEATS = "ACCEPT_WITH_CAVEATS"
    RETRY = "RETRY"
    REPLAN = "REPLAN"
    REJECT = "REJECT"
    IN_DOUBT = "IN_DOUBT"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"


@dataclass(frozen=True, slots=True)
class Depth:
    """How much machinery a task class earns."""

    candidates: int
    critics: int
    require_deterministic_evidence: bool
    require_authorization: bool
    # TX fails closed rather than running at some default depth, because a task
    # nobody could classify is not a task anybody sized.
    fail_closed: bool = False


def depth_for(task_class: TaskClass) -> Depth:
    """Machinery per class, per the handoff's §9.3."""
    return {
        TaskClass.T0_DETERMINISTIC: Depth(1, 0, True, False),
        TaskClass.T1_ORDINARY: Depth(1, 1, False, False),
        TaskClass.T2_HARD_REASONING: Depth(2, 1, False, False),
        TaskClass.T3_TOOL_EXECUTION: Depth(1, 1, True, False),
        TaskClass.T4_REVERSIBLE_EFFECT: Depth(2, 1, True, True),
        TaskClass.T5_COMPENSATABLE_EFFECT: Depth(3, 2, True, True),
        TaskClass.T6_IRREVERSIBLE_EFFECT: Depth(3, 2, True, True),
        TaskClass.TX_UNKNOWN: Depth(0, 0, True, True, fail_closed=True),
    }[task_class]


@dataclass(frozen=True, slots=True)
class Candidate:
    """One proposed answer, from a model or a deterministic participant."""

    candidate_id: str
    source: str
    text: str
    completion: Completion | None = None

    def evidence_ref(self) -> str:
        if self.completion is not None:
            return self.completion.evidence_ref()
        return f"candidate:{self.candidate_id}"


@dataclass(frozen=True, slots=True)
class VerifierResult:
    """A non-model check. This is what eliminates candidates."""

    verifier_id: str
    candidate_id: str
    passed: bool
    detail: str = ""
    # False when the verifier could not run at all — a missing compiler, an
    # unreachable service. Distinct from a failed check, because "we could not
    # look" must never read as "it failed" or as "it passed".
    conclusive: bool = True

    def evidence_ref(self) -> str:
        return f"verifier:{self.verifier_id}#{self.candidate_id}"


@dataclass(frozen=True, slots=True)
class Critique:
    """An adversarial review. Evidence, not a verdict.

    A critic is a model, so its objection is a hypothesis about a candidate,
    not a finding against it. Critiques are recorded and surfaced as caveats;
    they never eliminate on their own. Only a deterministic verifier does that.
    """

    critic_id: str
    candidate_id: str
    objection: str
    completion: Completion | None = None


class Verifier:
    """A deterministic check over a candidate.

    Implementations wrap a compiler, a test run, a schema check, a filesystem
    read-back — anything whose answer does not depend on a model's opinion.
    """

    verifier_id: str = "verifier"

    def check(self, candidate: Candidate, task: str) -> VerifierResult:  # pragma: no cover
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class Adjudication:
    """The outcome, with its reasoning kept inspectable."""

    verdict: Verdict
    task_class: TaskClass
    winner: Candidate | None
    reason: str
    surviving: tuple[str, ...] = ()
    eliminated: tuple[tuple[str, str], ...] = ()
    caveats: tuple[str, ...] = ()
    # Everything cited: verifier results AND the candidates themselves.
    evidence_refs: tuple[str, ...] = ()
    # Only refs from conclusive, passing deterministic verifiers.
    #
    # Kept separate from `evidence_refs` because conflating them is a real bug
    # this suite caught: a model completion is an evidence ref, so grading on
    # "has any evidence" promoted a wholly unverified answer to VERIFIED. That
    # is precisely the receipt-mistaken-for-proof failure, arriving through the
    # back door of a convenience property.
    deterministic_evidence: tuple[str, ...] = ()
    # Recorded because it is interesting, never used to decide anything. See
    # the module docstring.
    agreement: dict[str, int] = field(default_factory=dict)

    @property
    def epistemic_status(self) -> str:
        """The grade B1 would record for this result.

        Only a verdict backed by a conclusive deterministic check earns
        VERIFIED. Everything a model merely preferred is a working assumption,
        which is what it is.
        """
        if self.verdict is Verdict.ACCEPT:
            return "VERIFIED" if self.deterministic_evidence else "WORKING_ASSUMPTION"
        if self.verdict is Verdict.ACCEPT_WITH_CAVEATS:
            return "WORKING_ASSUMPTION"
        if self.verdict in (Verdict.IN_DOUBT, Verdict.RETRY, Verdict.REPLAN):
            return "IN_DOUBT"
        if self.verdict is Verdict.AUTHORIZATION_REQUIRED:
            return "WORKING_ASSUMPTION"
        return "UNKNOWN"


class Tournament:
    """Runs candidates, critics and verifiers, then adjudicates in layers."""

    def __init__(
        self,
        *,
        registry,
        ledger=None,
        verifiers: tuple[Verifier, ...] = (),
        deterministic_participant=None,
    ) -> None:
        self.registry = registry
        self.ledger = ledger
        self.verifiers = verifiers
        # An optional non-model entrant. When a task has a computable answer,
        # this one is frequently right while the models are guessing, so a
        # tournament that could not include it would be systematically worse.
        self.deterministic_participant = deterministic_participant

    # -- candidate generation ------------------------------------------------

    def gather_candidates(
        self, *, task: str, role: str, count: int, context_tokens: int | None = None
    ) -> tuple[tuple[Candidate, ...], tuple[str, ...]]:
        """Collect up to ``count`` candidates. Returns ``(candidates, notes)``.

        Models are loaded one at a time and released, per the handoff's §10:
        the ledger is consulted before each load and a model that will not fit
        is skipped with a note rather than attempted. A provider that is
        unreachable produces a note too — an absent model server is an
        environment fact, and recording it as such keeps it from looking like
        a model that declined to answer.
        """
        candidates: list[Candidate] = []
        notes: list[str] = []

        if self.deterministic_participant is not None:
            try:
                completion = self.deterministic_participant.complete(
                    model_id=self.deterministic_participant.available_models()[0],
                    prompt=task,
                )
                candidates.append(
                    Candidate(
                        candidate_id="deterministic",
                        source="deterministic",
                        text=completion.text,
                        completion=completion,
                    )
                )
            except ProviderError as exc:
                notes.append(f"deterministic participant unavailable: {exc}")

        for model_id in self.registry.models_for_role(role):
            if len(candidates) >= count:
                break
            profile = self.registry.profile(model_id)

            if self.ledger is not None:
                from b1_models.ledger import WouldNotFit

                try:
                    self.ledger.make_room_for(profile, keep=("qwen3:1.7b",),
                                              context_tokens=context_tokens)
                    self.ledger.reserve(profile, context_tokens)
                except WouldNotFit as exc:
                    notes.append(f"{model_id} skipped: {exc}")
                    continue

            try:
                completion = self.registry.provider_for(model_id).complete(
                    model_id=model_id, prompt=task
                )
                candidates.append(
                    Candidate(
                        candidate_id=model_id,
                        source=model_id,
                        text=completion.text,
                        completion=completion,
                    )
                )
            except ProviderError as exc:
                notes.append(f"{model_id} produced no candidate: {exc}")
            finally:
                if self.ledger is not None and model_id in self.ledger.resident():
                    self.ledger.release(model_id)

        return tuple(candidates), tuple(notes)

    # -- verification --------------------------------------------------------

    def verify(
        self, candidates: tuple[Candidate, ...], task: str
    ) -> tuple[VerifierResult, ...]:
        results: list[VerifierResult] = []
        for verifier in self.verifiers:
            for candidate in candidates:
                try:
                    results.append(verifier.check(candidate, task))
                except Exception as exc:  # noqa: BLE001
                    # A verifier that crashed did not find a fault; it failed
                    # to look. Recording that as a failed check would eliminate
                    # a candidate on the strength of a broken tool.
                    results.append(
                        VerifierResult(
                            verifier_id=verifier.verifier_id,
                            candidate_id=candidate.candidate_id,
                            passed=False,
                            conclusive=False,
                            detail=f"verifier raised {type(exc).__name__}: {exc}",
                        )
                    )
        return tuple(results)

    # -- adjudication --------------------------------------------------------

    def adjudicate(
        self,
        *,
        task: str,
        task_class: TaskClass,
        candidates: tuple[Candidate, ...],
        results: tuple[VerifierResult, ...] = (),
        critiques: tuple[Critique, ...] = (),
        notes: tuple[str, ...] = (),
        synthesiser=None,
    ) -> Adjudication:
        """Layer 1 eliminates, layer 2 chooses, layer 3 gates."""
        depth = depth_for(task_class)

        if depth.fail_closed:
            return Adjudication(
                verdict=Verdict.IN_DOUBT,
                task_class=task_class,
                winner=None,
                reason=(
                    "task class is TX (unclassified), which fails closed. A task nobody "
                    "could classify is a task nobody sized, and running it at some "
                    "default depth would be choosing a risk level by accident."
                ),
                caveats=notes,
            )

        if not candidates:
            return Adjudication(
                verdict=Verdict.IN_DOUBT,
                task_class=task_class,
                winner=None,
                reason="no candidate was produced",
                caveats=notes,
            )

        # ---- Layer 1: deterministic evidence eliminates ---------------------
        by_candidate: dict[str, list[VerifierResult]] = {c.candidate_id: [] for c in candidates}
        for result in results:
            by_candidate.setdefault(result.candidate_id, []).append(result)

        surviving: list[Candidate] = []
        eliminated: list[tuple[str, str]] = []
        inconclusive: list[str] = []

        for candidate in candidates:
            checks = by_candidate.get(candidate.candidate_id, [])
            failures = [r for r in checks if r.conclusive and not r.passed]
            if failures:
                eliminated.append(
                    (
                        candidate.candidate_id,
                        "; ".join(f"{r.verifier_id}: {r.detail}" for r in failures),
                    )
                )
                continue
            unlookable = [r for r in checks if not r.conclusive]
            if unlookable:
                inconclusive.append(
                    f"{candidate.candidate_id}: "
                    + "; ".join(f"{r.verifier_id} could not run ({r.detail})" for r in unlookable)
                )
            surviving.append(candidate)

        if not surviving:
            return Adjudication(
                verdict=Verdict.REJECT,
                task_class=task_class,
                winner=None,
                reason=(
                    "every candidate failed a deterministic check. No amount of model "
                    "agreement reinstates a candidate that objective evidence rejected."
                ),
                eliminated=tuple(eliminated),
                evidence_refs=tuple(r.evidence_ref() for r in results),
                caveats=notes,
                agreement=_agreement(candidates),
            )

        passing = tuple(
            r for r in results
            if r.conclusive and r.passed
            and r.candidate_id in {c.candidate_id for c in surviving}
        )
        if depth.require_deterministic_evidence and not passing:
            return Adjudication(
                verdict=Verdict.IN_DOUBT,
                task_class=task_class,
                winner=None,
                reason=(
                    f"{task_class.value} requires deterministic evidence and none was "
                    f"conclusive. Candidates survived only because nothing checked them, "
                    f"which is not the same as passing."
                ),
                surviving=tuple(c.candidate_id for c in surviving),
                eliminated=tuple(eliminated),
                caveats=tuple(notes) + tuple(inconclusive),
                agreement=_agreement(candidates),
            )

        # ---- Layer 2: synthesis chooses among survivors ---------------------
        winner = surviving[0]
        synthesis_note = ""
        if synthesiser is not None and len(surviving) > 1:
            chosen_id = synthesiser(task, tuple(surviving))
            match = next((c for c in surviving if c.candidate_id == chosen_id), None)
            if match is None:
                # A synthesiser naming something that did not survive is
                # exactly the overrule this layering exists to prevent.
                synthesis_note = (
                    f"synthesiser chose {chosen_id!r}, which is not among the surviving "
                    f"candidates; falling back to the first survivor"
                )
            else:
                winner = match

        caveats = list(notes) + list(inconclusive)
        if synthesis_note:
            caveats.append(synthesis_note)
        for critique in critiques:
            if critique.candidate_id == winner.candidate_id:
                # A critic's objection rides along as a caveat. It is a model's
                # opinion about a candidate that passed objective checks, so it
                # qualifies the result without overturning it.
                caveats.append(f"{critique.critic_id}: {critique.objection}")

        evidence = tuple(r.evidence_ref() for r in results) + tuple(
            c.evidence_ref() for c in surviving
        )
        proven = tuple(r.evidence_ref() for r in passing)

        # ---- Layer 3: authority ---------------------------------------------
        if depth.require_authorization:
            return Adjudication(
                verdict=Verdict.AUTHORIZATION_REQUIRED,
                task_class=task_class,
                winner=winner,
                reason=(
                    f"{task_class.value} concerns a persistent effect. The tournament has "
                    f"chosen a candidate; it has not authorized anything. Take this to the "
                    f"commit gate."
                ),
                surviving=tuple(c.candidate_id for c in surviving),
                eliminated=tuple(eliminated),
                caveats=tuple(caveats),
                evidence_refs=evidence,
                deterministic_evidence=proven,
                agreement=_agreement(candidates),
            )

        verdict = Verdict.ACCEPT_WITH_CAVEATS if caveats else Verdict.ACCEPT
        return Adjudication(
            verdict=verdict,
            task_class=task_class,
            winner=winner,
            reason=(
                f"{len(surviving)} of {len(candidates)} candidates survived deterministic "
                f"verification"
            ),
            surviving=tuple(c.candidate_id for c in surviving),
            eliminated=tuple(eliminated),
            caveats=tuple(caveats),
            evidence_refs=evidence,
            deterministic_evidence=proven,
            agreement=_agreement(candidates),
        )

    # -- the whole thing -----------------------------------------------------

    def run(
        self,
        *,
        task: str,
        task_class: TaskClass,
        role: str = "generalist",
        critiques: tuple[Critique, ...] = (),
        synthesiser=None,
        context_tokens: int | None = None,
    ) -> Adjudication:
        depth = depth_for(task_class)
        if depth.fail_closed:
            return self.adjudicate(
                task=task, task_class=task_class, candidates=(), results=()
            )
        candidates, notes = self.gather_candidates(
            task=task, role=role, count=depth.candidates, context_tokens=context_tokens
        )
        results = self.verify(candidates, task)
        return self.adjudicate(
            task=task,
            task_class=task_class,
            candidates=candidates,
            results=results,
            critiques=critiques,
            notes=notes,
            synthesiser=synthesiser,
        )


def _agreement(candidates: tuple[Candidate, ...]) -> dict[str, int]:
    """Count identical answers.

    Reported, never acted on. Correlated training data makes agreement cheap;
    it is a hint about where to look, not a reason to believe.
    """
    counts: dict[str, int] = {}
    for candidate in candidates:
        counts[candidate.text] = counts.get(candidate.text, 0) + 1
    return counts
