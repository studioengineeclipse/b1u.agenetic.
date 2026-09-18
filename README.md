# B1 Local

A local-first multi-model agent platform for Windows ARM64: canonical event identity, one
authoritative history, an authority gate above it, and a multi-model tournament. Security
evidence, the desktop UI and the pet overlay are designed but not built.

## Can I run the multi-model yet?

```bash
python3 tools/run_tournament.py --probe
```

That answers it from facts about your machine rather than from this README. If no local model
server is answering, you are one install and one pull away — `ollama pull qwen3:4b` — because
Ollama and LM Studio already speak the Responses API the Codex-derived backend requires. No
shim, no API key, nothing leaves the machine. **[docs/RUNNING.md](docs/RUNNING.md)** is the
bring-up guide.

The tournament runs today with no model at all, against B1's deterministic participant. That is
not a mock: a deterministic entrant is frequently right when models are guessing, and it means
the layering is exercised end to end before any weights exist.

## What is verified, on this machine

The point of this project is that its claims are checked by running code rather than asserted by
documentation, so what follows says what is verified, what is assumed, and what is simply not
known yet.

```
$ python3 tools/verify_all.py

  provenance record                       PASS     12 derived files declared; publication BLOCKED
  python unit tests                       PASS     Ran 114 tests
  rust unit and vector tests              PASS     49 tests passed
  rust/python canonical bytes agree       PASS     3/3 vectors AGREED
  rust/python journal history agrees      PASS     7/7 fields AGREED
  rust/python gate commits one effect     PASS     1 permit, 1 effect, heads AGREED
  fourteen-language participation         PARTIAL  10 POSTCONDITION_VERIFIED, 4 UNKNOWN of 14
  fourteen-language checks actually bite  PARTIAL  10 REFUSED, 4 UNKNOWN of 14
  tournament runs end to end              PARTIAL  verdict=ACCEPT; no model server here
```

Exit code 2: nothing failed, not everything was observed. `PARTIAL` is not a softer `PASS` —
Kotlin, Swift, C# and Dart report `UNKNOWN` because their toolchains are absent here, and the
tournament ran without competing models because no model server is listening. Nobody looked, so
nothing is claimed.

Everything runs with **the Python standard library and a Rust toolchain**. No `pip install`, no
`numpy`, no `pytest`, no npm packages.

## What is here

| Path | What it is |
|---|---|
| `schemas/b1-event-envelope-v1.schema.json` | The canonical event record. Keeps Origin, Authority, Executor and Effect as four separate fields so none can be inferred from another |
| `crates/b1-protocol`, `python/b1_protocol` | Canonical serialization and the envelope, implemented independently in each language |
| `crates/b1-state`, `python/b1_state` | The root journal: one global hash chain on SQLite/WAL |
| `crates/b1-authority`, `python/b1_authority` | The Dual-Core Commit Gate: authority envelopes, one-time fenced permits, transition proofs |
| `python/b1_models` | Providers, model registry, and the resource ledger that keeps a 16 GB machine honest |
| `python/b1_tournament` | Hybrid specialist + competitor tournament with layered adjudication |
| `conformance/vectors/` | Committed canonical bytes every implementation is checked against |
| `contracts/b1-envelope-v1.json` | Fourteen invariants, one owned by each language |
| `polyglot/envelope_v1/` | Fourteen independent consumers |
| `tools/` | Seven verifiers, plus the tournament runner |
| `docs/OMEGA13-ANALYSIS.md` | The full Ω13 deconstruction pass: findings, roadmap, risks, unknowns |
| `docs/decisions/` | Six ADRs, each recording what was decided and what it cost |
| `docs/RUNNING.md` | How to bring up local models on the OmniBook |

## No effect without live authority

Everything consequential goes `propose → grant → claim_permit → consume`. Proposing needs no
authority — analysis is not a persistent effect, so the gate does not gate it. Everything past
that does.

The authority envelope carries the state and plan it was authorized against, so "this
authorization went stale" is a comparison rather than a judgement. And it is checked **twice**:
at claim time, and again before the effect is recorded. A permit can be validly issued and the
world then move while the executor works; recording that effect as authorized would be recording
an authorization that no longer describes what happened.

`project_outcome` is where receipt ≠ effect ≠ postcondition is enforced as a pure function. A
provider reporting success with an unverified postcondition resolves to `IN_DOUBT`, never
`VERIFIED`. `NO_EFFECT` requires an observation, because a reported failure alone does not prove
nothing happened. And an `UNKNOWN` receipt blocks any retry of that effect until real state has
been read back with cited evidence — the failure that prevents is a "failed" payment retried, and
the customer charged twice.

Phase B's exit criterion is proven at two levels. Two Python connections race, and exactly one
permit issues and one effect lands. Then a **Rust process races a Python process** on one SQLite
file: both compute the same authority digest, exactly one permit issues, exactly one effect
reaches history, and both peers agree on the final head. The loser loses by the gate's own rule —
`REFUSED`, then `PERMIT_SPENT` — not by a database lock, because a gate that only worked because
SQLite returned `SQLITE_BUSY` would be relying on an implementation detail.
[ADR-0006](docs/decisions/ADR-0006-commit-gate.md).

## The three things the state layer guarantees

**One canonical form, two independent implementations, checked across a process boundary.** Rust
and Python must produce byte-identical canonical bytes. `verify_cross_language_digest.py` runs the
Rust peer as a separate process and compares bytes, not digests — two implementations linked into
one runtime can share a bug; two processes exchanging bytes cannot share one silently.

Floats and nulls are rejected inside digested payloads. Python renders floats via `repr()` and
Rust `serde_json` via Ryu; for some values these disagree, which would give the two peers
different digests for the same record. In Rust the `Digestable` enum has no float variant at all,
so it is a compile-time impossibility rather than a runtime check.

**One authoritative history, with damage made visible.** The journal is append-only and chained:
`record_digest = SHA256(prior_record_digest || canonical_bytes(envelope))` from a `GENESIS` anchor.
The head digest *and record count* are committed separately from the chain, which is the part
worth knowing about: a prefix of a valid chain is itself a valid chain, so the chain alone cannot
see a truncated tail. The count can.

Both peers detect all seven mutation cases: an edited payload (with `payload_digest` kept
internally consistent, so validation alone would miss it), a truncated tail, a record removed from
the middle, a forged head, a rewritten chain link, a row appended without a head update, and an
undamaged control. Note the limit honestly: **a hash chain proves damage happened; it does not
repair it.**

**Fourteen languages that actually check something.** Each owns one invariant of the canonical
form — and each is shown a vector violating it, and must refuse. A consumer that passed anyway
would be reported as `DECORATIVE`, which is a failure. Ten refuse for the correct reason; four
went unobserved.

## Equal peers, one writer

Rust and Python are equal computational peers: both may analyse, derive subgoals, plan, simulate,
verify and propose. Neither writes to the journal directly. Every append takes a SQLite
`BEGIN IMMEDIATE` lease, which the database grants to exactly one connection at a time, so
concurrent proposals linearise instead of interleaving.

This answers a question the handoff left open — which component owns the writer — without picking
a winner. None does. The database owns the lease and both peers queue for it on equal terms, so
there is no privileged process to fail over from. See
[ADR-0001](docs/decisions/ADR-0001-root-journal.md).

## Not built

Projection topology (compact / modular / audit-replay modes) · the chat-completions shim llama.cpp
needs · security evidence · Tauri desktop · pet overlay · the `%B1_HOME%` private user layer ·
capability policy above per-effect authorization.

`docs/OMEGA13-ANALYSIS.md` §11 has the dependency-ordered roadmap and §16 the next five actions.

## Two findings that change the plan

**Codex speaks only the Responses API.** `WireApi` has exactly one variant;
`wire_api = "chat"` is a hard error and the `ollama-chat` provider was removed. llama.cpp speaks
chat-completions, so it cannot drive a Codex-derived backend without a translation shim — and
Codex's own `responses-api-proxy` is a debugging proxy, not a translator. Also worth knowing:
`gpt-oss:20b` is the *built-in default*, not merely an option, on a 16 GB target.
[ADR-0002](docs/decisions/ADR-0002-responses-wire-api.md).

**Codex Security is not local.** It spawns the `codex` binary, hardcodes
`https://api.openai.com/v1/embeddings`, and requires login or an API key. Its schemas and fifteen
skills *are* portable and cloud-free; its orchestrator is not.
[ADR-0005](docs/decisions/ADR-0005-codex-security-scope.md).

## The upstream archive, re-verified

The ΩΣ13.9 archive this project derives design from was re-run on 2026-09-17: **646 tests passed,
0 failed**, and all fourteen of its own verifiers exit 0. That supersedes the handoff's "45
focused tests" figure and closes its note that an earlier full run had timed out with the
remainder unmeasured.

Two of its release checks assert on exact prose rather than on the property
(`verify_omega12.py:285-286` greps literal strings that the 13.9 README has since reworded, while
keeping and strengthening the guarantees). Paired with the `b1mu.toml` version gap, that is the
same pattern twice: the archive's *checks* have drifted from its *substance*. Neither is a
behavioural regression; both are checks that would not notice one. Recorded in
`docs/OMEGA13-ANALYSIS.md` §4.9 for the rights holder — nothing in that archive was modified.

## Licensing

New B1 Local code is Apache-2.0. **This does not relicense the B1μ-DQAS ΩΣ13.9 archive**, which is
all-rights-reserved and is not vendored here. `verify_provenance.py` reports
`PUBLICATION: BLOCKED` until the rights holder makes that decision — see
[ADR-0004](docs/decisions/ADR-0004-license-and-publication.md),
[`docs/provenance.md`](docs/provenance.md) and
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

Not affiliated with or endorsed by OpenAI. Apache-2.0 §6 withholds trademark rights, so no OpenAI
mark is used as B1 branding.

## Running the verifiers individually

```bash
python3 tools/verify_provenance.py               # provenance complete in both directions
python3 -m unittest discover -s python/tests -v  # Python peer
cargo test --workspace                           # Rust peer
python3 tools/verify_cross_language_digest.py    # canonical bytes agree
python3 tools/verify_cross_language_journal.py   # history agrees
python3 tools/verify_cross_language_gate.py      # a Rust peer races a Python peer
python3 tools/verify_polyglot.py                 # fourteen languages
python3 tools/verify_polyglot_mutations.py       # their checks bite
python3 tools/run_tournament.py --probe          # what can this machine serve?
python3 tools/build_vectors.py                   # regenerate vectors (review the diff)
```

Add `--json` to any verifier for a machine-readable report.
