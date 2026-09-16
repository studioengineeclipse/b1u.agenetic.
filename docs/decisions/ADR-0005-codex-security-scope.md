# ADR-0005 — Codex Security is not local, so Phase 7 is re-scoped

Design-derived-from: codex-security:sdk/typescript/src/server/embeddings.ts

- **Status:** Accepted as a constraint. The security subsystem is `PLAN_READY`, not built.
- **Date:** 2026-09-16
- **Origin:** M — discovered by reading the supplied Codex Security source.

## Context

The handoff's opening line is *"The user wants a completely local B1 multi-AI agent system"*, and
its Phase 7 objective is *"Make Codex Security workflows first-class evidence-producing B1
specialists."* Both are reasonable, and together they assume Codex Security can run locally.

It cannot, as supplied. Verified 2026-09-16 by reading the archive:

**It drives the Codex binary.** `sdk/typescript/src/runtime.ts:2373` resolves
`process.platform === "win32" ? "codex.exe" : "codex"` and spawns it. Codex Security is an
orchestrator over a Codex agent, not a standalone analyser — so everything in ADR-0002 about the
Responses API applies transitively to it.

**One endpoint is hardcoded to the cloud.** `sdk/typescript/src/server/embeddings.ts:82` posts to
`https://api.openai.com/v1/embeddings` as a literal. There is no base-URL override on that path.

**It requires authentication.** The README: *"Requires Node.js 22.13.0 or later and Python 3.10 or
later... `codex-security login`... For CI, set `OPENAI_API_KEY` instead of signing in."*

**Some capability is gated behind a programme.** Also from the README: *"Some cybersecurity
requests and protected findings require approval through Trusted Access for Cyber."* That is an
access decision outside the software, and no local configuration changes it.

**Only one call site is redirectable.** `sdk/typescript/src/deduplication/checkpointed-review.ts:46`
reads `OPENAI_BASE_URL`. That is a real but partial opening: it does not cover the embeddings path.

So "integrate Codex Security workflows" as written would make a local-first system depend on a
cloud account, a hardcoded external endpoint, and a third-party access programme. Under the
handoff's own rules that is not a detail to work around quietly — it is a material change to a
`U`-level constraint, and the constraint should win over the component.

## What *is* local and reusable

The portable part is substantial, and it is the part that matters for an evidence subsystem:

- `plugins/codex-security/schemas/` — `findings.schema.json`, `coverage.schema.json`,
  `scan-manifest.schema.json`, `patch-risk-assessment.schema.json`, plus `definitions/` and
  `tools/`. These are data contracts. They need no network and no account.
- `plugins/codex-security/skills/` — fifteen skills: `security-scan`, `deep-security-scan`,
  `security-diff-scan`, `finding-discovery`, `triage-finding`, `fix-finding`, `verify-fix`,
  `validation`, `assess-patch-risk`, `attack-path-analysis`, `threat-model`, `track-findings`,
  `vulnerability-writeup`, `define-security-policy`, `propose-security-hardening`. These are
  prompt-and-procedure definitions, portable to any model runtime.
- `plugins/codex-security/preflight/capability-profiles.toml` — capability declarations.

All of it is Apache-2.0 (`package.json` declares `"license": "Apache-2.0"`, and the archive
carries a matching `LICENSE`), so reuse is permitted subject to the ordinary notice obligations
recorded in `THIRD_PARTY_NOTICES.md`.

## Decision

**Adopt the contracts and the skills. Do not adopt the orchestrator.**

B1's security subsystem will use Codex Security's finding, coverage, scan-manifest and patch-risk
schemas as its security-evidence contract, and its skills as the procedure library, driven by B1's
own local model router rather than by a cloud-authenticated CLI.

**Semantic embeddings are an optional capability, not a dependency.** The hardcoded embeddings
endpoint is the one genuinely non-local piece. Deduplication that needs it degrades rather than
fails: B1 falls back to deterministic dedupe (location, rule identity, content digest) and records
that semantic dedupe was unavailable. It does not silently claim coverage it did not have.

**A cloud path may exist, but only as an explicitly authorised effect.** Sending source code to an
external service is a transmission — a persistent effect under §19 of the operating contract, and
an irreversible one, since content cannot be recalled once sent. If B1 ever offers cloud-assisted
scanning it is opt-in per scan, with the target and scope named in the authority envelope. It is
never the default and never implicit in "run a security scan".

**Security output stays evidence.** This part of the handoff needs no change and is worth
restating because it is the whole point: a finding constrains what B1 may claim, never what B1 may
do. A security subsystem that could authorise its own remediation would be an executor granting
itself authority, which is precisely the collapse the Origin/Authority/Executor/Effect separation
exists to prevent.

## Consequences

- Phase 7's exit criterion in the handoff — *"Security findings enter the tournament/evidence graph
  and block unsafe candidates without independently granting mutation authority"* — survives
  unchanged. Only the implementation path changes.
- A B1 security finding becomes an Event Envelope with `origin: M`, evidence references to the
  scan artifacts, and an `epistemic_status` that is honest about depth: a scan that ran without
  semantic dedupe reports `WORKING_ASSUMPTION` on its completeness, not `VERIFIED`.
- Every skill and schema B1 takes must be recorded in `provenance/provenance.json` with its
  upstream path at the point it is taken, and `tools/verify_provenance.py` will refuse an
  undeclared one. Nothing is vendored in this increment.
- Re-implementing orchestration is real work that the handoff's Phase 7 does not budget for. That
  cost should be stated in the roadmap rather than discovered during it.

## Verification status

The findings above are `VERIFIED` by direct reading of the supplied source at the cited paths.
Everything about B1's security subsystem is `PLAN_READY`: no security code exists in this
repository, and no claim about B1's scanning capability should be made until it does.
