# ADR-0011 — The private layer, and a second, independent publication blocker

- **Status:** Accepted, implemented, verified. Publication remains **BLOCKED** — by licensing.
- **Date:** 2026-09-18
- **Origin:** M — Phase G's second required evidence, *"a privacy scan finds no personal data or
  key material in the public tree"*. [ADR-0003](ADR-0003-durable-memory.md) put the memory key in
  `%B1_HOME%` and left the rest of the private state undefined.

## Context

ADR-0003 said the durable-memory key lives in `%B1_HOME%`, "outside the repository, alongside the
rest of the private state", and did not say what the rest of the private state *is*. That gap is
the kind that stays invisible until the moment it matters: nobody consults an undefined boundary,
they consult their memory of it, and memory is worst exactly when someone is debugging at speed and
wants to share a file.

Separately, publication is currently blocked for a licensing reason —
[ADR-0004](ADR-0004-license-and-publication.md), reported by `verify_provenance.py`. A licensing
clearance would say nothing whatever about whether the tree contains a key or someone's email
address, and it would be very easy for a clearance on one to read as a clearance overall.

## Decision

**The boundary is data, not prose.** `python/b1_privacy/boundary.py` declares two lists:

- `PRIVATE_LAYER` — what `%B1_HOME%` holds, each entry with its reason. The least obvious and most
  important is `root.db`: the root journal is B1's authoritative history, so it carries every task,
  target and payload a user ever handed the system. It is the most revealing artifact B1 produces
  *and* the one most likely to be committed by accident, because it sits in the workspace, has an
  innocuous name, and is exactly the file you want to send someone when something is wrong.
- `FORBIDDEN_IN_PUBLIC` — what may never appear in the repository, whatever its provenance.

The two are **not complements**, deliberately. `FORBIDDEN_IN_PUBLIC` is wider, because "should this
be published" has a safe default and "where does this live" does not. Every entry in both carries a
`why`: a boundary nobody can explain is a boundary someone will move.

**The privacy scan is an independent blocker that cannot clear the other one.**
`tools/verify_privacy.py` never prints the word AUTHORIZED. The strongest thing it says is
`NO_OBJECTION`, it states in its own output that it is independent of the licensing blocker and
that *neither clears the other*, and a test asserts that its vocabulary contains none of
AUTHORIZED, AUTHORISED, SAFE TO PUBLISH or CLEARED. A tree can be entirely private and still
unlawful to publish, which is precisely the current state of this repository.

**Two checks, and the second is the one people forget.** Content — ten rules over every text file.
And *placement*: some files are a problem by existing, whatever is in them. Both were shown to bite
by planting a `.pem` key block, which produced one objection of each kind on the same file.

**Coverage travels with the result.** The scan reuses `b1_security`'s `Finding`, `Coverage` and
`ScanManifest` rather than growing a second set that would drift — the requirement is identical: a
privacy scan over a partial tree must not read as clean, and `epistemic_status` is computed, not
supplied. The scan also reports its basis: `git ls-files` (what publication would actually carry)
or a filesystem walk if git is unavailable, and the difference between those two answers is exactly
the untracked file someone is about to `git add -A`.

## Redaction is a property of the line, not of the rule

The first version made redaction per-rule, which is the obvious design and is wrong. The test suite
caught it on the same day.

A database URL that carries a user and a password before the host matches
`B1-PRIV-003-credentials-in-url`, which redacts, **and** `B1-PRIV-005-email-address`, which does
not — because the password-and-host portion is address-shaped, and an address is the thing worth
showing. Per-rule redaction therefore printed the password the credentials rule had just withheld.

The reason to redact is *"this line contains a secret"*, and that does not stop being true for the
second rule that matches it. Redaction is now computed once per line across every matching rule.

Recorded as analysis finding 29, because a report that quotes the key it found has made a second
copy of the key, and that is a mistake you get to make once.

## What the first run found

Four objections, all real in the sense that the tree genuinely contained those patterns:

1. `docs/OMEGA13-ANALYSIS.md` named the working tree by its absolute container path. Fixed: it now
   says "the repository working tree". Described rather than quoted here, because quoting it would
   put it back — which is finding 31 in miniature.
2–4. Three credential-shaped literals in `python/tests/test_security.py` — fixtures built to test
   the *security* scanner's secret rule.

The fixtures deserve a note, because the fix looks like gaming the scanner and is not. They are now
assembled at run time by a `secret_shaped()` helper. The scanner under test sees exactly the same
text; the repository stops containing lines that look like credentials. **Removing the thing
reported is a different act from suppressing the report**, and only one of them is legitimate — an
exclusion list for test files would have been the other one, and `Coverage` exists across this
codebase specifically to stop that.

## Two more things the scan found, about itself

**It was looking at the wrong set of files.** The first version listed git-*tracked* files, and its
own docstring named the gap — *"the difference is precisely the untracked file someone is about to
`git add -A`"* — while the code took the narrower option anyway. It was blind at exactly the moment
a privacy scan matters, and it was blind to this increment's own new files until the basis widened
to `--cached --others --exclude-standard`. Analysis finding 30.

**Writing about a credential-shaped example puts one in the tree.** Documenting the redaction
defect above reproduced the pattern in three documents at once, and the widened scan objected to
all three. The shape is now described in prose rather than quoted, and the one test that needs a
real one assembles it at run time. Analysis finding 31 — and the reason this ADR describes the
absolute-path finding rather than repeating it.

## The one carve-out, and why it is not an exclusion list

Two files are not scanned: `python/b1_privacy/rules.py`, which defines every pattern, and
`python/tests/test_privacy.py`, which holds one positive case per rule. Scanning them would report
the rule table as a pile of secrets.

This is the thing argued against everywhere else in this codebase, so it is made the way a
defensible version has to be made: as **declared data** in `boundary.py` beside the rest of the
boundary, as two **exact paths** rather than a pattern or a directory (a carve-out that can grow by
matching will), printed in the verifier's output on every run with its reason, and carried in
`Coverage.not_scanned` — which means coverage is never complete, which means this scan reports
`WORKING_ASSUMPTION` and never `VERIFIED`. Permanently, and correctly: a scan that cannot read its
own rule table has not read the whole tree, and `Coverage` exists to say so rather than to let the
gap disappear.

## Consequences

Good:

- Publication readiness is now two independent questions with two independent answers, and neither
  can be mistaken for the other.
- The boundary is checkable. `forbidden_reason()` returns *why*, not a boolean, because "this file
  is forbidden" sends someone to read a list and "a database in the tree is user state" tells them
  what to do.
- The scan runs over what git tracks, which is what publication would carry — not over what happens
  to be on disk.

Costs:

- Ten regex rules again, with the same honesty: this finds patterns, not privacy. A photograph, a
  paraphrased conversation or a distinctive turn of phrase in a commit message are all personal
  data and none of them match a regex.
- The scan covers the **working tree**, not git history. A secret removed in a later commit is
  still in the objects, and removing it needs a history rewrite. That is out of scope here and is
  the more common real-world failure.
- `%B1_HOME%` itself is declared and not implemented. No code reads `B1_HOME` yet; the journal
  still lives wherever a caller points it. The boundary is now written down and checked against the
  public tree, which is the half that blocks publication — the half that relocates runtime state is
  Phase G proper.

## Verified

`tools/verify_privacy.py` — PASS: `NO_OBJECTION` over 10 rules on 154 files, 2 not scanned, and
therefore `WORKING_ASSUMPTION` rather than `VERIFIED` — permanently, because the two files it
cannot read are the ones defining what it reads for. Shown to bite: a planted `.pem` produces a
placement objection and a content objection, independently, and the content objection shows no
evidence because the rule redacts.

`python/tests/test_privacy.py` — 23 tests. Each of nine rules fires on its own case, none fires on
clean source, loopback and RFC1918 addresses are not flagged, redaction is checked including the
two-rule overlap above, and four tests assert that the verifier's vocabulary cannot express an
authorisation — one of them by running `verify_provenance.py` and requiring that publication is
still `BLOCKED`.
