# Running B1's multi-model tournament

**On Windows, and on the OmniBook specifically, start with
[OMNIBOOK.md](OMNIBOOK.md).** The commands below are written `python3`, which is
the POSIX spelling; on Windows it is `python`, and there are two other
Windows-specific things worth knowing before you begin.

## The short version

```bash
python3 tools/run_tournament.py --probe
```

That tells you where you stand, from facts rather than from this document. It
reports the resource budget, which shortlisted models fit it, and whether any
local model server is actually answering.

If nothing is answering, you are **one install and one pull** away:

```bash
# Ollama — https://ollama.com/download
ollama pull qwen3:4b

python3 tools/run_tournament.py --provider ollama --model qwen3:4b
```

LM Studio works the same way on port 1234.

## Why that is the whole list

`docs/decisions/ADR-0002-responses-wire-api.md` records the finding that shapes
this: the Codex-derived backend speaks **only** the Responses API. `WireApi` has
exactly one variant, and `wire_api = "chat"` is a hard deserialization error.

Ollama and LM Studio already expose `/v1/responses`, and upstream constructs both
with `env_key: None` and `requires_openai_auth: false`. So for those two there is
**no shim, no API key, and nothing leaves the machine**.

llama.cpp is the exception, and it is the one the handoff named as primary. Its
server speaks chat-completions, so it needs B1's translation shim, which does not
exist yet. If llama.cpp is your target, that shim is the blocker — not anything
else in this repository.

## What runs today, with no model at all

```bash
python3 tools/run_tournament.py
```

A complete tournament: candidate generation, deterministic verification, layered
adjudication, and a journal record with provenance. The participant is B1's
deterministic responder rather than a language model.

That is not a mock standing in for the real thing. A deterministic participant is
a legitimate tournament entrant — when a task has a computable answer it is
frequently right while models are guessing — and it means the *layering* is
exercised end to end before any weights exist. Adding a model adds competitors to
a tournament that already works; it does not switch on a different code path.

## What adding models changes, and what it does not

Adding a model gives the tournament something to disagree with. It does **not**
change who wins an argument:

- Deterministic verification eliminates candidates. Not down-ranks — eliminates.
- Model synthesis then chooses among survivors, and only among survivors.
- The authority layer decides what may actually happen, separately from what is
  true.

`python/tests/test_tournament.py` holds this up with the case that matters: four
models agree on a wrong answer, one deterministic participant gives the right
one, and a single check decides it. Agreement is recorded because it is
interesting; it is never an input to elimination. Five models agreeing is five
samples from correlated training distributions.

## The 16 GB arithmetic

The probe prints it, and it is worth understanding before choosing a model:

```
  total                   16.0 GiB
  reserved for non-models  6.0 GiB   <- OS, UI, journal, gate, both runtime peers
  model budget            10.0 GiB
```

Against that budget:

| model | predicted | |
|---|---|---|
| `qwen3:1.7b` | 1.95 GiB | resident router |
| `qwen3:4b` | 3.85 GiB | the sweet spot |
| `phi4-mini:3.8b` | 3.75 GiB | independent competitor, different family |
| `qwen3:8b` | 6.62 GiB | on demand, one at a time |
| `gpt-oss:20b` | 14.25 GiB | **does not fit** |

That last row is deliberate. `gpt-oss:20b` is Codex's *built-in default*
(`ollama/src/lib.rs:16`), so a B1 that simply never mentioned it would inherit it
by omission. Listing it with honest figures turns that inheritance into a visible
refusal.

Models are hot-loaded, asked, and released. The router stays resident so routing
never pays a load. One model process per logical agent is what the handoff warns
against, and the ledger is what stops it happening by accident.

**Every number above is a `WORKING_ASSUMPTION.** They are arithmetic over
declared profile estimates, not observed RSS on your machine. The ledger's own
report says so in its `basis` field rather than leaving it to a footnote.

## What is still genuinely unknown

No amount of work in this repository produces these. They are properties of your
hardware, measurable only on it:

- tokens/sec for any model on the Snapdragon X X1-26-100
- actual resident memory versus these predictions
- cold-load and warm latency
- thermal behaviour under sustained tournament load
- whether the Hexagon NPU is reachable at all through any of these runtimes
  (`IN_DOUBT` — vendor claims exist, runtime proof does not)

When you have a model running, those become measurable, and the profiles in
`python/b1_models/registry.py` should be replaced with what you measured. Until
then they stay estimates and say so.

## What B1 will not do for you

Pull a model. Downloading weights is a persistent effect — disk, bandwidth, a
file that outlives the process — and it needs your authorization, not an
assumption. The probe prints the command; you run it.

## Reading the result

```
verdict          : ACCEPT
epistemic status : VERIFIED
```

`VERIFIED` here means a conclusive deterministic check passed. A result that only
models liked reads `WORKING_ASSUMPTION`, and a task class touching a persistent
effect stops at `AUTHORIZATION_REQUIRED` — the tournament has chosen a candidate,
and authorized nothing.

Every run appends to the root journal, so a result has provenance rather than
scrolling away. Point it at a durable one with `--journal path/to/root.db` and
the history accumulates across runs, chained and verifiable.
