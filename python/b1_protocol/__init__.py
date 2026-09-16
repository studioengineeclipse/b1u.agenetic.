"""B1 Local protocol: canonical serialization and the Event Envelope v1."""

from .canonical import (
    CanonicalizationError,
    canonical_json_bytes,
    digest_bytes,
    digest_value,
)
from .envelope import (
    EPISTEMIC_STATUSES,
    ORIGINS,
    PERSISTENCE_CLASSES,
    SCHEMA_VERSION,
    Envelope,
    EnvelopeError,
)

__all__ = [
    "CanonicalizationError",
    "canonical_json_bytes",
    "digest_bytes",
    "digest_value",
    "Envelope",
    "EnvelopeError",
    "SCHEMA_VERSION",
    "ORIGINS",
    "EPISTEMIC_STATUSES",
    "PERSISTENCE_CLASSES",
]
