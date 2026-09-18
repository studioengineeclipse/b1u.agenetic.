"""B1 Local capability policy: what may ever be authorized here.

Sits above per-effect authority. The policy says whether a kind of action may be
authorized at all; the authority envelope says whether this specific one is
authorized now. Both must say yes, and the policy is asked first.
"""

from .policy import (
    PERSISTENCE_RANK,
    POLICY_SCHEMA_VERSION,
    Capability,
    CapabilityPolicy,
    PolicyDecision,
    PolicyError,
)

__all__ = [
    "PERSISTENCE_RANK",
    "POLICY_SCHEMA_VERSION",
    "Capability",
    "CapabilityPolicy",
    "PolicyDecision",
    "PolicyError",
]
