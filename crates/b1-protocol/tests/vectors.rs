//! The Rust peer is checked against the committed conformance vectors.
//!
//! The vectors were produced by the Python peer. Nothing here regenerates them:
//! if Rust disagrees with a committed byte, that is a finding, not a reason to
//! rewrite the vector. This is the test that actually holds up the claim made in
//! `canonical.rs` that the two implementations agree.

use std::fs;
use std::path::PathBuf;

use b1_protocol::canonical::{digest_bytes, Digestable};
use b1_protocol::envelope::Envelope;

fn vectors_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../conformance/vectors")
        .canonicalize()
        .expect("conformance/vectors must exist")
}

fn load(name: &str) -> (Digestable, Vec<u8>, serde_json::Value) {
    let dir = vectors_dir();
    let source = fs::read_to_string(dir.join(format!("{name}.json"))).expect("vector .json");
    let parsed: serde_json::Value = serde_json::from_str(&source).expect("vector parses");
    let value = Digestable::from_json(&parsed).expect("vector is digestable");
    let canonical = fs::read(dir.join(format!("{name}.canonical"))).expect("vector .canonical");
    let expected: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(dir.join(format!("{name}.expected.json"))).expect("vector .expected"),
    )
    .expect("expected parses");
    (value, canonical, expected)
}

/// Re-canonicalising a vector must reproduce the committed bytes exactly, and
/// those bytes must hash to the committed digest.
fn check(name: &str) {
    let (value, committed, expected) = load(name);

    let produced = b1_protocol::canonical_json_bytes(&value);
    assert_eq!(
        String::from_utf8_lossy(&produced),
        String::from_utf8_lossy(&committed),
        "{name}: Rust canonical bytes differ from the committed Python-produced bytes"
    );

    let declared = expected["canonical_sha256"].as_str().expect("declared digest");
    assert_eq!(digest_bytes(&produced), declared, "{name}: digest mismatch");

    let declared_len = expected["canonical_byte_length"].as_u64().expect("declared length");
    assert_eq!(produced.len() as u64, declared_len, "{name}: byte length mismatch");

    // The envelope layer must agree with the raw canonical layer: parsing the
    // vector as an envelope and re-emitting it has to land on the same bytes.
    let envelope = Envelope::from_canonical(&value).expect("vector is a valid envelope");
    assert_eq!(
        envelope.canonical_bytes(),
        committed,
        "{name}: envelope round-trip does not reproduce the committed bytes"
    );
    assert_eq!(envelope.digest(), declared, "{name}: envelope digest mismatch");

    let declared_payload = expected["payload_digest"].as_str().expect("payload digest");
    assert_eq!(
        envelope.payload_digest, declared_payload,
        "{name}: payload digest mismatch"
    );
}

#[test]
fn vector_001_baseline() {
    check("envelope-001");
}

/// The vector that proves, rather than assumes, that Python's code-point key
/// sort and Rust's `BTreeMap` UTF-8 byte-order key sort agree, and that
/// non-ASCII survives unescaped through both.
#[test]
fn vector_002_unicode_keys() {
    check("envelope-002-unicode-keys");
}

#[test]
fn vector_003_effect() {
    check("envelope-003-effect");
}

/// Every value the Python peer refuses, the Rust peer must also refuse.
/// A language peer that accepts any of these is non-conformant, because the
/// canonical bytes it would produce are not reproducible elsewhere.
#[test]
fn vector_004_rejections_are_mutual() {
    let expected: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(vectors_dir().join("envelope-004-rejected.expected.json"))
            .expect("rejection vector"),
    )
    .expect("parses");

    let cases = expected["must_reject"].as_array().expect("must_reject list");
    assert!(!cases.is_empty(), "the rejection vector must carry cases");

    // Each case is checked against the same JSON the Python builder used. These
    // are spelled out here rather than read from the vector, because a rejected
    // value has no canonical form to store.
    let samples: &[(&str, &str)] = &[
        ("float", r#"{"ratio":0.1}"#),
        ("nested_float", r#"{"outer":[1,2,{"inner":3.5}]}"#),
        ("null", r#"{"maybe":null}"#),
        ("empty_key", r#"{"":"empty"}"#),
    ];
    for (label, text) in samples {
        let parsed: serde_json::Value = serde_json::from_str(text).expect("sample parses as JSON");
        assert!(
            Digestable::from_json(&parsed).is_err(),
            "case {label}: Rust accepted a value Python refuses"
        );
    }

    // The non_string_key case has no JSON representation at all: JSON object
    // keys are always strings, so Python's dict-with-int-key cannot be written
    // here. Rust's type system rules it out for the same reason, which is why
    // it is asserted as unreachable rather than tested.
    assert!(
        cases.iter().any(|c| c["case"] == "non_string_key"),
        "the rejection vector should still record non_string_key for the Python peer"
    );
}
