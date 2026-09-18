"""The tournament: what wins, what is eliminated, and what cannot be overruled.

The decisive test here is
``a_unanimous_but_invalid_candidate_loses_to_one_deterministic_rejection``.
It is the handoff's Phase 5 exit criterion, and it is the property that
separates this from a voting system: every model can agree, and a single
compiler can still be right.

Stdlib unittest only. No model server is contacted.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from b1_models import (  # noqa: E402
    GIB,
    Completion,
    DeterministicProvider,
    ModelProfile,
    ModelRegistry,
    ProviderUnavailable,
    ResourceLedger,
    WouldNotFit,
    candidate_profiles,
    prompt_digest,
)
from b1_tournament import (  # noqa: E402
    Candidate,
    Critique,
    TaskClass,
    Tournament,
    Verdict,
    Verifier,
    VerifierResult,
    depth_for,
)

TASK = "return the integer sum of 2 and 2"


def candidate(name: str, text: str) -> Candidate:
    return Candidate(
        candidate_id=name,
        source=name,
        text=text,
        completion=Completion(
            model_id=name,
            provider_id="test",
            text=text,
            prompt_digest=prompt_digest(TASK),
        ),
    )


class ExactAnswerVerifier(Verifier):
    """Stands in for a compiler or a test run: an answer that does not depend
    on anyone's opinion."""

    verifier_id = "exact-answer"

    def __init__(self, expected: str) -> None:
        self.expected = expected

    def check(self, candidate: Candidate, task: str) -> VerifierResult:
        ok = candidate.text.strip() == self.expected
        return VerifierResult(
            verifier_id=self.verifier_id,
            candidate_id=candidate.candidate_id,
            passed=ok,
            detail="" if ok else f"expected {self.expected!r}, got {candidate.text.strip()!r}",
        )


class BrokenVerifier(Verifier):
    """A verifier whose tool is missing. It did not find a fault; it failed to look."""

    verifier_id = "broken"

    def check(self, candidate: Candidate, task: str) -> VerifierResult:
        raise FileNotFoundError("cc: not found")


class InconclusiveVerifier(Verifier):
    verifier_id = "inconclusive"

    def check(self, candidate: Candidate, task: str) -> VerifierResult:
        return VerifierResult(
            verifier_id=self.verifier_id,
            candidate_id=candidate.candidate_id,
            passed=False,
            conclusive=False,
            detail="sandbox unavailable",
        )


def tournament(**kwargs) -> Tournament:
    return Tournament(registry=ModelRegistry(), **kwargs)


class DeterministicEvidenceWins(unittest.TestCase):
    """Layer 1 eliminates. Nothing downstream reinstates."""

    def test_a_unanimous_but_invalid_candidate_loses_to_one_deterministic_rejection(self):
        """The handoff's Phase 5 exit criterion.

        Four models say 5. One deterministic participant says 4. The compiler
        agrees with the one. Majority loses, and it is not close.
        """
        wrong = [candidate(f"model-{i}", "5") for i in range(4)]
        right = candidate("deterministic", "4")

        result = tournament(verifiers=(ExactAnswerVerifier("4"),)).adjudicate(
            task=TASK,
            task_class=TaskClass.T2_HARD_REASONING,
            candidates=tuple(wrong) + (right,),
            results=tournament(verifiers=(ExactAnswerVerifier("4"),)).verify(
                tuple(wrong) + (right,), TASK
            ),
        )

        self.assertEqual(result.verdict, Verdict.ACCEPT)
        self.assertIsNotNone(result.winner)
        self.assertEqual(result.winner.candidate_id, "deterministic")
        self.assertEqual(len(result.eliminated), 4)
        # Agreement was recorded and ignored: the losing answer had four votes.
        self.assertEqual(result.agreement["5"], 4)
        self.assertEqual(result.agreement["4"], 1)

    def test_a_synthesiser_cannot_choose_an_eliminated_candidate(self):
        """Layer 2 may only choose among what layer 1 left standing.

        Two survivors, so the synthesiser is actually consulted, and it names a
        third candidate that deterministic verification already eliminated. An
        earlier version of this test left only one survivor, which short-circuits
        the synthesiser entirely — it passed without ever reaching the guard it
        claimed to check.
        """
        candidates = (
            candidate("good-a", "4"),
            candidate("good-b", "4"),
            candidate("bad", "5"),
        )
        runner = tournament(verifiers=(ExactAnswerVerifier("4"),))
        chosen: list[tuple[str, ...]] = []

        def synthesiser(task: str, surviving: tuple[Candidate, ...]) -> str:
            chosen.append(tuple(c.candidate_id for c in surviving))
            return "bad"  # tries to reach back across a layer it lost in

        result = runner.adjudicate(
            task=TASK,
            task_class=TaskClass.T2_HARD_REASONING,
            candidates=candidates,
            results=runner.verify(candidates, TASK),
            synthesiser=synthesiser,
        )

        # The synthesiser ran, and was only ever offered the survivors.
        self.assertEqual(chosen, [("good-a", "good-b")])
        self.assertNotIn("bad", chosen[0])
        # Its out-of-band choice was refused, not honoured.
        self.assertEqual(result.winner.candidate_id, "good-a")
        self.assertTrue(
            any("not among the surviving candidates" in c for c in result.caveats),
            result.caveats,
        )

    def test_when_everything_fails_verification_the_verdict_is_reject(self):
        candidates = (candidate("a", "5"), candidate("b", "6"))
        runner = tournament(verifiers=(ExactAnswerVerifier("4"),))
        result = runner.adjudicate(
            task=TASK,
            task_class=TaskClass.T2_HARD_REASONING,
            candidates=candidates,
            results=runner.verify(candidates, TASK),
        )
        self.assertEqual(result.verdict, Verdict.REJECT)
        self.assertIsNone(result.winner)
        self.assertIn("No amount of model agreement reinstates", result.reason)

    def test_a_critic_qualifies_a_winner_but_does_not_overturn_it(self):
        """A critic is a model. Its objection is a hypothesis, not a finding."""
        candidates = (candidate("good", "4"),)
        runner = tournament(verifiers=(ExactAnswerVerifier("4"),))
        result = runner.adjudicate(
            task=TASK,
            task_class=TaskClass.T2_HARD_REASONING,
            candidates=candidates,
            results=runner.verify(candidates, TASK),
            critiques=(Critique(critic_id="critic-1", candidate_id="good",
                                objection="I think this is wrong"),),
        )
        self.assertEqual(result.verdict, Verdict.ACCEPT_WITH_CAVEATS)
        self.assertEqual(result.winner.candidate_id, "good")
        self.assertTrue(any("critic-1" in c for c in result.caveats))


class CouldNotLookIsNotAPass(unittest.TestCase):
    """An unrunnable verifier must not read as either outcome."""

    def test_a_crashing_verifier_does_not_eliminate_a_candidate(self):
        candidates = (candidate("good", "4"),)
        runner = tournament(verifiers=(BrokenVerifier(),))
        results = runner.verify(candidates, TASK)

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].conclusive)
        self.assertIn("FileNotFoundError", results[0].detail)

        result = runner.adjudicate(
            task=TASK, task_class=TaskClass.T1_ORDINARY,
            candidates=candidates, results=results,
        )
        self.assertEqual(result.winner.candidate_id, "good")
        self.assertTrue(any("could not run" in c for c in result.caveats), result.caveats)

    def test_a_class_needing_evidence_is_in_doubt_when_none_was_conclusive(self):
        # Surviving because nothing checked you is not the same as passing.
        candidates = (candidate("unchecked", "4"),)
        runner = tournament(verifiers=(InconclusiveVerifier(),))
        result = runner.adjudicate(
            task=TASK, task_class=TaskClass.T3_TOOL_EXECUTION,
            candidates=candidates, results=runner.verify(candidates, TASK),
        )
        self.assertEqual(result.verdict, Verdict.IN_DOUBT)
        self.assertIn("not the same as passing", result.reason)

    def test_an_ordinary_task_with_no_verifier_is_a_working_assumption(self):
        result = tournament().adjudicate(
            task=TASK, task_class=TaskClass.T1_ORDINARY,
            candidates=(candidate("m", "4"),), results=(),
        )
        self.assertEqual(result.verdict, Verdict.ACCEPT)
        self.assertEqual(result.epistemic_status, "WORKING_ASSUMPTION")


class RiskAdaptiveDepth(unittest.TestCase):
    def test_an_unclassified_task_fails_closed(self):
        result = tournament().run(task=TASK, task_class=TaskClass.TX_UNKNOWN)
        self.assertEqual(result.verdict, Verdict.IN_DOUBT)
        self.assertIn("fails closed", result.reason)
        self.assertIsNone(result.winner)

    def test_an_effect_class_stops_at_authorization_required(self):
        """The tournament chooses a candidate. It authorizes nothing."""
        candidates = (candidate("good", "4"),)
        runner = tournament(verifiers=(ExactAnswerVerifier("4"),))
        for task_class in (
            TaskClass.T4_REVERSIBLE_EFFECT,
            TaskClass.T5_COMPENSATABLE_EFFECT,
            TaskClass.T6_IRREVERSIBLE_EFFECT,
        ):
            with self.subTest(task_class=task_class):
                result = runner.adjudicate(
                    task=TASK, task_class=task_class,
                    candidates=candidates, results=runner.verify(candidates, TASK),
                )
                self.assertEqual(result.verdict, Verdict.AUTHORIZATION_REQUIRED)
                self.assertIsNotNone(result.winner)
                self.assertIn("has not authorized anything", result.reason)

    def test_depth_scales_with_consequence(self):
        self.assertEqual(depth_for(TaskClass.T0_DETERMINISTIC).candidates, 1)
        self.assertEqual(depth_for(TaskClass.T5_COMPENSATABLE_EFFECT).candidates, 3)
        self.assertTrue(depth_for(TaskClass.T6_IRREVERSIBLE_EFFECT).require_authorization)
        self.assertFalse(depth_for(TaskClass.T1_ORDINARY).require_authorization)
        self.assertTrue(depth_for(TaskClass.TX_UNKNOWN).fail_closed)

    def test_no_candidates_is_in_doubt_not_reject(self):
        # Nothing was proposed, so nothing was disproved.
        result = tournament().adjudicate(
            task=TASK, task_class=TaskClass.T1_ORDINARY, candidates=(), results=()
        )
        self.assertEqual(result.verdict, Verdict.IN_DOUBT)


class EndToEnd(unittest.TestCase):
    """The whole loop, with a registry and a ledger, and no model server."""

    def registry(self) -> ModelRegistry:
        registry = ModelRegistry()
        registry.register_profile(
            ModelProfile(
                model_id="small-a", provider_id="test",
                weights_bytes=1 * GIB, kv_bytes_per_token=1024,
                runtime_overhead_bytes=128 * 1024 * 1024,
                roles=("generalist",),
            )
        )
        registry.register_profile(
            ModelProfile(
                model_id="small-b", provider_id="test",
                weights_bytes=1 * GIB, kv_bytes_per_token=1024,
                runtime_overhead_bytes=128 * 1024 * 1024,
                roles=("generalist",),
            )
        )
        registry.register_provider(
            DeterministicProvider(
                provider_id="test",
                answers={TASK: "4"},
                models=("small-a", "small-b"),
            )
        )
        return registry

    def test_a_full_run_produces_an_accepted_verified_answer(self):
        runner = Tournament(
            registry=self.registry(),
            ledger=ResourceLedger(total_bytes=8 * GIB, reserved_bytes=2 * GIB),
            verifiers=(ExactAnswerVerifier("4"),),
        )
        result = runner.run(task=TASK, task_class=TaskClass.T2_HARD_REASONING)

        self.assertEqual(result.verdict, Verdict.ACCEPT)
        self.assertEqual(result.winner.text, "4")
        self.assertEqual(result.epistemic_status, "VERIFIED")
        self.assertTrue(any(ref.startswith("verifier:") for ref in result.evidence_refs))
        self.assertTrue(any(ref.startswith("model:") for ref in result.evidence_refs))

    def test_models_are_released_after_answering(self):
        """Hot-load, answer, unload. Nothing stays resident by accident."""
        ledger = ResourceLedger(total_bytes=8 * GIB, reserved_bytes=2 * GIB)
        runner = Tournament(
            registry=self.registry(), ledger=ledger,
            verifiers=(ExactAnswerVerifier("4"),),
        )
        runner.run(task=TASK, task_class=TaskClass.T2_HARD_REASONING)
        self.assertEqual(ledger.resident(), ())
        self.assertEqual(ledger.held_bytes, 0)

    def test_a_model_that_does_not_fit_is_skipped_with_a_note_not_attempted(self):
        registry = self.registry()
        registry.register_profile(
            ModelProfile(
                model_id="enormous", provider_id="test",
                weights_bytes=100 * GIB, kv_bytes_per_token=1024,
                runtime_overhead_bytes=0, roles=("generalist",),
            )
        )
        runner = Tournament(
            registry=registry,
            ledger=ResourceLedger(total_bytes=8 * GIB, reserved_bytes=2 * GIB),
            verifiers=(ExactAnswerVerifier("4"),),
        )
        candidates, notes = runner.gather_candidates(task=TASK, role="generalist", count=5)

        self.assertNotIn("enormous", [c.candidate_id for c in candidates])
        self.assertTrue(any("enormous skipped" in n for n in notes), notes)
        self.assertTrue(any("exceeds the entire" in n for n in notes), notes)

    def test_an_unreachable_provider_is_a_note_not_a_candidate(self):
        registry = ModelRegistry()
        registry.register_profile(
            ModelProfile(
                model_id="absent", provider_id="test",
                weights_bytes=1 * GIB, kv_bytes_per_token=1024,
                runtime_overhead_bytes=0, roles=("generalist",),
            )
        )
        # A provider that serves nothing: the environment as it is.
        registry.register_provider(DeterministicProvider(provider_id="test", models=()))
        runner = Tournament(registry=registry)
        candidates, notes = runner.gather_candidates(task=TASK, role="generalist", count=2)

        self.assertEqual(candidates, ())
        self.assertTrue(any("produced no candidate" in n for n in notes), notes)

    def test_the_deterministic_participant_can_win_outright(self):
        runner = Tournament(
            registry=ModelRegistry(),
            verifiers=(ExactAnswerVerifier("4"),),
            deterministic_participant=DeterministicProvider(answers={TASK: "4"}),
        )
        result = runner.run(task=TASK, task_class=TaskClass.T2_HARD_REASONING)
        self.assertEqual(result.verdict, Verdict.ACCEPT)
        self.assertEqual(result.winner.candidate_id, "deterministic")


class ProviderContract(unittest.TestCase):
    def test_a_completion_cites_itself_as_model_evidence(self):
        provider = DeterministicProvider(answers={TASK: "4"})
        completion = provider.complete(model_id="deterministic-1", prompt=TASK)
        ref = completion.evidence_ref()
        # The prefix is what lets a journal reader tell a model's word from a
        # compiler's at a glance.
        self.assertTrue(ref.startswith("model:"))
        self.assertIn("deterministic-1", ref)

    def test_identical_answers_digest_identically_and_timing_does_not_count(self):
        a = Completion(model_id="m", provider_id="p", text="4",
                       prompt_digest=prompt_digest(TASK), latency_ms=10)
        b = Completion(model_id="m", provider_id="p", text="4",
                       prompt_digest=prompt_digest(TASK), latency_ms=9000)
        self.assertEqual(a.digest(), b.digest())

    def test_an_unserved_model_raises_unavailable(self):
        provider = DeterministicProvider(models=("only-this",))
        with self.assertRaises(ProviderUnavailable):
            provider.complete(model_id="something-else", prompt=TASK)

    def test_the_responses_request_matches_the_api_codex_requires(self):
        from b1_models import HttpResponsesProvider

        provider = HttpResponsesProvider(provider_id="ollama",
                                         base_url="http://localhost:11434/v1")
        url, body = provider.build_request(
            model_id="qwen3:4b", prompt="hello", max_tokens=64, temperature=0.0
        )
        # /responses, not /chat/completions. ADR-0002 is the reason.
        self.assertEqual(url, "http://localhost:11434/v1/responses")
        self.assertEqual(body["model"], "qwen3:4b")
        self.assertEqual(body["input"], "hello")
        self.assertFalse(body["stream"])

    def test_both_documented_responses_shapes_parse(self):
        from b1_models import HttpResponsesProvider as P

        self.assertEqual(P.parse_response({"output_text": "4"}), "4")
        self.assertEqual(P.parse_response({"output_text": ["4", "2"]}), "42")
        self.assertEqual(
            P.parse_response({"output": [{"content": [{"text": "4"}]}]}), "4"
        )

    def test_an_unrecognised_payload_raises_rather_than_returning_empty(self):
        from b1_models import HttpResponsesProvider as P, ProviderError

        with self.assertRaises(ProviderError) as caught:
            P.parse_response({"unexpected": "shape"})
        self.assertIn("Refusing to return an empty completion", str(caught.exception))


class LedgerDiscipline(unittest.TestCase):
    def test_the_omnibook_budget_refuses_the_model_codex_defaults_to(self):
        """gpt-oss:20b is Codex's built-in default and does not fit this machine."""
        ledger = ResourceLedger.for_omnibook()
        profiles = {p.model_id: p for p in candidate_profiles()}

        with self.assertRaises(WouldNotFit) as caught:
            ledger.reserve(profiles["gpt-oss:20b"])
        self.assertIn("gpt-oss:20b", str(caught.exception))

        # While the sweet spot the handoff identified does fit.
        ledger.reserve(profiles["qwen3:4b"])
        self.assertIn("qwen3:4b", ledger.resident())

    def test_a_refused_reservation_holds_nothing(self):
        ledger = ResourceLedger(total_bytes=4 * GIB, reserved_bytes=1 * GIB)
        huge = ModelProfile(model_id="huge", provider_id="p",
                            weights_bytes=10 * GIB, kv_bytes_per_token=0,
                            runtime_overhead_bytes=0)
        with self.assertRaises(WouldNotFit):
            ledger.reserve(huge)
        self.assertEqual(ledger.held_bytes, 0)
        self.assertEqual(ledger.resident(), ())

    def test_eviction_is_largest_first_so_fewest_models_pay_a_reload(self):
        ledger = ResourceLedger(total_bytes=10 * GIB, reserved_bytes=0)
        small = ModelProfile(model_id="small", provider_id="p", weights_bytes=1 * GIB,
                             kv_bytes_per_token=0, runtime_overhead_bytes=0)
        big = ModelProfile(model_id="big", provider_id="p", weights_bytes=6 * GIB,
                           kv_bytes_per_token=0, runtime_overhead_bytes=0)
        ledger.reserve(small)
        ledger.reserve(big)

        incoming = ModelProfile(model_id="incoming", provider_id="p",
                                weights_bytes=5 * GIB, kv_bytes_per_token=0,
                                runtime_overhead_bytes=0)
        evicted = ledger.make_room_for(incoming)
        self.assertEqual(evicted, ("big",))
        self.assertIn("small", ledger.resident())

    def test_a_protected_model_is_never_evicted(self):
        # The router stays resident so routing never pays a load.
        ledger = ResourceLedger(total_bytes=4 * GIB, reserved_bytes=0)
        router = ModelProfile(model_id="router", provider_id="p", weights_bytes=2 * GIB,
                              kv_bytes_per_token=0, runtime_overhead_bytes=0)
        ledger.reserve(router)
        incoming = ModelProfile(model_id="incoming", provider_id="p",
                                weights_bytes=3 * GIB, kv_bytes_per_token=0,
                                runtime_overhead_bytes=0)
        with self.assertRaises(WouldNotFit) as caught:
            ledger.make_room_for(incoming, keep=("router",))
        self.assertIn("router is protected", str(caught.exception))
        self.assertIn("router", ledger.resident())

    def test_context_length_is_part_of_the_footprint(self):
        profile = ModelProfile(model_id="m", provider_id="p", weights_bytes=1 * GIB,
                               kv_bytes_per_token=1024 * 1024, runtime_overhead_bytes=0)
        self.assertEqual(profile.footprint_bytes(0), 1 * GIB)
        self.assertEqual(profile.footprint_bytes(1024), 1 * GIB + 1024 * 1024 * 1024)

    def test_the_report_carries_its_epistemic_grade(self):
        # Arithmetic over estimates must not read as a measurement.
        report = ResourceLedger.for_omnibook().report()
        self.assertEqual(report["basis"], "WORKING_ASSUMPTION")
        self.assertIn("not observed RSS", report["basis_note"])


if __name__ == "__main__":
    unittest.main()
