//! The B1 root journal, Rust peer.
//!
//! Design-derived-from: b1mu-omega13.9:src/b1mu/durable.py
//!
//! Mirrors `python/b1_state/journal.py`. The two are peers: for the same event
//! sequence they must arrive at the same head digest, and
//! `tools/verify_cross_language_journal.py` is what establishes that they do.
//!
//! Taken from upstream, because it already works: SQLite in WAL mode as the
//! durability boundary; an append-only table chained by
//! `SHA256(prior || canonical_bytes(record))` from a `GENESIS` anchor; and a
//! committed head digest *and record count* held apart from the chain. That last
//! part is the one worth restating: a prefix of a valid chain is itself a valid
//! chain, so the chain alone cannot see a truncated tail. The committed count
//! can.
//!
//! Deliberately different: upstream chains per `(run_id, attempt, seq)`. B1 has
//! one monotonic `seq` and one chain across every subsystem, because B1 needs
//! one authoritative history rather than many. See
//! `docs/decisions/ADR-0001-root-journal.md`.
//!
//! # Who may write
//!
//! Rust and Python are equal computational peers; both may analyse, derive,
//! plan and propose. Neither writes directly. Every append takes a
//! `BEGIN IMMEDIATE` lease, which SQLite grants to one connection at a time, so
//! concurrent proposals linearise instead of interleaving.

use std::fmt;
use std::path::Path;

use b1_protocol::canonical::{digest_bytes, Digestable};
use b1_protocol::envelope::Envelope;
use rusqlite::{params, Connection};

pub const GENESIS: &str = "GENESIS";
pub const SCHEMA_VERSION: &str = "1";

#[derive(Debug)]
pub enum JournalError {
    /// The journal refused an operation and changed nothing.
    Refused(String),
    /// A record arrived carrying a fence below the one already committed.
    StaleEpoch(String),
    /// Stored history does not match its own chain or head commitment.
    Tampered(String),
    Sqlite(rusqlite::Error),
}

impl fmt::Display for JournalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            JournalError::Refused(m) => write!(f, "{m}"),
            JournalError::StaleEpoch(m) => write!(f, "{m}"),
            JournalError::Tampered(m) => write!(f, "{m}"),
            JournalError::Sqlite(e) => write!(f, "sqlite: {e}"),
        }
    }
}

impl std::error::Error for JournalError {}

impl From<rusqlite::Error> for JournalError {
    fn from(err: rusqlite::Error) -> Self {
        JournalError::Sqlite(err)
    }
}

type Result<T> = std::result::Result<T, JournalError>;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JournalRecord {
    pub seq: i64,
    pub envelope: Envelope,
    pub prior_record_digest: String,
    pub record_digest: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Head {
    pub digest: String,
    pub record_count: i64,
    pub max_epoch: i64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerificationReport {
    pub ok: bool,
    pub record_count: i64,
    pub head_digest: String,
    pub findings: Vec<String>,
}

/// The chained digest of one record.
///
/// Binding the prior digest in is what makes the chain a chain: altering any
/// earlier record changes every later digest, so a single-row edit cannot be
/// made to look consistent without rewriting everything after it.
pub fn chain_digest(prior: &str, envelope: &Envelope) -> String {
    let mut data = prior.as_bytes().to_vec();
    data.extend_from_slice(&envelope.canonical_bytes());
    digest_bytes(&data)
}

const SCHEMA: &[&str] = &[
    "CREATE TABLE IF NOT EXISTS root_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS root_journal(
         seq INTEGER PRIMARY KEY AUTOINCREMENT,
         event_id TEXT NOT NULL UNIQUE,
         effect_identity TEXT,
         epoch INTEGER NOT NULL,
         envelope_json TEXT NOT NULL,
         prior_record_digest TEXT NOT NULL,
         record_digest TEXT NOT NULL UNIQUE)",
    "CREATE TABLE IF NOT EXISTS root_head(
         id INTEGER PRIMARY KEY CHECK(id = 1),
         head_digest TEXT NOT NULL,
         record_count INTEGER NOT NULL,
         max_epoch INTEGER NOT NULL)",
    "CREATE INDEX IF NOT EXISTS root_journal_effect ON root_journal(effect_identity)",
];

/// The single write lease, held for the duration of one atomic unit.
///
/// Returned by [`RootJournal::lease`] so that a component with its own tables
/// in this database — the commit gate is the one that has them — can make its
/// writes and the journal's appends commit or roll back together. Two leases
/// would be two linearization points, which is the split-brain the
/// single-writer design exists to prevent.
///
/// Not a `Drop` guard on purpose: rolling back is a decision, and a guard that
/// committed or rolled back implicitly would make the choice invisible at the
/// call site. [`Lease::finish`] is explicit and returns the error.
#[must_use = "a lease must be finished with commit or rollback"]
pub struct Lease<'a> {
    conn: &'a Connection,
}

impl Lease<'_> {
    /// Commit when `commit` is true, otherwise roll back.
    pub fn finish(self, commit: bool) -> Result<()> {
        if commit {
            self.conn.execute_batch("COMMIT")?;
        } else {
            // A failed rollback is not reported over the original error: the
            // caller is already handling a failure and the rollback is
            // best-effort cleanup.
            let _ = self.conn.execute_batch("ROLLBACK");
        }
        Ok(())
    }
}

pub struct RootJournal {
    conn: Connection,
}

impl fmt::Debug for RootJournal {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Deliberately opaque. Printing a journal's contents into a log or a
        // panic message would leak whatever the payloads hold.
        f.write_str("RootJournal { .. }")
    }
}

impl RootJournal {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let conn = Connection::open(path)?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        conn.pragma_update(None, "synchronous", "FULL")?;
        let journal = RootJournal { conn };
        journal.initialise()?;
        Ok(journal)
    }

    fn initialise(&self) -> Result<()> {
        self.conn.execute_batch("BEGIN IMMEDIATE")?;
        let outcome = (|| -> Result<()> {
            for statement in SCHEMA {
                self.conn.execute(statement, [])?;
            }
            let existing: Option<String> = self
                .conn
                .query_row(
                    "SELECT value FROM root_meta WHERE key='schema_version'",
                    [],
                    |row| row.get(0),
                )
                .ok();
            match existing {
                None => {
                    self.conn.execute(
                        "INSERT INTO root_meta(key,value) VALUES('schema_version',?1)",
                        params![SCHEMA_VERSION],
                    )?;
                }
                Some(found) if found != SCHEMA_VERSION => {
                    return Err(JournalError::Refused(format!(
                        "unsupported root journal schema {found:?}; this build understands \
                         {SCHEMA_VERSION:?} and will not guess at a migration"
                    )));
                }
                Some(_) => {}
            }
            let has_head: i64 = self.conn.query_row(
                "SELECT COUNT(*) FROM root_head WHERE id=1",
                [],
                |row| row.get(0),
            )?;
            if has_head == 0 {
                self.conn.execute(
                    "INSERT INTO root_head(id,head_digest,record_count,max_epoch) \
                     VALUES(1,?1,0,-1)",
                    params![GENESIS],
                )?;
            }
            Ok(())
        })();
        match outcome {
            Ok(()) => {
                self.conn.execute_batch("COMMIT")?;
                Ok(())
            }
            Err(err) => {
                let _ = self.conn.execute_batch("ROLLBACK");
                Err(err)
            }
        }
    }

    /// Acquire the single write lease. See [`Lease`].
    ///
    /// Inside a lease, use [`RootJournal::append_in_lease`] rather than
    /// [`RootJournal::append`], which would try to open a second transaction.
    pub fn lease(&self) -> Result<Lease<'_>> {
        self.conn.execute_batch("BEGIN IMMEDIATE")?;
        Ok(Lease { conn: &self.conn })
    }

    /// The underlying connection, shared deliberately.
    ///
    /// A component holding its own tables in this database shares the
    /// connection so its writes and the journal's commit or roll back
    /// together. Anything writing here must hold the lease.
    pub fn connection(&self) -> &Connection {
        &self.conn
    }

    pub fn head(&self) -> Result<Head> {
        let head = self.conn.query_row(
            "SELECT head_digest,record_count,max_epoch FROM root_head WHERE id=1",
            [],
            |row| {
                Ok(Head {
                    digest: row.get(0)?,
                    record_count: row.get(1)?,
                    max_epoch: row.get(2)?,
                })
            },
        )?;
        Ok(head)
    }

    /// Append one envelope, or refuse and change nothing.
    ///
    /// Refusals, and why each exists: an invalid envelope could not be replayed
    /// later; a duplicate `event_id` would give history two rows claiming one
    /// identity; an unknown causal parent would make a record's lineage
    /// unreconstructible; a stale `epoch` is a late attempt from a superseded
    /// fence; and a repeated `effect_identity` at or below the committed epoch
    /// is a duplicate effect, which is why `effect_identity` is a column rather
    /// than a field buried in the payload.
    pub fn append(&self, envelope: &Envelope) -> Result<JournalRecord> {
        let lease = self.lease()?;
        let outcome = self.append_in_lease(envelope);
        lease.finish(outcome.is_ok())?;
        outcome
    }

    /// Append while the caller already holds the lease.
    ///
    /// Same validation as [`RootJournal::append`]; the only difference is who
    /// owns the transaction. Split out so a caller combining journal appends
    /// with its own table writes gets one atomic unit and one linearization
    /// point, rather than being tempted to insert rows directly and skip these
    /// checks.
    pub fn append_in_lease(&self, envelope: &Envelope) -> Result<JournalRecord> {
        envelope.validate().map_err(|e| {
            JournalError::Refused(format!("refusing to append an invalid envelope: {e}"))
        })?;
        self.append_locked(envelope)
    }

    fn append_locked(&self, envelope: &Envelope) -> Result<JournalRecord> {
        let head = self.head()?;

        let exists: i64 = self.conn.query_row(
            "SELECT COUNT(*) FROM root_journal WHERE event_id=?1",
            params![envelope.event_id],
            |row| row.get(0),
        )?;
        if exists > 0 {
            return Err(JournalError::Refused(format!(
                "event_id {:?} is already in the journal; history cannot hold two \
                 records with one identity",
                envelope.event_id
            )));
        }

        for parent in &envelope.causal_parents {
            let found: i64 = self.conn.query_row(
                "SELECT COUNT(*) FROM root_journal WHERE event_id=?1",
                params![parent],
                |row| row.get(0),
            )?;
            if found == 0 {
                return Err(JournalError::Refused(format!(
                    "causal parent {parent:?} is not in the journal; appending would \
                     create a record whose lineage cannot be replayed"
                )));
            }
        }

        if envelope.epoch < head.max_epoch {
            return Err(JournalError::StaleEpoch(format!(
                "event {:?} carries epoch {}, below the committed fence {}; a superseded \
                 attempt must not land after the fence moved on",
                envelope.event_id, envelope.epoch, head.max_epoch
            )));
        }

        if let Some(effect) = &envelope.effect {
            let clash: Option<(String, i64)> = self
                .conn
                .query_row(
                    "SELECT event_id,epoch FROM root_journal WHERE effect_identity=?1 \
                     ORDER BY seq DESC LIMIT 1",
                    params![effect.effect_identity],
                    |row| Ok((row.get(0)?, row.get(1)?)),
                )
                .ok();
            if let Some((event_id, epoch)) = clash {
                if epoch >= envelope.epoch {
                    return Err(JournalError::Refused(format!(
                        "effect {:?} was already recorded by {event_id:?} at epoch {epoch}; \
                         a retry must carry a newer fence, not repeat the same one",
                        effect.effect_identity
                    )));
                }
            }
        }

        let record_digest = chain_digest(&head.digest, envelope);
        let envelope_json = String::from_utf8(envelope.canonical_bytes())
            .expect("canonical bytes are valid UTF-8 by construction");
        let effect_identity = envelope.effect.as_ref().map(|e| e.effect_identity.clone());

        self.conn.execute(
            "INSERT INTO root_journal\
             (event_id,effect_identity,epoch,envelope_json,prior_record_digest,record_digest)\
             VALUES(?1,?2,?3,?4,?5,?6)",
            params![
                envelope.event_id,
                effect_identity,
                envelope.epoch,
                envelope_json,
                head.digest,
                record_digest
            ],
        )?;
        let seq = self.conn.last_insert_rowid();
        self.conn.execute(
            "UPDATE root_head SET head_digest=?1,record_count=?2,max_epoch=?3 WHERE id=1",
            params![
                record_digest,
                head.record_count + 1,
                head.max_epoch.max(envelope.epoch)
            ],
        )?;

        Ok(JournalRecord {
            seq,
            envelope: envelope.clone(),
            prior_record_digest: head.digest,
            record_digest,
        })
    }

    pub fn read_all(&self) -> Result<Vec<JournalRecord>> {
        let mut statement = self.conn.prepare(
            "SELECT seq,envelope_json,prior_record_digest,record_digest \
             FROM root_journal ORDER BY seq",
        )?;
        let rows: Vec<(i64, String, String, String)> = statement
            .query_map([], |row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
            })?
            .collect::<std::result::Result<_, _>>()?;

        let mut out = Vec::with_capacity(rows.len());
        for (seq, envelope_json, prior, digest) in rows {
            let parsed: serde_json::Value = serde_json::from_str(&envelope_json)
                .map_err(|e| JournalError::Tampered(format!("seq {seq}: unparseable: {e}")))?;
            let value = Digestable::from_json(&parsed)
                .map_err(|e| JournalError::Tampered(format!("seq {seq}: {e}")))?;
            let envelope = Envelope::from_canonical(&value)
                .map_err(|e| JournalError::Tampered(format!("seq {seq}: {e}")))?;
            out.push(JournalRecord {
                seq,
                envelope,
                prior_record_digest: prior,
                record_digest: digest,
            });
        }
        Ok(out)
    }

    /// Re-derive the whole chain and compare it against what is committed.
    ///
    /// Every finding is collected rather than returning on the first: when
    /// history is damaged, the useful question is how much of it is damaged.
    pub fn verify(&self) -> Result<VerificationReport> {
        let mut findings: Vec<String> = Vec::new();
        let head = self.head()?;

        let mut statement = self.conn.prepare(
            "SELECT seq,envelope_json,prior_record_digest,record_digest \
             FROM root_journal ORDER BY seq",
        )?;
        let rows: Vec<(i64, String, String, String)> = statement
            .query_map([], |row| {
                Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?))
            })?
            .collect::<std::result::Result<_, _>>()?;

        let mut prior = GENESIS.to_string();
        let mut expected_seq: i64 = 1;
        for (seq, envelope_json, stored_prior, stored_digest) in &rows {
            if *seq != expected_seq {
                findings.push(format!(
                    "seq {seq}: sequence is not contiguous, expected {expected_seq}; \
                     a record has been removed from the middle"
                ));
                expected_seq = *seq;
            }
            expected_seq += 1;

            if stored_prior != &prior {
                findings.push(format!(
                    "seq {seq}: prior digest is {stored_prior}, chain expects {prior}"
                ));
            }

            // Re-canonicalise rather than hash the stored text: that is what
            // catches a row whose JSON was edited into a different but still
            // parseable shape.
            let envelope = serde_json::from_str::<serde_json::Value>(envelope_json)
                .ok()
                .and_then(|parsed| Digestable::from_json(&parsed).ok())
                .and_then(|value| Envelope::from_canonical(&value).ok());

            match envelope {
                None => findings.push(format!("seq {seq}: stored envelope no longer validates")),
                Some(envelope) => {
                    let recomputed = chain_digest(stored_prior, &envelope);
                    if &recomputed != stored_digest {
                        findings.push(format!(
                            "seq {seq}: record digest is {stored_digest}, recomputes to \
                             {recomputed}; this record's contents were altered"
                        ));
                    }
                }
            }
            prior = stored_digest.clone();
        }

        let count = rows.len() as i64;
        if count != head.record_count {
            findings.push(format!(
                "journal holds {count} records but the committed head claims {}; {}",
                head.record_count,
                if count < head.record_count {
                    "records were removed from the end"
                } else {
                    "records were added without updating the head"
                }
            ));
        }
        if prior != head.digest {
            findings.push(format!(
                "recomputed head is {prior}, committed head is {}",
                head.digest
            ));
        }

        Ok(VerificationReport {
            ok: findings.is_empty(),
            record_count: count,
            head_digest: prior,
            findings,
        })
    }

    /// Recompute the head from the stored envelopes alone.
    pub fn replay_head(&self) -> Result<String> {
        let mut prior = GENESIS.to_string();
        for record in self.read_all()? {
            prior = chain_digest(&prior, &record.envelope);
        }
        Ok(prior)
    }

    /// The head a journal would have after appending exactly `envelopes`.
    ///
    /// Pure, needing no database, so a peer can predict a head before writing
    /// anything and two peers can compare heads without sharing storage.
    pub fn replay_digest(envelopes: &[Envelope]) -> String {
        let mut prior = GENESIS.to_string();
        for envelope in envelopes {
            prior = chain_digest(&prior, envelope);
        }
        prior
    }
}

/// A digest over a derived view of the journal.
///
/// Projections are rebuildable and therefore disposable: if a projection and a
/// replay disagree, the projection loses. This is how that comparison is made.
pub fn projection_digest(records: &[JournalRecord]) -> String {
    use std::collections::BTreeMap;

    let summary = Digestable::Array(
        records
            .iter()
            .map(|record| {
                let mut entry: BTreeMap<String, Digestable> = BTreeMap::new();
                entry.insert("seq".into(), Digestable::Integer(record.seq));
                entry.insert(
                    "event_id".into(),
                    Digestable::String(record.envelope.event_id.clone()),
                );
                entry.insert(
                    "origin".into(),
                    Digestable::String(record.envelope.origin.as_str().into()),
                );
                entry.insert("epoch".into(), Digestable::Integer(record.envelope.epoch));
                entry.insert(
                    "record_digest".into(),
                    Digestable::String(record.record_digest.clone()),
                );
                Digestable::Object(entry)
            })
            .collect(),
    );
    digest_bytes(&b1_protocol::canonical_json_bytes(&summary))
}
