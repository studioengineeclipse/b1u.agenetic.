//! Append a sequence of envelopes read from stdin and report the resulting head.
//!
//! Consumed by `tools/verify_cross_language_journal.py`, which feeds the same
//! sequence to the Python peer and compares. The comparison has to cross a
//! process boundary: two implementations linked into one runtime can share a
//! bug, two processes exchanging bytes cannot share one silently.
//!
//! Input is JSON Lines, one canonical envelope per line. Output is one JSON
//! object carrying the head both by real append and by pure replay; those two
//! must agree, which is what makes a journal reconstructible from its events
//! alone rather than only from its stored digests.

use std::io::Read;

use b1_protocol::canonical::Digestable;
use b1_protocol::envelope::Envelope;
use b1_state::RootJournal;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut input = String::new();
    std::io::stdin().read_to_string(&mut input)?;

    let mut envelopes: Vec<Envelope> = Vec::new();
    for (index, line) in input.lines().enumerate() {
        if line.trim().is_empty() {
            continue;
        }
        let parsed: serde_json::Value = serde_json::from_str(line)
            .map_err(|e| format!("line {}: {e}", index + 1))?;
        let value =
            Digestable::from_json(&parsed).map_err(|e| format!("line {}: {e}", index + 1))?;
        envelopes.push(
            Envelope::from_canonical(&value).map_err(|e| format!("line {}: {e}", index + 1))?,
        );
    }

    let dir = std::env::temp_dir().join(format!(
        "b1-replay-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_nanos()
    ));
    std::fs::create_dir_all(&dir)?;
    let result = (|| -> Result<serde_json::Value, Box<dyn std::error::Error>> {
        let journal = RootJournal::open(dir.join("root.db"))?;
        for envelope in &envelopes {
            journal.append(envelope)?;
        }
        let head = journal.head()?;
        let report = journal.verify()?;
        let records = journal.read_all()?;
        let views = b1_state::build_views(&records);
        Ok(serde_json::json!({
            "appended_head": head.digest,
            "record_count": head.record_count,
            "max_epoch": head.max_epoch,
            "replayed_head": journal.replay_head()?,
            "pure_replay_head": RootJournal::replay_digest(&envelopes),
            "verify_ok": report.ok,
            "findings": report.findings,
            "projection_digest": b1_state::projection_digest(&records),
            // The derived views, digested. A separate question from
            // `projection_digest` above: that one asks whether two peers see the
            // same history, this one whether they derive the same views from it.
            "views_digest": b1_state::views_digest(&views),
            "view_digests": b1_state::view_digests(&views),
        }))
    })();
    let _ = std::fs::remove_dir_all(&dir);

    println!("{}", serde_json::to_string(&result?)?);
    Ok(())
}
