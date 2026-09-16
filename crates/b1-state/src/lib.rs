//! B1 Local state: the authoritative root journal.
//!
//! This crate and `python/b1_state` are peers. For the same event sequence they
//! must land on the same head digest; that is the property that lets either one
//! rebuild what the other wrote.

pub mod journal;

pub use journal::{
    chain_digest, projection_digest, Head, JournalError, JournalRecord, RootJournal,
    VerificationReport, GENESIS, SCHEMA_VERSION,
};
