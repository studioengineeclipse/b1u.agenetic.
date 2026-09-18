# ADR-0008 — Capability policy above per-effect authorization

- **Status:** Accepted, implemented, verified across languages.
- **Date:** 2026-09-18
- **Origin:** M — the layer [ADR-0006](ADR-0006-commit-gate.md) explicitly did not cover, and
  §16 Action 4 of the analysis. Design reference: ΩΣ13.9's `AuthorityPolicy` and
  `workspaces.policy_json`, consulted and not vendored.

## Context

ADR-0006 built a gate that enforces *an* authorization. It said so plainly, and named what it did
not do: it does not know which actions a workspace permits at all.

That gap is larger than it sounds. With a per-effect gate and no standing policy, the only thing
between B1 and any action whatsoever is a person reading each envelope carefully, every time,
forever. Approval fatigue is not a hypothetical failure mode — it is the *expected* one, and a
system whose only safety property depends on sustained human attention has chosen the weakest
available guarantee and called it consent.

Worse, the per-effect gate is structurally incapable of expressing "B1 may never do that here".
Every refusal it knows how to make is of the form "not like this, not now": fresh state, a new
plan, or a re-approval resolves all of them. There was no way to say a thing was simply out of
bounds.

## Decision

A standing **capability policy** sits above per-effect authority and answers a different question:

```
policy      may this kind of action ever be authorized here?
authority   is this specific action, on this target, authorized now?
```

Both must say yes. Neither substitutes for the other.

**The policy is consulted before an authority can be granted**, not before an effect is committed.
That ordering is the decision, not an implementation detail: a capability the policy denies never
becomes an envelope a person is asked to approve. Asking someone to approve something that would
be refused regardless of their answer is how approval prompts stop being read.

### Four rules

1. **Default deny.** An action not named in `allow` is denied. `CapabilityPolicy.deny_everything()`
   is a meaningful, usable policy rather than an error state, and it is what a caller who has not
   decided must pass — so forgetting to choose fails closed instead of open.

2. **Deny wins, and is checked first.** A policy where entry order decided the outcome would make
   review depend on reading the whole list in sequence. A `deny` also ignores the persistence
   ceiling: it would be strange for denying an irreversible write to leave the reversible one
   permitted by the same rule.

3. **A persistence ceiling per capability.** `REVERSIBLE < COMPENSATABLE < IRREVERSIBLE`. A
   capability permitting `fs.write` up to REVERSIBLE does not permit an irreversible one on the
   same action and target.

4. **UNKNOWN is never admitted, by any ceiling, including the widest.** It is not a rank on that
   scale and is not treated as one — `persistence_rank` returns nothing for it in both languages,
   and it cannot be *written* as a ceiling either. An effect whose recovery class nobody could
   determine is not one anybody can permit in advance, and quietly sorting the unclassifiable
   somewhere in the ordering would decide, silently, whether it is safe.

### The policy digest is bound into the authority envelope

`AuthorityEnvelope` gains a required `policy_digest` (schema `b1-authority-envelope-2`), and
`staleness()` checks it *first*. Tightening the policy therefore makes every outstanding
authorization stale.

This is the handoff's own rule applied one layer up. Authority goes stale when its assumptions
change, and a standing policy is an assumption every authorization rests on. Without this, narrowing
what B1 may do would leave permissions already issued running under the old, wider rule — and the
moment that matters is precisely the moment someone tightens a policy because something went wrong.

The field is required, with no default. There is always a policy, even the one permitting nothing,
so an omittable field would make "no policy was in force" indistinguishable from "nobody filled
this in".

### The scope matcher moved

`scope_admits` now lives in `b1_protocol.scope` / `b1-protocol::scope`, because the policy bounds
targets too. Two implementations of "inside the scope" that drifted apart would mean a policy and
an authorization disagreeing about the same string. `scope_is_valid` was added at the same time and
refuses `docs/*` outright: the only wildcards are a trailing `/**` and a bare `**`, and a pattern
that reads as a glob but is not interpreted as one would silently match less, or more, than it
appears to.

## What this does not do, stated rather than implied

**Adopting a policy is a configuration change, and it does not go through B1's own commit gate.**
The governing rule says a configuration change is a persistent effect requiring its own
authorization. `CommitGate.adopt_policy` does not route itself through the gate, because the gate's
every decision depends on the policy: doing so would mean the first policy could never be adopted,
and a narrowing policy could be blocked by the wider one it replaces. A gate cannot gate its own
premise.

What is offered instead, and it is not nothing:

- the change cannot happen **silently** — opening the gate with a different policy raises
  `PolicyChanged` rather than adopting what it was handed;
- it is **journaled** as `gate.policy_adopted` with both digests, the caller, and a stated reason,
  under origin `U`, and a change with no reason is refused;
- every authorization granted under the previous policy **goes stale** the moment it lands.

The limit: whoever can call `adopt_policy` can widen what B1 may do, and B1's own machinery does
not stop them. It records them. Closing that properly needs an authority rooted outside the system
being governed — a signed policy, a second party, hardware — and none of those exist here. Claiming
otherwise would be the exact failure this project is built to avoid.

**A policy is not a sandbox.** It constrains what B1's own gate will authorize. It does not
constrain the operating system, and an executor with a bug can still do what its process
permissions allow. `FileWriteEffect` resolves paths inside its workspace for that reason; the policy
is a second, independent check on intent, not a replacement for the first one on reach.

## Consequences

Good:

- "B1 may never do that here" is expressible, once, in a reviewable artifact with a digest.
- Refusals say which of the three ways a request missed — unnamed action, out of scope, or over the
  ceiling — because "denied" alone sends someone to re-read the whole policy to find out what to
  change.
- Both peers enforce it. `verify_cross_language_gate.py` checks that Rust and Python agree on the
  policy digest *before* anything else, and that both refuse a denied capability by name.

Costs:

- A schema break. `b1-authority-envelope-2` and gate schema `2`; a database written by the previous
  build has no recorded policy, and the gate refuses rather than inferring one, because inferring a
  policy is inventing a permission. No migration is offered.
- Four rules is a small policy language and will not express everything. Rate limits, time windows,
  quotas, and "this action but only once" are all absent. Adding them is a schema change with a new
  digest, which is cheap in code and not cheap in outstanding authorizations.
- Every `CommitGate` caller must now supply a policy. That is deliberate friction.

## Verified

`python/tests/test_policy.py` — 31 tests across eight groups: default-deny, deny-wins, the ceiling,
the scope grammar, digest coverage, the gate integration (a denied capability cannot be granted, a
refused grant leaves no history, an envelope built against another policy is refused), and the
policy-change path (reopening under a different policy raises, adoption is journaled with both
digests, adopting the identical policy records nothing, and an authority granted under the old
policy cannot be claimed after a change — with a control proving the gate does not simply always
refuse).

`crates/b1-policy` — 8 tests on the Rust peer, including a canonical round-trip.

`tools/verify_cross_language_gate.py` — `policy_digest_agreement: AGREED`,
`rust_denied_capability: CAPABILITY_DENIED`, `python_denied_capability: CapabilityDenied`. Shown to
bite: disabling the Rust policy check leaves every digest comparison passing and turns that one row
into `OK`, which is exactly the failure a digest-only check cannot see.

`tools/run_agent.py --approve --forbidden-target` — approved by the user, correct answer from the
tournament, and refused anyway, with nothing written and the approval callback never reached.
