"""Findings can narrow what B1 may do. They can never widen it.

This module is the structural half of the handoff's Phase 7 exit criterion --
*"without independently granting mutation authority"*. The convention is easy to
state and easy to erode, so it is enforced by construction instead:

* `denials_from()` returns `Capability` objects that only ever appear in a
  policy's **deny** list. There is no code path here that produces an `allow`.
* `tighten()` copies the input policy's `allow` tuple through **unchanged** and
  adds to `deny`. It cannot widen a policy because it never touches the field
  that would.
* This package does not import `b1_authority` at all. A finding cannot reach an
  authority envelope, a permit or the gate, because the objects are not in
  scope. `test_security.py` asserts that import graph, so the guarantee survives
  someone later deciding a small exception would be convenient.

Why tightening is allowed at all
--------------------------------
A security subsystem that could only report would leave every response to a
finding as a human action, and the response to "this path writes secrets" is
always the same: stop writing there. Letting findings add denies makes that
automatic in the one direction that cannot cause harm -- the worst case of an
over-eager denial is that B1 refuses work it could have done, and asks.

The worst case in the other direction is B1 doing something nobody permitted,
which is why the other direction does not exist.
"""
from __future__ import annotations

from b1_policy import Capability, CapabilityPolicy

from .finding import Finding, ScanManifest

__all__ = ["denials_from", "tighten", "TighteningReport"]


def _target_scope(target: str) -> tuple[str, ...]:
    """The narrowest scope that covers one finding's target.

    Exactly the file, not its directory. A finding in `src/a.py` says nothing
    about `src/b.py`, and widening the denial to the directory would be the
    same overreach in miniature that this module exists to prevent -- it would
    just be overreach in the safe direction, which is still not evidence.
    """
    return (target,)


def denials_from(findings: tuple[Finding, ...], *, action: str = "fs.write") -> tuple[Capability, ...]:
    """Deny capabilities implied by blocking findings. Never allows.

    One denial per distinct target carrying a blocking finding, naming the rule
    and the severity in its reason so a later reader can tell why the policy is
    shaped the way it is without going back to the scan.

    Note the return type. Every `Capability` this returns is destined for a
    policy's `deny` list, and `tighten()` is the only caller in B1 -- but even a
    caller who put one in `allow` would be widening the policy under its own
    name, which is a different and visible act.
    """
    by_target: dict[str, list[Finding]] = {}
    for finding in findings:
        if finding.blocks:
            by_target.setdefault(finding.target, []).append(finding)

    denials: list[Capability] = []
    for target in sorted(by_target):
        reasons = sorted({f"{f.rule_id} ({f.severity})" for f in by_target[target]})
        denials.append(Capability(
            action=action,
            scope=_target_scope(target),
            # A deny ignores the ceiling, so this value is inert here. Set to
            # the narrowest anyway: a reader should not have to know that to
            # read the denial correctly.
            max_persistence_class="REVERSIBLE",
            reason=f"blocking security finding(s): {', '.join(reasons)}",
        ))
    return tuple(denials)


class TighteningReport:
    """What a tightening changed, and the proof that it only narrowed.

    Carries both policies so a caller can check the claim rather than take it.
    `allow_unchanged` is computed by comparing the tuples, not asserted.
    """

    __slots__ = ("before", "after", "added")

    def __init__(self, before: CapabilityPolicy, after: CapabilityPolicy,
                 added: tuple[Capability, ...]) -> None:
        self.before = before
        self.after = after
        self.added = added

    @property
    def allow_unchanged(self) -> bool:
        return self.before.allow == self.after.allow

    @property
    def changed(self) -> bool:
        return self.before.digest() != self.after.digest()

    def summary(self) -> str:
        if not self.changed:
            return "no blocking findings; the policy is unchanged"
        return (
            f"{len(self.added)} denial(s) added from blocking findings; "
            f"allow list unchanged ({len(self.after.allow)} entries)"
        )


def tighten(
    policy: CapabilityPolicy,
    manifest: ScanManifest,
    *,
    action: str = "fs.write",
    policy_id: str | None = None,
) -> TighteningReport:
    """A policy narrowed by a scan's blocking findings.

    `allow` is passed through as the same tuple object the input carried. That
    is the whole mechanism: there is no branch in this function that could
    produce a different allow list, so "a finding cannot widen the policy" is a
    property of the code rather than a rule someone has to remember.

    The result is a new policy with a new digest, which means adopting it makes
    every outstanding authorization stale -- ADR-0008's property doing exactly
    what it was built for, on a narrowing this time.
    """
    additions = denials_from(manifest.blocking, action=action)
    if not additions:
        return TighteningReport(policy, policy, ())

    existing = {(c.action, c.scope) for c in policy.deny}
    fresh = tuple(c for c in additions if (c.action, c.scope) not in existing)
    if not fresh:
        return TighteningReport(policy, policy, ())

    narrowed = CapabilityPolicy(
        policy_id=policy_id or f"{policy.policy_id}+scan",
        allow=policy.allow,          # the same tuple, deliberately
        deny=policy.deny + fresh,
        description=(
            f"{policy.description} Narrowed by {manifest.evidence_ref()}."
        ).strip(),
    )
    return TighteningReport(policy, narrowed, fresh)
