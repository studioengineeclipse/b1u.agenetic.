"""B1 Local Responses shim: the meaning of the translation, not the transport.

Codex speaks only `/v1/responses` (ADR-0002). llama.cpp and most
OpenAI-compatible servers speak chat-completions. This package defines what a
translation between them must preserve, and `conformance/responses/` checks it.

The shim itself -- an HTTP process with SSE framing -- is not built.
"""

from .translate import (
    REASONING_MARKER,
    SHIM_CONTRACT,
    Translation,
    TranslationNote,
    Untranslatable,
    chat_stream_to_events,
    chat_to_response,
    request_to_chat,
    stable_json,
)

__all__ = [
    "REASONING_MARKER",
    "SHIM_CONTRACT",
    "Translation",
    "TranslationNote",
    "Untranslatable",
    "chat_stream_to_events",
    "chat_to_response",
    "request_to_chat",
    "stable_json",
]
