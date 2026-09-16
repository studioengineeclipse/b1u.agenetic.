#!/usr/bin/env python3
"""Regenerate conformance/vectors/ from the Python reference implementation.

The vectors are committed, not generated at test time: every other language peer
is checked *against* the committed bytes, so if this script and the committed
vectors ever disagree that disagreement must be visible in a diff.

Run after any intentional change to the canonical form, and expect the diff to
be reviewed rather than rubber-stamped.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_protocol import Envelope  # noqa: E402
from b1_protocol.canonical import (  # noqa: E402
    CanonicalizationError,
    canonical_json_bytes,
    digest_bytes,
)

VECTORS = ROOT / "conformance" / "vectors"


def emit(name: str, envelope: Envelope, note: str) -> None:
    canonical = envelope.canonical_bytes()
    (VECTORS / f"{name}.canonical").write_bytes(canonical)
    (VECTORS / f"{name}.json").write_text(
        json.dumps(envelope.to_canonical_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (VECTORS / f"{name}.expected.json").write_text(
        json.dumps(
            {
                "vector": name,
                "note": note,
                "canonical_sha256": digest_bytes(canonical),
                "canonical_byte_length": len(canonical),
                "payload_digest": envelope.payload_digest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"{name}: {digest_bytes(canonical)} ({len(canonical)} bytes)")


def main() -> int:
    VECTORS.mkdir(parents=True, exist_ok=True)

    # 001 — an ordinary non-effect event. The baseline every language must match.
    emit(
        "envelope-001",
        Envelope.new(
            event_id="evt-001",
            origin="U",
            program_identity="b1-local",
            execution_identity="python-core-1",
            attempt_identity="attempt-1",
            epoch=1,
            epistemic_status="WORKING_ASSUMPTION",
            payload={"kind": "plan.accepted", "phase": 1, "reversible": True},
            causal_parents=(),
            evidence_refs=("doc:handoff#section-19",),
        ),
        "Baseline non-effect envelope. Establishes key ordering, integer and boolean "
        "rendering, and the trailing newline.",
    )

    # 002 — non-ASCII keys and values. Proves that Python's code-point key sort and
    # Rust's UTF-8 byte-order key sort agree, rather than assuming they do.
    emit(
        "envelope-002-unicode-keys",
        Envelope.new(
            event_id="evt-002",
            origin="M",
            program_identity="b1-local",
            execution_identity="rust-core-1",
            attempt_identity="attempt-1",
            epoch=2,
            epistemic_status="UNKNOWN",
            payload={
                "z": "ascii-last",
                "é": "e-acute",
                "ü": "u-umlaut",
                "Ω": "omega",
                "中文": "han",
                "\U0001f9ee": "abacus-outside-bmp",
                "a": "ascii-first",
            },
            causal_parents=("evt-001",),
            evidence_refs=(),
        ),
        "Non-ASCII keys spanning Latin-1, Greek, Han and a non-BMP codepoint. "
        "Key order must be identical under Python sort_keys (code point) and Rust "
        "BTreeMap (UTF-8 byte order), and non-ASCII must be emitted raw, not escaped.",
    )

    # 003 — an effect envelope. Carries the three fields that an effect record
    # cannot legally omit.
    emit(
        "envelope-003-effect",
        Envelope.new(
            event_id="evt-003",
            origin="U",
            program_identity="b1-local",
            execution_identity="rust-core-1",
            attempt_identity="attempt-2",
            epoch=3,
            epistemic_status="IN_DOUBT",
            payload={"kind": "fs.write", "target": "docs/provenance.md", "bytes": 4096},
            causal_parents=("evt-001", "evt-002"),
            evidence_refs=("receipt:fs-write-394",),
            effect_identity="fs-write-394",
            authority_envelope_digest="c" * 64,
            persistence_class="REVERSIBLE",
        ),
        "Effect envelope. Demonstrates that effect_identity drags "
        "authority_envelope_digest and persistence_class in with it, and that "
        "IN_DOUBT survives a successful receipt.",
    )

    # 004 — the rejection vector. No .canonical file exists for it, because the
    # point is that canonical bytes cannot be produced at all.
    rejected: list[dict[str, str]] = []
    for label, value in (
        ("float", {"ratio": 0.1}),
        ("nested_float", {"outer": [1, 2, {"inner": 3.5}]}),
        ("null", {"maybe": None}),
        ("non_string_key", {1: "int-key"}),
        ("empty_key", {"": "empty"}),
    ):
        try:
            canonical_json_bytes(value)
        except CanonicalizationError as exc:
            rejected.append({"case": label, "error": str(exc).split(":", 1)[-1].strip()})
        else:  # pragma: no cover - a pass here is the defect
            raise SystemExit(f"vector 004: {label} was accepted but must be rejected")

    (VECTORS / "envelope-004-rejected.expected.json").write_text(
        json.dumps(
            {
                "vector": "envelope-004-rejected",
                "note": "Values that must NOT produce canonical bytes in any language. "
                        "A language peer that accepts any of these is non-conformant.",
                "must_reject": rejected,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"envelope-004-rejected: {len(rejected)} cases must be refused")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
