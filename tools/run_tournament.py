#!/usr/bin/env python3
"""Run a B1 tournament. With real local models if you have them, without if not.

    python3 tools/run_tournament.py                  # deterministic, no models
    python3 tools/run_tournament.py --provider ollama --model qwen3:4b
    python3 tools/run_tournament.py --probe          # what can this machine serve?

The point of `--probe` is that it answers the question "can I run the multi-model
yet" with a fact rather than an opinion. It asks the local server what it has and
reports what came back, including nothing.

Every run appends to the root journal and passes consequential outcomes through
the commit gate, so a tournament result is a record with provenance rather than
a line of console output that scrolls away.

Nothing here downloads a model. Pulling weights is a persistent effect and needs
its own authorization; this script will tell you the command and let you decide.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_models import (  # noqa: E402
    GIB,
    DeterministicProvider,
    ModelRegistry,
    ResourceLedger,
    candidate_profiles,
    lmstudio_provider,
    ollama_provider,
)
from b1_protocol import Envelope  # noqa: E402
from b1_protocol.canonical import digest_value  # noqa: E402
from b1_state import RootJournal  # noqa: E402
from b1_tournament import (  # noqa: E402
    Candidate,
    TaskClass,
    Tournament,
    Verdict,
    Verifier,
    VerifierResult,
)

DEMO_TASK = (
    "A journal record is chained as SHA256(prior || canonical_bytes(envelope)). "
    "If one record in the middle is edited, how many later record digests change? "
    "Answer with a single word."
)
DEMO_ANSWER = "all"


class KeywordVerifier(Verifier):
    """A deterministic check standing in for a compiler or a test run.

    Trivial on purpose: the demo is about the *layering*, not about this
    verifier being clever. What matters is that its answer does not depend on
    any model's opinion, and that when it says no, that is the end of it.
    """

    verifier_id = "expected-keyword"

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword.lower()

    def check(self, candidate: Candidate, task: str) -> VerifierResult:
        ok = self.keyword in candidate.text.lower()
        return VerifierResult(
            verifier_id=self.verifier_id,
            candidate_id=candidate.candidate_id,
            passed=ok,
            detail="" if ok else f"answer does not contain {self.keyword!r}",
        )


def probe() -> int:
    """Report what this machine can actually serve, right now."""
    print("\nB1 local model probe\n" + "=" * 60)

    ledger = ResourceLedger.for_omnibook()
    report = ledger.report()
    print(f"\nResource budget ({report['basis']} — {report['basis_note']})")
    print(f"  total                  {report['total_gib']} GiB")
    print(f"  reserved for non-models {report['reserved_for_non_models_gib']} GiB")
    print(f"  model budget           {report['model_budget_gib']} GiB")

    print("\nShortlisted profiles, against that budget:")
    for profile in candidate_profiles():
        gib = profile.footprint_bytes() / GIB
        fits = "fits" if ledger.would_fit(profile) else "DOES NOT FIT"
        print(f"  {profile.model_id:18} {gib:5.2f} GiB  {fits}")
        if not ledger.would_fit(profile) and profile.notes:
            print(f"    {profile.notes}")

    print("\nReachable providers:")
    any_model = False
    for name, factory in (("ollama", ollama_provider), ("lmstudio", lmstudio_provider)):
        provider = factory()
        models = provider.available_models()
        if models:
            any_model = True
            print(f"  {name:10} {provider.base_url}  -> {len(models)} model(s)")
            for model in models:
                print(f"               {model}")
        else:
            print(f"  {name:10} {provider.base_url}  -> not reachable, or serving nothing")

    print("\n" + "=" * 60)
    if any_model:
        print("A model server is answering. Run a real tournament with:")
        print("  python3 tools/run_tournament.py --provider ollama --model <id>")
        return 0

    print("No local model server answered. To get one:")
    print()
    print("  Ollama    https://ollama.com/download  then:  ollama pull qwen3:4b")
    print("  LM Studio https://lmstudio.ai          then load a 3B-4B model")
    print()
    print("Both already speak the Responses API that the Codex-derived backend")
    print("requires, so neither needs B1's shim. llama.cpp speaks")
    print("chat-completions and does need it — see ADR-0002.")
    print()
    print("Pulling a model is a persistent effect. B1 will not do it for you.")
    # Not a failure: "nothing is installed" is a fact about the machine.
    return 0


def run(args: argparse.Namespace) -> int:
    registry = ModelRegistry()
    ledger = ResourceLedger.for_omnibook()
    using_real_models = False

    if args.provider:
        provider = {"ollama": ollama_provider, "lmstudio": lmstudio_provider}[args.provider]()
        registry.register_provider(provider)
        profiles = {p.model_id: p for p in candidate_profiles()}
        for model_id in args.model:
            profile = profiles.get(model_id)
            if profile is None:
                print(
                    f"No profile for {model_id!r}. A model with no declared footprint "
                    f"cannot be budgeted; add one to registry.candidate_profiles().",
                    file=sys.stderr,
                )
                return 2
            # Re-point the profile at whichever provider was asked for.
            from dataclasses import replace

            registry.register_profile(
                replace(profile, provider_id=args.provider, roles=("generalist",))
            )
            using_real_models = True

    deterministic = DeterministicProvider(answers={DEMO_TASK: DEMO_ANSWER})

    tournament = Tournament(
        registry=registry,
        ledger=ledger,
        verifiers=(KeywordVerifier(DEMO_ANSWER),),
        deterministic_participant=None if args.no_deterministic else deterministic,
    )

    print("\nB1 tournament\n" + "=" * 60)
    print(f"task class : {args.task_class}")
    print(f"models     : {', '.join(args.model) if using_real_models else 'none (deterministic only)'}")
    print(f"\ntask:\n  {DEMO_TASK}\n")

    result = tournament.run(task=DEMO_TASK, task_class=TaskClass[args.task_class])

    print("-" * 60)
    print(f"verdict          : {result.verdict.value}")
    print(f"epistemic status : {result.epistemic_status}")
    print(f"reason           : {result.reason}")
    if result.winner is not None:
        print(f"winner           : {result.winner.candidate_id}")
        print(f"answer           : {result.winner.text.strip()[:200]}")
    if result.surviving:
        print(f"survived         : {', '.join(result.surviving)}")
    for candidate_id, why in result.eliminated:
        print(f"eliminated       : {candidate_id} — {why}")
    if result.agreement and len(result.agreement) > 1:
        print(f"agreement        : {result.agreement}")
        print("                   (recorded, never used to decide — see the module docstring)")
    for caveat in result.caveats:
        print(f"caveat           : {caveat}")

    # Record it. A tournament result that only reaches the console has no
    # provenance and cannot be audited later.
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(args.journal) if args.journal else Path(tmp) / "root.db"
        journal = RootJournal(db)
        try:
            envelope = Envelope.new(
                event_id=f"tournament-{digest_value(DEMO_TASK)[:12]}",
                origin="M",
                program_identity="b1-local",
                execution_identity="tools/run_tournament.py",
                attempt_identity="attempt-1",
                epoch=max(0, journal.head()[2]),
                epistemic_status=result.epistemic_status,
                payload={
                    "kind": "tournament.adjudicated",
                    "verdict": result.verdict.value,
                    "task_class": result.task_class.value,
                    "winner": result.winner.candidate_id if result.winner else "",
                    "surviving": list(result.surviving),
                    "eliminated": [c for c, _ in result.eliminated],
                },
                evidence_refs=tuple(dict.fromkeys(result.evidence_refs)),
            )
            journal.append(envelope)
            print(f"\njournal          : {db}")
            print(f"head             : {journal.head()[0][:32]}...")
            print(f"records          : {journal.head()[1]}  verify_ok={journal.verify().ok}")
        finally:
            journal.close()

    print("=" * 60)
    if result.verdict is Verdict.AUTHORIZATION_REQUIRED:
        print(
            "\nThis task class concerns a persistent effect. The tournament chose a\n"
            "candidate; it authorized nothing. Take it to the commit gate."
        )
    print()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probe", action="store_true",
                        help="report what this machine can serve, then exit")
    parser.add_argument("--provider", choices=("ollama", "lmstudio"),
                        help="local model server (both are Responses-native)")
    parser.add_argument("--model", action="append", default=[],
                        help="model id; repeat for several competitors")
    parser.add_argument("--task-class", default="T2_HARD_REASONING",
                        choices=[c.name for c in TaskClass])
    parser.add_argument("--no-deterministic", action="store_true",
                        help="exclude the deterministic participant")
    parser.add_argument("--journal", help="journal path (default: a temporary one)")
    parser.add_argument("--json", action="store_true", help="machine-readable summary")
    args = parser.parse_args()

    if args.probe:
        return probe()
    if args.provider and not args.model:
        parser.error("--provider needs at least one --model")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
