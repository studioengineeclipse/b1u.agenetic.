//! Canonical serialization for B1 Local.
//!
//! Design-derived-from: b1mu-omega13.9:src/b1mu/serialization.py
//!
//! This must produce bytes identical to `python/b1_protocol/canonical.py` for
//! every value both accept. `tools/verify_cross_language_digest.py` is what
//! actually holds that claim up; the doc comment is only a promise.
//!
//! The serializer is written by hand rather than delegated to
//! `serde_json::to_string`. That is deliberate. Canonical bytes are the thing
//! two independent implementations have to agree on forever, so the escaping
//! rules and key ordering are stated here in B1's own code, where a change to
//! them shows up in a diff, rather than inherited from a dependency's internal
//! choices.
//!
//! # Why [`Digestable`] has no float and no null variant
//!
//! Python renders a float through `repr()`; Rust's `serde_json` renders it
//! through Ryu. Both produce a shortest round-tripping form, but not always the
//! same one. One disagreement anywhere in a payload yields different canonical
//! bytes, a different digest, and a root journal whose two peers silently
//! disagree about history. Making the type unable to hold a float turns that
//! runtime hazard into a compile-time impossibility.
//!
//! Null is excluded because after canonicalisation an absent key and an explicit
//! null are indistinguishable in several of the fourteen target languages, which
//! would give one envelope two canonical forms.

use std::collections::BTreeMap;
use std::fmt;

use sha2::{Digest, Sha256};

/// A value that can be canonicalised identically by every B1 language peer.
///
/// `Object` uses a [`BTreeMap`], whose `String` ordering is UTF-8 byte order.
/// UTF-8 is constructed so that byte-wise ordering equals Unicode code-point
/// ordering, which is what Python's `sort_keys=True` uses, so the two agree.
/// Vector `envelope-002-unicode-keys` exists to prove that rather than assume it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Digestable {
    String(String),
    Integer(i64),
    Bool(bool),
    Array(Vec<Digestable>),
    Object(BTreeMap<String, Digestable>),
}

/// A value cannot be canonicalised without risking cross-language divergence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CanonicalizationError {
    pub path: String,
    pub reason: String,
}

impl fmt::Display for CanonicalizationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.path, self.reason)
    }
}

impl std::error::Error for CanonicalizationError {}

impl Digestable {
    /// Convert a parsed JSON value, rejecting anything not digestable.
    ///
    /// This is the boundary where untrusted JSON becomes a value B1 is willing
    /// to hash. Rejections carry the path so a caller can fix the offending
    /// field rather than the whole payload.
    pub fn from_json(value: &serde_json::Value) -> Result<Self, CanonicalizationError> {
        Self::from_json_at(value, "$")
    }

    fn from_json_at(
        value: &serde_json::Value,
        path: &str,
    ) -> Result<Self, CanonicalizationError> {
        match value {
            serde_json::Value::String(s) => Ok(Digestable::String(s.clone())),
            serde_json::Value::Bool(b) => Ok(Digestable::Bool(*b)),
            serde_json::Value::Number(n) => match n.as_i64() {
                Some(i) => Ok(Digestable::Integer(i)),
                None => Err(CanonicalizationError {
                    path: path.to_string(),
                    reason: format!(
                        "{n} is not a 64-bit integer. Float values are not digestable: \
                         Python renders floats via repr() and Rust serde_json via Ryu, \
                         and these can disagree, which would break cross-language digest \
                         equality. Use an integer, or a decimal string."
                    ),
                }),
            },
            serde_json::Value::Null => Err(CanonicalizationError {
                path: path.to_string(),
                reason: "null is not digestable. After canonicalisation an absent key and \
                         an explicit null are indistinguishable in several target \
                         languages. Omit the key."
                    .to_string(),
            }),
            serde_json::Value::Array(items) => {
                let mut out = Vec::with_capacity(items.len());
                for (index, item) in items.iter().enumerate() {
                    out.push(Self::from_json_at(item, &format!("{path}[{index}]"))?);
                }
                Ok(Digestable::Array(out))
            }
            serde_json::Value::Object(map) => {
                let mut out = BTreeMap::new();
                for (key, item) in map {
                    if key.is_empty() {
                        return Err(CanonicalizationError {
                            path: path.to_string(),
                            reason: "object keys must be non-empty".to_string(),
                        });
                    }
                    out.insert(
                        key.clone(),
                        Self::from_json_at(item, &format!("{path}.{key}"))?,
                    );
                }
                Ok(Digestable::Object(out))
            }
        }
    }

    fn write(&self, out: &mut String) {
        match self {
            Digestable::String(s) => write_json_string(s, out),
            Digestable::Integer(i) => out.push_str(&i.to_string()),
            Digestable::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
            Digestable::Array(items) => {
                out.push('[');
                for (index, item) in items.iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    item.write(out);
                }
                out.push(']');
            }
            Digestable::Object(map) => {
                out.push('{');
                for (index, (key, item)) in map.iter().enumerate() {
                    if index > 0 {
                        out.push(',');
                    }
                    write_json_string(key, out);
                    out.push(':');
                    item.write(out);
                }
                out.push('}');
            }
        }
    }
}

/// Escape exactly as Python's `json.dumps(..., ensure_ascii=False)` does.
///
/// That means: `"` and `\` escaped; the five short control escapes; every other
/// control character below 0x20 as `\uXXXX`; and everything else, including all
/// non-ASCII, emitted raw as UTF-8. Note what is *not* escaped: forward slash,
/// and DEL (0x7F). Python leaves both alone, so B1 leaves both alone.
fn write_json_string(value: &str, out: &mut String) {
    out.push('"');
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// Return the one canonical byte encoding of `value`.
///
/// Sorted keys, non-ASCII raw, no whitespace, one trailing newline, UTF-8.
pub fn canonical_json_bytes(value: &Digestable) -> Vec<u8> {
    let mut text = String::new();
    value.write(&mut text);
    text.push('\n');
    text.into_bytes()
}

/// SHA-256 of raw bytes, lowercase hex.
pub fn digest_bytes(data: &[u8]) -> String {
    let mut hasher = Sha256::new();
    hasher.update(data);
    format!("{:x}", hasher.finalize())
}

/// SHA-256 over the canonical bytes of `value`, lowercase hex.
pub fn digest_value(value: &Digestable) -> String {
    digest_bytes(&canonical_json_bytes(value))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parse(text: &str) -> Result<Digestable, CanonicalizationError> {
        Digestable::from_json(&serde_json::from_str(text).expect("valid json"))
    }

    #[test]
    fn keys_are_sorted_by_code_point_not_insertion_order() {
        let value = parse(r#"{"z":1,"a":2,"m":3}"#).unwrap();
        assert_eq!(
            String::from_utf8(canonical_json_bytes(&value)).unwrap(),
            "{\"a\":2,\"m\":3,\"z\":1}\n"
        );
    }

    #[test]
    fn non_ascii_is_emitted_raw_and_ordered_by_code_point() {
        // 'a' (0x61) < 'z' (0x7A) < 'é' (0xE9) < 'Ω' (0x3A9) < '中' (0x4E2D).
        let value = parse("{\"\u{4e2d}\":1,\"\u{e9}\":2,\"z\":3,\"a\":4,\"\u{3a9}\":5}").unwrap();
        assert_eq!(
            String::from_utf8(canonical_json_bytes(&value)).unwrap(),
            "{\"a\":4,\"z\":3,\"\u{e9}\":2,\"\u{3a9}\":5,\"\u{4e2d}\":1}\n"
        );
    }

    #[test]
    fn floats_are_refused_with_the_reason_named() {
        let err = parse(r#"{"ratio":0.1}"#).unwrap_err();
        assert_eq!(err.path, "$.ratio");
        assert!(err.reason.contains("Ryu"), "reason should name the cause: {}", err.reason);
    }

    #[test]
    fn nested_floats_are_refused_with_their_path() {
        let err = parse(r#"{"outer":[1,2,{"inner":3.5}]}"#).unwrap_err();
        assert_eq!(err.path, "$.outer[2].inner");
    }

    #[test]
    fn null_is_refused() {
        let err = parse(r#"{"maybe":null}"#).unwrap_err();
        assert_eq!(err.path, "$.maybe");
        assert!(err.reason.contains("Omit the key"));
    }

    #[test]
    fn empty_keys_are_refused() {
        assert!(parse(r#"{"":1}"#).is_err());
    }

    #[test]
    fn control_characters_use_python_escapes() {
        let value = parse("{\"k\":\"a\\nb\\u0001c\\td\"}").unwrap();
        assert_eq!(
            String::from_utf8(canonical_json_bytes(&value)).unwrap(),
            "{\"k\":\"a\\nb\\u0001c\\td\"}\n"
        );
    }

    #[test]
    fn forward_slash_and_del_are_not_escaped() {
        let value = parse("{\"k\":\"a/b\\u007fc\"}").unwrap();
        assert_eq!(
            String::from_utf8(canonical_json_bytes(&value)).unwrap(),
            "{\"k\":\"a/b\u{7f}c\"}\n"
        );
    }

    #[test]
    fn empty_containers_round_trip() {
        let value = parse(r#"{"a":[],"b":{}}"#).unwrap();
        assert_eq!(
            String::from_utf8(canonical_json_bytes(&value)).unwrap(),
            "{\"a\":[],\"b\":{}}\n"
        );
    }
}
