"""Security evidence: it constrains, and it cannot authorise.

The handoff's Phase 7 exit criterion is one sentence and this file is arranged
around making it hard to pass by accident:

    Security findings enter the tournament/evidence graph and block unsafe
    candidates without independently granting mutation authority.

`TheExitCriterion` checks the first half against a real tournament run.
`AFindingCannotAuthorise` checks the second half three ways, including by
asserting the import graph in a fresh interpreter -- a guarantee that lives only
in a comment is one someone will later decide to make a small exception to.

`AbsenceOfFindingsIsNotAbsenceOfProblems` is the epistemic half, and the one
that would be easiest to leave out: an empty findings list means the same thing
whether the scanner looked everywhere or nowhere.

Stdlib unittest only.
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from b1_models import DeterministicProvider, ModelRegistry  # noqa: E402
from b1_policy import Capability, CapabilityPolicy  # noqa: E402
from b1_security import (  # noqa: E402
    RULES,
    Coverage,
    Finding,
    ScanManifest,
    SecurityError,
    SecurityVerifier,
    denials_from,
    ruleset_digest,
    scan_targets,
    scan_text,
    tighten,
)
from b1_tournament import (  # noqa: E402
    Candidate,
    TaskClass,
    Tournament,
    Verdict,
)

UNSAFE = "import subprocess\nsubprocess.run(cmd, shell=True)\n"
SAFE = "import subprocess\nsubprocess.run([binary, argument])\n"


class TheScannerSaysHowSureItIs(unittest.TestCase):
    def test_a_decidable_rule_produces_a_verified_finding(self):
        findings = scan_text("a.py", UNSAFE)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule_id, "B1-SEC-001-shell-true")
        self.assertEqual(findings[0].epistemic_status, "VERIFIED")

    def test_a_heuristic_rule_produces_a_working_assumption(self):
        findings = scan_text("a.py", 'API_KEY = "abcdefghijklmnopqrstuvwx"\n')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].epistemic_status, "WORKING_ASSUMPTION")
        self.assertEqual(findings[0].severity, "HIGH")

    def test_severity_and_confidence_are_separate_axes(self):
        """A CRITICAL a heuristic guessed at is not a CRITICAL a parser proved."""
        proven = scan_text("a.py", "eval(user_input)\n")[0]
        guessed = scan_text("a.py", 'password = "hunter2hunter2hunter2"\n')[0]
        self.assertEqual(proven.severity, "CRITICAL")
        self.assertEqual(proven.epistemic_status, "VERIFIED")
        self.assertEqual(guessed.severity, "HIGH")
        self.assertEqual(guessed.epistemic_status, "WORKING_ASSUMPTION")

    def test_clean_source_produces_no_findings(self):
        """The control. Without it every rule could match everything."""
        self.assertEqual(scan_text("a.py", SAFE), ())

    def test_the_ruleset_has_a_digest_that_moves_with_the_rules(self):
        trimmed = RULES[:-1]
        self.assertNotEqual(ruleset_digest(RULES), ruleset_digest(trimmed))
        self.assertEqual(ruleset_digest(RULES), ruleset_digest(RULES))

    def test_a_finding_with_an_unknown_severity_is_refused(self):
        with self.assertRaises(SecurityError):
            Finding(finding_id="f", rule_id="r", severity="SPICY", target="a", line=1,
                    message="m")


class AbsenceOfFindingsIsNotAbsenceOfProblems(unittest.TestCase):
    def test_an_unreadable_target_is_recorded_not_skipped(self):
        manifest = scan_targets({"a.py": SAFE, "b.bin": None})
        self.assertEqual(manifest.coverage.scanned, ("a.py",))
        self.assertEqual(manifest.coverage.not_scanned, (("b.bin", "unreadable"),))

    def test_a_partial_scan_finding_nothing_is_not_verified(self):
        manifest = scan_targets({"a.py": SAFE, "b.bin": None})
        self.assertEqual(manifest.findings, ())
        self.assertEqual(manifest.epistemic_status, "WORKING_ASSUMPTION")
        self.assertIn("absence of findings is not absence of problems",
                      manifest.summary())

    def test_a_complete_scan_finding_nothing_is_verified(self):
        """The control: without it the test above passes for a status that is
        always WORKING_ASSUMPTION, which would say nothing."""
        manifest = scan_targets({"a.py": SAFE})
        self.assertEqual(manifest.epistemic_status, "VERIFIED")
        self.assertIn("coverage complete", manifest.summary())

    def test_a_degraded_capability_drops_the_status(self):
        """ADR-0005's anticipated case: semantic dedupe with no endpoint."""
        manifest = scan_targets({"a.py": SAFE}, degraded=("semantic-dedupe",))
        self.assertEqual(manifest.epistemic_status, "WORKING_ASSUMPTION")
        self.assertIn("degraded: semantic-dedupe", manifest.summary())

    def test_the_status_is_computed_rather_than_supplied(self):
        """A caller who could set it could report a partial scan as VERIFIED."""
        manifest = ScanManifest(
            scanner_id="s", ruleset_digest="d", findings=(),
            coverage=Coverage(scanned=(), not_scanned=(("x", "nobody looked"),)),
        )
        self.assertEqual(manifest.epistemic_status, "WORKING_ASSUMPTION")
        self.assertNotIn("epistemic_status", ScanManifest.__slots__)


class AFindingCanNarrowThePolicy(unittest.TestCase):
    def policy(self) -> CapabilityPolicy:
        return CapabilityPolicy(
            policy_id="src", allow=(Capability("fs.write", ("src/**",)),)
        )

    def test_a_blocking_finding_denies_its_own_target(self):
        report = tighten(self.policy(), scan_targets({"src/a.py": UNSAFE}))
        self.assertTrue(report.changed)
        self.assertFalse(report.after.decide("fs.write", "src/a.py", "REVERSIBLE").allowed)

    def test_it_denies_that_target_and_not_its_neighbour(self):
        """A finding in one file says nothing about the file beside it."""
        report = tighten(self.policy(), scan_targets({"src/a.py": UNSAFE}))
        self.assertTrue(report.after.decide("fs.write", "src/b.py", "REVERSIBLE").allowed)

    def test_the_allow_list_is_passed_through_untouched(self):
        report = tighten(self.policy(), scan_targets({"src/a.py": UNSAFE}))
        self.assertTrue(report.allow_unchanged)
        self.assertIs(report.after.allow, report.before.allow)

    def test_no_finding_can_widen_a_policy(self):
        """Over every rule, not just the one the other tests happen to use.

        Anything the original policy denied must still be denied, and anything
        it refused must still be refused. A tightening that opened a single
        target would be caught here whichever rule produced it.
        """
        base = CapabilityPolicy(
            policy_id="base",
            allow=(Capability("fs.write", ("src/**",)),),
            deny=(Capability("fs.write", ("src/secrets/**",), reason="never"),),
        )
        probes = [
            ("fs.write", "src/a.py", "REVERSIBLE"),
            ("fs.write", "src/secrets/key", "REVERSIBLE"),
            ("fs.write", "elsewhere/a.py", "REVERSIBLE"),
            ("net.send", "src/a.py", "REVERSIBLE"),
            ("fs.write", "src/a.py", "IRREVERSIBLE"),
            ("fs.write", "src/a.py", "UNKNOWN"),
        ]
        sources = [UNSAFE, "eval(x)\n", "yaml.load(s)\n", 'token = "aaaaaaaaaaaaaaaaaaaa"\n',
                   "pickle.loads(b)\n", SAFE]
        for index, source in enumerate(sources):
            with self.subTest(source=index):
                report = tighten(base, scan_targets({f"src/f{index}.py": source}))
                for probe in probes:
                    if not base.decide(*probe).allowed:
                        self.assertFalse(
                            report.after.decide(*probe).allowed,
                            f"{probe} was refused before and is permitted after",
                        )

    def test_a_scan_with_no_blocking_findings_leaves_the_policy_identical(self):
        before = self.policy()
        report = tighten(before, scan_targets({"src/a.py": SAFE}))
        self.assertFalse(report.changed)
        self.assertEqual(report.after.digest(), before.digest())
        self.assertIn("unchanged", report.summary())

    def test_a_non_blocking_finding_does_not_narrow_anything(self):
        """MEDIUM is recorded and does not eliminate or deny."""
        manifest = scan_targets({"src/a.py": 'path = "/tmp/b1-fixed"\n'})
        self.assertEqual([f.severity for f in manifest.findings], ["MEDIUM"])
        self.assertFalse(tighten(self.policy(), manifest).changed)

    def test_denials_are_only_ever_denials(self):
        manifest = scan_targets({"src/a.py": UNSAFE, "src/b.py": "eval(x)\n"})
        denials = denials_from(manifest.blocking)
        self.assertEqual(len(denials), 2)
        for capability in denials:
            self.assertIn("blocking security finding", capability.reason)


class AFindingCannotAuthorise(unittest.TestCase):
    def test_the_package_does_not_import_the_authority_layer(self):
        """Asserted in a fresh interpreter, not by reading the source.

        A text scan catches a direct import and misses a transitive one, and
        the transitive one is how this guarantee would actually erode.
        """
        code = (
            "import sys; sys.path.insert(0, %r);"
            "import b1_security;"
            "print('b1_authority' in sys.modules)" % str(ROOT / "python")
        )
        result = subprocess.run([sys.executable, "-c", code], text=True,
                                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "False")

    def test_no_source_file_mentions_the_gate(self):
        for path in (ROOT / "python" / "b1_security").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("import b1_authority", text)
                self.assertNotIn("from b1_authority", text)

    def test_a_verifier_result_carries_nothing_that_could_permit(self):
        """The type is the guarantee: there is no field to put permission in."""
        result = SecurityVerifier().check(Candidate("c1", "model", SAFE), "task")
        fields = set(type(result).__slots__)
        self.assertEqual(
            fields, {"verifier_id", "candidate_id", "passed", "detail", "conclusive"}
        )


class TheExitCriterion(unittest.TestCase):
    """A finding blocks an unsafe candidate, in a real tournament run."""

    def tournament(self, answer: str) -> Tournament:
        return Tournament(
            registry=ModelRegistry(),
            verifiers=(SecurityVerifier(),),
            deterministic_participant=DeterministicProvider(answers={"write it": answer}),
        )

    def test_an_unsafe_candidate_is_rejected(self):
        adjudication = self.tournament(UNSAFE).run(
            task="write it", task_class=TaskClass.T4_REVERSIBLE_EFFECT
        )
        self.assertEqual(adjudication.verdict, Verdict.REJECT)
        self.assertIsNone(adjudication.winner)
        self.assertIn("B1-SEC-001-shell-true", " ".join(
            detail for _, detail in adjudication.eliminated
        ))

    def test_a_safe_candidate_survives(self):
        """The control. Without it the verifier could reject everything."""
        adjudication = self.tournament(SAFE).run(
            task="write it", task_class=TaskClass.T4_REVERSIBLE_EFFECT
        )
        self.assertIsNotNone(adjudication.winner)
        self.assertNotEqual(adjudication.verdict, Verdict.REJECT)

    def test_a_clean_pass_does_not_claim_safety(self):
        result = SecurityVerifier().check(Candidate("c1", "model", SAFE), "task")
        self.assertTrue(result.passed)
        self.assertIn("Not a proof of safety", result.detail)

    def test_non_blocking_findings_are_recorded_on_a_pass(self):
        result = SecurityVerifier().check(
            Candidate("c1", "model", 'p = "/tmp/fixed"\n'), "task"
        )
        self.assertTrue(result.passed)
        self.assertIn("B1-SEC-007-tmp-path", result.detail)

    def test_a_scanner_that_cannot_run_is_inconclusive_not_a_pass(self):
        """'We could not look' is not 'it failed' and is not 'it passed'."""
        class Exploding:
            rule_id = "boom"
            severity = "HIGH"
            decidable = True

            def compiled(self):
                raise RuntimeError("bad pattern")

        result = SecurityVerifier(rules=(Exploding(),)).check(
            Candidate("c1", "model", SAFE), "task"
        )
        self.assertFalse(result.conclusive)
        self.assertFalse(result.passed)
        self.assertIn("could not run", result.detail)


if __name__ == "__main__":
    unittest.main()
