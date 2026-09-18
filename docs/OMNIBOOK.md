# Running B1 on the OmniBook (Windows 11 ARM64)

For the HP OmniBook 3 — Snapdragon X X1-26-100, 16 GB. Everything here also
applies to any Windows ARM64 machine.

## Read this first

**B1 has never been executed on Windows.** Every measurement in the README was
taken on linux-x86_64. That is a statement about evidence, not a prediction: the
code is standard-library Python with no POSIX-only calls, which was *audited*
rather than assumed — and the audit found two real Windows defects, both now
fixed:

| Defect | Why it would have failed | Fix |
|---|---|---|
| The Python consumer invoked `python3` | `python3` does not exist on a default Windows install | the harness now invokes the interpreter it is itself running (`{python}`), which is also the more correct check |
| Compiled consumers were built as `consumer` | Windows launches by extension, so an extensionless binary cannot be run | `.exe` is appended on `os.name == "nt"` |

Two found means the audit was worth doing. It does not mean there is not a
third. So the first thing to run is the thing that reports facts about your
machine rather than repeating claims about mine.

## Step 1 — what does this machine have?

```powershell
python tools\check_machine.py
```

Needs nothing: no cargo, no model server, no toolchains, no arguments. It reports
your interpreter version, architecture, RAM, which model servers answer, which of
the fourteen toolchains exist, and then names the next command for *your*
situation. It also tells you, on every run, that this platform is untested.

If `python` is not found: install Python 3.10 or newer — 3.10 is the floor
because `dataclass(slots=True)` is. python.org ships a native **ARM64**
installer; take that one rather than the x64 build, which would run under
emulation.

## Step 2 — a model server

B1 needs one only for the multi-model path. Everything else works without it,
and `verify_all.py` reports that row `PARTIAL` rather than `PASS` — it ran, with
one deterministic entrant, and "ran with no models" is a different claim from
"ran against competing models".

Two work with **no shim and no API key**, because both already speak
`/v1/responses`, which is the only wire protocol the Codex-derived backend has
([ADR-0002](decisions/ADR-0002-responses-wire-api.md)):

- **Ollama** — `ollama pull qwen3:4b`
- **LM Studio** — start its local server; B1 finds it on port 1234

**llama.cpp will not work yet.** Its server speaks chat-completions, and the
translation layer for that is specified but not built
([ADR-0009](decisions/ADR-0009-responses-shim-contract.md)). If llama.cpp is
your target, that is the blocker and nothing else in this repository is.

Whether Ollama and LM Studio ship Windows **ARM64** builds today is a question
about their releases, and I could not check it from where this was written —
outbound access to their download hosts is refused by this environment's network
policy. `check_machine.py` answers it from your machine by seeing whether
anything actually answers, which is better evidence than my guess either way.

## Step 3 — what holds here

```powershell
python tools\verify_all.py
```

Believe this over anything the README says, because the README describes a
different machine. Exit code 2 means nothing failed and something was not
observed — absent toolchains, no model server. `PARTIAL` is not a softer `PASS`.

```powershell
python tools\run_agent.py --approve
```

One task from prompt to a verified file on disk, with every gate printing. Then
the four refusals, each from a different layer:

```powershell
python tools\run_agent.py                            # nothing approved it
python tools\run_agent.py --approve --interfere      # the world moved
python tools\run_agent.py --approve --bad-answer     # verification eliminated it
python tools\run_agent.py --approve --forbidden-target   # the policy said no first
```

## What to expect from the hardware, honestly

**The Snapdragon X's NPU is not in this picture.** Neither Ollama nor LM Studio
routes these models through the Hexagon NPU; they run on the CPU cores. Nothing
in B1 can change that, and a 4B-class model on Snapdragon X CPU is usable rather
than fast. If someone later ships a Responses-API server that uses the NPU, B1
drives it with no changes — that is what the provider abstraction is for.

**16 GB is the constraint the resource ledger exists for.** It budgets against
~10 GiB for models and refuses anything that does not fit rather than letting
the machine swap. Notably it refuses `gpt-oss:20b` at a predicted 14.25 GiB — and
that model is Codex's *built-in default*, not merely an option, so B1 overrides
it explicitly rather than inheriting it by omission.

**Every size and speed figure in `b1_models/registry.py` is an estimate**, marked
`WORKING_ASSUMPTION` in the code. To replace them with measurements from this
machine:

```powershell
python tools\benchmark_model.py --provider ollama --model qwen3:4b
```

It prints a profile to paste into `candidate_profiles()`, and it refuses to
invent the fields it could not measure — a benchmark that filled a gap with a
plausible value would be worse than none, because the output looks measured
either way.

## Optional: the Rust peer

Without `cargo`, the Rust rows report `UNKNOWN` and the cross-language agreement
claims go unchecked rather than assumed. With it, `verify_all.py` races a Rust
process against a Python process through one commit gate and requires that
exactly one effect lands.

`rustup` supports `aarch64-pc-windows-msvc` and needs the MSVC build tools. This
is worth doing if you want the cross-language guarantees checked on your machine
and skippable if you do not.

## Where your data lives

Nothing durable is written unless you ask for it
([ADR-0003](decisions/ADR-0003-durable-memory.md)), and
[ADR-0011](decisions/ADR-0011-private-layer-and-publication.md) declares what
belongs in the per-user private layer rather than the repository — the journal
first among them, because it carries every task, target and payload you ever
hand B1.

`%B1_HOME%` is **declared and not yet implemented**: no code reads it, so today
the journal lives wherever the caller points it, which for the demos is a
temporary directory that is deleted on exit. If you want history kept, pass
`--workspace` to `run_agent.py` and know that you are choosing where it lands.

## If something breaks

`python tools\verify_all.py --json` is the machine-readable version, and every
individual verifier takes `--json` too. A failure names what it checked and what
it saw. If a Windows-specific defect turns up — a third one — the fix belongs in
the code rather than in a note telling you to work around it.
