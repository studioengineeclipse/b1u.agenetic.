"""The Dual-Core Commit Gate.

Design-derived-from: b1mu-omega13.9:src/b1mu/work/runtime.py

Rust and Python are equal computational peers. Both may analyse, derive
subgoals, plan, simulate and *propose*. Neither may cause a persistent effect on
its own. Everything consequential passes through this gate, which holds exactly
one SQLite write lease at a time, so two peers proposing the same effect
linearise rather than both acting.

The sequence, and what each step refuses
----------------------------------------

``propose``          records intent. No authority, no effect. Always allowed:
                     thinking is not a persistent effect.

``grant``            binds a user's authorization to one specific
                     :class:`AuthorityEnvelope`. Authorization for one effect
                     does not authorize another, which is enforced by putting
                     action, target, scope, state and plan inside the digest.

``claim_permit``     validates authority *at effect time* and issues a one-time,
                     fenced permit. Refuses on: no authority; action or target
                     outside the envelope; authority expired; **authority
                     stale**; an effect already needing reconciliation; a target
                     already holding an active permit.

``consume``          atomically spends the permit and records what happened.
                     Refuses on: unknown or already-spent permit; a proof whose
                     permit digest does not match; a stale fence; and --
                     the one that matters most -- **authority that went stale
                     between claim and consume**.

That last refusal is the whole reason this is a gate rather than a checklist.
Upstream puts it well at `work/store.py:766`: *"validate+consume is intentionally
one transaction / linearization point."* B1 adds a second check of the same
authority at consume time, because planning-time approval is not permanently
sufficient and the world can move while an executor is working.

What a permit is not
--------------------
Upstream's comment on this is worth keeping verbatim in spirit: *"A permit is
not evidence that the effect actually happened."* A permit says an effect was
allowed to be attempted. What happened is what :class:`TransitionProof` reports,
and whether the objective holds is a third question again.

Unknown outcomes
----------------
An UNKNOWN receipt flags the effect identity for reconciliation, and no further
permit for it issues until :meth:`reconcile` is given an observation of the real
state. This is what prevents a blind retry against state that may already have
changed -- the failure mode where a "failed" payment is retried and the customer
is charged twice.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from b1_protocol.canonical import digest_value
from b1_protocol.envelope import Envelope
from b1_state.journal import RootJournal

from .authority import (
    AuthorityEnvelope,
    AuthorityError,
    TransitionProof,
    project_outcome,
)

__all__ = [
    "GATE_SCHEMA_VERSION",
    "GateError",
    "AuthorityStale",
    "PermitSpent",
    "ReconciliationRequired",
    "Permit",
    "CommitGate",
]

GATE_SCHEMA_VERSION = "1"


class GateError(Exception):
    """The gate refused an operation and changed nothing."""


class AuthorityStale(GateError):
    """The authorization no longer matches the world it was granted against."""


class PermitSpent(GateError):
    """This permit has already been consumed. Permits are one-time by design."""


class ReconciliationRequired(GateError):
    """An earlier attempt's outcome is unknown; read back before retrying."""


@dataclass(frozen=True, slots=True)
class Permit:
    """A one-time, fenced licence to attempt exactly one effect."""

    permit_id: str
    authority_digest: str
    effect_identity: str
    capability: str
    target: str
    fencing_epoch: int
    issued_at_epoch: int
    state_digest: str
    plan_digest: str

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "permit_id": self.permit_id,
            "authority_digest": self.authority_digest,
            "effect_identity": self.effect_identity,
            "capability": self.capability,
            "target": self.target,
            "fencing_epoch": self.fencing_epoch,
            "issued_at_epoch": self.issued_at_epoch,
            "state_digest": self.state_digest,
            "plan_digest": self.plan_digest,
        }

    def digest(self) -> str:
        """What a transition proof must carry back to prove it held this permit."""
        return digest_value(self.to_canonical_dict())


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS gate_meta(
           key TEXT PRIMARY KEY, value TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS authorities(
           authority_id TEXT PRIMARY KEY,
           digest TEXT NOT NULL UNIQUE,
           envelope_json TEXT NOT NULL,
           granted_at_epoch INTEGER NOT NULL,
           expires_at_epoch INTEGER)""",
    """CREATE TABLE IF NOT EXISTS permits(
           permit_id TEXT PRIMARY KEY,
           digest TEXT NOT NULL UNIQUE,
           authority_digest TEXT NOT NULL REFERENCES authorities(digest),
           effect_identity TEXT NOT NULL,
           capability TEXT NOT NULL,
           target TEXT NOT NULL,
           fencing_epoch INTEGER NOT NULL,
           issued_at_epoch INTEGER NOT NULL,
           state_digest TEXT NOT NULL,
           plan_digest TEXT NOT NULL,
           state TEXT NOT NULL CHECK(state IN ('ACTIVE','CONSUMED')),
           consumed_at_epoch INTEGER)""",
    # One fence per domain, monotonic. The destination checks it to reject a
    # superseded executor that woke up late and is still holding an old permit.
    """CREATE TABLE IF NOT EXISTS domain_fences(
           domain TEXT PRIMARY KEY,
           epoch INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS effect_outcomes(
           effect_identity TEXT PRIMARY KEY,
           last_permit_id TEXT NOT NULL,
           outcome TEXT NOT NULL,
           epistemic_status TEXT NOT NULL,
           needs_reconciliation INTEGER NOT NULL,
           proof_digest TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS permits_target_state ON permits(target, state)",
    "CREATE INDEX IF NOT EXISTS permits_effect ON permits(effect_identity)",
)


class CommitGate:
    """The single linearization point for every consequential effect."""

    def __init__(self, journal: RootJournal) -> None:
        self.journal = journal
        self._conn: sqlite3.Connection = journal._conn  # same connection, same lease
        self._initialise()

    # -- setup ---------------------------------------------------------------

    def _initialise(self) -> None:
        with self.journal._lease():
            for statement in _SCHEMA:
                self._conn.execute(statement)
            row = self._conn.execute(
                "SELECT value FROM gate_meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO gate_meta(key,value) VALUES('schema_version',?)",
                    (GATE_SCHEMA_VERSION,),
                )
            elif str(row["value"]) != GATE_SCHEMA_VERSION:
                raise GateError(
                    f"unsupported gate schema {row['value']!r}; this build understands "
                    f"{GATE_SCHEMA_VERSION!r} and will not guess at a migration"
                )

    def _next_epoch(self) -> int:
        """One past the journal's committed fence.

        The journal's ``max_epoch`` is the global authority epoch, so deriving
        from it keeps gate events and journal records on one monotonic clock
        rather than two that can disagree.
        """
        return self.journal.head()[2] + 1

    def _record(self, envelope: Envelope) -> None:
        """Append a gate event to the root journal, inside the current lease.

        Goes through ``append_in_lease`` rather than writing the row directly.
        An earlier draft wrote it directly to avoid nesting transactions, which
        silently skipped the journal's own refusals -- duplicate event id,
        unknown causal parent, stale epoch, duplicate effect. A gate that
        bypasses those is a gate with a hole in it, so the journal grew a
        lease-aware entry point instead.
        """
        self.journal.append_in_lease(envelope)

    # -- propose -------------------------------------------------------------

    def propose(
        self,
        *,
        event_id: str,
        proposer: str,
        action: str,
        target: str,
        rationale: str,
        origin: str = "M",
        causal_parents: tuple[str, ...] = (),
    ) -> Envelope:
        """Record an intent to act. No authority, no effect.

        Either peer may propose freely. A proposal is analysis, and analysis is
        never a persistent effect -- which is exactly why the gate does not gate
        it.
        """
        with self.journal._lease():
            envelope = Envelope.new(
                event_id=event_id,
                origin=origin,
                program_identity="b1-local",
                execution_identity=proposer,
                attempt_identity=f"{event_id}:proposal",
                epoch=self.journal.head()[2] if self.journal.head()[2] >= 0 else 0,
                epistemic_status="WORKING_ASSUMPTION",
                payload={
                    "kind": "gate.proposal",
                    "action": action,
                    "target": target,
                    "rationale": rationale,
                },
                causal_parents=causal_parents,
            )
            self._record(envelope)
        return envelope

    # -- grant ---------------------------------------------------------------

    def grant(self, authority: AuthorityEnvelope, *, event_id: str, granted_by: str) -> str:
        """Bind a user's authorization to one envelope. Returns its digest.

        The envelope is stored whole, not summarised. A gate that kept only a
        digest could tell you an authorization existed but not what it permitted,
        which is useless at the moment someone asks why an effect was allowed.
        """
        authority.validate()
        digest = authority.digest()

        with self.journal._lease():
            existing = self._conn.execute(
                "SELECT digest FROM authorities WHERE authority_id=?",
                (authority.authority_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["digest"]) == digest:
                    return digest  # idempotent re-grant of the identical envelope
                raise GateError(
                    f"authority_id {authority.authority_id!r} already exists with a different "
                    f"envelope; widening an authorization requires a new id, not an edit"
                )

            epoch = self._next_epoch()
            self._conn.execute(
                "INSERT INTO authorities"
                "(authority_id,digest,envelope_json,granted_at_epoch,expires_at_epoch)"
                " VALUES(?,?,?,?,?)",
                (
                    authority.authority_id,
                    digest,
                    authority.canonical_bytes().decode("utf-8"),
                    authority.granted_at_epoch,
                    authority.expires_at_epoch,
                ),
            )
            self._record(
                Envelope.new(
                    event_id=event_id,
                    origin="U",  # only a user grants authority
                    program_identity="b1-local",
                    execution_identity=granted_by,
                    attempt_identity=f"{event_id}:grant",
                    epoch=epoch,
                    epistemic_status="VERIFIED",
                    payload={
                        "kind": "gate.authority_granted",
                        "authority_id": authority.authority_id,
                        "authority_digest": digest,
                        "action": authority.proposed_action,
                        "target": authority.target,
                        "persistence_class": authority.persistence_class,
                    },
                )
            )
        return digest

    def _load_authority(self, digest: str) -> AuthorityEnvelope:
        import json

        row = self._conn.execute(
            "SELECT envelope_json FROM authorities WHERE digest=?", (digest,)
        ).fetchone()
        if row is None:
            raise GateError(
                f"no authority with digest {digest[:16]}...; an effect cannot be attempted "
                f"under an authorization the gate has never seen"
            )
        return AuthorityEnvelope.from_dict(json.loads(row["envelope_json"]))

    # -- claim ---------------------------------------------------------------

    def claim_permit(
        self,
        *,
        permit_id: str,
        authority_digest: str,
        effect_identity: str,
        action: str,
        target: str,
        observed_state_digest: str,
        observed_plan_digest: str,
        executor: str,
        event_id: str,
    ) -> Permit:
        """Validate authority at effect time and issue a one-time fenced permit.

        Every refusal below leaves the database untouched. A gate that partially
        applied a refused claim would be worse than no gate, because the caller
        would believe nothing happened.
        """
        with self.journal._lease():
            authority = self._load_authority(authority_digest)
            epoch = self._next_epoch()

            ok, reason = authority.admits(action, target)
            if not ok:
                raise GateError(reason)

            if authority.expires_at_epoch is not None and epoch >= authority.expires_at_epoch:
                raise AuthorityStale(
                    f"authority {authority.authority_id!r} expired at epoch "
                    f"{authority.expires_at_epoch}; it is now {epoch}"
                )

            stale = authority.staleness(observed_state_digest, observed_plan_digest)
            if stale is not None:
                raise AuthorityStale(
                    f"authority {authority.authority_id!r} is stale: {stale}. "
                    f"The persistence gate has closed again and needs fresh authorization."
                )

            outcome = self._conn.execute(
                "SELECT outcome,needs_reconciliation FROM effect_outcomes WHERE effect_identity=?",
                (effect_identity,),
            ).fetchone()
            if outcome is not None and int(outcome["needs_reconciliation"]):
                raise ReconciliationRequired(
                    f"effect {effect_identity!r} has an unresolved outcome "
                    f"({outcome['outcome']}); read back the real state and call reconcile() "
                    f"before attempting it again. Retrying an unknown effect is how one "
                    f"attempt becomes two."
                )

            active = self._conn.execute(
                "SELECT permit_id FROM permits WHERE target=? AND state='ACTIVE' LIMIT 1",
                (target,),
            ).fetchone()
            if active is not None:
                raise GateError(
                    f"target {target!r} already has an active permit "
                    f"({active['permit_id']}); two executors holding live permits for one "
                    f"target is the split-brain this gate exists to prevent"
                )

            if self._conn.execute(
                "SELECT 1 FROM permits WHERE permit_id=?", (permit_id,)
            ).fetchone():
                raise GateError(f"permit_id {permit_id!r} has already been issued")

            # Fences are per-domain and strictly increasing, so a late executor
            # holding an older permit can be rejected at the destination.
            domain = target or f"capability:{action}"
            fence_row = self._conn.execute(
                "SELECT epoch FROM domain_fences WHERE domain=?", (domain,)
            ).fetchone()
            fencing_epoch = 1 if fence_row is None else int(fence_row["epoch"]) + 1
            self._conn.execute(
                "INSERT INTO domain_fences(domain,epoch) VALUES(?,?) "
                "ON CONFLICT(domain) DO UPDATE SET epoch=excluded.epoch",
                (domain, fencing_epoch),
            )

            permit = Permit(
                permit_id=permit_id,
                authority_digest=authority_digest,
                effect_identity=effect_identity,
                capability=action,
                target=target,
                fencing_epoch=fencing_epoch,
                issued_at_epoch=epoch,
                state_digest=observed_state_digest,
                plan_digest=observed_plan_digest,
            )
            self._conn.execute(
                "INSERT INTO permits"
                "(permit_id,digest,authority_digest,effect_identity,capability,target,"
                " fencing_epoch,issued_at_epoch,state_digest,plan_digest,state,consumed_at_epoch)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,'ACTIVE',NULL)",
                (
                    permit.permit_id, permit.digest(), authority_digest, effect_identity,
                    action, target, fencing_epoch, epoch,
                    observed_state_digest, observed_plan_digest,
                ),
            )
            self._record(
                Envelope.new(
                    event_id=event_id,
                    origin="M",
                    program_identity="b1-local",
                    execution_identity=executor,
                    attempt_identity=permit_id,
                    epoch=epoch,
                    # A permit is permission to attempt, not evidence of effect.
                    epistemic_status="WORKING_ASSUMPTION",
                    payload={
                        "kind": "gate.permit_issued",
                        "permit_id": permit_id,
                        "permit_digest": permit.digest(),
                        "authority_digest": authority_digest,
                        "effect_identity": effect_identity,
                        "fencing_epoch": fencing_epoch,
                    },
                )
            )
        return permit

    # -- consume -------------------------------------------------------------

    def consume(
        self,
        proof: TransitionProof,
        *,
        observed_state_digest: str,
        observed_plan_digest: str,
        executor: str,
        event_id: str,
    ) -> tuple[str, str]:
        """Spend the permit atomically and record what actually happened.

        Returns ``(outcome, epistemic_status)``.

        ``observed_state_digest`` and ``observed_plan_digest`` are re-checked
        here against the same authority that was validated at claim time. This
        is the effect-time revalidation the design requires: a check only at
        claim time would leave the interval during which the executor was
        working entirely unguarded.
        """
        proof.validate()

        with self.journal._lease():
            row = self._conn.execute(
                "SELECT * FROM permits WHERE permit_id=?", (proof.permit_id,)
            ).fetchone()
            if row is None:
                raise GateError(
                    f"no permit {proof.permit_id!r}; an effect reported without a permit was "
                    f"never authorized"
                )
            if str(row["state"]) != "ACTIVE":
                raise PermitSpent(
                    f"permit {proof.permit_id!r} was already consumed at epoch "
                    f"{row['consumed_at_epoch']}; permits are one-time so that a replayed "
                    f"report cannot re-authorize a second attempt"
                )
            if proof.permit_digest != str(row["digest"]):
                raise GateError(
                    f"transition proof carries permit digest {proof.permit_digest[:16]}..., "
                    f"the issued permit is {str(row['digest'])[:16]}...; the proof does not "
                    f"describe the permit it claims"
                )
            if proof.effect_identity != str(row["effect_identity"]):
                raise GateError("transition proof effect_identity does not match its permit")
            if proof.target != str(row["target"]):
                raise GateError("transition proof target does not match its permit")
            if int(proof.fencing_epoch) != int(row["fencing_epoch"]):
                raise GateError(
                    f"transition proof carries fence {proof.fencing_epoch}, permit holds "
                    f"{row['fencing_epoch']}; a superseded executor must not report"
                )

            domain = str(row["target"]) or f"capability:{row['capability']}"
            fence_row = self._conn.execute(
                "SELECT epoch FROM domain_fences WHERE domain=?", (domain,)
            ).fetchone()
            if fence_row is not None and int(proof.fencing_epoch) < int(fence_row["epoch"]):
                raise GateError(
                    f"fence {proof.fencing_epoch} is below the current {fence_row['epoch']} for "
                    f"{domain!r}; this executor was superseded while it was working"
                )

            # Effect-time authority revalidation. The world may have moved while
            # the executor was busy, and an authorization granted against the
            # old world does not cover the new one.
            authority = self._load_authority(str(row["authority_digest"]))
            stale = authority.staleness(observed_state_digest, observed_plan_digest)
            if stale is not None:
                raise AuthorityStale(
                    f"authority {authority.authority_id!r} went stale between claim and "
                    f"consume: {stale}. The effect is not recorded as authorized."
                )

            outcome, epistemic_status, needs_reconciliation = project_outcome(proof)
            epoch = self._next_epoch()

            # Validate and consume in one transaction: the conditional UPDATE
            # plus a changes() check is what makes concurrent consumption lose
            # rather than interleave.
            self._conn.execute(
                "UPDATE permits SET state='CONSUMED',consumed_at_epoch=? "
                "WHERE permit_id=? AND state='ACTIVE'",
                (epoch, proof.permit_id),
            )
            if self._conn.execute("SELECT changes() AS c").fetchone()["c"] != 1:
                raise PermitSpent(
                    f"permit {proof.permit_id!r} was consumed concurrently"
                )

            self._conn.execute(
                "INSERT INTO effect_outcomes"
                "(effect_identity,last_permit_id,outcome,epistemic_status,"
                " needs_reconciliation,proof_digest)"
                " VALUES(?,?,?,?,?,?)"
                " ON CONFLICT(effect_identity) DO UPDATE SET"
                "  last_permit_id=excluded.last_permit_id, outcome=excluded.outcome,"
                "  epistemic_status=excluded.epistemic_status,"
                "  needs_reconciliation=excluded.needs_reconciliation,"
                "  proof_digest=excluded.proof_digest",
                (
                    proof.effect_identity, proof.permit_id, outcome, epistemic_status,
                    1 if needs_reconciliation else 0, proof.digest(),
                ),
            )

            payload: dict[str, object] = {
                "kind": "gate.effect_reported",
                "permit_id": proof.permit_id,
                "receipt_status": proof.receipt_status,
                "postcondition_verified": proof.postcondition_verified,
                "outcome": outcome,
                "proof_digest": proof.digest(),
            }
            if proof.observed_effect_digest is not None:
                payload["observed_effect_digest"] = proof.observed_effect_digest

            self._record(
                Envelope.new(
                    event_id=event_id,
                    origin="M",
                    program_identity="b1-local",
                    execution_identity=executor,
                    attempt_identity=proof.permit_id,
                    epoch=epoch,
                    epistemic_status=epistemic_status,
                    payload=payload,
                    evidence_refs=proof.postcondition_evidence,
                    effect_identity=proof.effect_identity,
                    authority_envelope_digest=str(row["authority_digest"]),
                    persistence_class=authority.persistence_class,
                )
            )
        return outcome, epistemic_status

    # -- reconcile -----------------------------------------------------------

    def reconcile(
        self,
        *,
        effect_identity: str,
        observed_effect_digest: str,
        effect_happened: bool,
        evidence: tuple[str, ...],
        executor: str,
        event_id: str,
    ) -> str:
        """Resolve an unknown outcome by reading back the real state.

        This is the only way to clear a reconciliation flag. It requires an
        observation and cited evidence, because the whole point is to replace a
        guess with a look. Returns the resolved outcome.
        """
        if not evidence:
            raise GateError(
                "reconciliation requires cited evidence; an unknown outcome cannot be "
                "resolved by assertion"
            )

        with self.journal._lease():
            row = self._conn.execute(
                "SELECT * FROM effect_outcomes WHERE effect_identity=?", (effect_identity,)
            ).fetchone()
            if row is None:
                raise GateError(f"no recorded outcome for effect {effect_identity!r}")
            if not int(row["needs_reconciliation"]):
                raise GateError(
                    f"effect {effect_identity!r} is not awaiting reconciliation; its outcome "
                    f"is already {row['outcome']}"
                )

            resolved = "VERIFIED" if effect_happened else "NO_EFFECT"
            epoch = self._next_epoch()
            self._conn.execute(
                "UPDATE effect_outcomes SET outcome=?,epistemic_status='VERIFIED',"
                "needs_reconciliation=0 WHERE effect_identity=?",
                (resolved, effect_identity),
            )
            self._record(
                Envelope.new(
                    event_id=event_id,
                    origin="M",
                    program_identity="b1-local",
                    execution_identity=executor,
                    attempt_identity=f"{event_id}:reconcile",
                    epoch=epoch,
                    epistemic_status="VERIFIED",
                    payload={
                        "kind": "gate.reconciled",
                        "effect_identity": effect_identity,
                        "resolved_outcome": resolved,
                        "observed_effect_digest": observed_effect_digest,
                        "prior_outcome": str(row["outcome"]),
                    },
                    evidence_refs=evidence,
                )
            )
        return resolved

    # -- inspection ----------------------------------------------------------

    def effect_outcome(self, effect_identity: str) -> tuple[str, str, bool] | None:
        """``(outcome, epistemic_status, needs_reconciliation)`` or ``None``."""
        row = self._conn.execute(
            "SELECT outcome,epistemic_status,needs_reconciliation FROM effect_outcomes "
            "WHERE effect_identity=?",
            (effect_identity,),
        ).fetchone()
        if row is None:
            return None
        return (
            str(row["outcome"]),
            str(row["epistemic_status"]),
            bool(int(row["needs_reconciliation"])),
        )

    def permit_state(self, permit_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT state FROM permits WHERE permit_id=?", (permit_id,)
        ).fetchone()
        return None if row is None else str(row["state"])

    def domain_fence(self, domain: str) -> int:
        row = self._conn.execute(
            "SELECT epoch FROM domain_fences WHERE domain=?", (domain,)
        ).fetchone()
        return 0 if row is None else int(row["epoch"])
