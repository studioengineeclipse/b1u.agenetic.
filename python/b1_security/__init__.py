"""B1 Local security evidence: findings constrain, and never authorise.

Deliberately does not import `b1_authority`. A finding cannot reach an authority
envelope, a permit or the commit gate, because those objects are not in scope
here -- and `python/tests/test_security.py` asserts that import graph so the
guarantee outlives someone deciding a small exception would be convenient.
"""

from .constraint import TighteningReport, denials_from, tighten
from .evidence import SecurityVerifier, manifest_evidence_refs
from .finding import (
    BLOCKING_SEVERITIES,
    SECURITY_SCHEMA_VERSION,
    SEVERITIES,
    Coverage,
    Finding,
    ScanManifest,
    SecurityError,
)
from .scan import RULES, Rule, ruleset_digest, scan_targets, scan_text

__all__ = [
    "BLOCKING_SEVERITIES",
    "RULES",
    "SECURITY_SCHEMA_VERSION",
    "SEVERITIES",
    "Coverage",
    "Finding",
    "Rule",
    "ScanManifest",
    "SecurityError",
    "SecurityVerifier",
    "TighteningReport",
    "denials_from",
    "manifest_evidence_refs",
    "ruleset_digest",
    "scan_targets",
    "scan_text",
    "tighten",
]
