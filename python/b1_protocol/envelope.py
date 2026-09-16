"""The B1 Event Envelope v1.

Schema: ``schemas/b1-event-envelope-v1.schema.json``.

The envelope's job is to keep four things apart that are constantly collapsed
into one another:

    Origin  !=  Authority  !=  Executor  !=  Effect

``origin`` says where an event came from. ``authority_envelope_digest`` says what
permitted it. ``execution_identity`` says who actually ran it. ``effect_identity``
says which persistent change it concerns. Knowing one of these tells you nothing
about the others, so the envelope carries all four rather than deriving any of
them.

A second separation is carried in ``epistemic_status``:

    Execution receipt  !=  Observed effect  !=  Objective postcondition

A provider returning success is a receipt. It justifies ``WORKING_ASSUMPTION``.
``VERIFIED`` requires that the resulting state was independently observed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .canonical import CanonicalizationError, canonical_json_bytes, digest_value

__all__ = [
    "SCHEMA_VERSION",
    "ORIGINS",
    "EPISTEMIC_STATUSES",
    "PERSISTENCE_CLASSES",
    "EnvelopeError",
    "Envelope",
]

SCHEMA_VERSION = "b1-event-envelope-1"

ORIGINS = ("U", "M", "P", "E")
EPISTEMIC_STATUSES = ("VERIFIED", "WORKING_ASSUMPTION", "UNKNOWN", "IN_DOUBT")
PERSISTENCE_CLASSES = ("REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE", "UNKNOWN")

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HEX256 = re.compile(r"^[0-9a-f]{64}$")


class EnvelopeError(ValueError):
    """An envelope violates the v1 contract."""


@dataclass(frozen=True, slots=True)
class Envelope:
    """One canonical B1 event.

    Construct through ``Envelope.new`` so that ``payload_digest`` is computed
    rather than supplied; a caller-supplied digest is a place for the digest and
    the payload to disagree.
    """

    event_id: str
    origin: str
    program_identity: str
    execution_identity: str
    attempt_identity: str
    epoch: int
    epistemic_status: str
    payload: object
    payload_digest: str
    causal_parents: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    effect_identity: str | None = None
    authority_envelope_digest: str | None = None
    persistence_class: str | None = None
    schema_version: str = field(default=SCHEMA_VERSION)

    @classmethod
    def new(
        cls,
        *,
        event_id: str,
        origin: str,
        program_identity: str,
        execution_identity: str,
        attempt_identity: str,
        epoch: int,
        epistemic_status: str,
        payload: object,
        causal_parents: tuple[str, ...] = (),
        evidence_refs: tuple[str, ...] = (),
        effect_identity: str | None = None,
        authority_envelope_digest: str | None = None,
        persistence_class: str | None = None,
    ) -> "Envelope":
        try:
            payload_digest = digest_value(payload)
        except CanonicalizationError as exc:
            raise EnvelopeError(f"payload is not digestable: {exc}") from exc
        envelope = cls(
            event_id=event_id,
            origin=origin,
            program_identity=program_identity,
            execution_identity=execution_identity,
            attempt_identity=attempt_identity,
            epoch=epoch,
            epistemic_status=epistemic_status,
            payload=payload,
            payload_digest=payload_digest,
            causal_parents=tuple(causal_parents),
            evidence_refs=tuple(evidence_refs),
            effect_identity=effect_identity,
            authority_envelope_digest=authority_envelope_digest,
            persistence_class=persistence_class,
        )
        envelope.validate()
        return envelope

    def validate(self) -> None:
        """Raise ``EnvelopeError`` if this envelope violates the v1 contract."""
        if self.schema_version != SCHEMA_VERSION:
            raise EnvelopeError(
                f"unknown schema_version {self.schema_version!r}; refusing to guess at it"
            )
        if not _ID.match(self.event_id):
            raise EnvelopeError(f"event_id {self.event_id!r} is not a valid identifier")
        if self.origin not in ORIGINS:
            raise EnvelopeError(f"origin must be one of {ORIGINS}, got {self.origin!r}")
        if self.epistemic_status not in EPISTEMIC_STATUSES:
            raise EnvelopeError(
                f"epistemic_status must be one of {EPISTEMIC_STATUSES}, "
                f"got {self.epistemic_status!r}"
            )
        for name in ("program_identity", "execution_identity", "attempt_identity"):
            if not getattr(self, name):
                raise EnvelopeError(f"{name} must be non-empty")
        if not isinstance(self.epoch, int) or isinstance(self.epoch, bool) or self.epoch < 0:
            raise EnvelopeError(f"epoch must be a non-negative integer, got {self.epoch!r}")

        seen: set[str] = set()
        for parent in self.causal_parents:
            if not _ID.match(parent):
                raise EnvelopeError(f"causal parent {parent!r} is not a valid identifier")
            if parent in seen:
                raise EnvelopeError(f"causal parent {parent!r} is repeated")
            seen.add(parent)
        if self.event_id in seen:
            raise EnvelopeError(f"event {self.event_id!r} lists itself as a causal parent")

        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise EnvelopeError("evidence_refs must be unique")
        if any(not ref for ref in self.evidence_refs):
            raise EnvelopeError("evidence_refs must be non-empty strings")

        if not _HEX256.match(self.payload_digest):
            raise EnvelopeError("payload_digest must be 64 lowercase hex characters")
        actual = digest_value(self.payload)
        if actual != self.payload_digest:
            raise EnvelopeError(
                f"payload_digest does not match payload: declared {self.payload_digest}, "
                f"computed {actual}"
            )

        # An effect record must name what authorised it and how recoverable it
        # is, and must do so before the effect happens. Discovering either
        # afterwards is the failure mode this check exists to prevent.
        if self.effect_identity is not None:
            if not self.effect_identity:
                raise EnvelopeError("effect_identity must be non-empty when present")
            if self.authority_envelope_digest is None:
                raise EnvelopeError(
                    f"effect {self.effect_identity!r} carries no authority_envelope_digest; "
                    f"an effect without bound authority is unauthorised by construction"
                )
            if not _HEX256.match(self.authority_envelope_digest):
                raise EnvelopeError(
                    "authority_envelope_digest must be 64 lowercase hex characters"
                )
            if self.persistence_class is None:
                raise EnvelopeError(
                    f"effect {self.effect_identity!r} carries no persistence_class; "
                    f"recovery limits must be classified before the effect, not after"
                )
            if self.persistence_class not in PERSISTENCE_CLASSES:
                raise EnvelopeError(
                    f"persistence_class must be one of {PERSISTENCE_CLASSES}, "
                    f"got {self.persistence_class!r}"
                )
        else:
            if self.persistence_class is not None:
                raise EnvelopeError(
                    "persistence_class is meaningless without effect_identity"
                )
            if self.authority_envelope_digest is not None:
                raise EnvelopeError(
                    "authority_envelope_digest is meaningless without effect_identity"
                )

    def to_canonical_dict(self) -> dict[str, object]:
        """The exact mapping that gets canonicalised.

        Optional fields are omitted when absent rather than emitted as null, so
        that the canonical form has one representation per envelope.
        """
        out: dict[str, object] = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "origin": self.origin,
            "causal_parents": list(self.causal_parents),
            "program_identity": self.program_identity,
            "execution_identity": self.execution_identity,
            "attempt_identity": self.attempt_identity,
            "epoch": self.epoch,
            "evidence_refs": list(self.evidence_refs),
            "epistemic_status": self.epistemic_status,
            "payload_digest": self.payload_digest,
            "payload": self.payload,
        }
        if self.effect_identity is not None:
            out["effect_identity"] = self.effect_identity
        if self.authority_envelope_digest is not None:
            out["authority_envelope_digest"] = self.authority_envelope_digest
        if self.persistence_class is not None:
            out["persistence_class"] = self.persistence_class
        return out

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_dict())

    def digest(self) -> str:
        return digest_value(self.to_canonical_dict())

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "Envelope":
        """Rebuild an envelope from its canonical mapping, validating it."""
        known = {
            "schema_version", "event_id", "origin", "causal_parents", "program_identity",
            "execution_identity", "attempt_identity", "effect_identity",
            "authority_envelope_digest", "epoch", "evidence_refs", "epistemic_status",
            "persistence_class", "payload_digest", "payload",
        }
        unknown = set(data) - known
        if unknown:
            raise EnvelopeError(f"unknown envelope fields: {sorted(unknown)}")
        try:
            envelope = cls(
                schema_version=str(data["schema_version"]),
                event_id=str(data["event_id"]),
                origin=str(data["origin"]),
                causal_parents=tuple(str(p) for p in data.get("causal_parents", ())),
                program_identity=str(data["program_identity"]),
                execution_identity=str(data["execution_identity"]),
                attempt_identity=str(data["attempt_identity"]),
                epoch=data["epoch"],  # type: ignore[arg-type]
                evidence_refs=tuple(str(r) for r in data.get("evidence_refs", ())),
                epistemic_status=str(data["epistemic_status"]),
                payload_digest=str(data["payload_digest"]),
                payload=data["payload"],
                effect_identity=(
                    str(data["effect_identity"]) if "effect_identity" in data else None
                ),
                authority_envelope_digest=(
                    str(data["authority_envelope_digest"])
                    if "authority_envelope_digest" in data else None
                ),
                persistence_class=(
                    str(data["persistence_class"]) if "persistence_class" in data else None
                ),
            )
        except KeyError as exc:
            raise EnvelopeError(f"missing required envelope field: {exc.args[0]}") from exc
        envelope.validate()
        return envelope
