"""Security findings, coverage, and a scan manifest that cannot overclaim.

The handoff's Phase 7 exit criterion survives ADR-0005 unchanged:

    Security findings enter the tournament/evidence graph and block unsafe
    candidates without independently granting mutation authority.

Two halves, and the second is the one with teeth. A finding is *evidence*: it
constrains what B1 may claim and what a candidate may be, and it never
constrains less. What it must never do is authorise anything -- a security
subsystem that could authorise its own remediation would be an executor
granting itself authority, which is the exact collapse the
Origin/Authority/Executor/Effect separation exists to prevent.

The other thing this module is careful about
--------------------------------------------
**No findings is not no vulnerabilities.** A scan that found nothing and a scan
that looked nowhere produce the same empty list, and only `Coverage` tells them
apart. So a `ScanManifest` carries coverage beside its findings, refuses to be
built without it, and computes its own `epistemic_status` rather than accepting
one: a scan with degraded capabilities or unscanned targets reports
`WORKING_ASSUMPTION` on its completeness, never `VERIFIED`.

Provenance, stated
------------------
ADR-0005 decided to adopt Codex Security's `findings`, `coverage`,
`scan-manifest` and `patch-risk` schemas as B1's security-evidence contract.
**That port has not happened.** The archive is no longer present in this
environment, so the shapes below are B1's own, written to fill the same role.
They are not derived from those schemas, are not claimed to be compatible with
them, and `provenance/provenance.json` declares nothing from that archive here.
Aligning them is work that needs the archive back.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from b1_protocol.canonical import canonical_json_bytes, digest_value

__all__ = [
    "SECURITY_SCHEMA_VERSION",
    "SEVERITIES",
    "BLOCKING_SEVERITIES",
    "SecurityError",
    "Finding",
    "Coverage",
    "ScanManifest",
]

SECURITY_SCHEMA_VERSION = "b1-security-evidence-1"

# Ordered least to most severe. A tuple rather than an enum so the ordering is
# visible at the point of use, and `index()` is the comparison.
SEVERITIES = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")

# What blocks a candidate outright. HIGH and CRITICAL only: a MEDIUM finding is
# real and is recorded, and eliminating on it would make the tournament
# unusable for ordinary code, which is how a blocking threshold ends up being
# switched off entirely.
BLOCKING_SEVERITIES = ("HIGH", "CRITICAL")


class SecurityError(Exception):
    """A finding, coverage record or manifest is not usable as given."""


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing a scanner found, and how sure it is.

    `epistemic_status` is the scanner's confidence in *this finding*, and it is
    separate from severity on purpose. A CRITICAL finding a heuristic is unsure
    about is not the same object as a CRITICAL finding a parser proved, and
    collapsing them is how a noisy scanner teaches people to ignore it.
    """

    finding_id: str
    rule_id: str
    severity: str
    target: str
    line: int
    message: str
    # VERIFIED: the rule matched something structurally decidable.
    # WORKING_ASSUMPTION: a heuristic matched and may be wrong.
    epistemic_status: str = "WORKING_ASSUMPTION"
    evidence: str = ""
    remediation: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise SecurityError(
                f"severity must be one of {SEVERITIES}, got {self.severity!r}"
            )
        if self.epistemic_status not in ("VERIFIED", "WORKING_ASSUMPTION", "UNKNOWN", "IN_DOUBT"):
            raise SecurityError(f"unknown epistemic_status {self.epistemic_status!r}")
        if not self.finding_id or not self.rule_id:
            raise SecurityError("a finding needs both a finding_id and a rule_id")
        if self.line < 0:
            raise SecurityError("line must be non-negative")

    @property
    def blocks(self) -> bool:
        return self.severity in BLOCKING_SEVERITIES

    def to_canonical_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            "finding_id": self.finding_id,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "target": self.target,
            "line": self.line,
            "message": self.message,
            "epistemic_status": self.epistemic_status,
        }
        if self.evidence:
            out["evidence"] = self.evidence
        if self.remediation:
            out["remediation"] = self.remediation
        return out

    def evidence_ref(self) -> str:
        return f"finding:{self.rule_id}#{self.finding_id}"


@dataclass(frozen=True, slots=True)
class Coverage:
    """What was looked at, what was not, and what could not be.

    Exists because an empty findings list is ambiguous. `scanned` and
    `not_scanned` are both required and `not_scanned` entries carry a reason,
    so "we did not look there" can never be read off as "there is nothing
    there".

    `degraded` names capabilities that were unavailable -- semantic dedupe
    without an embeddings endpoint is the case ADR-0005 anticipated. A degraded
    scan is still a scan; it is just not a complete one, and the difference
    belongs in the record rather than in someone's memory.
    """

    scanned: tuple[str, ...]
    not_scanned: tuple[tuple[str, str], ...] = ()
    rules_applied: tuple[str, ...] = ()
    degraded: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.not_scanned and not self.degraded

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "scanned": list(self.scanned),
            "not_scanned": [{"target": t, "reason": r} for t, r in self.not_scanned],
            "rules_applied": list(self.rules_applied),
            "degraded": list(self.degraded),
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class ScanManifest:
    """One scan: what ran, over what, and what it is entitled to claim.

    `epistemic_status` is computed, not supplied. A caller who could set it
    could report a partial scan as VERIFIED, and the whole reason this type
    exists is that the two are easy to confuse when the findings list is empty
    either way.
    """

    scanner_id: str
    ruleset_digest: str
    findings: tuple[Finding, ...]
    coverage: Coverage
    schema_version: str = field(default=SECURITY_SCHEMA_VERSION)

    def __post_init__(self) -> None:
        if self.schema_version != SECURITY_SCHEMA_VERSION:
            raise SecurityError(
                f"unknown security schema_version {self.schema_version!r}; "
                f"refusing to guess at it"
            )
        if not self.scanner_id:
            raise SecurityError("a manifest needs a scanner_id")

    @property
    def epistemic_status(self) -> str:
        """What this scan may claim about the absence of problems.

        VERIFIED only when coverage is complete *and* nothing was degraded.
        Anything else is WORKING_ASSUMPTION, including -- especially -- a scan
        that found nothing over a partial target set, which is the shape a
        false all-clear takes.
        """
        return "VERIFIED" if self.coverage.complete else "WORKING_ASSUMPTION"

    @property
    def blocking(self) -> tuple[Finding, ...]:
        return tuple(f for f in self.findings if f.blocks)

    def summary(self) -> str:
        """One line that never reads as an all-clear when it is not one."""
        counts = {s: sum(1 for f in self.findings if f.severity == s) for s in SEVERITIES}
        found = ", ".join(f"{n} {s}" for s, n in counts.items() if n) or "no findings"
        if self.coverage.complete:
            return f"{found}; {len(self.coverage.scanned)} target(s) scanned, coverage complete"
        gaps = []
        if self.coverage.not_scanned:
            gaps.append(f"{len(self.coverage.not_scanned)} target(s) not scanned")
        if self.coverage.degraded:
            gaps.append(f"degraded: {', '.join(self.coverage.degraded)}")
        return (
            f"{found}; {len(self.coverage.scanned)} target(s) scanned, "
            f"{' and '.join(gaps)} -- absence of findings is not absence of problems here"
        )

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "scanner_id": self.scanner_id,
            "ruleset_digest": self.ruleset_digest,
            "findings": [f.to_canonical_dict() for f in self.findings],
            "coverage": self.coverage.to_canonical_dict(),
            "epistemic_status": self.epistemic_status,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_canonical_dict())

    def digest(self) -> str:
        return digest_value(self.to_canonical_dict())

    def evidence_ref(self) -> str:
        return f"scan:{self.scanner_id}#{self.digest()[:16]}"
