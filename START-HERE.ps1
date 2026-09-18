# B1 Local - one command to get running on Windows.
#
#   .\START-HERE.ps1
#
# Checks your machine, runs the verifiers, then runs one real task end to end.
# Needs nothing but Python 3.10+. Does not need a model, cargo, or the network.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Find-Python {
    # `python3` does not exist on a default Windows install. `py` is the
    # launcher that ships with python.org installs and is the most reliable.
    foreach ($candidate in @("py", "python", "python3")) {
        $found = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($found) {
            $version = & $candidate -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $version) {
                $parts = $version.Split('.')
                if ([int]$parts[0] -ge 3 -and [int]$parts[1] -ge 10) {
                    return $candidate
                }
                Write-Host "  $candidate is Python $version - B1 needs 3.10 or newer" -ForegroundColor Yellow
            }
        }
    }
    return $null
}

Write-Host ""
Write-Host "B1 Local" -ForegroundColor Cyan
Write-Host "========"

$python = Find-Python
if (-not $python) {
    Write-Host ""
    Write-Host "No usable Python found." -ForegroundColor Red
    Write-Host ""
    Write-Host "Install Python 3.10 or newer from python.org. On this machine take the"
    Write-Host "ARM64 installer, not the x64 one - the x64 build runs under emulation."
    Write-Host "Tick 'Add python.exe to PATH' during install, then run this again."
    exit 1
}
Write-Host "  using: $python"

Write-Host ""
Write-Host "[1/3] What can this machine run?" -ForegroundColor Cyan
& $python tools\check_machine.py

Write-Host ""
Write-Host "[2/3] What actually holds here?" -ForegroundColor Cyan
Write-Host "      Exit code 2 is normal: nothing failed, something was not observed."
& $python tools\verify_all.py
$verifyCode = $LASTEXITCODE

Write-Host ""
Write-Host "[3/3] One real task, all the way through." -ForegroundColor Cyan
& $python tools\run_agent.py --approve

Write-Host ""
Write-Host "Done." -ForegroundColor Green
if ($verifyCode -eq 1) {
    Write-Host "Something FAILED above. That is a real defect on this machine -" -ForegroundColor Red
    Write-Host "re-run 'py tools\verify_all.py' and read which row said FAIL."
} elseif ($verifyCode -eq 2) {
    Write-Host "Nothing failed. Rows marked PARTIAL were not fully observed here"
    Write-Host "(a missing toolchain, or no model server) - which is reported, not hidden."
}
Write-Host ""
Write-Host "Next, if you want the multi-model path:"
Write-Host "  1. Install Ollama          https://ollama.com/download"
Write-Host "  2. ollama pull qwen3:4b    (about 2.5 GB, fits 16 GB comfortably)"
Write-Host "  3. $python tools\run_tournament.py --probe"
Write-Host ""
Write-Host "Windows specifics and what the Snapdragon NPU will not do:"
Write-Host "  docs\OMNIBOOK.md"
Write-Host ""
