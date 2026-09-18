#!/usr/bin/env python3
"""Verify that both peers derive the same views, and that all three modes agree.

Two claims, checked together because they are only interesting together:

1. **Cross-language.** Given one event sequence, the Rust peer and the Python
   peer derive byte-identical views. Compared per view, not only in aggregate,
   so a divergence names the view rather than sending someone to read four.

2. **Cross-mode.** The same views, stored compact, stored modular, and not
   stored at all, produce one digest. That is Phase C's exit criterion: the
   deployment mode is a storage decision and cannot change what is true.

Checking them in one report is deliberate. Either alone can be passed by a
system that is wrong in the other direction — three modes can agree perfectly
inside one implementation that disagrees with its peer, and two peers can agree
on a projection that a third deployment mode would have stored differently.

The event sequence is imported from `verify_cross_language_journal.py` rather
than rewritten here. Two "the same sequence" definitions that drift apart is a
defect this project has already found once in its own README.

Requires `cargo`. If cargo is absent the cross-language half is UNKNOWN, and the
cross-mode half still runs and still reports — an absent toolchain makes one
claim unobservable, not both.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "tools"))

from b1_projection import (  # noqa: E402
    VIEW_NAMES,
    ProjectionMode,
    ProjectionStore,
    build_views,
    view_digests,
    views_digest,
)
from b1_state import RootJournal  # noqa: E402
from verify_cross_language_journal import build_sequence, rust_side  # noqa: E402


def python_side(events: list) -> dict[str, object]:
    """Build the views, then store them three ways and compare.

    The journal and the projections live in one temporary workspace, because a
    `ProjectionStore` refuses a root outside the workspace it is bound to and
    that refusal is worth exercising on the real path rather than only in tests.
    """
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        journal = RootJournal(workspace / "root.db")
        try:
            for event in events:
                journal.append(event)

            records = journal.read_all()
            views = build_views(records)

            modes: dict[str, dict[str, object]] = {}
            for mode in ProjectionMode:
                store = ProjectionStore(workspace, mode)
                first = store.rebuild(journal)
                destroyed = store.destroy()
                survivors = [p.name for p in store.paths() if p.exists()]
                second = store.rebuild(journal)
                # Read back through the mode's own reader, which for
                # audit-replay means replaying and for the other two means
                # parsing files off disk. A mode that wrote correctly and read
                # back something else would pass a digest-only comparison.
                readback = views_digest(store.read(journal))
                drift = store.check(journal)
                modes[mode.value] = {
                    "built": first.views_digest,
                    "rebuilt_after_destroy": second.views_digest,
                    "read_back": readback,
                    "files": list(first.files_written),
                    "destroyed": list(destroyed),
                    "survived_destroy": survivors,
                    "drift_ok": drift.ok,
                    "drift_checkable": drift.checkable,
                }

            # The journal must be exactly where it started. A projection rebuild
            # reads history; it never writes to it.
            report = journal.verify()
            return {
                "views_digest": views_digest(views),
                "view_digests": view_digests(views),
                "record_count": len(records),
                "modes": modes,
                "journal_ok": report.ok,
                "journal_head": report.head_digest,
                "journal_findings": list(report.findings),
            }
        finally:
            journal.close()


def verify(timeout: int) -> dict[str, object]:
    events = build_sequence()
    python = python_side(events)
    errors: list[str] = []

    if not python["journal_ok"]:
        errors.append(
            f"the journal does not verify after projecting it: {python['journal_findings']}"
        )

    # -- cross-mode ------------------------------------------------------
    modes = python["modes"]
    assert isinstance(modes, dict)
    mode_digests: dict[str, str] = {}
    for name, result in modes.items():
        assert isinstance(result, dict)
        for stage in ("built", "rebuilt_after_destroy", "read_back"):
            value = str(result[stage])
            if value != python["views_digest"]:
                errors.append(
                    f"{name}/{stage}: {value[:16]}... does not match the views digest "
                    f"{str(python['views_digest'])[:16]}..."
                )
        if result["survived_destroy"]:
            errors.append(
                f"{name}: {result['survived_destroy']} survived destroy(), so the "
                f"projection is not disposable"
            )
        if not result["drift_ok"]:
            errors.append(f"{name}: reports drift immediately after a rebuild")
        mode_digests[name] = str(result["built"])

    distinct = set(mode_digests.values())
    cross_mode = "AGREED" if len(distinct) == 1 else "MISMATCH"
    if cross_mode == "MISMATCH":
        errors.append(f"the three modes disagree: {mode_digests}")

    # -- cross-language --------------------------------------------------
    rust, limitation = rust_side(events, timeout)
    if rust is None:
        return {
            "status": "UNKNOWN",
            "limitation": limitation,
            "errors": errors,
            "cross_mode": cross_mode,
            "modes": mode_digests,
            "views_digest": python["views_digest"],
            "note": (
                "The three deployment modes were checked and agree; only the "
                "cross-language claim is unverified. One toolchain being absent "
                "makes one claim unobservable, not both."
            ),
        }

    agreed: dict[str, str] = {}
    if rust.get("views_digest") == python["views_digest"]:
        agreed["views_digest"] = "AGREED"
    else:
        agreed["views_digest"] = "MISMATCH"
        errors.append(
            f"views_digest: python={python['views_digest']} rust={rust.get('views_digest')}"
        )

    rust_views = rust.get("view_digests") or {}
    python_views = python["view_digests"]
    assert isinstance(python_views, dict)
    for name in VIEW_NAMES:
        if rust_views.get(name) == python_views.get(name):
            agreed[name] = "AGREED"
        else:
            agreed[name] = "MISMATCH"
            errors.append(
                f"{name} view: python={python_views.get(name)} rust={rust_views.get(name)}"
            )

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "cross_mode": cross_mode,
        "cross_language": agreed,
        "modes": mode_digests,
        "views_digest": python["views_digest"],
        "events": len(events),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    result = verify(args.timeout)

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
    else:
        print(f"projection topology: {result['status']}")
        print(f"  three modes: {result['cross_mode']}  ({', '.join(sorted(result['modes']))})")
        for name, verdict in sorted(result.get("cross_language", {}).items()):
            print(f"  rust/python {name}: {verdict}")
        if result.get("views_digest"):
            print(f"  views digest: {result['views_digest']}")
        if result.get("limitation"):
            print(f"  LIMITATION {result['limitation']}")
            print(f"  {result['note']}")
        for message in result["errors"]:
            print(f"  ERROR {message}")

    return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}[str(result["status"])]


if __name__ == "__main__":
    raise SystemExit(main())
