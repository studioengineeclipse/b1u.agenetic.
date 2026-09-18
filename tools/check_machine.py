#!/usr/bin/env python3
"""What this machine can run, and the one command to type next.

The first thing to run on a new machine, and deliberately the only script here
that needs nothing: no cargo, no model server, no toolchains, no arguments. It
reads the machine and says what follows from that.

    python tools/check_machine.py

What it will not do
-------------------
Tell you B1 works here. It reports facts -- interpreter version, architecture,
memory, which servers answer, which toolchains exist -- and then names the next
command. Whether B1 works is what `verify_all.py` answers, by running things.

**B1 has never been executed on Windows.** Every measurement in this
repository's README was taken on linux-x86_64 in a container. The code is
standard-library Python with no POSIX-only calls (audited, not assumed), and two
real Windows defects were found and fixed by that audit -- a `python3` command
that does not exist on a default Windows install, and a compiled consumer named
without `.exe`. Two found means the audit was worth doing and does not mean
there is not a third. This script says so on every run rather than in a footnote.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The floor is `dataclass(slots=True)`, which is 3.10. Stated as a fact about
# the code rather than a preference.
MINIMUM_PYTHON = (3, 10)

# Where B1's numbers actually come from. Anything else is untested, and the
# distinction belongs in the output.
TESTED_PLATFORMS = ("linux-x86_64",)

SERVERS = (
    ("Ollama", "http://localhost:11434/v1/models", "ollama serve"),
    ("LM Studio", "http://localhost:1234/v1/models", "start LM Studio's local server"),
)

# The fourteen, with the executable each needs. Absent is not a failure: the
# polyglot harness reports UNKNOWN, which is the honest answer.
TOOLCHAINS = (
    ("C", "cc"), ("C++", "c++"), ("Java", "javac"), ("Python", None),
    ("JavaScript", "node"), ("Go", "go"), ("Rust", "rustc"), ("TypeScript", "tsc"),
    ("Kotlin", "kotlinc"), ("Swift", "swiftc"), ("PHP", "php"), ("Ruby", "ruby"),
    ("C#", "dotnet"), ("Dart", "dart"),
)


def machine() -> dict[str, object]:
    system = platform.system()
    machine_name = platform.machine()
    normalised = {
        "AMD64": "x86_64", "x86_64": "x86_64",
        "ARM64": "arm64", "aarch64": "arm64",
    }.get(machine_name, machine_name)
    tag = f"{system.lower()}-{normalised}"
    return {
        "platform": tag,
        "system": system,
        "machine": machine_name,
        "release": platform.release(),
        "windows_arm64": system == "Windows" and normalised == "arm64",
        "tested_by_b1": tag in TESTED_PLATFORMS,
    }


def interpreter() -> dict[str, object]:
    version = sys.version_info[:3]
    return {
        "executable": sys.executable,
        "version": ".".join(map(str, version)),
        "sufficient": version[:2] >= MINIMUM_PYTHON,
        "minimum": ".".join(map(str, MINIMUM_PYTHON)),
        "why": "dataclass(slots=True) is 3.10; nothing here needs more",
    }


def memory() -> dict[str, object]:
    """Total RAM, or an honest absence.

    Three sources tried in order, because no one of them covers Linux, macOS
    and Windows. Returning None rather than a default matters: the resource
    ledger budgets against this number, and a guessed total produces a budget
    that is confidently wrong.
    """
    try:  # Linux, and the only one that needs no subprocess
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    kib = int(line.split()[1])
                    return {"total_gib": round(kib / (1024 * 1024), 2), "source": "/proc/meminfo"}
    except OSError:
        pass
    try:  # POSIX, including macOS
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return {"total_gib": round(pages * size / (1024 ** 3), 2), "source": "os.sysconf"}
    except (ValueError, OSError, AttributeError):
        pass
    try:  # Windows
        import ctypes

        class Status(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

        status = Status()
        status.dwLength = ctypes.sizeof(Status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))  # type: ignore[attr-defined]
        if status.ullTotalPhys:
            return {"total_gib": round(status.ullTotalPhys / (1024 ** 3), 2),
                    "source": "GlobalMemoryStatusEx"}
    except Exception:  # noqa: BLE001 - an unavailable API is a fact, not a crash
        pass
    return {"total_gib": None, "source": "not determinable here; the ledger will say so"}


def servers() -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for name, url, hint in SERVERS:
        entry: dict[str, object] = {"name": name, "url": url, "start_with": hint}
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            models = [m.get("id") for m in payload.get("data", []) if isinstance(m, dict)]
            entry["reachable"] = True
            entry["models"] = [m for m in models if m]
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
            entry["reachable"] = False
            entry["models"] = []
        found.append(entry)
    return found


def toolchains() -> dict[str, object]:
    present, absent = [], []
    for language, executable in TOOLCHAINS:
        if executable is None or shutil.which(executable):
            present.append(language)
        else:
            absent.append(language)
    return {
        "present": present,
        "absent": absent,
        "note": "absent toolchains make the polyglot harness report UNKNOWN for those "
                "languages, which is not a failure and is not a pass",
    }


def next_command(report: dict[str, object]) -> list[str]:
    """The one thing to type next, derived from what was actually found."""
    steps: list[str] = []
    interp = report["interpreter"]
    assert isinstance(interp, dict)
    if not interp["sufficient"]:
        return [
            f"Install Python {interp['minimum']} or newer. Found {interp['version']}, "
            f"and nothing else here will run until that changes."
        ]

    reachable = [s for s in report["servers"] if s["reachable"]]  # type: ignore[union-attr]
    if not reachable:
        steps.append(
            "No model server is answering. Install Ollama, then:  ollama pull qwen3:4b"
        )
        steps.append(
            "Then B1 runs its tournament against a real model instead of alone. Until "
            "then everything still works -- the deterministic participant is a real "
            "entrant, and verify_all reports that row PARTIAL rather than PASS."
        )
    else:
        served = [m for s in reachable for m in s["models"]]  # type: ignore[union-attr]
        if served:
            steps.append(f"A model server is answering and serves: {', '.join(served[:6])}")
        else:
            steps.append(
                "A model server is answering but serves no models yet:  ollama pull qwen3:4b"
            )

    steps.append("python tools/verify_all.py        # what holds on this machine")
    steps.append("python tools/run_agent.py --approve   # one task, all the way through")

    mem = report["memory"]
    assert isinstance(mem, dict)
    total = mem["total_gib"]
    if isinstance(total, (int, float)) and total < 12:
        steps.append(
            f"With {total} GiB total, stay at a 4B-class model. B1's ledger will refuse "
            f"anything that does not fit rather than letting the machine swap."
        )
    return steps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report: dict[str, object] = {
        "machine": machine(),
        "interpreter": interpreter(),
        "memory": memory(),
        "servers": servers(),
        "toolchains": toolchains(),
        "rust_peer": {
            "cargo": bool(shutil.which("cargo")),
            "note": "optional. Without it the Rust peer's rows report UNKNOWN, and the "
                    "cross-language agreement claims go unchecked rather than assumed",
        },
        "tested_platforms": list(TESTED_PLATFORMS),
    }
    report["next"] = next_command(report)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    host = report["machine"]
    interp = report["interpreter"]
    mem = report["memory"]
    assert isinstance(host, dict) and isinstance(interp, dict) and isinstance(mem, dict)

    print("\nB1 Local — what this machine can run")
    print("=" * 70)
    print(f"  platform     {host['platform']}  ({host['system']} {host['release']})")
    print(f"  python       {interp['version']}  "
          f"{'ok' if interp['sufficient'] else 'TOO OLD, need ' + str(interp['minimum'])}")
    total = mem["total_gib"]
    print(f"  memory       {total if total is not None else 'unknown'} GiB  "
          f"[{mem['source']}]")
    for server in report["servers"]:  # type: ignore[union-attr]
        state = "answering" if server["reachable"] else "not answering"
        extra = f" — {len(server['models'])} model(s)" if server["reachable"] else ""
        print(f"  {str(server['name']):<12} {state}{extra}")
    tools = report["toolchains"]
    assert isinstance(tools, dict)
    print(f"  toolchains   {len(tools['present'])} of 14 present")
    if tools["absent"]:
        print(f"               absent: {', '.join(tools['absent'])}")
    rust = report["rust_peer"]
    assert isinstance(rust, dict)
    print(f"  cargo        {'present' if rust['cargo'] else 'absent (optional)'}")

    if not host["tested_by_b1"]:
        print()
        print(f"  NOTE: B1 has never been executed on {host['platform']}. Every number in")
        print(f"  this repository's README was measured on {', '.join(TESTED_PLATFORMS)}.")
        print("  The Python is standard-library with no POSIX-only calls -- audited, and")
        print("  the audit found and fixed two real Windows defects. Two found does not")
        print("  mean there is not a third. Run verify_all.py and believe what it says")
        print("  about this machine over anything the README says about another one.")

    print("\n  next:")
    for step in report["next"]:  # type: ignore[union-attr]
        print(f"    {step}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
