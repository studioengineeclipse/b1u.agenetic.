#!/usr/bin/env python3
"""Measure a local model, and emit a profile to replace the estimates.

Every byte and millisecond figure in `python/b1_models/registry.py` is a
declared estimate carrying `WORKING_ASSUMPTION`. This is the tool that turns
them into measurements — but only on a machine that is actually running the
model. Run it on the OmniBook; nothing else produces these numbers.

    python3 tools/benchmark_model.py --provider ollama --model qwen3:4b

It prints a `ModelProfile(...)` you can paste into `candidate_profiles()`,
with the fields it measured filled in and the fields it could not measure
marked rather than guessed.

What it will not do
-------------------
Invent a number. If the resident-memory figure cannot be read from the
provider, it says so and leaves the estimate in place with a comment saying it
is still an estimate. A benchmark that filled a gap with a plausible value
would be worse than no benchmark, because the output looks measured either way.

Cold load
---------
"Cold" means the model was not resident when the first request arrived. This
tool cannot force that state — only the provider can — so it reports the first
request's latency and says plainly whether it knows the model was cold. If you
want a true cold measurement, unload first (`ollama stop <model>`) and pass
`--assume-cold`.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from b1_models import (  # noqa: E402
    GIB,
    MIB,
    ProviderError,
    ProviderUnavailable,
    lmstudio_provider,
    ollama_provider,
)

# Long enough to exercise generation, short enough that a slow machine still
# finishes. The content is deliberately about B1 so the answer is inspectable.
PROMPT = (
    "In two sentences, explain why a hash chain can prove that a record was "
    "altered but cannot restore the original record."
)


def ollama_resident_bytes(model_id: str) -> int | None:
    """Ask Ollama what it currently holds resident, in bytes.

    Ollama's /api/ps reports loaded models and their sizes. This is the one
    place a real resident figure is available without platform-specific process
    inspection, and it is worth using because the alternative is an estimate
    wearing a measurement's clothes.

    Returns None when unavailable — which is a fact, not a failure.
    """
    try:
        with urllib.request.urlopen("http://localhost:11434/api/ps", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None
    for entry in payload.get("models", []) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") == model_id or entry.get("model") == model_id:
            size = entry.get("size_vram") or entry.get("size")
            if isinstance(size, int):
                return size
    return None


def measure(provider, model_id: str, runs: int, assume_cold: bool) -> dict[str, object]:
    latencies: list[float] = []
    throughputs: list[float] = []
    first_latency: float | None = None
    sample_text = ""

    for index in range(runs):
        started = time.monotonic()
        try:
            completion = provider.complete(model_id=model_id, prompt=PROMPT, max_tokens=256)
        except ProviderUnavailable as exc:
            return {"error": f"provider unavailable: {exc}"}
        except ProviderError as exc:
            return {"error": f"provider error: {exc}"}
        elapsed = time.monotonic() - started

        if index == 0:
            first_latency = elapsed
            sample_text = completion.text
            # The first request may include a load. It is excluded from the
            # warm statistics rather than averaged in, because mixing a load
            # into a latency figure makes both numbers meaningless.
            continue

        latencies.append(elapsed)
        if completion.completion_tokens:
            throughputs.append(completion.completion_tokens / elapsed)

    result: dict[str, object] = {
        "model_id": model_id,
        "provider_id": provider.provider_id,
        "runs": runs,
        "prompt_chars": len(PROMPT),
        "sample_answer": sample_text[:280],
    }

    if first_latency is not None:
        result["first_request_s"] = round(first_latency, 3)
        result["first_request_was_cold"] = (
            "ASSUMED_COLD (you passed --assume-cold)" if assume_cold
            else "UNKNOWN — this tool cannot force an unload; run `ollama stop` "
                 "first and pass --assume-cold for a true cold figure"
        )

    if latencies:
        result["warm_latency_s"] = {
            "min": round(min(latencies), 3),
            "median": round(statistics.median(latencies), 3),
            "max": round(max(latencies), 3),
            "samples": len(latencies),
        }
    else:
        result["warm_latency_s"] = (
            "NOT MEASURED — only one run was requested, and the first is excluded "
            "because it may include a load"
        )

    if throughputs:
        result["tokens_per_second"] = {
            "median": round(statistics.median(throughputs), 1),
            "samples": len(throughputs),
        }
    else:
        result["tokens_per_second"] = (
            "NOT MEASURED — the provider reported no completion token count"
        )

    resident = (
        ollama_resident_bytes(model_id)
        if provider.provider_id == "ollama" else None
    )
    if resident is not None:
        result["resident_bytes"] = resident
        result["resident_gib"] = round(resident / GIB, 2)
        result["resident_basis"] = "MEASURED via Ollama /api/ps"
    else:
        result["resident_bytes"] = None
        result["resident_basis"] = (
            "NOT MEASURED — no resident figure available from this provider. "
            "The profile below keeps its estimate, and it is still an estimate."
        )
    return result


def emit_profile(result: dict[str, object]) -> str:
    """A ModelProfile to paste into candidate_profiles(), honest about gaps."""
    resident = result.get("resident_bytes")
    if isinstance(resident, int):
        # Split the measured resident figure into weights and overhead using
        # the same shape the registry uses. The split itself is a convention,
        # not a measurement, and the comment says so.
        weights = int(resident * 0.85)
        overhead = resident - weights
        weights_line = f"        weights_bytes={weights},  # from measured resident {resident}"
        overhead_line = f"        runtime_overhead_bytes={overhead},  # remainder of measured resident"
        note = "MEASURED resident; weights/overhead split is a convention"
    else:
        weights_line = "        weights_bytes=...,  # STILL AN ESTIMATE — not measured"
        overhead_line = "        runtime_overhead_bytes=...,  # STILL AN ESTIMATE — not measured"
        note = "resident NOT measured on this machine"

    return f"""    ModelProfile(
        model_id={result['model_id']!r},
        provider_id={result['provider_id']!r},
{weights_line}
        kv_bytes_per_token=...,  # STILL AN ESTIMATE — measure by varying context
{overhead_line}
        default_context_tokens=8192,
        roles=("generalist",),
        notes="{note}; benchmarked on this machine",
    ),"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=("ollama", "lmstudio"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--runs", type=int, default=4,
                        help="total requests; the first is excluded from warm stats")
    parser.add_argument("--assume-cold", action="store_true",
                        help="you unloaded the model first, so the first request is a true cold load")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.runs < 2:
        parser.error("--runs must be at least 2: the first request is excluded from warm stats")

    provider = {"ollama": ollama_provider, "lmstudio": lmstudio_provider}[args.provider]()

    available = provider.available_models()
    if not available:
        print(
            f"{args.provider} at {provider.base_url} is not answering.\n"
            f"Start it, then: ollama pull {args.model}\n"
            f"See docs/RUNNING.md.",
            file=sys.stderr,
        )
        return 2
    if args.model not in available:
        print(
            f"{args.provider} is answering but does not serve {args.model!r}.\n"
            f"It serves: {', '.join(available)}",
            file=sys.stderr,
        )
        return 2

    print(f"benchmarking {args.model} on {args.provider} ({args.runs} runs)...\n",
          file=sys.stderr)
    result = measure(provider, args.model, args.runs, args.assume_cold)

    if "error" in result:
        print(result["error"], file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    print("=" * 66)
    print(f"  model            {result['model_id']}  via {result['provider_id']}")
    print(f"  first request    {result['first_request_s']}s")
    print(f"                   cold: {result['first_request_was_cold']}")
    warm = result["warm_latency_s"]
    if isinstance(warm, dict):
        print(f"  warm latency     median {warm['median']}s  "
              f"(min {warm['min']}, max {warm['max']}, n={warm['samples']})")
    else:
        print(f"  warm latency     {warm}")
    tps = result["tokens_per_second"]
    if isinstance(tps, dict):
        print(f"  throughput       {tps['median']} tok/s median (n={tps['samples']})")
    else:
        print(f"  throughput       {tps}")
    if result.get("resident_gib"):
        print(f"  resident         {result['resident_gib']} GiB  [{result['resident_basis']}]")
    else:
        print(f"  resident         {result['resident_basis']}")
    print("=" * 66)
    print("\nSample answer, so you can judge whether the model is any good at this:\n")
    print(f"  {result['sample_answer']}\n")
    print("Paste into candidate_profiles() in python/b1_models/registry.py,")
    print("replacing the estimate for this model:\n")
    print(emit_profile(result))
    print("\nFields still marked ESTIMATE are ones this run did not measure.")
    print("Leave them marked until something measures them.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
