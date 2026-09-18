//! The standing capability policy — the Rust peer of `python/b1_policy`.
//!
//! This layer sits *above* per-effect authority and answers a different
//! question:
//!
//! ```text
//! policy      may this kind of action ever be authorized here?
//! authority   is this specific action, on this target, authorized now?
//! ```
//!
//! Both must say yes. Neither substitutes for the other, and the ordering
//! matters: the policy is consulted **before an authority can be granted**, so a
//! capability the policy denies never becomes an envelope a user could be asked
//! to approve. Asking someone to approve something that would be refused anyway
//! trains them to approve things.
//!
//! Four rules, identical in both peers:
//!
//! 1. **Default deny.** An action not named in `allow` is denied.
//! 2. **Deny wins**, and is checked first.
//! 3. **A persistence ceiling** per capability: `REVERSIBLE < COMPENSATABLE <
//!    IRREVERSIBLE`.
//! 4. **UNKNOWN is never admitted.** It is not a rank on that scale. An effect
//!    whose recovery class nobody could determine is not one anybody can permit
//!    in advance, and sorting it anywhere in the ordering would silently decide
//!    whether the unclassifiable is safe.
//!
//! The two peers must produce the same policy digest for the same policy, since
//! an authority envelope binds it; `tools/verify_cross_language_gate.py` is what
//! establishes that they do.

use std::collections::BTreeMap;

use b1_protocol::canonical::{canonical_json_bytes, digest_value, Digestable};
use b1_protocol::scope::{scope_admits, scope_is_valid};

pub const POLICY_SCHEMA_VERSION: &str = "b1-capability-policy-1";

/// Ordered by how much of the damage is recoverable. `UNKNOWN` is deliberately
/// absent: see rule 4. Returning `None` for it is what keeps "unclassifiable"
/// from quietly acquiring a position on the scale.
pub fn persistence_rank(class: &str) -> Option<u8> {
    match class {
        "REVERSIBLE" => Some(0),
        "COMPENSATABLE" => Some(1),
        "IRREVERSIBLE" => Some(2),
        _ => None,
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PolicyError(pub String);

impl std::fmt::Display for PolicyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::error::Error for PolicyError {}

/// One action, bounded by scope and by how much damage it may risk.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Capability {
    pub action: String,
    pub scope: Vec<String>,
    pub max_persistence_class: String,
    pub reason: String,
}

impl Capability {
    pub fn new(
        action: impl Into<String>,
        scope: Vec<String>,
        max_persistence_class: impl Into<String>,
    ) -> Result<Self, PolicyError> {
        Self::with_reason(action, scope, max_persistence_class, "")
    }

    pub fn with_reason(
        action: impl Into<String>,
        scope: Vec<String>,
        max_persistence_class: impl Into<String>,
        reason: impl Into<String>,
    ) -> Result<Self, PolicyError> {
        let capability = Capability {
            action: action.into(),
            scope,
            max_persistence_class: max_persistence_class.into(),
            reason: reason.into(),
        };
        if capability.action.is_empty()
            || !capability
                .action
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | ':' | '-'))
        {
            return Err(PolicyError(format!(
                "action {:?} is not a usable action name; expected something like \
                 'fs.write' or 'net.send'",
                capability.action
            )));
        }
        if let Some(problem) = scope_is_valid(&capability.scope) {
            return Err(PolicyError(format!("{}: {problem}", capability.action)));
        }
        if persistence_rank(&capability.max_persistence_class).is_none() {
            return Err(PolicyError(format!(
                "{}: max_persistence_class must be REVERSIBLE, COMPENSATABLE or \
                 IRREVERSIBLE, got {:?}. UNKNOWN is not accepted as a ceiling: an effect \
                 whose recovery class nobody could determine is not one anybody can permit \
                 in advance",
                capability.action, capability.max_persistence_class
            )));
        }
        Ok(capability)
    }

    /// Does this capability cover this action, target and recovery class?
    pub fn admits(&self, action: &str, target: &str, persistence_class: &str) -> bool {
        if action != self.action || !scope_admits(&self.scope, target) {
            return false;
        }
        match (
            persistence_rank(persistence_class),
            persistence_rank(&self.max_persistence_class),
        ) {
            // Covers UNKNOWN and anything unrecognised. Both mean the same
            // thing here: nothing on this scale describes it, so no ceiling
            // admits it.
            (Some(rank), Some(ceiling)) => rank <= ceiling,
            _ => false,
        }
    }

    /// Ignores the ceiling. Used by `deny`, which is not a ceiling question: it
    /// would be strange for denying an irreversible write to leave the
    /// reversible one permitted by the same rule.
    pub fn matches_action_and_target(&self, action: &str, target: &str) -> bool {
        action == self.action && scope_admits(&self.scope, target)
    }

    /// Rebuild from the canonical mapping, refusing unknown fields.
    ///
    /// Unknown fields are an error rather than something to skip: a capability
    /// carrying a constraint this build does not understand must not be read as
    /// the weaker capability that remains once the constraint is dropped.
    pub fn from_canonical(value: &Digestable) -> Result<Self, PolicyError> {
        let Digestable::Object(map) = value else {
            return Err(PolicyError("capability must be an object".into()));
        };
        const KNOWN: &[&str] = &["action", "scope", "max_persistence_class", "reason"];
        if let Some(unknown) = map.keys().find(|k| !KNOWN.contains(&k.as_str())) {
            return Err(PolicyError(format!("unknown capability field {unknown:?}")));
        }
        let action = match map.get("action") {
            Some(Digestable::String(s)) => s.clone(),
            _ => return Err(PolicyError("capability.action must be a string".into())),
        };
        let scope = match map.get("scope") {
            Some(Digestable::Array(items)) => items
                .iter()
                .map(|item| match item {
                    Digestable::String(s) => Ok(s.clone()),
                    _ => Err(PolicyError("capability.scope entries must be strings".into())),
                })
                .collect::<Result<Vec<String>, PolicyError>>()?,
            _ => return Err(PolicyError("capability.scope must be an array".into())),
        };
        let ceiling = match map.get("max_persistence_class") {
            Some(Digestable::String(s)) => s.clone(),
            _ => {
                return Err(PolicyError(
                    "capability.max_persistence_class must be a string".into(),
                ))
            }
        };
        let reason = match map.get("reason") {
            Some(Digestable::String(s)) => s.clone(),
            None => String::new(),
            _ => return Err(PolicyError("capability.reason must be a string".into())),
        };
        Capability::with_reason(action, scope, ceiling, reason)
    }

    fn to_canonical(&self) -> Digestable {
        let mut map: BTreeMap<String, Digestable> = BTreeMap::new();
        map.insert("action".into(), Digestable::String(self.action.clone()));
        map.insert(
            "scope".into(),
            Digestable::Array(
                self.scope
                    .iter()
                    .cloned()
                    .map(Digestable::String)
                    .collect(),
            ),
        );
        map.insert(
            "max_persistence_class".into(),
            Digestable::String(self.max_persistence_class.clone()),
        );
        if !self.reason.is_empty() {
            map.insert("reason".into(), Digestable::String(self.reason.clone()));
        }
        Digestable::Object(map)
    }
}

/// Allowed or not, and why. The reason is the product, not a courtesy: the point
/// of a standing rule set is that someone can read why a thing was refused and
/// decide whether the rule is the one they meant to write.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PolicyDecision {
    pub allowed: bool,
    pub reason: String,
    pub matched: String,
}

/// A named, digestable set of standing capabilities.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CapabilityPolicy {
    pub policy_id: String,
    pub allow: Vec<Capability>,
    pub deny: Vec<Capability>,
    pub description: String,
}

impl CapabilityPolicy {
    pub fn new(policy_id: impl Into<String>, allow: Vec<Capability>) -> Self {
        CapabilityPolicy {
            policy_id: policy_id.into(),
            allow,
            deny: Vec::new(),
            description: String::new(),
        }
    }

    /// The policy that permits nothing.
    ///
    /// Not an error state and not a placeholder. It is the correct starting
    /// point for a system that must be told what it may do, so that forgetting
    /// to choose fails closed instead of open.
    pub fn deny_everything() -> Self {
        CapabilityPolicy {
            policy_id: "deny-everything".into(),
            allow: Vec::new(),
            deny: Vec::new(),
            description: "Permits nothing. Every capability must be added deliberately."
                .into(),
        }
    }

    pub fn decide(&self, action: &str, target: &str, persistence_class: &str) -> PolicyDecision {
        for capability in &self.deny {
            if capability.matches_action_and_target(action, target) {
                let detail = if capability.reason.is_empty() {
                    String::new()
                } else {
                    format!(": {}", capability.reason)
                };
                return PolicyDecision {
                    allowed: false,
                    reason: format!(
                        "policy {:?} explicitly denies {action:?} on {target:?}{detail}. \
                         A deny is not outvoted by an allow",
                        self.policy_id
                    ),
                    matched: format!("deny:{}", capability.action),
                };
            }
        }
        for capability in &self.allow {
            if capability.admits(action, target, persistence_class) {
                return PolicyDecision {
                    allowed: true,
                    reason: format!(
                        "policy {:?} permits {action:?} within {:?} up to {}",
                        self.policy_id, capability.scope, capability.max_persistence_class
                    ),
                    matched: format!("allow:{}", capability.action),
                };
            }
        }

        // Say which of the three ways it missed, because "denied" alone sends
        // someone to re-read the whole policy to find out what to change.
        let near: Vec<&Capability> = self.allow.iter().filter(|c| c.action == action).collect();
        let detail = if near.is_empty() {
            format!("no capability in the policy names the action {action:?}")
        } else if !near.iter().any(|c| scope_admits(&c.scope, target)) {
            let scopes: Vec<&Vec<String>> = near.iter().map(|c| &c.scope).collect();
            format!("{action:?} is permitted, but not on {target:?}: scopes are {scopes:?}")
        } else if persistence_rank(persistence_class).is_none() {
            format!(
                "{action:?} on {target:?} is in scope, but its persistence class is \
                 {persistence_class:?}, which is not a rank on the recovery scale. An effect \
                 nobody could classify is not one a ceiling can admit"
            )
        } else {
            let mut ceilings: Vec<String> = near
                .iter()
                .map(|c| c.max_persistence_class.clone())
                .collect();
            ceilings.sort();
            ceilings.dedup();
            format!(
                "{action:?} on {target:?} is in scope, but {persistence_class} exceeds the \
                 ceiling {ceilings:?}"
            )
        };
        PolicyDecision {
            allowed: false,
            reason: format!("policy {:?} does not permit it: {detail}", self.policy_id),
            matched: String::new(),
        }
    }

    /// Rebuild from the canonical mapping, refusing unknown fields and any
    /// schema version this build does not understand.
    pub fn from_canonical(value: &Digestable) -> Result<Self, PolicyError> {
        let Digestable::Object(map) = value else {
            return Err(PolicyError("policy must be an object".into()));
        };
        const KNOWN: &[&str] = &[
            "schema_version",
            "policy_id",
            "allow",
            "deny",
            "description",
        ];
        if let Some(unknown) = map.keys().find(|k| !KNOWN.contains(&k.as_str())) {
            return Err(PolicyError(format!("unknown policy field {unknown:?}")));
        }
        match map.get("schema_version") {
            Some(Digestable::String(s)) if s == POLICY_SCHEMA_VERSION => {}
            Some(Digestable::String(s)) => {
                return Err(PolicyError(format!(
                    "unknown policy schema_version {s:?}; refusing to guess at it"
                )))
            }
            _ => return Err(PolicyError("policy.schema_version must be a string".into())),
        }
        let policy_id = match map.get("policy_id") {
            Some(Digestable::String(s)) => s.clone(),
            _ => return Err(PolicyError("policy.policy_id must be a string".into())),
        };
        let list = |key: &str| -> Result<Vec<Capability>, PolicyError> {
            match map.get(key) {
                Some(Digestable::Array(items)) => {
                    items.iter().map(Capability::from_canonical).collect()
                }
                None => Ok(Vec::new()),
                _ => Err(PolicyError(format!("policy.{key} must be an array"))),
            }
        };
        let description = match map.get("description") {
            Some(Digestable::String(s)) => s.clone(),
            None => String::new(),
            _ => return Err(PolicyError("policy.description must be a string".into())),
        };
        Ok(CapabilityPolicy {
            policy_id,
            allow: list("allow")?,
            deny: list("deny")?,
            description,
        })
    }

    pub fn to_canonical(&self) -> Digestable {
        let mut map: BTreeMap<String, Digestable> = BTreeMap::new();
        map.insert(
            "schema_version".into(),
            Digestable::String(POLICY_SCHEMA_VERSION.into()),
        );
        map.insert("policy_id".into(), Digestable::String(self.policy_id.clone()));
        map.insert(
            "allow".into(),
            Digestable::Array(self.allow.iter().map(Capability::to_canonical).collect()),
        );
        map.insert(
            "deny".into(),
            Digestable::Array(self.deny.iter().map(Capability::to_canonical).collect()),
        );
        if !self.description.is_empty() {
            map.insert(
                "description".into(),
                Digestable::String(self.description.clone()),
            );
        }
        Digestable::Object(map)
    }

    pub fn canonical_bytes(&self) -> Vec<u8> {
        canonical_json_bytes(&self.to_canonical())
    }

    /// The digest an authority envelope binds itself to.
    ///
    /// Every field is inside it, including the order of entries, so there is no
    /// way to widen a policy while keeping the same digest — and therefore no
    /// way to widen one without making every outstanding authorization stale.
    pub fn digest(&self) -> String {
        digest_value(&self.to_canonical())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    fn notes() -> CapabilityPolicy {
        let mut policy = CapabilityPolicy::new(
            "notes-only",
            vec![Capability::new("fs.write", v(&["notes/**"]), "REVERSIBLE").unwrap()],
        );
        policy.deny = vec![Capability::with_reason(
            "fs.write",
            v(&["notes/secrets/**"]),
            "REVERSIBLE",
            "key material never gets written by an agent",
        )
        .unwrap()];
        policy
    }

    #[test]
    fn the_empty_policy_permits_nothing() {
        let empty = CapabilityPolicy::deny_everything();
        for action in ["fs.write", "net.send", "proc.spawn"] {
            assert!(!empty.decide(action, "anything", "REVERSIBLE").allowed);
        }
    }

    #[test]
    fn what_it_permits_it_permits() {
        let decision = notes().decide("fs.write", "notes/a.md", "REVERSIBLE");
        assert!(decision.allowed);
        assert_eq!(decision.matched, "allow:fs.write");
    }

    #[test]
    fn a_deny_overrides_an_allow_that_would_have_matched() {
        let policy = notes();
        assert!(policy.decide("fs.write", "notes/a.md", "REVERSIBLE").allowed);
        for persistence in ["REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE", "UNKNOWN"] {
            let decision = policy.decide("fs.write", "notes/secrets/key", persistence);
            assert!(!decision.allowed, "{persistence} slipped past the deny");
            assert!(decision.reason.contains("not outvoted by an allow"));
        }
    }

    #[test]
    fn a_reversible_ceiling_does_not_admit_an_irreversible_effect() {
        let decision = notes().decide("fs.write", "notes/a.md", "IRREVERSIBLE");
        assert!(!decision.allowed);
        assert!(decision.reason.contains("exceeds the ceiling"));
    }

    #[test]
    fn unknown_is_never_admitted_by_any_ceiling() {
        let wide = CapabilityPolicy::new(
            "wide",
            vec![Capability::new("fs.write", v(&["**"]), "IRREVERSIBLE").unwrap()],
        );
        let decision = wide.decide("fs.write", "anything", "UNKNOWN");
        assert!(!decision.allowed);
        assert!(decision.reason.contains("not a rank on the recovery scale"));
    }

    #[test]
    fn unknown_cannot_be_written_as_a_ceiling_either() {
        let error = Capability::new("fs.write", v(&["notes/**"]), "UNKNOWN").unwrap_err();
        assert!(error.0.contains("not accepted as a ceiling"));
    }

    #[test]
    fn a_policy_round_trips_through_its_canonical_form() {
        let original = notes();
        let rebuilt = CapabilityPolicy::from_canonical(&original.to_canonical()).unwrap();
        assert_eq!(original, rebuilt);
        assert_eq!(original.digest(), rebuilt.digest());
    }

    #[test]
    fn widening_a_scope_changes_the_digest() {
        let narrow = CapabilityPolicy::new(
            "p",
            vec![Capability::new("fs.write", v(&["notes/**"]), "REVERSIBLE").unwrap()],
        );
        let wide = CapabilityPolicy::new(
            "p",
            vec![Capability::new("fs.write", v(&["**"]), "REVERSIBLE").unwrap()],
        );
        assert_ne!(narrow.digest(), wide.digest());
    }
}
