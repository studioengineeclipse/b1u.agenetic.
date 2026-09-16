# B1μ-DQAS Ω13 — Deconstruction, Reconstruction and Convergence Findings

**Subject:** the B1 Local Claude Master Build Handoff (2026-09-15) and the three supplied
archives — OpenAI Codex, OpenAI Codex Security, B1μ-DQAS ΩΣ13.9 Quantum-Fidelity.
**Date of this pass:** 2026-09-16.
**Output contract:** the twenty-one sections required by the Ω13 deterministic output contract,
in order.

Everything below distinguishes what was *measured in this session* from what was *supplied* by
the handoff. Where the two disagree, the measurement is cited with a file path and line number
and the handoff's claim is named as superseded. Where neither could be established, the entry
says `UNKNOWN` or `IN_DOUBT` and says why rather than closing the gap with plausible text.

---

## 1. Strategic Goal

Establish a verifiable causal substrate for B1 Local — canonical event identity, one
authoritative history, and independent multi-language conformance — such that every later phase
(model router, tournament, security evidence, desktop, pet) is built on state whose integrity is
checked by running code rather than asserted by documentation.

Traceable to U: the handoff's §19 priority matrix marks exactly four items CRITICAL — the
source/licensing/provenance map, the canonical event envelope, the root authoritative journal,
and the Rust/Python commit protocol with fencing and effect identity. This pass delivers the
first three as running, verified code and identifies the precise seam the fourth plugs into.

The goal is deliberately *not* "build B1 Local". The handoff describes a ten-phase product. A
pass that claimed to deliver it would be the kind of complexity-mistaken-for-advancement the
operating contract's §29 warns against.

## 2. Success Criteria

| # | Criterion | How observed | Result |
|---|---|---|---|
| 1 | Every reused upstream is licence-mapped, and an undeclared derivation cannot enter the tree | `tools/verify_provenance.py` | **Met.** PASS, 7 derived files declared, publication reported BLOCKED |
| 2 | Rust and Python produce byte-identical canonical bytes for every vector, compared across a process boundary | `tools/verify_cross_language_digest.py` | **Met.** 3/3 AGREED |
| 3 | Both peers derive identical history from one event sequence | `tools/verify_cross_language_journal.py` | **Met.** 7/7 fields AGREED |
| 4 | Journal damage is detected, not merely survived | 7 mutation cases per language | **Met.** All 7 detected in both languages |
| 5 | Fourteen languages participate observably, with absence reported as absence | `tools/verify_polyglot.py` | **Partially met.** 10 POSTCONDITION_VERIFIED, 4 UNKNOWN — see §6 |
| 6 | Each language's check demonstrably bites | `tools/verify_polyglot_mutations.py` | **Partially met.** 10 REFUSED with correct reasons, 4 UNKNOWN |
| 7 | No claim of VERIFIED is made for anything unmeasured | this document; `tools/verify_all.py` reports PARTIAL | **Met.** |

Criterion 5 and 6 are recorded as *partially* met on purpose. Ten of fourteen is what was
observed. Reporting it as met would be the precise substitution of "the harness behaved" for "the
property holds" that the contract's §3 forbids.

## 3. Epistemic State

| Category | Count | Notes |
|---|---|---|
| VERIFIED | 7 success criteria as scored above; 15 findings in §4 | Each cites a path, a line, or a verifier run in this session |
| WORKING_ASSUMPTION | 4 | Listed in §5 |
| UNKNOWN | 5 | Listed in §6 |
| IN_DOUBT | 2 | Listed in §6 |

One entry deserves separate mention because the handoff presents it as settled: the claim
*"45/45 focused tests passed on 2026-09-15"* for ΩΣ13.9 is **supplied evidence, not re-verified**.
Neither `numpy` (a hard dependency declared in the archive's `pyproject.toml`) nor `pytest` is
installed in this container, so the suite could not be run. Re-running it needs
`pip install numpy pytest`, a persistent environment change that has not been authorised. It is
recorded as WORKING_ASSUMPTION, not VERIFIED, and §5 says so.

## 4. Verified Facts

Measured in this session by direct inspection of the supplied archives and by running code in
this repository.

### 4.1 Archive identity and licensing

| Archive | Entries | SHA-256 (first 16) | Licence |
|---|---|---|---|
| Codex | 7,673 | `89b649348fab595c` | Apache-2.0, with `NOTICE` declaring Ratatui (MIT) |
| Codex Security | 669 | `3322deac5d7891cb` | Apache-2.0 (`package.json`: `@openai/codex-security@0.1.24`) |
| ΩΣ13.9 | 570 | `fde33c81e59800ca` | **All rights reserved.** No redistribution, sublicensing or commercial use granted |

The entry counts match the handoff exactly. The ΩΣ13.9 licence is confirmed verbatim: *"No patent,
copyright, trademark, redistribution, sublicensing, or commercial-use license is granted by this
file."*

### 4.2 The Codex wire-protocol constraint — supersedes the handoff's runtime plan

This is the most consequential correction in the pass. See `docs/decisions/ADR-0002-responses-wire-api.md`.

`codex-rs/model-provider-info/src/lib.rs:60-67` — `WireApi` has **exactly one** variant,
`Responses`. Line 57 and lines 79-92 — `wire_api = "chat"` is a hard deserialization error, not a
fallback. Lines 58-59 — the `ollama-chat` provider was removed. `codex-rs/ollama/src/client.rs` —
the `ollama` crate touches only `/api/tags`, `/api/pull`, `/api/version`: model management, not
inference. Lines 533 and 537 — both the Ollama and LM Studio OSS providers are constructed with
`WireApi::Responses`.

Therefore **llama.cpp, which speaks chat-completions, cannot drive a Codex-derived backend**. The
handoff names llama.cpp as the primary runtime candidate and treats the provider layer as a free
abstraction; it is not. `codex-rs/responses-api-proxy` is a dump/debug proxy against the real
OpenAI endpoint, per its own README — a structural template, not a translator.

Related: `codex-rs/ollama/src/lib.rs:16` sets `DEFAULT_OSS_MODEL = "gpt-oss:20b"`;
`codex-rs/lmstudio/src/lib.rs:7` sets `"openai/gpt-oss-20b"`. The handoff discourages a 20B model
on a 16 GB machine but does not note that it is the **built-in default**, so B1 must override it
rather than merely not choose it.

### 4.3 Codex Security is not local — supersedes the handoff's Phase 7 scope

See `docs/decisions/ADR-0005-codex-security-scope.md`.

`sdk/typescript/src/runtime.ts:2373` spawns the `codex` binary.
`sdk/typescript/src/server/embeddings.ts:82` posts to a hardcoded
`https://api.openai.com/v1/embeddings`. The README requires `codex-security login` or
`OPENAI_API_KEY`, and gates some findings behind OpenAI's "Trusted Access for Cyber" programme.
Only `sdk/typescript/src/deduplication/checkpointed-review.ts:46` honours `OPENAI_BASE_URL`.

The portable part is real and substantial: `plugins/codex-security/schemas/` (findings, coverage,
scan-manifest, patch-risk) and `plugins/codex-security/skills/` (fifteen skills). Those need no
network and no account.

### 4.4 ΩΣ13.9 already implements most of handoff Phases 1-3

`src/b1mu/durable.py` carries: `PRAGMA journal_mode=WAL` (line 103); an append-only `journal`
table chained `prior_record_digest` → `record_digest` from a `GENESIS` anchor (lines 84-92,
176-221); committed head digest plus record count on run rows for truncation detection (line 223
of `README.md` states the property explicitly); v1→v4 migrations that preserve chain identity
(lines 198-221); `verify_journal` (line 604); and wall-clock exclusion from digested payloads
(line 76). `src/b1mu/serialization.py` carries the canonical-JSON rule. `src/b1mu/work/store.py`
carries `workspaces`/`projects`/`contexts`/`runs`/`steps`/`approvals`/`fences`/`permits`/
`artifacts`/`schedules`/`work_events`, and `src/b1mu/work/runtime.py`'s `SovereignWorkRuntime`
implements approvals → one-time permits → fencing → permit-digest-bound transition proofs.

The handoff's Phases 1-3 are therefore substantially **already built in Python**. The genuine
delta is architectural, not absent: the upstream journal chains per `(run_id, attempt, seq)`,
which is the wrong shape for a history spanning subsystems that are not runs of a program.

### 4.5 ΩΣ13.9 already ships a fourteen-language harness

`polyglot/quantum_fidelity/<lang>/{main.*,manifest.json}` for all fourteen languages,
`contracts/quantum-fidelity-v1.json`, and `tools/verify_omega13_9_polyglot.py` — which enforces
unique responsibilities, unique observable contributions, exact expected-output matching, and
reports a missing toolchain as `UNKNOWN` rather than promoting source-file existence. The
handoff's Phase 9 treats this as greenfield. It is not.

Three real defects in that verifier, found by reading it:

1. It validates each manifest's `build_command` and `run_command` and then executes **hardcoded**
   commands from an internal `_commands()` table instead. The declared commands are decorative —
   in a harness whose entire purpose is preventing decorative participation.
2. It exempts `build_command` from its own non-empty check:
   `if field != "build_command" and value in (None, "", (), []):`.
3. It targets legacy `csc` plus `mono` for C#.

### 4.6 The `b1mu.toml` version question is resolved: stale, not protected

The handoff's §20 item 2 leaves this IN_DOUBT, asking whether `b1mu.toml`'s
`version = "13.8.0-ref1"` intentionally represents an inherited compatibility boundary.

It does not. `tests/test_omega13_9_release_contract.py` asserts `pyproject.toml`
(`version = "13.9.0a1"`), `README.md` and `CHANGELOG.md` — and **never** asserts `b1mu.toml`. The
only other reference, `tools/verify_omega12.py:38`, lists the filename among files checked for
*presence*, not content. Note also that `b1mu.toml`'s `[b1mu]` section carries its own separate
compatibility numbers (`language`, `ir`, `bytecode`, `vm`, `proof`, `adapter_abi`, `omega`), so
the `[package].version` field is not serving that purpose either.

It is an unguarded release-contract gap. The handoff's instruction to add a regression test before
changing it is exactly right, and that test belongs upstream.

### 4.7 What this repository now does, measured

`tools/verify_all.py`, run in this container:

```
provenance record                       PASS     7 derived files declared; publication BLOCKED
python unit tests                       PASS     Ran 48 tests
rust unit and vector tests              PASS     40 tests passed
rust/python canonical bytes agree       PASS     3/3 vectors AGREED
rust/python journal history agrees      PASS     7/7 fields AGREED
fourteen-language participation         PARTIAL  10 POSTCONDITION_VERIFIED, 4 UNKNOWN of 14
fourteen-language checks actually bite  PARTIAL  10 REFUSED, 4 UNKNOWN of 14
```

Exit code 2 — nothing failed, not everything was observed.

Two defects in this repository's own work were found by reading refusal *reasons* rather than
exit codes, and both are worth recording because they are the failure mode the contract's §3
describes: the Ruby mutation produced malformed JSON, so Ruby refused at its JSON parse step with
its actual invariant untested; and the JVM's `JAVA_TOOL_OPTIONS` banner was masking Java's real
message inside truncated failure reports. A green run concealed both.

### 4.8 Toolchain availability in this container

Present (10): `cc`, `c++`, `javac`/`java`, `python3`, `node`, `go`, `rustc`/`cargo`, `tsc`, `php`,
`ruby`. Absent (4): `kotlinc`, `swiftc`, `dotnet`, `dart`. The OmniBook's availability will
differ; the harness reports what it measures rather than what it hopes.

## 5. Working Assumptions

1. **ΩΣ13.9's "45/45 focused tests passed" holds.** Supplied by the handoff, not re-run here
   (`numpy` and `pytest` absent). The static reading of `durable.py`, `work/store.py` and
   `work/runtime.py` is consistent with the claim, which is why this is a working assumption
   rather than IN_DOUBT — but consistency is not execution.
2. **UTF-8 byte order equals Unicode code-point order, so Python `sort_keys` and Rust `BTreeMap`
   agree on key order.** This is true by UTF-8's construction, and `envelope-002-unicode-keys`
   exercises Latin-1, Greek, Han and a non-BMP character to hold it up. It is listed here rather
   than under VERIFIED because three vectors are evidence, not a proof over all inputs.
3. **The four unobserved languages' consumers are correct.** Kotlin, Swift, C# and Dart sources are
   written and reviewed but never executed. This is the weakest assumption in the pass and the one
   most likely to be wrong; see §13 for the risk and §19 for the recovery.
4. **`rusqlite` with `bundled` will build on Windows ARM64.** It builds here on x86-64 Linux and
   compiles SQLite from C source, so it needs only a working C toolchain — but that is an
   inference from how it works, not an observation on the target.

## 6. UNKNOWN / IN_DOUBT

**UNKNOWN — the information is not available from here.**

1. **Any OmniBook performance number.** Throughput, latency, cold-load time, RAM residency and
   thermal behaviour for any model on the Snapdragon X X1-26-100. Nothing in this session ran on
   that machine. The handoff correctly declines to invent these and so does this pass.
2. **Whether the four absent-toolchain consumers execute correctly.** Not a code-quality
   statement; a statement that nobody has run them.
3. **Whether ΩΣ13.9's full test suite passes.** The handoff reports an earlier full run that
   "progressed substantially but timed out before completion". Its unexecuted remainder was
   correctly left UNKNOWN then and remains UNKNOWN now.
4. **What a chat-completions → Responses translation must cover for streaming, tool calls and
   reasoning items.** The shim is `PLAN_READY`; its hard cases have not been mapped.
5. **Whether any B1 workload benefits from the Hexagon NPU.** See IN_DOUBT 2 for the prior
   question.

**IN_DOUBT — evidence exists but does not establish current validity.**

1. **The handoff's "work session + security session chat" artifact.** The handoff's §20 item 1
   marks this unresolved, and nothing in the three supplied archives corresponds to it. It stays
   IN_DOUBT until the user identifies or uploads it. Not closed by guessing.
2. **NPU acceleration on the Snapdragon X X1-26-100.** Vendor material asserts a 45 TOPS Hexagon
   NPU and evidence exists that ~4B-class on-device agents run on Snapdragon X-class hardware, but
   no local runtime proof exists for this SKU through any of the runtimes B1 would use. Marketing
   capability and reachable capability are different claims.

## 7. System Decomposition

```
                              B1 LOCAL
                                 |
        +------------------------+------------------------+
        |                        |                        |
   BUILT THIS PASS          SEAM IDENTIFIED          NOT STARTED
        |                        |                        |
  b1-protocol             Dual-Core Commit Gate     model router + shim
   canonical form          (write lease exists,      tournament runtime
   Event Envelope v1        protocol does not)       security evidence
        |                                            Tauri desktop
  b1-state                                           pet overlay
   root journal                                      B1_HOME private layer
   SQLite/WAL
   chain + head commitment
        |
  polyglot/envelope_v1
   14 consumers
   14 unique invariants
        |
  provenance + licence
   manifest + verifier
```

The layering that matters, and the order it has to hold in:

```
canonical bytes  ->  envelope identity  ->  chained record  ->  authoritative history
     (agreed)           (validated)          (verified)            (replayable)
```

Each arrow is a place where two implementations could disagree, and each has a verifier. Nothing
above this line can be trusted further than the line itself, which is why the line was built
first.

## 8. Fourteen-Language Participation Map

Every language owns exactly one invariant of B1's canonical envelope form. `digest` records whether
that language's **standard library** provides SHA-256; nine do and additionally verify the
vector's digest, five do not and declare the gap rather than carrying a hand-written primitive
nobody on the target machine can execute.

| Language | Owned invariant | Digest | Vector | Status |
|---|---|---|---|---|
| C | `canonical_has_no_insignificant_whitespace` | NOT_IN_STDLIB | 001 | **POSTCONDITION_VERIFIED** |
| C++ | `canonical_ends_with_single_newline` | NOT_IN_STDLIB | 001 | **POSTCONDITION_VERIFIED** |
| Java | `top_level_keys_are_ascending` | STDLIB | 001 | **POSTCONDITION_VERIFIED** |
| Python | `payload_contains_no_float` | STDLIB | 001 | **POSTCONDITION_VERIFIED** |
| JavaScript | `canonical_contains_no_null_literal` | STDLIB | 001 | **POSTCONDITION_VERIFIED** |
| Go | `origin_is_one_of_u_m_p_e` | STDLIB | 001 | **POSTCONDITION_VERIFIED** |
| Rust | `non_ascii_is_unescaped` | NOT_IN_STDLIB | 002 | **POSTCONDITION_VERIFIED** |
| TypeScript | `schema_version_is_exact` | STDLIB | 001 | **POSTCONDITION_VERIFIED** |
| Kotlin | `epoch_is_non_negative_integer` | STDLIB | 001 | **UNKNOWN** — `kotlinc` absent |
| Swift | `payload_digest_is_lowercase_hex64` | NOT_IN_STDLIB | 001 | **UNKNOWN** — `swiftc` absent |
| PHP | `effect_requires_authority_digest` | STDLIB | 003 | **POSTCONDITION_VERIFIED** |
| Ruby | `effect_requires_persistence_class` | STDLIB | 003 | **POSTCONDITION_VERIFIED** |
| C# | `epistemic_status_is_one_of_four` | STDLIB | 001 | **UNKNOWN** — `dotnet` absent |
| Dart | `unicode_keys_are_ascending_by_code_point` | NOT_IN_STDLIB | 002 | **UNKNOWN** — `dart` absent |

Each invariant is load-bearing: violating it would change the canonical bytes, or let an
unauthorised effect or an ungraded claim into history. Two are worth singling out. PHP's
`effect_requires_authority_digest` and Ruby's `effect_requires_persistence_class` are the
authority model itself, checked by languages entirely outside the resident stack — so a bug in
Rust or Python cannot excuse an unauthorised effect from being noticed. Dart's
`unicode_keys_are_ascending_by_code_point` compares by rune rather than UTF-16 code unit, which is
the one case where a language's natural string comparison would silently give the wrong answer.

`tools/verify_polyglot_mutations.py` establishes that these checks bite: each language's own
invariant is broken in turn, the declared digest is recomputed so a STDLIB consumer cannot refuse
for the wrong reason, and that language must refuse. 10 REFUSED, 4 UNKNOWN, every refusal naming
the invariant it owns.

## 9. Ω13 Local Language Selection Map

| Work unit | Primary | Supporting | Why | Rejected, and why |
|---|---|---|---|---|
| Canonical serialization | Rust + Python as equals | — | Both are resident state peers; neither may be the only implementation, or "agreement" is unfalsifiable | A single implementation with ports: a port that shares code with its original cannot disagree with it |
| Root journal | Rust + Python as equals | C (via bundled SQLite) | Same reason; SQLite provides the durability boundary and the write lease | A hand-rolled log: SQLite's `BEGIN IMMEDIATE` already solves single-writer linearisation correctly |
| Conformance consumers | all 14 | — | The point is independence; a consumer sharing code with the implementation proves only that code equals itself | Generating the consumers from one template: identical logic in fourteen syntaxes is one implementation |
| Provenance and verifiers | Python | — | Standard library only, so verification never depends on an install step | Rust: verifiers must run before the toolchain is proven present |
| Model router and shim | Rust | C/C++ | Not built. Rust for the Codex-side integration; C/C++ for the inference runtime boundary | Python for the hot path: per-token overhead on a 16 GB ARM64 machine |
| Desktop and pet | TypeScript | Rust (Tauri) | Not built. Per the handoff's locked decision | Electron: footprint on the target |

The rejections do not remove a language from the architecture. Python is rejected for the model
hot path and owns two other work units; Rust is rejected for the verifiers and owns the state
fabric. That is Rule A and Rule B operating as intended: fourteen globally, the strongest subset
locally.

## 10. Cross-Language Interface Graph

| # | Producer | Consumer | Interface | Contract | Validation | On failure | Verified by |
|---|---|---|---|---|---|---|---|
| 1 | Python `b1_protocol` | Rust `b1-protocol` | canonical bytes over a process boundary | `schemas/b1-event-envelope-v1.schema.json` | byte equality, then digest | FAIL, with both byte strings printed | `verify_cross_language_digest.py` |
| 2 | Either peer | Either peer | SQLite/WAL file + chained digests | `docs/decisions/ADR-0001-root-journal.md` | replay, then head comparison | FAIL, naming every finding | `verify_cross_language_journal.py` |
| 3 | `conformance/vectors/` | 14 consumers | files on disk (`FILE_JSON_V1`) | `contracts/b1-envelope-v1.json` | exact stdout match | non-zero exit, reason on stderr | `verify_polyglot.py` |
| 4 | `provenance/provenance.json` | the tree | `Design-derived-from:` markers | `THIRD_PARTY_NOTICES.md` | bidirectional — manifest to file *and* file to manifest | FAIL, naming the path | `verify_provenance.py` |

Four interfaces, deliberately. The handoff's §12 asks for the interface count to be kept as low
as possible, and the temptation with fourteen languages is fourteen bespoke integrations. Instead
every consumer speaks interface 3, which is files on disk: the lowest-common-denominator transport
that all fourteen support without a dependency.

Interface 4's bidirectionality is the one that does real work. Checking that every manifest entry
has a file is bookkeeping; checking that every file with a derivation marker is in the manifest is
what stops an undeclared copy arriving quietly later.

## 11. Execution Roadmap

### Phase A — Causal substrate (**COMPLETE this pass**)

Objective: canonical identity and one authoritative history, verified rather than asserted.
Deliverables: `crates/b1-protocol`, `crates/b1-state`, `python/b1_protocol`, `python/b1_state`,
`schemas/`, `conformance/vectors/`, `polyglot/envelope_v1/` (14), `tools/` (6 verifiers),
`provenance/`, 5 ADRs.
Languages: Rust + Python (implementation), all 14 (conformance), Python (verifiers).
Exit criteria: all met except the two PARTIAL rows in §4.7.
Authority state: local file creation only. No external effect.

### Phase B — Dual-Core Commit Gate (**NEXT**)

Objective: the handoff's fourth CRITICAL item. Proposal protocol, capability leases, one-time
effect permits, effect-time authority revalidation, permit-digest-bound transition proofs.
Dependencies: Phase A. The write lease in `b1-state` is the seam.
Design reference: ΩΣ13.9's `SovereignWorkRuntime` and the `approvals`/`fences`/`permits` table
shapes in `src/b1mu/work/store.py`. Adapted, not vendored.
Required evidence: concurrent conflicting attempts produce at most one authoritative effect
commit; a stale executor is rejected; an authority envelope that went stale between planning and
effect time is refused at effect time, not at admission.
Exit criteria: mutation tests for each, in both languages, plus cross-language agreement.
Authority state: local only.

### Phase C — Projection topology

Objective: the handoff's compact / modular / audit-replay modes over one logical authority.
Dependencies: Phase B.
Required evidence: destroy every projection and rebuild from the root journal to an identical
digest. `projection_digest` and the `a_projection_can_be_destroyed_and_rebuilt_identically` tests
are the seed; the full three-mode topology is not built.
Exit criteria: identical digests across all three modes.

### Phase D — Responses shim + model provider + resource ledger

Objective: the handoff's Phase 4, re-scoped by ADR-0002. Shim **and** native adapters.
Dependencies: Phase A for event identity. Independent of B and C.
Required evidence: translation conformance vectors covering streaming, tool calls and reasoning
items — asserting on translated bytes, not on "a reply arrived". Then measured RAM, cold load,
warm latency and throughput **on the OmniBook**.
Exit criteria: one 3B/4B-class model loads, answers, unloads, with measured numbers recorded as
machine-specific evidence.
Authority state: **downloading a model is a persistent effect requiring its own authorisation.**

### Phase E — Tournament runtime

Objective: the handoff's approved hybrid specialist + competitor tournament with layered
adjudication.
Dependencies: Phase B (authority), Phase D (models).
Required evidence: a known-invalid candidate cannot win on majority or model preference when
deterministic evidence rejects it.

### Phase F — Security evidence

Objective: per ADR-0005 — adopt Codex Security's schemas and skills, drive them from B1's local
router, treat semantic embeddings as an optional capability that degrades rather than fails.
Required evidence: a finding blocks an unsafe candidate without granting mutation authority.

### Phase G — Desktop, pet, and the private user layer

Objective: Tauri/TypeScript UI, real-state-driven pet overlay, `%B1_HOME%` private layer per
ADR-0003.
Required evidence: pet state is driven by journal events, not by decorative progress; a privacy
scan finds no personal data or key material in the public tree.

## 12. Priority Matrix

Every roadmap task appears exactly once.

| Task | Priority | Reason |
|---|---|---|
| Provenance and licence map | CRITICAL | **Done.** Blocks publication of anything; blocks undeclared copying |
| Canonical event envelope | CRITICAL | **Done.** Every state and effect claim is expressed in it |
| Root authoritative journal | CRITICAL | **Done.** Prevents multiple truths |
| Dual-Core Commit Gate | CRITICAL | **Next.** Without it, equal peers can still produce two histories of one effect |
| Fencing, idempotency, effect identity | CRITICAL | **Partly done.** Journal-level fencing and duplicate-effect rejection exist; effect-time authority revalidation does not |
| Deterministic replay and recovery | HIGH | **Done for the journal.** Not done for work-runtime recovery contracts |
| Responses shim | HIGH | Nothing local answers through a Codex-derived path without it (ADR-0002) |
| Model provider and resource ledger | HIGH | Required before any local-AI feasibility claim is more than a hope |
| Projection topology | HIGH | Required for the handoff's three deployment modes |
| Fourteen-language conformance | HIGH | **Done, 10/14 observed.** U-level invariant |
| Hybrid tournament | HIGH | Defines B1's multi-model behaviour |
| Security evidence integration | HIGH | Consequential code and agent safety |
| Private user layer isolation | HIGH | Public-repository requirement; depends on ADR-0003 |
| Desktop full UI | MEDIUM | User-facing core, but after the backend contracts it renders |
| Pet overlay | MEDIUM | Depends on real runtime state existing to display |
| Executing the four unobserved consumers | MEDIUM | Cheap on a machine with the toolchains; currently the weakest assumption in the pass |
| Upstream `b1mu.toml` regression test | LOW | A real defect, but in the archive rather than here, and it blocks nothing in B1 Local |
| Visual polish | LOW | Non-blocking until functional contracts pass |

## 13. Risk Mitigation

| Risk | Impact | Likelihood | Detection | Mitigation | Recovery | Persistence class |
|---|---|---|---|---|---|---|
| The four unobserved consumers are wrong | Fourteen-language claim is weaker than it reads | Medium | `verify_polyglot.py` on a machine with the toolchains | Status reports UNKNOWN, never PASS; the claim is already scoped to 10/14 | Fix the consumer; no state to unwind | REVERSIBLE |
| Shim drops a tool call in translation | An agent appears to answer while silently doing nothing | Medium-high | Conformance vectors asserting on translated bytes | Not yet mitigated — the shim does not exist | Re-translate; a dropped call has no effect to undo | REVERSIBLE |
| Shim mistranslates a call into a *different* effect | A real action taken that nobody authorised | Low | Effect-time authority revalidation (Phase B) | Phase B before Phase D, or accept the exposure knowingly | Depends entirely on the effect | **UNKNOWN** |
| A float reaches a digested payload | Two peers disagree about history, silently | Low | Rejected by both peers at construction; Python owns the invariant in the polyglot layer | Structural: Rust's `Digestable` has no float variant | Reject and re-emit; nothing persisted | REVERSIBLE |
| Projection drifts from the journal | Derived state contradicts authoritative state | Medium | `projection_digest` comparison against replay | Projections are disposable by construction; on disagreement the projection loses | Rebuild from the journal | REVERSIBLE |
| ΩΣ13.9 is accidentally vendored | Licence violation; publication becomes unlawful | Low | `verify_provenance.py` refuses an undeclared derivation marker, and reports publication BLOCKED | Nothing vendored; bidirectional manifest check | Remove and rewrite history — costly | **IRREVERSIBLE** once published |
| A model download is treated as routine | Disk and bandwidth consumed without authorisation | Medium | Phase D gates it explicitly | Recorded as requiring its own authorisation | Delete the file | REVERSIBLE |
| Durable memory enabled by default | Every conversation becomes an unauthorised persistent effect | Low | ADR-0003 makes ephemeral the default | Policy decided before implementation | Erase the store; decisions already made from it cannot be unwound | **COMPENSATABLE** |

The third row is the one to watch. It is the only entry whose recovery is UNKNOWN, and it argues
for ordering Phase B before Phase D even though they are otherwise independent.

## 14. Resource Requirements

**Established and used this session:** Python 3.11.15 (standard library only — no `numpy`, no
`pytest`), Rust 1.94.1 with `cargo` and network access to crates.io, `serde`/`serde_json`/`sha2`/
`rusqlite` (bundled SQLite, builds from C source), and ten of fourteen language toolchains (§4.8).

**Required but not established:**

| Need | For | Status |
|---|---|---|
| `kotlinc`, `swiftc`, `dotnet`, `dart` | Observing the remaining four consumers | Absent here |
| `numpy`, `pytest` | Re-verifying ΩΣ13.9's 45/45 claim | Absent; installing is a persistent environment change, unauthorised |
| The actual OmniBook 3 | Every performance number in Phase D | Not reachable from here |
| A local model file | Phase D | Downloading is a persistent effect requiring authorisation |
| Windows ARM64 toolchain | Target-platform builds | Codex ships `aarch64-pc-windows-msvc` in `rust-release-windows.yml`, `dotslash-config.json` and `codex-rs/.cargo/config.toml`, so the target is real; B1 has not built for it |
| Rights-holder licence decision on ΩΣ13.9 | Publishing anything derived from it | Not made. Apache-2.0 here covers new B1 code only |

## 15. Decision Points

**Decided this session** (five ADRs): one global root journal with a leased single writer
(ADR-0001, resolves handoff open item 7); Apache-2.0 for new B1 code without relicensing ΩΣ13.9
(ADR-0004); shim plus native adapters (ADR-0002); ephemeral-by-default encrypted-opt-in memory
(ADR-0003, closes handoff open item 6); adopt Codex Security's contracts and skills but not its
orchestrator (ADR-0005).

**Open, and materially affecting later work:**

1. **Phase B before Phase D, or in parallel?** Building the model path before effect-time
   authority revalidation exposes the one risk in §13 whose recovery is UNKNOWN. Recommendation:
   Phase B first. Not yet decided.
2. **Which quantised model is B1's default?** Affects the resource ledger's whole design. The
   handoff's candidate list is sound but unbenchmarked on the target, and `gpt-oss:20b` must be
   overridden explicitly (§4.2).
3. **Does the upstream `b1mu.toml` defect get fixed?** It is the rights holder's archive. The
   finding is recorded; the fix needs a regression test and their say-so.
4. **Is ΩΣ13.9 ever relicensed?** Determines whether a public B1 repository can include anything
   derived from it. Publication is BLOCKED until answered, and that is reported by a verifier
   rather than left in prose.
5. **Does B1 offer a cloud-assisted security path at all?** ADR-0005 permits one as an explicitly
   authorised per-scan effect. Whether to build it is undecided.

## 16. Next Five Actions

Dependency-ordered. Every item producing persistent state outside this repository is labelled.

**Action 1 — Execute the four unobserved consumers.**
Objective: close the weakest assumption in the pass. Input: a machine with `kotlinc`, `swiftc`,
`dotnet`, `dart`. Output: `verify_polyglot.py` reporting 14/14, or named defects. Dependency:
none. Verification: `verify_polyglot.py` and `verify_polyglot_mutations.py` both at 14.
Authority: local only.

**Action 2 — Dual-Core Commit Gate.**
Objective: the fourth CRITICAL item. Input: the write lease in `b1-state`; ΩΣ13.9's
`SovereignWorkRuntime` as design reference. Output: proposal, lease, permit, fence and
effect-time revalidation in both languages. Dependency: Action 1 not required; Phase A complete.
Verification: concurrent conflicting attempts yield at most one commit; a stale executor is
rejected; an authority envelope that went stale between planning and effect time is refused *at
effect time*. Authority: local only.

**Action 3 — Projection rebuild across all three deployment modes.**
Objective: prove projections are disposable in compact, modular and audit-replay layouts. Input:
Action 2. Output: three layouts, one logical authority. Verification: destroy every projection,
rebuild, compare digests. Authority: local only.

**Action 4 — Responses shim conformance vectors, before any shim code.**
Objective: define what the translation must preserve before writing it. Input: Codex's Responses
API surface; a chat-completions server's surface. Output: vectors for streaming, tool calls and
reasoning items, asserting on translated bytes. Dependency: none. Verification: vectors exist and
fail against a deliberately lossy translator. Authority: local only.

**Action 5 — OmniBook model benchmark.**
Objective: replace every UNKNOWN in §6 item 1 with measurement. Input: Action 4's shim; a chosen
quantised model. Output: measured RAM, cold load, warm latency, throughput, unload/reload.
Verification: a repeatable local benchmark artifact, recorded as machine-specific evidence.
Authority: **`PLAN_READY` — AUTHORIZATION REQUIRED.** Downloading a model file is a persistent
effect.

## 17. Verification Strategy

Six verifiers, and what would falsify each:

| Verifier | Establishes | Falsified by |
|---|---|---|
| `verify_provenance.py` | The provenance record is complete in both directions | A file with a derivation marker absent from the manifest, or a manifest entry whose marker disagrees |
| `verify_cross_language_digest.py` | Two independent implementations agree on canonical bytes | One byte of difference, printed as both strings |
| `verify_cross_language_journal.py` | Both peers derive identical history | Any of seven compared fields differing |
| `verify_polyglot.py` | Fourteen toolchains independently check fourteen invariants | A consumer whose output differs from its declared postcondition |
| `verify_polyglot_mutations.py` | Those checks bite | A consumer passing against a vector violating its own invariant — reported as `DECORATIVE` |
| `verify_all.py` | All of the above, with unobserved things visibly unobserved | Any FAIL; PARTIAL when something went unobserved |

Three properties of this strategy are worth stating because they are what makes it evidence
rather than decoration.

**Independence is structural, not promised.** The cross-language verifiers run the Rust peer as a
separate process and compare bytes. Two implementations linked into one runtime can share a bug;
two processes exchanging bytes cannot share one silently. The Rust polyglot consumer is compiled
standalone with `rustc` and shares no code with `crates/b1-protocol` — a consumer that imported
the thing under test would prove only that code equals itself.

**Every verifier has been shown a broken input.** A verifier that has only ever seen correct data
is untested. Seven journal mutations, fourteen polyglot mutations, and a rejection vector for the
canonical form all exist to make each verifier demonstrate refusal.

**Refusal reasons are read, not just exit codes.** This caught two real defects in this pass
(§4.7) that a green run concealed. A test that passes for the wrong reason is worse than a failing
one, because it looks like evidence.

The limitation, stated plainly: three vectors and fourteen mutations are evidence about those
inputs. They are not a proof over all inputs, and no claim here should be read as one.

## 18. Authority and Persistence State

**Current state: `PLAN_READY` for everything outside this repository.**

Persistent effects taken this session, all local and all within the approved scope: files created
in `/home/user/b1u.agenetic.`, and four commits on `claude/b1-local-omega13-handoff-auv8bc`.
Classification: REVERSIBLE (git history, local).

**Not authorised, and not done:**

- Creating a public GitHub repository, publishing, or opening a pull request.
- Relicensing ΩΣ13.9. It stays all-rights-reserved; `verify_provenance.py` reports publication
  BLOCKED, and will keep reporting it until the rights holder decides.
- Downloading, installing or running any model.
- `pip install numpy pytest` to re-verify ΩΣ13.9's suite — a persistent environment change.
- Editing the ΩΣ13.9 archive, including the `b1mu.toml` defect in §4.6.
- Any external write, transmission, account change or configuration change.

Nothing in this repository claims `VERIFIED` for anything unmeasured in this session. The handoff
assertions that could not be re-run are marked as supplied evidence in §5, and `verify_all.py`
reports PARTIAL rather than PASS where observation was incomplete.

## 19. Recovery Strategy

| Failure | Detection | Containment | Recovery | Residual |
|---|---|---|---|---|
| Journal record altered | `verify()` recomputes the chain | Append refuses on an invalid envelope, so damage cannot be extended validly | Replay from `GENESIS`; every altered record is named | Altered records are identified, not restored — the journal proves damage occurred, it does not undo it |
| Journal tail truncated | Committed record count disagrees with rows | Same | Same | Truncated records are gone. This is why the count is committed separately: the loss is *visible* |
| Projection drifts | `projection_digest` vs replay | Projections are never authoritative | Rebuild from the journal | None; projections are disposable by construction |
| Two peers disagree on canonical bytes | `verify_cross_language_digest.py` | Neither peer is authoritative over the other | Fix the divergent implementation; re-run every vector | Any history written during the divergence is IN_DOUBT and must be re-verified, not assumed |
| A consumer's check does not bite | `verify_polyglot_mutations.py` reports DECORATIVE | That language's participation claim is withdrawn, not downgraded | Fix the consumer | The invariant was unchecked for as long as the defect existed |
| An unobserved consumer proves broken | Running it | The claim was already scoped to 10/14 | Fix and re-run | None — this is why the status was UNKNOWN rather than PASS |

The recovery limit worth stating explicitly: **a hash chain proves that damage happened. It does
not repair it.** Detection and recovery are different capabilities, and B1 currently has the
first. Restoring a damaged journal needs a replica or an external anchor, and neither exists. A
design that conflated the two would promise a repair it cannot perform.

Recovery actions are themselves persistent effects and carry the ordinary authority requirements.

## 20. Convergence Findings

**Baseline:** the B1 Local handoff of 2026-09-15, plus the three archives as supplied.
**Candidate:** this repository, plus the five ADRs and this document.

**Material defects found in the baseline, with what each cost:**

| # | Defect | Evidence | Retained correction |
|---|---|---|---|
| 1 | llama.cpp named primary runtime; provider layer assumed free | `model-provider-info/src/lib.rs:57-92,507-537` — one `WireApi` variant, `chat` a hard error | ADR-0002: shim + native adapters; translation is real work |
| 2 | Codex Security assumed local | `runtime.ts:2373`, `embeddings.ts:82`, README | ADR-0005: adopt schemas and skills, not the orchestrator |
| 3 | Phase 9 (fourteen languages) treated as greenfield | `polyglot/quantum_fidelity/` + verifier exist | Ported and upgraded into Phase A, not deferred |
| 4 | Phases 1-3 treated as greenfield | `durable.py`, `work/store.py`, `work/runtime.py` | Adapted the proven design; one deliberate divergence in ADR-0001 |
| 5 | `b1mu.toml` left IN_DOUBT | `test_omega13_9_release_contract.py` never asserts it | §4.6: resolved as stale, not protected |
| 6 | Open item 7 (who owns the writer) unanswered | handoff §20 | ADR-0001: nobody does; the database leases it |
| 7 | Open item 6 (memory policy) unanswered | handoff §20 | ADR-0003: ephemeral default, encrypted opt-in |
| 8 | Canonical JSON assumed sufficient for cross-language digests | Python `repr()` vs Rust Ryu float rendering | Floats and nulls rejected structurally; `Digestable` has no float variant |
| 9 | Inherited polyglot verifier's declared commands were decorative | `_commands()` bypasses `build_command`/`run_command` | B1 executes the declared templates, constrained |
| 10 | Inherited verifier exempted `build_command` from its own check | `if field != "build_command" and ...` | Exemption removed |
| 11 | Inherited verifier targeted legacy `csc`/`mono` | its `_commands()` table | `dotnet` |
| 12 | `gpt-oss:20b` is the built-in default, not merely an option | `ollama/src/lib.rs:16`, `lmstudio/src/lib.rs:7` | B1 must override explicitly |
| 13 | Passing against correct data treated as participation proof | the inherited harness has no mutation suite | `verify_polyglot_mutations.py` |
| 14 | Own defect: Ruby mutation produced malformed JSON | refusal reason read, not exit code | `drop_field` keeps JSON valid; invariant actually tested |
| 15 | Own defect: JVM banner masked Java's refusal reason | same | `strip_noise` in `verify_polyglot.py` |

**Changes rejected during the pass, and why** — these matter as much as the retained ones:

- **Hand-rolled SHA-256 in all five NOT_IN_STDLIB languages.** Rejected. It would have made the
  fourteen-language claim uniform and stronger-sounding, but two of the five (Swift, Dart) cannot
  be executed here, so it would have meant shipping untested cryptographic code and *claiming* it
  verified digests. An honestly declared gap is better evidence than an unexecutable
  implementation. This is the clearest case in the pass of rejecting a change that would have made
  the output look better while making the evidence worse.
- **Generating the fourteen consumers from one template.** Rejected. Identical logic in fourteen
  syntaxes is one implementation wearing fourteen costumes, and it would prove nothing about
  independence.
- **Rolling the four UNKNOWN languages into a clean PASS at the `verify_all.py` altitude.**
  Rejected; `PARTIAL` was added instead. A reader who stops at the summary line must not be told
  that something was checked when it was not.
- **Fixing the upstream `b1mu.toml` defect.** Rejected as out of scope. It is the rights holder's
  archive, the fix needs a regression test, and the finding is recorded for their decision.

**Regression check against the baseline:** all U-level invariants preserved — Origin ≠ Authority ≠
Executor ≠ Effect, Receipt ≠ Effect ≠ Postcondition, PLAN_READY ≠ EXECUTION_AUTHORIZED, fourteen
global ≠ fourteen local. No working behaviour was replaced; nothing upstream was modified. The
locked decisions in the handoff's §27 all survive, three of them now with running code behind
them and two (memory policy, writer ownership) now answered.

**Stop reason:** further iteration on this increment would add components without adding evidence.
The four unobserved consumers need a different machine, not more design. The Dual-Core Commit
Gate is the next dependency-correct step and is a phase of its own, not a refinement of this one.
Continuing to elaborate Phase A would be recursion mistaken for improvement.

**Status:** `CONVERGED_FOR_CURRENT_OBJECTIVE_AND_EVIDENCE`

Scoped precisely: converged for the causal substrate, on this machine, with these toolchains,
against this evidence. Not converged for B1 Local, which has six phases remaining. Two verifier
rows report PARTIAL, and that is a limitation of the environment rather than of the work — stated
separately, as the contract's §39 requires, rather than folded into the convergence claim.

## 21. Executive Plan

**Strategic goal.** A verifiable causal substrate for B1 Local, so later phases rest on state
whose integrity is checked by running code.

**Current verified state.** Canonical Event Envelope v1 and a global hash-chained root journal on
SQLite/WAL, implemented independently in Rust and Python, with the two agreeing byte-for-byte
across a process boundary on three vectors and on seven history fields. 88 tests (48 Python, 40
Rust). Seven journal mutation cases detected in both languages. Fourteen language consumers, ten
observed to verify their invariant and ten observed to refuse when it is broken. A provenance
record whose verifier refuses undeclared derivations in both directions and reports publication
BLOCKED.

**Critical constraints.** ΩΣ13.9 is all-rights-reserved and is not vendored; Apache-2.0 here
covers new B1 code only. Codex speaks only the Responses API, so llama.cpp needs a shim. Codex
Security is not local; only its schemas and skills are portable. The target is 16 GB Windows
ARM64, so `gpt-oss:20b` — Codex's built-in default — must be overridden.

**Major dependencies.** Phase B (Dual-Core Commit Gate) depends only on Phase A and is next.
Phase D (models) depends on the shim, which depends on conformance vectors that do not exist yet.
Phase D also depends on hardware not reachable from here.

**Important unknowns.** Every OmniBook performance number. Whether the four absent-toolchain
consumers execute correctly. Whether ΩΣ13.9's full suite passes. What the shim's streaming and
tool-call translation must cover. Whether the Hexagon NPU is reachable — IN_DOUBT, not UNKNOWN,
because vendor claims exist without runtime proof.

**Major risks.** In order: a shim that mistranslates a call into a *different* effect, whose
recovery is UNKNOWN and which argues for Phase B before Phase D; accidental vendoring of ΩΣ13.9,
which becomes IRREVERSIBLE once published; and projection drift, which is mitigated by
construction.

**Next actions.** (1) Execute the four unobserved consumers on a machine with their toolchains.
(2) Build the Dual-Core Commit Gate. (3) Prove projection rebuild across three deployment modes.
(4) Write shim conformance vectors before shim code. (5) Benchmark on the OmniBook —
`PLAN_READY`, authorisation required.

**Authority state.** `PLAN_READY`. Local file creation and four commits to the designated branch
only. No repository creation, no publication, no relicensing, no model download, no external
write, no environment change.

**Verification requirements.** `python3 tools/verify_all.py` from the repository root. Standard
library only; no `pip install`. Exit 0 means everything was checked and holds; 1 means something
failed; 2 means nothing failed but something was not observed. It currently exits 2, and §4.7
says exactly which two rows and why.
