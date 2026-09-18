"""Findings as tournament evidence: they eliminate, and they eliminate alone.

`SecurityVerifier` implements the tournament's `Verifier` protocol, which is the
only thing in B1 that can eliminate a candidate. That placement is the point of
the handoff's Phase 7 criterion: a finding is a deterministic check, not a
model's opinion, so it belongs where deterministic checks live -- and a
`VerifierResult` has no field that could authorise anything, so a finding
entering here cannot leave as permission.

The inconclusive case matters as much as the failing one. A scanner that could
not run -- an unreadable target, a rule that threw -- returns `conclusive=False`,
which the tournament records without eliminating. "We could not look" must never
read as "it failed" and must never read as "it passed"; a security check is the
last place to blur that.
"""
from __future__ import annotations

from b1_tournament import Candidate, VerifierResult

from .finding import ScanManifest
from .scan import RULES, Rule, scan_text

__all__ = ["SecurityVerifier"]


class SecurityVerifier:
    """Eliminates a candidate whose own text carries a blocking finding.

    Scans the candidate, not the workspace. A tournament candidate is a proposed
    answer -- often code -- and the question here is whether *this answer* is
    safe to accept, which is decidable from the answer itself.
    """

    verifier_id = "security-scan"

    def __init__(self, rules: tuple[Rule, ...] = RULES, target: str = "candidate") -> None:
        self.rules = rules
        self.target = target

    def check(self, candidate: Candidate, task: str) -> VerifierResult:
        try:
            findings = scan_text(self.target, candidate.text, self.rules)
        except Exception as exc:  # noqa: BLE001 - a broken rule is inconclusive, not a pass
            return VerifierResult(
                self.verifier_id, candidate.candidate_id, False,
                detail=f"the scanner could not run: {type(exc).__name__}: {exc}",
                conclusive=False,
            )

        blocking = [f for f in findings if f.blocks]
        if blocking:
            named = "; ".join(
                f"{f.rule_id} at line {f.line}: {f.message}" for f in blocking[:3]
            )
            more = f" (+{len(blocking) - 3} more)" if len(blocking) > 3 else ""
            return VerifierResult(
                self.verifier_id, candidate.candidate_id, False,
                detail=f"{len(blocking)} blocking finding(s) -- {named}{more}",
            )

        if findings:
            # Passing with non-blocking findings recorded. They are real and
            # they do not eliminate; saying "passed" with no detail would throw
            # away the only record that they exist.
            return VerifierResult(
                self.verifier_id, candidate.candidate_id, True,
                detail=(
                    f"no blocking findings; {len(findings)} non-blocking: "
                    + ", ".join(sorted({f.rule_id for f in findings}))
                ),
            )
        return VerifierResult(
            self.verifier_id, candidate.candidate_id, True,
            detail=f"no findings under {len(self.rules)} rule(s). Not a proof of safety: "
                   f"it is the absence of a match, over these rules only",
        )


def manifest_evidence_refs(manifest: ScanManifest) -> tuple[str, ...]:
    """Evidence references for a whole scan, for an event envelope.

    The manifest's own ref comes first so the coverage record travels with the
    findings. A finding cited without its coverage is a fact with its context
    removed, and the context is what says whether the absence of others means
    anything.
    """
    return (manifest.evidence_ref(),) + tuple(f.evidence_ref() for f in manifest.findings)
