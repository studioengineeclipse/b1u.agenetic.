//! B1 Local protocol: canonical serialization and the Event Envelope v1.
//!
//! This crate and `python/b1_protocol` are peers, not a primary and a port.
//! Neither is authoritative over the other; they are required to agree, and
//! `tools/verify_cross_language_digest.py` is what establishes that they do.

pub mod canonical;
pub mod envelope;

pub use canonical::{
    canonical_json_bytes, digest_bytes, digest_value, CanonicalizationError, Digestable,
};
pub use envelope::{
    EffectBinding, Envelope, EnvelopeError, EpistemicStatus, Origin, PersistenceClass,
    SCHEMA_VERSION,
};
