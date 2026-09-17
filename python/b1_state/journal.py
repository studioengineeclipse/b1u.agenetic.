"""The B1 root journal: one authoritative history, on SQLite/WAL.

Design-derived-from: b1mu-omega13.9:src/b1mu/durable.py

What is taken from upstream, because it is already proven to work:

* SQLite in WAL mode as the durability boundary.
* An append-only table whose every row is chained to its predecessor by
  ``record_digest = SHA256(prior_record_digest || canonical_bytes(record))``,
  anchored at the literal string ``GENESIS``.
* A committed head digest *and* record count stored separately from the chain.
  This is the part that is easy to miss: a chain alone detects a mutated row but
  not a truncated tail, because a prefix of a valid chain is itself a valid
  chain. The committed count is what makes trailing truncation visible.
* Wall-clock values excluded from digested bytes, so replaying the same events
  twice produces the same head.

What deliberately differs:

* Upstream chains per ``(run_id, attempt, seq)``. B1 Local needs one
  authoritative history across every subsystem, so there is a single monotonic
  ``seq`` and a single chain. Per-run views are a projection over that, not a
  separate truth. See docs/decisions/ADR-0001-root-journal.md.

Who may write
-------------
Rust and Python are equal computational peers: both may analyse, derive, plan
and propose. Neither writes to the journal directly. Every append takes a
``BEGIN IMMEDIATE`` write lease, which SQLite grants to exactly one connection
at a time, so concurrent proposals linearise instead of interleaving. Equality
is about computation; it was never about uncoordinated writes.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from pathlib import Path

from b1_protocol.canonical import canonical_json_bytes, digest_bytes
from b1_protocol.envelope import Envelope, EnvelopeError

__all__ = [
    "GENESIS",
    "SCHEMA_VERSION",
    "JournalError",
    "TamperDetected",
    "StaleEpoch",
    "JournalRecord",
    "VerificationReport",
    "RootJournal",
]

GENESIS = "GENESIS"
SCHEMA_VERSION = "1"


class JournalError(Exception):
    """The journal refused an operation."""


class TamperDetected(JournalError):
    """Stored history does not match its own chain or head commitment."""


class StaleEpoch(JournalError):
    """A record arrived carrying a fence below the one already committed."""


@dataclass(frozen=True, slots=True)
class JournalRecord:
    seq: int
    envelope: Envelope
    prior_record_digest: str
    record_digest: str


@dataclass(frozen=True, slots=True)
class VerificationReport:
    ok: bool
    record_count: int
    head_digest: str
    findings: tuple[str, ...]

    def __bool__(self) -> bool:
        return self.ok


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS root_meta(
           key TEXT PRIMARY KEY,
           value TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS root_journal(
           seq INTEGER PRIMARY KEY AUTOINCREMENT,
           event_id TEXT NOT NULL UNIQUE,
           effect_identity TEXT,
           epoch INTEGER NOT NULL,
           envelope_json TEXT NOT NULL,
           prior_record_digest TEXT NOT NULL,
           record_digest TEXT NOT NULL UNIQUE)""",
    # The head is committed separately from the chain on purpose. A chain
    # verifies itself; only an independent commitment reveals that rows have
    # been removed from the end.
    """CREATE TABLE IF NOT EXISTS root_head(
           id INTEGER PRIMARY KEY CHECK(id = 1),
           head_digest TEXT NOT NULL,
           record_count INTEGER NOT NULL,
           max_epoch INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS root_journal_effect ON root_journal(effect_identity)",
)


def chain_digest(prior: str, envelope: Envelope) -> str:
    """The chained digest of one record.

    Binding the prior digest in is what makes the chain a chain: changing any
    earlier record changes every later digest, so a single-row edit cannot be
    made to look consistent without rewriting everything after it.
    """
    return digest_bytes(prior.encode("utf-8") + envelope.canonical_bytes())


class RootJournal:
    """Append-only authoritative history for one B1 Local instance."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._initialise()

    def __enter__(self) -> "RootJournal":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._conn.close()

    def _initialise(self) -> None:
        with self._lease():
            for statement in _SCHEMA:
                self._conn.execute(statement)
            row = self._conn.execute(
                "SELECT value FROM root_meta WHERE key='schema_version'"
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO root_meta(key,value) VALUES('schema_version',?)",
                    (SCHEMA_VERSION,),
                )
            elif str(row["value"]) != SCHEMA_VERSION:
                raise JournalError(
                    f"unsupported root journal schema {row['value']!r}; "
                    f"this build understands {SCHEMA_VERSION!r} and will not guess at a migration"
                )
            if self._conn.execute("SELECT 1 FROM root_head WHERE id=1").fetchone() is None:
                self._conn.execute(
                    "INSERT INTO root_head(id,head_digest,record_count,max_epoch) "
                    "VALUES(1,?,0,-1)",
                    (GENESIS,),
                )

    class _Lease:
        """The single-writer lease. See the module docstring."""

        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def __enter__(self) -> sqlite3.Connection:
            self._conn.execute("BEGIN IMMEDIATE")
            return self._conn

        def __exit__(self, exc_type, exc, tb) -> None:
            if exc_type is None:
                self._conn.execute("COMMIT")
            else:
                self._conn.execute("ROLLBACK")

    def _lease(self) -> "RootJournal._Lease":
        return RootJournal._Lease(self._conn)

    def lease(self) -> "RootJournal._Lease":
        """Acquire the single write lease.

        Public because a component that needs several journal appends and its
        own table writes to land as one atomic unit -- the commit gate is the
        one that does -- must hold *this* lease rather than opening a second
        transaction. Two leases would be two linearization points, which is the
        split-brain the single-writer design exists to prevent.

        Inside a lease, use :meth:`append_in_lease` rather than :meth:`append`.
        """
        return self._lease()

    @property
    def connection(self) -> sqlite3.Connection:
        """The underlying connection, shared deliberately.

        A component holding its own tables in this database shares the
        connection so that its writes and the journal's commit or roll back
        together. Anything writing here must hold the lease.
        """
        return self._conn

    def head(self) -> tuple[str, int, int]:
        """Committed ``(head_digest, record_count, max_epoch)``."""
        row = self._conn.execute(
            "SELECT head_digest,record_count,max_epoch FROM root_head WHERE id=1"
        ).fetchone()
        return str(row["head_digest"]), int(row["record_count"]), int(row["max_epoch"])

    def append(self, envelope: Envelope) -> JournalRecord:
        """Append one envelope, or refuse and change nothing.

        Refusals, and why each exists:

        * invalid envelope -- a record that cannot be validated cannot be
          replayed later either;
        * duplicate ``event_id`` -- history would have two rows claiming one
          identity;
        * unknown causal parent -- a record whose parents are not in the journal
          cannot have its lineage reconstructed;
        * stale ``epoch`` -- a late attempt from a superseded fence must not
          land after the fence moved on;
        * duplicate ``effect_identity`` at or below the committed epoch -- this
          is the duplicate-effect guard, and it is the reason effect_identity is
          a first-class column rather than a field buried in the payload.
        """
        with self._lease():
            return self.append_in_lease(envelope)

    def append_in_lease(self, envelope: Envelope) -> JournalRecord:
        """Append while the caller already holds the lease.

        Same validation as :meth:`append`; the only difference is who owns the
        transaction. Split out so a caller combining journal appends with its
        own table writes gets one atomic unit and one linearization point,
        rather than being tempted to write rows directly and skip these checks.
        """
        try:
            envelope.validate()
        except EnvelopeError as exc:
            raise JournalError(f"refusing to append an invalid envelope: {exc}") from exc

        conn = self._conn
        head_digest, record_count, max_epoch = self.head()

        if conn.execute(
            "SELECT 1 FROM root_journal WHERE event_id=?", (envelope.event_id,)
        ).fetchone():
            raise JournalError(
                f"event_id {envelope.event_id!r} is already in the journal; "
                f"history cannot hold two records with one identity"
            )

        for parent in envelope.causal_parents:
            if not conn.execute(
                "SELECT 1 FROM root_journal WHERE event_id=?", (parent,)
            ).fetchone():
                raise JournalError(
                    f"causal parent {parent!r} is not in the journal; "
                    f"appending would create a record whose lineage cannot be replayed"
                )

        if envelope.epoch < max_epoch:
            raise StaleEpoch(
                f"event {envelope.event_id!r} carries epoch {envelope.epoch}, "
                f"below the committed fence {max_epoch}; a superseded attempt "
                f"must not land after the fence moved on"
            )

        if envelope.effect_identity is not None:
            clash = conn.execute(
                "SELECT event_id,epoch FROM root_journal WHERE effect_identity=? "
                "ORDER BY seq DESC LIMIT 1",
                (envelope.effect_identity,),
            ).fetchone()
            if clash is not None and int(clash["epoch"]) >= envelope.epoch:
                raise JournalError(
                    f"effect {envelope.effect_identity!r} was already recorded by "
                    f"{clash['event_id']!r} at epoch {clash['epoch']}; a retry must "
                    f"carry a newer fence, not repeat the same one"
                )

        record_digest = chain_digest(head_digest, envelope)
        conn.execute(
            "INSERT INTO root_journal"
            "(event_id,effect_identity,epoch,envelope_json,prior_record_digest,record_digest)"
            " VALUES(?,?,?,?,?,?)",
            (
                envelope.event_id,
                envelope.effect_identity,
                envelope.epoch,
                envelope.canonical_bytes().decode("utf-8"),
                head_digest,
                record_digest,
            ),
        )
        seq = int(conn.execute("SELECT last_insert_rowid() AS s").fetchone()["s"])
        conn.execute(
            "UPDATE root_head SET head_digest=?,record_count=?,max_epoch=? WHERE id=1",
            (record_digest, record_count + 1, max(max_epoch, envelope.epoch)),
        )

        return JournalRecord(
            seq=seq,
            envelope=envelope,
            prior_record_digest=head_digest,
            record_digest=record_digest,
        )

    def read_all(self) -> tuple[JournalRecord, ...]:
        rows = self._conn.execute(
            "SELECT seq,envelope_json,prior_record_digest,record_digest "
            "FROM root_journal ORDER BY seq"
        ).fetchall()
        out: list[JournalRecord] = []
        for row in rows:
            import json

            envelope = Envelope.from_dict(json.loads(row["envelope_json"]))
            out.append(
                JournalRecord(
                    seq=int(row["seq"]),
                    envelope=envelope,
                    prior_record_digest=str(row["prior_record_digest"]),
                    record_digest=str(row["record_digest"]),
                )
            )
        return tuple(out)

    def verify(self) -> VerificationReport:
        """Re-derive the whole chain and compare it against what is committed.

        Every finding is collected rather than raising on the first, because
        when history is damaged the useful question is how much of it is
        damaged, not merely that some of it is.
        """
        findings: list[str] = []
        committed_head, committed_count, _ = self.head()

        rows = self._conn.execute(
            "SELECT seq,envelope_json,prior_record_digest,record_digest "
            "FROM root_journal ORDER BY seq"
        ).fetchall()

        prior = GENESIS
        expected_seq = 1
        for row in rows:
            seq = int(row["seq"])
            if seq != expected_seq:
                findings.append(
                    f"seq {seq}: sequence is not contiguous, expected {expected_seq}; "
                    f"a record has been removed from the middle"
                )
                expected_seq = seq
            expected_seq += 1

            if str(row["prior_record_digest"]) != prior:
                findings.append(
                    f"seq {seq}: prior digest is {row['prior_record_digest']}, "
                    f"chain expects {prior}"
                )

            try:
                import json

                envelope = Envelope.from_dict(json.loads(row["envelope_json"]))
            except (EnvelopeError, ValueError) as exc:
                findings.append(f"seq {seq}: stored envelope no longer validates: {exc}")
                prior = str(row["record_digest"])
                continue

            # Re-canonicalising rather than hashing the stored text is what
            # catches a row whose JSON was edited into a different but still
            # parseable shape.
            recomputed = chain_digest(str(row["prior_record_digest"]), envelope)
            if recomputed != str(row["record_digest"]):
                findings.append(
                    f"seq {seq}: record digest is {row['record_digest']}, recomputes to "
                    f"{recomputed}; this record's contents were altered"
                )
            prior = str(row["record_digest"])

        if len(rows) != committed_count:
            findings.append(
                f"journal holds {len(rows)} records but the committed head claims "
                f"{committed_count}; "
                + (
                    "records were removed from the end"
                    if len(rows) < committed_count
                    else "records were added without updating the head"
                )
            )
        if prior != committed_head:
            findings.append(
                f"recomputed head is {prior}, committed head is {committed_head}"
            )

        return VerificationReport(
            ok=not findings,
            record_count=len(rows),
            head_digest=prior,
            findings=tuple(findings),
        )

    def replay_head(self) -> str:
        """Recompute the head from the stored envelopes alone.

        Two journals built from the same event sequence must land here on the
        same string. That is what makes a projection rebuildable: it can be
        thrown away and reconstructed, and the result is checkable.
        """
        prior = GENESIS
        for record in self.read_all():
            prior = chain_digest(prior, record.envelope)
        return prior

    @staticmethod
    def replay_digest(envelopes: tuple[Envelope, ...]) -> str:
        """The head a journal would have after appending exactly ``envelopes``.

        Pure, needing no database, so a peer can predict a head before writing
        anything, and two peers can compare heads without sharing storage.
        """
        prior = GENESIS
        for envelope in envelopes:
            prior = chain_digest(prior, envelope)
        return prior


def projection_digest(records: tuple[JournalRecord, ...]) -> str:
    """A digest over a derived view of the journal.

    Projections are rebuildable and therefore disposable: if a projection and a
    replay disagree, the projection loses. This function is how that comparison
    is made, and it is what the "destroy every projection and rebuild" test
    checks against.
    """
    summary = [
        {
            "seq": record.seq,
            "event_id": record.envelope.event_id,
            "origin": record.envelope.origin,
            "epoch": record.envelope.epoch,
            "record_digest": record.record_digest,
        }
        for record in records
    ]
    return digest_bytes(canonical_json_bytes(summary))
