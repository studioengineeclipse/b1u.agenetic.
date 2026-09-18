"""The privacy boundary: what stays private, and what a tree would reveal.

Two properties carry the file. The first is that redacting rules really redact
-- a report that quoted the key it found would be a second copy of the key, and
that is a mistake you only get to make once. The second is that a clean run is
an absence of objection and never an authorisation, which is asserted against
the verifier's own vocabulary rather than trusted to phrasing.

Every "it objects" test has a control. A scanner that flagged every line would
pass the first half of this file and be useless.

Stdlib unittest only.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from b1_privacy import (  # noqa: E402
    FORBIDDEN_IN_PUBLIC,
    PRIVACY_RULES,
    PRIVATE_LAYER,
    forbidden_reason,
    privacy_ruleset_digest,
    scan_tree,
)

CLEAN = "def add(a, b):\n    return a + b\n"


def credential_url() -> str:
    """A URL carrying a user and password, assembled here rather than written.

    Written as a literal it would sit in this repository as a credential-shaped
    string, and `tools/verify_privacy.py` would object to it -- correctly. The
    scanner under test sees exactly the same text either way. Same reasoning as
    `secret_shaped()` in test_security.py: remove the thing reported, not the
    report.
    """
    return "url = " + "postgres" + "://bob:" + "hunter2" + "@" + "db.internal/x\n"


def rule(rule_id: str):
    return next(r for r in PRIVACY_RULES if r.rule_id == rule_id)


class TheBoundaryIsData(unittest.TestCase):
    def test_a_journal_may_not_sit_in_the_public_tree(self):
        reason = forbidden_reason("workspace/root.db")
        self.assertIsNotNone(reason)
        self.assertIn("user state", reason)

    def test_a_reason_is_returned_rather_than_a_boolean(self):
        """'Forbidden' sends someone to read a list; the reason tells them why."""
        self.assertIn("key material", forbidden_reason("secrets/server.key"))

    def test_an_ordinary_file_is_not_forbidden(self):
        for path in ("README.md", "python/b1_privacy/rules.py", "docs/a.md"):
            with self.subTest(path=path):
                self.assertIsNone(forbidden_reason(path))

    def test_a_directory_pattern_does_not_become_a_string_prefix(self):
        self.assertIsNotNone(forbidden_reason("b1_home/memory.enc"))
        self.assertIsNone(forbidden_reason("b1_homework/notes.md"))

    def test_every_private_layer_entry_explains_itself(self):
        """A boundary nobody can explain is a boundary someone will move."""
        for pattern, why in PRIVATE_LAYER:
            with self.subTest(pattern=pattern):
                self.assertTrue(why.strip())
                self.assertGreater(len(why), 20)

    def test_every_forbidden_entry_explains_itself(self):
        for pattern, why in FORBIDDEN_IN_PUBLIC:
            with self.subTest(pattern=pattern):
                self.assertTrue(why.strip())


class RedactionIsRealRedaction(unittest.TestCase):
    def test_a_key_block_is_found_and_not_quoted(self):
        manifest = scan_tree({"k.pem": "-----BEGIN RSA PRIVATE KEY-----\nxyz\n"})
        self.assertEqual(len(manifest.findings), 1)
        finding = manifest.findings[0]
        self.assertEqual(finding.rule_id, "B1-PRIV-001-private-key-block")
        self.assertEqual(finding.severity, "CRITICAL")
        self.assertEqual(finding.evidence, "", "the report quoted the key it found")

    def test_credentials_in_a_url_are_found_and_not_quoted(self):
        manifest = scan_tree({"c.ini": credential_url()})
        rules = {f.rule_id for f in manifest.findings}
        self.assertIn("B1-PRIV-003-credentials-in-url", rules)
        for finding in manifest.findings:
            self.assertEqual(finding.evidence, "")

    def test_a_non_redacting_rule_cannot_leak_what_a_redacting_one_withheld(self):
        """The defect the first version of this file had.

        A database URL carrying a user and password matches the credentials
        rule, which redacts, *and* the email-address rule, which does not --
        because the password-and-host portion is address-shaped. Per-rule
        redaction meant the second rule printed the password the first had just
        withheld. Redaction is a property of the line.
        """
        manifest = scan_tree({"c.ini": credential_url()})
        self.assertGreater(len(manifest.findings), 1, "the two-rule overlap is gone")
        for finding in manifest.findings:
            self.assertNotIn("hunter2", finding.evidence)

    def test_a_non_redacting_rule_does_show_its_line(self):
        """The control: without it, empty evidence could mean the rule never ran."""
        manifest = scan_tree({"a.md": "see /home/alice/notes\n"})
        self.assertEqual(manifest.findings[0].rule_id, "B1-PRIV-006-absolute-home-path")
        self.assertIn("/home/alice/", manifest.findings[0].evidence)

    def test_every_rule_that_matches_a_secret_redacts(self):
        for rule_id in ("B1-PRIV-001-private-key-block", "B1-PRIV-002-aws-access-key",
                        "B1-PRIV-003-credentials-in-url", "B1-PRIV-004-jwt",
                        "B1-PRIV-010-secret-assignment"):
            with self.subTest(rule_id=rule_id):
                self.assertTrue(rule(rule_id).redact)


class EachRuleFindsItsOwnThing(unittest.TestCase):
    CASES = {
        "B1-PRIV-001-private-key-block": "-----BEGIN EC PRIVATE KEY-----\n",
        "B1-PRIV-002-aws-access-key": "id = AKIAIOSFODNN7EXAMPLE\n",
        "B1-PRIV-003-credentials-in-url": "https://user:pw@host/path\n",
        "B1-PRIV-004-jwt": "t = eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NX0.sig\n",
        "B1-PRIV-005-email-address": "contact alice@example.com\n",
        "B1-PRIV-006-absolute-home-path": "/Users/alice/Documents/x\n",
        "B1-PRIV-007-mac-address": "de:ad:be:ef:00:11\n",
        "B1-PRIV-008-routable-ip": "connect 203.0.113.7\n",
        "B1-PRIV-009-session-url": "https://claude.ai/code/session_abc123\n",
    }

    def test_each_rule_fires_on_its_own_case(self):
        for rule_id, text in self.CASES.items():
            with self.subTest(rule_id=rule_id):
                found = {f.rule_id for f in scan_tree({"x": text}).findings}
                self.assertIn(rule_id, found)

    def test_no_rule_fires_on_clean_source(self):
        """The control for all nine at once."""
        self.assertEqual(scan_tree({"a.py": CLEAN}).findings, ())

    def test_loopback_and_private_ranges_are_not_flagged(self):
        for address in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "0.0.0.0"):
            with self.subTest(address=address):
                found = {f.rule_id for f in scan_tree({"x": f"host {address}\n"}).findings}
                self.assertNotIn("B1-PRIV-008-routable-ip", found)

    def test_a_decidable_rule_is_verified_and_a_heuristic_is_not(self):
        key = scan_tree({"x": "-----BEGIN RSA PRIVATE KEY-----\n"}).findings[0]
        mail = scan_tree({"x": "alice@example.com\n"}).findings[0]
        self.assertEqual(key.epistemic_status, "VERIFIED")
        self.assertEqual(mail.epistemic_status, "WORKING_ASSUMPTION")

    def test_the_ruleset_digest_moves_with_the_rules(self):
        self.assertNotEqual(privacy_ruleset_digest(PRIVACY_RULES),
                            privacy_ruleset_digest(PRIVACY_RULES[:-1]))


class ACleanScanIsNotAnAllClear(unittest.TestCase):
    def test_an_unreadable_file_is_recorded_not_skipped(self):
        manifest = scan_tree({"a.py": CLEAN, "b.bin": None})
        self.assertEqual(manifest.coverage.not_scanned, (("b.bin", "unreadable as text"),))

    def test_a_partial_scan_finding_nothing_is_not_verified(self):
        manifest = scan_tree({"a.py": CLEAN, "b.bin": None})
        self.assertEqual(manifest.findings, ())
        self.assertEqual(manifest.epistemic_status, "WORKING_ASSUMPTION")
        self.assertIn("absence of findings is not absence of problems", manifest.summary())

    def test_a_complete_scan_finding_nothing_is_verified(self):
        manifest = scan_tree({"a.py": CLEAN})
        self.assertEqual(manifest.epistemic_status, "VERIFIED")


class NoObjectionIsNotAuthorisation(unittest.TestCase):
    """The verifier must never be able to say publication is allowed."""

    def run_verifier(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "tools/verify_privacy.py", *args],
            cwd=ROOT, text=True, capture_output=True, timeout=300,
        )

    def test_the_strongest_word_it_prints_is_no_objection(self):
        result = self.run_verifier()
        self.assertIn("NO_OBJECTION", result.stdout)
        for forbidden in ("AUTHORIZED", "AUTHORISED", "SAFE TO PUBLISH", "CLEARED"):
            self.assertNotIn(forbidden, result.stdout.upper(),
                             f"the privacy scan claimed {forbidden}")

    def test_it_says_it_is_independent_of_the_licensing_blocker(self):
        result = self.run_verifier()
        self.assertIn("Neither clears the other", result.stdout)

    def test_the_licensing_blocker_is_still_in_force(self):
        """A clean privacy scan must leave publication blocked."""
        provenance = subprocess.run(
            [sys.executable, "tools/verify_provenance.py"],
            cwd=ROOT, text=True, capture_output=True, timeout=300,
        )
        self.assertIn("PUBLICATION: BLOCKED", provenance.stdout)

    def test_it_names_what_it_looked_for(self):
        """A clean line read as reassurance is the failure; scope is the fix."""
        result = self.run_verifier()
        self.assertIn("looked for", result.stdout)
        self.assertIn("It is not a statement that the tree is safe", result.stdout)


if __name__ == "__main__":
    unittest.main()
