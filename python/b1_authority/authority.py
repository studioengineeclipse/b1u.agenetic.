"""Authority envelopes and transition proofs.

Design-derived-from: b1mu-omega13.9:src/b1mu/work/store.py

An authority envelope is the artifact a user's authorization binds to. The
handoff's rule is that authorization for one effect must not silently authorize
another, which means the envelope has to carry enough to make "another effect"
detectable:

    proposed action, target, scope, expected effect,
    relevant current state, current plan, causal objective

The two that do the real work are ``state_digest`` and ``plan_digest``. Without
them, "this authorization went stale" is a judgement call. With them it is a
comparison: the gate is handed the state and plan it observes at effect time,
and if either differs from what was authorized, the envelope is stale and the
persistence gate closes again.

Upstream already does this, at `work/store.py:737-740`, where a changed project
context digest raises *"run context changed; re-plan before effects"*. B1 moves
the digest into the envelope itself so the envelope is self-describing: the
thing the user authorized carries the state it was authorized against, rather
than that state living somewhere the envelope refers to.

Scope
-----
``scope`` bounds what the authority may touch, and is checked independently of
``target``. An envelope that authorizes writing one file does not authorize
writing its neighbour, even though both are "a file write". Entries are exact
strings, or prefixes ending ``/**``. Deliberately not ``fnmatch``: its ``*``
crosses ``/``, so ``docs/*`` would match ``docs/secrets/key``, which is the one
mistake this field exists to prevent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from b1_protocol.canonical import canonical_json_bytes, digest_value
from b1_protocol.envelope import PERSISTENCE_CLASSES

__all__ = [
    "AUTHORITY_SCHEMA_VERSION",
    "PROOF_SCHEMA_VERSION",
    "RECEIPT_STATUSES",
    "OUTCOMES",
    "AuthorityError",
    "AuthorityEnvelope",
    "TransitionProof",
    "project_outcome",
]

AUTHORITY_SCHEMA_VERSION = "b1-authority-envelope-1"
PROOF_SCHEMA_VERSION = "b1-transition-proof-1"

# What the executor reported. Note UNKNOWN: a call that timed out, or whose
# response was lost, is not a failure. Treating it as one is what produces a
# blind retry against state that may already have changed.
RECEIPT_STATUSES = ("SUCCEEDED", "FAILED", "UNKNOWN")

# What the gate concluded, which is not the same thing.
OUTCOMES = ("VERIFIED", "NO_EFFECT", "IN_DOUBT")

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HEX256 = re.compile(r"^[0-9a-f]{64}$")


class AuthorityError(Exception):
    """An authority envelope or transition proof is not usable as given."""


def _scope_admits(scope: tuple[str, ...], target: str) -> bool:
    """Is ``target`` inside ``scope``?

    Exact match, or a prefix entry ending ``/**`` which admits anything beneath
    that directory. A bare ``**`` admits everything and has to be written out,
    so that granting unlimited scope is a visible act rather than a default.
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


@dataclass(frozen=True, slots=True)
class AuthorityEnvelope:
    """One user authorization, bound to one specific intended effect."""

    authority_id: str
    proposed_action: str
    target: str
    scope: tuple[str, ...]
    expected_effect: str
    state_digest: str
    plan_digest: str
    causal_objective: str
    persistence_class: str
    granted_at_epoch: int
    expires_at_epoch: int | None = None
    schema_version: str = field(default=AUTHORITY_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.schema_version != AUTHORITY_SCHEMA_VERSION:
            raise AuthorityError(
                f"unknown authority schema_version {self.schema_version!r}; "
                f"refusing to guess at it"
            )
        if not _ID.match(self.authority_id):
            raise AuthorityError(f"authority_id {self.authority_id!r} is not a valid identifier")
        for name in ("proposed_action", "target", "expected_effect", "causal_objective"):
            if not getattr(self, name):
                raise AuthorityError(
                    f"{name} must be non-empty: an envelope missing it cannot be compared "
                    f"against what actually happens"
                )
        if not self.scope:
            raise AuthorityError(
                "scope must be non-empty; unlimited scope is written as ('**',) so that "
                "granting it is a visible act rather than an omission"
            )
        if not _scope_admits(self.scope, self.target):
            raise AuthorityError(
                f"target {self.target!r} is outside its own scope {self.scope!r}; "
                f"an envelope that does not admit its own target authorizes nothing"
            )
        for name in ("state_digest", "plan_digest"):
            value = getattr(self, name)
            if not _HEX256.match(value):
                raise AuthorityError(f"{name} must be 64 lowercase hex characters")
        if self.persistence_class not in PERSISTENCE_CLASSES:
            raise AuthorityError(
                f"persistence_class must be one of {PERSISTENCE_CLASSES}, "
                f"got {self.persistence_class!r}"
            )
        if not isinstance(self.granted_at_epoch, int) or isinstance(self.granted_at_epoch, bool):
            raise AuthorityError("granted_at_epoch must be an integer")
        if self.granted_at_epoch < 0:
            raise AuthorityError("granted_at_epoch must be non-negative")
        if self.expires_at_epoch is not None:
            if not isinstance(self.expires_at_epoch, int) or isinstance(self.expires_at_epoch, bool):
                raise AuthorityError("expires_at_epoch must be an integer when present")
            if self.expires_at_epoch <= self.granted_at_epoch:
                raise AuthorityError(
                    f"expires_at_epoch {self.expires_at_epoch} is not after granted_at_epoch "
                    f"{self.granted_at_epoch}; an authority that expires before it is granted "
                    f"authorizes nothing"
                )

    def to_canonical_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "schema_version": self.schema_version,
            "authority_id": self.authority_id,
            "proposed_action": self.proposed_action,
            "target": self.target,
            "scope": list(self.scope),
            "expected_effect": self.expected_effect,
            "state_digest": self.state_digest,
            "plan_digest": self.plan_digest,
            "causal_objective": self.causal_objective,
            "persistence_class": self.persistence_class,
            "granted_at_epoch": self.granted_at_epoch,
        }
        if self.expires_at_epoch is not None:
            out["expires_at_epoch"] = self.expires_at_epoch
        return out

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_dict())

    def digest(self) -> str:
        """The digest an event envelope's ``authority_envelope_digest`` carries.

        Because every field above is inside the digest, changing any of them
        produces a different authority. There is no way to widen a target or a
        scope while keeping the same authorization.
        """
        return digest_value(self.to_canonical_dict())

    def admits(self, action: str, target: str) -> tuple[bool, str]:
        """Does this authority admit ``action`` on ``target``?

        Returns ``(ok, reason)``. The reason is returned rather than raised so a
        caller can report why an effect was refused without catching.
        """
        if action != self.proposed_action:
            return False, (
                f"authority {self.authority_id!r} authorizes action "
                f"{self.proposed_action!r}, not {action!r}"
            )
        if target != self.target:
            return False, (
                f"authority {self.authority_id!r} authorizes target {self.target!r}, "
                f"not {target!r}; authorization for one effect does not authorize another"
            )
        if not _scope_admits(self.scope, target):
            return False, (
                f"target {target!r} is outside scope {self.scope!r}"
            )
        return True, ""

    def staleness(self, observed_state_digest: str, observed_plan_digest: str) -> str | None:
        """Why this authority is stale, or ``None`` if it still holds.

        Called at effect time, and called *again* before the effect is
        committed. Planning-time approval is not permanently sufficient, so a
        single check at claim time would leave a window in which the world moves
        and the authorization does not.
        """
        if observed_state_digest != self.state_digest:
            return (
                f"relevant state changed since authorization: authorized against "
                f"{self.state_digest[:16]}..., now {observed_state_digest[:16]}..."
            )
        if observed_plan_digest != self.plan_digest:
            return (
                f"plan changed since authorization: authorized against "
                f"{self.plan_digest[:16]}..., now {observed_plan_digest[:16]}..."
            )
        return None

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "AuthorityEnvelope":
        known = {
            "schema_version", "authority_id", "proposed_action", "target", "scope",
            "expected_effect", "state_digest", "plan_digest", "causal_objective",
            "persistence_class", "granted_at_epoch", "expires_at_epoch",
        }
        unknown = set(data) - known
        if unknown:
            raise AuthorityError(f"unknown authority fields: {sorted(unknown)}")
        try:
            return cls(
                schema_version=str(data["schema_version"]),
                authority_id=str(data["authority_id"]),
                proposed_action=str(data["proposed_action"]),
                target=str(data["target"]),
                scope=tuple(str(s) for s in data["scope"]),  # type: ignore[union-attr]
                expected_effect=str(data["expected_effect"]),
                state_digest=str(data["state_digest"]),
                plan_digest=str(data["plan_digest"]),
                causal_objective=str(data["causal_objective"]),
                persistence_class=str(data["persistence_class"]),
                granted_at_epoch=data["granted_at_epoch"],  # type: ignore[arg-type]
                expires_at_epoch=data.get("expires_at_epoch"),  # type: ignore[arg-type]
            )
        except KeyError as exc:
            raise AuthorityError(f"missing required authority field: {exc.args[0]}") from exc


@dataclass(frozen=True, slots=True)
class TransitionProof:
    """What an executor must return after acting under a permit.

    The three fields kept separate here are the whole point:

        ``receipt_status``       -- what the executor was told
        ``observed_effect_digest`` -- what was independently read back
        ``postcondition_verified`` -- whether the objective actually holds

    A provider returning success populates only the first. It is evidence that a
    call completed, not that the world changed, and not that the change was the
    one wanted.
    """

    permit_id: str
    permit_digest: str
    effect_identity: str
    target: str
    fencing_epoch: int
    receipt_status: str
    postcondition_verified: bool
    observed_effect_digest: str | None = None
    postcondition_evidence: tuple[str, ...] = ()
    schema_version: str = field(default=PROOF_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if self.schema_version != PROOF_SCHEMA_VERSION:
            raise AuthorityError(
                f"unknown proof schema_version {self.schema_version!r}; refusing to guess at it"
            )
        if not _ID.match(self.permit_id):
            raise AuthorityError(f"permit_id {self.permit_id!r} is not a valid identifier")
        if not _HEX256.match(self.permit_digest):
            raise AuthorityError("permit_digest must be 64 lowercase hex characters")
        if not self.effect_identity:
            raise AuthorityError("effect_identity must be non-empty")
        if not self.target:
            raise AuthorityError("target must be non-empty")
        if self.receipt_status not in RECEIPT_STATUSES:
            raise AuthorityError(
                f"receipt_status must be one of {RECEIPT_STATUSES}, got {self.receipt_status!r}"
            )
        if self.observed_effect_digest is not None and not _HEX256.match(
            self.observed_effect_digest
        ):
            raise AuthorityError(
                "observed_effect_digest must be 64 lowercase hex characters when present"
            )
        if self.postcondition_verified and self.observed_effect_digest is None:
            raise AuthorityError(
                "postcondition_verified is true but no observed_effect_digest was supplied; "
                "a postcondition cannot be verified without having observed the resulting state"
            )
        if self.postcondition_verified and not self.postcondition_evidence:
            raise AuthorityError(
                "postcondition_verified is true but no evidence was cited; a verified claim "
                "must say what establishes it"
            )
        if len(set(self.postcondition_evidence)) != len(self.postcondition_evidence):
            raise AuthorityError("postcondition_evidence must be unique")

    def to_canonical_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "schema_version": self.schema_version,
            "permit_id": self.permit_id,
            "permit_digest": self.permit_digest,
            "effect_identity": self.effect_identity,
            "target": self.target,
            "fencing_epoch": self.fencing_epoch,
            "receipt_status": self.receipt_status,
            "postcondition_verified": self.postcondition_verified,
            "postcondition_evidence": list(self.postcondition_evidence),
        }
        if self.observed_effect_digest is not None:
            out["observed_effect_digest"] = self.observed_effect_digest
        return out

    def digest(self) -> str:
        return digest_value(self.to_canonical_dict())


def project_outcome(proof: TransitionProof) -> tuple[str, str, bool]:
    """Project a proof onto ``(outcome, epistemic_status, needs_reconciliation)``.

    This function is where the project's central discipline is enforced
    mechanically rather than by intention:

        Execution receipt != Observed effect != Objective postcondition

    The row that matters most is the second one. An executor reporting success
    while the postcondition is unverified is the single most common way a system
    comes to believe something it has not established, and it resolves to
    IN_DOUBT here, never VERIFIED.

    The third row matters most for safety. An UNKNOWN receipt means nobody knows
    whether the effect landed, so the effect identity is flagged for
    reconciliation and no further permit for it will issue until the real state
    has been read back. That is what stops a blind retry against state that may
    already have changed.
    """
    if proof.receipt_status == "UNKNOWN":
        # Whether the effect happened is unknown, regardless of what the
        # postcondition check thinks it saw: the two could describe different
        # attempts.
        return "IN_DOUBT", "IN_DOUBT", True

    if proof.receipt_status == "SUCCEEDED":
        if proof.postcondition_verified:
            return "VERIFIED", "VERIFIED", False
        # The call completed and the objective was not established. Something
        # may well have changed; nobody has checked what.
        return "IN_DOUBT", "IN_DOUBT", True

    # receipt_status == "FAILED"
    if proof.observed_effect_digest is not None:
        # A failure whose resulting state was read back is a known non-effect.
        # This is the only path to NO_EFFECT, and it requires an observation:
        # a reported failure on its own does not prove nothing happened.
        return "NO_EFFECT", "VERIFIED", False
    return "IN_DOUBT", "IN_DOUBT", True
