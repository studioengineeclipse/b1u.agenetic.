#!/usr/bin/env python3
"""Run one task through the whole of B1, and watch every gate it passes.

    python3 tools/run_agent.py                    # refuses: nothing approved it
    python3 tools/run_agent.py --approve          # runs to a verified effect
    python3 tools/run_agent.py --approve --interfere   # the interesting one

This is the vertical: propose -> tournament -> adjudicate -> authority ->
permit -> execute -> read back -> consume -> journal. Every stage prints, so a
refusal is legible rather than a silent no-op.

`--interfere` is the demonstration worth running. It edits the target file
after the authority is granted, and the gate refuses because the authorization
no longer describes the world it was given for. Nothing is simulated: the
authority is bound to a digest of the real file, and the real file changed.

`--approve` is a stand-in for a human saying yes to one specific envelope. It
is a flag here because this is a demo; in a real interface it is a person
reading the envelope's action, target, scope and expected effect. What is not a
stand-in is that the default refuses.
"""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_models import DeterministicProvider, ModelRegistry  # noqa: E402
from b1_tournament import (  # noqa: E402
    Candidate,
    TaskClass,
    Tournament,
    Verifier,
    VerifierResult,
)
from b1_work import AuthorityDecision, FileWriteEffect, workspace_runner  # noqa: E402

TASK = "State, in one line, what a hash chain proves and what it does not."
ANSWER = "It proves a record was altered; it does not restore the original."
TARGET = "notes/hash-chain.md"


class MentionsBothHalves(Verifier):
    """A deterministic check with an actual opinion about the answer.

    Trivial, but not vacuous: it fails an answer that claims a hash chain can
    repair damage, which is the specific overclaim this task invites.
    """

    verifier_id = "mentions-both-halves"

    def check(self, candidate: Candidate, task: str) -> VerifierResult:
        text = candidate.text.lower()
        proves = "prove" in text or "detect" in text
        limits = "not" in text and ("restore" in text or "repair" in text)
        if proves and limits:
            return VerifierResult(self.verifier_id, candidate.candidate_id, True)
        missing = []
        if not proves:
            missing.append("what it proves")
        if not limits:
            missing.append("what it cannot do")
        return VerifierResult(
            self.verifier_id, candidate.candidate_id, False,
            detail=f"answer omits {' and '.join(missing)}",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--approve", action="store_true",
                        help="stand in for a human approving this specific envelope")
    parser.add_argument("--interfere", action="store_true",
                        help="edit the target after authorization, to show the gate refuse")
    parser.add_argument("--bad-answer", action="store_true",
                        help="feed an overclaiming answer, to show verification eliminate it")
    parser.add_argument("--workspace", help="keep the workspace here instead of a temp dir")
    args = parser.parse_args()

    answer = (
        "A hash chain repairs any record that was altered."
        if args.bad_answer else ANSWER
    )

    tournament = Tournament(
        registry=ModelRegistry(),
        verifiers=(MentionsBothHalves(),),
        deterministic_participant=DeterministicProvider(answers={TASK: answer}),
    )

    temp = None
    if args.workspace:
        workspace = Path(args.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        journal_path = workspace / "root.db"
    else:
        temp = tempfile.mkdtemp(prefix="b1-agent-")
        workspace = Path(temp) / "workspace"
        workspace.mkdir()
        journal_path = Path(temp) / "root.db"

    def decide(envelope) -> AuthorityDecision:
        print("\n  authority envelope presented for approval:")
        print(f"    action           {envelope.proposed_action}")
        print(f"    target           {envelope.target}")
        print(f"    scope            {envelope.scope}")
        print(f"    expected effect  {envelope.expected_effect}")
        print(f"    recovery class   {envelope.persistence_class}")
        print(f"    bound to state   {envelope.state_digest[:16]}...")

        if args.interfere:
            path = workspace / TARGET
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("another actor wrote here first", encoding="utf-8")
            print("\n    [--interfere] another actor just edited the target")

        if not args.approve:
            return AuthorityDecision(
                granted=False,
                reason="not approved (run with --approve to authorize this envelope)",
            )
        print("    -> approved\n")
        return AuthorityDecision(granted=True, reason="approved at the prompt")

    runner, journal = workspace_runner(
        root=workspace, journal_path=journal_path,
        tournament=tournament, decide=decide,
    )

    print("\nB1 agent\n" + "=" * 66)
    print(f"task      {TASK}")
    print(f"target    {TARGET}")
    print(f"workspace {workspace}")

    try:
        outcome = runner.run(
            task=TASK,
            target=TARGET,
            objective="record what a hash chain does and does not prove",
            scope=("notes/**",),
        )

        print("-" * 66)
        for step in outcome.steps:
            print(f"  · {step}")
        print("-" * 66)
        print(f"  verdict          {outcome.verdict}")
        print(f"  winner           {outcome.winner or '—'}")
        print(f"  effect attempted {outcome.effect_attempted}")
        if outcome.receipt_status:
            print(f"  receipt          {outcome.receipt_status}")
        if outcome.postcondition_verified is not None:
            print(f"  postcondition    "
                  f"{'holds (read back)' if outcome.postcondition_verified else 'does NOT hold'}")
        if outcome.effect_outcome:
            print(f"  recorded as      {outcome.effect_outcome} / {outcome.epistemic_status}")
        if outcome.refusal:
            print(f"\n  REFUSED: {outcome.refusal}")

        path = workspace / TARGET
        print(f"\n  file on disk     "
              f"{path.read_text(encoding='utf-8')[:80]!r}" if path.exists()
              else "\n  file on disk     does not exist")
        print(f"  journal head     {outcome.journal_head[:32]}...")
        print(f"  journal records  {outcome.journal_records}, "
              f"verify_ok={journal.verify().ok}")
        print("=" * 66)

        if not args.approve:
            print("\nNothing was written, because nothing approved it. That is the")
            print("default. Run again with --approve.\n")
        elif args.interfere:
            print("\nThe authority was granted, and then the world moved. The gate")
            print("refused to record an effect under an authorization that no longer")
            print("describes the world it was given for.\n")
        elif args.bad_answer:
            print("\nThe answer overclaimed and a deterministic check eliminated it.")
            print("No model preference reinstates a candidate objective evidence rejected.\n")
        else:
            print("\nA task became a verified effect on disk, and every stage of that")
            print("is in the journal with its provenance.\n")
    finally:
        journal.close()
        if temp and not args.workspace:
            shutil.rmtree(temp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
