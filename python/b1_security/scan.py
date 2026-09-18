"""A small deterministic scanner, and a ruleset that says what it cannot see.

Deliberately modest. Ten regex rules over text is not a security product, and
calling it one would be the overclaim this project exists to avoid. What it is:
a real, local, dependency-free source of findings, so the layering above it --
findings as tournament evidence, findings as policy constraints -- is exercised
against something rather than against a fixture.

Every rule declares `decidable`. A rule that matches a structurally decidable
fact (a literal `shell=True`) produces a VERIFIED finding; a rule that matches a
heuristic (a string that *looks* like a key) produces WORKING_ASSUMPTION. The
distinction is not decoration: a CRITICAL finding a heuristic is unsure about is
a different object from one a parser proved, and collapsing them is how a noisy
scanner teaches people to ignore it.

The ruleset has a digest, and a scan manifest carries it. Two scans of the same
file under different rulesets are different evidence, and a manifest that did
not say which rules ran could not be compared with anything.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from b1_protocol.canonical import canonical_json_bytes, digest_bytes

from .finding import Coverage, Finding, ScanManifest

__all__ = ["Rule", "RULES", "ruleset_digest", "scan_text", "scan_targets"]


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    severity: str
    pattern: str
    message: str
    remediation: str
    # True when a match is a structurally decidable fact rather than a guess.
    # Drives the finding's epistemic status, which is the honest part.
    decidable: bool

    def compiled(self) -> re.Pattern[str]:
        return re.compile(self.pattern)


RULES: tuple[Rule, ...] = (
    Rule(
        rule_id="B1-SEC-001-shell-true",
        severity="HIGH",
        pattern=r"subprocess\.\w+\([^)]*shell\s*=\s*True",
        message="subprocess call with shell=True: the argument string is parsed by a shell",
        remediation="pass a list of arguments and leave shell at its default",
        decidable=True,
    ),
    Rule(
        rule_id="B1-SEC-002-eval",
        severity="CRITICAL",
        pattern=r"(?<![\w.])eval\s*\(",
        message="eval() executes whatever it is given",
        remediation="parse explicitly; use ast.literal_eval for data",
        decidable=True,
    ),
    Rule(
        rule_id="B1-SEC-003-exec",
        severity="CRITICAL",
        pattern=r"(?<![\w.])exec\s*\(",
        message="exec() executes whatever it is given",
        remediation="restructure so the code path is fixed at authoring time",
        decidable=True,
    ),
    Rule(
        rule_id="B1-SEC-004-pickle-load",
        severity="HIGH",
        pattern=r"pickle\.loads?\s*\(",
        message="pickle deserialisation executes constructors from the payload",
        remediation="use JSON, or a format that cannot carry code",
        decidable=True,
    ),
    Rule(
        rule_id="B1-SEC-005-verify-disabled",
        severity="HIGH",
        pattern=r"verify\s*=\s*False|CERT_NONE|check_hostname\s*=\s*False",
        message="TLS verification disabled: the connection is encrypted and unauthenticated",
        remediation="fix the trust store rather than removing the check",
        decidable=True,
    ),
    Rule(
        rule_id="B1-SEC-006-hardcoded-secret",
        severity="HIGH",
        pattern=r"(?i)(api[_-]?key|secret|password|token)\s*=\s*[\"'][A-Za-z0-9/+_-]{16,}[\"']",
        message="a long literal assigned to a secret-shaped name",
        remediation="read it from the environment or a keyring at run time",
        # A heuristic. A test fixture and a real key look identical here, which
        # is exactly why this reports WORKING_ASSUMPTION.
        decidable=False,
    ),
    Rule(
        rule_id="B1-SEC-007-tmp-path",
        severity="MEDIUM",
        pattern=r"[\"']/tmp/[A-Za-z0-9._-]+[\"']",
        message="a fixed path under /tmp is predictable and world-writable",
        remediation="use tempfile.mkstemp or a directory you own",
        decidable=True,
    ),
    Rule(
        rule_id="B1-SEC-008-weak-hash",
        severity="MEDIUM",
        pattern=r"hashlib\.(md5|sha1)\s*\(",
        message="MD5 or SHA-1 used where a digest may be relied on",
        remediation="sha256, unless an external format forces otherwise",
        # Sometimes entirely correct -- a checksum against a legacy format --
        # so the match is a fact and the *problem* is a judgement.
        decidable=False,
    ),
    Rule(
        rule_id="B1-SEC-009-yaml-load",
        severity="HIGH",
        pattern=r"yaml\.load\s*\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)",
        message="yaml.load without SafeLoader can construct arbitrary objects",
        remediation="yaml.safe_load",
        decidable=True,
    ),
    Rule(
        rule_id="B1-SEC-010-assert-as-check",
        severity="LOW",
        pattern=r"^\s*assert\s+.*(?:auth|permission|allowed|token)",
        message="an authorisation check written as assert disappears under -O",
        remediation="raise explicitly",
        decidable=False,
    ),
)


def ruleset_digest(rules: tuple[Rule, ...] = RULES) -> str:
    """A digest over the rules themselves.

    Carried in every manifest, because two scans under different rulesets are
    different evidence and a manifest that did not say which rules ran could
    not be compared with anything -- including with itself, later.
    """
    return digest_bytes(canonical_json_bytes([
        {
            "rule_id": r.rule_id,
            "severity": r.severity,
            "pattern": r.pattern,
            "decidable": r.decidable,
        }
        for r in sorted(rules, key=lambda r: r.rule_id)
    ]))


def scan_text(
    target: str, text: str, rules: tuple[Rule, ...] = RULES
) -> tuple[Finding, ...]:
    """Every match, in file order then rule order. Pure; reads nothing."""
    findings: list[Finding] = []
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        for rule in rules:
            if rule.compiled().search(line):
                findings.append(Finding(
                    finding_id=f"{target}:{number}:{rule.rule_id}",
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    target=target,
                    line=number,
                    message=rule.message,
                    epistemic_status="VERIFIED" if rule.decidable else "WORKING_ASSUMPTION",
                    evidence=line.strip()[:160],
                    remediation=rule.remediation,
                ))
    return tuple(findings)


def scan_targets(
    targets: dict[str, str | None],
    *,
    scanner_id: str = "b1-regex-scan-1",
    rules: tuple[Rule, ...] = RULES,
    degraded: tuple[str, ...] = (),
) -> ScanManifest:
    """Scan a mapping of target -> text, where ``None`` means unreadable.

    An unreadable target goes into `not_scanned` with a reason rather than
    being skipped. Skipping it would make the manifest report a clean, complete
    scan of everything it happened to manage, which is the specific way a
    security report lies without anyone writing a false sentence.

    `degraded` names capabilities that were unavailable -- ADR-0005's semantic
    dedupe without an embeddings endpoint is the anticipated case. Passing it
    drops the manifest's status to WORKING_ASSUMPTION, by construction.
    """
    findings: list[Finding] = []
    scanned: list[str] = []
    not_scanned: list[tuple[str, str]] = []

    for target in sorted(targets):
        text = targets[target]
        if text is None:
            not_scanned.append((target, "unreadable"))
            continue
        scanned.append(target)
        findings.extend(scan_text(target, text, rules))

    return ScanManifest(
        scanner_id=scanner_id,
        ruleset_digest=ruleset_digest(rules),
        findings=tuple(findings),
        coverage=Coverage(
            scanned=tuple(scanned),
            not_scanned=tuple(not_scanned),
            rules_applied=tuple(sorted(r.rule_id for r in rules)),
            degraded=degraded,
        ),
    )
