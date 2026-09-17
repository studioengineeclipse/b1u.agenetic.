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
    project_outcome, scope_admits, AuthorityEnvelope, AuthorityError, Outcome, ReceiptStatus,
    TransitionProof, AUTHORITY_SCHEMA_VERSION, PROOF_SCHEMA_VERSION,
};
pub use gate::{CommitGate, GateError, Permit, GATE_SCHEMA_VERSION};
