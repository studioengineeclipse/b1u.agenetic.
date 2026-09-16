//! Emit the Rust peer's canonical bytes for every committed vector, as JSON.
//!
//! Consumed by `tools/verify_cross_language_digest.py`, which compares these
//! against what the Python peer produces in its own process. The comparison has
//! to cross a process boundary: two implementations linked into one runtime can
//! share a bug, two processes exchanging bytes cannot share one silently.
//!
//! Bytes are emitted as hex rather than as a string so that the transport
//! cannot normalise, re-encode or line-ending-translate them on the way out.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use b1_protocol::canonical::{digest_bytes, Digestable};
use b1_protocol::envelope::Envelope;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let vectors = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../conformance/vectors")
        .canonicalize()?;

    let mut names: Vec<String> = Vec::new();
    for entry in fs::read_dir(&vectors)? {
        let path = entry?.path();
        if path.extension().and_then(|e| e.to_str()) == Some("canonical") {
            if let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
                names.push(stem.to_string());
            }
        }
    }
    names.sort();

    let mut report: BTreeMap<String, serde_json::Value> = BTreeMap::new();
    for name in names {
        let source = fs::read_to_string(vectors.join(format!("{name}.json")))?;
        let parsed: serde_json::Value = serde_json::from_str(&source)?;
        let value = Digestable::from_json(&parsed)
            .map_err(|e| format!("{name}: {e}"))?;

        // Go through the envelope layer rather than canonicalising the parsed
        // JSON directly. Emitting the raw parse would prove only that two JSON
        // writers agree; going through Envelope proves the peers agree about
        // the envelope contract too, which is the part that carries meaning.
        let envelope = Envelope::from_canonical(&value).map_err(|e| format!("{name}: {e}"))?;
        let bytes = envelope.canonical_bytes();

        report.insert(
            name,
            serde_json::json!({
                "canonical_hex": bytes.iter().map(|b| format!("{b:02x}")).collect::<String>(),
                "digest": digest_bytes(&bytes),
                "byte_length": bytes.len(),
                "payload_digest": envelope.payload_digest,
            }),
        );
    }

    println!("{}", serde_json::to_string(&report)?);
    Ok(())
}
