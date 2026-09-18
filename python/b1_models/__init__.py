"""B1 Local model layer: providers, registry and the resource ledger.

Model output enters B1 here, as evidence. Nothing in this package can authorize
an effect; that is the commit gate's job, and the separation is deliberate.
"""

from .ledger import (
    GIB,
    MIB,
    LedgerError,
    ModelProfile,
    Reservation,
    ResourceLedger,
    WouldNotFit,
)
from .provider import (
    DEFAULT_LMSTUDIO_BASE_URL,
    DEFAULT_OLLAMA_BASE_URL,
    Completion,
    DeterministicProvider,
    HttpResponsesProvider,
    ModelProvider,
    ProviderError,
    ProviderUnavailable,
    prompt_digest,
)
from .registry import (
    ModelRegistry,
    RegistryError,
    candidate_profiles,
    lmstudio_provider,
    ollama_provider,
)

__all__ = [
    "GIB", "MIB",
    "LedgerError", "WouldNotFit", "ModelProfile", "Reservation", "ResourceLedger",
    "Completion", "DeterministicProvider", "HttpResponsesProvider", "ModelProvider",
    "ProviderError", "ProviderUnavailable", "prompt_digest",
    "DEFAULT_OLLAMA_BASE_URL", "DEFAULT_LMSTUDIO_BASE_URL",
    "ModelRegistry", "RegistryError", "candidate_profiles",
    "ollama_provider", "lmstudio_provider",
]
