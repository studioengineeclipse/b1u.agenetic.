# Third-party notices

B1 Local is licensed under Apache-2.0 (see `LICENSE`). That grant covers **new B1 Local code only**.
This file records every third-party work this project studied, derived design from, or interoperates
with, and states precisely what was and was not taken from each.

Machine-readable form: [`provenance/provenance.json`](provenance/provenance.json), validated by
`tools/verify_provenance.py`.

> **Nothing from any upstream is vendored into this repository.** Every entry below is a
> *design-derivation* or *interoperation* relationship. No upstream source file is copied here.
> Where a B1 file's design is traceable to an upstream file, the B1 file carries a
> `Design-derived-from:` comment naming that upstream path, and `provenance.json` records the pair.

---

## 1. OpenAI Codex

| | |
|---|---|
| Upstream | `openai/codex` |
| Supplied as | `codex-main.zip`, SHA-256 `89b649348fab595cafc6a152d2db99417db177ea51339d558a71fdb01d98f330` |
| Inspected | 2026-09-16, read-only, 7,673 archive entries |
| License | Apache-2.0 (`LICENSE` at archive root) |
| SPDX | `Apache-2.0` |
| Upstream NOTICE | `OpenAI Codex, Copyright 2025 OpenAI`. Also declares code derived from [Ratatui](https://github.com/ratatui/ratatui) under MIT, Copyright (c) 2016-2022 Florian Dehau, Copyright (c) 2023-2025 The Ratatui Developers. |

**Taken:** design observations only, recorded in `docs/decisions/ADR-0002-responses-wire-api.md`
and `docs/OMEGA13-ANALYSIS.md`. Specifically the app-server JSON-RPC `Thread`/`Turn`/`ThreadItem`
primitive shape (`codex-rs/app-server-protocol/src/protocol/v2/{thread_data,item}.rs`), the local
OSS provider wiring (`codex-rs/model-provider-info/src/lib.rs`), and the structural shape of
`codex-rs/responses-api-proxy` as a template for a future B1 shim.

**Not taken:** no source code, no OpenAI trademark, brand, product name, logo or asset is used as a
B1 mark. This project is not endorsed by or affiliated with OpenAI. Because nothing is copied, the
Ratatui MIT notice above is reproduced for completeness rather than because Ratatui-derived code is
present here.

**Apache-2.0 obligations if code is later copied in:** retain `LICENSE` and `NOTICE`, state
modifications in changed files, and preserve attribution. `tools/verify_provenance.py` fails the
build if a B1 file declares an upstream that is not listed in this document.

---

## 2. OpenAI Codex Security

| | |
|---|---|
| Upstream | `openai/codex-security`, npm `@openai/codex-security` 0.1.24 |
| Supplied as | `codex-security-main.zip`, SHA-256 `3322deac5d7891cbf16ebb62e1d62c8eb52febfb901c0c550174292c95e2b78c` |
| Inspected | 2026-09-16, read-only, 669 archive entries |
| License | Apache-2.0 (`LICENSE` at archive root; `package.json` declares `"license": "Apache-2.0"`) |
| SPDX | `Apache-2.0` |
| Upstream NOTICE | none present in the supplied archive |

**Taken:** design observations only. Recorded in `docs/decisions/ADR-0005-codex-security-scope.md`.

**Material finding affecting reuse:** Codex Security is **not a local tool**. It spawns the `codex`
binary (`sdk/typescript/src/runtime.ts:2373`), hardcodes `https://api.openai.com/v1/embeddings`
(`sdk/typescript/src/server/embeddings.ts:82`), requires `codex-security login` or `OPENAI_API_KEY`,
and gates some findings behind OpenAI's "Trusted Access for Cyber" programme. Only
`sdk/typescript/src/deduplication/checkpointed-review.ts:46` honours `OPENAI_BASE_URL`. The portable,
cloud-free part is `plugins/codex-security/schemas/` and `plugins/codex-security/skills/`.

**Not taken:** nothing is vendored. No trademark use.

---

## 3. B1u-DQAS OmegaSigma13.9 Quantum-Fidelity

| | |
|---|---|
| Upstream | supplied by the rights holder |
| Supplied as | `B1mu-DQAS-Omega13.9-QuantumFidelity-13.9.0a1.zip`, SHA-256 `fde33c81e59800cac8767250981c9919ca142ad5fa7394ee096a8aff8ca6b230` |
| Inspected | 2026-09-16, read-only, 570 archive entries |
| Package version | `pyproject.toml` `13.9.0a1`; runtime/compiler constants `13.9.0-ref1` |
| License | **All rights reserved.** "No patent, copyright, trademark, redistribution, sublicensing, or commercial-use license is granted by this file." |
| SPDX | `LicenseRef-B1mu-AllRightsReserved` |

**Status: publication blocker.** This archive grants no redistribution right. It is **not** vendored
into this repository and **must not be**. The Apache-2.0 grant in `LICENSE` covers new B1 Local code
only and does **not** relicense this archive. Relicensing is the rights holder's decision and has
not been made — see `docs/decisions/ADR-0004-license-and-publication.md`.

**Taken:** design derivation only, from a read-only inspection. Each derived B1 file names its
upstream counterpart in a `Design-derived-from:` comment. The derivations are:

| B1 Local file | Upstream design source | What was derived |
|---|---|---|
| `python/b1_protocol/canonical.py` | `src/b1mu/serialization.py` | The canonical-JSON rule: sorted keys, `ensure_ascii=False`, separators `(",", ":")`, `allow_nan=False`, trailing newline, UTF-8. |
| `crates/b1-protocol/src/canonical.rs` | same | The same rule, reimplemented in Rust to match byte-for-byte. |
| `python/b1_state/journal.py` | `src/b1mu/durable.py` | Append-only SQLite/WAL journal; SHA-256 `prior_record_digest` -> `record_digest` chaining from a `GENESIS` anchor; committed head digest + record count for truncation detection; exclusion of wall-clock fields from digested payloads. |
| `crates/b1-state/src/journal.rs` | same | The same design in Rust. |
| `tools/verify_polyglot.py` | `tools/verify_omega13_9_polyglot.py` | Manifest-driven fourteen-language conformance verification; unique-responsibility and unique-contribution checks; exact expected-output matching; missing toolchain reported as `UNKNOWN` rather than promoted from source-file existence. B1's port additionally executes each manifest's declared commands, removes the `build_command` non-empty exemption, and targets `dotnet` instead of `csc`/`mono`. See ADR in `docs/OMEGA13-ANALYSIS.md` section 20. |

**Deliberate divergences** (B1 Local does not copy these choices):

- The upstream journal is chained per `(run_id, attempt, seq)`. B1's root journal is a single global
  chain over one monotonic `seq`. See `docs/decisions/ADR-0001-root-journal.md`.
- B1 forbids floating-point values inside digested payloads, which upstream permits. Python renders
  floats via `repr()` and Rust `serde_json` via Ryu; for some values these disagree, which would
  silently break the cross-language digest equality the whole fabric depends on.

---

## 4. Build and runtime dependencies

New B1 Local code in this increment uses **only** the Rust standard library, `serde`/`serde_json`,
`rusqlite`, `sha2` on the Rust side, and **only the Python standard library** on the Python side
(`json`, `hashlib`, `sqlite3`, `unittest`). No `numpy`, no `pytest`, no npm packages. Rust crate
licenses are resolved by Cargo at build time and are not restated here.
