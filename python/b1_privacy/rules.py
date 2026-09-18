"""What a privacy scan looks for in a tree about to be published.

Distinct from `b1_security`, which asks whether *code behaves* unsafely. This
asks whether *the tree reveals* something -- a key, a person, a machine. The two
overlap on hardcoded secrets and diverge everywhere else, and they are kept
apart because a finding that "this file contains an email address" is not a
statement about the code's behaviour and should not be counted as one.

The evidence types are shared, though: `b1_security.finding` already carries
Finding, Coverage and a ScanManifest whose `epistemic_status` is computed rather
than supplied. A privacy scan over a partial tree must not read as clean, which
is the identical requirement, so it reuses the identical machinery rather than
growing a second one that would drift.

Every `evidence` string is truncated and, when any rule matching a line
redacts, **no** rule matching that line shows it -- a report that quotes the key
it found has copied the key into a second file, and it makes no difference which
rule did the quoting.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from b1_protocol.canonical import canonical_json_bytes, digest_bytes
from b1_security.finding import Coverage, Finding, ScanManifest

__all__ = ["PrivacyRule", "PRIVACY_RULES", "privacy_ruleset_digest", "scan_tree"]


@dataclass(frozen=True, slots=True)
class PrivacyRule:
    rule_id: str
    severity: str
    pattern: str
    message: str
    remediation: str
    decidable: bool
    # True when a match must not be echoed into the report. A scan that quoted
    # the secret it found would have copied it somewhere new.
    redact: bool = False

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern)


PRIVACY_RULES: tuple[PrivacyRule, ...] = (
    PrivacyRule(
        rule_id="B1-PRIV-001-private-key-block",
        severity="CRITICAL",
        pattern=r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        message="a PEM private key block",
        remediation="remove it, rotate the key, and rewrite the history that carried it",
        decidable=True,
        redact=True,
    ),
    PrivacyRule(
        rule_id="B1-PRIV-002-aws-access-key",
        severity="CRITICAL",
        pattern=r"\bAKIA[0-9A-Z]{16}\b",
        message="an AWS access key id",
        remediation="revoke it first; removing the file does not revoke anything",
        decidable=True,
        redact=True,
    ),
    PrivacyRule(
        rule_id="B1-PRIV-003-credentials-in-url",
        severity="CRITICAL",
        pattern=r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@",
        message="a URL carrying a username and password",
        remediation="move the credentials to the environment and rotate them",
        decidable=True,
        redact=True,
    ),
    PrivacyRule(
        rule_id="B1-PRIV-004-jwt",
        severity="HIGH",
        pattern=r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.",
        message="something shaped like a signed JWT",
        remediation="treat it as live until proven otherwise; rotate, then remove",
        decidable=False,
        redact=True,
    ),
    PrivacyRule(
        rule_id="B1-PRIV-005-email-address",
        severity="HIGH",
        pattern=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        message="an email address, which identifies a person",
        remediation="use a role address, or remove it",
        # A heuristic: `noreply@` addresses and documentation examples match
        # too, and telling them apart is a judgement rather than a fact.
        decidable=False,
    ),
    PrivacyRule(
        rule_id="B1-PRIV-006-absolute-home-path",
        severity="MEDIUM",
        pattern=r"(/home/[A-Za-z0-9._-]+/|/Users/[A-Za-z0-9._-]+/|[A-Z]:\\Users\\[A-Za-z0-9._-]+\\)",
        message="an absolute path into someone's home directory",
        remediation="make it relative, or name the variable instead of the path",
        decidable=True,
    ),
    PrivacyRule(
        rule_id="B1-PRIV-007-mac-address",
        severity="MEDIUM",
        pattern=r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b",
        message="something shaped like a MAC address, which identifies a machine",
        remediation="remove it; a hardware address is not configuration",
        decidable=False,
    ),
    PrivacyRule(
        rule_id="B1-PRIV-008-routable-ip",
        severity="LOW",
        pattern=r"\b(?!127\.|10\.|192\.168\.|0\.0\.0\.0|255\.)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
        message="a literal IP address outside the loopback and private ranges",
        remediation="use a hostname, or confirm it is meant to be public",
        # Version strings and example addresses match this too.
        decidable=False,
    ),
    PrivacyRule(
        rule_id="B1-PRIV-009-session-url",
        severity="LOW",
        pattern=r"claude\.ai/code/session_[A-Za-z0-9]+",
        message="a session URL, which identifies one working session",
        remediation="keep it in commit metadata rather than in the tree",
        decidable=True,
    ),
    PrivacyRule(
        rule_id="B1-PRIV-010-secret-assignment",
        severity="HIGH",
        pattern=r"(?i)(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*[\"'][A-Za-z0-9/+_-]{16,}[\"']",
        message="a long literal assigned to a secret-shaped name",
        remediation="read it from the environment at run time",
        decidable=False,
        redact=True,
    ),
)


def privacy_ruleset_digest(rules: tuple[PrivacyRule, ...] = PRIVACY_RULES) -> str:
    return digest_bytes(canonical_json_bytes([
        {"rule_id": r.rule_id, "severity": r.severity, "pattern": r.pattern,
         "decidable": r.decidable, "redact": r.redact}
        for r in sorted(rules, key=lambda r: r.rule_id)
    ]))


def scan_tree(
    targets: dict[str, str | None],
    *,
    scanner_id: str = "b1-privacy-scan-1",
    rules: tuple[PrivacyRule, ...] = PRIVACY_RULES,
    degraded: tuple[str, ...] = (),
) -> ScanManifest:
    """Scan a mapping of path -> text, where ``None`` means unreadable.

    An unreadable file is recorded in `not_scanned` with its reason, never
    skipped. A privacy scan that quietly ignored the binary it could not read
    would report a clean tree while the one file nobody could open sat in it.
    """
    findings: list[Finding] = []
    scanned: list[str] = []
    not_scanned: list[tuple[str, str]] = []

    for target in sorted(targets):
        text = targets[target]
        if text is None:
            not_scanned.append((target, "unreadable as text"))
            continue
        scanned.append(target)
        for number, line in enumerate(text.splitlines(), start=1):
            matched = [rule for rule in rules if rule.compiled().search(line)]
            if not matched:
                continue

            # Redaction is a property of the *line*, not of the rule that
            # matched it. The first version of this made it per-rule, and the
            # tests caught the consequence immediately:
            # `postgres://bob:hunter2@db.internal/x` matches both the
            # credentials-in-URL rule (which redacts) and the email-address rule
            # (which does not, because an address is the thing worth showing) --
            # so the non-redacting rule printed the password the redacting one
            # had just withheld.
            #
            # The reason to redact is "this line contains a secret", and that
            # does not stop being true for the second rule that matches it.
            redacted = any(rule.redact for rule in matched)

            for rule in matched:
                findings.append(Finding(
                    finding_id=f"{target}:{number}:{rule.rule_id}",
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    target=target,
                    line=number,
                    message=rule.message,
                    epistemic_status="VERIFIED" if rule.decidable else "WORKING_ASSUMPTION",
                    evidence="" if redacted else line.strip()[:160],
                    remediation=rule.remediation,
                ))

    return ScanManifest(
        scanner_id=scanner_id,
        ruleset_digest=privacy_ruleset_digest(rules),
        findings=tuple(findings),
        coverage=Coverage(
            scanned=tuple(scanned),
            not_scanned=tuple(not_scanned),
            rules_applied=tuple(sorted(r.rule_id for r in rules)),
            degraded=degraded,
        ),
    )
