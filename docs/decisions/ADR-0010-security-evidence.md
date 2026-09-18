# ADR-0010 — Security evidence constrains; it cannot authorise

- **Status:** Accepted, implemented, verified. The Codex Security schema port named in ADR-0005 is
  **not** done and is blocked on the archive.
- **Date:** 2026-09-18
- **Origin:** M — Phase F, whose exit criterion the handoff states and [ADR-0005](ADR-0005-codex-security-scope.md)
  preserved unchanged.

## Context

The handoff's Phase 7 criterion survived ADR-0005's re-scoping word for word:

> Security findings enter the tournament/evidence graph and block unsafe candidates without
> independently granting mutation authority.

Two halves, and only the first is obvious. Making findings block is a matter of putting them where
the tournament already eliminates candidates. Making them *unable to authorise* is the harder half,
because it is a negative property, and negative properties that live in documentation stop being
true the first time someone has a good reason to make an exception.

ADR-0005 decided to adopt Codex Security's `findings`, `coverage`, `scan-manifest` and
`patch-risk` schemas as B1's security-evidence contract. **That port has not happened, and this
ADR does not claim it has.** The archive is no longer present in this environment. The shapes in
`python/b1_security/finding.py` are B1's own, written to fill the same role; they are not derived
from those schemas and are not claimed compatible with them. `provenance/provenance.json` declares
nothing from that archive here, and `verify_provenance.py` would refuse an undeclared derivation
marker if one appeared.

## Decision

**A finding is a deterministic verifier.** `SecurityVerifier` implements the tournament's
`Verifier` protocol — the only thing in B1 that eliminates a candidate. That is the right home for
it: a scan result is not a model's opinion, and the tournament's layering already says
deterministic evidence eliminates before model synthesis chooses among survivors.

**A finding can narrow the capability policy and cannot widen it**, structurally rather than by
convention:

- `denials_from()` produces `Capability` objects destined only for a policy's `deny` list. No code
  path in `b1_security` produces an `allow`.
- `tighten()` passes the input policy's `allow` tuple through as **the same object**. There is no
  branch that could produce a different allow list, so "a finding cannot widen the policy" is a
  property of the code rather than a rule to remember.
- `b1_security` does not import `b1_authority` at all. A finding cannot reach an authority
  envelope, a permit or the gate, because those objects are not in scope — and the check is run in
  a **fresh interpreter**, so a transitive import would be caught too. A text scan catches a direct
  import and misses the one that would actually erode this.

Tightening is permitted at all because the alternative is worse. A subsystem that could only report
leaves every response to a finding as a human action, and the response to "this path writes
secrets" is always the same: stop writing there. The worst case of an over-eager denial is that B1
refuses work it could have done and asks. The worst case in the other direction is B1 doing
something nobody permitted, which is why the other direction does not exist.

A narrowing produces a new policy digest, so adopting it makes every outstanding authorization
stale — [ADR-0008](ADR-0008-capability-policy.md)'s property doing exactly what it was built for,
on the narrowing side.

**Severity and confidence are separate axes.** Every rule declares `decidable`. A structurally
decidable match (a literal `shell=True`) produces a `VERIFIED` finding; a heuristic (a string that
*looks* like a key) produces `WORKING_ASSUMPTION`. A CRITICAL finding a heuristic guessed at is not
the same object as one a parser proved, and collapsing them is how a noisy scanner teaches people
to ignore it.

**Blocking is HIGH and CRITICAL only.** A MEDIUM finding is recorded and does not eliminate.
Eliminating on MEDIUM would make the tournament unusable for ordinary code, which is how a blocking
threshold ends up switched off entirely.

## The part that matters most: absence of findings is not absence of problems

An empty findings list means the same thing whether the scanner looked everywhere or nowhere. So:

- `Coverage` is required beside the findings and carries `scanned`, `not_scanned` **with a reason
  per entry**, `rules_applied` and `degraded`. An unreadable target is recorded, never skipped —
  skipping would make the manifest report a clean, complete scan of everything it happened to
  manage, which is how a security report lies without anyone writing a false sentence.
- `ScanManifest.epistemic_status` is **computed, not supplied**. `VERIFIED` requires complete
  coverage and nothing degraded; anything else is `WORKING_ASSUMPTION`. A caller who could set it
  could report a partial scan as verified, and the two are easiest to confuse precisely when the
  findings list is empty either way.
- `degraded` is ADR-0005's anticipated case made concrete: semantic dedupe without an embeddings
  endpoint drops the status, by construction, rather than by someone remembering to mention it.
- A clean `SecurityVerifier` pass says *"not a proof of safety: it is the absence of a match, over
  these rules only"* in its own detail string.

## Consequences

Good:

- The exit criterion is checked by a verifier rather than asserted, and both halves are checked —
  including the control, because a verifier that rejected everything would pass the first half.
- `tools/verify_security_evidence.py` points the rules at this repository's own Python on every
  run. A security layer that has never been aimed at real code is one whose rules have never been
  wrong about anything.
- The layer is pure and dependency-free: ten regexes, no network, no account, no subprocess.

Costs, stated plainly:

- **Ten regex rules is not a security product.** It is a real, local source of findings so the
  layering above it is exercised against something rather than a fixture. Calling it a scanner in
  any stronger sense would be the overclaim this project exists to avoid.
- The self-scan currently reports 14 findings, **all 14** in the scanner's own rule table or in its
  tests: a regex scanner matches its own patterns, and the fixtures deliberately contain unsafe
  code. They are reported rather than excluded. An exclusion list is how coverage silently shrinks,
  and `Coverage` exists in this package specifically to stop that — suppressing them here would be
  the first thing the package was built to prevent.
- Denials are per-file, not per-directory. A finding in `src/a.py` says nothing about `src/b.py`,
  and widening the denial would be overreach in the safe direction, which is still not evidence.
- The Codex Security port remains open. Fifteen skills and four schemas were the point of ADR-0005's
  "adopt the contracts", and none of them are here.

## Verified

`tools/verify_security_evidence.py` — PASS. Unsafe candidate `REJECT` with `B1-SEC-001-shell-true`
named; safe candidate survives; no `b1_authority` import in a fresh interpreter; allow list
unchanged; 0 of 6 probes widened; the finding's own target denied afterwards.

`python/tests/test_security.py` — 26 tests. The negative property is checked three ways (fresh
interpreter, source text, and the shape of `VerifierResult`). `test_no_finding_can_widen_a_policy`
runs six source samples against six probes and requires that nothing refused before is permitted
after. Every "it refuses" test has a control proving it does not refuse everything.
