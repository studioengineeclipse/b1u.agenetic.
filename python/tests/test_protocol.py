"""Python peer: canonical form and Event Envelope v1.

Stdlib unittest only. This suite runs with no pip install.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from b1_protocol import Envelope, EnvelopeError  # noqa: E402
from b1_protocol.canonical import (  # noqa: E402
    CanonicalizationError,
    canonical_json_bytes,
    digest_bytes,
)

VECTORS = ROOT / "conformance" / "vectors"


class CanonicalForm(unittest.TestCase):
    def test_keys_are_sorted_not_insertion_ordered(self):
        self.assertEqual(
            canonical_json_bytes({"z": 1, "a": 2, "m": 3}),
            b'{"a":2,"m":3,"z":1}\n',
        )

    def test_non_ascii_is_raw_and_code_point_ordered(self):
        value = {"中": 1, "é": 2, "z": 3, "a": 4, "Ω": 5}
        self.assertEqual(
            canonical_json_bytes(value).decode("utf-8"),
            '{"a":4,"z":3,"é":2,"Ω":5,"中":1}\n',
        )

    def test_there_is_exactly_one_trailing_newline(self):
        data = canonical_json_bytes({"a": 1})
        self.assertTrue(data.endswith(b"}\n"))
        self.assertFalse(data.endswith(b"}\n\n"))

    def test_control_characters_use_the_short_escapes(self):
        self.assertEqual(
            canonical_json_bytes({"k": "a\nbc\td"}),
            b'{"k":"a\\nb\\u0001c\\td"}\n',
        )

    def test_forward_slash_and_del_are_left_alone(self):
        self.assertEqual(
            canonical_json_bytes({"k": "a/bc"}).decode("utf-8"),
            '{"k":"a/bc"}\n',
        )


class RejectedValues(unittest.TestCase):
    """The values that must not have canonical bytes at all.

    Each of these would let two language peers disagree about the same logical
    record, which is the one failure the whole state fabric cannot absorb.
    """

    def test_float_is_refused_and_says_why(self):
        with self.assertRaises(CanonicalizationError) as caught:
            canonical_json_bytes({"ratio": 0.1})
        self.assertIn("Ryu", str(caught.exception))

    def test_nested_float_is_refused_with_its_path(self):
        with self.assertRaises(CanonicalizationError) as caught:
            canonical_json_bytes({"outer": [1, 2, {"inner": 3.5}]})
        self.assertIn("$.outer[2].inner", str(caught.exception))

    def test_null_is_refused(self):
        with self.assertRaises(CanonicalizationError) as caught:
            canonical_json_bytes({"maybe": None})
        self.assertIn("Omit the key", str(caught.exception))

    def test_non_string_key_is_refused(self):
        with self.assertRaises(CanonicalizationError):
            canonical_json_bytes({1: "int-key"})

    def test_empty_key_is_refused(self):
        with self.assertRaises(CanonicalizationError):
            canonical_json_bytes({"": "empty"})

    def test_bool_is_not_mistaken_for_an_int(self):
        # bool subclasses int in Python; the canonical form must still say
        # true/false, not 1/0, or Rust and Python diverge on every flag.
        self.assertEqual(canonical_json_bytes({"k": True}), b'{"k":true}\n')


class EnvelopeContract(unittest.TestCase):
    def basic(self, **overrides) -> Envelope:
        kwargs = dict(
            event_id="evt-1",
            origin="U",
            program_identity="b1-local",
            execution_identity="python-core-1",
            attempt_identity="attempt-1",
            epoch=1,
            epistemic_status="WORKING_ASSUMPTION",
            payload={"kind": "test"},
        )
        kwargs.update(overrides)
        return Envelope.new(**kwargs)

    def test_round_trips_through_its_canonical_form(self):
        envelope = self.basic()
        rebuilt = Envelope.from_dict(envelope.to_canonical_dict())
        self.assertEqual(envelope.digest(), rebuilt.digest())
        self.assertEqual(envelope.canonical_bytes(), rebuilt.canonical_bytes())

    def test_a_float_payload_is_refused_at_construction(self):
        with self.assertRaises(EnvelopeError) as caught:
            self.basic(payload={"ratio": 0.5})
        self.assertIn("not digestable", str(caught.exception))

    def test_an_effect_without_authority_is_refused(self):
        with self.assertRaises(EnvelopeError) as caught:
            self.basic(effect_identity="fs-write-1", persistence_class="REVERSIBLE")
        self.assertIn("unauthorised by construction", str(caught.exception))

    def test_an_effect_without_a_recovery_class_is_refused(self):
        with self.assertRaises(EnvelopeError) as caught:
            self.basic(effect_identity="fs-write-1", authority_envelope_digest="a" * 64)
        self.assertIn("before the effect", str(caught.exception))

    def test_persistence_class_without_an_effect_is_refused(self):
        with self.assertRaises(EnvelopeError) as caught:
            self.basic(persistence_class="REVERSIBLE")
        self.assertIn("meaningless without effect_identity", str(caught.exception))

    def test_a_tampered_payload_digest_is_refused(self):
        envelope = self.basic()
        tampered = envelope.to_canonical_dict()
        tampered["payload_digest"] = "0" * 64
        with self.assertRaises(EnvelopeError) as caught:
            Envelope.from_dict(tampered)
        self.assertIn("does not match payload", str(caught.exception))

    def test_unknown_fields_are_refused_rather_than_ignored(self):
        data = self.basic().to_canonical_dict()
        data["smuggled"] = "x"
        with self.assertRaises(EnvelopeError) as caught:
            Envelope.from_dict(data)
        self.assertIn("unknown envelope fields", str(caught.exception))

    def test_an_unrecognised_schema_version_is_refused(self):
        data = self.basic().to_canonical_dict()
        data["schema_version"] = "b1-event-envelope-2"
        with self.assertRaises(EnvelopeError) as caught:
            Envelope.from_dict(data)
        self.assertIn("refusing to guess", str(caught.exception))

    def test_an_event_cannot_be_its_own_causal_parent(self):
        with self.assertRaises(EnvelopeError) as caught:
            self.basic(causal_parents=("evt-1",))
        self.assertIn("itself as a causal parent", str(caught.exception))

    def test_a_repeated_causal_parent_is_refused(self):
        with self.assertRaises(EnvelopeError):
            self.basic(causal_parents=("evt-0", "evt-0"))

    def test_a_negative_epoch_is_refused(self):
        with self.assertRaises(EnvelopeError) as caught:
            self.basic(epoch=-1)
        self.assertIn("non-negative", str(caught.exception))

    def test_a_bool_epoch_is_refused(self):
        # True would otherwise sail through an isinstance(x, int) check and
        # become epoch 1, which is a silently wrong fence.
        with self.assertRaises(EnvelopeError):
            self.basic(epoch=True)

    def test_origin_does_not_imply_authority(self):
        # A U-origin event carries no authority of its own. Only an effect
        # binding does, and that is checked independently of origin.
        envelope = self.basic(origin="U")
        self.assertIsNone(envelope.authority_envelope_digest)
        self.assertIsNone(envelope.effect_identity)


class CommittedVectors(unittest.TestCase):
    """The committed vectors must still describe what this code produces.

    If these fail after an intentional change to the canonical form, rerun
    tools/build_vectors.py and review the resulting diff. Do not edit a vector
    by hand to make a test pass.
    """

    def vector_names(self) -> list[str]:
        return sorted(p.stem for p in VECTORS.glob("*.canonical"))

    def test_every_committed_vector_reproduces(self):
        names = self.vector_names()
        self.assertGreaterEqual(len(names), 3, "expected at least three canonical vectors")
        for name in names:
            with self.subTest(vector=name):
                source = json.loads((VECTORS / f"{name}.json").read_text(encoding="utf-8"))
                expected = json.loads(
                    (VECTORS / f"{name}.expected.json").read_text(encoding="utf-8")
                )
                committed = (VECTORS / f"{name}.canonical").read_bytes()

                envelope = Envelope.from_dict(source)
                self.assertEqual(envelope.canonical_bytes(), committed)
                self.assertEqual(digest_bytes(committed), expected["canonical_sha256"])
                self.assertEqual(len(committed), expected["canonical_byte_length"])
                self.assertEqual(envelope.payload_digest, expected["payload_digest"])

    def test_the_rejection_vector_still_rejects(self):
        expected = json.loads(
            (VECTORS / "envelope-004-rejected.expected.json").read_text(encoding="utf-8")
        )
        cases = {entry["case"] for entry in expected["must_reject"]}
        self.assertEqual(
            cases, {"float", "nested_float", "null", "non_string_key", "empty_key"}
        )


if __name__ == "__main__":
    unittest.main()
