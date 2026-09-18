"""Reference translation between the Responses API and chat-completions.

What this is
------------
The meaning of the translation, as pure functions over already-parsed JSON. It
is what `contracts/b1-responses-shim-v1.json` specifies and what
`conformance/responses/` checks.

What this is not
----------------
The shim. There is no HTTP server here, no SSE framing, no backpressure, no
connection handling and no error mapping. Those are the parts that move bytes;
this is the part that says what the bytes mean, and it was written first on
purpose. A conformance suite written after the code tends to describe what the
code already does.

Two working assumptions, stated rather than buried
--------------------------------------------------
The Codex archive is not present in this environment, so the *field spellings*
below follow the documented API surfaces rather than source that was re-read in
this pass. What is VERIFIED (ADR-0002) is that Codex speaks only `/v1/responses`
and that `wire_api = "chat"` is a hard error. What is WORKING_ASSUMPTION is
every key name in this file.

Every invariant is phrased in terms of what must survive rather than what it is
called, so a corrected spelling changes the vectors and not the contract. But
nothing here is evidence about the wire, and a shim written against these
vectors must re-check them against the archive first.

Why comparison is exact JSON and not canonical bytes
----------------------------------------------------
`b1_protocol.canonical` rejects floats and nulls, for reasons that are about
B1's own journal staying byte-reproducible across languages. These payloads are
a wire format B1 does not own: `temperature` really is a float and `content`
really can be null. So vectors compare with a stable-but-ordinary JSON dump, and
what reaches the journal is a digest of the *translation decision*, never the
payload.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

__all__ = [
    "SHIM_CONTRACT",
    "Untranslatable",
    "TranslationNote",
    "Translation",
    "stable_json",
    "request_to_chat",
    "chat_to_response",
    "chat_stream_to_events",
]

SHIM_CONTRACT = "b1-responses-shim-v1"

# Fields this translator understands. Anything else refuses (R10) rather than
# being forwarded minus the field, because a future Responses field that
# constrains behaviour would otherwise be discarded silently.
KNOWN_REQUEST_FIELDS = frozenset(
    {"model", "instructions", "input", "tools", "max_output_tokens", "stream", "tool_choice"}
)
KNOWN_INPUT_TYPES = frozenset(
    {"message", "function_call", "function_call_output", "reasoning"}
)

# The marker a reasoning item is carried under when the server has no place for
# one. Deliberately conspicuous: a human reading the transcript should be able
# to see that the shape was changed, and a later reader of this code should not
# be able to mistake it for a native representation.
REASONING_MARKER = "[b1-shim: reasoning carried from a Responses reasoning item]"


class Untranslatable(Exception):
    """The translation would have to guess, so it refuses.

    Raised rather than returning a best effort. A translator that degrades
    quietly is worse than one that stops, because the caller cannot tell the
    difference between a faithful translation and a lossy one that happened to
    still parse.
    """


@dataclass(frozen=True, slots=True)
class TranslationNote:
    """Something the caller has to know about how this was carried.

    `severity` is the field that matters:

        PRESERVED                 carried with its meaning intact
        REPRESENTED_DIFFERENTLY   carried, in a shape the target does not
                                  natively have, and the caller should know
        UNREPRESENTABLE           not carried; the translation refused

    A note is not a warning to be logged and forgotten. R5 is satisfied by a
    reasoning item either surviving in a labelled form *or* refusing, and in
    both cases by the caller being told -- the note is how it is told.
    """

    invariant: str
    severity: str
    detail: str


@dataclass(frozen=True, slots=True)
class Translation:
    """A translated payload and everything the caller must know about it."""

    payload: dict
    notes: tuple[TranslationNote, ...] = ()

    def note_severities(self) -> set[str]:
        return {note.severity for note in self.notes}


def stable_json(value: object) -> str:
    """Deterministic JSON for comparing a translation against a vector.

    Sorted keys and no whitespace, so two structurally identical payloads
    compare equal as strings. Not `canonical_json_bytes`: see the module
    docstring for why a wire format B1 does not own is held to a different rule
    than B1's own journal.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _text_of(content: object, *, where: str) -> str:
    """The text of a Responses content list, or refuse.

    Refuses a shape it does not recognise rather than reaching for whatever
    string it can find. A content part this translator has not been taught
    about may well carry an image, a file or a citation, and flattening it to
    its nearest text would silently change what was asked.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise Untranslatable(f"{where}: content must be a string or a list, got {type(content).__name__}")
    parts: list[str] = []
    for index, part in enumerate(content):
        if not isinstance(part, dict):
            raise Untranslatable(f"{where}[{index}]: content part must be an object")
        kind = part.get("type")
        if kind in ("input_text", "output_text", "text"):
            parts.append(str(part.get("text", "")))
        else:
            raise Untranslatable(
                f"{where}[{index}]: content part type {kind!r} has no chat-completions "
                f"equivalent this translator knows. Refusing rather than flattening it to "
                f"its nearest text, which would change what was asked."
            )
    return "".join(parts)


def request_to_chat(request: dict) -> Translation:
    """A Responses request, as a chat-completions request.

    Covers R1, R2, R3, R4, R5 and R10.
    """
    if not isinstance(request, dict):
        raise Untranslatable("request must be an object")

    unknown = sorted(set(request) - KNOWN_REQUEST_FIELDS)
    if unknown:
        raise Untranslatable(
            f"request carries field(s) this translator does not understand: {unknown}. "
            f"Refusing rather than forwarding the request without them -- a field that "
            f"constrains behaviour would otherwise be discarded silently and the request "
            f"would mean something else. (R10)"
        )
    if "model" not in request:
        raise Untranslatable("request has no model")

    notes: list[TranslationNote] = []
    messages: list[dict] = []

    # R1: instructions become the first system message, unaltered.
    instructions = request.get("instructions")
    if instructions is not None:
        if not isinstance(instructions, str):
            raise Untranslatable("instructions must be a string")
        messages.append({"role": "system", "content": instructions})
        notes.append(TranslationNote("R1", "PRESERVED", "instructions carried as a system message"))

    for index, item in enumerate(request.get("input") or []):
        if not isinstance(item, dict):
            raise Untranslatable(f"input[{index}] must be an object")
        kind = item.get("type", "message")
        if kind not in KNOWN_INPUT_TYPES:
            raise Untranslatable(
                f"input[{index}]: item type {kind!r} is not one this translator knows "
                f"({sorted(KNOWN_INPUT_TYPES)}). (R10)"
            )

        if kind == "message":
            role = item.get("role")
            if role not in ("user", "assistant", "system", "developer"):
                raise Untranslatable(f"input[{index}]: unknown role {role!r}")
            # `developer` is the Responses spelling of a system-authored turn.
            messages.append({
                "role": "system" if role == "developer" else role,
                "content": _text_of(item.get("content"), where=f"input[{index}]"),
            })

        elif kind == "function_call":
            # R3: arguments travel as an opaque string. Never parsed and
            # re-serialised: that can reorder keys or renormalise numbers, and
            # the call would still parse while no longer being the call the
            # model made.
            arguments = item.get("arguments")
            if not isinstance(arguments, str):
                raise Untranslatable(
                    f"input[{index}]: function_call arguments must be the string the model "
                    f"emitted, not a parsed object -- re-serialising it would change bytes "
                    f"the caller may be comparing. (R3)"
                )
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": item.get("call_id"),
                    "type": "function",
                    "function": {"name": item.get("name"), "arguments": arguments},
                }],
            })
            notes.append(TranslationNote("R3", "PRESERVED", f"call {item.get('call_id')!r} carried verbatim"))

        elif kind == "function_call_output":
            # R4: the result rejoins its call by id.
            call_id = item.get("call_id")
            if not call_id:
                raise Untranslatable(
                    f"input[{index}]: function_call_output has no call_id, so nothing can "
                    f"say which call it answers. (R4)"
                )
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": item.get("output", ""),
            })

        else:  # reasoning
            # R5. Chat-completions has no reasoning item. It is carried in a
            # labelled form rather than dropped, and the caller is told the
            # shape changed -- that is what "never silently" means.
            summary = item.get("summary") or []
            text = "".join(
                str(part.get("text", "")) for part in summary if isinstance(part, dict)
            )
            if not text:
                raise Untranslatable(
                    f"input[{index}]: a reasoning item with no readable summary cannot be "
                    f"carried at all. Refusing rather than dropping it. (R5)"
                )
            messages.append({"role": "assistant", "content": f"{REASONING_MARKER}\n{text}"})
            notes.append(TranslationNote(
                "R5", "REPRESENTED_DIFFERENTLY",
                "a reasoning item was carried as a labelled assistant message; "
                "chat-completions has no reasoning item and the server will treat it as "
                "ordinary context",
            ))

    chat: dict = {"model": request["model"], "messages": messages}

    # R2: name, description and parameters survive the flat-to-nested move
    # without being touched.
    tools = request.get("tools")
    if tools is not None:
        translated_tools = []
        for index, tool in enumerate(tools):
            if not isinstance(tool, dict) or tool.get("type") != "function":
                raise Untranslatable(
                    f"tools[{index}]: only function tools have a chat-completions "
                    f"equivalent this translator knows. (R2)"
                )
            function: dict = {"name": tool.get("name")}
            if "description" in tool:
                function["description"] = tool["description"]
            if "parameters" in tool:
                function["parameters"] = tool["parameters"]
            translated_tools.append({"type": "function", "function": function})
        chat["tools"] = translated_tools
        notes.append(TranslationNote("R2", "PRESERVED", f"{len(translated_tools)} tool(s) carried"))

    if "tool_choice" in request:
        chat["tool_choice"] = request["tool_choice"]
    if "max_output_tokens" in request:
        chat["max_tokens"] = request["max_output_tokens"]
    if "stream" in request:
        chat["stream"] = request["stream"]

    return Translation(payload=chat, notes=tuple(notes))


def chat_to_response(chat: dict, *, response_id: str) -> Translation:
    """A chat-completions response, as a Responses response.

    Covers R3, R6 and R7.
    """
    if not isinstance(chat, dict):
        raise Untranslatable("chat response must be an object")
    choices = chat.get("choices")
    if not choices:
        raise Untranslatable("chat response has no choices")
    choice = choices[0]
    message = choice.get("message") or {}
    notes: list[TranslationNote] = []
    output: list[dict] = []

    content = message.get("content")
    if content:
        output.append({
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": content}],
        })

    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if not isinstance(arguments, str):
            raise Untranslatable(
                "a tool call's arguments must be the string the server sent. (R3)"
            )
        output.append({
            "type": "function_call",
            "call_id": call.get("id"),
            "name": function.get("name"),
            "arguments": arguments,
        })

    # R6: a cut-off answer must not read as a finished one. This is
    # receipt-is-not-postcondition arriving in the transport layer.
    finish = choice.get("finish_reason")
    response: dict = {"id": response_id, "object": "response", "output": output}
    if finish == "length":
        response["status"] = "incomplete"
        response["incomplete_details"] = {"reason": "max_output_tokens"}
        notes.append(TranslationNote(
            "R6", "PRESERVED",
            "finish_reason 'length' became status 'incomplete'; the answer is cut off",
        ))
    elif finish in ("stop", "tool_calls"):
        response["status"] = "completed"
    elif finish is None:
        raise Untranslatable(
            "chat response has no finish_reason, so whether the answer is complete is "
            "unknown. Refusing rather than assuming 'completed'. (R6)"
        )
    else:
        response["status"] = "incomplete"
        response["incomplete_details"] = {"reason": str(finish)}
        notes.append(TranslationNote(
            "R6", "PRESERVED", f"finish_reason {finish!r} carried through as incomplete",
        ))

    # R7: counts are renamed, never computed. A server reporting no usage
    # produces a response with no usage, because zeros are indistinguishable
    # from measurements and a ledger built on them budgets against fiction.
    usage = chat.get("usage")
    if isinstance(usage, dict):
        translated: dict = {}
        if "prompt_tokens" in usage:
            translated["input_tokens"] = usage["prompt_tokens"]
        if "completion_tokens" in usage:
            translated["output_tokens"] = usage["completion_tokens"]
        if translated:
            response["usage"] = translated
    else:
        notes.append(TranslationNote(
            "R7", "PRESERVED",
            "the server reported no usage, so the translation carries none. Absent is "
            "not zero",
        ))

    return Translation(payload=response, notes=tuple(notes))


def chat_stream_to_events(chunks: list[dict], *, response_id: str) -> list[dict]:
    """Chat-completions stream chunks, as Responses stream events.

    Covers R8 and R9. Returns the event list rather than a payload, so the
    caller -- and the vectors -- can assert on the sequence.
    """
    events: list[dict] = []
    text_parts: list[str] = []
    # Keyed by the index the server assigns a tool call, because parallel calls
    # interleave and merging them would produce one malformed argument blob.
    call_arguments: dict[int, list[str]] = {}
    call_identity: dict[int, dict] = {}
    finish: str | None = None

    for position, chunk in enumerate(chunks):
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if choice.get("finish_reason"):
                finish = choice["finish_reason"]

            piece = delta.get("content")
            if piece:
                text_parts.append(piece)
                events.append({
                    "type": "response.output_text.delta",
                    "response_id": response_id,
                    "delta": piece,
                })

            for call in delta.get("tool_calls") or []:
                index = call.get("index")
                if index is None:
                    raise Untranslatable(
                        f"chunk {position}: a streamed tool call has no index, so parallel "
                        f"calls cannot be kept apart. (R9)"
                    )
                function = call.get("function") or {}
                if call.get("id") or function.get("name"):
                    call_identity[index] = {
                        "call_id": call.get("id") or call_identity.get(index, {}).get("call_id"),
                        "name": function.get("name") or call_identity.get(index, {}).get("name"),
                    }
                fragment = function.get("arguments")
                if fragment:
                    call_arguments.setdefault(index, []).append(fragment)
                    events.append({
                        "type": "response.function_call_arguments.delta",
                        "response_id": response_id,
                        "call_id": call_identity.get(index, {}).get("call_id"),
                        "delta": fragment,
                    })

    for index in sorted(call_arguments):
        identity = call_identity.get(index, {})
        events.append({
            "type": "response.function_call_arguments.done",
            "response_id": response_id,
            "call_id": identity.get("call_id"),
            "name": identity.get("name"),
            "arguments": "".join(call_arguments[index]),
        })

    if text_parts:
        events.append({
            "type": "response.output_text.done",
            "response_id": response_id,
            "text": "".join(text_parts),
        })

    if finish is None:
        raise Untranslatable(
            "the stream ended with no finish_reason, so whether the answer is complete is "
            "unknown. Refusing rather than emitting response.completed. (R6)"
        )
    if finish == "length":
        events.append({
            "type": "response.incomplete",
            "response_id": response_id,
            "reason": "max_output_tokens",
        })
    else:
        events.append({"type": "response.completed", "response_id": response_id})

    return events
