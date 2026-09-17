# Provenance map

Machine-readable form: [`provenance/provenance.json`](../provenance/provenance.json).
Licence terms and notice obligations: [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).
Checked by: `python3 tools/verify_provenance.py`.

## The claim this file makes, and how it is held up

**No upstream source file is vendored into this repository.** Every relationship recorded here is
either a *design derivation* — B1 code whose design is traceable to an upstream file — or a *design
observation* — a finding about upstream recorded in an ADR.

"We did not copy anything" is a claim, and a claim without a check is a working assumption. So the
check runs in both directions:

1. Every entry in `provenance.json` must name a file that exists and that carries a matching
   `Design-derived-from: <upstream_id>:<path>` comment. A manifest entry that drifts from the file
   it describes is a provenance defect, not a formatting nit.
2. **Every file in the tree carrying such a marker must appear in the manifest.** This is the
   direction that does the real work: a new derived file cannot be added without declaring it.

`verify_provenance.py` fails on either. It also reports `PUBLICATION: BLOCKED` for as long as any
upstream forbids redistribution, which is the machine-checkable form of
[ADR-0004](decisions/ADR-0004-license-and-publication.md).

A design idea is not a copyrightable expression, but the boundary is not always obvious. The
marker plus the manifest makes each claimed derivation reviewable by a human who can judge where
that boundary falls, rather than leaving it implicit.

## Upstreams

| id | Work | Licence | Redistributable | Archive SHA-256 (first 16) |
|---|---|---|---|---|
| `codex` | OpenAI Codex | Apache-2.0 | yes | `89b649348fab595c` |
| `codex-security` | OpenAI Codex Security | Apache-2.0 | yes | `3322deac5d7891cb` |
| `b1mu-omega13.9` | B1μ-DQAS ΩΣ13.9 Quantum-Fidelity | **All rights reserved** | **no** | `fde33c81e59800ca` |

All three were inspected read-only on 2026-09-16. Entry counts (7,673 / 669 / 570) match the
handoff.

## Derivations from ΩΣ13.9

This archive grants no redistribution right, so nothing from it is vendored and nothing may be.
The Apache-2.0 grant in `LICENSE` covers new B1 Local code only.

| B1 file | Upstream design source | Derived |
|---|---|---|
| `python/b1_protocol/canonical.py` | `src/b1mu/serialization.py` | The canonical-JSON rule: sorted keys, `ensure_ascii=False`, separators `(",", ":")`, `allow_nan=False`, trailing newline, UTF-8 |
| `crates/b1-protocol/src/canonical.rs` | same | The same rule in Rust, required to match byte-for-byte |
| `python/b1_state/journal.py` | `src/b1mu/durable.py` | Append-only SQLite/WAL journal; SHA-256 `prior_record_digest` → `record_digest` chaining from a `GENESIS` anchor; committed head digest **and record count** for truncation detection; wall-clock fields excluded from digested payloads |
| `crates/b1-state/src/journal.rs` | same | The same design in Rust |
| `tools/verify_polyglot.py` | `tools/verify_omega13_9_polyglot.py` | Manifest-driven fourteen-language verification; unique-responsibility and unique-contribution checks; exact expected-output matching; missing toolchain reported as `UNKNOWN` rather than promoted from source-file existence |
| `python/b1_authority/authority.py` | `src/b1mu/work/store.py` | Effect-time staleness by digest comparison — upstream raises *"run context changed; re-plan before effects"* at `store.py:737-740` when a context digest no longer matches |
| `python/b1_authority/gate.py` | `src/b1mu/work/runtime.py` | Approval → one-time permit → fence → permit-digest-bound transition proof. Upstream validates and consumes an approval in one transaction (`store.py:766-772`: a conditional `UPDATE` plus a `changes()` check), issues a per-domain monotonic fence, allows one active permit per target, and checks a proof's permit digest, capability, target and epochs |

### Deliberate divergences

B1 does not copy these upstream choices, and the reasons are recorded so a future reader does not
"fix" them back:

- **One global chain, not one per run.** Upstream chains per `(run_id, attempt, seq)`, which is
  right for a runtime that executes programs. B1's history spans the model router, the tournament,
  security evidence and the desktop — subsystems that are not runs of a program — so it uses a
  single monotonic `seq` and a single chain. See
  [ADR-0001](decisions/ADR-0001-root-journal.md).
- **Floats and nulls are rejected inside digested payloads.** Upstream permits them. Python renders
  floats via `repr()` and Rust `serde_json` via Ryu; for some values these disagree, which would
  give the two peers different digests for the same record. In Rust the `Digestable` enum simply
  has no float variant, so it is a compile-time impossibility rather than a runtime check.
- **The polyglot verifier executes each manifest's declared commands.** Upstream validates
  `build_command` and `run_command` and then runs hardcoded commands from an internal table
  instead, so the declared commands are decorative — in a harness whose purpose is preventing
  decorative participation. B1 executes the templates, constrained: argument vectors with no
  shell, only known placeholders expand, the executable must match the manifest's own declared
  tool or a helper allowlist, and every path must resolve inside the repository root.
- **`build_command` is not exempt from the non-empty check.** Upstream writes
  `if field != "build_command" and value in (None, "", (), []):`, so a manifest could declare no
  build step at all and pass.
- **C# targets `dotnet`, not `csc` plus `mono`.** Mono is legacy, and the OmniBook's Windows ARM64
  target ships the modern SDK.

## Observations recorded from Codex and Codex Security

No code is taken from either. Both are Apache-2.0, so code reuse would be permitted subject to the
notice obligations in `THIRD_PARTY_NOTICES.md`; this increment takes none.

| ADR | Upstream source | Observation |
|---|---|---|
| [ADR-0002](decisions/ADR-0002-responses-wire-api.md) | `codex:codex-rs/model-provider-info/src/lib.rs` | `WireApi` has exactly one variant, `Responses`; `wire_api = "chat"` is a hard deserialization error; the `ollama-chat` provider was removed. llama.cpp therefore cannot drive a Codex-derived backend without a shim |
| [ADR-0005](decisions/ADR-0005-codex-security-scope.md) | `codex-security:sdk/typescript/src/server/embeddings.ts` | The embeddings endpoint is hardcoded to `api.openai.com`, making the scanner non-local |

Neither project is affiliated with or endorsing B1 Local. Apache-2.0 §6 withholds trademark
rights, so "Codex", "OpenAI" and their marks are not usable as B1 branding regardless of the code
licence, and none is used here.

## Current status

```
provenance record: PASS (7 derived files declared)
PUBLICATION: BLOCKED
  BLOCKER b1mu-omega13.9: All rights reserved...
```

Publication stays blocked until the rights holder makes a licence decision on ΩΣ13.9. Choosing a
licence for new B1 code, as ADR-0004 did, is not permission to publish: creating a public
repository or distributing any artifact remains a separate persistent effect needing its own
authorisation at the time it happens.
