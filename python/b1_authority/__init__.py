"""B1 Local authority: the Dual-Core Commit Gate.

Rust and Python are equal computational peers. Both may analyse, derive
subgoals, plan, simulate and propose; neither may cause a persistent effect on
its own. Everything consequential passes through the gate, which holds one write
lease at a time, so two peers proposing the same effect linearise rather than
both acting.
"""

from .authority import (
    AUTHORITY_SCHEMA_VERSION,
    OUTCOMES,
    PROOF_SCHEMA_VERSION,
    RECEIPT_STATUSES,
    AuthorityEnvelope,
    AuthorityError,
    TransitionProof,
    project_outcome,
)
from .gate import (
    GATE_SCHEMA_VERSION,
    AuthorityStale,
    CommitGate,
    GateError,
    Permit,
    PermitSpent,
    ReconciliationRequired,
)

__all__ = [
    "AUTHORITY_SCHEMA_VERSION",
    "PROOF_SCHEMA_VERSION",
    "GATE_SCHEMA_VERSION",
    "RECEIPT_STATUSES",
    "OUTCOMES",
    "AuthorityEnvelope",
    "AuthorityError",
    "TransitionProof",
    "project_outcome",
    "CommitGate",
    "GateError",
    "AuthorityStale",
    "PermitSpent",
    "ReconciliationRequired",
    "Permit",
]
