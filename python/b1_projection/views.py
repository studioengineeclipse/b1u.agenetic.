"""Derived views over the root journal, and the digests that compare them.

A projection is a rearrangement of history for reading. It contains nothing the
journal does not already contain, which is exactly why it can be thrown away:
if a projection and a replay disagree, the projection loses, always, without a
conversation about which is more current.

Everything here is a pure function of the record sequence. That is not a style
preference — it is what makes the three deployment modes in `store.py` provably
equivalent. If building a view could consult the clock, the filesystem, or which
mode was in use, then "compact and modular agree" would be a coincidence to be
re-tested rather than a property of the code.

Ordering is by `seq`, the journal's total order, and never by time. No envelope
carries a wall-clock field at all; that is what makes a replay reproduce the
same bytes on a different machine on a different day.

Four views
----------
    events      the ordered index: one row per record
    effects     one row per effect identity, folded in journal order
    authority   one row per authority envelope digest
    heads       the singleton: counts, epoch ceiling, and both heads

`heads` carries `chain_head` and `replayed_head` separately on purpose. The
first is what the last record claims; the second is recomputed from the
envelopes. A projection built over a damaged journal must not launder the
damage into a clean-looking summary, so when those two disagree the projection
says so in its own content rather than quietly picking one.
"""
from __future__ import annotations

from b1_protocol.canonical import canonical_json_bytes, digest_bytes
from b1_state import GENESIS, JournalRecord, chain_digest

__all__ = [
    "VIEW_NAMES",
    "Views",
    "build_views",
    "views_digest",
    "view_digests",
    "events_view",
    "effects_view",
    "authority_view",
    "heads_view",
]

# Sorted, and relied upon to stay sorted: the modular mode writes one file per
# name and the compact mode writes one object keyed by them, so a name appearing
# in one and not the other is a divergence the digest would catch but the
# message would not explain.
VIEW_NAMES = ("authority", "effects", "events", "heads")

# A view is a list of rows or a single object; a projection is the four of them.
Views = dict[str, object]


def events_view(records: tuple[JournalRecord, ...]) -> list[dict[str, object]]:
    """One row per record, in journal order.

    Optional envelope fields are *omitted* when absent rather than emitted as
    null, because the canonical form rejects null: after canonicalisation an
    absent key and an explicit null are indistinguishable in several of the
    fourteen target languages. That constraint reaches all the way out here.
    """
    rows: list[dict[str, object]] = []
    for record in records:
        envelope = record.envelope
        row: dict[str, object] = {
            "seq": record.seq,
            "event_id": envelope.event_id,
            "origin": envelope.origin,
            "epoch": envelope.epoch,
            "epistemic_status": envelope.epistemic_status,
            "record_digest": record.record_digest,
        }
        if envelope.causal_parents:
            row["causal_parents"] = list(envelope.causal_parents)
        if envelope.effect_identity is not None:
            row["effect_identity"] = envelope.effect_identity
        if envelope.authority_envelope_digest is not None:
            row["authority_envelope_digest"] = envelope.authority_envelope_digest
        if envelope.persistence_class is not None:
            row["persistence_class"] = envelope.persistence_class
        rows.append(row)
    return rows


def effects_view(records: tuple[JournalRecord, ...]) -> list[dict[str, object]]:
    """One row per effect identity, folded in journal order.

    `epistemic_status` is the *last* status recorded for that effect, not the
    first and not the strongest. An effect can land IN_DOUBT and later be
    reconciled to VERIFIED once real state has been read back, and a view that
    kept the first status would report a resolved doubt as still open. Taking
    the strongest would be worse: it would let a later IN_DOUBT be outranked by
    an earlier VERIFIED, which is the receipt-outliving-the-evidence failure.

    Rows are emitted in first-seen order rather than sorted, so the view reads
    as history. The digest does not care, but a person does.
    """
    folded: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for record in records:
        envelope = record.envelope
        identity = envelope.effect_identity
        if identity is None:
            continue
        if identity not in folded:
            order.append(identity)
            folded[identity] = {
                "effect_identity": identity,
                "first_seq": record.seq,
                "last_seq": record.seq,
                "record_count": 0,
                "authority_envelope_digests": [],
            }
        row = folded[identity]
        row["last_seq"] = record.seq
        row["record_count"] = int(row["record_count"]) + 1
        row["epistemic_status"] = envelope.epistemic_status
        if envelope.persistence_class is not None:
            row["persistence_class"] = envelope.persistence_class
        if envelope.authority_envelope_digest is not None:
            digests = row["authority_envelope_digests"]
            assert isinstance(digests, list)
            if envelope.authority_envelope_digest not in digests:
                digests.append(envelope.authority_envelope_digest)

    rows = []
    for identity in order:
        row = folded[identity]
        # Sorted, because "which authorities touched this effect" is a set
        # question and the answer must not depend on arrival order.
        digests = row["authority_envelope_digests"]
        assert isinstance(digests, list)
        row["authority_envelope_digests"] = sorted(digests)
        rows.append(row)
    return rows


def authority_view(records: tuple[JournalRecord, ...]) -> list[dict[str, object]]:
    """One row per authority envelope digest, in first-seen order.

    This is the view that answers "what was actually done under this
    authorization", which is the question an audit asks and the one a grant-time
    record cannot answer on its own.
    """
    folded: dict[str, dict[str, object]] = {}
    order: list[str] = []
    for record in records:
        digest = record.envelope.authority_envelope_digest
        if digest is None:
            continue
        if digest not in folded:
            order.append(digest)
            folded[digest] = {
                "authority_envelope_digest": digest,
                "first_seq": record.seq,
                "event_count": 0,
                "effect_identities": [],
            }
        row = folded[digest]
        row["event_count"] = int(row["event_count"]) + 1
        identity = record.envelope.effect_identity
        if identity is not None:
            identities = row["effect_identities"]
            assert isinstance(identities, list)
            if identity not in identities:
                identities.append(identity)

    rows = []
    for digest in order:
        row = folded[digest]
        identities = row["effect_identities"]
        assert isinstance(identities, list)
        row["effect_identities"] = sorted(identities)
        rows.append(row)
    return rows


def heads_view(records: tuple[JournalRecord, ...]) -> dict[str, object]:
    """Counts, the epoch ceiling, and both heads.

    `chain_head` is the last record's stored digest. `replayed_head` is
    recomputed from the envelopes alone. On an intact journal they are equal;
    on a damaged one they are not, and `consistent` is how the projection
    reports that without deciding which to believe. Deciding is the journal's
    own `verify()`, which has the committed count as well.
    """
    prior = GENESIS
    for record in records:
        prior = chain_digest(prior, record.envelope)
    chain_head = records[-1].record_digest if records else GENESIS
    return {
        "record_count": len(records),
        "max_epoch": max((r.envelope.epoch for r in records), default=0),
        "chain_head": chain_head,
        "replayed_head": prior,
        "consistent": chain_head == prior,
    }


def build_views(records: tuple[JournalRecord, ...]) -> Views:
    """The whole projection, as a pure function of the record sequence."""
    return {
        "authority": authority_view(records),
        "effects": effects_view(records),
        "events": events_view(records),
        "heads": heads_view(records),
    }


def view_digests(views: Views) -> dict[str, str]:
    """A digest per view, so drift can be named rather than merely detected.

    "The projection disagrees" sends someone to read four views. "The effects
    view disagrees" sends them to one.
    """
    return {
        name: digest_bytes(canonical_json_bytes(views[name])) for name in sorted(views)
    }


def views_digest(views: Views) -> str:
    """One digest over the whole projection.

    This is the value the three deployment modes must agree on. It is computed
    over the views themselves and knows nothing about how or where they were
    stored, which is what makes "the mode is a storage decision" checkable
    rather than merely asserted.

    Distinct from `b1_state.projection_digest`, deliberately. That one digests
    the record index and answers "do two language peers see the same history".
    This one digests the derived views and answers "does this deployment mode
    tell the same truth". Folding them into one function would make a change to
    either question silently alter the answer to the other.
    """
    return digest_bytes(canonical_json_bytes(views))
