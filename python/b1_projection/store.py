"""Three deployment modes over one logical authority.

The handoff asks for compact, modular and audit-replay deployments. The claim
worth proving is not that three modes exist — it is that **the mode is a storage
decision and cannot change what is true**. A deployment that read differently
depending on how it was packaged would have three truths, and B1's entire
premise is one.

    compact       every view in one file. Fewest inodes, one atomic replace.
    modular       one file per view, plus a manifest. A subsystem can be
                  rebuilt or shipped on its own.
    audit-replay  nothing stored. Every read replays from the journal.

The third is not a degenerate case, it is the honest baseline: it cannot drift,
because there is nothing to drift. The other two trade that impossibility for
speed, and `check()` is what buys the guarantee back.

Why a stored projection's own digest is not the check
-----------------------------------------------------
Each stored file carries the digest of what it holds, which catches a truncated
or garbled write. It does not catch an edit that also updates the digest — the
same lesson as the journal's `payload_digest`, where an internally consistent
record is still a forged one. So `check()` compares stored views against views
rebuilt from the journal, and never against the file's own claim about itself.

Writing a projection is a persistent effect
-------------------------------------------
A real one: it creates files. It is classified REVERSIBLE and does not take the
commit gate, because a projection contains nothing the journal does not already
contain — destroying it loses nothing and writing it adds nothing, so its
authority is the journal's own. That reasoning holds only while the projection
stays inside a workspace B1 owns. Writing derived state into somewhere it does
not own is an effect on someone else's storage regardless of how rebuildable the
content is, so the root is resolved inside a workspace and an escape is refused
before anything is written. See ADR-0007.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path

from b1_protocol.canonical import canonical_json_bytes, digest_bytes
from b1_state import RootJournal

from .views import VIEW_NAMES, Views, build_views, view_digests, views_digest

__all__ = [
    "ProjectionError",
    "OutsideWorkspace",
    "ProjectionMissing",
    "ProjectionMode",
    "RebuildReport",
    "DriftReport",
    "ProjectionStore",
]

# The file extension is distinctive so a stray projection is recognisable for
# what it is: derived, disposable, and never the authority.
SUFFIX = ".b1p"


class ProjectionError(Exception):
    """A projection could not be built, read or checked."""


class OutsideWorkspace(ProjectionError):
    """The projection root resolves outside the workspace it was bound to."""


class ProjectionMissing(ProjectionError):
    """A stored projection was read and is not there.

    Distinct from an empty projection, which is what a journal with no records
    produces. Absent and empty are different states, and returning an empty view
    set for a missing file would report "nothing has happened" for "nobody
    looked".
    """


class ProjectionMode(str, Enum):
    COMPACT = "compact"
    MODULAR = "modular"
    AUDIT_REPLAY = "audit-replay"


@dataclass(frozen=True, slots=True)
class RebuildReport:
    mode: ProjectionMode
    record_count: int
    views_digest: str
    view_digests: dict[str, str]
    files_written: tuple[str, ...]
    files_destroyed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DriftReport:
    """What a stored projection says versus what the journal says.

    `checkable` is False only for audit-replay, and the distinction matters:
    "no drift found" and "drift is not possible here" are different claims, and
    reporting the second as the first would be claiming an observation nobody
    made.
    """

    mode: ProjectionMode
    checkable: bool
    ok: bool
    drifted_views: tuple[str, ...]
    findings: tuple[str, ...]

    def __bool__(self) -> bool:
        return self.ok


class ProjectionStore:
    """Build, store, destroy and check a projection in one of three modes."""

    def __init__(
        self,
        workspace: Path,
        mode: ProjectionMode,
        *,
        subdir: str = "projection",
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.mode = ProjectionMode(mode)
        candidate = (self.workspace / subdir).resolve()
        try:
            candidate.relative_to(self.workspace)
        except ValueError:
            raise OutsideWorkspace(
                f"projection root {candidate} is outside the workspace "
                f"{self.workspace}. A projection is disposable; the directory it "
                f"would overwrite is not. Refusing before anything is written."
            ) from None
        self.root = candidate

    # -- layout ----------------------------------------------------------

    def paths(self) -> tuple[Path, ...]:
        """The files this mode stores. Empty for audit-replay, which is the point."""
        if self.mode is ProjectionMode.COMPACT:
            return (self.root / f"projection{SUFFIX}",)
        if self.mode is ProjectionMode.MODULAR:
            return (self.root / f"manifest{SUFFIX}",) + tuple(
                self.root / f"{name}{SUFFIX}" for name in VIEW_NAMES
            )
        return ()

    def destroy(self) -> tuple[str, ...]:
        """Delete every file this mode stores. Returns what was removed.

        Safe to call on a mode that stores nothing, and safe to call twice. A
        projection that could not be destroyed on demand would not be
        disposable, and disposable is the whole claim.
        """
        removed = []
        for path in self.paths():
            if path.exists():
                path.unlink()
                removed.append(path.name)
        return tuple(removed)

    # -- build -----------------------------------------------------------

    def rebuild(self, journal: RootJournal) -> RebuildReport:
        """Destroy whatever is stored, then rebuild it from the journal.

        Destroy-then-build rather than overwrite-in-place, so a view that a
        previous version wrote and this one no longer produces cannot survive as
        a stale file nobody reads but everybody trusts.
        """
        destroyed = self.destroy()
        records = journal.read_all()
        views = build_views(records)
        digests = view_digests(views)
        whole = views_digest(views)
        written: list[str] = []

        if self.mode is ProjectionMode.COMPACT:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / f"projection{SUFFIX}"
            path.write_bytes(
                canonical_json_bytes(
                    {
                        "mode": self.mode.value,
                        "view_digests": digests,
                        "views": views,
                        "views_digest": whole,
                    }
                )
            )
            written.append(path.name)
        elif self.mode is ProjectionMode.MODULAR:
            self.root.mkdir(parents=True, exist_ok=True)
            for name in VIEW_NAMES:
                path = self.root / f"{name}{SUFFIX}"
                path.write_bytes(canonical_json_bytes(views[name]))
                written.append(path.name)
            manifest = self.root / f"manifest{SUFFIX}"
            manifest.write_bytes(
                canonical_json_bytes(
                    {
                        "mode": self.mode.value,
                        "view_digests": digests,
                        "view_names": list(VIEW_NAMES),
                        "views_digest": whole,
                    }
                )
            )
            written.append(manifest.name)

        return RebuildReport(
            mode=self.mode,
            record_count=len(records),
            views_digest=whole,
            view_digests=digests,
            files_written=tuple(sorted(written)),
            files_destroyed=destroyed,
        )

    # -- read ------------------------------------------------------------

    def read(self, journal: RootJournal) -> Views:
        """The views this deployment would serve.

        For audit-replay that means rebuilding from the journal on every call,
        which is slow and exactly right: the mode exists for deployments that
        would rather pay per read than hold derived state at all.
        """
        if self.mode is ProjectionMode.AUDIT_REPLAY:
            return build_views(journal.read_all())

        if self.mode is ProjectionMode.COMPACT:
            path = self.root / f"projection{SUFFIX}"
            if not path.exists():
                raise ProjectionMissing(
                    f"{path} does not exist. A missing projection is not an empty "
                    f"one; rebuild it from the journal."
                )
            stored = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(stored, dict) or "views" not in stored:
                raise ProjectionMissing(f"{path} is not a compact projection")
            return dict(stored["views"])

        views: Views = {}
        for name in VIEW_NAMES:
            path = self.root / f"{name}{SUFFIX}"
            if not path.exists():
                raise ProjectionMissing(
                    f"{path} does not exist, so the {name!r} view is unreadable. "
                    f"In modular mode one missing file is a missing projection, not "
                    f"a smaller one."
                )
            views[name] = json.loads(path.read_text(encoding="utf-8"))
        return views

    # -- check -----------------------------------------------------------

    def check(self, journal: RootJournal) -> DriftReport:
        """Compare what is stored against what the journal says. Storage loses.

        Deliberately does not consult the stored `views_digest`. A file that
        recorded its own digest and had both edited together would pass that
        check and fail this one, which is the only ordering that makes the
        check worth running.
        """
        if self.mode is ProjectionMode.AUDIT_REPLAY:
            return DriftReport(
                mode=self.mode,
                checkable=False,
                ok=True,
                drifted_views=(),
                findings=(
                    "audit-replay stores no derived state, so drift is not possible "
                    "rather than not found",
                ),
            )

        expected = build_views(journal.read_all())
        expected_digests = view_digests(expected)
        try:
            stored = self.read(journal)
        except ProjectionMissing as exc:
            return DriftReport(
                mode=self.mode,
                checkable=True,
                ok=False,
                drifted_views=tuple(VIEW_NAMES),
                findings=(str(exc),),
            )

        drifted: list[str] = []
        findings: list[str] = []
        for name in VIEW_NAMES:
            if name not in stored:
                drifted.append(name)
                findings.append(f"{name}: absent from the stored projection")
                continue
            actual = digest_bytes(canonical_json_bytes(stored[name]))
            if actual != expected_digests[name]:
                drifted.append(name)
                findings.append(
                    f"{name}: stored digest {actual[:16]}... does not match the "
                    f"journal's {expected_digests[name][:16]}.... The projection "
                    f"loses; rebuild it."
                )

        extra = sorted(set(stored) - set(VIEW_NAMES))
        for name in extra:
            findings.append(
                f"{name}: stored but not a view this version produces. A file "
                f"nobody rebuilds is a file nobody checks."
            )

        return DriftReport(
            mode=self.mode,
            checkable=True,
            ok=not drifted and not extra,
            drifted_views=tuple(drifted),
            findings=tuple(findings),
        )
