#!/usr/bin/env python3
"""Verify that B1 Local's provenance record is complete and self-consistent.

This verifier exists because "we did not copy anything" is a claim, and a claim
without a check is a working assumption, not a verified fact. It enforces four
things that can actually be observed from the tree:

1.  Every upstream named in provenance.json is also documented in
    THIRD_PARTY_NOTICES.md, with its SPDX identifier present.
2.  Every file listed in derived_files exists and carries a
    ``Design-derived-from: <upstream_id>:<upstream_path>`` marker that matches
    the manifest. A manifest entry that drifts away from the file it describes
    is a provenance defect, not a formatting nit.
3.  Every file in the tree that carries a Design-derived-from marker is listed
    in derived_files. This is the direction that actually prevents undeclared
    copying: a new derived file cannot be added without declaring it.
4.  Any upstream whose license forbids redistribution is reported as a
    publication blocker, and its blocker note is carried through rather than
    summarised away.

Exit code 0 means the record is consistent. It does NOT mean publication is
authorised; see the PUBLICATION line in the output.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MARKER = re.compile(r"Design-derived-from:\s*([A-Za-z0-9._-]+):(\S+)")

# Extensions worth scanning for markers. Binary and vendored trees are skipped.
SCANNED_SUFFIXES = {
    ".py", ".rs", ".ts", ".js", ".go", ".c", ".cpp", ".h", ".hpp", ".java",
    ".kt", ".swift", ".php", ".rb", ".cs", ".dart", ".md", ".json", ".toml",
}
SKIPPED_DIRS = {".git", "target", "node_modules", "__pycache__", ".venv"}


def iter_source_files(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if any(part in SKIPPED_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def verify(root: Path) -> dict[str, object]:
    errors: list[str] = []
    blockers: list[dict[str, str]] = []

    manifest_path = root / "provenance" / "provenance.json"
    if not manifest_path.is_file():
        return {"status": "FAIL", "errors": ["provenance/provenance.json is missing"],
                "publication": "BLOCKED", "blockers": [], "derived_file_count": 0}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    notices_path = root / "THIRD_PARTY_NOTICES.md"
    notices = notices_path.read_text(encoding="utf-8") if notices_path.is_file() else ""
    if not notices:
        errors.append("THIRD_PARTY_NOTICES.md is missing or empty")

    upstreams = {str(item["id"]): item for item in manifest.get("upstreams", [])}
    if not upstreams:
        errors.append("provenance.json declares no upstreams")

    # (1) every upstream is documented, with its SPDX id present.
    for upstream_id, upstream in upstreams.items():
        name = str(upstream.get("name", ""))
        spdx = str(upstream.get("spdx", ""))
        if name and name not in notices:
            errors.append(f"upstream {upstream_id!r}: name {name!r} absent from THIRD_PARTY_NOTICES.md")
        if spdx and spdx not in notices:
            errors.append(f"upstream {upstream_id!r}: SPDX {spdx!r} absent from THIRD_PARTY_NOTICES.md")
        if upstream.get("vendored"):
            errors.append(
                f"upstream {upstream_id!r} is marked vendored; this increment vendors nothing, "
                "so either the claim or the tree is wrong"
            )
        if not upstream.get("redistribution_permitted", True):
            blockers.append({
                "upstream": upstream_id,
                "license": str(upstream.get("license", "")),
                "note": str(upstream.get("blocker_note", "")),
            })

    # (2) every declared derived file exists and its marker matches the manifest.
    declared: dict[str, tuple[str, str]] = {}
    for entry in manifest.get("derived_files", []):
        rel = str(entry.get("path", ""))
        upstream_id = str(entry.get("upstream_id", ""))
        upstream_path = str(entry.get("upstream_path", ""))
        declared[rel] = (upstream_id, upstream_path)

        if upstream_id not in upstreams:
            errors.append(f"{rel}: declares unknown upstream {upstream_id!r}")
        target = root / rel
        if not target.is_file():
            errors.append(f"{rel}: declared in provenance.json but does not exist")
            continue
        found = MARKER.findall(target.read_text(encoding="utf-8", errors="replace"))
        if not found:
            errors.append(f"{rel}: missing 'Design-derived-from: {upstream_id}:{upstream_path}' marker")
        elif (upstream_id, upstream_path) not in found:
            errors.append(
                f"{rel}: marker disagrees with provenance.json; "
                f"manifest says {upstream_id}:{upstream_path}, file says "
                + ", ".join(f"{a}:{b}" for a, b in found)
            )

    # (3) no undeclared derived file may exist in the tree.
    for path in iter_source_files(root):
        rel = str(path.relative_to(root))
        if rel == "tools/verify_provenance.py":
            continue  # this file documents the marker format; it is not itself derived
        found = MARKER.findall(path.read_text(encoding="utf-8", errors="replace"))
        if found and rel not in declared:
            errors.append(
                f"{rel}: carries a Design-derived-from marker but is not listed in "
                "provenance.json derived_files"
            )

    return {
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "publication": "BLOCKED" if blockers else "NOT_AUTHORIZED",
        "blockers": blockers,
        "derived_file_count": len(declared),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = verify(Path(args.root).resolve())

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"provenance record: {result['status']} "
              f"({result['derived_file_count']} derived files declared)")
        for message in result["errors"]:
            print(f"  ERROR {message}")
        print(f"PUBLICATION: {result['publication']}")
        for blocker in result["blockers"]:
            print(f"  BLOCKER {blocker['upstream']}: {blocker['license']}")
            if blocker["note"]:
                print(f"          {blocker['note']}")
        if not result["blockers"]:
            print("  No redistribution blocker found, but publication still requires "
                  "separate explicit authorization.")
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
