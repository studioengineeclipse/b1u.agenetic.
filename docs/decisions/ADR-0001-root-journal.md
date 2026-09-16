# ADR-0001 — One global root journal, and who is allowed to write to it

- **Status:** Accepted, implemented, verified.
- **Date:** 2026-09-16
- **Origin:** M — derived from two U-level constraints: "append-only hash-chained
  authoritative journal, rebuildable projections" and "Rust and Python as equal computational
  peers".

## Context

Two of the handoff's locked decisions pull against each other if taken naively.

1. *Rust and Python are equal computational peers.* Read as "both may write", this is a
   recipe for split-brain: two processes appending to one chain with no ordering between them
   produce two histories that each verify locally and disagree globally.
2. *There is one authoritative history.* Read naively against the existing OmegaSigma13.9
   implementation, this looks already solved — but it is not, and the difference matters.

On the second point, verified by reading `src/b1mu/durable.py` in the supplied archive: the
upstream journal is real and works, but it is chained per `(run_id, attempt, seq)` with a
separate `GENESIS` anchor for each `(run, attempt)` pair. That is the right shape for a runtime
that executes programs. It is the wrong shape for B1 Local, which needs one history spanning the
model router, the tournament, security evidence, the desktop UI and the work runtime — subsystems
that are not runs of a program.

The handoff also leaves this explicitly open (§20, item 7): *"Whether Rust or another component
physically owns the root SQLite writer thread/process within the equal-peer model still needs
implementation-level protocol design. Equality should not become dual uncoordinated writers."*

## Decision

**One global chain.** A single monotonic `seq`, a single `GENESIS` anchor, one
`record_digest = SHA256(prior_record_digest || canonical_bytes(envelope))` chain across every
subsystem. Per-run, per-subsystem and per-workspace views are projections over that chain, not
separate truths.

**Equality is computational; writing is leased.** Both peers may analyse, derive subgoals, plan,
simulate, verify and *propose*. Neither writes directly. Every append opens a SQLite
`BEGIN IMMEDIATE` transaction, which the database grants to exactly one connection at a time.
Concurrent proposals therefore linearise rather than interleave, and the loser gets told it lost
rather than silently producing a second history.

This answers the handoff's open item 7 without picking a winner: *no component owns the writer*.
The database owns the lease, and both peers queue for it on equal terms. There is no privileged
process to fail over from.

**Two commitments, not one.** The chain is stored alongside a separately committed head digest
*and record count* in a singleton `root_head` row. This is taken from upstream and is easy to
undervalue: a prefix of a valid chain is itself a valid chain, so the chain alone cannot see a
truncated tail. The committed count is what makes trailing truncation visible. The test
`a_truncated_tail_is_detected_by_the_committed_count` exists to keep that property honest.

**`effect_identity` is a column, not a payload field.** Duplicate-effect detection has to run in
the same transaction as the append, against an index, before the write lands. Burying the
identity inside the payload would make that a full scan of deserialised JSON, which in practice
means it would not be done.

## Consequences

- Appending refuses, and changes nothing, on: an invalid envelope; a duplicate `event_id`; a
  causal parent not already in the journal; an `epoch` below the committed fence; or a repeat of
  an `effect_identity` at or below the epoch that already recorded it.
- The last of these encodes the retry rule directly. A retry of the same intended effect is
  allowed — it is how recovery works — but only under a *newer* fence. The earlier attempt is
  kept, never overwritten, so both attempts stay in history and `Attempt identity != Effect
  identity` survives in the stored record rather than only in the documentation.
- `replay_digest` is pure: it needs no database. A peer can predict a head before writing
  anything, and two peers can compare heads without sharing storage. That is what makes the
  cross-language check possible at all.
- Projections are disposable by construction. If a projection and a replay disagree, the
  projection loses and is rebuilt.
- An unrecognised schema version is refused rather than migrated. A build that guesses at a
  migration it was not written for can corrupt history while appearing to repair it.

## Verified this session

- 19 Rust tests and 21 Python journal tests, including seven mutation cases: edited payload
  (with the `payload_digest` kept internally consistent, so validation alone would not catch it),
  truncated tail, record removed from the middle, forged head, rewritten chain link, row appended
  without a head update, and an undamaged control.
- `tools/verify_cross_language_journal.py`: for one 4-event sequence spanning all four origin
  classes, non-ASCII payloads, a causal chain and an effect record, the Rust and Python peers
  agree on the appended head, the replayed head, the pure-replay head, the projection digest, the
  record count, the max epoch and the verification verdict.

## Not decided here

The Dual-Core Commit Gate — proposal protocol, capability leases, effect permits, effect-time
authority revalidation — sits on top of this journal and is not built yet. The write lease is the
seam it plugs into. OmegaSigma13.9's `SovereignWorkRuntime` is the design reference for it, and
`src/b1mu/work/store.py` already carries the `approvals` / `fences` / `permits` table shapes that
work will adapt.
