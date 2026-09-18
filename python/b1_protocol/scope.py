"""Target-scope matching, defined once for everything that bounds a target.

This lives in `b1_protocol` rather than in either of its two callers because
both the authority envelope and the capability policy bound what a target may
be, and two implementations of "inside the scope" that drift apart would mean a
policy and an authorization disagreeing about the same string. There is one
matcher, and both import it.

The grammar is deliberately tiny:

    "docs/notes.md"   exactly that target
    "docs/**"         anything at or beneath docs/
    "**"              everything

Deliberately **not** `fnmatch`. Its `*` crosses `/`, so `docs/*` would match
`docs/secrets/key` -- which is the single mistake a scope field exists to
prevent. A pattern language that is too small to express a mistake is worth more
here than one that is expressive enough to make it.

`**` admits everything and has to be written out, so granting unlimited scope is
a visible act rather than something that happens by omission.
"""
from __future__ import annotations

__all__ = ["scope_admits", "scope_is_valid"]


def scope_admits(scope: tuple[str, ...] | list[str], target: str) -> bool:
    """Is ``target`` inside ``scope``?

    A prefix entry `docs/**` keeps its trailing slash when compared, so
    `docs/**` admits `docs/a.md` and does *not* admit `docsecret/a.md`. Dropping
    the slash would turn a directory scope into a string prefix, which is a
    different and much wider thing.
    """
    for entry in scope:
        if entry == "**":
            return True
        if entry.endswith("/**"):
            prefix = entry[:-2]  # keep the trailing slash
            if target.startswith(prefix):
                return True
        elif entry == target:
            return True
    return False


def scope_is_valid(scope: tuple[str, ...] | list[str]) -> str | None:
    """Why this scope is unusable, or ``None``.

    An empty scope is rejected rather than treated as "everything" or as
    "nothing". Both readings are defensible, which is the problem: a field whose
    empty value has two plausible meanings will eventually be read with the
    wrong one.
    """
    if not scope:
        return (
            "scope must be non-empty; unlimited scope is written as ('**',) so that "
            "granting it is a visible act rather than an omission"
        )
    for entry in scope:
        if not isinstance(entry, str) or not entry:
            return f"scope entry {entry!r} is not a non-empty string"
        if "*" in entry and entry != "**" and not entry.endswith("/**"):
            return (
                f"scope entry {entry!r} uses '*' somewhere this matcher does not "
                f"interpret it. The only wildcards are a trailing '/**' and a bare "
                f"'**'; anything else would read as a glob and silently match less, "
                f"or more, than it appears to"
            )
    return None
