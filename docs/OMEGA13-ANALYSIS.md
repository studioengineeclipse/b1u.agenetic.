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
| 8 | No persistent effect without live authority; concurrent attempts commit at most one | `tools/verify_cross_language_gate.py`; 38 gate tests | **Met 2026-09-17.** A Rust process and a Python process race: 1 permit, 1 effect, heads AGREED |

Criterion 5 and 6 are recorded as *partially* met on purpose. Ten of fourteen is what was
observed. Reporting it as met would be the precise substitution of "the harness behaved" for "the
property holds" that the contract's §3 forbids.

## 3. Epistemic State

| Category | Count | Notes |
|---|---|---|
| VERIFIED | 7 success criteria as scored above; 18 findings in §4 | Each cites a path, a line, or a verifier run in this session |
| WORKING_ASSUMPTION | 3 | Listed in §5 |
| UNKNOWN | 4 | Listed in §6 |
| IN_DOUBT | 2 | Listed in §6 |

**Upgraded on 2026-09-17.** The first pass recorded the handoff's *"45/45 focused tests passed"*
claim as WORKING_ASSUMPTION because `numpy` and `pytest` were absent and installing them is a
persistent environment change. With that install authorised, the archive's suite was run: **646
tests passed, 0 failed, in 67 seconds**, and all fourteen of its own verifiers exit 0. That is a
stronger result than the claim it replaces, and it also closes the handoff's own note that an
earlier full run *"progressed substantially but timed out before completion"* with its remainder
left UNKNOWN. §4.9 records it.

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

In this container, on 2026-09-18:

```
$ python3 tools/verify_all.py

  provenance record                       PASS     12 derived files declared; publication BLOCKED
  python unit tests                       PASS     Ran 156 tests
  rust unit and vector tests              PASS     54 tests passed
  rust/python canonical bytes agree       PASS     3/3 vectors AGREED
  rust/python journal history agrees      PASS     7/7 fields AGREED after 4 events
  rust/python gate commits one effect     PASS     1 permit, 1 effect, losers REFUSED/PERMIT_SPENT, heads AGREED
  three deployment modes, one truth       PASS     3 modes AGREED, 5/5 rust/python AGREED
  fourteen-language participation         PARTIAL  10 POSTCONDITION_VERIFIED, 4 UNKNOWN of 14 (unknown: C#, Dart, Kotlin, Swift)
  fourteen-language checks actually bite  PARTIAL  10 REFUSED, 4 UNKNOWN of 14
  tournament runs end to end              PARTIAL  verdict=ACCEPT; no model server here, see docs/RUNNING.md
  one task travels the whole path         PASS     4 runs: 1 writes, 3 refuse correctly

  3 check(s) PARTIAL: nothing wrong was found, but not everything was observed on this machine.
```

Exit code 2 — nothing failed, not everything was observed. This block is checked against the
tool's real output by `tools/verify_readme_transcript.py`, which is how a measured-status section
stops being a thing someone has to remember to update. The earlier version of this section carried
Phase A's figures (7 derived files, 48 Python tests, no gate or tournament rows) for a full day
after they stopped being true.

Two defects in this repository's own work were found by reading refusal *reasons* rather than
exit codes, and both are worth recording because they are the failure mode the contract's §3
describes: the Ruby mutation produced malformed JSON, so Ruby refused at its JSON parse step with
its actual invariant untested; and the JVM's `JAVA_TOOL_OPTIONS` banner was masking Java's real
message inside truncated failure reports. A green run concealed both.

### 4.8 Toolchain availability in this container

Present (10): `cc`, `c++`, `javac`/`java`, `python3`, `node`, `go`, `rustc`/`cargo`, `tsc`, `php`,
`ruby`. Absent (4): `kotlinc`, `swiftc`, `dotnet`, `dart`. The OmniBook's availability will
differ; the harness reports what it measures rather than what it hopes.

### 4.9 ΩΣ13.9 re-verified — supersedes the handoff's test claim

Measured 2026-09-17 with `numpy` 2.4.6 and `pytest` 9.1.1 installed against the archive's declared
requirement of `numpy>=2.0,<3`.

**Full test suite: 646 passed, 0 failed, 67.28s.** The handoff reported 45 focused tests passing
and noted that an earlier *full* run timed out before completion, correctly leaving the remainder
UNKNOWN. That remainder is now measured. The archive is in better shape than its own handoff
claimed.

**All fourteen of the archive's verifiers exit 0.** On first attempt four appeared to fail; that
was an invocation error on my part, not a defect — `verify_omega13_release.py` and
`verify_omega13_autonomous_upgrade.py` take a positional root rather than `--root`,
`verify_skill_runtime.py` accepts only `--structural-only`, and `verify_omega12.py` requires
`--baseline` and `--json`. Worth recording because "four verifiers fail" is exactly the kind of
finding that would have been wrong and damaging to report.

| Verifier | Result |
|---|---|
| `verify_omega13_release.py` | 1997 checks, 0 failures |
| `verify_omega13_autonomous_upgrade.py` | 52 checks, 0 failures, baseline 1997 |
| `verify_omega13_9_polyglot.py` | 10 POSTCONDITION_VERIFIED, 4 UNKNOWN, 0 errors |
| `verify_skill_runtime.py` | 14 superpowers, 28 workflows |
| 9 others (13.5 integrity, 13.6, 13.7, 13.8, 13.9 quantum-fidelity, autonomy, qx, spectral, work extension) | exit 0 |
| `verify_omega12.py` | 1092/1099; 7 reported failures — analysed below |

Upstream's polyglot verifier independently reports **the same 10/4 split** this repository's
harness reports, which is a useful cross-check that B1's port did not change the answer while
changing the mechanism.

**The seven `verify_omega12.py` failures are not regressions.** That verifier targets the
superseded ΩΣ12 generation. Five failures are it correctly observing that the tree has moved on:
`language version 2.2 vs 1.0`, `ir 7 vs 1`, `proof 9 vs 1`, `adapter abi 4 vs 1`,
`omega generation 13 vs 12`.

The remaining two are more interesting, and they are a *check* defect rather than a *substance*
defect. Lines 285-286 assert on exact prose:

```python
v.check("README physical-quantum honesty", "does not claim physical quantum computation" in readme)
v.check("README capability-authority boundary", "Capability does not imply authority" in readme)
```

Neither literal string is in the 13.9 README. Both guarantees are, reworded and arguably
strengthened: line 125 reads *"Physical quantum execution requires observed backend evidence,
operation-bound capability, freshness, an execution receipt, and objective postcondition evidence.
A provider receipt alone is insufficient"*; line 88 reads *"skill invocation != authority"*, line
186 *"never grants capability authority or mutation budget"*, and line 27 carries
`CAPABILITY != AUTHORITY` in the separation chain.

So the guarantee survived the rewrite and the check did not. This is the same failure mode as the
`b1mu.toml` finding in §4.6: the archive's *checks* have drifted from the archive's *substance* in
two independent places. A check that greps for prose is brittle by construction, and a check
asserting a property should assert the property.

One further observation: `verify_omega12.py` reports `failed=7` and still **exits 0**. For a
reporting tool aimed at a superseded generation that may well be deliberate, but it means a reader
or a CI job consulting only the exit code learns nothing about those seven.

## 5. Working Assumptions

1. **UTF-8 byte order equals Unicode code-point order, so Python `sort_keys` and Rust `BTreeMap`
   agree on key order.** This is true by UTF-8's construction, and `envelope-002-unicode-keys`
   exercises Latin-1, Greek, Han and a non-BMP character to hold it up. It is listed here rather
   than under VERIFIED because three vectors are evidence, not a proof over all inputs.
2. **The four unobserved languages' consumers are correct.** Kotlin, Swift, C# and Dart sources are
   written and reviewed but never executed. This is the weakest assumption in the pass and the one
   most likely to be wrong; see §13 for the risk and §19 for the recovery.
3. **`rusqlite` with `bundled` will build on Windows ARM64.** It builds here on x86-64 Linux and
   compiles SQLite from C source, so it needs only a working C toolchain — but that is an
   inference from how it works, not an observation on the target.

*Removed 2026-09-17:* "ΩΣ13.9's 45/45 claim holds" was assumption 1 in the first pass. It is now
VERIFIED and exceeded — 646/646 — per §4.9.

## 6. UNKNOWN / IN_DOUBT

**UNKNOWN — the information is not available from here.**

1. **Any OmniBook performance number.** Throughput, latency, cold-load time, RAM residency and
   thermal behaviour for any model on the Snapdragon X X1-26-100. Nothing in this session ran on
   that machine. The handoff correctly declines to invent these and so does this pass.
2. **Whether the four absent-toolchain consumers execute correctly.** Not a code-quality
   statement; a statement that nobody has run them.
3. **What a chat-completions → Responses translation must cover for streaming, tool calls and
   reasoning items.** The shim is `PLAN_READY`; its hard cases have not been mapped.
4. **Whether any B1 workload benefits from the Hexagon NPU.** See IN_DOUBT 2 for the prior
   question.

*Resolved 2026-09-17:* "Whether ΩΣ13.9's full test suite passes" was UNKNOWN 3 in the first pass.
It passes: 646/646, per §4.9.

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
        |                                                 |
                    BUILT                            NOT STARTED
        |                                                 |
  b1-protocol                                     model router + shim
   canonical form                                 tournament runtime
   Event Envelope v1                              security evidence
        |                                         Tauri desktop
  b1-state                                        pet overlay
   root journal, SQLite/WAL                       B1_HOME private layer
   chain + head commitment                        projection topology
        |                                         capability policy
  b1-authority
   authority envelopes
   one-time fenced permits
   effect-time revalidation (x2)
   unknown-outcome reconciliation
        |
  polyglot/envelope_v1
   14 consumers, 14 unique invariants
        |
  provenance + licence
   manifest + bidirectional verifier
```

The layering that matters, and the order it has to hold in:

```
canonical bytes -> envelope identity -> chained record -> authoritative history -> authorized effect
    (agreed)          (validated)        (verified)          (replayable)            (gated)
```

Each arrow is a place where two implementations could disagree, and each has a verifier. Nothing
above this line can be trusted further than the line itself, which is why the line was built
first, left to right.

The last arrow is the one that turns the substrate into something an agent can safely act
through. Everything to its left establishes what is true; the gate establishes what is *allowed*,
and keeps the two apart — a security finding or a model's confidence can constrain what B1
claims, never what B1 may do.

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
| 5 | Either peer | The commit gate | one SQLite write lease on one file | `docs/decisions/ADR-0006-commit-gate.md` | authority digest agreement, then permit and effect counts | refusal carries a `kind()`, so losing by the rule is distinguishable from losing to a lock | `verify_cross_language_gate.py` |

Five interfaces, deliberately. The handoff's §12 asks for the interface count to be kept as low
as possible, and the temptation with fourteen languages is fourteen bespoke integrations. Instead
every consumer speaks interface 3, which is files on disk: the lowest-common-denominator transport
that all fourteen support without a dependency.

Interface 5 is the only one where the two peers are not merely required to *agree* but required to
*contend*, and it is the only one whose failure mode is a duplicated real-world action rather than
a mismatched digest. That is why its verifier asserts on the refusal's discriminant: a gate that
happened to work because SQLite returned `SQLITE_BUSY` would pass a count-based check while
resting on an implementation detail.

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

### Phase B — Dual-Core Commit Gate (**COMPLETE 2026-09-17**)

Objective: the handoff's fourth CRITICAL item. Proposal protocol, one-time effect permits,
effect-time authority revalidation, permit-digest-bound transition proofs.
Deliverables: `python/b1_authority`, `crates/b1-authority`, `tools/verify_cross_language_gate.py`,
[ADR-0006](decisions/ADR-0006-commit-gate.md).
Design reference: ΩΣ13.9's `SovereignWorkRuntime` and the `approvals`/`fences`/`permits` table
shapes in `src/b1mu/work/store.py`. Adapted, not vendored; declared in `provenance.json`.

Exit criterion met, at two levels. Two Python connections race: exactly one permit, exactly one
effect, loser refused by the gate's own rule. Then a **Rust process races a Python process** on
one SQLite file: both peers compute the same authority digest on bytes, exactly one permit issues
(`REFUSED`), exactly one effect commits (`PERMIT_SPENT`), one effect record reaches history, and
both peers agree on the final head.

Two additions beyond upstream, both recorded in ADR-0006. Authority is revalidated a **second**
time at consume time, so an authorization that goes stale while the executor works is refused
before the effect lands — the handoff's §1.3 requires effect-time checking, and a single
claim-time check implements only half of it. And an `UNKNOWN` receipt flags the effect identity
for reconciliation, refusing any further permit until real state is read back with cited evidence.

Authority state: local only.

### Phase C — Projection topology (**COMPLETE 2026-09-18**)

Objective: the handoff's compact / modular / audit-replay modes over one logical authority.
Dependencies: Phase B.
Deliverables: `python/b1_projection`, `crates/b1-state/src/projection.rs`,
`tools/verify_cross_language_projection.py`,
[ADR-0007](decisions/ADR-0007-projection-topology.md).

Exit criteria: identical digests across all three modes. **Met** — and met twice over, because
the same report also compares the two language peers. Either claim alone can be passed by a
system wrong in the other direction: three modes can agree perfectly inside one implementation
that disagrees with its peer, and two peers can agree on a projection a third mode would have
stored differently.

The load-bearing decision is that `build_views(records)` is a pure function — it consults no
clock, no filesystem, and not which mode is in use. That is what makes the modes *provably*
equivalent rather than separately tested into agreement: one derivation, three destinations.

Four views: `events` (one row per record), `effects` (one per effect identity, carrying the
*latest* status, because an effect can land IN_DOUBT and later reconcile to VERIFIED), `authority`
(what was actually done under each authorization), and `heads`. `heads` carries `chain_head` and
`replayed_head` separately so that a projection built over a damaged journal reports the damage
instead of laundering it into a clean summary.

Audit-replay stores nothing and is the honest baseline: it *cannot* drift. Its `DriftReport` says
"drift is not possible rather than not found", which is the same distinction §3 draws between an
absent observation and a negative one.

A stored projection's own digest is not the check. `check()` compares against a rebuild from the
journal, never against the file's claim about itself — the journal's `payload_digest` lesson
applied to derived state, and tested by damaging a compact projection *and repairing its
self-declared digests*.

### Phase D — Responses shim + model provider + resource ledger (**PARTIAL 2026-09-18**)

Objective: the handoff's Phase 4, re-scoped by ADR-0002. Shim **and** native adapters.
Dependencies: Phase A for event identity. Independent of B and C.

Built: `python/b1_models` — the provider abstraction, native Ollama and LM Studio adapters (both
already speak `/v1/responses`, so neither needs the shim), the model registry, and the resource
ledger that hot-loads and unloads against a 16 GiB budget rather than assuming one resident model
per logical agent. `tools/benchmark_model.py` is written and **has never been run against a
model**, because none is installed here and installing one is a persistent effect.

Not built: the chat-completions→Responses shim llama.cpp needs, and its conformance vectors.

Required evidence, still outstanding: translation conformance vectors covering streaming, tool
calls and reasoning items — asserting on translated bytes, not on "a reply arrived". Then measured
RAM, cold load, warm latency and throughput **on the OmniBook**. Every byte and millisecond figure
in `registry.py` is a declared estimate carrying `WORKING_ASSUMPTION`, and `benchmark_model.py`
refuses to fill a gap it could not measure — a benchmark that substituted a plausible number would
be worse than none, because the output looks measured either way.

Exit criteria: one 3B/4B-class model loads, answers, unloads, with measured numbers recorded as
machine-specific evidence. **Not met.**
Authority state: **downloading a model is a persistent effect requiring its own authorisation.**

### Phase E — Tournament runtime and the work vertical (**COMPLETE 2026-09-18**)

Objective: the handoff's approved hybrid specialist + competitor tournament with layered
adjudication, and the runner that joins it to the gate.
Dependencies: Phase B (authority), Phase D (models).
Deliverables: `python/b1_tournament`, `python/b1_work`, `tools/run_tournament.py`,
`tools/run_agent.py`.

Required evidence: a known-invalid candidate cannot win on majority or model preference when
deterministic evidence rejects it. **Met** — `run_agent.py --approve --bad-answer` puts an
overclaiming answer in, a deterministic verifier eliminates it, and the verdict is `REJECT` with
nothing written. Model synthesis chooses only among survivors; it never reinstates one.

Adjudication is graded, not scored. `Adjudication.epistemic_status` returns `VERIFIED` only when
`deterministic_evidence` is non-empty — refs from conclusive, *passing* verifiers. An earlier
draft graded on `evidence_refs`, which includes model completions, so a unanimous room of models
would have read as `VERIFIED`. Agreement is not evidence, and the two fields exist to keep that
distinction structural rather than remembered.

The vertical runs end to end: propose → tournament → adjudicate → authority → permit → execute →
read back → consume → journal. `python/b1_work/effects.py` is the only code in B1 that touches the
world. Four demo paths are checked by `verify_all.py`: one writes, three refuse — unapproved,
stale authority, and failed verification — and the three refusals matter more than the one write,
because a runner that wrote unconditionally would pass the first.

Authority is re-observed **immediately before the effect**, and that observation, not the one
captured at grant time, is what `consume` revalidates against. The first implementation passed the
grant-time digest, which always matches, so the check was vacuous while reading as enforced.

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
| Dual-Core Commit Gate | CRITICAL | **Done.** A Rust process and a Python process race through it and exactly one effect commits |
| Fencing, idempotency, effect identity | CRITICAL | **Done.** Journal-level fencing, per-domain gate fences, one-time permits, and authority revalidated at both claim and consume time |
| Deterministic replay and recovery | HIGH | **Done for the journal.** Not done for work-runtime recovery contracts |
| Responses shim | HIGH | Only llama.cpp needs it: Ollama and LM Studio already speak `/v1/responses` (ADR-0002) |
| Model provider and resource ledger | HIGH | **Done, unmeasured.** Adapters, registry and ledger exist; every size and latency figure is still a declared estimate |
| Projection topology | HIGH | **Done.** Three modes produce one digest, and the Rust peer derives the same four views |
| Fourteen-language conformance | HIGH | **Done, 10/14 observed.** U-level invariant |
| Hybrid tournament | HIGH | **Done.** Deterministic evidence eliminates before models choose, and a rejected candidate cannot be reinstated by agreement |
| End-to-end work runner | HIGH | **Done.** One task to a verified effect, with the three refusal paths checked alongside the one write |
| OmniBook model benchmark | HIGH | Harness written, never run. The only thing that turns §6's UNKNOWNs into measurement, and it needs a model download — a persistent effect |
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
| ~~`numpy`, `pytest`~~ | ~~Re-verifying ΩΣ13.9's claim~~ | **Established 2026-09-17** — installed under explicit authorisation; §4.9 records the result. Not a dependency of B1 Local itself, which remains standard-library-only |
| The actual OmniBook 3 | Every performance number in Phase D | Not reachable from here |
| A local model file | Phase D | Downloading is a persistent effect requiring authorisation |
| Windows ARM64 toolchain | Target-platform builds | Codex ships `aarch64-pc-windows-msvc` in `rust-release-windows.yml`, `dotslash-config.json` and `codex-rs/.cargo/config.toml`, so the target is real; B1 has not built for it |
| Rights-holder licence decision on ΩΣ13.9 | Publishing anything derived from it | Not made. Apache-2.0 here covers new B1 code only |

## 15. Decision Points

**Decided this session** (seven ADRs): one global root journal with a leased single writer
(ADR-0001, resolves handoff open item 7); Apache-2.0 for new B1 code without relicensing ΩΣ13.9
(ADR-0004); shim plus native adapters (ADR-0002); ephemeral-by-default encrypted-opt-in memory
(ADR-0003, closes handoff open item 6); adopt Codex Security's contracts and skills but not its
orchestrator (ADR-0005); the Dual-Core Commit Gate with authority revalidated at both claim and
consume time (ADR-0006); three projection deployment modes over one pure derivation, with a
projection write classified REVERSIBLE and confined to the workspace (ADR-0007).

**Open, and materially affecting later work:**

1. ~~**Phase B before Phase D, or in parallel?**~~ **Decided by doing it: Phase B first.** The gate
   landed on 2026-09-17 and the model layer on 2026-09-18, so §13's one UNKNOWN-recovery risk — a
   shim mistranslating a call into a *different* effect — was mitigated by effect-time
   revalidation before any model path existed to expose it. Recorded here rather than deleted,
   because the ordering was a real choice and a document that quietly drops its own open questions
   teaches nothing about how they were settled.
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

**Action 2 — ~~Dual-Core Commit Gate~~. DONE 2026-09-17.**
See Phase B above and [ADR-0006](decisions/ADR-0006-commit-gate.md). Verification landed stronger
than planned: not only do concurrent conflicting attempts yield at most one commit and a stale
executor get rejected, but the race is run **between a Rust process and a Python process** on one
database, and the loser is required to lose by the gate's own rule rather than by a database lock.

**Action 3 — ~~Projection rebuild across all three deployment modes~~. DONE 2026-09-18.**
See Phase C above and [ADR-0007](decisions/ADR-0007-projection-topology.md). Verification landed
wider than planned: the same report that proves the three modes agree also proves the Rust and
Python peers derive byte-identical views, per view rather than only in aggregate. Shown to bite —
perturbing one integer in the Rust `authority` view produces `MISMATCH` on that view and on
`views_digest`, and on nothing else.

**Action 4 — Capability policy above per-effect authorization. NEXT.**
Objective: the layer ADR-0006 explicitly does not cover. The gate enforces *an* authorization; it
does not yet know which actions a workspace permits at all. Input: ΩΣ13.9's `AuthorityPolicy` and
`workspaces.policy_json` as design reference. Output: a policy checked before an authority can
even be granted. Verification: a capability the policy denies cannot be granted, let alone
claimed. Authority: local only.

**Action 5 — Responses shim conformance vectors, before any shim code.**
Objective: define what the translation must preserve before writing it. Input: Codex's Responses
API surface; a chat-completions server's surface. Output: vectors for streaming, tool calls and
reasoning items, asserting on translated bytes. Dependency: none. Verification: vectors exist and
fail against a deliberately lossy translator. Authority: local only.

**Deferred — OmniBook model benchmark.** Still the only way to replace §6 item 1's UNKNOWNs with
measurement, and still **`PLAN_READY` — AUTHORIZATION REQUIRED**: downloading a model file is a
persistent effect, and the hardware is not reachable from here. It drops out of the top five
because Action 4 is now the cheaper dependency-correct step, and because §13's highest-risk row
(a shim mistranslating a call into a *different* effect, recovery UNKNOWN) is mitigated now that
effect-time revalidation exists.

What changed on 2026-09-18: the harness for that benchmark now exists —
`tools/benchmark_model.py`, one command on the OmniBook — so the deferred item is no longer
"write a benchmark" but "authorise a download and run one". The tool reports what it measured and
marks what it could not, including refusing to synthesise a resident-memory figure the provider
did not give it. Nothing in it has been run against a model, and no number it would produce
appears anywhere in this repository.

Also landed outside the five: `python/b1_models`, `python/b1_tournament` and `python/b1_work`
(Phase D partial, Phase E complete), plus `tools/verify_readme_transcript.py`, which was not
planned at all — it exists because this README had drifted into paraphrasing its own verifier, and
a drift that already happened once is not a hypothetical worth trusting to care.

## 17. Verification Strategy

Nine verifiers, and what would falsify each:

| Verifier | Establishes | Falsified by |
|---|---|---|
| `verify_provenance.py` | The provenance record is complete in both directions | A file with a derivation marker absent from the manifest, or a manifest entry whose marker disagrees |
| `verify_cross_language_digest.py` | Two independent implementations agree on canonical bytes | One byte of difference, printed as both strings |
| `verify_cross_language_journal.py` | Both peers derive identical history | Any of seven compared fields differing |
| `verify_cross_language_gate.py` | A Rust process and a Python process race one gate and exactly one effect commits | Two permits, two effect records, disagreeing heads, or a loser that lost to `SQLITE_BUSY` rather than to the gate's own rule |
| `verify_cross_language_projection.py` | Three deployment modes derive one digest, and both peers derive the same four views | Two modes disagreeing; a file surviving `destroy()`; a mode whose read-back differs from what it wrote; either peer's view digest differing |
| `verify_polyglot.py` | Fourteen toolchains independently check fourteen invariants | A consumer whose output differs from its declared postcondition |
| `verify_polyglot_mutations.py` | Those checks bite | A consumer passing against a vector violating its own invariant — reported as `DECORATIVE` |
| `verify_readme_transcript.py` | README.md quotes the verifier rather than paraphrasing it | A transcript line the tool does not print; a named check that does not exist; a check the transcript omits |
| `verify_all.py` | All of the above, with unobserved things visibly unobserved | Any FAIL; PARTIAL when something went unobserved |

`verify_readme_transcript.py` is the newest and the narrowest, and it exists because the defect it
checks for had already happened: the README printed `7/7 fields AGREED` on a row where the tool
had reported a head digest. Nobody fabricated that number — a summary drifted from the thing it
summarised, which is the same failure mode as §4.6 and §4.9 upstream, arriving here. It is
deliberately **not** run from `verify_all.py`: that would recurse, and its `STALE` outcome is
ambiguous by construction (either the README is wrong or your machine differs from the one it
records) so rolling it into a PASS/FAIL table would assert a cause nobody observed.

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

Persistent effects taken, all local and all within the approved scope: files created in
`/home/user/b1u.agenetic.`, commits on `claude/b1-local-omega13-handoff-auv8bc` and a push of that
branch to `origin` (REVERSIBLE — git history, and a branch that can be deleted), plus
`pip install numpy pytest` into this ephemeral container on 2026-09-17 under explicit
authorisation (REVERSIBLE — the container is discarded, and B1 Local itself remains
standard-library-only).

**Not authorised, and not done:**

- Creating a public GitHub repository, publishing, or opening a pull request.
- Relicensing ΩΣ13.9. It stays all-rights-reserved; `verify_provenance.py` reports publication
  BLOCKED, and will keep reporting it until the rights holder decides.
- Downloading, installing or running any model.
- Editing the ΩΣ13.9 archive, including the `b1mu.toml` defect in §4.6 and the two stale prose
  checks in §4.9.
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
**Candidate:** this repository, plus the seven ADRs and this document.

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
| 16 | Own defect: `verify_all.py` reported a clean PASS while four languages went unobserved | reading the footer against the row detail | `PARTIAL` status added; exit 2 |
| 17 | ΩΣ13.9's full suite was left UNKNOWN by the handoff | 646/646 passed in 67s once `numpy`/`pytest` were installed | §4.9; supersedes the 45/45 claim |
| 18 | Two of ΩΣ13.9's release checks assert on exact prose, not on the property | `verify_omega12.py:285-286` greps literal strings absent from the 13.9 README, whose guarantees are present and reworded | §4.9; recorded for the rights holder, same failure mode as the `b1mu.toml` gap |
| 19 | The handoff's §1.3 requires effect-time authority checking; a claim-time-only check implements half of it | the interval between claim and consume is unguarded by construction | ADR-0006: authority is revalidated a second time before the effect commits |
| 20 | Own defect: the gate's first draft wrote journal rows directly to dodge nested transactions, skipping the journal's own refusals | duplicate event id, unknown causal parent, stale epoch and duplicate effect were all bypassed | both journals grew `append_in_lease`, so the gate gets one atomic unit *and* full validation |
| 21 | Own defect: the work runner's consume-time revalidation was vacuous while reading as enforced | it passed the grant-time digest to `consume()`, which always matches, under a comment claiming re-observation | `pre_effect_digest` — observed immediately before acting, which is the only value that reads the property that matters |
| 22 | Own defect: README printed a paraphrase of `verify_all.py` as though it were the transcript, including `7/7 fields AGREED` on a row reporting a head digest | comparing the block against a real run | the tool's lines were made worth quoting, and `verify_readme_transcript.py` now checks both files that quote them |
| 23 | Own defect: two projection tamper tests mutated a field to the value it already held | the tests failed, correctly, reporting no drift | mutations now assert they are mutations before writing |

Finding 20 is the most instructive of this increment's own defects. The workaround produced
passing tests, because nothing in the gate's own suite exercised the journal's refusals — the hole
was invisible from inside the component that had it. The fix was structural rather than local:
rather than special-casing the gate, the journal gained a lease-aware entry point, which removes
the incentive that created the hole.

Finding 18 generalises finding 5, and the pair is the most useful thing this pass learned about the
archive: in two independent places the archive's **checks** have drifted from the archive's
**substance**. Neither is a regression in behaviour. Both are checks that would not notice a real
regression, which is the more expensive kind of defect.

Finding 16 is worth separating from 14 and 15 because it is not a bug in a test — it is a
reporting defect. The rows were correct and the footer was not, and the footer is where a reader
stops.

Findings 21, 22 and 23 are one pattern in three places: **a check that cannot fail.** A staleness
comparison against a value that always matches, a transcript quoting itself, a tamper test that
changes nothing. None of the three produced a failing test, and two of them produced a *passing*
one, which is worse than failing because it is evidence-shaped. The only reason all three were
found is that each was read for whether it *could* report a problem rather than for whether it
did. That habit is the one this project most depends on and the one nothing in it can automate.

Finding 22 also closes a loop with findings 5 and 18: the "checks drifted from substance" pattern
was first recorded against the upstream archive, then found here, in this document's own §4.7 and
in the README. It is not a property of that archive. It is what documentation does.

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

**Stop reason:** further iteration on Phases A and B would add components without adding evidence.
The four unobserved consumers need a different machine, not more design. Capability policy and
projection topology are phases of their own, not refinements of these. Continuing to elaborate the
substrate would be recursion mistaken for improvement.

**Status:** `CONVERGED_FOR_CURRENT_OBJECTIVE_AND_EVIDENCE`

Scoped precisely: converged for the causal substrate **and the authority gate above it**, on this
machine, with these toolchains, against this evidence. Not converged for B1 Local, which has five
phases remaining. Two verifier rows report PARTIAL, and that is a limitation of the environment
rather than of the work — stated separately, as the contract's §39 requires, rather than folded
into the convergence claim.

One claim was materially *strengthened* rather than merely restated between the two passes, and
it is worth separating from the rest. The first pass established that Rust and Python agree. The
second establishes that they **contend correctly**: two processes in two languages racing one
gate produce one permit and one effect, with the loser refused by the design rather than by the
database. Agreement is a property of two implementations; correct contention is a property of the
architecture, and it is the one the handoff's "equal computational peers" decision actually rests
on.

## 21. Executive Plan

**Strategic goal.** A verifiable causal substrate for B1 Local, so later phases rest on state
whose integrity is checked by running code.

**Current verified state.** Canonical Event Envelope v1, a global hash-chained root journal on
SQLite/WAL, and the Dual-Core Commit Gate above it — each implemented independently in Rust and
Python. The peers agree byte-for-byte across a process boundary on three vectors, on seven history
fields, and on authority digests. 135 tests (86 Python, 49 Rust). Seven journal mutation cases
detected in both languages. A Rust process and a Python process race through one gate and produce
exactly one permit and exactly one effect, with the loser refused by the gate's own rule. Fourteen
language consumers, ten observed to verify their invariant and ten observed to refuse when it is
broken. A provenance record whose verifier refuses undeclared derivations in both directions —
which it did three times during this increment, correctly — and reports publication BLOCKED.

The upstream ΩΣ13.9 archive was also re-verified rather than trusted: 646/646 tests passing, all
fourteen of its verifiers exiting 0.

**Critical constraints.** ΩΣ13.9 is all-rights-reserved and is not vendored; Apache-2.0 here
covers new B1 code only. Codex speaks only the Responses API, so llama.cpp needs a shim. Codex
Security is not local; only its schemas and skills are portable. The target is 16 GB Windows
ARM64, so `gpt-oss:20b` — Codex's built-in default — must be overridden.

**Major dependencies.** Phases A and B are complete. Phase C (projection topology) and capability
policy depend only on them and are next. Phase D (models) depends on the shim, which depends on
conformance vectors that do not exist yet, and on hardware not reachable from here.

**Important unknowns.** Every OmniBook performance number. Whether the four absent-toolchain
consumers execute correctly. What the shim's streaming and tool-call translation must cover.
Whether the Hexagon NPU is reachable — IN_DOUBT, not UNKNOWN, because vendor claims exist without
runtime proof. (ΩΣ13.9's full suite was on this list in the first pass; it is now measured at
646/646.)

**Major risks.** Reordered by this increment. A shim that mistranslates a call into a *different*
effect was the top risk with UNKNOWN recovery; effect-time authority revalidation now mitigates
it, because a mistranslated target no longer matches the envelope that authorized it. That leaves:
accidental vendoring of ΩΣ13.9, which becomes IRREVERSIBLE once published and is guarded by a
bidirectional verifier; the four unobserved language consumers, whose status is honestly UNKNOWN
rather than assumed; and projection drift, mitigated by construction.

**Next actions.** (1) Execute the four unobserved consumers on a machine with their toolchains.
(2) Prove projection rebuild across three deployment modes. (3) Add capability policy above
per-effect authorization — the layer ADR-0006 explicitly does not cover. (4) Write shim
conformance vectors before shim code. (5) Benchmark on the OmniBook — `PLAN_READY`, authorisation
required.

**Authority state.** `PLAN_READY`. Local file creation, commits and a branch push to the
designated branch, plus one authorised `pip install` into this ephemeral container. No repository
creation, no publication, no relicensing, no model download, no external write.

**Verification requirements.** `python3 tools/verify_all.py` from the repository root. Standard
library only; no `pip install`. Exit 0 means everything was checked and holds; 1 means something
failed; 2 means nothing failed but something was not observed. It currently exits 2, and §4.7
says exactly which two rows and why.
