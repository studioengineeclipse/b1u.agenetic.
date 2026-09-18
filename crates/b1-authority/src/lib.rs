//! B1 Local authority: the Dual-Core Commit Gate.
//!
//! Rust and Python are equal computational peers. Both may analyse, derive
//! subgoals, plan, simulate and propose; neither may cause a persistent effect
//! on its own. Everything consequential passes through the gate, which holds
//! one write lease at a time — the same lease the Python peer contends for, on
//! the same database file.

pub mod authority;
pub mod gate;

pub use authority::{
    project_outcome, AuthorityEnvelope, AuthorityError, Outcome, ReceiptStatus, TransitionProof,
    AUTHORITY_SCHEMA_VERSION, PROOF_SCHEMA_VERSION,
};
// Re-exported from b1-protocol so callers of this crate keep one import path
// for it. There is still exactly one implementation.
pub use b1_protocol::scope::{scope_admits, scope_is_valid};
pub use gate::{CommitGate, GateError, Permit, GATE_SCHEMA_VERSION};
