"""Model providers: the boundary where model output enters B1 as evidence.

The rule this module exists to enforce is the handoff's, and it is not a
detail: **a model's output is evidence, never authority.** A provider returns a
`Completion`. A `Completion` cannot authorize anything, cannot be recorded as
`VERIFIED` on its own, and carries no way to claim it was. The type system is
doing the work here so that a later caller cannot forget.

Why this layer is not the free abstraction the handoff assumed
--------------------------------------------------------------
`docs/decisions/ADR-0002-responses-wire-api.md` records the finding: the
Codex-derived backend speaks only the Responses API. `WireApi` has exactly one
variant and `wire_api = "chat"` is a hard error. So providers split into two
families, and the split is real work rather than configuration:

* **Responses-native** — Ollama (`:11434`) and LM Studio (`:1234`) already
  expose `/v1/responses`. These need no translation, and they are the fastest
  path to a running system.
* **Chat-completions-only** — llama.cpp's server and most other
  OpenAI-compatible servers. These need B1's shim, which does not exist yet.

Both OSS providers are constructed upstream with `env_key: None` and
`requires_openai_auth: false`, so the local path needs no API key. That is
worth stating because it is what makes "completely local" achievable at all.

No network calls in tests
-------------------------
`HttpResponsesProvider` is the real adapter and is never exercised by the test
suite: a test that needed a running model server would not be a test of B1.
`DeterministicProvider` stands in, and it is deliberately *not* a pretend
model — it is an explicitly deterministic participant, useful both as a test
double and as a real baseline competitor whose answers a tournament can
compare against.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Protocol

__all__ = [
    "ProviderError",
    "ProviderUnavailable",
    "Completion",
    "ModelProvider",
    "DeterministicProvider",
    "HttpResponsesProvider",
    "DEFAULT_OLLAMA_BASE_URL",
    "DEFAULT_LMSTUDIO_BASE_URL",
]

# Taken from the supplied Codex source: model-provider-info/src/lib.rs:504-505.
DEFAULT_LMSTUDIO_BASE_URL = "http://localhost:1234/v1"
DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434/v1"


class ProviderError(Exception):
    """A provider could not produce a completion."""


class ProviderUnavailable(ProviderError):
    """The provider is not reachable or not configured.

    Distinct from a provider that answered badly. "Nobody is listening on this
    port" is an environment fact; "the model said something wrong" is evidence.
    Collapsing them would let an absent model server look like a model opinion.
    """


@dataclass(frozen=True, slots=True)
class Completion:
    """One model's answer, as evidence.

    There is deliberately no ``verified`` flag, no ``confidence`` the adjudicator
    could mistake for proof, and no method that promotes this to anything. A
    model saying it is sure is still a model saying something.
    """

    model_id: str
    provider_id: str
    text: str
    # Which prompt produced this, so a candidate can be traced back to its
    # input rather than being trusted because it exists.
    prompt_digest: str
    # Tokens are for the resource ledger, not for ranking. A longer answer is
    # not a better one.
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # Wall-clock, kept OUT of any digest for the same reason the journal keeps
    # it out: it would make otherwise identical evidence non-reproducible.
    latency_ms: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def digest(self) -> str:
        """Content digest of the answer, excluding timing.

        Two providers that returned the same text for the same prompt produce
        the same digest, which is what lets a tournament notice agreement
        without treating agreement as correctness.
        """
        payload = json.dumps(
            {
                "model_id": self.model_id,
                "prompt_digest": self.prompt_digest,
                "text": self.text,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def evidence_ref(self) -> str:
        """How this completion is cited in an event envelope's evidence_refs.

        The ``model:`` prefix matters. Anything reading the journal can tell at
        a glance that this piece of evidence came from a model rather than from
        a compiler, a test run or a filesystem read-back.
        """
        return f"model:{self.provider_id}/{self.model_id}#{self.digest()[:16]}"


class ModelProvider(Protocol):
    """What B1 requires of anything that can answer a prompt."""

    provider_id: str

    def complete(
        self,
        *,
        model_id: str,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout_s: float = 120.0,
    ) -> Completion:
        """Answer a prompt, or raise ``ProviderError``."""
        ...

    def available_models(self) -> tuple[str, ...]:
        """Models this provider can currently serve.

        May be empty. An empty list is a fact about the environment, not a
        failure: a provider with nothing loaded is a provider with nothing
        loaded.
        """
        ...


def prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class DeterministicProvider:
    """A provider whose answers are a pure function of the prompt.

    Two roles, both honest:

    1.  **Test double.** The tournament's behaviour can be tested exhaustively
        without a model server, because the answers are fixed by construction.
    2.  **Baseline competitor.** A deterministic participant is a legitimate
        tournament entrant. When a task has a computable answer, the
        deterministic responder is frequently *right* and the models are
        guessing, and a tournament that could not include it would be
        systematically worse.

    It is not a mock pretending to be a language model, and nothing here should
    ever be reported as a model's output.
    """

    def __init__(
        self,
        *,
        provider_id: str = "deterministic",
        answers: dict[str, str] | None = None,
        default_answer: str = "",
        models: tuple[str, ...] = ("deterministic-1",),
    ) -> None:
        self.provider_id = provider_id
        # Keyed by exact prompt so a test can state precisely what a
        # participant says, rather than matching on a substring and hoping.
        self._answers = dict(answers or {})
        self._default = default_answer
        self._models = models
        self.calls: list[tuple[str, str]] = []

    def complete(
        self,
        *,
        model_id: str,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout_s: float = 120.0,
    ) -> Completion:
        if model_id not in self._models:
            raise ProviderUnavailable(
                f"{self.provider_id} does not serve {model_id!r}; it serves {self._models}"
            )
        self.calls.append((model_id, prompt))
        text = self._answers.get(prompt, self._default)
        return Completion(
            model_id=model_id,
            provider_id=self.provider_id,
            text=text,
            prompt_digest=prompt_digest(prompt),
            prompt_tokens=len(prompt.split()),
            completion_tokens=len(text.split()),
            metadata={"kind": "deterministic"},
        )

    def available_models(self) -> tuple[str, ...]:
        return self._models


class HttpResponsesProvider:
    """Adapter for a local server exposing the OpenAI Responses API.

    Covers Ollama and LM Studio as they ship: both are constructed upstream
    with `WireApi::Responses`, `env_key: None` and `requires_openai_auth:
    false`, so no credential is involved and nothing leaves the machine.

    Deliberately built on ``urllib`` from the standard library. B1's whole
    verification story is that it runs with no install step, and adding an HTTP
    dependency here would trade that away for convenience.

    **Not exercised by the test suite.** A test that required a running model
    server would be testing the server. What *is* tested is the request this
    builds and the parsing of a recorded response, which is the part B1 owns.
    """

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        # No api_key parameter. The local OSS path does not use one, and
        # accepting one would invite sending a credential to localhost and
        # then, by copy-paste, somewhere else.
    ) -> None:
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")

    def build_request(
        self, *, model_id: str, prompt: str, max_tokens: int, temperature: float
    ) -> tuple[str, dict[str, object]]:
        """The URL and JSON body this provider would send.

        Split out from ``complete`` so it can be asserted on without a network
        call. The shape a provider sends is B1's responsibility; whether a
        server likes it is the server's.
        """
        return (
            f"{self.base_url}/responses",
            {
                "model": model_id,
                "input": prompt,
                "max_output_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            },
        )

    @staticmethod
    def parse_response(payload: dict[str, object]) -> str:
        """Pull the text out of a Responses API payload.

        Handles the shapes the API actually returns rather than assuming one:
        a convenience ``output_text``, or the nested ``output`` item list. An
        unrecognised shape raises instead of returning an empty string, because
        silently producing "" would enter the tournament as a candidate that
        merely looks unhelpful rather than as the parse failure it is.
        """
        text = payload.get("output_text")
        if isinstance(text, str):
            return text
        if isinstance(text, list) and all(isinstance(part, str) for part in text):
            return "".join(text)

        output = payload.get("output")
        if isinstance(output, list):
            chunks: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                for part in item.get("content", []) or []:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        chunks.append(part["text"])
            if chunks:
                return "".join(chunks)

        raise ProviderError(
            f"unrecognised Responses payload shape; keys were {sorted(payload)}. "
            f"Refusing to return an empty completion, which would enter a tournament "
            f"as an unhelpful candidate rather than as the parse failure it is."
        )

    def complete(
        self,
        *,
        model_id: str,
        prompt: str,
        max_tokens: int = 512,
        temperature: float = 0.0,
        timeout_s: float = 120.0,
    ) -> Completion:
        import time
        import urllib.error
        import urllib.request

        url, body = self.build_request(
            model_id=model_id, prompt=prompt, max_tokens=max_tokens, temperature=temperature
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise ProviderUnavailable(
                f"{self.provider_id} at {self.base_url} is not reachable: {exc}. "
                f"If this is Ollama, is it running? If llama.cpp, it speaks "
                f"chat-completions and needs B1's shim (see ADR-0002)."
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise ProviderUnavailable(f"{self.provider_id} timed out: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ProviderError(f"{self.provider_id} returned unparseable JSON: {exc}") from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        usage = payload.get("usage") or {}
        return Completion(
            model_id=model_id,
            provider_id=self.provider_id,
            text=self.parse_response(payload),
            prompt_digest=prompt_digest(prompt),
            prompt_tokens=usage.get("input_tokens") if isinstance(usage, dict) else None,
            completion_tokens=usage.get("output_tokens") if isinstance(usage, dict) else None,
            latency_ms=latency_ms,
            metadata={"kind": "responses", "base_url": self.base_url},
        )

    def available_models(self) -> tuple[str, ...]:
        """Ask the server what it has. Empty on any failure, never a guess."""
        import urllib.error
        import urllib.request

        try:
            with urllib.request.urlopen(f"{self.base_url}/models", timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            return ()
        data = payload.get("data")
        if not isinstance(data, list):
            return ()
        return tuple(
            str(item["id"])
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        )
