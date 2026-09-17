//! The Dual-Core Commit Gate, Rust peer.
//!
//! Design-derived-from: b1mu-omega13.9:src/b1mu/work/runtime.py
//!
//! Mirrors `python/b1_authority/gate.py`, operating on the same database file
//! and the same single write lease. That is what makes "equal computational
//! peers" a property rather than a slogan: this peer and the Python one
//! contend for one lease, so two of them proposing the same effect linearise
//! instead of both acting.
//!
//! `tools/verify_cross_language_gate.py` races a Rust peer against a Python
//! peer on one database and requires that exactly one permit issues and
//! exactly one effect lands in history.
//!
//! The refusals, and the one that matters most
//! -------------------------------------------
//! `claim_permit` refuses on: no authority; action or target outside the
//! envelope; authority expired; authority stale; an effect awaiting
//! reconciliation; a target already holding an active permit.
//!
//! `consume` refuses on: unknown or already-spent permit; a proof whose permit
//! digest does not match; a superseded fence; and — the reason this is a gate
//! rather than a checklist — **authority that went stale between claim and
//! consume**. A permit may be validly issued and the world then move while the
//! executor works. Recording that effect as authorized would be recording an
//! authorization that no longer describes what happened.

use std::collections::BTreeMap;
use std::fmt;

use b1_protocol::canonical::{digest_value, Digestable};
use b1_protocol::envelope::{
    EffectBinding, Envelope, EpistemicStatus, Origin, PersistenceClass,
};
use b1_state::journal::{JournalError, RootJournal};
use rusqlite::params;

use crate::authority::{
    project_outcome, AuthorityEnvelope, AuthorityError, TransitionProof,
};

pub const GATE_SCHEMA_VERSION: &str = "1";

#[derive(Debug)]
pub enum GateError {
    /// The gate refused an operation and changed nothing.
    Refused(String),
    /// The authorization no longer matches the world it was granted against.
    AuthorityStale(String),
    /// This permit has already been consumed. Permits are one-time by design.
    PermitSpent(String),
    /// An earlier attempt's outcome is unknown; read back before retrying.
    ReconciliationRequired(String),
    Journal(JournalError),
    Authority(AuthorityError),
    Sqlite(rusqlite::Error),
}

impl fmt::Display for GateError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            GateError::Refused(m)
            | GateError::AuthorityStale(m)
            | GateError::PermitSpent(m)
            | GateError::ReconciliationRequired(m) => f.write_str(m),
            GateError::Journal(e) => write!(f, "journal: {e}"),
            GateError::Authority(e) => write!(f, "authority: {e}"),
            GateError::Sqlite(e) => write!(f, "sqlite: {e}"),
        }
    }
}

impl std::error::Error for GateError {}

impl From<rusqlite::Error> for GateError {
    fn from(e: rusqlite::Error) -> Self {
        GateError::Sqlite(e)
    }
}
impl From<JournalError> for GateError {
    fn from(e: JournalError) -> Self {
        GateError::Journal(e)
    }
}
impl From<AuthorityError> for GateError {
    fn from(e: AuthorityError) -> Self {
        GateError::Authority(e)
    }
}

/// The kind of refusal, so a caller can tell "lost by the rule" from "lost by
/// a lock". The cross-language verifier asserts on this rather than on the
/// message, because a gate that only worked because SQLite returned
/// SQLITE_BUSY would be relying on an implementation detail.
impl GateError {
    pub fn kind(&self) -> &'static str {
        match self {
            GateError::Refused(_) => "REFUSED",
            GateError::AuthorityStale(_) => "AUTHORITY_STALE",
            GateError::PermitSpent(_) => "PERMIT_SPENT",
            GateError::ReconciliationRequired(_) => "RECONCILIATION_REQUIRED",
            GateError::Journal(_) => "JOURNAL_ERROR",
            GateError::Authority(_) => "AUTHORITY_ERROR",
            GateError::Sqlite(_) => "SQLITE_ERROR",
        }
    }
}

type Result<T> = std::result::Result<T, GateError>;

/// A one-time, fenced licence to attempt exactly one effect.
///
/// Note what a permit is *not*: evidence that the effect happened. It says an
/// effect was allowed to be attempted. What happened is what a
/// [`TransitionProof`] reports.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Permit {
    pub permit_id: String,
    pub authority_digest: String,
    pub effect_identity: String,
    pub capability: String,
    pub target: String,
    pub fencing_epoch: i64,
    pub issued_at_epoch: i64,
    pub state_digest: String,
    pub plan_digest: String,
}

impl Permit {
    pub fn to_canonical(&self) -> Digestable {
        let mut map: BTreeMap<String, Digestable> = BTreeMap::new();
        map.insert("permit_id".into(), Digestable::String(self.permit_id.clone()));
        map.insert(
            "authority_digest".into(),
            Digestable::String(self.authority_digest.clone()),
        );
        map.insert(
            "effect_identity".into(),
            Digestable::String(self.effect_identity.clone()),
        );
        map.insert("capability".into(), Digestable::String(self.capability.clone()));
        map.insert("target".into(), Digestable::String(self.target.clone()));
        map.insert("fencing_epoch".into(), Digestable::Integer(self.fencing_epoch));
        map.insert(
            "issued_at_epoch".into(),
            Digestable::Integer(self.issued_at_epoch),
        );
        map.insert("state_digest".into(), Digestable::String(self.state_digest.clone()));
        map.insert("plan_digest".into(), Digestable::String(self.plan_digest.clone()));
        Digestable::Object(map)
    }

    /// What a transition proof must carry back to prove it held this permit.
    pub fn digest(&self) -> String {
        digest_value(&self.to_canonical())
    }
}

const SCHEMA: &[&str] = &[
    "CREATE TABLE IF NOT EXISTS gate_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS authorities(
         authority_id TEXT PRIMARY KEY,
         digest TEXT NOT NULL UNIQUE,
         envelope_json TEXT NOT NULL,
         granted_at_epoch INTEGER NOT NULL,
         expires_at_epoch INTEGER)",
    "CREATE TABLE IF NOT EXISTS permits(
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
         consumed_at_epoch INTEGER)",
    "CREATE TABLE IF NOT EXISTS domain_fences(
         domain TEXT PRIMARY KEY,
         epoch INTEGER NOT NULL)",
    "CREATE TABLE IF NOT EXISTS effect_outcomes(
         effect_identity TEXT PRIMARY KEY,
         last_permit_id TEXT NOT NULL,
         outcome TEXT NOT NULL,
         epistemic_status TEXT NOT NULL,
         needs_reconciliation INTEGER NOT NULL,
         proof_digest TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS permits_target_state ON permits(target, state)",
    "CREATE INDEX IF NOT EXISTS permits_effect ON permits(effect_identity)",
];

pub struct CommitGate {
    journal: RootJournal,
}

impl fmt::Debug for CommitGate {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Opaque on purpose: printing gate contents into a log or panic
        // message would leak whatever the payloads hold.
        f.write_str("CommitGate { .. }")
    }
}

impl CommitGate {
    pub fn open(journal: RootJournal) -> Result<Self> {
        let gate = CommitGate { journal };
        gate.initialise()?;
        Ok(gate)
    }

    pub fn journal(&self) -> &RootJournal {
        &self.journal
    }

    fn initialise(&self) -> Result<()> {
        let lease = self.journal.lease()?;
        let outcome = (|| -> Result<()> {
            for statement in SCHEMA {
                self.journal.connection().execute(statement, [])?;
            }
            let existing: Option<String> = self
                .journal
                .connection()
                .query_row(
                    "SELECT value FROM gate_meta WHERE key='schema_version'",
                    [],
                    |row| row.get(0),
                )
                .ok();
            match existing {
                None => {
                    self.journal.connection().execute(
                        "INSERT INTO gate_meta(key,value) VALUES('schema_version',?1)",
                        params![GATE_SCHEMA_VERSION],
                    )?;
                }
                Some(found) if found != GATE_SCHEMA_VERSION => {
                    return Err(GateError::Refused(format!(
                        "unsupported gate schema {found:?}; this build understands \
                         {GATE_SCHEMA_VERSION:?} and will not guess at a migration"
                    )));
                }
                Some(_) => {}
            }
            Ok(())
        })();
        lease.finish(outcome.is_ok())?;
        outcome
    }

    fn next_epoch(&self) -> Result<i64> {
        Ok(self.journal.head()?.max_epoch + 1)
    }

    fn load_authority(&self, digest: &str) -> Result<AuthorityEnvelope> {
        let json: Option<String> = self
            .journal
            .connection()
            .query_row(
                "SELECT envelope_json FROM authorities WHERE digest=?1",
                params![digest],
                |row| row.get(0),
            )
            .ok();
        let Some(json) = json else {
            return Err(GateError::Refused(format!(
                "no authority with digest {}...; an effect cannot be attempted under an \
                 authorization the gate has never seen",
                &digest[..digest.len().min(16)]
            )));
        };
        let parsed: serde_json::Value = serde_json::from_str(&json)
            .map_err(|e| GateError::Refused(format!("stored authority is unparseable: {e}")))?;
        let value = Digestable::from_json(&parsed)
            .map_err(|e| GateError::Refused(format!("stored authority is not digestable: {e}")))?;
        Ok(AuthorityEnvelope::from_canonical(&value)?)
    }

    /// Bind a user's authorization to one envelope. Returns its digest.
    pub fn grant(
        &self,
        authority: &AuthorityEnvelope,
        event_id: &str,
        granted_by: &str,
    ) -> Result<String> {
        authority.validate()?;
        let digest = authority.digest();

        let lease = self.journal.lease()?;
        let outcome = (|| -> Result<String> {
            let existing: Option<String> = self
                .journal
                .connection()
                .query_row(
                    "SELECT digest FROM authorities WHERE authority_id=?1",
                    params![authority.authority_id],
                    |row| row.get(0),
                )
                .ok();
            if let Some(found) = existing {
                if found == digest {
                    return Ok(digest.clone()); // idempotent identical re-grant
                }
                return Err(GateError::Refused(format!(
                    "authority_id {:?} already exists with a different envelope; widening \
                     an authorization requires a new id, not an edit",
                    authority.authority_id
                )));
            }

            let epoch = self.next_epoch()?;
            self.journal.connection().execute(
                "INSERT INTO authorities\
                 (authority_id,digest,envelope_json,granted_at_epoch,expires_at_epoch)\
                 VALUES(?1,?2,?3,?4,?5)",
                params![
                    authority.authority_id,
                    digest,
                    String::from_utf8(authority.canonical_bytes()).expect("canonical is UTF-8"),
                    authority.granted_at_epoch,
                    authority.expires_at_epoch
                ],
            )?;

            let mut payload: BTreeMap<String, Digestable> = BTreeMap::new();
            payload.insert("kind".into(), Digestable::String("gate.authority_granted".into()));
            payload.insert(
                "authority_id".into(),
                Digestable::String(authority.authority_id.clone()),
            );
            payload.insert("authority_digest".into(), Digestable::String(digest.clone()));
            payload.insert(
                "action".into(),
                Digestable::String(authority.proposed_action.clone()),
            );
            payload.insert("target".into(), Digestable::String(authority.target.clone()));
            payload.insert(
                "persistence_class".into(),
                Digestable::String(authority.persistence_class.clone()),
            );

            let envelope = Envelope::new(
                event_id,
                Origin::U, // only a user grants authority
                "b1-local",
                granted_by,
                format!("{event_id}:grant"),
                epoch,
                EpistemicStatus::Verified,
                Digestable::Object(payload),
                vec![],
                vec![],
                None,
            )
            .map_err(|e| GateError::Refused(format!("grant event invalid: {e}")))?;
            self.journal.append_in_lease(&envelope)?;
            Ok(digest.clone())
        })();
        lease.finish(outcome.is_ok())?;
        outcome
    }

    /// Validate authority at effect time and issue a one-time fenced permit.
    ///
    /// Every refusal leaves the database untouched. A gate that partially
    /// applied a refused claim would be worse than no gate, because the caller
    /// would believe nothing happened.
    #[allow(clippy::too_many_arguments)]
    pub fn claim_permit(
        &self,
        permit_id: &str,
        authority_digest: &str,
        effect_identity: &str,
        action: &str,
        target: &str,
        observed_state: &str,
        observed_plan: &str,
        executor: &str,
        event_id: &str,
    ) -> Result<Permit> {
        let lease = self.journal.lease()?;
        let outcome = (|| -> Result<Permit> {
            let conn = self.journal.connection();
            let authority = self.load_authority(authority_digest)?;
            let epoch = self.next_epoch()?;

            authority.admits(action, target)?;

            if let Some(expires) = authority.expires_at_epoch {
                if epoch >= expires {
                    return Err(GateError::AuthorityStale(format!(
                        "authority {:?} expired at epoch {expires}; it is now {epoch}",
                        authority.authority_id
                    )));
                }
            }

            if let Some(stale) = authority.staleness(observed_state, observed_plan) {
                return Err(GateError::AuthorityStale(format!(
                    "authority {:?} is stale: {stale}. The persistence gate has closed \
                     again and needs fresh authorization.",
                    authority.authority_id
                )));
            }

            let pending: Option<(String, i64)> = conn
                .query_row(
                    "SELECT outcome,needs_reconciliation FROM effect_outcomes \
                     WHERE effect_identity=?1",
                    params![effect_identity],
                    |row| Ok((row.get(0)?, row.get(1)?)),
                )
                .ok();
            if let Some((prior, needs)) = pending {
                if needs != 0 {
                    return Err(GateError::ReconciliationRequired(format!(
                        "effect {effect_identity:?} has an unresolved outcome ({prior}); \
                         read back the real state and reconcile before attempting it again. \
                         Retrying an unknown effect is how one attempt becomes two."
                    )));
                }
            }

            let active: Option<String> = conn
                .query_row(
                    "SELECT permit_id FROM permits WHERE target=?1 AND state='ACTIVE' LIMIT 1",
                    params![target],
                    |row| row.get(0),
                )
                .ok();
            if let Some(other) = active {
                return Err(GateError::Refused(format!(
                    "target {target:?} already has an active permit ({other}); two executors \
                     holding live permits for one target is the split-brain this gate exists \
                     to prevent"
                )));
            }

            let taken: i64 = conn.query_row(
                "SELECT COUNT(*) FROM permits WHERE permit_id=?1",
                params![permit_id],
                |row| row.get(0),
            )?;
            if taken > 0 {
                return Err(GateError::Refused(format!(
                    "permit_id {permit_id:?} has already been issued"
                )));
            }

            let domain = if target.is_empty() {
                format!("capability:{action}")
            } else {
                target.to_string()
            };
            let current: Option<i64> = conn
                .query_row(
                    "SELECT epoch FROM domain_fences WHERE domain=?1",
                    params![domain],
                    |row| row.get(0),
                )
                .ok();
            let fencing_epoch = current.unwrap_or(0) + 1;
            conn.execute(
                "INSERT INTO domain_fences(domain,epoch) VALUES(?1,?2) \
                 ON CONFLICT(domain) DO UPDATE SET epoch=excluded.epoch",
                params![domain, fencing_epoch],
            )?;

            let permit = Permit {
                permit_id: permit_id.into(),
                authority_digest: authority_digest.into(),
                effect_identity: effect_identity.into(),
                capability: action.into(),
                target: target.into(),
                fencing_epoch,
                issued_at_epoch: epoch,
                state_digest: observed_state.into(),
                plan_digest: observed_plan.into(),
            };
            conn.execute(
                "INSERT INTO permits\
                 (permit_id,digest,authority_digest,effect_identity,capability,target,\
                  fencing_epoch,issued_at_epoch,state_digest,plan_digest,state,consumed_at_epoch)\
                 VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,'ACTIVE',NULL)",
                params![
                    permit.permit_id, permit.digest(), authority_digest, effect_identity,
                    action, target, fencing_epoch, epoch, observed_state, observed_plan
                ],
            )?;

            let mut payload: BTreeMap<String, Digestable> = BTreeMap::new();
            payload.insert("kind".into(), Digestable::String("gate.permit_issued".into()));
            payload.insert("permit_id".into(), Digestable::String(permit_id.into()));
            payload.insert("permit_digest".into(), Digestable::String(permit.digest()));
            payload.insert(
                "authority_digest".into(),
                Digestable::String(authority_digest.into()),
            );
            payload.insert(
                "effect_identity".into(),
                Digestable::String(effect_identity.into()),
            );
            payload.insert("fencing_epoch".into(), Digestable::Integer(fencing_epoch));

            let envelope = Envelope::new(
                event_id,
                Origin::M,
                "b1-local",
                executor,
                permit_id,
                epoch,
                // A permit is permission to attempt, not evidence of effect.
                EpistemicStatus::WorkingAssumption,
                Digestable::Object(payload),
                vec![],
                vec![],
                None,
            )
            .map_err(|e| GateError::Refused(format!("permit event invalid: {e}")))?;
            self.journal.append_in_lease(&envelope)?;
            Ok(permit)
        })();
        lease.finish(outcome.is_ok())?;
        outcome
    }

    /// Spend the permit atomically and record what actually happened.
    ///
    /// `observed_state` and `observed_plan` are re-checked here against the
    /// same authority validated at claim time. A check only at claim time
    /// would leave the interval during which the executor was working
    /// entirely unguarded.
    pub fn consume(
        &self,
        proof: &TransitionProof,
        observed_state: &str,
        observed_plan: &str,
        executor: &str,
        event_id: &str,
    ) -> Result<(String, String)> {
        proof.validate()?;

        let lease = self.journal.lease()?;
        let outcome = (|| -> Result<(String, String)> {
            let conn = self.journal.connection();

            let row: Option<(String, String, String, String, i64, String)> = conn
                .query_row(
                    "SELECT state,digest,effect_identity,target,fencing_epoch,authority_digest \
                     FROM permits WHERE permit_id=?1",
                    params![proof.permit_id],
                    |r| {
                        Ok((
                            r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?,
                        ))
                    },
                )
                .ok();
            let Some((state, digest, effect, target, fence, authority_digest)) = row else {
                return Err(GateError::Refused(format!(
                    "no permit {:?}; an effect reported without a permit was never authorized",
                    proof.permit_id
                )));
            };

            if state != "ACTIVE" {
                return Err(GateError::PermitSpent(format!(
                    "permit {:?} was already consumed; permits are one-time so that a \
                     replayed report cannot re-authorize a second attempt",
                    proof.permit_id
                )));
            }
            if proof.permit_digest != digest {
                return Err(GateError::Refused(format!(
                    "transition proof carries permit digest {}..., the issued permit is \
                     {}...; the proof does not describe the permit it claims",
                    &proof.permit_digest[..16],
                    &digest[..16]
                )));
            }
            if proof.effect_identity != effect {
                return Err(GateError::Refused(
                    "transition proof effect_identity does not match its permit".into(),
                ));
            }
            if proof.target != target {
                return Err(GateError::Refused(
                    "transition proof target does not match its permit".into(),
                ));
            }
            if proof.fencing_epoch != fence {
                return Err(GateError::Refused(format!(
                    "transition proof carries fence {}, permit holds {fence}; a superseded \
                     executor must not report",
                    proof.fencing_epoch
                )));
            }

            let domain = if target.is_empty() {
                format!("capability:{}", proof.target)
            } else {
                target.clone()
            };
            let current: Option<i64> = conn
                .query_row(
                    "SELECT epoch FROM domain_fences WHERE domain=?1",
                    params![domain],
                    |r| r.get(0),
                )
                .ok();
            if let Some(latest) = current {
                if proof.fencing_epoch < latest {
                    return Err(GateError::Refused(format!(
                        "fence {} is below the current {latest} for {domain:?}; this executor \
                         was superseded while it was working",
                        proof.fencing_epoch
                    )));
                }
            }

            // Effect-time authority revalidation.
            let authority = self.load_authority(&authority_digest)?;
            if let Some(stale) = authority.staleness(observed_state, observed_plan) {
                return Err(GateError::AuthorityStale(format!(
                    "authority {:?} went stale between claim and consume: {stale}. The \
                     effect is not recorded as authorized.",
                    authority.authority_id
                )));
            }

            let (projected, status, needs) = project_outcome(proof);
            let epoch = self.next_epoch()?;

            // Validate and consume in one transaction: the conditional UPDATE
            // plus a changes() check is what makes concurrent consumption lose
            // rather than interleave.
            let changed = conn.execute(
                "UPDATE permits SET state='CONSUMED',consumed_at_epoch=?1 \
                 WHERE permit_id=?2 AND state='ACTIVE'",
                params![epoch, proof.permit_id],
            )?;
            if changed != 1 {
                return Err(GateError::PermitSpent(format!(
                    "permit {:?} was consumed concurrently",
                    proof.permit_id
                )));
            }

            conn.execute(
                "INSERT INTO effect_outcomes\
                 (effect_identity,last_permit_id,outcome,epistemic_status,\
                  needs_reconciliation,proof_digest)\
                 VALUES(?1,?2,?3,?4,?5,?6)\
                 ON CONFLICT(effect_identity) DO UPDATE SET\
                  last_permit_id=excluded.last_permit_id, outcome=excluded.outcome,\
                  epistemic_status=excluded.epistemic_status,\
                  needs_reconciliation=excluded.needs_reconciliation,\
                  proof_digest=excluded.proof_digest",
                params![
                    proof.effect_identity, proof.permit_id, projected.as_str(), status,
                    if needs { 1 } else { 0 }, proof.digest()
                ],
            )?;

            let mut payload: BTreeMap<String, Digestable> = BTreeMap::new();
            payload.insert("kind".into(), Digestable::String("gate.effect_reported".into()));
            payload.insert(
                "permit_id".into(),
                Digestable::String(proof.permit_id.clone()),
            );
            payload.insert(
                "receipt_status".into(),
                Digestable::String(proof.receipt_status.as_str().into()),
            );
            payload.insert(
                "postcondition_verified".into(),
                Digestable::Bool(proof.postcondition_verified),
            );
            payload.insert("outcome".into(), Digestable::String(projected.as_str().into()));
            payload.insert("proof_digest".into(), Digestable::String(proof.digest()));
            if let Some(observed) = &proof.observed_effect_digest {
                payload.insert(
                    "observed_effect_digest".into(),
                    Digestable::String(observed.clone()),
                );
            }

            let envelope = Envelope::new(
                event_id,
                Origin::M,
                "b1-local",
                executor,
                proof.permit_id.clone(),
                epoch,
                b1_protocol::envelope::EpistemicStatus::parse(status).expect("known status"),
                Digestable::Object(payload),
                vec![],
                proof.postcondition_evidence.clone(),
                Some(EffectBinding {
                    effect_identity: proof.effect_identity.clone(),
                    authority_envelope_digest: authority_digest.clone(),
                    persistence_class: PersistenceClass::parse(&authority.persistence_class)
                        .expect("validated persistence class"),
                }),
            )
            .map_err(|e| GateError::Refused(format!("effect event invalid: {e}")))?;
            self.journal.append_in_lease(&envelope)?;
            Ok((projected.as_str().to_string(), status.to_string()))
        })();
        lease.finish(outcome.is_ok())?;
        outcome
    }

    pub fn permit_state(&self, permit_id: &str) -> Option<String> {
        self.journal
            .connection()
            .query_row(
                "SELECT state FROM permits WHERE permit_id=?1",
                params![permit_id],
                |r| r.get(0),
            )
            .ok()
    }

    pub fn effect_outcome(&self, effect_identity: &str) -> Option<(String, String, bool)> {
        self.journal
            .connection()
            .query_row(
                "SELECT outcome,epistemic_status,needs_reconciliation FROM effect_outcomes \
                 WHERE effect_identity=?1",
                params![effect_identity],
                |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, i64>(2)? != 0,
                    ))
                },
            )
            .ok()
    }

    pub fn domain_fence(&self, domain: &str) -> i64 {
        self.journal
            .connection()
            .query_row(
                "SELECT epoch FROM domain_fences WHERE domain=?1",
                params![domain],
                |r| r.get(0),
            )
            .unwrap_or(0)
    }
}
