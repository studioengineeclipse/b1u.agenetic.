"""Root journal: append rules, tamper detection, and replay determinism.

The mutation cases below are the point of this file. A verifier that has never
been shown a broken journal is an untested verifier, so each test damages
history in a specific way and requires that the damage is named. Stdlib
unittest only.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from b1_protocol import Envelope  # noqa: E402
from b1_state import (  # noqa: E402
    GENESIS,
    JournalError,
    RootJournal,
    StaleEpoch,
    projection_digest,
)


def envelope(
    event_id: str,
    *,
    epoch: int = 1,
    origin: str = "M",
    parents: tuple[str, ...] = (),
    effect: str | None = None,
    payload: object | None = None,
) -> Envelope:
    return Envelope.new(
        event_id=event_id,
        origin=origin,
        program_identity="b1-local",
        execution_identity="python-core-1",
        attempt_identity="attempt-1",
        epoch=epoch,
        epistemic_status="WORKING_ASSUMPTION",
        payload=payload if payload is not None else {"kind": "test", "id": event_id},
        causal_parents=parents,
        effect_identity=effect,
        authority_envelope_digest=("a" * 64) if effect else None,
        persistence_class="REVERSIBLE" if effect else None,
    )


class JournalTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "root.db"
        self.journal = RootJournal(self.path)

    def tearDown(self) -> None:
        self.journal.close()
        self._dir.cleanup()

    def raw(self) -> sqlite3.Connection:
        """A second connection, for damaging the journal behind its back."""
        conn = sqlite3.connect(self.path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn


class Appending(JournalTestCase):
    def test_an_empty_journal_is_anchored_at_genesis(self):
        head, count, max_epoch = self.journal.head()
        self.assertEqual(head, GENESIS)
        self.assertEqual(count, 0)
        self.assertEqual(max_epoch, -1)
        self.assertTrue(self.journal.verify())

    def test_appending_chains_each_record_to_its_predecessor(self):
        first = self.journal.append(envelope("evt-1"))
        second = self.journal.append(envelope("evt-2", parents=("evt-1",)))
        self.assertEqual(first.prior_record_digest, GENESIS)
        self.assertEqual(second.prior_record_digest, first.record_digest)
        self.assertEqual(self.journal.head()[0], second.record_digest)
        self.assertEqual(self.journal.head()[1], 2)
        self.assertTrue(self.journal.verify())

    def test_a_duplicate_event_id_is_refused(self):
        self.journal.append(envelope("evt-1"))
        with self.assertRaises(JournalError) as caught:
            self.journal.append(envelope("evt-1"))
        self.assertIn("already in the journal", str(caught.exception))
        self.assertEqual(self.journal.head()[1], 1, "a refused append must change nothing")

    def test_an_unknown_causal_parent_is_refused(self):
        with self.assertRaises(JournalError) as caught:
            self.journal.append(envelope("evt-2", parents=("evt-missing",)))
        self.assertIn("lineage cannot be replayed", str(caught.exception))

    def test_a_refused_append_leaves_the_head_untouched(self):
        self.journal.append(envelope("evt-1"))
        before = self.journal.head()
        with self.assertRaises(JournalError):
            self.journal.append(envelope("evt-2", parents=("nope",)))
        self.assertEqual(self.journal.head(), before)
        self.assertTrue(self.journal.verify())


class Fencing(JournalTestCase):
    def test_a_stale_epoch_is_rejected(self):
        self.journal.append(envelope("evt-1", epoch=5))
        with self.assertRaises(StaleEpoch) as caught:
            self.journal.append(envelope("evt-late", epoch=4))
        self.assertIn("below the committed fence", str(caught.exception))

    def test_the_same_epoch_is_still_accepted(self):
        # Fencing rejects records from a *superseded* fence, not concurrent
        # records at the current one. Rejecting equal epochs would make the
        # journal unable to record two events from one execution.
        self.journal.append(envelope("evt-1", epoch=5))
        self.journal.append(envelope("evt-2", epoch=5))
        self.assertEqual(self.journal.head()[1], 2)

    def test_a_repeated_effect_at_the_same_fence_is_refused(self):
        self.journal.append(envelope("evt-1", epoch=1, effect="fs-write-1"))
        with self.assertRaises(JournalError) as caught:
            self.journal.append(envelope("evt-2", epoch=1, effect="fs-write-1"))
        self.assertIn("must carry a newer fence", str(caught.exception))

    def test_a_retry_of_the_same_effect_under_a_newer_fence_is_allowed(self):
        # This is the retry shape the design requires: same intended effect,
        # new attempt, newer fence. The earlier record is kept, not overwritten,
        # so both attempts remain in history.
        self.journal.append(envelope("evt-1", epoch=1, effect="fs-write-1"))
        self.journal.append(envelope("evt-2", epoch=2, effect="fs-write-1"))
        self.assertEqual(self.journal.head()[1], 2)
        ids = [r.envelope.event_id for r in self.journal.read_all()]
        self.assertEqual(ids, ["evt-1", "evt-2"])


class Determinism(JournalTestCase):
    def test_the_same_event_sequence_produces_the_same_head(self):
        events = tuple(envelope(f"evt-{i}", epoch=i) for i in range(1, 6))
        for event in events:
            self.journal.append(event)
        first_head = self.journal.head()[0]

        with tempfile.TemporaryDirectory() as other_dir:
            other = RootJournal(Path(other_dir) / "root.db")
            try:
                for event in events:
                    other.append(event)
                self.assertEqual(other.head()[0], first_head)
            finally:
                other.close()

    def test_replay_reproduces_the_committed_head(self):
        for i in range(1, 4):
            self.journal.append(envelope(f"evt-{i}", epoch=i))
        self.assertEqual(self.journal.replay_head(), self.journal.head()[0])

    def test_a_head_can_be_predicted_without_a_database(self):
        events = tuple(envelope(f"evt-{i}", epoch=i) for i in range(1, 4))
        predicted = RootJournal.replay_digest(events)
        for event in events:
            self.journal.append(event)
        self.assertEqual(predicted, self.journal.head()[0])

    def test_reordering_two_events_changes_the_head(self):
        a, b = envelope("evt-a"), envelope("evt-b")
        self.assertNotEqual(
            RootJournal.replay_digest((a, b)),
            RootJournal.replay_digest((b, a)),
            "order must be part of history, or a reordered journal would verify",
        )

    def test_a_projection_can_be_destroyed_and_rebuilt_identically(self):
        for i in range(1, 6):
            self.journal.append(envelope(f"evt-{i}", epoch=i))
        before = projection_digest(self.journal.read_all())

        # Close and reopen: everything derived is gone, the journal remains.
        self.journal.close()
        self.journal = RootJournal(self.path)

        self.assertEqual(projection_digest(self.journal.read_all()), before)
        self.assertTrue(self.journal.verify())


class MutationDetection(JournalTestCase):
    """Deliberate damage. Each case must be named by verify(), not merely failed."""

    def populate(self, count: int = 4) -> None:
        for i in range(1, count + 1):
            self.journal.append(envelope(f"evt-{i}", epoch=i))
        self.assertTrue(self.journal.verify())

    def test_an_edited_payload_is_detected(self):
        self.populate()
        conn = self.raw()
        row = conn.execute(
            "SELECT envelope_json FROM root_journal WHERE seq=2"
        ).fetchone()
        data = json.loads(row["envelope_json"])
        data["payload"] = {"kind": "test", "id": "tampered"}
        # Keep the payload_digest consistent so the envelope still validates.
        # A mutation that fails validation proves nothing; this one is designed
        # to be internally coherent and must still be caught by the chain.
        from b1_protocol.canonical import digest_value

        data["payload_digest"] = digest_value(data["payload"])
        conn.execute(
            "UPDATE root_journal SET envelope_json=? WHERE seq=2",
            (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
        )
        conn.close()

        report = self.journal.verify()
        self.assertFalse(report.ok)
        self.assertTrue(
            any("contents were altered" in f for f in report.findings),
            report.findings,
        )

    def test_a_truncated_tail_is_detected_by_the_committed_count(self):
        # A prefix of a valid chain is itself a valid chain, so the chain alone
        # cannot see this. Only the separately committed record count can.
        self.populate()
        conn = self.raw()
        conn.execute("DELETE FROM root_journal WHERE seq=4")
        conn.close()

        report = self.journal.verify()
        self.assertFalse(report.ok)
        self.assertTrue(
            any("removed from the end" in f for f in report.findings), report.findings
        )

    def test_a_record_removed_from_the_middle_is_detected(self):
        self.populate()
        conn = self.raw()
        conn.execute("DELETE FROM root_journal WHERE seq=2")
        conn.close()

        report = self.journal.verify()
        self.assertFalse(report.ok)
        self.assertTrue(
            any("not contiguous" in f for f in report.findings), report.findings
        )
        self.assertTrue(
            any("chain expects" in f for f in report.findings), report.findings
        )

    def test_a_forged_head_is_detected(self):
        self.populate()
        conn = self.raw()
        conn.execute("UPDATE root_head SET head_digest=? WHERE id=1", ("f" * 64,))
        conn.close()

        report = self.journal.verify()
        self.assertFalse(report.ok)
        self.assertTrue(
            any("committed head is" in f for f in report.findings), report.findings
        )

    def test_a_rewritten_chain_link_is_detected(self):
        self.populate()
        conn = self.raw()
        conn.execute(
            "UPDATE root_journal SET prior_record_digest=? WHERE seq=3", ("0" * 64,)
        )
        conn.close()

        report = self.journal.verify()
        self.assertFalse(report.ok)
        self.assertTrue(
            any("chain expects" in f for f in report.findings), report.findings
        )

    def test_an_appended_row_without_a_head_update_is_detected(self):
        self.populate()
        conn = self.raw()
        row = conn.execute(
            "SELECT record_digest FROM root_journal ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        smuggled = envelope("evt-smuggled", epoch=9)
        conn.execute(
            "INSERT INTO root_journal"
            "(event_id,effect_identity,epoch,envelope_json,prior_record_digest,record_digest)"
            " VALUES(?,NULL,?,?,?,?)",
            (
                "evt-smuggled",
                9,
                smuggled.canonical_bytes().decode("utf-8"),
                str(row["record_digest"]),
                "9" * 64,
            ),
        )
        conn.close()

        report = self.journal.verify()
        self.assertFalse(report.ok)
        self.assertTrue(
            any("without updating the head" in f for f in report.findings),
            report.findings,
        )

    def test_an_undamaged_journal_reports_no_findings(self):
        # The control case. Without it, a verify() that always returned findings
        # would pass every test above.
        self.populate()
        report = self.journal.verify()
        self.assertTrue(report.ok)
        self.assertEqual(report.findings, ())
        self.assertEqual(report.record_count, 4)


class SchemaGuard(JournalTestCase):
    def test_an_unknown_schema_version_is_refused_rather_than_migrated(self):
        self.journal.append(envelope("evt-1"))
        self.journal.close()
        conn = self.raw()
        conn.execute("UPDATE root_meta SET value='99' WHERE key='schema_version'")
        conn.close()

        with self.assertRaises(JournalError) as caught:
            RootJournal(self.path)
        self.assertIn("will not guess at a migration", str(caught.exception))
        self.journal = RootJournal.__new__(RootJournal)  # tearDown needs an object
        self.journal.close = lambda: None  # type: ignore[method-assign]


if __name__ == "__main__":
    unittest.main()
