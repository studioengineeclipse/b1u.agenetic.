//! Root journal, Rust peer: append rules, tamper detection, replay determinism.
//!
//! The mutation cases are the point. A verifier that has never been shown a
//! broken journal is an untested verifier, so each case damages history in one
//! specific way and requires that the damage is named. These mirror
//! `python/tests/test_journal.py`: both peers must catch the same damage, or
//! "equal peers" is a slogan rather than a property.

use std::collections::BTreeMap;

use b1_protocol::canonical::Digestable;
use b1_protocol::envelope::{EffectBinding, Envelope, EpistemicStatus, Origin, PersistenceClass};
use b1_state::{projection_digest, JournalError, RootJournal, GENESIS};
use rusqlite::Connection;

struct Temp {
    dir: std::path::PathBuf,
}

impl Temp {
    fn new(tag: &str) -> Self {
        let dir = std::env::temp_dir().join(format!(
            "b1-journal-{tag}-{}-{:?}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).expect("temp dir");
        Temp { dir }
    }
    fn db(&self) -> std::path::PathBuf {
        self.dir.join("root.db")
    }
}

impl Drop for Temp {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn payload(id: &str) -> Digestable {
    let mut map = BTreeMap::new();
    map.insert("kind".to_string(), Digestable::String("test".into()));
    map.insert("id".to_string(), Digestable::String(id.into()));
    Digestable::Object(map)
}

fn envelope(event_id: &str, epoch: i64, parents: Vec<String>) -> Envelope {
    Envelope::new(
        event_id, Origin::M, "b1-local", "rust-core-1", "attempt-1", epoch,
        EpistemicStatus::WorkingAssumption, payload(event_id), parents, vec![], None,
    )
    .expect("valid envelope")
}

fn effect_envelope(event_id: &str, epoch: i64, effect: &str) -> Envelope {
    Envelope::new(
        event_id, Origin::M, "b1-local", "rust-core-1", "attempt-1", epoch,
        EpistemicStatus::WorkingAssumption, payload(event_id), vec![], vec![],
        Some(EffectBinding {
            effect_identity: effect.into(),
            authority_envelope_digest: "a".repeat(64),
            persistence_class: PersistenceClass::Reversible,
        }),
    )
    .expect("valid envelope")
}

// --- appending -----------------------------------------------------------

#[test]
fn an_empty_journal_is_anchored_at_genesis() {
    let temp = Temp::new("empty");
    let journal = RootJournal::open(temp.db()).unwrap();
    let head = journal.head().unwrap();
    assert_eq!(head.digest, GENESIS);
    assert_eq!(head.record_count, 0);
    assert_eq!(head.max_epoch, -1);
    assert!(journal.verify().unwrap().ok);
}

#[test]
fn appending_chains_each_record_to_its_predecessor() {
    let temp = Temp::new("chain");
    let journal = RootJournal::open(temp.db()).unwrap();
    let first = journal.append(&envelope("evt-1", 1, vec![])).unwrap();
    let second = journal
        .append(&envelope("evt-2", 1, vec!["evt-1".into()]))
        .unwrap();

    assert_eq!(first.prior_record_digest, GENESIS);
    assert_eq!(second.prior_record_digest, first.record_digest);
    assert_eq!(journal.head().unwrap().digest, second.record_digest);
    assert_eq!(journal.head().unwrap().record_count, 2);
    assert!(journal.verify().unwrap().ok);
}

#[test]
fn a_duplicate_event_id_is_refused_and_changes_nothing() {
    let temp = Temp::new("dupe");
    let journal = RootJournal::open(temp.db()).unwrap();
    journal.append(&envelope("evt-1", 1, vec![])).unwrap();
    let before = journal.head().unwrap();

    let err = journal.append(&envelope("evt-1", 1, vec![])).unwrap_err();
    assert!(matches!(err, JournalError::Refused(ref m) if m.contains("already in the journal")));
    assert_eq!(journal.head().unwrap(), before, "a refused append must change nothing");
}

#[test]
fn an_unknown_causal_parent_is_refused() {
    let temp = Temp::new("parent");
    let journal = RootJournal::open(temp.db()).unwrap();
    let err = journal
        .append(&envelope("evt-2", 1, vec!["evt-missing".into()]))
        .unwrap_err();
    assert!(
        matches!(err, JournalError::Refused(ref m) if m.contains("lineage cannot be replayed")),
        "{err}"
    );
}

// --- fencing -------------------------------------------------------------

#[test]
fn a_stale_epoch_is_rejected() {
    let temp = Temp::new("stale");
    let journal = RootJournal::open(temp.db()).unwrap();
    journal.append(&envelope("evt-1", 5, vec![])).unwrap();
    let err = journal.append(&envelope("evt-late", 4, vec![])).unwrap_err();
    assert!(
        matches!(err, JournalError::StaleEpoch(ref m) if m.contains("below the committed fence")),
        "{err}"
    );
}

#[test]
fn the_same_epoch_is_still_accepted() {
    // Fencing rejects a superseded fence, not concurrent records at the current
    // one. Rejecting equal epochs would stop one execution recording two events.
    let temp = Temp::new("same-epoch");
    let journal = RootJournal::open(temp.db()).unwrap();
    journal.append(&envelope("evt-1", 5, vec![])).unwrap();
    journal.append(&envelope("evt-2", 5, vec![])).unwrap();
    assert_eq!(journal.head().unwrap().record_count, 2);
}

#[test]
fn a_repeated_effect_at_the_same_fence_is_refused() {
    let temp = Temp::new("dup-effect");
    let journal = RootJournal::open(temp.db()).unwrap();
    journal.append(&effect_envelope("evt-1", 1, "fs-write-1")).unwrap();
    let err = journal
        .append(&effect_envelope("evt-2", 1, "fs-write-1"))
        .unwrap_err();
    assert!(
        matches!(err, JournalError::Refused(ref m) if m.contains("must carry a newer fence")),
        "{err}"
    );
}

#[test]
fn a_retry_of_the_same_effect_under_a_newer_fence_is_allowed() {
    // The retry shape the design requires: same intended effect, new attempt,
    // newer fence. The earlier record is kept, not overwritten.
    let temp = Temp::new("retry");
    let journal = RootJournal::open(temp.db()).unwrap();
    journal.append(&effect_envelope("evt-1", 1, "fs-write-1")).unwrap();
    journal.append(&effect_envelope("evt-2", 2, "fs-write-1")).unwrap();
    let ids: Vec<String> = journal
        .read_all()
        .unwrap()
        .iter()
        .map(|r| r.envelope.event_id.clone())
        .collect();
    assert_eq!(ids, vec!["evt-1".to_string(), "evt-2".to_string()]);
}

// --- determinism ---------------------------------------------------------

#[test]
fn the_same_event_sequence_produces_the_same_head() {
    let events: Vec<Envelope> = (1..=5)
        .map(|i| envelope(&format!("evt-{i}"), i, vec![]))
        .collect();

    let a = Temp::new("det-a");
    let b = Temp::new("det-b");
    let first = RootJournal::open(a.db()).unwrap();
    let second = RootJournal::open(b.db()).unwrap();
    for event in &events {
        first.append(event).unwrap();
        second.append(event).unwrap();
    }
    assert_eq!(first.head().unwrap().digest, second.head().unwrap().digest);
}

#[test]
fn a_head_can_be_predicted_without_a_database() {
    let temp = Temp::new("predict");
    let events: Vec<Envelope> = (1..=3)
        .map(|i| envelope(&format!("evt-{i}"), i, vec![]))
        .collect();
    let predicted = RootJournal::replay_digest(&events);

    let journal = RootJournal::open(temp.db()).unwrap();
    for event in &events {
        journal.append(event).unwrap();
    }
    assert_eq!(predicted, journal.head().unwrap().digest);
    assert_eq!(journal.replay_head().unwrap(), journal.head().unwrap().digest);
}

#[test]
fn reordering_two_events_changes_the_head() {
    let a = envelope("evt-a", 1, vec![]);
    let b = envelope("evt-b", 1, vec![]);
    assert_ne!(
        RootJournal::replay_digest(&[a.clone(), b.clone()]),
        RootJournal::replay_digest(&[b, a]),
        "order must be part of history, or a reordered journal would verify"
    );
}

#[test]
fn a_projection_can_be_destroyed_and_rebuilt_identically() {
    let temp = Temp::new("projection");
    {
        let journal = RootJournal::open(temp.db()).unwrap();
        for i in 1..=5 {
            journal.append(&envelope(&format!("evt-{i}"), i, vec![])).unwrap();
        }
    }
    // Everything derived is gone with the dropped connection; the journal remains.
    let reopened = RootJournal::open(temp.db()).unwrap();
    let rebuilt = projection_digest(&reopened.read_all().unwrap());
    let again = projection_digest(&reopened.read_all().unwrap());
    assert_eq!(rebuilt, again);
    assert!(reopened.verify().unwrap().ok);
}

// --- mutation detection --------------------------------------------------

fn populated(temp: &Temp) -> RootJournal {
    let journal = RootJournal::open(temp.db()).unwrap();
    for i in 1..=4 {
        journal.append(&envelope(&format!("evt-{i}"), i, vec![])).unwrap();
    }
    assert!(journal.verify().unwrap().ok, "control: an undamaged journal must verify");
    journal
}

fn damage(temp: &Temp, sql: &str) {
    let conn = Connection::open(temp.db()).expect("raw connection");
    conn.execute_batch(sql).expect("damage applied");
}

#[test]
fn a_truncated_tail_is_detected_by_the_committed_count() {
    // A prefix of a valid chain is itself a valid chain, so the chain alone
    // cannot see this. Only the separately committed record count can.
    let temp = Temp::new("truncate");
    let journal = populated(&temp);
    damage(&temp, "DELETE FROM root_journal WHERE seq=4");

    let report = journal.verify().unwrap();
    assert!(!report.ok);
    assert!(
        report.findings.iter().any(|f| f.contains("removed from the end")),
        "{:?}",
        report.findings
    );
}

#[test]
fn a_record_removed_from_the_middle_is_detected() {
    let temp = Temp::new("middle");
    let journal = populated(&temp);
    damage(&temp, "DELETE FROM root_journal WHERE seq=2");

    let report = journal.verify().unwrap();
    assert!(!report.ok);
    assert!(report.findings.iter().any(|f| f.contains("not contiguous")));
    assert!(report.findings.iter().any(|f| f.contains("chain expects")));
}

#[test]
fn a_forged_head_is_detected() {
    let temp = Temp::new("forged");
    let journal = populated(&temp);
    damage(
        &temp,
        &format!("UPDATE root_head SET head_digest='{}' WHERE id=1", "f".repeat(64)),
    );

    let report = journal.verify().unwrap();
    assert!(!report.ok);
    assert!(
        report.findings.iter().any(|f| f.contains("committed head is")),
        "{:?}",
        report.findings
    );
}

#[test]
fn a_rewritten_chain_link_is_detected() {
    let temp = Temp::new("link");
    let journal = populated(&temp);
    damage(
        &temp,
        &format!(
            "UPDATE root_journal SET prior_record_digest='{}' WHERE seq=3",
            "0".repeat(64)
        ),
    );

    let report = journal.verify().unwrap();
    assert!(!report.ok);
    assert!(report.findings.iter().any(|f| f.contains("chain expects")));
}

#[test]
fn an_edited_payload_is_detected() {
    let temp = Temp::new("edit");
    let journal = populated(&temp);

    // The edit keeps payload_digest consistent so the envelope still validates.
    // A mutation that fails validation proves nothing; this one is internally
    // coherent and must still be caught by the chain.
    let tampered = envelope("evt-2", 2, vec![]);
    let mut map = match tampered.to_canonical() {
        Digestable::Object(map) => map,
        _ => unreachable!(),
    };
    let new_payload = payload("tampered");
    map.insert("payload".into(), new_payload.clone());
    map.insert(
        "payload_digest".into(),
        Digestable::String(b1_protocol::digest_value(&new_payload)),
    );
    let json = String::from_utf8(b1_protocol::canonical_json_bytes(&Digestable::Object(map)))
        .unwrap();
    let conn = Connection::open(temp.db()).unwrap();
    conn.execute(
        "UPDATE root_journal SET envelope_json=?1 WHERE seq=2",
        rusqlite::params![json],
    )
    .unwrap();
    drop(conn);

    let report = journal.verify().unwrap();
    assert!(!report.ok);
    assert!(
        report.findings.iter().any(|f| f.contains("contents were altered")),
        "{:?}",
        report.findings
    );
}

#[test]
fn an_undamaged_journal_reports_no_findings() {
    // The control case. Without it, a verify() that always reported findings
    // would pass every test above.
    let temp = Temp::new("control");
    let journal = populated(&temp);
    let report = journal.verify().unwrap();
    assert!(report.ok);
    assert!(report.findings.is_empty());
    assert_eq!(report.record_count, 4);
}

#[test]
fn an_unknown_schema_version_is_refused_rather_than_migrated() {
    let temp = Temp::new("schema");
    {
        let journal = RootJournal::open(temp.db()).unwrap();
        journal.append(&envelope("evt-1", 1, vec![])).unwrap();
    }
    damage(&temp, "UPDATE root_meta SET value='99' WHERE key='schema_version'");

    let err = RootJournal::open(temp.db()).unwrap_err();
    assert!(
        matches!(err, JournalError::Refused(ref m) if m.contains("will not guess at a migration")),
        "{err}"
    );
}
