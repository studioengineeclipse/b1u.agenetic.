#!/usr/bin/env python3
"""Regenerate the Responses-shim conformance vectors. Review the diff.

The inputs below are written by hand; the expected outputs are produced by the
reference translation in `python/b1_shim/translate.py`. That is worth being
honest about: a vector whose expectation came from the implementation cannot
catch a bug in that implementation. It is a regression fence, not an oracle.

What makes the suite mean something is the other half, in
`verify_responses_vectors.py`: every vector must also *fail* the translator that
deliberately breaks its invariant. A vector that passes both the reference and
the lossy translator is checking nothing, and the verifier reports that as a
defect rather than as ten green ticks.

    python3 tools/build_responses_vectors.py          # show what would change
    python3 tools/build_responses_vectors.py --write  # write it
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_shim import (  # noqa: E402
    Untranslatable,
    chat_stream_to_events,
    chat_to_response,
    request_to_chat,
)

OUT = ROOT / "conformance" / "responses"

READ_FILE_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
    "required": ["path"],
}

# Three things here are load-bearing, and the first draft of this vector had
# none of them -- it used `{"path": "notes/a.md", "limit": 10}`, which survives a
# JSON round-trip unchanged because Python preserves key order and that spacing
# is already what `json.dumps` emits. The verifier caught it and reported the
# vector DECORATIVE, which is exactly its job.
#
#   no space after the colon   a round-trip inserts one
#   1e1 rather than 10         a round-trip renormalises it to 10.0
#   a non-ASCII character      a round-trip escapes it to \u00e9
#
# Each is a real way a "tidying" translator changes bytes a caller may be
# comparing, while leaving a string that still parses as valid JSON.
ARGUMENTS = '{"path":"notes/a.md","limit":1e1,"note":"caf\u00e9"}'

CASES: list[dict] = [
    {
        "vector": "r1-instructions-survive",
        "invariant": "R1",
        "kind": "request",
        "why": "A Responses request carries the system prompt out of band. Chat-completions "
               "has no such field, so it has to become a system message -- first, and "
               "unaltered. Dropping it produces answers that look entirely normal.",
        "input": {
            "model": "qwen3:4b",
            "instructions": "Answer in one sentence. Never guess.",
            "input": [
                {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "What does a hash chain prove?"}]}
            ],
        },
    },
    {
        "vector": "r2-tool-schema-survives",
        "invariant": "R2",
        "kind": "request",
        "why": "Responses declares tools flat; chat-completions nests them under "
               "`function`. The shape changes and the contents must not -- a parameter "
               "schema that gains a constraint on the way through changes what the model "
               "is allowed to emit.",
        "input": {
            "model": "qwen3:4b",
            "input": [
                {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "read notes/a.md"}]}
            ],
            "tools": [{
                "type": "function",
                "name": "read_file",
                "description": "Read a file from the workspace.",
                "parameters": READ_FILE_SCHEMA,
            }],
        },
    },
    {
        "vector": "r3-tool-call-arguments-are-opaque",
        "invariant": "R3",
        "kind": "request",
        "why": "The arguments string is what the model emitted. A round-trip through "
               "parse-and-reserialise inserts whitespace, renormalises 1e1 to 10.0 and "
               "escapes non-ASCII -- the call still parses, and is no longer the call the "
               "model made. A caller comparing bytes sees a different call; a caller "
               "comparing parsed values sees nothing wrong until a number changes type.",
        "input": {
            "model": "qwen3:4b",
            "input": [
                {"type": "function_call", "call_id": "call_a1",
                 "name": "read_file", "arguments": ARGUMENTS},
            ],
        },
    },
    {
        "vector": "r4-tool-result-rejoins-its-call",
        "invariant": "R4",
        "kind": "request",
        "why": "A result with no call_id is attributed to whatever the server guesses. "
               "The model then reasons from an answer to a question it did not ask.",
        "input": {
            "model": "qwen3:4b",
            "input": [
                {"type": "function_call", "call_id": "call_a1",
                 "name": "read_file", "arguments": ARGUMENTS},
                {"type": "function_call_output", "call_id": "call_a1",
                 "output": "a hash chain proves damage, not repair"},
                {"type": "function_call", "call_id": "call_b2",
                 "name": "read_file", "arguments": '{"path": "notes/b.md"}'},
                {"type": "function_call_output", "call_id": "call_b2", "output": "second"},
            ],
        },
    },
    {
        "vector": "r5-reasoning-is-never-silently-dropped",
        "invariant": "R5",
        "kind": "request",
        "why": "Chat-completions has no reasoning item, which makes dropping it the "
               "path of least resistance. It is also the single highest-value case for a "
               "local reasoning model, and it degrades invisibly: nothing errors, the "
               "answers just get worse. Carried in a labelled form, with a note saying "
               "the shape changed.",
        "input": {
            "model": "qwen3:4b",
            "input": [
                {"type": "reasoning", "id": "rs_1",
                 "summary": [{"type": "summary_text",
                              "text": "The user asked twice; they want the limit, not the guarantee."}]},
                {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "and what can it not do?"}]},
            ],
        },
    },
    {
        "vector": "r6-truncation-never-reads-as-complete",
        "invariant": "R6",
        "kind": "response",
        "why": "receipt != postcondition, arriving in the transport layer. A cut-off "
               "answer presented as a finished one is the same class of error as a "
               "provider's success receipt taken as proof of effect.",
        "input": {
            "id": "chatcmpl-9",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "A hash chain proves that a record"},
                "finish_reason": "length",
            }],
            "usage": {"prompt_tokens": 42, "completion_tokens": 8},
        },
    },
    {
        "vector": "r7-usage-absent-is-not-zero",
        "invariant": "R7",
        "kind": "response",
        "why": "Zero-valued counts are indistinguishable from measured ones. A resource "
               "ledger built on them budgets against fiction, which is precisely the "
               "failure `b1_models.ledger` exists to avoid.",
        "input": {
            "id": "chatcmpl-10",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "It proves alteration."},
                "finish_reason": "stop",
            }],
        },
    },
    {
        "vector": "r8-text-deltas-concatenate",
        "invariant": "R8",
        "kind": "stream",
        "why": "A dropped delta produces an answer that is wrong in a way no status "
               "field reports. Whitespace-only deltas are the ones a tidying pass eats, "
               "and they are load-bearing.",
        "input": [
            {"choices": [{"index": 0, "delta": {"content": "It proves"}}]},
            {"choices": [{"index": 0, "delta": {"content": " "}}]},
            {"choices": [{"index": 0, "delta": {"content": "alteration."}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        ],
    },
    {
        "vector": "r9-parallel-tool-arguments-stay-apart",
        "invariant": "R9",
        "kind": "stream",
        "why": "Two tool calls stream interleaved, distinguished only by index. Merging "
               "them produces one malformed argument blob that parses as neither call.",
        "input": [
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "id": "call_a1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "'}}]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 1, "id": "call_b2", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "'}}]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": 'a.md"}'}}]}}]},
            {"choices": [{"index": 0, "delta": {"tool_calls": [
                {"index": 1, "function": {"arguments": 'b.md"}'}}]}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        ],
    },
    {
        "vector": "r10-unknown-field-is-refused",
        "invariant": "R10",
        "kind": "request",
        "why": "A future Responses field that constrains behaviour -- a safety setting, a "
               "cache key, a seed -- must not be discarded on the way through. Forwarding "
               "the request without it means something different from what was asked.",
        "input": {
            "model": "qwen3:4b",
            "input": [
                {"type": "message", "role": "user",
                 "content": [{"type": "input_text", "text": "hello"}]}
            ],
            "safety_identifier": "user-42",
        },
        "expect": "refusal",
    },
]

RESPONSE_ID = "resp_b1_vector"


def translate(case: dict):
    """Run the reference translation for one case, or report its refusal."""
    kind = case["kind"]
    if kind == "request":
        return request_to_chat(case["input"])
    if kind == "response":
        return chat_to_response(case["input"], response_id=RESPONSE_ID)
    return chat_stream_to_events(case["input"], response_id=RESPONSE_ID)


def build(case: dict) -> dict:
    vector = {
        "vector": case["vector"],
        "contract": "b1-responses-shim-v1",
        "invariant": case["invariant"],
        "kind": case["kind"],
        "why": case["why"],
        "expect": case.get("expect", "translation"),
        "input": case["input"],
    }
    if vector["expect"] == "refusal":
        try:
            translate(case)
        except Untranslatable as exc:
            vector["refusal_contains"] = str(exc)[:80]
            return vector
        raise SystemExit(
            f"{case['vector']}: expected a refusal and the reference translated it. "
            f"Either the vector or the translator is wrong; neither is safe to record."
        )

    result = translate(case)
    if case["kind"] == "stream":
        vector["expected_events"] = result
    else:
        vector["expected"] = result.payload
        vector["expected_notes"] = [
            {"invariant": n.invariant, "severity": n.severity, "detail": n.detail}
            for n in result.notes
        ]
    return vector


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    changed = []
    for case in CASES:
        vector = build(case)
        path = OUT / f"{case['vector']}.json"
        text = json.dumps(vector, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if text != existing:
            changed.append(path.name)
            if args.write:
                path.write_text(text, encoding="utf-8")

    if not changed:
        print(f"{len(CASES)} vectors, all unchanged.")
        return 0
    verb = "wrote" if args.write else "would change"
    print(f"{verb} {len(changed)} of {len(CASES)}: {', '.join(changed)}")
    if not args.write:
        print("Re-run with --write, and review the diff before committing it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
