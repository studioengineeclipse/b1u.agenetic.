# ADR-0003 — Durable memory: ephemeral by default, encrypted on explicit opt-in

- **Status:** Accepted as policy. Implementation is `PLAN_READY`, not built.
- **Date:** 2026-09-16
- **Origin:** U — the user selected this when asked directly. It closes handoff open item 6.

## Context

The handoff's §20 lists this as unanswered: *"Durable user memory policy was the next design
question and had not yet been answered."* It offered three options and recorded a leaning toward
the third without committing:

1. always-encrypted local memory;
2. plain local memory unless encryption is enabled;
3. ephemeral by default, encrypted persistent memory only with explicit opt-in.

The reason this is a design question and not a preference is that it interacts directly with the
project's central invariant. B1's whole persistence model rests on:

```text
PLAN_READY != EXECUTION_AUTHORIZED
```

and on the rule that a durable memory change is a persistent effect requiring its own
authorisation. Option 1 and option 2 both make *every conversation* a persistent effect by
default. Under either, the authority gate is open before anyone has been asked, and the
distinction between planning and persisting stops doing any work at the exact place a user would
most expect it to.

## Decision

**Nothing reaches durable memory unless the user explicitly enables it.** When enabled, durable
memory is encrypted at rest.

The user selected option 3. It is also the only one of the three under which a durable memory
write is genuinely an authorised persistent effect rather than a default, which is why the
handoff leaned this way and why the reasoning is worth keeping rather than just the outcome.

## Consequences

- A durable memory write is an ordinary B1 persistent effect and carries the ordinary machinery:
  an `effect_identity`, a bound `authority_envelope_digest`, and a `persistence_class` classified
  before the write. The Event Envelope already enforces that these three arrive together — an
  effect record missing any of them is refused by both language peers. Memory gets no exemption.
- Durable memory is classified `COMPENSATABLE`, not `REVERSIBLE`. Deleting a stored memory removes
  the record but cannot unwind decisions already made from it while it was live. Calling that
  reversible would be the kind of optimistic recovery classification the handoff's §10 warns
  about discovering only after execution.
- Encryption is at rest and local. The key lives in the per-user `%B1_HOME%` layer, outside the
  repository, alongside the rest of the private state. No key material, and nothing derived from
  it, goes into the public tree — `tools/verify_provenance.py` already refuses undeclared
  derivations, and a future privacy scan should refuse key-shaped content outright.
- Turning memory *off* after it was on is itself a persistent effect. It needs its own
  authorisation and its own recovery classification, and it must say plainly whether it erases
  what was stored or merely stops adding. Silently leaving the old store in place while reporting
  "memory off" would be exactly the sort of existence-without-authorisation state the handoff's
  §23 requires be marked `IN_DOUBT` rather than normalised.
- The default costs continuity. A fresh B1 session starts without recall, and that is the
  intended trade: the alternative buys convenience by spending the authority boundary.

## Consequences for the root journal

Worth stating explicitly, because it is easy to conflate the two stores: the root journal is not
user memory. The journal records *what happened* — proposals, authorisations, effects, evidence —
and it is authoritative history that must not be selectively erasable, or tamper detection means
nothing. Durable memory records *what the user wants remembered about them*, and must be
erasable on request.

These are different stores with different lifecycles, and B1 must not implement the second inside
the first. A memory-erasure request that required rewriting journal history would put a privacy
obligation in direct conflict with an integrity guarantee. Keeping them separate is what avoids
having to choose.

## Not decided here

The cipher and key-derivation choice, where the key is held on Windows (DPAPI, the credential
store, or a passphrase), what a memory record looks like, and how memory is scoped across
workspaces. None of it is built, and no claim about B1's memory behaviour should be made until it
is.
