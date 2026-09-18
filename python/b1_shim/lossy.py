"""Deliberately broken translators, one per invariant.

A conformance suite that has only ever been shown a correct translator is an
untested suite. These exist so each vector can be required to *catch* something:
for every invariant in `contracts/b1-responses-shim-v1.json` there is a
translator that violates exactly that invariant and nothing else, and the
verifier requires that the vectors fail it -- and fail it for the right reason.

Each one is a plausible shortcut rather than a contrived error. That is the
point. `drop_instructions` is what happens when someone forgets that Responses
carries the system prompt out of band. `reserialise_arguments` is what happens
when someone round-trips JSON because it seems tidier. `zero_usage` is what
happens when a downstream type wants an integer. None of them look like bugs
while you are writing them, and every one is caught here.
"""
from __future__ import annotations

import json

from .translate import (
    Translation,
    TranslationNote,
    Untranslatable,
    chat_stream_to_events,
    chat_to_response,
    request_to_chat,
)

__all__ = ["LOSSY_TRANSLATORS", "lossy_for"]


def drop_instructions(request: dict) -> Translation:
    """R1. Forgets that Responses carries the system prompt out of band."""
    stripped = {k: v for k, v in request.items() if k != "instructions"}
    return request_to_chat(stripped)


def rename_tool_parameters(request: dict) -> Translation:
    """R2. 'Normalises' a parameter schema on the way through."""
    result = request_to_chat(request)
    payload = json.loads(json.dumps(result.payload))
    for tool in payload.get("tools") or []:
        parameters = tool["function"].get("parameters")
        if isinstance(parameters, dict) and "properties" in parameters:
            # Helpfully adds a default nobody asked for.
            parameters["additionalProperties"] = False
    return Translation(payload=payload, notes=result.notes)


def reserialise_arguments(request: dict) -> Translation:
    """R3. Round-trips the arguments string because it seems tidier."""
    result = request_to_chat(request)
    payload = json.loads(json.dumps(result.payload))
    for message in payload.get("messages") or []:
        for call in message.get("tool_calls") or []:
            arguments = call["function"].get("arguments")
            if isinstance(arguments, str):
                try:
                    call["function"]["arguments"] = json.dumps(json.loads(arguments))
                except json.JSONDecodeError:
                    pass
    return Translation(payload=payload, notes=result.notes)


def forget_call_id(request: dict) -> Translation:
    """R4. Drops tool_call_id, since 'the last call is obviously the one'."""
    result = request_to_chat(request)
    payload = json.loads(json.dumps(result.payload))
    for message in payload.get("messages") or []:
        if message.get("role") == "tool":
            message.pop("tool_call_id", None)
    return Translation(payload=payload, notes=result.notes)


def drop_reasoning(request: dict) -> Translation:
    """R5. The one that matters most, and the easiest to write by accident.

    Chat-completions has nowhere to put a reasoning item, so this skips it. No
    error, no note, no trace: the answers just get worse.
    """
    stripped = dict(request)
    stripped["input"] = [
        item for item in (request.get("input") or [])
        if not (isinstance(item, dict) and item.get("type") == "reasoning")
    ]
    result = request_to_chat(stripped)
    return Translation(
        payload=result.payload,
        notes=tuple(n for n in result.notes if n.invariant != "R5"),
    )


def truncation_reads_as_complete(chat: dict, *, response_id: str) -> Translation:
    """R6. Maps every finish_reason to 'completed' because it is simpler."""
    relaxed = json.loads(json.dumps(chat))
    for choice in relaxed.get("choices") or []:
        if choice.get("finish_reason") == "length":
            choice["finish_reason"] = "stop"
    return chat_to_response(relaxed, response_id=response_id)


def zero_usage(chat: dict, *, response_id: str) -> Translation:
    """R7. Fills missing counts with zeros because a downstream type wants ints."""
    result = chat_to_response(chat, response_id=response_id)
    payload = json.loads(json.dumps(result.payload))
    payload.setdefault("usage", {})
    payload["usage"].setdefault("input_tokens", 0)
    payload["usage"].setdefault("output_tokens", 0)
    return Translation(
        payload=payload,
        notes=tuple(n for n in result.notes if n.invariant != "R7"),
    )


def drop_empty_looking_deltas(chunks: list[dict], *, response_id: str) -> list[dict]:
    """R8. Skips a delta that looks like whitespace, to 'tidy' the stream."""
    trimmed = json.loads(json.dumps(chunks))
    for chunk in trimmed:
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str) and not delta["content"].strip():
                delta.pop("content")
    return chat_stream_to_events(trimmed, response_id=response_id)


def merge_parallel_tool_calls(chunks: list[dict], *, response_id: str) -> list[dict]:
    """R9. Treats every argument fragment as belonging to one call."""
    flattened = json.loads(json.dumps(chunks))
    for chunk in flattened:
        for choice in chunk.get("choices") or []:
            for call in (choice.get("delta") or {}).get("tool_calls") or []:
                call["index"] = 0
    return chat_stream_to_events(flattened, response_id=response_id)


def forward_unknown_fields(request: dict) -> Translation:
    """R10. Passes through what it does not understand, minus the field."""
    known = {
        k: v for k, v in request.items()
        if k in {"model", "instructions", "input", "tools", "max_output_tokens",
                 "stream", "tool_choice"}
    }
    return request_to_chat(known)


# Mapped by invariant, so the verifier can require that each vector fails the
# translator that breaks *its* invariant and passes the other nine.
LOSSY_TRANSLATORS = {
    "R1": ("drop_instructions", drop_instructions, "request"),
    "R2": ("rename_tool_parameters", rename_tool_parameters, "request"),
    "R3": ("reserialise_arguments", reserialise_arguments, "request"),
    "R4": ("forget_call_id", forget_call_id, "request"),
    "R5": ("drop_reasoning", drop_reasoning, "request"),
    "R6": ("truncation_reads_as_complete", truncation_reads_as_complete, "response"),
    "R7": ("zero_usage", zero_usage, "response"),
    "R8": ("drop_empty_looking_deltas", drop_empty_looking_deltas, "stream"),
    "R9": ("merge_parallel_tool_calls", merge_parallel_tool_calls, "stream"),
    "R10": ("forward_unknown_fields", forward_unknown_fields, "request"),
}


def lossy_for(invariant: str):
    """The translator that breaks one named invariant."""
    if invariant not in LOSSY_TRANSLATORS:
        raise KeyError(f"no lossy translator for {invariant!r}")
    return LOSSY_TRANSLATORS[invariant]


_ = Untranslatable, TranslationNote  # re-exported through translate; kept in view here
