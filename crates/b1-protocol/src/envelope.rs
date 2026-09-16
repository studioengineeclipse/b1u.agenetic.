//! The B1 Event Envelope v1, Rust peer.
//!
//! Mirrors `python/b1_protocol/envelope.py`. Schema:
//! `schemas/b1-event-envelope-v1.schema.json`.
//!
//! The envelope keeps four dimensions apart that are routinely collapsed:
//!
//! ```text
//! Origin  !=  Authority  !=  Executor  !=  Effect
//! ```
//!
//! and one more inside [`EpistemicStatus`]:
//!
//! ```text
//! Execution receipt  !=  Observed effect  !=  Objective postcondition
//! ```
//!
//! A provider returning success is a receipt. It justifies
//! `WorkingAssumption`. `Verified` requires that the resulting state was
//! independently observed.

use std::collections::BTreeMap;
use std::fmt;

use crate::canonical::{canonical_json_bytes, digest_value, CanonicalizationError, Digestable};

pub const SCHEMA_VERSION: &str = "b1-event-envelope-1";

/// Where an event came from. Never determines who authorised or executed it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Origin {
    /// User-literal: explicitly requested, stated, constrained or authorised.
    U,
    /// Model-derived: a subgoal derived because it materially advances U.
    M,
    /// Platform-generated: introduced by a runtime, service or toolchain.
    P,
    /// Emergent: no single actor cleanly explains the resulting state.
    E,
}

/// How well supported a record's claim is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EpistemicStatus {
    Verified,
    WorkingAssumption,
    Unknown,
    InDoubt,
}

/// Recovery characteristics, classified *before* the effect happens.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PersistenceClass {
    Reversible,
    Compensatable,
    Irreversible,
    Unknown,
}

macro_rules! str_enum {
    ($ty:ty, $($variant:path => $text:literal),+ $(,)?) => {
        impl $ty {
            pub fn as_str(self) -> &'static str {
                match self { $($variant => $text),+ }
            }
            pub fn parse(value: &str) -> Option<Self> {
                match value { $($text => Some($variant),)+ _ => None }
            }
        }
    };
}

str_enum!(Origin, Origin::U => "U", Origin::M => "M", Origin::P => "P", Origin::E => "E");
str_enum!(
    EpistemicStatus,
    EpistemicStatus::Verified => "VERIFIED",
    EpistemicStatus::WorkingAssumption => "WORKING_ASSUMPTION",
    EpistemicStatus::Unknown => "UNKNOWN",
    EpistemicStatus::InDoubt => "IN_DOUBT",
);
str_enum!(
    PersistenceClass,
    PersistenceClass::Reversible => "REVERSIBLE",
    PersistenceClass::Compensatable => "COMPENSATABLE",
    PersistenceClass::Irreversible => "IRREVERSIBLE",
    PersistenceClass::Unknown => "UNKNOWN",
);

/// An envelope violates the v1 contract.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnvelopeError(pub String);

impl fmt::Display for EnvelopeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for EnvelopeError {}

impl From<CanonicalizationError> for EnvelopeError {
    fn from(err: CanonicalizationError) -> Self {
        EnvelopeError(format!("payload is not digestable: {err}"))
    }
}

/// The persistent-effect fields, which are all-or-nothing.
///
/// Modelling these as one struct rather than three independent `Option`s is the
/// point: an effect record cannot be constructed that names what changed
/// without also naming what authorised it and how recoverable it is. In the
/// Python peer that has to be a runtime check; here the type system carries it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EffectBinding {
    pub effect_identity: String,
    /// SHA-256 of the authority envelope that permitted this effect.
    pub authority_envelope_digest: String,
    pub persistence_class: PersistenceClass,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Envelope {
    pub event_id: String,
    pub origin: Origin,
    pub causal_parents: Vec<String>,
    pub program_identity: String,
    pub execution_identity: String,
    pub attempt_identity: String,
    pub epoch: i64,
    pub evidence_refs: Vec<String>,
    pub epistemic_status: EpistemicStatus,
    pub payload: Digestable,
    pub payload_digest: String,
    pub effect: Option<EffectBinding>,
}

fn valid_id(value: &str) -> bool {
    if value.is_empty() || value.len() > 128 {
        return false;
    }
    let mut chars = value.chars();
    let first = chars.next().expect("non-empty");
    if !first.is_ascii_alphanumeric() {
        return false;
    }
    value
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | ':' | '-'))
}

fn valid_hex256(value: &str) -> bool {
    value.len() == 64 && value.chars().all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c))
}

impl Envelope {
    /// Build an envelope, computing `payload_digest` rather than accepting one.
    ///
    /// A caller-supplied digest is a place for the digest and the payload to
    /// disagree, so there is no constructor that takes it.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        event_id: impl Into<String>,
        origin: Origin,
        program_identity: impl Into<String>,
        execution_identity: impl Into<String>,
        attempt_identity: impl Into<String>,
        epoch: i64,
        epistemic_status: EpistemicStatus,
        payload: Digestable,
        causal_parents: Vec<String>,
        evidence_refs: Vec<String>,
        effect: Option<EffectBinding>,
    ) -> Result<Self, EnvelopeError> {
        let payload_digest = digest_value(&payload);
        let envelope = Envelope {
            event_id: event_id.into(),
            origin,
            causal_parents,
            program_identity: program_identity.into(),
            execution_identity: execution_identity.into(),
            attempt_identity: attempt_identity.into(),
            epoch,
            evidence_refs,
            epistemic_status,
            payload,
            payload_digest,
            effect,
        };
        envelope.validate()?;
        Ok(envelope)
    }

    pub fn validate(&self) -> Result<(), EnvelopeError> {
        if !valid_id(&self.event_id) {
            return Err(EnvelopeError(format!(
                "event_id {:?} is not a valid identifier",
                self.event_id
            )));
        }
        // Program, execution and attempt identity are three separate dimensions:
        // which logical workflow, which concrete executor, which specific try.
        // A retry is a new attempt and may be a new executor, so none of these
        // may be blank and stand in for another.
        for (label, value) in [
            ("program_identity", &self.program_identity),
            ("execution_identity", &self.execution_identity),
            ("attempt_identity", &self.attempt_identity),
        ] {
            if value.is_empty() {
                return Err(EnvelopeError(format!("{label} must be non-empty")));
            }
        }
        if self.epoch < 0 {
            return Err(EnvelopeError(format!(
                "epoch must be a non-negative integer, got {}",
                self.epoch
            )));
        }

        let mut seen = std::collections::BTreeSet::new();
        for parent in &self.causal_parents {
            if !valid_id(parent) {
                return Err(EnvelopeError(format!(
                    "causal parent {parent:?} is not a valid identifier"
                )));
            }
            if !seen.insert(parent.clone()) {
                return Err(EnvelopeError(format!("causal parent {parent:?} is repeated")));
            }
        }
        if seen.contains(&self.event_id) {
            return Err(EnvelopeError(format!(
                "event {:?} lists itself as a causal parent",
                self.event_id
            )));
        }

        let unique_refs: std::collections::BTreeSet<_> = self.evidence_refs.iter().collect();
        if unique_refs.len() != self.evidence_refs.len() {
            return Err(EnvelopeError("evidence_refs must be unique".to_string()));
        }
        if self.evidence_refs.iter().any(|r| r.is_empty()) {
            return Err(EnvelopeError(
                "evidence_refs must be non-empty strings".to_string(),
            ));
        }

        if !valid_hex256(&self.payload_digest) {
            return Err(EnvelopeError(
                "payload_digest must be 64 lowercase hex characters".to_string(),
            ));
        }
        let actual = digest_value(&self.payload);
        if actual != self.payload_digest {
            return Err(EnvelopeError(format!(
                "payload_digest does not match payload: declared {}, computed {actual}",
                self.payload_digest
            )));
        }

        if let Some(effect) = &self.effect {
            if effect.effect_identity.is_empty() {
                return Err(EnvelopeError(
                    "effect_identity must be non-empty when present".to_string(),
                ));
            }
            if !valid_hex256(&effect.authority_envelope_digest) {
                return Err(EnvelopeError(
                    "authority_envelope_digest must be 64 lowercase hex characters".to_string(),
                ));
            }
        }
        Ok(())
    }

    /// The exact mapping that gets canonicalised.
    ///
    /// Optional fields are omitted when absent rather than emitted as null, so
    /// that one envelope has exactly one canonical form.
    pub fn to_canonical(&self) -> Digestable {
        let mut map: BTreeMap<String, Digestable> = BTreeMap::new();
        map.insert("schema_version".into(), Digestable::String(SCHEMA_VERSION.into()));
        map.insert("event_id".into(), Digestable::String(self.event_id.clone()));
        map.insert("origin".into(), Digestable::String(self.origin.as_str().into()));
        map.insert(
            "causal_parents".into(),
            Digestable::Array(
                self.causal_parents.iter().cloned().map(Digestable::String).collect(),
            ),
        );
        map.insert(
            "program_identity".into(),
            Digestable::String(self.program_identity.clone()),
        );
        map.insert(
            "execution_identity".into(),
            Digestable::String(self.execution_identity.clone()),
        );
        map.insert(
            "attempt_identity".into(),
            Digestable::String(self.attempt_identity.clone()),
        );
        map.insert("epoch".into(), Digestable::Integer(self.epoch));
        map.insert(
            "evidence_refs".into(),
            Digestable::Array(
                self.evidence_refs.iter().cloned().map(Digestable::String).collect(),
            ),
        );
        map.insert(
            "epistemic_status".into(),
            Digestable::String(self.epistemic_status.as_str().into()),
        );
        map.insert(
            "payload_digest".into(),
            Digestable::String(self.payload_digest.clone()),
        );
        map.insert("payload".into(), self.payload.clone());

        if let Some(effect) = &self.effect {
            map.insert(
                "effect_identity".into(),
                Digestable::String(effect.effect_identity.clone()),
            );
            map.insert(
                "authority_envelope_digest".into(),
                Digestable::String(effect.authority_envelope_digest.clone()),
            );
            map.insert(
                "persistence_class".into(),
                Digestable::String(effect.persistence_class.as_str().into()),
            );
        }
        Digestable::Object(map)
    }

    pub fn canonical_bytes(&self) -> Vec<u8> {
        canonical_json_bytes(&self.to_canonical())
    }

    pub fn digest(&self) -> String {
        digest_value(&self.to_canonical())
    }

    /// Rebuild an envelope from its canonical mapping, validating it.
    ///
    /// Unknown fields are refused rather than ignored: a reader that silently
    /// drops a field it does not understand will happily process a record whose
    /// meaning it has not grasped.
    pub fn from_canonical(value: &Digestable) -> Result<Self, EnvelopeError> {
        let Digestable::Object(map) = value else {
            return Err(EnvelopeError("envelope must be a JSON object".into()));
        };

        const KNOWN: &[&str] = &[
            "schema_version", "event_id", "origin", "causal_parents", "program_identity",
            "execution_identity", "attempt_identity", "effect_identity",
            "authority_envelope_digest", "epoch", "evidence_refs", "epistemic_status",
            "persistence_class", "payload_digest", "payload",
        ];
        let unknown: Vec<&String> = map.keys().filter(|k| !KNOWN.contains(&k.as_str())).collect();
        if !unknown.is_empty() {
            return Err(EnvelopeError(format!("unknown envelope fields: {unknown:?}")));
        }

        fn text<'a>(
            map: &'a BTreeMap<String, Digestable>,
            key: &str,
        ) -> Result<&'a str, EnvelopeError> {
            match map.get(key) {
                Some(Digestable::String(s)) => Ok(s),
                Some(_) => Err(EnvelopeError(format!("{key} must be a string"))),
                None => Err(EnvelopeError(format!("missing required envelope field: {key}"))),
            }
        }
        fn list(
            map: &BTreeMap<String, Digestable>,
            key: &str,
        ) -> Result<Vec<String>, EnvelopeError> {
            match map.get(key) {
                Some(Digestable::Array(items)) => items
                    .iter()
                    .map(|item| match item {
                        Digestable::String(s) => Ok(s.clone()),
                        _ => Err(EnvelopeError(format!("{key} entries must be strings"))),
                    })
                    .collect(),
                Some(_) => Err(EnvelopeError(format!("{key} must be an array"))),
                None => Ok(Vec::new()),
            }
        }

        let schema_version = text(map, "schema_version")?;
        if schema_version != SCHEMA_VERSION {
            return Err(EnvelopeError(format!(
                "unknown schema_version {schema_version:?}; refusing to guess at it"
            )));
        }

        let origin = Origin::parse(text(map, "origin")?)
            .ok_or_else(|| EnvelopeError("origin must be one of U, M, P, E".into()))?;
        let epistemic_status = EpistemicStatus::parse(text(map, "epistemic_status")?)
            .ok_or_else(|| {
                EnvelopeError(
                    "epistemic_status must be one of VERIFIED, WORKING_ASSUMPTION, UNKNOWN, IN_DOUBT"
                        .into(),
                )
            })?;
        let epoch = match map.get("epoch") {
            Some(Digestable::Integer(i)) => *i,
            Some(_) => return Err(EnvelopeError("epoch must be an integer".into())),
            None => return Err(EnvelopeError("missing required envelope field: epoch".into())),
        };
        let payload = map
            .get("payload")
            .ok_or_else(|| EnvelopeError("missing required envelope field: payload".into()))?
            .clone();

        // An effect record must name all three effect fields or none. A partial
        // binding is the shape an unauthorised effect takes when it is written
        // down carelessly, so it is refused here rather than repaired.
        let has = |key: &str| map.contains_key(key);
        let effect = match (
            has("effect_identity"),
            has("authority_envelope_digest"),
            has("persistence_class"),
        ) {
            (false, false, false) => None,
            (true, true, true) => Some(EffectBinding {
                effect_identity: text(map, "effect_identity")?.to_string(),
                authority_envelope_digest: text(map, "authority_envelope_digest")?.to_string(),
                persistence_class: PersistenceClass::parse(text(map, "persistence_class")?)
                    .ok_or_else(|| {
                        EnvelopeError(
                            "persistence_class must be one of REVERSIBLE, COMPENSATABLE, \
                             IRREVERSIBLE, UNKNOWN"
                                .into(),
                        )
                    })?,
            }),
            (true, _, _) => {
                return Err(EnvelopeError(
                    "an effect record must carry effect_identity, authority_envelope_digest \
                     and persistence_class together: an effect without bound authority is \
                     unauthorised by construction, and recovery limits must be classified \
                     before the effect"
                        .into(),
                ))
            }
            _ => {
                return Err(EnvelopeError(
                    "authority_envelope_digest and persistence_class are meaningless without \
                     effect_identity"
                        .into(),
                ))
            }
        };

        let envelope = Envelope {
            event_id: text(map, "event_id")?.to_string(),
            origin,
            causal_parents: list(map, "causal_parents")?,
            program_identity: text(map, "program_identity")?.to_string(),
            execution_identity: text(map, "execution_identity")?.to_string(),
            attempt_identity: text(map, "attempt_identity")?.to_string(),
            epoch,
            evidence_refs: list(map, "evidence_refs")?,
            epistemic_status,
            payload,
            payload_digest: text(map, "payload_digest")?.to_string(),
            effect,
        };
        envelope.validate()?;
        Ok(envelope)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn payload() -> Digestable {
        let mut map = BTreeMap::new();
        map.insert("kind".to_string(), Digestable::String("test".into()));
        Digestable::Object(map)
    }

    fn basic() -> Envelope {
        Envelope::new(
            "evt-1", Origin::U, "b1-local", "rust-core-1", "attempt-1", 1,
            EpistemicStatus::WorkingAssumption, payload(), vec![], vec![], None,
        )
        .expect("valid envelope")
    }

    #[test]
    fn round_trips_through_its_canonical_form() {
        let envelope = basic();
        let rebuilt = Envelope::from_canonical(&envelope.to_canonical()).unwrap();
        assert_eq!(envelope, rebuilt);
        assert_eq!(envelope.digest(), rebuilt.digest());
    }

    #[test]
    fn a_tampered_payload_digest_is_refused() {
        let mut envelope = basic();
        envelope.payload_digest = "0".repeat(64);
        let err = envelope.validate().unwrap_err();
        assert!(err.0.contains("does not match payload"), "{}", err.0);
    }

    #[test]
    fn an_effect_without_authority_cannot_be_read_back() {
        let Digestable::Object(mut map) = basic().to_canonical() else { unreachable!() };
        map.insert("effect_identity".into(), Digestable::String("fs-write-1".into()));
        let err = Envelope::from_canonical(&Digestable::Object(map)).unwrap_err();
        assert!(err.0.contains("unauthorised by construction"), "{}", err.0);
    }

    #[test]
    fn persistence_class_without_an_effect_is_refused() {
        let Digestable::Object(mut map) = basic().to_canonical() else { unreachable!() };
        map.insert("persistence_class".into(), Digestable::String("REVERSIBLE".into()));
        let err = Envelope::from_canonical(&Digestable::Object(map)).unwrap_err();
        assert!(err.0.contains("meaningless without effect_identity"), "{}", err.0);
    }

    #[test]
    fn unknown_fields_are_refused_rather_than_ignored() {
        let Digestable::Object(mut map) = basic().to_canonical() else { unreachable!() };
        map.insert("smuggled".into(), Digestable::String("x".into()));
        let err = Envelope::from_canonical(&Digestable::Object(map)).unwrap_err();
        assert!(err.0.contains("unknown envelope fields"), "{}", err.0);
    }

    #[test]
    fn an_unrecognised_schema_version_is_refused() {
        let Digestable::Object(mut map) = basic().to_canonical() else { unreachable!() };
        map.insert("schema_version".into(), Digestable::String("b1-event-envelope-2".into()));
        let err = Envelope::from_canonical(&Digestable::Object(map)).unwrap_err();
        assert!(err.0.contains("refusing to guess"), "{}", err.0);
    }

    #[test]
    fn an_event_cannot_be_its_own_causal_parent() {
        let err = Envelope::new(
            "evt-1", Origin::M, "b1-local", "rust-core-1", "attempt-1", 1,
            EpistemicStatus::Unknown, payload(), vec!["evt-1".into()], vec![], None,
        )
        .unwrap_err();
        assert!(err.0.contains("itself as a causal parent"), "{}", err.0);
    }

    #[test]
    fn a_negative_epoch_is_refused() {
        let err = Envelope::new(
            "evt-1", Origin::M, "b1-local", "rust-core-1", "attempt-1", -1,
            EpistemicStatus::Unknown, payload(), vec![], vec![], None,
        )
        .unwrap_err();
        assert!(err.0.contains("non-negative"), "{}", err.0);
    }
}
