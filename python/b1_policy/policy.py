"""The standing capability policy: what B1 may be authorized to do at all.

[ADR-0006](../../docs/decisions/ADR-0006-commit-gate.md) built a gate that
enforces *an* authorization. It did not know which actions a workspace permits
in the first place, and that gap is a real one: with a per-effect gate and no
policy, the only thing standing between B1 and any action whatsoever is a user
reading each envelope carefully, every time, forever. A standing rule set is
what makes "B1 may never do that here" expressible at all, rather than
re-decided per prompt.

This layer sits *above* per-effect authority and answers a different question:

    policy      may this kind of action ever be authorized here?
    authority   is this specific action, on this target, authorized now?

Both must say yes. Neither substitutes for the other, and the ordering matters:
the policy is consulted **before an authority can be granted**, so a capability
the policy denies never becomes an envelope a user could be asked to approve.
Asking someone to approve something that would be refused anyway trains them to
approve things.

Four rules
----------
1. **Default deny.** An action not named in `allow` is denied. There is no
   implicit permission and no wildcard default; the empty policy permits
   nothing, and that is a usable, meaningful policy rather than an error.

2. **Deny wins.** An explicit `deny` overrides any `allow`, always, and is
   checked first. A policy where the order of entries decided the outcome would
   make review depend on reading the whole list in sequence.

3. **A persistence ceiling, per capability.** `REVERSIBLE < COMPENSATABLE <
   IRREVERSIBLE`. A capability permitting `fs.write` up to REVERSIBLE does not
   permit an irreversible one, even on the same action and target.

4. **UNKNOWN is never admitted.** It is not a rank on that scale and it is not
   treated as one. An effect whose recovery class nobody could determine is not
   an effect anybody can permit in advance -- and quietly sorting it anywhere in
   the ordering would decide, silently, whether the unclassifiable is safe.

Changing the policy
-------------------
Adopting a policy is a configuration change, and it is journaled: see
`CommitGate.adopt_policy`. It is *not* routed through B1's own commit gate,
because the gate's decisions depend on the policy and a gate cannot gate its own
premise. What is offered instead is stated plainly in ADR-0008: the change
cannot happen silently, it is digest-bound, it appears in history, and every
authorization granted under the previous policy goes stale the moment it lands.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from b1_protocol.canonical import canonical_json_bytes, digest_value
from b1_protocol.scope import scope_admits, scope_is_valid

__all__ = [
    "POLICY_SCHEMA_VERSION",
    "PERSISTENCE_RANK",
    "PolicyError",
    "Capability",
    "PolicyDecision",
    "CapabilityPolicy",
]

POLICY_SCHEMA_VERSION = "b1-capability-policy-1"

# Ordered by how much of the damage is recoverable. UNKNOWN is deliberately
# absent: see rule 4 above. A dict rather than a list so that a lookup of an
# unranked class raises rather than returning a position.
PERSISTENCE_RANK = {
    "REVERSIBLE": 0,
    "COMPENSATABLE": 1,
    "IRREVERSIBLE": 2,
}

_ACTION_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")


class PolicyError(Exception):
    """A capability or policy is not usable as given."""


@dataclass(frozen=True, slots=True)
class Capability:
    """One action, bounded by scope and by how much damage it may risk."""

    action: str
    scope: tuple[str, ...]
    max_persistence_class: str = "REVERSIBLE"
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.action or not set(self.action) <= _ACTION_CHARS:
            raise PolicyError(
                f"action {self.action!r} is not a usable action name; expected something "
                f"like 'fs.write' or 'net.send'"
            )
        problem = scope_is_valid(self.scope)
        if problem is not None:
            raise PolicyError(f"{self.action}: {problem}")
        if self.max_persistence_class not in PERSISTENCE_RANK:
            raise PolicyError(
                f"{self.action}: max_persistence_class must be one of "
                f"{sorted(PERSISTENCE_RANK)}, got {self.max_persistence_class!r}. "
                f"UNKNOWN is not accepted as a ceiling: an effect whose recovery class "
                f"nobody could determine is not one anybody can permit in advance"
            )

    def admits(self, action: str, target: str, persistence_class: str) -> bool:
        """Does this capability cover this action, target and recovery class?"""
        if action != self.action:
            return False
        if not scope_admits(self.scope, target):
            return False
        rank = PERSISTENCE_RANK.get(persistence_class)
        if rank is None:
            # Covers UNKNOWN and anything unrecognised. Both mean the same thing
            # here: nothing on this scale describes it, so no ceiling admits it.
            return False
        return rank <= PERSISTENCE_RANK[self.max_persistence_class]

    def matches_action_and_target(self, action: str, target: str) -> bool:
        """Ignores the ceiling. Used by `deny`, which is not a ceiling question.

        A deny entry means "not this, here" -- it would be strange for denying
        an irreversible write to leave the reversible one permitted by the same
        rule, so a deny matches on action and target alone.
        """
        return action == self.action and scope_admits(self.scope, target)

    def to_canonical_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "action": self.action,
            "scope": list(self.scope),
            "max_persistence_class": self.max_persistence_class,
        }
        if self.reason:
            out["reason"] = self.reason
        return out


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Allowed or not, and why. The reason is the product, not a courtesy.

    A policy that answered only yes or no would be unreviewable: the whole point
    of a standing rule set is that someone can read why a thing was refused and
    decide whether the rule is the one they meant to write.
    """

    allowed: bool
    reason: str
    matched: str = ""

    def __bool__(self) -> bool:
        return self.allowed


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    """A named, digestable set of standing capabilities."""

    policy_id: str
    allow: tuple[Capability, ...] = ()
    deny: tuple[Capability, ...] = ()
    description: str = ""
    schema_version: str = field(default=POLICY_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        if not self.policy_id:
            raise PolicyError("policy_id must be non-empty")
        if self.schema_version != POLICY_SCHEMA_VERSION:
            raise PolicyError(
                f"unknown policy schema_version {self.schema_version!r}; "
                f"refusing to guess at it"
            )

    @classmethod
    def deny_everything(cls, policy_id: str = "deny-everything") -> "CapabilityPolicy":
        """The policy that permits nothing.

        Not an error state and not a placeholder. It is the correct starting
        point for a system that must be told what it may do, and it is what a
        gate falls back to if nobody decides -- so that forgetting to choose
        fails closed instead of open.
        """
        return cls(
            policy_id=policy_id,
            description="Permits nothing. Every capability must be added deliberately.",
        )

    def decide(self, action: str, target: str, persistence_class: str) -> PolicyDecision:
        """May this action, on this target, at this recovery class, be authorized?"""
        for capability in self.deny:
            if capability.matches_action_and_target(action, target):
                detail = f": {capability.reason}" if capability.reason else ""
                return PolicyDecision(
                    allowed=False,
                    reason=(
                        f"policy {self.policy_id!r} explicitly denies {action!r} on "
                        f"{target!r}{detail}. A deny is not outvoted by an allow"
                    ),
                    matched=f"deny:{capability.action}",
                )

        for capability in self.allow:
            if capability.admits(action, target, persistence_class):
                return PolicyDecision(
                    allowed=True,
                    reason=(
                        f"policy {self.policy_id!r} permits {action!r} within "
                        f"{capability.scope} up to {capability.max_persistence_class}"
                    ),
                    matched=f"allow:{capability.action}",
                )

        # Say which of the three ways it missed, because "denied" alone sends
        # someone to re-read the whole policy to find out what to change.
        near = [c for c in self.allow if c.action == action]
        if not near:
            detail = f"no capability in the policy names the action {action!r}"
        elif not any(scope_admits(c.scope, target) for c in near):
            detail = (
                f"{action!r} is permitted, but not on {target!r}: "
                f"scopes are {[list(c.scope) for c in near]}"
            )
        elif persistence_class not in PERSISTENCE_RANK:
            detail = (
                f"{action!r} on {target!r} is in scope, but its persistence class is "
                f"{persistence_class!r}, which is not a rank on the recovery scale. "
                f"An effect nobody could classify is not one a ceiling can admit"
            )
        else:
            ceilings = sorted({c.max_persistence_class for c in near})
            detail = (
                f"{action!r} on {target!r} is in scope, but {persistence_class} exceeds "
                f"the ceiling {ceilings}"
            )
        return PolicyDecision(
            allowed=False,
            reason=f"policy {self.policy_id!r} does not permit it: {detail}",
        )

    def to_canonical_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "schema_version": self.schema_version,
            "policy_id": self.policy_id,
            "allow": [c.to_canonical_dict() for c in self.allow],
            "deny": [c.to_canonical_dict() for c in self.deny],
        }
        if self.description:
            out["description"] = self.description
        return out

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_dict())

    def digest(self) -> str:
        """The digest an authority envelope binds itself to.

        Every field is inside it, including the order of entries, so there is no
        way to widen a policy while keeping the same digest -- and therefore no
        way to widen one without making every outstanding authorization stale.
        """
        return digest_value(self.to_canonical_dict())
