"""The model registry: which models exist, what they cost, and who routes to them.

The handoff's rule, kept: *"Do not hard-code these exact model names as
permanent architecture."* Models are data here, not code. Swapping Qwen3-4B for
something better should be an edit to a registry entry, not a change to the
tournament, the ledger or the authority layer.

About the numbers below
-----------------------
Every byte figure is a **declared estimate**, and the estimates are deliberately
conservative. Quantised weight sizes are roughly what a Q4_K_M GGUF of that
parameter count occupies; runtime overhead and per-token KV cost are order-of-
magnitude allowances, not measurements.

Nobody has run any of these on the OmniBook. `docs/OMEGA13-ANALYSIS.md` §6 keeps
that UNKNOWN, and the right way to close it is `tools/benchmark_model.py` on the
actual machine — which needs a model file, which is a persistent effect
requiring its own authorization.

What the roles mean
-------------------
A role is a job in a tournament, not a model. Several roles usually share one
resident model; the router is deliberately tiny so it can stay resident and
routing never pays a load.
"""
from __future__ import annotations

from dataclasses import dataclass

from .ledger import GIB, MIB, ModelProfile
from .provider import (
    DEFAULT_LMSTUDIO_BASE_URL,
    DEFAULT_OLLAMA_BASE_URL,
    ModelProvider,
    ProviderUnavailable,
)

__all__ = ["ModelRegistry", "RegistryError", "candidate_profiles", "ollama_provider", "lmstudio_provider"]


class RegistryError(Exception):
    """The registry cannot satisfy a request."""


def candidate_profiles() -> tuple[ModelProfile, ...]:
    """Starting profiles for the models the handoff shortlisted.

    Every figure is an estimate. `gpt-oss:20b` is included **so that the ledger
    can refuse it**: it is Codex's built-in default
    (`ollama/src/lib.rs:16`), so a B1 that simply did not mention it would
    inherit it by omission. Listed and sized honestly, the ledger turns that
    inheritance into a visible refusal on a 16 GB machine.
    """
    return (
        ModelProfile(
            model_id="qwen3:1.7b",
            provider_id="ollama",
            weights_bytes=int(1.2 * GIB),
            kv_bytes_per_token=48 * 1024,
            runtime_overhead_bytes=384 * MIB,
            default_context_tokens=8192,
            roles=("router", "classifier", "background"),
            notes="Small enough to stay resident so routing never pays a load.",
        ),
        ModelProfile(
            model_id="qwen3:4b",
            provider_id="ollama",
            weights_bytes=int(2.6 * GIB),
            kv_bytes_per_token=96 * 1024,
            runtime_overhead_bytes=512 * MIB,
            default_context_tokens=8192,
            roles=("generalist", "specialist", "synthesiser", "tool"),
            notes="The handoff's 3B-4B sweet spot; hot-loaded, not resident.",
        ),
        ModelProfile(
            model_id="phi4-mini:3.8b",
            provider_id="ollama",
            weights_bytes=int(2.5 * GIB),
            kv_bytes_per_token=96 * 1024,
            runtime_overhead_bytes=512 * MIB,
            default_context_tokens=8192,
            roles=("specialist", "critic", "reasoning"),
            notes="An independent competitor: different family to Qwen, so its "
                  "mistakes are less likely to correlate.",
        ),
        ModelProfile(
            model_id="qwen3:8b",
            provider_id="ollama",
            weights_bytes=int(5.0 * GIB),
            kv_bytes_per_token=128 * 1024,
            runtime_overhead_bytes=640 * MIB,
            default_context_tokens=8192,
            roles=("heavy-specialist",),
            notes="On demand only. Two of these do not co-reside on this machine.",
        ),
        ModelProfile(
            model_id="gpt-oss:20b",
            provider_id="ollama",
            weights_bytes=int(12.0 * GIB),
            kv_bytes_per_token=160 * 1024,
            runtime_overhead_bytes=1 * GIB,
            default_context_tokens=8192,
            roles=(),
            notes="Codex's built-in default and wrong for this machine. Listed "
                  "with honest figures so the ledger refuses it explicitly rather "
                  "than B1 inheriting it by not mentioning it.",
        ),
    )


def ollama_provider() -> ModelProvider:
    """Ollama, which already speaks the Responses API. No shim, no API key."""
    from .provider import HttpResponsesProvider

    return HttpResponsesProvider(provider_id="ollama", base_url=DEFAULT_OLLAMA_BASE_URL)


def lmstudio_provider() -> ModelProvider:
    """LM Studio, likewise Responses-native."""
    from .provider import HttpResponsesProvider

    return HttpResponsesProvider(provider_id="lmstudio", base_url=DEFAULT_LMSTUDIO_BASE_URL)


@dataclass
class ModelRegistry:
    """Maps roles to models, and models to the providers that can serve them."""

    def __init__(self) -> None:
        self._profiles: dict[str, ModelProfile] = {}
        self._providers: dict[str, ModelProvider] = {}

    def register_profile(self, profile: ModelProfile) -> None:
        self._profiles[profile.model_id] = profile

    def register_provider(self, provider: ModelProvider) -> None:
        self._providers[provider.provider_id] = provider

    def profile(self, model_id: str) -> ModelProfile:
        try:
            return self._profiles[model_id]
        except KeyError:
            raise RegistryError(
                f"no profile for {model_id!r}; a model with no declared footprint "
                f"cannot be budgeted, and loading it would be guessing"
            ) from None

    def provider_for(self, model_id: str) -> ModelProvider:
        profile = self.profile(model_id)
        try:
            return self._providers[profile.provider_id]
        except KeyError:
            raise RegistryError(
                f"{model_id!r} declares provider {profile.provider_id!r}, which is "
                f"not registered"
            ) from None

    def models_for_role(self, role: str) -> tuple[str, ...]:
        """Every registered model willing to take ``role``, in registration order."""
        return tuple(
            model_id
            for model_id, profile in self._profiles.items()
            if role in profile.roles
        )

    def profiles(self) -> tuple[ModelProfile, ...]:
        return tuple(self._profiles.values())

    def reachable_models(self) -> dict[str, tuple[str, ...]]:
        """What each provider says it can actually serve, right now.

        An empty tuple means the provider answered with nothing or did not
        answer at all — the environment as it is, not as the registry hopes.
        Callers should treat a registered-but-unreachable model as absent
        rather than as a failure to explain away.
        """
        out: dict[str, tuple[str, ...]] = {}
        for provider_id, provider in self._providers.items():
            try:
                out[provider_id] = provider.available_models()
            except ProviderUnavailable:
                out[provider_id] = ()
        return out
