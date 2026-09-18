"""Effect executors: the only code in B1 that touches the world.

Everything else in this repository decides, records or refuses. This module
acts. That is why it is small, why each executor is a few methods, and why
every one of them must answer three questions separately:

    observe()  -- what is the state right now?
    apply()    -- do the thing, and report what the attempt returned
    verify()   -- read the state back and say whether the objective holds

The three are kept apart because collapsing them is the failure this whole
project is built around. `apply` returning without raising is a *receipt*. Only
`verify` can say the world changed, and only by looking.

The `observe` method carries more weight than it appears to. Its digest is what
an authority envelope is bound to, so a target that changed between
authorization and execution produces a different digest and the gate refuses.
That makes staleness a real property of the filesystem rather than a simulated
one — the authority is bound to the world, not to a description of it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from b1_protocol.canonical import digest_value

__all__ = [
    "EffectError",
    "OutsideScope",
    "Attempt",
    "EffectExecutor",
    "FileWriteEffect",
]


class EffectError(Exception):
    """An effect could not be attempted."""


class OutsideScope(EffectError):
    """The target resolves outside the workspace it was bound to.

    Raised before anything is attempted. A path that escapes its root via `..`
    or a symlink is the difference between writing a note and writing somewhere
    that matters, and the authority envelope's scope cannot catch it alone: the
    string can look fine and the resolved path not be.
    """


@dataclass(frozen=True, slots=True)
class Attempt:
    """What an execution attempt returned. A receipt, nothing more."""

    # SUCCEEDED / FAILED / UNKNOWN. UNKNOWN is for an attempt whose outcome
    # genuinely is not known — a process killed mid-write, a call that timed
    # out. It is not a synonym for failure, and treating it as one is what
    # produces a blind retry.
    status: str
    detail: str = ""


class EffectExecutor(Protocol):
    """What B1 requires of anything that can change the world."""

    action: str

    def observe(self, target: str) -> str:
        """Digest the current state of ``target``.

        Must be stable for unchanged state and must differ for changed state.
        Everything about staleness detection rests on this.
        """
        ...

    def apply(self, target: str, payload: str) -> Attempt:
        """Attempt the effect. The return value is a receipt, not proof."""
        ...

    def verify(self, target: str, payload: str) -> tuple[bool, str]:
        """Read the state back. Returns ``(objective_holds, observed_digest)``."""
        ...


class FileWriteEffect:
    """Write text to a file inside a workspace root.

    Classified REVERSIBLE: the previous content is captured before the write, so
    the original state can be restored. Worth being precise about what that
    does and does not mean — it restores *the file*. It does not unwind anything
    that read the file while the new content was live. Recovery of the artifact
    is not recovery of its consequences, and calling this REVERSIBLE is a claim
    about the first only.
    """

    action = "fs.write"

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._previous: dict[str, str | None] = {}

    def _resolve(self, target: str) -> Path:
        """Resolve inside the root, or refuse.

        `Path.resolve()` follows symlinks, so this catches both `../` escapes
        and a symlink pointing out of the workspace. Checked on the resolved
        path rather than the string, because a string that looks contained and
        a path that is contained are different things.
        """
        candidate = (self.root / target).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise OutsideScope(
                f"{target!r} resolves to {candidate}, which is outside the workspace "
                f"root {self.root}. Refusing before anything is attempted."
            ) from None
        return candidate

    def observe(self, target: str) -> str:
        """Digest the file's current content, or its absence.

        Absence is a state, not an error: `{"exists": False}` digests to
        something stable and distinct, so authorizing a write against a missing
        file and then finding one there is correctly detected as staleness.
        """
        path = self._resolve(target)
        if not path.exists():
            return digest_value({"exists": False, "path": target})
        return digest_value(
            {"exists": True, "path": target, "content": path.read_text(encoding="utf-8")}
        )

    def apply(self, target: str, payload: str) -> Attempt:
        path = self._resolve(target)
        # Capture the prior state first. A rollback that had to reconstruct what
        # was there would be guessing.
        self._previous[target] = (
            path.read_text(encoding="utf-8") if path.exists() else None
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            # A write that failed partway through may still have changed the
            # file. The caller must not assume nothing happened, and `verify`
            # is what settles it.
            return Attempt(status="UNKNOWN", detail=f"write failed partway: {exc}")
        return Attempt(status="SUCCEEDED", detail=f"wrote {len(payload)} characters")

    def verify(self, target: str, payload: str) -> tuple[bool, str]:
        """Read back and compare. This is the only thing that can say it worked."""
        observed = self.observe(target)
        path = self._resolve(target)
        holds = path.exists() and path.read_text(encoding="utf-8") == payload
        return holds, observed

    def rollback(self, target: str) -> bool:
        """Restore the captured prior state. Returns whether anything was restored.

        Itself a persistent effect, so a caller putting this behind the gate is
        doing the right thing. It is offered unwrapped here because recovery
        that cannot run without an authority round-trip is recovery that will
        not run in the case that needs it most.
        """
        if target not in self._previous:
            return False
        path = self._resolve(target)
        previous = self._previous.pop(target)
        if previous is None:
            if path.exists():
                path.unlink()
            return True
        path.write_text(previous, encoding="utf-8")
        return True
