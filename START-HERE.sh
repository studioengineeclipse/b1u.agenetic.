#!/usr/bin/env bash
# B1 Local - one command to get running on Linux or macOS.
#
#   ./START-HERE.sh
#
# Checks your machine, runs the verifiers, then runs one real task end to end.
# Needs nothing but Python 3.10+. Does not need a model, cargo, or the network.
set -uo pipefail
cd "$(dirname "$0")"

find_python() {
    for candidate in python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)' 2>/dev/null; then
                echo "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

printf '\nB1 Local\n========\n'

if ! PYTHON="$(find_python)"; then
    printf '\nNo Python 3.10 or newer found. Install one and run this again.\n\n'
    exit 1
fi
printf '  using: %s\n' "$PYTHON"

printf '\n[1/3] What can this machine run?\n'
"$PYTHON" tools/check_machine.py

printf '\n[2/3] What actually holds here?\n'
printf '      Exit code 2 is normal: nothing failed, something was not observed.\n'
"$PYTHON" tools/verify_all.py
verify_code=$?

printf '\n[3/3] One real task, all the way through.\n'
"$PYTHON" tools/run_agent.py --approve

printf '\nDone.\n'
if [ "$verify_code" -eq 1 ]; then
    printf 'Something FAILED above. Re-run "%s tools/verify_all.py" and read which row.\n' "$PYTHON"
elif [ "$verify_code" -eq 2 ]; then
    printf 'Nothing failed. PARTIAL rows were not fully observed here, which is reported.\n'
fi
printf '\nNext, for the multi-model path:\n'
printf '  1. Install Ollama         https://ollama.com/download\n'
printf '  2. ollama pull qwen3:4b   (about 2.5 GB)\n'
printf '  3. %s tools/run_tournament.py --probe\n\n' "$PYTHON"
