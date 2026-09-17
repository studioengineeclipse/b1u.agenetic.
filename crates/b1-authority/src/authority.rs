//! Authority envelopes and transition proofs, Rust peer.
//!
//! Design-derived-from: b1mu-omega13.9:src/b1mu/work/store.py
//!
//! Mirrors `python/b1_authority/authority.py`. The two must agree on the
//! canonical bytes of an authority envelope, because that digest is what an
//! effect record cites as its authorization — if the peers disagree about it,
//! an effect authorized under one peer is unauthorized under the other.
//! `tools/verify_cross_language_gate.py` is what establishes that they agree.
//!
//! An authority envelope carries what the handoff requires of it: proposed
//! action, target, scope, expected effect, relevant current state, current
//! plan, causal objective. The two that do the real work are `state_digest`
//! and `plan_digest`. Without them, "this authorization went stale" is a
//! judgement call; with them it is a comparison.

use std::collections::BTreeMap;
use std::fmt;

use b1_protocol::canonical::{canonical_json_bytes, digest_value, Digestable};

pub const AUTHORITY_SCHEMA_VERSION: &str = "b1-authority-envelope-1";
pub const PROOF_SCHEMA_VERSION: &str = "b1-transition-proof-1";

/// What the executor was told. `Unknown` is not a failure: a call that timed
/// out, or whose response was lost, is a call whose outcome nobody knows.
/// Treating it as failed is what produces a blind retry.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReceiptStatus {
    Succeeded,
    Failed,
    Unknown,
}

/// What the gate concluded, which is not the same thing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    Verified,
    NoEffect,
    InDoubt,
}

impl ReceiptStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            ReceiptStatus::Succeeded => "SUCCEEDED",
            ReceiptStatus::Failed => "FAILED",
            ReceiptStatus::Unknown => "UNKNOWN",
        }
    }
    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "SUCCEEDED" => Some(ReceiptStatus::Succeeded),
            "FAILED" => Some(ReceiptStatus::Failed),
            "UNKNOWN" => Some(ReceiptStatus::Unknown),
            _ => None,
        }
    }
}

impl Outcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Outcome::Verified => "VERIFIED",
            Outcome::NoEffect => "NO_EFFECT",
            Outcome::InDoubt => "IN_DOUBT",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityError(pub String);

impl fmt::Display for AuthorityError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for AuthorityError {}

fn valid_hex256(value: &str) -> bool {
    value.len() == 64 && value.chars().all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c))
}

fn valid_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.chars().next().is_some_and(|c| c.is_ascii_alphanumeric())
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | ':' | '-'))
}

/// Is `target` inside `scope`?
///
/// Exact match, or a prefix entry ending `/**`. A bare `**` admits everything
/// and must be written out, so granting unlimited scope is a visible act.
///
/// Deliberately not a general glob: a `*` that crosses `/` would let
/// `docs/*` admit `docs/secrets/key`, which is the one mistake this field
/// exists to prevent.
pub fn scope_admits(scope: &[String], target: &str) -> bool {
    scope.iter().any(|entry| {
        if entry == "**" {
            true
        } else if let Some(prefix) = entry.strip_suffix("**") {
            // prefix keeps its trailing slash, so "docs/**" -> "docs/"
            prefix.ends_with('/') && target.starts_with(prefix)
        } else {
            entry == target
        }
    })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AuthorityEnvelope {
    pub authority_id: String,
    pub proposed_action: String,
    pub target: String,
    pub scope: Vec<String>,
    pub expected_effect: String,
    pub state_digest: String,
    pub plan_digest: String,
    pub causal_objective: String,
    pub persistence_class: String,
    pub granted_at_epoch: i64,
    pub expires_at_epoch: Option<i64>,
}

impl AuthorityEnvelope {
    pub fn validate(&self) -> Result<(), AuthorityError> {
        if !valid_id(&self.authority_id) {
            return Err(AuthorityError(format!(
                "authority_id {:?} is not a valid identifier",
                self.authority_id
            )));
        }
        for (label, value) in [
            ("proposed_action", &self.proposed_action),
            ("target", &self.target),
            ("expected_effect", &self.expected_effect),
            ("causal_objective", &self.causal_objective),
        ] {
            if value.is_empty() {
                return Err(AuthorityError(format!(
                    "{label} must be non-empty: an envelope missing it cannot be compared \
                     against what actually happens"
                )));
            }
        }
        if self.scope.is_empty() {
            return Err(AuthorityError(
                "scope must be non-empty; unlimited scope is written as [\"**\"] so that \
                 granting it is a visible act rather than an omission"
                    .into(),
            ));
        }
        if !scope_admits(&self.scope, &self.target) {
            return Err(AuthorityError(format!(
                "target {:?} is outside its own scope {:?}; an envelope that does not admit \
                 its own target authorizes nothing",
                self.target, self.scope
            )));
        }
        for (label, value) in [
            ("state_digest", &self.state_digest),
            ("plan_digest", &self.plan_digest),
        ] {
            if !valid_hex256(value) {
                return Err(AuthorityError(format!(
                    "{label} must be 64 lowercase hex characters"
                )));
            }
        }
        if !matches!(
            self.persistence_class.as_str(),
            "REVERSIBLE" | "COMPENSATABLE" | "IRREVERSIBLE" | "UNKNOWN"
        ) {
            return Err(AuthorityError(format!(
                "persistence_class {:?} is not one of REVERSIBLE, COMPENSATABLE, \
                 IRREVERSIBLE, UNKNOWN",
                self.persistence_class
            )));
        }
        if self.granted_at_epoch < 0 {
            return Err(AuthorityError("granted_at_epoch must be non-negative".into()));
        }
        if let Some(expires) = self.expires_at_epoch {
            if expires <= self.granted_at_epoch {
                return Err(AuthorityError(format!(
                    "expires_at_epoch {expires} is not after granted_at_epoch {}; an \
                     authority that expires before it is granted authorizes nothing",
                    self.granted_at_epoch
                )));
            }
        }
        Ok(())
    }

    pub fn to_canonical(&self) -> Digestable {
        let mut map: BTreeMap<String, Digestable> = BTreeMap::new();
        map.insert(
            "schema_version".into(),
            Digestable::String(AUTHORITY_SCHEMA_VERSION.into()),
        );
        map.insert("authority_id".into(), Digestable::String(self.authority_id.clone()));
        map.insert(
            "proposed_action".into(),
            Digestable::String(self.proposed_action.clone()),
        );
        map.insert("target".into(), Digestable::String(self.target.clone()));
        map.insert(
            "scope".into(),
            Digestable::Array(self.scope.iter().cloned().map(Digestable::String).collect()),
        );
        map.insert(
            "expected_effect".into(),
            Digestable::String(self.expected_effect.clone()),
        );
        map.insert("state_digest".into(), Digestable::String(self.state_digest.clone()));
        map.insert("plan_digest".into(), Digestable::String(self.plan_digest.clone()));
        map.insert(
            "causal_objective".into(),
            Digestable::String(self.causal_objective.clone()),
        );
        map.insert(
            "persistence_class".into(),
            Digestable::String(self.persistence_class.clone()),
        );
        map.insert(
            "granted_at_epoch".into(),
            Digestable::Integer(self.granted_at_epoch),
        );
        // Omitted when absent, never emitted as null, so one envelope has one
        // canonical form.
        if let Some(expires) = self.expires_at_epoch {
            map.insert("expires_at_epoch".into(), Digestable::Integer(expires));
        }
        Digestable::Object(map)
    }

    pub fn canonical_bytes(&self) -> Vec<u8> {
        canonical_json_bytes(&self.to_canonical())
    }

    /// What an event envelope's `authority_envelope_digest` carries.
    ///
    /// Every field is inside the digest, so there is no way to widen a target
    /// or a scope while keeping the same authorization.
    pub fn digest(&self) -> String {
        digest_value(&self.to_canonical())
    }

    /// Does this authority admit `action` on `target`?
    pub fn admits(&self, action: &str, target: &str) -> Result<(), AuthorityError> {
        if action != self.proposed_action {
            return Err(AuthorityError(format!(
                "authority {:?} authorizes action {:?}, not {action:?}",
                self.authority_id, self.proposed_action
            )));
        }
        if target != self.target {
            return Err(AuthorityError(format!(
                "authority {:?} authorizes target {:?}, not {target:?}; authorization for \
                 one effect does not authorize another",
                self.authority_id, self.target
            )));
        }
        if !scope_admits(&self.scope, target) {
            return Err(AuthorityError(format!(
                "target {target:?} is outside scope {:?}",
                self.scope
            )));
        }
        Ok(())
    }

    /// Why this authority is stale, or `None` if it still holds.
    ///
    /// Called at effect time, and called *again* before the effect commits.
    /// Planning-time approval is not permanently sufficient, so a single check
    /// at claim time would leave a window in which the world moves and the
    /// authorization does not.
    pub fn staleness(&self, observed_state: &str, observed_plan: &str) -> Option<String> {
        if observed_state != self.state_digest {
            return Some(format!(
                "relevant state changed since authorization: authorized against {}..., \
                 now {}...",
                &self.state_digest[..16],
                &observed_state[..observed_state.len().min(16)]
            ));
        }
        if observed_plan != self.plan_digest {
            return Some(format!(
                "plan changed since authorization: authorized against {}..., now {}...",
                &self.plan_digest[..16],
                &observed_plan[..observed_plan.len().min(16)]
            ));
        }
        None
    }

    /// Rebuild from the canonical mapping, refusing unknown fields.
    pub fn from_canonical(value: &Digestable) -> Result<Self, AuthorityError> {
        let Digestable::Object(map) = value else {
            return Err(AuthorityError("authority must be a JSON object".into()));
        };
        const KNOWN: &[&str] = &[
            "schema_version", "authority_id", "proposed_action", "target", "scope",
            "expected_effect", "state_digest", "plan_digest", "causal_objective",
            "persistence_class", "granted_at_epoch", "expires_at_epoch",
        ];
        let unknown: Vec<&String> = map.keys().filter(|k| !KNOWN.contains(&k.as_str())).collect();
        if !unknown.is_empty() {
            return Err(AuthorityError(format!("unknown authority fields: {unknown:?}")));
        }

        fn text<'a>(
            map: &'a BTreeMap<String, Digestable>,
            key: &str,
        ) -> Result<&'a str, AuthorityError> {
            match map.get(key) {
                Some(Digestable::String(s)) => Ok(s),
                Some(_) => Err(AuthorityError(format!("{key} must be a string"))),
                None => Err(AuthorityError(format!("missing authority field: {key}"))),
            }
        }

        let schema = text(map, "schema_version")?;
        if schema != AUTHORITY_SCHEMA_VERSION {
            return Err(AuthorityError(format!(
                "unknown authority schema_version {schema:?}; refusing to guess at it"
            )));
        }
        let scope = match map.get("scope") {
            Some(Digestable::Array(items)) => items
                .iter()
                .map(|item| match item {
                    Digestable::String(s) => Ok(s.clone()),
                    _ => Err(AuthorityError("scope entries must be strings".into())),
                })
                .collect::<Result<Vec<String>, AuthorityError>>()?,
            _ => return Err(AuthorityError("scope must be an array".into())),
        };
        let granted_at_epoch = match map.get("granted_at_epoch") {
            Some(Digestable::Integer(i)) => *i,
            _ => return Err(AuthorityError("granted_at_epoch must be an integer".into())),
        };
        let expires_at_epoch = match map.get("expires_at_epoch") {
            Some(Digestable::Integer(i)) => Some(*i),
            Some(_) => return Err(AuthorityError("expires_at_epoch must be an integer".into())),
            None => None,
        };

        let envelope = AuthorityEnvelope {
            authority_id: text(map, "authority_id")?.to_string(),
            proposed_action: text(map, "proposed_action")?.to_string(),
            target: text(map, "target")?.to_string(),
            scope,
            expected_effect: text(map, "expected_effect")?.to_string(),
            state_digest: text(map, "state_digest")?.to_string(),
            plan_digest: text(map, "plan_digest")?.to_string(),
            causal_objective: text(map, "causal_objective")?.to_string(),
            persistence_class: text(map, "persistence_class")?.to_string(),
            granted_at_epoch,
            expires_at_epoch,
        };
        envelope.validate()?;
        Ok(envelope)
    }
}

/// What an executor must return after acting under a permit.
///
/// The three fields kept separate here are the whole point:
/// `receipt_status` is what the executor was told, `observed_effect_digest` is
/// what was independently read back, and `postcondition_verified` is whether
/// the objective actually holds. A provider returning success populates only
/// the first.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransitionProof {
    pub permit_id: String,
    pub permit_digest: String,
    pub effect_identity: String,
    pub target: String,
    pub fencing_epoch: i64,
    pub receipt_status: ReceiptStatus,
    pub postcondition_verified: bool,
    pub observed_effect_digest: Option<String>,
    pub postcondition_evidence: Vec<String>,
}

impl TransitionProof {
    pub fn validate(&self) -> Result<(), AuthorityError> {
        if !valid_id(&self.permit_id) {
            return Err(AuthorityError(format!(
                "permit_id {:?} is not a valid identifier",
                self.permit_id
            )));
        }
        if !valid_hex256(&self.permit_digest) {
            return Err(AuthorityError(
                "permit_digest must be 64 lowercase hex characters".into(),
            ));
        }
        if self.effect_identity.is_empty() {
            return Err(AuthorityError("effect_identity must be non-empty".into()));
        }
        if self.target.is_empty() {
            return Err(AuthorityError("target must be non-empty".into()));
        }
        if let Some(observed) = &self.observed_effect_digest {
            if !valid_hex256(observed) {
                return Err(AuthorityError(
                    "observed_effect_digest must be 64 lowercase hex characters when present"
                        .into(),
                ));
            }
        }
        if self.postcondition_verified && self.observed_effect_digest.is_none() {
            return Err(AuthorityError(
                "postcondition_verified is true but no observed_effect_digest was supplied; \
                 a postcondition cannot be verified without having observed the resulting state"
                    .into(),
            ));
        }
        if self.postcondition_verified && self.postcondition_evidence.is_empty() {
            return Err(AuthorityError(
                "postcondition_verified is true but no evidence was cited; a verified claim \
                 must say what establishes it"
                    .into(),
            ));
        }
        let unique: std::collections::BTreeSet<_> = self.postcondition_evidence.iter().collect();
        if unique.len() != self.postcondition_evidence.len() {
            return Err(AuthorityError("postcondition_evidence must be unique".into()));
        }
        Ok(())
    }

    pub fn to_canonical(&self) -> Digestable {
        let mut map: BTreeMap<String, Digestable> = BTreeMap::new();
        map.insert(
            "schema_version".into(),
            Digestable::String(PROOF_SCHEMA_VERSION.into()),
        );
        map.insert("permit_id".into(), Digestable::String(self.permit_id.clone()));
        map.insert(
            "permit_digest".into(),
            Digestable::String(self.permit_digest.clone()),
        );
        map.insert(
            "effect_identity".into(),
            Digestable::String(self.effect_identity.clone()),
        );
        map.insert("target".into(), Digestable::String(self.target.clone()));
        map.insert("fencing_epoch".into(), Digestable::Integer(self.fencing_epoch));
        map.insert(
            "receipt_status".into(),
            Digestable::String(self.receipt_status.as_str().into()),
        );
        map.insert(
            "postcondition_verified".into(),
            Digestable::Bool(self.postcondition_verified),
        );
        map.insert(
            "postcondition_evidence".into(),
            Digestable::Array(
                self.postcondition_evidence
                    .iter()
                    .cloned()
                    .map(Digestable::String)
                    .collect(),
            ),
        );
        if let Some(observed) = &self.observed_effect_digest {
            map.insert(
                "observed_effect_digest".into(),
                Digestable::String(observed.clone()),
            );
        }
        Digestable::Object(map)
    }

    pub fn digest(&self) -> String {
        digest_value(&self.to_canonical())
    }
}

/// Project a proof onto `(outcome, epistemic_status, needs_reconciliation)`.
///
/// Where the central discipline is enforced mechanically rather than by
/// intention:
///
/// ```text
/// Execution receipt != Observed effect != Objective postcondition
/// ```
///
/// An executor reporting success while the postcondition is unverified is the
/// most common way a system comes to believe something it has not established,
/// and it resolves to `InDoubt` here, never `Verified`.
pub fn project_outcome(proof: &TransitionProof) -> (Outcome, &'static str, bool) {
    match proof.receipt_status {
        // Whether the effect happened is unknown regardless of what a
        // postcondition check thinks it saw: the two could describe different
        // attempts.
        ReceiptStatus::Unknown => (Outcome::InDoubt, "IN_DOUBT", true),
        ReceiptStatus::Succeeded => {
            if proof.postcondition_verified {
                (Outcome::Verified, "VERIFIED", false)
            } else {
                // The call completed and the objective was not established.
                // Something may well have changed; nobody has checked what.
                (Outcome::InDoubt, "IN_DOUBT", true)
            }
        }
        ReceiptStatus::Failed => {
            if proof.observed_effect_digest.is_some() {
                // The only path to NoEffect, and it requires an observation: a
                // reported failure on its own does not prove nothing happened.
                (Outcome::NoEffect, "VERIFIED", false)
            } else {
                (Outcome::InDoubt, "IN_DOUBT", true)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn envelope() -> AuthorityEnvelope {
        AuthorityEnvelope {
            authority_id: "auth-1".into(),
            proposed_action: "fs.write".into(),
            target: "docs/x.md".into(),
            scope: vec!["docs/**".into()],
            expected_effect: "docs/x.md contains the plan".into(),
            state_digest: "a".repeat(64),
            plan_digest: "b".repeat(64),
            causal_objective: "ship phase B".into(),
            persistence_class: "REVERSIBLE".into(),
            granted_at_epoch: 0,
            expires_at_epoch: None,
        }
    }

    #[test]
    fn a_valid_envelope_round_trips_through_its_canonical_form() {
        let original = envelope();
        let rebuilt = AuthorityEnvelope::from_canonical(&original.to_canonical()).unwrap();
        assert_eq!(original, rebuilt);
        assert_eq!(original.digest(), rebuilt.digest());
    }

    #[test]
    fn a_wildcard_does_not_cross_a_directory_boundary() {
        let scope = vec!["docs/**".to_string()];
        assert!(scope_admits(&scope, "docs/a.md"));
        assert!(scope_admits(&scope, "docs/deep/b.md"));
        assert!(!scope_admits(&scope, "secrets/key"));
        assert!(!scope_admits(&scope, "docsecret"));
    }

    #[test]
    fn unlimited_scope_must_be_written_out() {
        assert!(scope_admits(&["**".to_string()], "anything/at/all"));
        assert!(!scope_admits(&[], "anything"));
    }

    #[test]
    fn an_envelope_that_does_not_admit_its_own_target_is_refused() {
        let mut bad = envelope();
        bad.target = "secrets/key".into();
        let err = bad.validate().unwrap_err();
        assert!(err.0.contains("outside its own scope"), "{}", err.0);
    }

    #[test]
    fn a_different_action_or_target_is_not_admitted() {
        let auth = envelope();
        assert!(auth.admits("fs.write", "docs/x.md").is_ok());
        assert!(auth
            .admits("fs.delete", "docs/x.md")
            .unwrap_err()
            .0
            .contains("authorizes action"));
        assert!(auth
            .admits("fs.write", "docs/y.md")
            .unwrap_err()
            .0
            .contains("does not authorize another"));
    }

    #[test]
    fn changed_state_or_plan_is_stale() {
        let auth = envelope();
        assert!(auth.staleness(&"a".repeat(64), &"b".repeat(64)).is_none());
        assert!(auth
            .staleness(&"c".repeat(64), &"b".repeat(64))
            .unwrap()
            .contains("relevant state changed"));
        assert!(auth
            .staleness(&"a".repeat(64), &"c".repeat(64))
            .unwrap()
            .contains("plan changed"));
    }

    #[test]
    fn an_expiry_before_the_grant_is_refused() {
        let mut bad = envelope();
        bad.granted_at_epoch = 5;
        bad.expires_at_epoch = Some(5);
        assert!(bad.validate().unwrap_err().0.contains("authorizes nothing"));
    }

    #[test]
    fn a_verified_postcondition_needs_an_observation_and_evidence() {
        let mut proof = TransitionProof {
            permit_id: "permit-1".into(),
            permit_digest: "a".repeat(64),
            effect_identity: "e-1".into(),
            target: "docs/x.md".into(),
            fencing_epoch: 1,
            receipt_status: ReceiptStatus::Succeeded,
            postcondition_verified: true,
            observed_effect_digest: None,
            postcondition_evidence: vec!["e".into()],
        };
        assert!(proof.validate().unwrap_err().0.contains("without having observed"));

        proof.observed_effect_digest = Some("b".repeat(64));
        proof.postcondition_evidence.clear();
        assert!(proof
            .validate()
            .unwrap_err()
            .0
            .contains("must say what establishes it"));
    }

    #[test]
    fn outcome_projection_matches_the_declared_table() {
        let base = TransitionProof {
            permit_id: "permit-1".into(),
            permit_digest: "a".repeat(64),
            effect_identity: "e-1".into(),
            target: "t".into(),
            fencing_epoch: 1,
            receipt_status: ReceiptStatus::Succeeded,
            postcondition_verified: false,
            observed_effect_digest: None,
            postcondition_evidence: vec![],
        };

        /// One row of the projection table: what the executor reported, and
        /// what the gate must conclude from it.
        struct Case {
            receipt: ReceiptStatus,
            postcondition_verified: bool,
            observed: Option<String>,
            expected: (Outcome, &'static str, bool),
        }

        let seen = || Some("b".repeat(64));
        let cases = [
            Case {
                receipt: ReceiptStatus::Succeeded,
                postcondition_verified: true,
                observed: seen(),
                expected: (Outcome::Verified, "VERIFIED", false),
            },
            // A provider reporting success with an unverified postcondition is
            // the most common way a system believes something it has not
            // established.
            Case {
                receipt: ReceiptStatus::Succeeded,
                postcondition_verified: false,
                observed: None,
                expected: (Outcome::InDoubt, "IN_DOUBT", true),
            },
            // The only path to NoEffect, and it requires an observation.
            Case {
                receipt: ReceiptStatus::Failed,
                postcondition_verified: false,
                observed: seen(),
                expected: (Outcome::NoEffect, "VERIFIED", false),
            },
            // A reported failure alone does not prove nothing happened.
            Case {
                receipt: ReceiptStatus::Failed,
                postcondition_verified: false,
                observed: None,
                expected: (Outcome::InDoubt, "IN_DOUBT", true),
            },
            Case {
                receipt: ReceiptStatus::Unknown,
                postcondition_verified: false,
                observed: None,
                expected: (Outcome::InDoubt, "IN_DOUBT", true),
            },
            // Unknown stays InDoubt even with an observation: the observation
            // and the receipt may describe different attempts.
            Case {
                receipt: ReceiptStatus::Unknown,
                postcondition_verified: true,
                observed: seen(),
                expected: (Outcome::InDoubt, "IN_DOUBT", true),
            },
        ];

        for case in &cases {
            let proof = TransitionProof {
                receipt_status: case.receipt,
                postcondition_verified: case.postcondition_verified,
                observed_effect_digest: case.observed.clone(),
                postcondition_evidence: if case.postcondition_verified {
                    vec!["e".into()]
                } else {
                    vec![]
                },
                ..base.clone()
            };
            assert_eq!(
                project_outcome(&proof),
                case.expected,
                "case {:?}/{}",
                case.receipt,
                case.postcondition_verified
            );
        }
    }
}
