//! Derived views over the root journal — the Rust peer of `python/b1_projection`.
//!
//! A projection is a rearrangement of history for reading. It contains nothing
//! the journal does not already contain, which is exactly why it can be thrown
//! away: if a projection and a replay disagree, the projection loses.
//!
//! Everything here is a pure function of the record slice, for the same reason
//! it is in Python: the three deployment modes are equivalent because building a
//! view cannot consult anything outside the records, not because three code
//! paths were separately tested into agreement.
//!
//! This peer builds the views and digests them. It deliberately does *not*
//! implement the storage modes. The claim under cross-language test is that two
//! independent implementations derive the same views from the same history;
//! where those bytes then land is a deployment decision, and duplicating the
//! file layout here would test the layout twice and the derivation once.

use std::collections::BTreeMap;

use b1_protocol::canonical::Digestable;
use b1_protocol::{canonical_json_bytes, digest_bytes};

use crate::journal::{chain_digest, JournalRecord, GENESIS};

/// Sorted, and relied upon to stay sorted: the Python peer writes one file per
/// name in modular mode, so a name present in one implementation and not the
/// other is a divergence the digest catches but the message would not explain.
pub const VIEW_NAMES: [&str; 4] = ["authority", "effects", "events", "heads"];

fn object(pairs: Vec<(&str, Digestable)>) -> Digestable {
    let mut map: BTreeMap<String, Digestable> = BTreeMap::new();
    for (key, value) in pairs {
        map.insert(key.to_string(), value);
    }
    Digestable::Object(map)
}

fn strings(values: &[String]) -> Digestable {
    Digestable::Array(values.iter().cloned().map(Digestable::String).collect())
}

/// One row per record, in journal order.
///
/// Optional fields are *omitted* rather than emitted as null. The canonical form
/// has no null, so this is not a stylistic choice: emitting one would make the
/// projection undigestable rather than merely differently shaped.
pub fn events_view(records: &[JournalRecord]) -> Digestable {
    Digestable::Array(
        records
            .iter()
            .map(|record| {
                let envelope = &record.envelope;
                let mut pairs = vec![
                    ("seq", Digestable::Integer(record.seq)),
                    ("event_id", Digestable::String(envelope.event_id.clone())),
                    (
                        "origin",
                        Digestable::String(envelope.origin.as_str().into()),
                    ),
                    ("epoch", Digestable::Integer(envelope.epoch)),
                    (
                        "epistemic_status",
                        Digestable::String(envelope.epistemic_status.as_str().into()),
                    ),
                    (
                        "record_digest",
                        Digestable::String(record.record_digest.clone()),
                    ),
                ];
                if !envelope.causal_parents.is_empty() {
                    pairs.push(("causal_parents", strings(&envelope.causal_parents)));
                }
                if let Some(effect) = &envelope.effect {
                    pairs.push((
                        "effect_identity",
                        Digestable::String(effect.effect_identity.clone()),
                    ));
                    pairs.push((
                        "authority_envelope_digest",
                        Digestable::String(effect.authority_envelope_digest.clone()),
                    ));
                    pairs.push((
                        "persistence_class",
                        Digestable::String(effect.persistence_class.as_str().into()),
                    ));
                }
                object(pairs)
            })
            .collect(),
    )
}

/// Scratch shape for folding; mirrors the Python dict exactly.
struct EffectRow {
    first_seq: i64,
    last_seq: i64,
    record_count: i64,
    authority_envelope_digests: Vec<String>,
    epistemic_status: String,
    persistence_class: String,
}

/// One row per effect identity, folded in journal order.
///
/// `epistemic_status` is the *last* status recorded for that effect. An effect
/// can land IN_DOUBT and later be reconciled to VERIFIED once real state has
/// been read back; keeping the first would report a resolved doubt as open, and
/// keeping the strongest would let an earlier VERIFIED outrank a later
/// IN_DOUBT, which is the receipt outliving the evidence.
pub fn effects_view(records: &[JournalRecord]) -> Digestable {
    let mut folded: BTreeMap<String, EffectRow> = BTreeMap::new();
    let mut order: Vec<String> = Vec::new();

    for record in records {
        let Some(effect) = &record.envelope.effect else {
            continue;
        };
        let identity = effect.effect_identity.clone();
        let row = folded.entry(identity.clone()).or_insert_with(|| {
            order.push(identity.clone());
            EffectRow {
                first_seq: record.seq,
                last_seq: record.seq,
                record_count: 0,
                authority_envelope_digests: Vec::new(),
                epistemic_status: String::new(),
                persistence_class: String::new(),
            }
        });
        row.last_seq = record.seq;
        row.record_count += 1;
        row.epistemic_status = record.envelope.epistemic_status.as_str().into();
        row.persistence_class = effect.persistence_class.as_str().into();
        if !row
            .authority_envelope_digests
            .contains(&effect.authority_envelope_digest)
        {
            row.authority_envelope_digests
                .push(effect.authority_envelope_digest.clone());
        }
    }

    Digestable::Array(
        order
            .iter()
            .map(|identity| {
                let row = &folded[identity];
                // Sorted, because "which authorities touched this effect" is a
                // set question and the answer must not depend on arrival order.
                let mut digests = row.authority_envelope_digests.clone();
                digests.sort();
                object(vec![
                    ("effect_identity", Digestable::String(identity.clone())),
                    ("first_seq", Digestable::Integer(row.first_seq)),
                    ("last_seq", Digestable::Integer(row.last_seq)),
                    ("record_count", Digestable::Integer(row.record_count)),
                    ("authority_envelope_digests", strings(&digests)),
                    (
                        "epistemic_status",
                        Digestable::String(row.epistemic_status.clone()),
                    ),
                    (
                        "persistence_class",
                        Digestable::String(row.persistence_class.clone()),
                    ),
                ])
            })
            .collect(),
    )
}

struct AuthorityRow {
    first_seq: i64,
    event_count: i64,
    effect_identities: Vec<String>,
}

/// One row per authority envelope digest, in first-seen order.
///
/// This answers "what was actually done under this authorization", which is the
/// question an audit asks and the one a grant-time record cannot answer alone.
pub fn authority_view(records: &[JournalRecord]) -> Digestable {
    let mut folded: BTreeMap<String, AuthorityRow> = BTreeMap::new();
    let mut order: Vec<String> = Vec::new();

    for record in records {
        let Some(effect) = &record.envelope.effect else {
            continue;
        };
        let digest = effect.authority_envelope_digest.clone();
        let row = folded.entry(digest.clone()).or_insert_with(|| {
            order.push(digest.clone());
            AuthorityRow {
                first_seq: record.seq,
                event_count: 0,
                effect_identities: Vec::new(),
            }
        });
        row.event_count += 1;
        if !row.effect_identities.contains(&effect.effect_identity) {
            row.effect_identities.push(effect.effect_identity.clone());
        }
    }

    Digestable::Array(
        order
            .iter()
            .map(|digest| {
                let row = &folded[digest];
                let mut identities = row.effect_identities.clone();
                identities.sort();
                object(vec![
                    (
                        "authority_envelope_digest",
                        Digestable::String(digest.clone()),
                    ),
                    ("first_seq", Digestable::Integer(row.first_seq)),
                    ("event_count", Digestable::Integer(row.event_count)),
                    ("effect_identities", strings(&identities)),
                ])
            })
            .collect(),
    )
}

/// Counts, the epoch ceiling, and both heads.
///
/// `chain_head` is what the last record claims; `replayed_head` is recomputed
/// from the envelopes. On an intact journal they agree. A projection built over
/// a damaged one must not launder the damage into a clean-looking summary, so
/// `consistent` reports the disagreement rather than picking a side.
pub fn heads_view(records: &[JournalRecord]) -> Digestable {
    let mut prior = GENESIS.to_string();
    for record in records {
        prior = chain_digest(&prior, &record.envelope);
    }
    let chain_head = records
        .last()
        .map(|record| record.record_digest.clone())
        .unwrap_or_else(|| GENESIS.to_string());
    let max_epoch = records
        .iter()
        .map(|record| record.envelope.epoch)
        .max()
        .unwrap_or(0);
    let consistent = chain_head == prior;
    object(vec![
        ("record_count", Digestable::Integer(records.len() as i64)),
        ("max_epoch", Digestable::Integer(max_epoch)),
        ("chain_head", Digestable::String(chain_head)),
        ("replayed_head", Digestable::String(prior)),
        ("consistent", Digestable::Bool(consistent)),
    ])
}

/// The whole projection, as a pure function of the record slice.
pub fn build_views(records: &[JournalRecord]) -> Digestable {
    object(vec![
        ("authority", authority_view(records)),
        ("effects", effects_view(records)),
        ("events", events_view(records)),
        ("heads", heads_view(records)),
    ])
}

/// A digest per view, so drift can be named rather than merely detected.
pub fn view_digests(views: &Digestable) -> BTreeMap<String, String> {
    let Digestable::Object(map) = views else {
        return BTreeMap::new();
    };
    map.iter()
        .map(|(name, view)| (name.clone(), digest_bytes(&canonical_json_bytes(view))))
        .collect()
}

/// One digest over the whole projection.
///
/// Distinct from [`crate::projection_digest`], deliberately. That one digests
/// the record index and answers "do two language peers see the same history".
/// This one digests the derived views and answers "does this deployment mode
/// tell the same truth". Folding them together would make a change to either
/// question silently alter the answer to the other.
pub fn views_digest(views: &Digestable) -> String {
    digest_bytes(&canonical_json_bytes(views))
}

#[cfg(test)]
mod tests {
    use super::*;
    use b1_protocol::envelope::{EffectBinding, Envelope, EpistemicStatus, Origin, PersistenceClass};

    fn record(seq: i64, envelope: Envelope, prior: &str) -> JournalRecord {
        let digest = chain_digest(prior, &envelope);
        JournalRecord {
            seq,
            envelope,
            prior_record_digest: prior.to_string(),
            record_digest: digest,
        }
    }

    fn plain(event_id: &str, epoch: i64) -> Envelope {
        Envelope::new(
            event_id,
            Origin::M,
            "b1-local",
            "rust-peer",
            "attempt-1",
            epoch,
            EpistemicStatus::WorkingAssumption,
            Digestable::String("payload".into()),
            vec![],
            vec![],
            None,
        )
        .unwrap()
    }

    fn with_effect(event_id: &str, identity: &str, status: EpistemicStatus) -> Envelope {
        Envelope::new(
            event_id,
            Origin::P,
            "b1-local",
            "rust-peer",
            "attempt-1",
            1,
            status,
            Digestable::String("payload".into()),
            vec![],
            vec![],
            Some(EffectBinding {
                effect_identity: identity.to_string(),
                authority_envelope_digest: "a".repeat(64),
                persistence_class: PersistenceClass::Reversible,
            }),
        )
        .unwrap()
    }

    fn chain(envelopes: Vec<Envelope>) -> Vec<JournalRecord> {
        let mut prior = GENESIS.to_string();
        let mut records = Vec::new();
        for (index, envelope) in envelopes.into_iter().enumerate() {
            let built = record(index as i64 + 1, envelope, &prior);
            prior = built.record_digest.clone();
            records.push(built);
        }
        records
    }

    #[test]
    fn building_twice_gives_identical_digests() {
        let records = chain(vec![plain("a", 1), plain("b", 2)]);
        assert_eq!(
            views_digest(&build_views(&records)),
            views_digest(&build_views(&records))
        );
    }

    #[test]
    fn every_declared_view_is_built() {
        let records = chain(vec![plain("a", 1)]);
        let digests = view_digests(&build_views(&records));
        let names: Vec<&str> = digests.keys().map(String::as_str).collect();
        assert_eq!(names, VIEW_NAMES.to_vec());
    }

    #[test]
    fn an_effect_reports_its_latest_status_not_its_first() {
        let records = chain(vec![
            with_effect("e1", "fs:a", EpistemicStatus::InDoubt),
            with_effect("e2", "fs:a", EpistemicStatus::Verified),
        ]);
        let Digestable::Array(rows) = effects_view(&records) else {
            panic!("effects view is not an array");
        };
        assert_eq!(rows.len(), 1);
        let Digestable::Object(row) = &rows[0] else {
            panic!("effect row is not an object");
        };
        assert_eq!(
            row["epistemic_status"],
            Digestable::String("VERIFIED".into())
        );
        assert_eq!(row["record_count"], Digestable::Integer(2));
        assert_eq!(row["first_seq"], Digestable::Integer(1));
        assert_eq!(row["last_seq"], Digestable::Integer(2));
    }

    #[test]
    fn an_empty_history_projects_to_an_empty_projection() {
        let Digestable::Object(heads) = heads_view(&[]) else {
            panic!("heads view is not an object");
        };
        assert_eq!(heads["record_count"], Digestable::Integer(0));
        assert_eq!(heads["chain_head"], Digestable::String(GENESIS.into()));
        assert_eq!(heads["consistent"], Digestable::Bool(true));
    }

    #[test]
    fn a_forged_head_is_reported_rather_than_laundered() {
        let mut records = chain(vec![plain("a", 1), plain("b", 2)]);
        records.last_mut().unwrap().record_digest = "f".repeat(64);
        let Digestable::Object(heads) = heads_view(&records) else {
            panic!("heads view is not an object");
        };
        assert_eq!(heads["consistent"], Digestable::Bool(false));
        assert_ne!(heads["chain_head"], heads["replayed_head"]);
    }
}
