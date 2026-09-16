"""Canonical serialization for B1 Local.

Design-derived-from: b1mu-omega13.9:src/b1mu/serialization.py

The rule is taken unchanged from OmegaSigma13.9, because it is already proven to
produce byte-reproducible journals: keys sorted, non-ASCII emitted raw,
separators with no whitespace, NaN and Infinity rejected, one trailing newline,
UTF-8.

One rule is *added*, and it is the reason this module exists separately rather
than being a thin wrapper.

    Floats are rejected inside digested values.

Python renders a float through ``repr()``; Rust's ``serde_json`` renders it
through Ryu. Both produce a shortest round-tripping representation, but they do
not always choose the same one. A single disagreement anywhere in a payload
produces different canonical bytes, therefore a different digest, therefore a
root journal whose Rust and Python peers silently disagree about history. That
is precisely the failure the cross-language digest equality is supposed to rule
out, so it is rejected at the door instead of being discovered later.

``None`` is rejected for a related reason: after canonicalisation an absent key
and an explicit null are indistinguishable in several of the fourteen target
languages, so permitting null would make the canonical form ambiguous.

Numerics that need fractional precision are carried as decimal strings, which
every one of the fourteen languages can compare byte-for-byte without adopting a
floating-point printing algorithm.
"""
from __future__ import annotations

import hashlib
import json

__all__ = [
    "CanonicalizationError",
    "canonical_json_bytes",
    "digest_bytes",
    "digest_value",
]

# Sort order note: Python's sort_keys sorts str by Unicode code point, and Rust's
# BTreeMap<String, _> sorts by UTF-8 byte order. UTF-8 is constructed so that
# byte-wise ordering equals code-point ordering, so the two agree. Vector
# envelope-002-unicode-keys exists to prove that rather than assume it.
_JSON_ARGS = {
    "ensure_ascii": False,
    "sort_keys": True,
    "separators": (",", ":"),
    "allow_nan": False,
}


class CanonicalizationError(ValueError):
    """A value cannot be canonicalised without risking cross-language divergence."""


def _check(value: object, path: str) -> None:
    """Reject anything whose canonical bytes are not reproducible everywhere."""
    # bool must be tested before int: in Python bool is a subclass of int, and
    # letting True fall through to the int branch would be harmless here but
    # would hide the distinction from anyone reading this function.
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        return
    if isinstance(value, str):
        return
    if isinstance(value, float):
        raise CanonicalizationError(
            f"{path}: float values are not digestable. Python renders floats via repr() "
            f"and Rust serde_json via Ryu; these can disagree, which would break "
            f"cross-language digest equality. Use an integer, or a decimal string."
        )
    if value is None:
        raise CanonicalizationError(
            f"{path}: null is not digestable. After canonicalisation an absent key and an "
            f"explicit null are indistinguishable in several target languages. Omit the key."
        )
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(
                    f"{path}: object keys must be strings, got {type(key).__name__}"
                )
            if not key:
                raise CanonicalizationError(f"{path}: object keys must be non-empty")
            _check(item, f"{path}.{key}")
        return
    raise CanonicalizationError(
        f"{path}: {type(value).__name__} is not digestable; "
        f"use a string, integer, boolean, array or object"
    )


def canonical_json_bytes(value: object) -> bytes:
    """Return the one canonical byte encoding of ``value``.

    Raises ``CanonicalizationError`` if the value contains anything that cannot
    be encoded identically by every B1 language peer.
    """
    _check(value, "$")
    text = json.dumps(value, **_JSON_ARGS)
    return (text + "\n").encode("utf-8")


def digest_bytes(data: bytes) -> str:
    """SHA-256 of raw bytes, lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def digest_value(value: object) -> str:
    """SHA-256 over the canonical bytes of ``value``, lowercase hex."""
    return digest_bytes(canonical_json_bytes(value))
