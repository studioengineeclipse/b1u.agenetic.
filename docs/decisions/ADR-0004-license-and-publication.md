# ADR-0004 — License for new B1 Local code, and the publication blocker

- **Status:** Accepted (license choice). Publication remains `PLAN_READY — AUTHORIZATION REQUIRED`.
- **Date:** 2026-09-16
- **Origin:** U — the user selected Apache-2.0 when asked directly.

## Context

Three bodies of material meet in this project, and they do not share a licence.

| Work | Licence | Redistributable? |
|---|---|---|
| OpenAI Codex | Apache-2.0 | yes |
| OpenAI Codex Security | Apache-2.0 | yes |
| B1u-DQAS OmegaSigma13.9 | **All rights reserved** | **no** |

Verified by reading each archive's `LICENSE` on 2026-09-16. The OmegaSigma13.9 text is explicit:
"No patent, copyright, trademark, redistribution, sublicensing, or commercial-use license is granted
by this file."

The handoff lists a public reusable repository as a goal and correctly identifies this as a blocker.
It is worth being precise about *which* blocker, because two different questions were being run
together:

1. What licence does **new B1 Local code** carry? — a free choice, answerable now.
2. May the **OmegaSigma13.9 archive** be published or relicensed? — not ours to answer.

## Decision

**New B1 Local code is Apache-2.0.** See `LICENSE`.

Apache-2.0 was chosen over MIT because both upstreams are Apache-2.0, so obligations compose without
a licence-compatibility argument: one `LICENSE`, one `NOTICE`, one modified-files discipline. It
also carries an explicit patent grant, which MIT does not.

**This does not relicense the OmegaSigma13.9 archive.** That archive stays all-rights-reserved. No
part of it is vendored into this repository, and none may be. Where B1 code derives a *design* from
it, the derivation is declared in `provenance/provenance.json` and marked in the file itself with a
`Design-derived-from:` comment.

## Consequences

- `tools/verify_provenance.py` reports `PUBLICATION: BLOCKED` for as long as any upstream is marked
  `redistribution_permitted: false`. That line is not decoration: it is the machine-checkable form
  of this ADR.
- A design idea is not a copyrightable expression, but the boundary is not always obvious. The
  `Design-derived-from:` marker plus the manifest makes each claimed derivation reviewable by a
  human who can judge where that boundary falls, rather than leaving it implicit.
- The verifier also fails if a file in the tree carries a derivation marker without being declared.
  That is the direction that matters: it prevents an undeclared copy from arriving quietly later.
- No trademark is inherited from either grant. Apache-2.0 §6 explicitly withholds trademark rights,
  so "Codex", "OpenAI" and their marks are not usable as B1 branding regardless of the code licence.

## What is still not authorized

Choosing a licence is not permission to publish. Creating a public repository, pushing to one,
or distributing any artifact remains a separate persistent effect requiring its own explicit
authorization at the time it happens. Relicensing OmegaSigma13.9 is the rights holder's decision and
has not been made.
