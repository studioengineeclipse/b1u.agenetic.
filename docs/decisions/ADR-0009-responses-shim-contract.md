# ADR-0009 — The shim's contract, written before the shim

- **Status:** Accepted. Contract and vectors built and verified. **The shim itself is not built.**
- **Date:** 2026-09-18
- **Origin:** M — §16 Action 5, which named the ordering explicitly: *"conformance vectors, before
  any shim code"*.

## Context

[ADR-0002](ADR-0002-responses-wire-api.md) established the constraint: Codex has exactly one
`WireApi` variant, `wire_api = "chat"` is a hard error, and llama.cpp's server speaks
chat-completions. A Codex-derived backend cannot drive it without translation, and Codex's own
`responses-api-proxy` is a debugging proxy that translates nothing.

The obvious next move is to write the shim. The roadmap said not to, and the reason is worth
stating rather than assuming: **a translation layer is judged entirely by what it preserves.**
A conformance suite written after the code tends to describe what the code already does. Written
first, it describes what the code must do — and the difference between those two shows up exactly
where the translation is lossy, which is the only place it matters.

## Decision

Ten named invariants in `contracts/b1-responses-shim-v1.json`, one conformance vector each in
`conformance/responses/`, a reference translation in `python/b1_shim/translate.py`, and — the part
that makes the suite mean something — **a deliberately lossy translator per invariant** in
`python/b1_shim/lossy.py`.

| | Invariant | The failure it prevents |
|---|---|---|
| R1 | instructions survive | The system prompt is silently dropped. The answers look normal. |
| R2 | tool declarations survive exactly | A schema that gains a constraint on the way through changes what the model may emit |
| R3 | tool-call arguments are opaque | A round-trip renormalises the bytes; the call still parses and is no longer the call the model made |
| R4 | a tool result rejoins its call | The model reasons from an answer to a question it did not ask |
| R5 | reasoning is never silently dropped | The highest-value case for a local reasoning model degrades invisibly |
| R6 | a truncated completion never reads as complete | receipt ≠ postcondition, in the transport layer |
| R7 | usage is reported, never invented | Zeros are indistinguishable from measurements; a ledger budgets against fiction |
| R8 | streamed text concatenates | A dropped delta is wrong in a way no status field reports |
| R9 | streamed tool arguments stay apart | Parallel calls merge into one malformed blob |
| R10 | an unknown field is refused | A future field that constrains behaviour is discarded and the request means something else |

### The verifier checks three things, and only the last two are evidence

1. The reference translation passes every vector. **This is a regression fence, not an oracle** —
   the expected outputs were generated from that same translation, and `build_responses_vectors.py`
   says so in its own docstring.
2. Every vector *fails* the translator that breaks its invariant. A vector that passes both is
   reported `DECORATIVE`, the same word and the same failure as a polyglot consumer that accepts a
   vector violating its own rule.
3. Each vector's collateral damage is reported: which *other* lossy translators it also fails. A
   vector that every one of them fails is broad rather than precise, and a broad vector makes a
   real regression harder to locate.

The lossy translators are plausible shortcuts, not contrived errors. `drop_instructions` is what
happens when someone forgets Responses carries the system prompt out of band. `reserialise_arguments`
is what happens when someone round-trips JSON because it seems tidier. `zero_usage` is what happens
when a downstream type wants an integer. None of them look like bugs while you are writing them.

### R5 is the one with a judgement call in it

Chat-completions has no reasoning item, so a translator has three options: drop it, carry it in
some other shape, or refuse. Dropping is forbidden — it is the failure R5 exists to name. Refusing
every reasoning-carrying request would make the shim useless with exactly the models it is most
wanted for.

So it is carried as a conspicuously labelled assistant message *and* the caller is handed a
`TranslationNote` with severity `REPRESENTED_DIFFERENTLY`. "Never silently" is satisfied by the
note, not by the carrying: a translation with the right payload and no note has not honoured R5,
and the verifier checks the notes as part of the vector.

### Refusal over best effort, throughout

`Untranslatable` is raised for a pre-parsed arguments object, an unrecognised content part, a
missing `finish_reason`, a streamed tool call with no index, a reasoning item with no readable
summary, and any unknown request field. In every case a best-effort translation was available and
was rejected, because a translator that degrades quietly is worse than one that stops: the caller
cannot tell a faithful translation from a lossy one that happened to still parse.

## The honest part: what these vectors are not

**The field spellings are `WORKING_ASSUMPTION`.** The Codex archive is not present in this
environment, so the key names were not re-derived from source in this pass. What is `VERIFIED`
(ADR-0002, 2026-09-16) is the wire-api selection. What is assumed is every key name in the
vectors.

Every invariant is phrased in terms of what must survive rather than what it is called, so a
corrected spelling changes the vectors and not the contract. But a vector encoding a guessed field
name is a real test of its invariant and **is not evidence about the wire**, and the verifier
prints that on every run rather than leaving it in a docstring. Re-check the vectors against the
archive before writing a shim against them.

**No transport.** No HTTP server, no SSE framing, no backpressure, no connection handling, no error
mapping, no retries. This is the meaning of the bytes, not the moving of them. Calling
`python/b1_shim` "the shim" would be the overclaim this whole project is built to avoid — it is the
shim's contract, and the package docstring says so.

**No provider quirks.** Real servers deviate from documented shapes, and those deviations are found
by running against them.

## Consequences

Good:

- The hard part of the shim is now specified and checked. What remains is plumbing, which is
  tedious and not subtle.
- The suite is shown to discriminate rather than asserted to. Ten vectors, ten catching a lossy
  translator.
- `b1_shim.translate` is pure and has no dependencies beyond the standard library, so the meaning
  of the translation can be tested without a server, a socket or a model.

Costs:

- A contract written against assumed spellings will need a revision pass. That cost is smaller than
  the alternative: a shim written against assumed spellings, discovered wrong after the transport
  exists.
- Ten invariants is not all of them. Images, files, citations, `tool_choice` semantics, refusal
  items, annotations and multi-choice responses are all untouched; several are refused outright
  rather than being translated, which is correct for now and is not a complete shim.
- The reference translation could drift from the vectors' intent without the verifier noticing,
  because it generates them. Finding 27 below is what that looks like when it happens.

## Verified

`tools/verify_responses_vectors.py` — PASS: 10 vectors for 10 invariants, 10 catch a lossy
translator.

`python/tests/test_shim.py` — 29 tests stating the properties directly rather than by recorded
example, including the R8 concatenation property, the R3 control (a round-trip *would* have changed
the string), and nine refusal cases.

**Found by the verifier, in its own first run:** the R3 vector was `DECORATIVE`. Its arguments
string was `{"path": "notes/a.md", "limit": 10}`, which survives a JSON round-trip unchanged —
Python preserves key order and that spacing is already what `json.dumps` emits. The vector passed
the lossy translator as well as the reference, establishing nothing. It now uses
`{"path":"notes/a.md","limit":1e1,"note":"café"}`, where a round-trip inserts whitespace,
renormalises `1e1` to `10.0` and escapes the non-ASCII character. That is the third time in this
project a check has been written that could not fail, and the second time a verifier caught it
rather than a person.
