//! A Rust gate peer, driven from the command line.
//!
//! Consumed by `tools/verify_cross_language_gate.py`, which races this process
//! against a Python peer on one database file and requires that exactly one
//! permit issues and exactly one effect lands in history.
//!
//! The race has to cross a process boundary. Two implementations linked into
//! one runtime can share a bug and can share a transaction; two processes
//! contending for one SQLite lease cannot. This binary exists so the dual-peer
//! claim is tested the way it will actually be used.
//!
//! One JSON object per invocation on stdout, always, including on refusal —
//! the verifier needs the *kind* of refusal, not just a non-zero exit, because
//! losing by the gate's rule and losing by a database lock are different
//! outcomes and only one of them is the design working.
//!
//! The policy file is positional and required. A peer that could be started
//! without one would have to assume a policy, and a gate whose standing rules
//! depend on how a process was launched has no standing rules.
//!
//! Usage:
//!   b1-gate-peer <db> <policy.json> digest-authority <authority.json>
//!   b1-gate-peer <db> <policy.json> grant            <authority.json> <event_id>
//!   b1-gate-peer <db> <policy.json> claim            <request.json>
//!   b1-gate-peer <db> <policy.json> consume          <request.json>
//!   b1-gate-peer <db> <policy.json> inspect          <effect_identity>
//!   b1-gate-peer <db> <policy.json> policy-digest

use std::collections::BTreeMap;
use std::fs;

use b1_authority::authority::{AuthorityEnvelope, ReceiptStatus, TransitionProof};
use b1_authority::gate::{CommitGate, GateError};
use b1_policy::CapabilityPolicy;
use b1_protocol::canonical::Digestable;
use b1_state::journal::RootJournal;

fn read_json(path: &str) -> Result<serde_json::Value, String> {
    let text = fs::read_to_string(path).map_err(|e| format!("cannot read {path}: {e}"))?;
    serde_json::from_str(&text).map_err(|e| format!("{path} is not valid JSON: {e}"))
}

fn authority_from(path: &str) -> Result<AuthorityEnvelope, String> {
    let parsed = read_json(path)?;
    let value = Digestable::from_json(&parsed).map_err(|e| format!("{path}: {e}"))?;
    AuthorityEnvelope::from_canonical(&value).map_err(|e| format!("{path}: {e}"))
}

fn text<'a>(value: &'a serde_json::Value, key: &str) -> Result<&'a str, String> {
    value[key]
        .as_str()
        .ok_or_else(|| format!("request field {key:?} must be a string"))
}

fn emit(payload: serde_json::Value) -> ! {
    println!("{payload}");
    std::process::exit(0)
}

/// Report a refusal as data rather than as an exit code.
///
/// `kind` is the discriminant the verifier asserts on. A gate that only
/// appeared to work because SQLite returned SQLITE_BUSY would show up here as
/// `SQLITE_ERROR`, which is exactly what the verifier must be able to catch.
fn refused(kind: &str, message: String) -> ! {
    emit(serde_json::json!({"status": "REFUSED", "kind": kind, "message": message}))
}

fn fail(message: String) -> ! {
    emit(serde_json::json!({"status": "ERROR", "message": message}))
}

fn policy_from(path: &str) -> Result<CapabilityPolicy, String> {
    let parsed = read_json(path)?;
    let value = Digestable::from_json(&parsed).map_err(|e| format!("{path}: {e}"))?;
    CapabilityPolicy::from_canonical(&value).map_err(|e| format!("{path}: {e}"))
}

fn open(db: &str, policy: CapabilityPolicy) -> CommitGate {
    let journal = match RootJournal::open(db) {
        Ok(journal) => journal,
        Err(err) => fail(format!("cannot open journal: {err}")),
    };
    // Wait for the other peer's commit rather than giving up. Losing on a
    // timeout would look like the right answer for the wrong reason.
    if let Err(err) = journal
        .connection()
        .pragma_update(None, "busy_timeout", 20_000)
    {
        fail(format!("cannot set busy_timeout: {err}"));
    }
    match CommitGate::open(journal, policy) {
        Ok(gate) => gate,
        Err(err) => refused(err.kind(), err.to_string()),
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() < 3 {
        fail("usage: b1-gate-peer <db> <policy.json> <command> [args...]".into());
    }
    let (db, policy_path, command) = (&args[0], &args[1], args[2].as_str());
    let policy = match policy_from(policy_path) {
        Ok(p) => p,
        Err(e) => fail(e),
    };

    match command {
        // The policy digest as this peer computes it, without touching the
        // database. Both peers must agree on it before either grants anything,
        // because it is what an authority envelope binds itself to.
        "policy-digest" => emit(serde_json::json!({
            "status": "OK",
            "digest": policy.digest(),
            "policy_id": policy.policy_id,
        })),

        // Digest an authority envelope without touching the database, so the
        // verifier can confirm both peers compute the same digest before it
        // relies on that digest meaning the same thing to both.
        "digest-authority" => {
            let authority = match authority_from(&args[3]) {
                Ok(a) => a,
                Err(e) => fail(e),
            };
            emit(serde_json::json!({
                "status": "OK",
                "digest": authority.digest(),
                "canonical_hex": authority
                    .canonical_bytes()
                    .iter()
                    .map(|b| format!("{b:02x}"))
                    .collect::<String>(),
            }))
        }

        "grant" => {
            let authority = match authority_from(&args[3]) {
                Ok(a) => a,
                Err(e) => fail(e),
            };
            let gate = open(db, policy.clone());
            match gate.grant(&authority, &args[4], "rust-peer") {
                Ok(digest) => emit(serde_json::json!({"status": "OK", "digest": digest})),
                Err(err) => refused(err.kind(), err.to_string()),
            }
        }

        "claim" => {
            let request = match read_json(&args[3]) {
                Ok(r) => r,
                Err(e) => fail(e),
            };
            let fields: Result<Vec<&str>, String> = [
                "permit_id", "authority_digest", "effect_identity", "action", "target",
                "observed_state_digest", "observed_plan_digest", "executor", "event_id",
            ]
            .iter()
            .map(|key| text(&request, key))
            .collect();
            let fields = match fields {
                Ok(f) => f,
                Err(e) => fail(e),
            };

            let gate = open(db, policy.clone());
            match gate.claim_permit(
                fields[0], fields[1], fields[2], fields[3], fields[4], fields[5], fields[6],
                fields[7], fields[8],
            ) {
                Ok(permit) => emit(serde_json::json!({
                    "status": "OK",
                    "permit_id": permit.permit_id,
                    "permit_digest": permit.digest(),
                    "fencing_epoch": permit.fencing_epoch,
                    "issued_at_epoch": permit.issued_at_epoch,
                })),
                Err(err) => refused(err.kind(), err.to_string()),
            }
        }

        "consume" => {
            let request = match read_json(&args[3]) {
                Ok(r) => r,
                Err(e) => fail(e),
            };
            let receipt = match text(&request, "receipt_status") {
                Ok(r) => match ReceiptStatus::parse(r) {
                    Some(status) => status,
                    None => fail(format!("unknown receipt_status {r:?}")),
                },
                Err(e) => fail(e),
            };
            let evidence: Vec<String> = request["postcondition_evidence"]
                .as_array()
                .map(|items| {
                    items
                        .iter()
                        .filter_map(|v| v.as_str().map(str::to_string))
                        .collect()
                })
                .unwrap_or_default();

            let proof = TransitionProof {
                permit_id: match text(&request, "permit_id") {
                    Ok(v) => v.into(),
                    Err(e) => fail(e),
                },
                permit_digest: match text(&request, "permit_digest") {
                    Ok(v) => v.into(),
                    Err(e) => fail(e),
                },
                effect_identity: match text(&request, "effect_identity") {
                    Ok(v) => v.into(),
                    Err(e) => fail(e),
                },
                target: match text(&request, "target") {
                    Ok(v) => v.into(),
                    Err(e) => fail(e),
                },
                fencing_epoch: match request["fencing_epoch"].as_i64() {
                    Some(v) => v,
                    None => fail("fencing_epoch must be an integer".into()),
                },
                receipt_status: receipt,
                postcondition_verified: request["postcondition_verified"]
                    .as_bool()
                    .unwrap_or(false),
                observed_effect_digest: request["observed_effect_digest"]
                    .as_str()
                    .map(str::to_string),
                postcondition_evidence: evidence,
            };

            let gate = open(db, policy.clone());
            let observed_state = match text(&request, "observed_state_digest") {
                Ok(v) => v.to_string(),
                Err(e) => fail(e),
            };
            let observed_plan = match text(&request, "observed_plan_digest") {
                Ok(v) => v.to_string(),
                Err(e) => fail(e),
            };
            let event_id = match text(&request, "event_id") {
                Ok(v) => v.to_string(),
                Err(e) => fail(e),
            };
            let executor = match text(&request, "executor") {
                Ok(v) => v.to_string(),
                Err(e) => fail(e),
            };

            match gate.consume(&proof, &observed_state, &observed_plan, &executor, &event_id) {
                Ok((outcome, status)) => emit(serde_json::json!({
                    "status": "OK",
                    "outcome": outcome,
                    "epistemic_status": status,
                    "proof_digest": proof.digest(),
                })),
                Err(err) => refused(err.kind(), err.to_string()),
            }
        }

        "inspect" => {
            let gate = open(db, policy.clone());
            let head = match gate.journal().head() {
                Ok(head) => head,
                Err(err) => fail(format!("cannot read head: {err}")),
            };
            let report = match gate.journal().verify() {
                Ok(report) => report,
                Err(err) => fail(format!("cannot verify: {err}")),
            };
            let outcome = gate.effect_outcome(&args[3]);

            let mut payload: BTreeMap<String, serde_json::Value> = BTreeMap::new();
            payload.insert("status".into(), "OK".into());
            payload.insert("head_digest".into(), head.digest.into());
            payload.insert("record_count".into(), head.record_count.into());
            payload.insert("max_epoch".into(), head.max_epoch.into());
            payload.insert("verify_ok".into(), report.ok.into());
            payload.insert("findings".into(), report.findings.into());
            payload.insert(
                "effect_outcome".into(),
                match outcome {
                    Some((o, s, needs)) => serde_json::json!({
                        "outcome": o, "epistemic_status": s, "needs_reconciliation": needs
                    }),
                    None => serde_json::Value::Null,
                },
            );
            emit(serde_json::Value::Object(payload.into_iter().collect()))
        }

        other => fail(format!("unknown command {other:?}")),
    }
}

// Keep the unused-import lint honest about GateError, which is referenced only
// through its `kind()` method above.
#[allow(dead_code)]
fn _kind_is_used(err: &GateError) -> &'static str {
    err.kind()
}
