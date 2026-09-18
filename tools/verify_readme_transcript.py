#!/usr/bin/env python3
"""Check that every quoted verifier transcript is the verifier's real output.

README.md and `docs/OMEGA13-ANALYSIS.md` §4.7 each print a block introduced by
`$ python3 tools/verify_all.py` and present it as that command's output. This
checks the claim, in every file that makes it.

It exists because that claim was once false. An earlier draft paraphrased the
table and printed `7/7 fields AGREED` on a line where the tool had actually
reported a head digest -- a number the tool never produced, in a project whose
whole argument is that its claims are checked by running code. The paraphrase
was not a lie anyone told; it was a summary that drifted. Drift is exactly what
a check is for.

    python3 tools/verify_all.py --json > report.json
    python3 tools/verify_readme_transcript.py --report report.json

With no `--report` it runs `verify_all.py --json` itself, which takes as long
as the full suite. It is deliberately not wired into `verify_all.py`: that
would recurse, and the result would be noise on any machine whose measurements
differ from the ones the README records.

Three outcomes, and the third is the point
------------------------------------------
  PASS    every transcript line is a line this tool prints
  STALE   a transcript line names a check whose real line now reads differently
  FAIL    the transcript names a check that does not exist, or is missing

`STALE` is not called `FAIL` because two different things produce it and this
script cannot tell them apart: the README may be wrong, or your machine may
simply differ from the one the README records (fewer toolchains, a model server
running, a different test count). Both mean "this transcript does not describe
this run". Only a person can say which. Reporting it as a defect would be
asserting a cause that was not observed.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from verify_all import render_footer, render_table  # noqa: E402

TRANSCRIPT_COMMAND = "$ python3 tools/verify_all.py"

# Every file that presents the table as this tool's output. A file that quotes
# the verifier and is not listed here is unchecked, which is the state README.md
# and the analysis document were both in until each drifted.
QUOTING_FILES = ("README.md", "docs/OMEGA13-ANALYSIS.md")


def extract_transcript(text: str) -> tuple[list[str], str | None]:
    """The lines of the fenced block that follows the transcript command.

    Returns (lines, error). Blank lines and the command line itself are
    dropped; everything else is compared verbatim, including leading spaces,
    because the alignment is part of what is being claimed.
    """
    fences = [m.start() for m in re.finditer(r"^```", text, flags=re.MULTILINE)]
    for opening, closing in zip(fences[0::2], fences[1::2]):
        block = text[opening:closing]
        if TRANSCRIPT_COMMAND not in block:
            continue
        lines = [
            line.rstrip()
            for line in block.splitlines()[1:]
            if line.strip() and TRANSCRIPT_COMMAND not in line
        ]
        return lines, None
    return [], f"no fenced block containing {TRANSCRIPT_COMMAND!r}"


def name_of(line: str) -> str:
    """The check name a table line starts with, or '' for a footer line.

    Table lines are two spaces, a left-padded check name, two spaces, a status.
    Splitting on the run of spaces before the status recovers the name without
    needing to know the padding width.
    """
    match = re.match(r"^  (\S.*?)\s{2,}(PASS|FAIL|PARTIAL|UNKNOWN)\s", line)
    return match.group(1) if match else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--report", help="a saved `verify_all.py --json` report")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.report:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
        source = args.report
    else:
        completed = subprocess.run(
            [sys.executable, "tools/verify_all.py", "--json"],
            cwd=ROOT, text=True, capture_output=True, timeout=args.timeout,
        )
        try:
            report = json.loads(completed.stdout)
        except json.JSONDecodeError:
            payload = {
                "status": "FAIL",
                "errors": [f"verify_all.py --json produced no parseable report: "
                           f"{(completed.stdout + completed.stderr).strip()[:300]}"],
            }
            print(json.dumps(payload, indent=2) if args.json else payload["errors"][0])
            return 1
        source = "a fresh verify_all.py run"

    checks = report["checks"]
    actual = render_table(checks) + render_footer(checks)
    by_name = {name_of(line): line for line in actual if name_of(line)}

    errors: list[str] = []
    stale: list[str] = []
    counted: dict[str, int] = {}

    for relative in QUOTING_FILES:
        path = ROOT / relative
        if not path.exists():
            errors.append(f"{relative}: listed as quoting the verifier, but does not exist")
            continue
        transcript, error = extract_transcript(path.read_text(encoding="utf-8"))
        if error:
            errors.append(f"{relative}: {error}")
            continue
        counted[relative] = len(transcript)
        quoted: set[str] = set()

        for line in transcript:
            if line in actual:
                quoted.add(name_of(line))
                continue
            name = name_of(line)
            if not name:
                # A footer line that does not match. The footer counts PARTIAL
                # and UNKNOWN checks, so this is the same ambiguity as a table
                # line.
                stale.append(f"{relative} prints {line.strip()!r}; this run prints "
                             f"{' / '.join(s.strip() for s in render_footer(checks))!r}")
            elif name in by_name:
                stale.append(f"{relative} · {name}: prints {line.strip()!r}, "
                             f"this run prints {by_name[name].strip()!r}")
            else:
                errors.append(
                    f"{relative}: transcript names a check that does not exist: {name!r}"
                )

        missing = sorted(set(by_name) - quoted - {name_of(line) for line in transcript})
        if missing and not stale:
            errors.append(
                f"{relative}: transcript omits checks this tool prints: " + ", ".join(missing)
            )

    status = "FAIL" if errors else ("STALE" if stale else "PASS")
    payload = {
        "status": status,
        "source": source,
        "files": counted,
        "errors": errors,
        "stale": stale,
    }

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif status == "PASS":
        quoted_files = ", ".join(f"{name} ({n} lines)" for name, n in counted.items())
        print(f"quoted transcripts: PASS — verbatim from {source}: {quoted_files}")
    else:
        print(f"quoted transcripts: {status}")
        for entry in errors + stale:
            print(f"  {entry}")
        if stale and not errors:
            print(
                "\nEither the file is out of date, or this machine differs from the one\n"
                "it records. This script cannot tell which, and does not guess."
            )

    return {"PASS": 0, "STALE": 3, "FAIL": 1}[status]


if __name__ == "__main__":
    raise SystemExit(main())
