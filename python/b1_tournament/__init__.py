"""B1 Local tournament: hybrid specialist + competitor, layered adjudication.

Deterministic evidence eliminates candidates. Model synthesis chooses among the
survivors. The authority layer decides what may happen. In that order, and a
model can never reach back across a layer it has already lost in.
"""

from .tournament import (
    Adjudication,
    Candidate,
    Critique,
    Depth,
    TaskClass,
    Tournament,
    TournamentError,
    Verdict,
    Verifier,
    VerifierResult,
    depth_for,
)

__all__ = [
    "Adjudication", "Candidate", "Critique", "Depth", "TaskClass", "Tournament",
    "TournamentError", "Verdict", "Verifier", "VerifierResult", "depth_for",
]
