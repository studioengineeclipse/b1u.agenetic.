"""Where the line is between the public tree and the per-user private layer.

ADR-0003 put the durable-memory key in `%B1_HOME%`, "outside the repository,
alongside the rest of the private state", and left *the rest of the private
state* undefined. This module defines it, as data rather than as prose, so a
verifier can check the boundary instead of a person remembering it.

Two lists, and the asymmetry between them is the design
-------------------------------------------------------
`PRIVATE_LAYER` says what lives in `%B1_HOME%`. `FORBIDDEN_IN_PUBLIC` says what
may never appear in the repository. They are not complements: the second is
deliberately wider, because the question "should this be published" has a safe
default and the question "where does this live" does not.

A file can be absent from both lists and still be fine. A file matching
`FORBIDDEN_IN_PUBLIC` is never fine, wherever it came from.

Why a journal is private
------------------------
`root.db` is the least obvious entry and the most important one. The root
journal is B1's authoritative history: every task, every target, every payload a
user ever handed it. It is the single most revealing artifact the system
produces, and it is also the one a developer is most likely to commit by
accident while debugging -- it sits in the workspace, it has an innocuous name,
and it is exactly the shape of a file you want to share when something is wrong.
"""
from __future__ import annotations

__all__ = [
    "B1_HOME_VARIABLE",
    "PRIVATE_LAYER",
    "FORBIDDEN_IN_PUBLIC",
    "SELF_REFERENTIAL",
    "forbidden_reason",
    "self_referential_reason",
]

B1_HOME_VARIABLE = "B1_HOME"

# What the per-user layer holds. Each entry is (glob, why), and the `why`
# matters as much as the glob: a boundary nobody can explain is a boundary
# someone will move.
PRIVATE_LAYER: tuple[tuple[str, str], ...] = (
    ("root.db", "the authoritative journal: every task, target and payload the user gave B1"),
    ("root.db-wal", "SQLite write-ahead log for the journal; the same content, mid-flight"),
    ("root.db-shm", "SQLite shared-memory index for the journal"),
    ("projection/**", "derived views over the journal, so exactly as revealing as the journal"),
    ("memory.enc", "durable memory, encrypted at rest per ADR-0003"),
    ("memory.key", "the key for the above. Never anywhere else, ever"),
    ("policy.json", "the capability policy in force, which describes the user's machine"),
    ("models/**", "downloaded weights: large, licensed, and not this repository's to carry"),
    ("workspace/**", "the directory B1's effects actually write into"),
    ("logs/**", "run logs, which quote payloads verbatim"),
)

# What must never appear in the public tree, whatever its provenance. Wider than
# the complement of PRIVATE_LAYER on purpose: publication has a safe default and
# file placement does not.
FORBIDDEN_IN_PUBLIC: tuple[tuple[str, str], ...] = (
    ("*.db", "a database in the tree is user state; the journal is the likely one"),
    ("*.sqlite", "as above"),
    ("*.sqlite3", "as above"),
    ("*.db-wal", "a write-ahead log carries the same content as its database"),
    ("*.db-shm", "as above"),
    ("*.key", "key material"),
    ("*.pem", "key or certificate material"),
    ("*.p12", "key material"),
    ("*.pfx", "key material"),
    ("id_rsa", "an SSH private key"),
    ("id_ed25519", "an SSH private key"),
    (".env", "environment files hold credentials by convention"),
    (".env.*", "as above"),
    ("*.enc", "encrypted user data still identifies that the user had data"),
    ("b1_home/**", "the private layer itself, if someone ever puts it here"),
    (".b1/**", "as above, under its dot-directory spelling"),
)


# Files that define what the scan looks for, and therefore contain one example
# of everything it looks for. Excluding them is the one carve-out in this
# module, and it is made this way rather than as a filter inside the scanner so
# that it is reviewable data sitting beside the rest of the boundary.
#
# The cost is paid rather than hidden: these files go into `Coverage.not_scanned`
# with their reason, which means coverage is never complete, which means a
# privacy scan of this repository reports WORKING_ASSUMPTION and not VERIFIED --
# permanently, and correctly. A scan that cannot read its own rule table has not
# read the whole tree, and `Coverage` exists to say so rather than to let the
# gap disappear.
#
# Two entries, both exact paths. Not a pattern, not a directory: a carve-out
# that could grow by matching is a carve-out that will.
SELF_REFERENTIAL: tuple[tuple[str, str], ...] = (
    ("python/b1_privacy/rules.py",
     "defines every pattern the scan looks for, so it contains one of each"),
    ("python/tests/test_privacy.py",
     "holds one positive case per rule, which is how the rules are shown to fire"),
)


def self_referential_reason(path: str) -> str | None:
    """Why this file defines the scan rather than being subject to them."""
    for name, reason in SELF_REFERENTIAL:
        if path == name:
            return reason
    return None


def _matches(pattern: str, path: str) -> bool:
    """Match a path against one boundary glob.

    Handles `dir/**` as a prefix and `*.ext` as a suffix on the basename, and
    otherwise compares the basename exactly. Deliberately small, and
    deliberately not `fnmatch`: the same reasoning as `b1_protocol.scope`, where
    a `*` that crosses `/` turns a narrow rule into a wide one without looking
    any different.
    """
    if pattern.endswith("/**"):
        prefix = pattern[:-2]
        return path.startswith(prefix) or f"/{prefix}" in path
    basename = path.rsplit("/", 1)[-1]
    if pattern.startswith("*."):
        return basename.endswith(pattern[1:])
    if pattern.endswith(".*"):
        return basename.startswith(pattern[:-1])
    return basename == pattern


def forbidden_reason(path: str) -> str | None:
    """Why this path may not be published, or ``None``.

    Returns the reason rather than a boolean, because "this file is forbidden"
    sends someone to read a list and "a database in the tree is user state"
    tells them what to do about it.
    """
    for pattern, reason in FORBIDDEN_IN_PUBLIC:
        if _matches(pattern, path):
            return f"{path} matches {pattern!r}: {reason}"
    return None
