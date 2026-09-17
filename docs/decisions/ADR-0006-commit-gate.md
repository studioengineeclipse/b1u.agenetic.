# ADR-0006 — The Dual-Core Commit Gate

Design-derived-from: b1mu-omega13.9:src/b1mu/work/runtime.py

- **Status:** Accepted, implemented, verified across languages.
- **Date:** 2026-09-17
- **Origin:** M — derived from the handoff's fourth CRITICAL item, *"Rust/Python commit protocol,
  fencing, idempotency, effect identity"*.

## Context

[ADR-0001](ADR-0001-root-journal.md) established one authoritative history and one write lease,
and answered *who owns the writer* with "nobody — the database leases it". That left the harder
half: **what has to be true before a persistent effect is allowed to happen at all.**

The handoff's §1.3 states the rule, and it is a rule about time as much as about content:

> If the target/scope/state/plan/effect/assumptions/execution conditions materially change,
> authority becomes stale and must be refreshed... with authority checked again at effect-time
> before the persistent transition occurs rather than treating planning-time or admission-time
> approval as permanently sufficient.

Read carefully, that says a single check is never enough. Approval happens at one moment; the
effect happens at another; the world can move in between. A gate that validates once and then
trusts its own earlier answer has not implemented this rule — it has implemented the first half
and assumed the second.

Upstream ΩΣ13.9 already solves most of the rest, and solves it well. `work/store.py:766` carries
the comment worth keeping: *"validate+consume is intentionally one transaction / linearization
point."* It validates and consumes an approval with a conditional `UPDATE` plus a `changes()`
check, issues a per-domain monotonic fence, allows one active permit per target, and binds a
transition proof to a permit digest. `store.py:737-740` even has the staleness idea, raising
*"run context changed; re-plan before effects"* when a project context digest no longer matches.

## Decision

**The sequence, and what each step is for.**

```text
propose      record intent. No authority, no effect. Always allowed.
grant        bind a user's authorization to one AuthorityEnvelope.
claim_permit validate authority AT EFFECT TIME; issue a one-time fenced permit.
consume      atomically spend the permit; record what actually happened.
reconcile    resolve an unknown outcome by reading back real state.
```

**Staleness is a comparison, not a judgement.** The authority envelope carries `state_digest` and
`plan_digest` — the state and plan it was authorized against. The gate is handed the state and
plan observed at effect time and compares. This is upstream's idea, moved *into* the envelope so
that the thing the user authorized is self-describing: it carries the world it was authorized
against, rather than referring to a context that lives elsewhere.

**Authority is revalidated at consume time, not only at claim time.** This is the addition that
makes the gate answer the handoff's rule rather than half of it. A permit may be validly issued
and the world then move while the executor works. Recording that effect as authorized would be
recording an authorization that no longer describes what happened.

**Scope is bounded by an explicit matcher, and `fnmatch` is not used.** Its `*` crosses `/`, so
`docs/*` would admit `docs/secrets/key`. B1's entries are exact strings or prefixes ending `/**`,
and unlimited scope must be written out as `**` so that granting it is a visible act rather than
an omission. An envelope must also admit its own target, so a mismatched scope is refused at grant
time rather than becoming a live authority nothing can use.

**`project_outcome` is a pure function, and it is where the central discipline lives.**

| receipt | postcondition verified | state read back | outcome | epistemic status | reconcile? |
|---|---|---|---|---|---|
| `SUCCEEDED` | yes | yes | `VERIFIED` | `VERIFIED` | no |
| `SUCCEEDED` | no | — | `IN_DOUBT` | `IN_DOUBT` | **yes** |
| `FAILED` | no | yes | `NO_EFFECT` | `VERIFIED` | no |
| `FAILED` | no | no | `IN_DOUBT` | `IN_DOUBT` | **yes** |
| `UNKNOWN` | either | either | `IN_DOUBT` | `IN_DOUBT` | **yes** |

Three rows deserve naming. Row 2 is the most common way a system comes to believe something it
has not established: a provider returns success and that becomes a fact. It resolves to
`IN_DOUBT`. Row 3 is the only path to `NO_EFFECT` and it *requires an observation* — a reported
failure on its own does not prove nothing happened. Row 5 stays `IN_DOUBT` even when a
postcondition check claims success, because with an unknown receipt the check and the receipt may
describe different attempts.

**An unknown outcome blocks retries until reconciled.** `reconcile` requires an observed digest
and cited evidence; it is the only way to clear the flag, because the point is to replace a guess
with a look. This is the handoff's §21 rule 14, and the failure it prevents is concrete: a
"failed" payment retried, and the customer charged twice.

**A permit is not evidence.** Upstream's phrasing is right and worth preserving: a permit says an
effect was *allowed to be attempted*. What happened is what the transition proof reports, and
whether the objective holds is a third question again.

## Consequences

- Every refusal is atomic. A gate that partially applied a refused claim would be worse than no
  gate, because the caller would believe nothing happened. Tested directly: after five different
  refusals the journal head and record count are byte-identical.
- A refused `consume` leaves the permit `ACTIVE`. The caller can re-authorize rather than having
  silently lost its permit to a refusal it did not cause.
- `effect_identity` is a column on both the journal and the gate's tables, so duplicate-effect
  detection runs against an index inside the transaction rather than over deserialised JSON.
- `GateError` carries a `kind()` discriminant. This exists so a verifier can distinguish *losing
  by the gate's rule* from *losing to a SQLite lock* — a gate that only worked because the driver
  returned `SQLITE_BUSY` would be relying on an implementation detail rather than a design.
- One flaw found while building this, worth recording because the fix was structural rather than
  local: the gate's first draft wrote journal rows directly to avoid nesting transactions, which
  silently skipped the journal's own refusals for duplicate event id, unknown causal parent, stale
  epoch and duplicate effect. A gate that bypasses those is a gate with a hole in it. Rather than
  keep the workaround, both journals grew a lease-aware `append_in_lease`, so the gate gets one
  atomic unit *and* full validation.

## Verified this session

Phase B's exit criterion is *"concurrent conflicting attempts produce at most one authoritative
effect commit; a stale executor is rejected."* Proven at two levels, because one is not enough:

**Two Python connections** (`python/tests/test_gate_concurrency.py`, 5 tests). Separate
connections, released from a barrier together. Exactly one permit issues; exactly one effect lands;
the loser gets the gate's own message, not a lock error; the chain stays contiguous; and a held
lease genuinely excludes a second writer.

**A Rust process against a Python process** (`tools/verify_cross_language_gate.py`). This is what
"equal computational peers" has to mean — two languages, two processes, one SQLite file. Four
things established in dependency order:

| Step | Result |
|---|---|
| Both peers compute the same authority digest, on bytes | AGREED |
| Concurrent claim on one target | 1 granted, 1 refused (`REFUSED`) |
| Concurrent consume of one permit | 1 committed, 1 refused (`PERMIT_SPENT`) |
| Effect records in history | **1** |
| Both peers agree on the final head | AGREED |

Step 1 comes first because without it nothing downstream means anything: if the peers disagreed
about an authority digest, an effect authorized under one would be unauthorized under the other.

Plus 33 Python gate tests and 9 Rust authority tests covering every refusal named above.

## Not decided here

Capability policy (which actions a workspace permits at all, above and beyond a per-effect
authorization), permit expiry by wall-clock rather than epoch, and how an authority envelope is
presented to a human for approval. The gate enforces authorization; it does not yet have an
interface through which a person grants one.
