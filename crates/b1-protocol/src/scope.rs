//! Target-scope matching, defined once for everything that bounds a target.
//!
//! This lives in `b1-protocol` rather than in either of its callers because both
//! the authority envelope and the capability policy bound what a target may be,
//! and two implementations of "inside the scope" that drifted apart would mean a
//! policy and an authorization disagreeing about the same string.
//!
//! The grammar is deliberately tiny:
//!
//! ```text
//! "docs/notes.md"   exactly that target
//! "docs/**"         anything at or beneath docs/
//! "**"              everything
//! ```
//!
//! Deliberately not a general glob. A `*` that crossed `/` would let `docs/*`
//! admit `docs/secrets/key`, which is the single mistake a scope field exists to
//! prevent. A pattern language too small to express a mistake is worth more here
//! than one expressive enough to make it.

/// Is `target` inside `scope`?
///
/// A prefix entry keeps its trailing slash when compared, so `docs/**` admits
/// `docs/a.md` and does *not* admit `docsecret/a.md`. Dropping the slash would
/// turn a directory scope into a string prefix, which is a wider thing.
pub fn scope_admits(scope: &[String], target: &str) -> bool {
    scope.iter().any(|entry| {
        if entry == "**" {
            true
        } else if let Some(prefix) = entry.strip_suffix("**") {
            prefix.ends_with('/') && target.starts_with(prefix)
        } else {
            entry == target
        }
    })
}

/// Why this scope is unusable, or `None`.
///
/// An empty scope is rejected rather than read as "everything" or "nothing".
/// Both readings are defensible, which is the problem: a field whose empty value
/// has two plausible meanings will eventually be read with the wrong one.
pub fn scope_is_valid(scope: &[String]) -> Option<String> {
    if scope.is_empty() {
        return Some(
            "scope must be non-empty; unlimited scope is written as [\"**\"] so that \
             granting it is a visible act rather than an omission"
                .to_string(),
        );
    }
    for entry in scope {
        if entry.is_empty() {
            return Some("scope entry is empty".to_string());
        }
        if entry.contains('*') && entry != "**" && !entry.ends_with("/**") {
            return Some(format!(
                "scope entry {entry:?} uses '*' somewhere this matcher does not interpret \
                 it. The only wildcards are a trailing '/**' and a bare '**'; anything \
                 else would read as a glob and silently match less, or more, than it appears to"
            ));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn a_directory_scope_is_not_a_string_prefix() {
        let scope = v(&["docs/**"]);
        assert!(scope_admits(&scope, "docs/a.md"));
        assert!(scope_admits(&scope, "docs/deep/b.md"));
        assert!(!scope_admits(&scope, "docsecret/a.md"));
        assert!(!scope_admits(&scope, "secrets/key"));
    }

    #[test]
    fn everything_has_to_be_written_out() {
        assert!(scope_admits(&v(&["**"]), "anything/at/all"));
        assert!(!scope_admits(&[], "anything"));
        assert!(scope_is_valid(&[]).is_some());
    }

    #[test]
    fn a_glob_that_would_cross_a_slash_is_refused() {
        assert!(scope_is_valid(&v(&["docs/*"])).is_some());
        assert!(scope_is_valid(&v(&["docs/**", "**", "a/b.md"])).is_none());
    }
}
