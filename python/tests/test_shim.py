"""The Responses translation: what it preserves, and what it refuses to guess.

The conformance vectors in `conformance/responses/` are a regression fence --
their expected outputs came from this translator, so they cannot catch a bug in
it. These tests are the other kind: they state properties directly, and several
of them would fail against a translator that passed every vector.

The refusal tests carry the weight. A translator that degrades quietly is worse
than one that stops, because the caller cannot tell a faithful translation from
a lossy one that happened to still parse.

Stdlib unittest only.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from b1_shim import (  # noqa: E402
    REASONING_MARKER,
    Untranslatable,
    chat_stream_to_events,
    chat_to_response,
    request_to_chat,
)

RESPONSE_ID = "resp_test"


def user(text: str) -> dict:
    return {"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": text}]}


class InstructionsSurvive(unittest.TestCase):
    def test_instructions_become_the_first_system_message(self):
        result = request_to_chat({
            "model": "m", "instructions": "be brief", "input": [user("hi")],
        })
        self.assertEqual(result.payload["messages"][0],
                         {"role": "system", "content": "be brief"})

    def test_no_instructions_means_no_system_message(self):
        """Absent is not empty. An empty system prompt is itself an instruction."""
        result = request_to_chat({"model": "m", "input": [user("hi")]})
        self.assertEqual([m["role"] for m in result.payload["messages"]], ["user"])

    def test_a_developer_turn_is_a_system_turn(self):
        result = request_to_chat({
            "model": "m",
            "input": [{"type": "message", "role": "developer",
                       "content": [{"type": "input_text", "text": "rules"}]}],
        })
        self.assertEqual(result.payload["messages"][0]["role"], "system")


class ToolsSurviveTheShapeChange(unittest.TestCase):
    def test_the_parameter_schema_is_carried_untouched(self):
        schema = {"type": "object", "properties": {"path": {"type": "string"}},
                  "required": ["path"]}
        result = request_to_chat({
            "model": "m", "input": [user("go")],
            "tools": [{"type": "function", "name": "read", "description": "d",
                       "parameters": schema}],
        })
        function = result.payload["tools"][0]["function"]
        self.assertEqual(function["parameters"], schema)
        self.assertEqual(function["name"], "read")
        self.assertEqual(function["description"], "d")

    def test_a_non_function_tool_is_refused_rather_than_skipped(self):
        with self.assertRaises(Untranslatable) as caught:
            request_to_chat({
                "model": "m", "input": [user("go")],
                "tools": [{"type": "web_search"}],
            })
        self.assertIn("R2", str(caught.exception))


class ArgumentsAreOpaque(unittest.TestCase):
    ARGUMENTS = '{"path":"a.md","limit":1e1,"note":"café"}'

    def test_the_arguments_string_is_carried_byte_for_byte(self):
        result = request_to_chat({
            "model": "m",
            "input": [{"type": "function_call", "call_id": "c1", "name": "read",
                       "arguments": self.ARGUMENTS}],
        })
        carried = result.payload["messages"][0]["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(carried, self.ARGUMENTS)

    def test_a_round_trip_would_have_changed_it(self):
        """The control: without this, the test above passes for any string.

        Three separate changes, each of which a caller comparing bytes would
        see: whitespace inserted, 1e1 renormalised, non-ASCII escaped.
        """
        round_tripped = json.dumps(json.loads(self.ARGUMENTS))
        self.assertNotEqual(round_tripped, self.ARGUMENTS)

    def test_pre_parsed_arguments_are_refused(self):
        """A dict here means someone already parsed it, and the bytes are gone."""
        with self.assertRaises(Untranslatable) as caught:
            request_to_chat({
                "model": "m",
                "input": [{"type": "function_call", "call_id": "c1", "name": "read",
                           "arguments": {"path": "a.md"}}],
            })
        self.assertIn("R3", str(caught.exception))


class ToolResultsRejoinTheirCall(unittest.TestCase):
    def test_the_call_id_travels_with_the_result(self):
        result = request_to_chat({
            "model": "m",
            "input": [{"type": "function_call_output", "call_id": "c1", "output": "x"}],
        })
        self.assertEqual(result.payload["messages"][0],
                         {"role": "tool", "tool_call_id": "c1", "content": "x"})

    def test_a_result_with_no_call_id_is_refused(self):
        with self.assertRaises(Untranslatable) as caught:
            request_to_chat({
                "model": "m",
                "input": [{"type": "function_call_output", "output": "x"}],
            })
        self.assertIn("R4", str(caught.exception))


class ReasoningIsNeverSilentlyDropped(unittest.TestCase):
    def test_it_is_carried_in_a_labelled_form(self):
        result = request_to_chat({
            "model": "m",
            "input": [{"type": "reasoning", "id": "r1",
                       "summary": [{"type": "summary_text", "text": "because"}]}],
        })
        content = result.payload["messages"][0]["content"]
        self.assertIn(REASONING_MARKER, content)
        self.assertIn("because", content)

    def test_the_caller_is_told_the_shape_changed(self):
        """Carrying it is half. Saying so is the other half."""
        result = request_to_chat({
            "model": "m",
            "input": [{"type": "reasoning", "id": "r1",
                       "summary": [{"type": "summary_text", "text": "because"}]}],
        })
        notes = [n for n in result.notes if n.invariant == "R5"]
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].severity, "REPRESENTED_DIFFERENTLY")

    def test_an_unreadable_reasoning_item_is_refused_not_skipped(self):
        with self.assertRaises(Untranslatable) as caught:
            request_to_chat({
                "model": "m", "input": [{"type": "reasoning", "id": "r1", "summary": []}],
            })
        self.assertIn("R5", str(caught.exception))


class TruncationNeverReadsAsComplete(unittest.TestCase):
    def response(self, finish, **extra):
        chat = {"choices": [{"index": 0,
                             "message": {"role": "assistant", "content": "cut"},
                             "finish_reason": finish}]}
        chat.update(extra)
        return chat_to_response(chat, response_id=RESPONSE_ID)

    def test_length_becomes_incomplete_with_a_stated_reason(self):
        payload = self.response("length").payload
        self.assertEqual(payload["status"], "incomplete")
        self.assertEqual(payload["incomplete_details"]["reason"], "max_output_tokens")

    def test_stop_and_tool_calls_are_complete(self):
        for finish in ("stop", "tool_calls"):
            with self.subTest(finish=finish):
                self.assertEqual(self.response(finish).payload["status"], "completed")

    def test_an_unrecognised_finish_reason_is_incomplete_not_complete(self):
        """Fails closed. An unknown stop condition is not evidence of finishing."""
        payload = self.response("content_filter").payload
        self.assertEqual(payload["status"], "incomplete")
        self.assertEqual(payload["incomplete_details"]["reason"], "content_filter")

    def test_a_missing_finish_reason_is_refused(self):
        with self.assertRaises(Untranslatable) as caught:
            chat_to_response(
                {"choices": [{"index": 0, "message": {"content": "x"}}]},
                response_id=RESPONSE_ID,
            )
        self.assertIn("R6", str(caught.exception))


class UsageIsReportedNeverInvented(unittest.TestCase):
    def test_counts_are_renamed_not_computed(self):
        result = chat_to_response(
            {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
            response_id=RESPONSE_ID,
        )
        self.assertEqual(result.payload["usage"], {"input_tokens": 7, "output_tokens": 2})

    def test_absent_usage_stays_absent(self):
        result = chat_to_response(
            {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]},
            response_id=RESPONSE_ID,
        )
        self.assertNotIn("usage", result.payload)
        self.assertTrue(any(n.invariant == "R7" for n in result.notes))

    def test_a_partial_usage_report_carries_only_what_was_reported(self):
        result = chat_to_response(
            {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 7}},
            response_id=RESPONSE_ID,
        )
        self.assertEqual(result.payload["usage"], {"input_tokens": 7})


class StreamsConcatenate(unittest.TestCase):
    def test_text_deltas_concatenate_to_the_whole_answer(self):
        """Stated as a property, not as a recorded event list.

        The vector records one expected sequence; this asserts the relationship
        the sequence exists to demonstrate, which is the thing that would still
        have to hold if the event shapes changed.
        """
        pieces = ["It ", "proves", " ", "alteration", "."]
        chunks = [{"choices": [{"index": 0, "delta": {"content": p}}]} for p in pieces]
        chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})

        events = chat_stream_to_events(chunks, response_id=RESPONSE_ID)
        deltas = [e["delta"] for e in events if e["type"] == "response.output_text.delta"]
        done = [e for e in events if e["type"] == "response.output_text.done"]
        self.assertEqual("".join(deltas), "".join(pieces))
        self.assertEqual(done[0]["text"], "".join(pieces))

    def test_a_whitespace_only_delta_is_not_dropped(self):
        chunks = [
            {"choices": [{"index": 0, "delta": {"content": "a"}}]},
            {"choices": [{"index": 0, "delta": {"content": " "}}]},
            {"choices": [{"index": 0, "delta": {"content": "b"}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ]
        events = chat_stream_to_events(chunks, response_id=RESPONSE_ID)
        done = [e for e in events if e["type"] == "response.output_text.done"][0]
        self.assertEqual(done["text"], "a b")

    def test_parallel_tool_arguments_stay_apart(self):
        chunks = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "f", "arguments": '{"a":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 1, "id": "c2", "function": {"name": "f", "arguments": '{"b":'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "1}"}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 1, "function": {"arguments": "2}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        events = chat_stream_to_events(chunks, response_id=RESPONSE_ID)
        done = {e["call_id"]: e["arguments"]
                for e in events if e["type"] == "response.function_call_arguments.done"}
        self.assertEqual(done, {"c1": '{"a":1}', "c2": '{"b":2}'})
        for arguments in done.values():
            json.loads(arguments)  # each is valid JSON on its own

    def test_a_streamed_tool_call_without_an_index_is_refused(self):
        chunks = [{"choices": [{"delta": {"tool_calls": [
            {"id": "c1", "function": {"name": "f", "arguments": "{}"}}]}}]}]
        with self.assertRaises(Untranslatable) as caught:
            chat_stream_to_events(chunks, response_id=RESPONSE_ID)
        self.assertIn("R9", str(caught.exception))

    def test_a_stream_that_never_finishes_is_refused(self):
        chunks = [{"choices": [{"index": 0, "delta": {"content": "a"}}]}]
        with self.assertRaises(Untranslatable) as caught:
            chat_stream_to_events(chunks, response_id=RESPONSE_ID)
        self.assertIn("R6", str(caught.exception))


class UnknownThingsAreRefused(unittest.TestCase):
    def test_an_unknown_request_field_is_refused(self):
        with self.assertRaises(Untranslatable) as caught:
            request_to_chat({"model": "m", "input": [user("hi")], "seed": 7})
        message = str(caught.exception)
        self.assertIn("seed", message)
        self.assertIn("R10", message)

    def test_an_unknown_input_item_type_is_refused(self):
        with self.assertRaises(Untranslatable):
            request_to_chat({"model": "m", "input": [{"type": "computer_call"}]})

    def test_an_unknown_content_part_is_refused_not_flattened(self):
        """An image part has no nearest text, and pretending it does loses the ask."""
        with self.assertRaises(Untranslatable) as caught:
            request_to_chat({
                "model": "m",
                "input": [{"type": "message", "role": "user",
                           "content": [{"type": "input_image", "image_url": "x"}]}],
            })
        self.assertIn("flattening", str(caught.exception))

    def test_a_request_with_no_model_is_refused(self):
        with self.assertRaises(Untranslatable):
            request_to_chat({"input": [user("hi")]})


if __name__ == "__main__":
    unittest.main()
