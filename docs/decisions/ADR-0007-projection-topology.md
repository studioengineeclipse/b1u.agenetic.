# ADR-0007 — Projection topology: three deployment modes, one truth

- **Status:** Accepted, implemented, verified across languages.
- **Date:** 2026-09-18
- **Origin:** U — the handoff names compact, modular and audit-replay deployment modes.
  M — the view set, the storage layouts, and the containment rule.

## Context

The handoff asks for three deployment modes over one logical authority. Taken at face value that
is a packaging request, and packaging requests are usually cheap. This one is not, because of what
it would mean if it were done badly: three ways to read the same history, each reading it slightly
differently, is three truths wearing one name. B1's entire premise is one authoritative history,
and a deployment mode that could change what history says would dissolve that premise from
underneath while every individual test still passed.

So the deliverable is not "three modes exist". It is **the mode is a storage decision and cannot
change what is true**, established as a property rather than as a convention everyone agrees to
honour.

## Decision

**A projection is a pure function of the record sequence.** `build_views(records)` consults
nothing else — not the clock, not the filesystem, not which mode is in use. Modes differ only in
where the resulting bytes land. This is what makes the three modes provably equivalent instead of
separately tested into agreement; there is one derivation and three destinations.

**Four views, and the mode never changes which four.**

| View | One row per | Answers |
|---|---|---|
| `events` | record | what happened, in order |
| `effects` | effect identity | what has been done to this thing, and how well supported the latest claim is |
| `authority` | authority envelope digest | what was actually done under this authorization |
| `heads` | the journal | counts, epoch ceiling, and both heads |

**The three modes.**

| Mode | Stores | Trade |
|---|---|---|
| `compact` | one file | fewest inodes, one atomic replace |
| `modular` | one file per view plus a manifest | a subsystem can be rebuilt or shipped alone |
| `audit-replay` | nothing | every read replays; cannot drift, because nothing is stored |

Audit-replay is not a degenerate case. It is the honest baseline the other two are measured
against: it pays per read and gets the impossibility of drift for free, and the other two buy
speed back with `check()`.

**Storage loses.** `check()` compares stored views against views rebuilt from the journal. On
disagreement the projection is wrong by definition — there is no merge, no reconciliation, and no
case where a derived view is the more current one.

**A stored projection's own digest is not the check.** Each file records the digest of what it
holds, which catches a truncated or garbled write. It cannot catch an edit that also updates that
digest. This is the journal's `payload_digest` lesson applied to derived state: an internally
consistent record is not thereby a true one. So `check()` compares against the journal and never
against the file's claim about itself, and a test damages a compact projection *and repairs its
self-declared digests* to prove the ordering matters.

**`heads` carries both heads.** `chain_head` is what the last record claims; `replayed_head` is
recomputed from the envelopes. A projection built over a damaged journal must not launder the
damage into a clean-looking summary, so `consistent` reports the disagreement rather than picking
a side. Deciding which to believe is the journal's own `verify()`, which has the committed record
count as well.

## Why two projection digests

`b1_state.projection_digest` already existed and is unchanged. It digests the record index and
answers *do two language peers see the same history*. `b1_projection.views_digest` digests the
derived views and answers *does this deployment mode tell the same truth*.

They are kept separate deliberately. Folding them into one function would make a change made for
either question silently alter the answer to the other, and the cross-language journal contract
already compares the first by name.

## Writing a projection is a persistent effect

It is, literally: it creates files. It is classified **REVERSIBLE** and does not take the commit
gate, and the reasoning is worth stating rather than assuming, because "it's only derived data" is
exactly the kind of sentence that later turns out to have been load-bearing.

A projection contains nothing the journal does not already contain. Destroying it loses nothing;
writing it adds nothing. Its authority is therefore the journal's own, and requiring a separate
authorization to rebuild derived state would mean recovery could not run in the case that needs it
most.

That reasoning holds only while the projection stays inside a workspace B1 owns. Writing derived
state into somewhere it does not own is an effect on someone else's storage regardless of how
rebuildable the content is — so `ProjectionStore` resolves its root inside the workspace it is
bound to and refuses an escape before anything is written, by `Path.resolve()` and
`relative_to()`, the same way `FileWriteEffect` does. Both a `../` traversal and a symlink pointing
out are refused, and both are tested.

## Consequences

Good:

- The exit criterion is one sentence and it is checked: destroy every projection, rebuild, and all
  three modes produce one digest. `tools/verify_cross_language_projection.py` also reads each mode
  back *through its own reader*, so a mode that wrote correctly and parsed back something else
  cannot pass on a digest comparison alone.
- Cross-language agreement extends past history into derived state. Rust and Python derive
  byte-identical views, compared per view so a divergence names the view rather than the
  projection.
- Drift is detectable and localised. A damaged view is named; a missing one is reported as a
  missing projection rather than a smaller one.

Costs, stated plainly:

- The Rust peer builds and digests views but does **not** implement the storage modes. The claim
  under cross-language test is that two independent implementations derive the same views; where
  those bytes land is a deployment decision, and duplicating the file layout would have tested the
  layout twice and the derivation once. A Rust-native deployment that needs to *store* projections
  is not covered by this ADR.
- `check()` costs a full rebuild. It is correct and it is O(history). Incremental projection
  maintenance is not built, and would need its own argument about why an increment cannot drift.
- Four views is a guess at what a real deployment reads. The set will grow, and growing it changes
  `views_digest` — which is a schema change to derived state, cheap only because projections are
  disposable. That cheapness is the reason to keep them disposable.

## Verified

`python/tests/test_projection.py` — 24 tests: purity, the exit criterion in all three modes,
read-back through each mode's own reader, four drift cases including the internally-consistent
edit, containment against traversal and symlink escapes, and a hand-built damaged chain that the
`heads` view reports rather than hides.

`crates/b1-state/src/projection.rs` — 5 tests on the Rust peer.

`tools/verify_cross_language_projection.py` — PASS: 3 modes AGREED, 5/5 rust/python AGREED. Shown
to bite: perturbing one field in the Rust `authority` view produces `MISMATCH` on that view and on
`views_digest`, and on nothing else.
