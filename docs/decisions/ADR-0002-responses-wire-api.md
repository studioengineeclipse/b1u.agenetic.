# ADR-0002 — Codex speaks only the Responses API, so B1 needs a shim

Design-derived-from: codex:codex-rs/model-provider-info/src/lib.rs

- **Status:** Accepted as a constraint. The shim itself is `PLAN_READY`, not built.
- **Date:** 2026-09-16
- **Origin:** M — discovered by reading the supplied Codex source. U confirmed the response
  (shim *and* native adapters) when the finding was put to the user.

## Context

The handoff names llama.cpp as the primary local inference runtime and treats the provider
layer as a free abstraction: *"Build a model registry/provider abstraction so models can be
replaced without redesigning authority, tournament, or UI layers."* Reasonable on its face, and
it is how the handoff justifies listing llama.cpp, LM Studio, Ollama, Foundry Local and future
NPU adapters side by side as interchangeable options.

Reading the supplied Codex source, they are not interchangeable. Verified 2026-09-16:

**`WireApi` has exactly one variant.** In `codex-rs/model-provider-info/src/lib.rs:60-67`:

```rust
pub enum WireApi {
    /// The Responses API exposed by OpenAI at `/v1/responses`.
    #[default]
    Responses,
}
```

**`wire_api = "chat"` is a hard error, not a fallback.** Lines 57 and 79-92: deserialising it
returns `CHAT_WIRE_API_REMOVED_ERROR` — *"`wire_api = "chat"` is no longer supported. How to fix:
set `wire_api = "responses"` in your provider config."*

**The `ollama-chat` provider was removed outright.** Lines 58-59 keep
`LEGACY_OLLAMA_CHAT_PROVIDER_ID` alive only to emit an error telling the user to switch.

**The `ollama` crate does not do inference at all.** `codex-rs/ollama/src/client.rs` touches
`/api/tags`, `/api/pull` and `/api/version` — model management. Inference goes through the OSS
provider's base URL, and lines 533 and 537 construct both the Ollama and LM Studio OSS providers
with `WireApi::Responses`.

The consequence the handoff misses: **llama.cpp's server speaks OpenAI chat-completions, not
`/v1/responses`.** A Codex-derived backend cannot drive it. The same applies to the large family
of OpenAI-compatible servers that implement chat-completions only — which is most of them.

`codex-rs/responses-api-proxy` looked like it might already solve this. It does not. Its README
shows it launched with an `OPENAI_API_KEY` and a `--dump-dir`: it is a debugging proxy that
records request/response pairs against the real OpenAI endpoint. It performs no protocol
translation. It remains useful as a *structural* template — a local process that presents
`/v1/responses` upstream — but the translation is B1's to write.

One further finding, separate but related: `codex-rs/ollama/src/lib.rs:16` sets
`DEFAULT_OSS_MODEL = "gpt-oss:20b"`, and `codex-rs/lmstudio/src/lib.rs:7` sets
`"openai/gpt-oss-20b"`. The handoff already flags a 20B model as wrong for a 16 GB machine
(§21 rule 4). Worth recording that this is not merely a discouraged option but the **built-in
default**, so B1 must override it explicitly rather than simply not selecting it.

## Decision

Build both, behind one provider abstraction.

**A B1 responses shim.** A local process presenting `/v1/responses` to the Codex-derived backend
and translating down to chat-completions for the upstream runtime. This is what keeps llama.cpp
— and any of the many chat-completions-only servers — usable, and keeps the model registry open
rather than pinned to two vendors.

**Native adapters for Ollama and LM Studio.** Both already expose the Responses API, so routing
them through a translator would add latency and a translation bug surface for nothing.

**An explicit default-model override.** B1 selects its own default from the model registry. It
never inherits `gpt-oss:20b`.

The user chose "shim + native adapters both" over restricting to Ollama/LM Studio, accepting that
it roughly doubles the provider surface and the tests holding it up.

## Consequences

- The provider abstraction is not the free layer the handoff assumes. It carries a real protocol
  translation, and that translation needs its own conformance vectors and round-trip tests, in the
  same spirit as `conformance/vectors/`. Streaming, tool calls and reasoning items are where a
  chat-completions/Responses translation is hardest, and none of them is optional for an agent
  runtime.
- Phase 4's exit criterion in the handoff ("a known model can load, answer, unload, and report
  measured RAM/latency on the exact OmniBook") now has a prerequisite: the shim has to exist
  before llama.cpp can answer at all through a Codex-derived path.
- The shim is a place where a bug is silent rather than loud: a translation that drops a tool call
  produces a plausible-looking answer with a missing action. Its tests should therefore assert on
  the translated request and response bytes, not only on "a reply arrived".
- If a future Codex version reintroduces a chat wire API, the native adapters stay and the shim
  becomes optional. Nothing about this decision is hard to unwind, which is part of why it is
  acceptable to take the larger surface now.

## Not decided here

Which quantised model B1 defaults to, what the shim's streaming translation looks like, and
whether the Hexagon NPU can be reached at all on the Snapdragon X X1-26-100. That last one stays
`IN_DOUBT`: no local runtime proof exists, and none can be produced from this container.

## Verification status

The Codex findings above are `VERIFIED` by direct reading of the supplied source at the cited
paths. Everything about the shim's behaviour is `PLAN_READY` — no shim code exists, and no claim
about its performance should be made until it does.
