"""Projection topology: three modes, one truth.

The exit criterion for Phase C is a single sentence — destroy every projection,
rebuild it, and the digests are identical in all three deployment modes — so the
tests are arranged around making that sentence hard to pass by accident. A mode
that stored nothing and reported the same constant would pass a naive version of
it, which is why the drift tests damage real files and require the damage to be
named.

Stdlib unittest only.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from b1_projection import (  # noqa: E402
    VIEW_NAMES,
    OutsideWorkspace,
    ProjectionMissing,
    ProjectionMode,
    ProjectionStore,
    build_views,
    view_digests,
    views_digest,
)
from b1_protocol import Envelope  # noqa: E402
from b1_state import RootJournal  # noqa: E402

AUTHORITY_A = "a" * 64
AUTHORITY_B = "b" * 64


def envelope(
    event_id: str,
    *,
    epoch: int = 1,
    origin: str = "M",
    parents: tuple[str, ...] = (),
    effect: str | None = None,
    authority: str | None = None,
    status: str = "WORKING_ASSUMPTION",
) -> Envelope:
    return Envelope.new(
        event_id=event_id,
        origin=origin,
        program_identity="b1-local",
        execution_identity="python-core-1",
        attempt_identity="attempt-1",
        epoch=epoch,
        epistemic_status=status,
        payload={"kind": "test", "id": event_id},
        causal_parents=parents,
        effect_identity=effect,
        authority_envelope_digest=authority,
        persistence_class="REVERSIBLE" if effect else None,
    )


class ProjectionTestCase(unittest.TestCase):
    """A journal with enough shape that every view has something to say."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.workspace = Path(self._dir.name)
        self.journal = RootJournal(self.workspace / "root.db")
        self.journal.append(envelope("plan-1", origin="U"))
        self.journal.append(envelope("plan-2", parents=("plan-1",)))
        self.journal.append(
            envelope("effect-1", effect="fs:notes/a.md", authority=AUTHORITY_A,
                     parents=("plan-2",))
        )
        # The same effect again, resolved. The effects view must report the
        # later status, not the earlier one.
        self.journal.append(
            envelope("effect-1-reconciled", effect="fs:notes/a.md",
                     authority=AUTHORITY_A, status="VERIFIED", epoch=2)
        )
        self.journal.append(
            envelope("effect-2", effect="fs:notes/b.md", authority=AUTHORITY_B, epoch=2)
        )

    def tearDown(self) -> None:
        self.journal.close()
        self._dir.cleanup()

    def store(self, mode: ProjectionMode) -> ProjectionStore:
        return ProjectionStore(self.workspace, mode)


class ViewsAreAPureFunctionOfHistory(ProjectionTestCase):
    def test_building_twice_gives_identical_digests(self):
        first = views_digest(build_views(self.journal.read_all()))
        second = views_digest(build_views(self.journal.read_all()))
        self.assertEqual(first, second)

    def test_every_declared_view_is_built(self):
        views = build_views(self.journal.read_all())
        self.assertEqual(sorted(views), sorted(VIEW_NAMES))

    def test_an_effect_reports_its_latest_status_not_its_first(self):
        views = build_views(self.journal.read_all())
        effects = {row["effect_identity"]: row for row in views["effects"]}
        row = effects["fs:notes/a.md"]
        self.assertEqual(row["record_count"], 2)
        self.assertEqual(row["epistemic_status"], "VERIFIED")
        self.assertEqual(row["first_seq"], 3)
        self.assertEqual(row["last_seq"], 4)

    def test_an_authority_lists_what_was_done_under_it(self):
        views = build_views(self.journal.read_all())
        authorities = {
            row["authority_envelope_digest"]: row for row in views["authority"]
        }
        self.assertEqual(
            authorities[AUTHORITY_A]["effect_identities"], ["fs:notes/a.md"]
        )
        self.assertEqual(authorities[AUTHORITY_A]["event_count"], 2)
        self.assertEqual(
            authorities[AUTHORITY_B]["effect_identities"], ["fs:notes/b.md"]
        )

    def test_heads_carries_both_heads_and_says_they_agree(self):
        views = build_views(self.journal.read_all())
        heads = views["heads"]
        self.assertEqual(heads["record_count"], 5)
        self.assertEqual(heads["max_epoch"], 2)
        self.assertEqual(heads["chain_head"], heads["replayed_head"])
        self.assertTrue(heads["consistent"])

    def test_an_empty_journal_projects_to_an_empty_projection(self):
        with tempfile.TemporaryDirectory() as temp:
            empty = RootJournal(Path(temp) / "empty.db")
            try:
                views = build_views(empty.read_all())
                self.assertEqual(views["events"], [])
                self.assertEqual(views["heads"]["record_count"], 0)
                self.assertEqual(views["heads"]["chain_head"], "GENESIS")
            finally:
                empty.close()

    def test_no_view_row_contains_a_null(self):
        """The canonical form rejects null, so an absent field must be omitted.

        Asserted on the raw JSON text rather than the objects, because that is
        where a null would actually appear, and because `canonical_json_bytes`
        raising is the behaviour under test — a view that emitted None would
        make the whole projection undigestable.
        """
        views = build_views(self.journal.read_all())
        for name in VIEW_NAMES:
            self.assertNotIn("null", json.dumps(views[name]), f"{name} emitted a null")


class TheModeIsAStorageDecision(ProjectionTestCase):
    """The exit criterion, stated three ways."""

    def test_all_three_modes_agree_on_the_digest(self):
        digests = {}
        for mode in ProjectionMode:
            store = self.store(mode)
            digests[mode] = store.rebuild(self.journal).views_digest
        self.assertEqual(len(set(digests.values())), 1, digests)

    def test_destroy_and_rebuild_is_identical_in_every_mode(self):
        for mode in ProjectionMode:
            with self.subTest(mode=mode):
                store = self.store(mode)
                before = store.rebuild(self.journal)
                store.destroy()
                for path in store.paths():
                    self.assertFalse(path.exists(), f"{path} survived destroy()")
                after = store.rebuild(self.journal)
                self.assertEqual(before.views_digest, after.views_digest)
                self.assertEqual(before.view_digests, after.view_digests)

    def test_what_each_mode_reads_back_is_what_the_journal_says(self):
        expected = view_digests(build_views(self.journal.read_all()))
        for mode in ProjectionMode:
            with self.subTest(mode=mode):
                store = self.store(mode)
                store.rebuild(self.journal)
                self.assertEqual(view_digests(store.read(self.journal)), expected)

    def test_rebuilding_does_not_touch_the_journal(self):
        """A projection rebuild reads history and writes nothing to it.

        Compared on the journal's own verification report rather than on file
        bytes: SQLite in WAL mode legitimately changes its files on read, so a
        byte comparison would fail for a reason that has nothing to do with
        history changing.
        """
        before = self.journal.verify()
        for mode in ProjectionMode:
            store = self.store(mode)
            store.rebuild(self.journal)
            store.read(self.journal)
        after = self.journal.verify()
        self.assertTrue(after.ok)
        self.assertEqual(before.head_digest, after.head_digest)
        self.assertEqual(before.record_count, after.record_count)

    def test_audit_replay_stores_nothing_at_all(self):
        store = self.store(ProjectionMode.AUDIT_REPLAY)
        report = store.rebuild(self.journal)
        self.assertEqual(report.files_written, ())
        self.assertEqual(store.paths(), ())
        self.assertFalse((self.workspace / "projection").exists())

    def test_modular_writes_one_file_per_view_plus_a_manifest(self):
        store = self.store(ProjectionMode.MODULAR)
        report = store.rebuild(self.journal)
        self.assertEqual(
            report.files_written,
            tuple(sorted([f"{name}.b1p" for name in VIEW_NAMES] + ["manifest.b1p"])),
        )

    def test_compact_writes_exactly_one_file(self):
        store = self.store(ProjectionMode.COMPACT)
        self.assertEqual(store.rebuild(self.journal).files_written, ("projection.b1p",))


class AProjectionLosesToTheJournal(ProjectionTestCase):
    def test_an_edited_compact_projection_is_caught_and_named(self):
        store = self.store(ProjectionMode.COMPACT)
        store.rebuild(self.journal)
        path = store.root / "projection.b1p"
        stored = json.loads(path.read_text(encoding="utf-8"))
        # Assert the mutation is a mutation. The first draft of this test set
        # origin to the value it already had, so it damaged nothing and the
        # check correctly reported no drift -- a test that passed for the wrong
        # reason would have been the more expensive outcome.
        self.assertEqual(stored["views"]["events"][0]["origin"], "U")
        stored["views"]["events"][0]["origin"] = "M"
        path.write_text(json.dumps(stored), encoding="utf-8")

        report = store.check(self.journal)
        self.assertFalse(report.ok)
        self.assertEqual(report.drifted_views, ("events",))
        self.assertIn("The projection loses", report.findings[0])

    def test_an_internally_consistent_edit_is_still_caught(self):
        """The file's own digest is updated too, and it changes nothing.

        This is the journal's `payload_digest` lesson applied to derived state:
        a record that agrees with itself is not thereby a true record, so the
        check compares against the journal and never against the file's claim.
        """
        store = self.store(ProjectionMode.COMPACT)
        store.rebuild(self.journal)
        path = store.root / "projection.b1p"
        stored = json.loads(path.read_text(encoding="utf-8"))

        # Understate the record count. Chosen because it is unambiguously a lie
        # about history rather than a plausible alternative reading of it, and
        # because a projection that undercounts is how a truncated tail would
        # present itself to anything reading the projection instead of the
        # journal.
        original = stored["views"]["heads"]["record_count"]
        stored["views"]["heads"]["record_count"] = original - 1
        # Re-derive the file's self-declared digests so it is internally clean.
        stored["view_digests"] = view_digests(stored["views"])
        stored["views_digest"] = views_digest(stored["views"])
        path.write_text(json.dumps(stored), encoding="utf-8")

        report = store.check(self.journal)
        self.assertFalse(report.ok)
        self.assertEqual(report.drifted_views, ("heads",))

    def test_an_edited_modular_view_names_only_that_view(self):
        store = self.store(ProjectionMode.MODULAR)
        store.rebuild(self.journal)
        path = store.root / "authority.b1p"
        rows = json.loads(path.read_text(encoding="utf-8"))
        rows.append({"authority_envelope_digest": "c" * 64, "first_seq": 99,
                     "event_count": 1, "effect_identities": []})
        path.write_text(json.dumps(rows), encoding="utf-8")

        report = store.check(self.journal)
        self.assertFalse(report.ok)
        self.assertEqual(report.drifted_views, ("authority",))

    def test_a_deleted_modular_view_is_a_missing_projection_not_a_smaller_one(self):
        store = self.store(ProjectionMode.MODULAR)
        store.rebuild(self.journal)
        (store.root / "effects.b1p").unlink()

        report = store.check(self.journal)
        self.assertFalse(report.ok)
        self.assertEqual(report.drifted_views, VIEW_NAMES)
        with self.assertRaises(ProjectionMissing):
            store.read(self.journal)

    def test_a_projection_that_never_existed_is_not_an_empty_one(self):
        store = self.store(ProjectionMode.COMPACT)
        with self.assertRaises(ProjectionMissing):
            store.read(self.journal)

    def test_a_stale_projection_is_caught_after_history_moves(self):
        store = self.store(ProjectionMode.MODULAR)
        store.rebuild(self.journal)
        self.assertTrue(store.check(self.journal).ok)

        self.journal.append(envelope("plan-3", epoch=2))
        report = store.check(self.journal)
        self.assertFalse(report.ok)
        # events and heads both move; effects and authority do not.
        self.assertEqual(report.drifted_views, ("events", "heads"))

        store.rebuild(self.journal)
        self.assertTrue(store.check(self.journal).ok)

    def test_audit_replay_reports_that_drift_is_impossible_not_absent(self):
        store = self.store(ProjectionMode.AUDIT_REPLAY)
        store.rebuild(self.journal)
        report = store.check(self.journal)
        self.assertTrue(report.ok)
        self.assertFalse(report.checkable)
        self.assertIn("not possible rather than not found", report.findings[0])


class TheProjectionRootStaysInsideTheWorkspace(ProjectionTestCase):
    def test_a_traversal_escape_is_refused_before_anything_is_written(self):
        with self.assertRaises(OutsideWorkspace):
            ProjectionStore(self.workspace, ProjectionMode.COMPACT,
                            subdir="../escaped")

    def test_a_symlinked_subdir_pointing_out_is_refused(self):
        with tempfile.TemporaryDirectory() as outside:
            link = self.workspace / "linked"
            link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OutsideWorkspace):
                ProjectionStore(self.workspace, ProjectionMode.MODULAR,
                                subdir="linked")


class DamageInTheJournalIsNotLaundered(ProjectionTestCase):
    def test_a_broken_chain_shows_up_in_the_heads_view(self):
        """A projection over a damaged journal reports the damage.

        Built by hand from records whose chain does not close, because the
        journal itself refuses to write one. The point is that `heads` compares
        a replay against what the last record claims, so a projection cannot
        present a forged head as a clean summary.
        """
        records = list(self.journal.read_all())
        last = records[-1]
        records[-1] = type(last)(
            seq=last.seq,
            envelope=last.envelope,
            prior_record_digest=last.prior_record_digest,
            record_digest="f" * 64,
        )
        views = build_views(tuple(records))
        self.assertFalse(views["heads"]["consistent"])
        self.assertEqual(views["heads"]["chain_head"], "f" * 64)
        self.assertNotEqual(
            views["heads"]["replayed_head"], views["heads"]["chain_head"]
        )


if __name__ == "__main__":
    unittest.main()
