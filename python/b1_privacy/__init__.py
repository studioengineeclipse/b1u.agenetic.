"""B1 Local privacy boundary: what stays in %B1_HOME%, and what a tree reveals.

A privacy scan is an independent publication blocker. It can object, and a clean
run is an absence of objection -- never an authorisation. The licensing blocker
in `verify_provenance.py` is separate and neither clears the other.
"""

from .boundary import (
    B1_HOME_VARIABLE,
    FORBIDDEN_IN_PUBLIC,
    PRIVATE_LAYER,
    SELF_REFERENTIAL,
    forbidden_reason,
    self_referential_reason,
)
from .rules import PRIVACY_RULES, PrivacyRule, privacy_ruleset_digest, scan_tree

__all__ = [
    "B1_HOME_VARIABLE",
    "FORBIDDEN_IN_PUBLIC",
    "PRIVACY_RULES",
    "PRIVATE_LAYER",
    "PrivacyRule",
    "SELF_REFERENTIAL",
    "forbidden_reason",
    "self_referential_reason",
    "privacy_ruleset_digest",
    "scan_tree",
]
