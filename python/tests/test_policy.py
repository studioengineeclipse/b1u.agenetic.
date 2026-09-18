"""The standing capability policy, and what it refuses before anyone is asked.

The four rules under test are default-deny, deny-wins, the persistence ceiling,
and UNKNOWN never being admitted. Each is checked both ways -- something the rule
permits and something it refuses -- because a policy that denied everything
would pass every refusal test in this file and be useless.

The integration tests matter more than the unit ones. A policy object that
decides correctly and is never consulted is decoration, so the gate tests here
check that a denied capability cannot be granted, that an envelope built against
another policy is refused, and that changing the policy invalidates
authorizations already granted under the old one.

Stdlib unittest only.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from b1_authority import (  # noqa: E402
    AuthorityEnvelope,
    AuthorityStale,
    CapabilityDenied,
    CommitGate,
    GateError,
    PolicyChanged,
)
from b1_policy import (  # noqa: E402
    Capability,
    CapabilityPolicy,
    PolicyError,
)
from b1_protocol.canonical import digest_value  # noqa: E402
from b1_state import RootJournal  # noqa: E402

STATE = digest_value({"tree": "clean"})
PLAN = digest_value({"phase": "policy"})

NOTES = CapabilityPolicy(
    policy_id="notes-only",
    allow=(
        Capability(action="fs.write", scope=("notes/**",),
                   max_persistence_class="REVERSIBLE"),
    ),
    deny=(
        Capability(action="fs.write", scope=("notes/secrets/**",),
                   reason="key material never gets written by an agent"),
    ),
)


class DefaultIsDeny(unittest.TestCase):
    def test_the_empty_policy_permits_nothing(self):
        empty = CapabilityPolicy.deny_everything()
        for action in ("fs.write", "net.send", "proc.spawn"):
            decision = empty.decide(action, "anything", "REVERSIBLE")
            self.assertFalse(decision.allowed)
            self.assertIn("does not permit it", decision.reason)

    def test_an_unnamed_action_is_denied_and_says_so(self):
        decision = NOTES.decide("net.send", "notes/a.md", "REVERSIBLE")
        self.assertFalse(decision.allowed)
        self.assertIn("no capability in the policy names the action", decision.reason)

    def test_a_named_action_outside_scope_says_which_scopes_exist(self):
        decision = NOTES.decide("fs.write", "elsewhere/a.md", "REVERSIBLE")
        self.assertFalse(decision.allowed)
        self.assertIn("not on 'elsewhere/a.md'", decision.reason)
        self.assertIn("notes/**", decision.reason)

    def test_what_it_permits_it_permits(self):
        decision = NOTES.decide("fs.write", "notes/a.md", "REVERSIBLE")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.matched, "allow:fs.write")


class DenyBeatsAllow(unittest.TestCase):
    def test_a_deny_overrides_an_allow_that_would_have_matched(self):
        # notes/** allows it; notes/secrets/** denies it. The deny wins.
        self.assertTrue(NOTES.decide("fs.write", "notes/a.md", "REVERSIBLE").allowed)
        decision = NOTES.decide("fs.write", "notes/secrets/key", "REVERSIBLE")
        self.assertFalse(decision.allowed)
        self.assertIn("explicitly denies", decision.reason)
        self.assertIn("not outvoted by an allow", decision.reason)

    def test_the_deny_reason_is_carried_into_the_refusal(self):
        decision = NOTES.decide("fs.write", "notes/secrets/key", "REVERSIBLE")
        self.assertIn("key material never gets written by an agent", decision.reason)

    def test_a_deny_ignores_the_ceiling(self):
        """Denying the reversible case must not leave the irreversible one open."""
        for persistence in ("REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE", "UNKNOWN"):
            with self.subTest(persistence=persistence):
                self.assertFalse(
                    NOTES.decide("fs.write", "notes/secrets/key", persistence).allowed
                )


class ThePersistenceCeiling(unittest.TestCase):
    def test_a_reversible_capability_does_not_permit_an_irreversible_effect(self):
        decision = NOTES.decide("fs.write", "notes/a.md", "IRREVERSIBLE")
        self.assertFalse(decision.allowed)
        self.assertIn("exceeds the ceiling", decision.reason)

    def test_a_higher_ceiling_admits_everything_below_it(self):
        wide = CapabilityPolicy(
            policy_id="wide",
            allow=(Capability("fs.write", ("notes/**",), "IRREVERSIBLE"),),
        )
        for persistence in ("REVERSIBLE", "COMPENSATABLE", "IRREVERSIBLE"):
            with self.subTest(persistence=persistence):
                self.assertTrue(wide.decide("fs.write", "notes/a.md", persistence).allowed)

    def test_unknown_is_never_admitted_by_any_ceiling(self):
        """Not even by the widest one. UNKNOWN is not a rank on the scale."""
        wide = CapabilityPolicy(
            policy_id="wide",
            allow=(Capability("fs.write", ("**",), "IRREVERSIBLE"),),
        )
        decision = wide.decide("fs.write", "anything", "UNKNOWN")
        self.assertFalse(decision.allowed)
        self.assertIn("not a rank on the recovery scale", decision.reason)

    def test_unknown_cannot_be_written_as_a_ceiling_either(self):
        with self.assertRaises(PolicyError) as caught:
            Capability("fs.write", ("notes/**",), "UNKNOWN")
        self.assertIn("not accepted as a ceiling", str(caught.exception))


class TheScopeGrammarIsSmallOnPurpose(unittest.TestCase):
    def test_a_glob_that_would_cross_a_slash_is_refused_at_construction(self):
        with self.assertRaises(PolicyError) as caught:
            Capability("fs.write", ("notes/*",))
        self.assertIn("does not interpret it", str(caught.exception))

    def test_a_directory_scope_is_not_a_string_prefix(self):
        policy = CapabilityPolicy(
            policy_id="dir", allow=(Capability("fs.write", ("notes/**",)),)
        )
        self.assertTrue(policy.decide("fs.write", "notes/a.md", "REVERSIBLE").allowed)
        self.assertFalse(policy.decide("fs.write", "notesecret/a.md", "REVERSIBLE").allowed)

    def test_everything_has_to_be_written_out(self):
        with self.assertRaises(PolicyError):
            Capability("fs.write", ())
        self.assertTrue(
            CapabilityPolicy(policy_id="all", allow=(Capability("fs.write", ("**",)),))
            .decide("fs.write", "anywhere/at/all", "REVERSIBLE").allowed
        )


class TheDigestCoversEverything(unittest.TestCase):
    def test_widening_a_scope_changes_the_digest(self):
        narrow = CapabilityPolicy(policy_id="p", allow=(Capability("fs.write", ("notes/**",)),))
        wide = CapabilityPolicy(policy_id="p", allow=(Capability("fs.write", ("**",)),))
        self.assertNotEqual(narrow.digest(), wide.digest())

    def test_raising_a_ceiling_changes_the_digest(self):
        low = CapabilityPolicy(policy_id="p", allow=(Capability("fs.write", ("notes/**",), "REVERSIBLE"),))
        high = CapabilityPolicy(policy_id="p", allow=(Capability("fs.write", ("notes/**",), "IRREVERSIBLE"),))
        self.assertNotEqual(low.digest(), high.digest())

    def test_removing_a_deny_changes_the_digest(self):
        without = CapabilityPolicy(policy_id="notes-only", allow=NOTES.allow)
        self.assertNotEqual(NOTES.digest(), without.digest())

    def test_the_same_policy_digests_the_same(self):
        again = CapabilityPolicy(
            policy_id=NOTES.policy_id, allow=NOTES.allow, deny=NOTES.deny
        )
        self.assertEqual(NOTES.digest(), again.digest())


class PolicyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "root.db"
        self.journal = RootJournal(self.path)
        self.gate = CommitGate(self.journal, NOTES)
        self._event = 0

    def tearDown(self) -> None:
        self.journal.close()
        self._dir.cleanup()

    def event(self) -> str:
        self._event += 1
        return f"evt-{self._event}"

    def authority(self, **overrides) -> AuthorityEnvelope:
        kwargs = dict(
            authority_id="auth-1",
            proposed_action="fs.write",
            target="notes/a.md",
            scope=("notes/**",),
            expected_effect="notes/a.md contains the answer",
            state_digest=STATE,
            plan_digest=PLAN,
            policy_digest=NOTES.digest(),
            causal_objective="record the answer",
            persistence_class="REVERSIBLE",
            granted_at_epoch=0,
        )
        kwargs.update(overrides)
        return AuthorityEnvelope(**kwargs)


class ThePolicyIsConsultedBeforeAuthorityIsGranted(PolicyTestCase):
    def test_a_permitted_capability_is_granted(self):
        digest = self.gate.grant(
            self.authority(), event_id=self.event(), granted_by="user"
        )
        self.assertEqual(len(digest), 64)

    def test_a_denied_action_cannot_be_granted_at_all(self):
        with self.assertRaises(CapabilityDenied) as caught:
            self.gate.grant(
                self.authority(
                    authority_id="auth-net",
                    proposed_action="net.send",
                    target="notes/a.md",
                ),
                event_id=self.event(),
                granted_by="user",
            )
        message = str(caught.exception)
        self.assertIn("no capability in the policy names the action", message)
        self.assertIn("nothing here for a person to approve", message)

    def test_a_target_outside_the_policy_scope_cannot_be_granted(self):
        with self.assertRaises(CapabilityDenied):
            self.gate.grant(
                self.authority(authority_id="auth-out", target="elsewhere/a.md",
                               scope=("elsewhere/**",)),
                event_id=self.event(),
                granted_by="user",
            )

    def test_an_irreversible_effect_above_the_ceiling_cannot_be_granted(self):
        with self.assertRaises(CapabilityDenied) as caught:
            self.gate.grant(
                self.authority(authority_id="auth-irr",
                               persistence_class="IRREVERSIBLE"),
                event_id=self.event(),
                granted_by="user",
            )
        self.assertIn("exceeds the ceiling", str(caught.exception))

    def test_a_refused_grant_leaves_no_authority_and_no_history(self):
        before = self.journal.head()
        with self.assertRaises(CapabilityDenied):
            self.gate.grant(
                self.authority(authority_id="auth-net", proposed_action="net.send"),
                event_id=self.event(),
                granted_by="user",
            )
        self.assertEqual(self.journal.head(), before)
        self.assertTrue(self.journal.verify().ok)

    def test_an_envelope_built_against_another_policy_is_refused(self):
        """Even when the action itself would be permitted.

        The envelope describes a write this policy allows. It still loses,
        because it was reasoned about under rules that are not the ones in force,
        and a gate that accepted it would be honouring a different policy's
        reasoning while reporting its own.
        """
        other = CapabilityPolicy(
            policy_id="other", allow=(Capability("fs.write", ("notes/**",)),)
        )
        self.assertNotEqual(other.digest(), NOTES.digest())
        with self.assertRaises(CapabilityDenied) as caught:
            self.gate.grant(
                self.authority(policy_digest=other.digest()),
                event_id=self.event(),
                granted_by="user",
            )
        self.assertIn("reasoned about under different rules", str(caught.exception))


class ChangingThePolicyIsAnEvent(PolicyTestCase):
    def test_reopening_the_gate_with_a_different_policy_raises(self):
        self.journal.close()
        journal = RootJournal(self.path)
        try:
            wider = CapabilityPolicy(
                policy_id="wider", allow=(Capability("fs.write", ("**",)),)
            )
            with self.assertRaises(PolicyChanged) as caught:
                CommitGate(journal, wider)
            self.assertIn("call adopt_policy()", str(caught.exception))
        finally:
            journal.close()
            self.journal = RootJournal(self.path)

    def test_reopening_with_the_same_policy_is_fine(self):
        self.journal.close()
        self.journal = RootJournal(self.path)
        gate = CommitGate(self.journal, NOTES)
        self.assertEqual(gate.policy_in_force(), NOTES.digest())

    def test_adopting_a_policy_is_journaled_with_both_digests(self):
        wider = CapabilityPolicy(
            policy_id="wider", allow=(Capability("fs.write", ("**",)),)
        )
        before = self.journal.head()[1]
        digest = self.gate.adopt_policy(
            wider, event_id=self.event(), adopted_by="user",
            reason="the user widened it deliberately",
        )
        self.assertEqual(digest, wider.digest())
        self.assertEqual(self.gate.policy_in_force(), wider.digest())
        self.assertEqual(self.journal.head()[1], before + 1)

        record = self.journal.read_all()[-1]
        payload = record.envelope.payload
        self.assertEqual(payload["kind"], "gate.policy_adopted")
        self.assertEqual(payload["previous_policy_digest"], NOTES.digest())
        self.assertEqual(payload["policy_digest"], wider.digest())
        self.assertEqual(payload["reason"], "the user widened it deliberately")
        # Origin U: a standing rule change is never model-derived.
        self.assertEqual(record.envelope.origin, "U")

    def test_adopting_without_a_reason_is_refused(self):
        wider = CapabilityPolicy(policy_id="w", allow=(Capability("fs.write", ("**",)),))
        with self.assertRaises(GateError) as caught:
            self.gate.adopt_policy(wider, event_id=self.event(), adopted_by="user",
                                   reason="")
        self.assertIn("requires a reason", str(caught.exception))

    def test_adopting_the_identical_policy_records_nothing(self):
        before = self.journal.head()
        self.gate.adopt_policy(NOTES, event_id=self.event(), adopted_by="user",
                               reason="no change")
        self.assertEqual(self.journal.head(), before)


class APolicyChangeMakesOutstandingAuthorityStale(PolicyTestCase):
    def test_an_authority_granted_under_the_old_policy_cannot_be_claimed(self):
        """The property that makes tightening a policy mean anything.

        Without it, narrowing what B1 may do would leave every permission
        already issued running under the old, wider rule -- and the moment that
        matters is exactly the moment someone tightens a policy because
        something went wrong.
        """
        digest = self.gate.grant(
            self.authority(), event_id=self.event(), granted_by="user"
        )
        narrower = CapabilityPolicy(
            policy_id="narrower",
            allow=(Capability("fs.write", ("notes/public/**",)),),
        )
        self.gate.adopt_policy(
            narrower, event_id=self.event(), adopted_by="user",
            reason="narrowed after an incident",
        )

        with self.assertRaises(AuthorityStale) as caught:
            self.gate.claim_permit(
                permit_id="permit-1",
                authority_digest=digest,
                effect_identity="fs:notes/a.md",
                action="fs.write",
                target="notes/a.md",
                observed_state_digest=STATE,
                observed_plan_digest=PLAN,
                executor="python-core-1",
                event_id=self.event(),
            )
        message = str(caught.exception)
        self.assertIn("capability policy changed since authorization", message)
        self.assertIn("does not outlive the rule it was granted under", message)

    def test_an_unchanged_policy_does_not_look_stale(self):
        """The control. Without it the test above passes for a gate that always refuses."""
        digest = self.gate.grant(
            self.authority(), event_id=self.event(), granted_by="user"
        )
        permit = self.gate.claim_permit(
            permit_id="permit-1",
            authority_digest=digest,
            effect_identity="fs:notes/a.md",
            action="fs.write",
            target="notes/a.md",
            observed_state_digest=STATE,
            observed_plan_digest=PLAN,
            executor="python-core-1",
            event_id=self.event(),
        )
        self.assertEqual(permit.permit_id, "permit-1")


if __name__ == "__main__":
    unittest.main()
