#!/usr/bin/env python3
"""Run multiple commands in a single PowerShell session and capture final result.

Why this script exists:
- Keep all steps in one shell process so env/cwd/session state is shared.
- Avoid long one-line command chaining.
- Give parent process stable stdout/stderr/exit code.

Examples:
    # Inline commands (order is preserved)
    .\\.venv\\Scripts\\python.exe .\\run_powershell_session.py \
        --command "Set-Location $Env:KAYAK" \
        --command ".\\src\\kayak\\scripts\\setup_kayak.ps1 --provisioning" \
        --command "$Env:KAYAK_PYTHONSV_MAIN=$Env:PythonSvRoot" \
        --command "$Env:KAYAK_PYTHONSV_LIBS=$Env:SitePackage" \
        --command "python -m kayak.domains.ras.patch_system_configuration" \
        --command "kayak .\\tests\\contents\\ras\\test_dmr_correctable.py::TestClassRasDmr::test_poison_pfd_permanent_uc --log-dir $PWD"

Notes:
- The script exits immediately when any command fails.
- Final process return code is the failing command's exit code.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path


class SessionRunError(Exception):
    """Raised when a session command fails."""


def build_powershell_script(commands: list[str]) -> str:
    if not commands:
        raise ValueError("No commands provided.")

    # Use strict mode and Stop behavior to make failures deterministic.
    parts: list[str] = [
        "$ErrorActionPreference = 'Stop'",
        "Set-StrictMode -Version Latest",
        "",
        "function Invoke-Step {",
        "    param([int]$StepNo, [string]$Command)",
        "",
        "    Write-Host (\"[STEP {0}] {1}\" -f $StepNo, $Command)",
        "",
        "    # Run each step in the same PowerShell process/session.",
        "    Invoke-Expression $Command",
        "",
        "    $lastExit = $null",
        "    $lastExitVar = Get-Variable -Name LASTEXITCODE -Scope Global -ErrorAction SilentlyContinue",
        "    if ($lastExitVar -ne $null) {",
        "        $lastExit = $lastExitVar.Value",
        "    }",
        "",
        "    if ($lastExit -ne $null -and $lastExit -ne 0) {",
        "        Write-Error (\"Step {0} failed with exit code {1}\" -f $StepNo, $lastExit)",
        "        exit $lastExit",
        "    }",
        "}",
        "",
    ]

    for idx, command in enumerate(commands, start=1):
        escaped = command.replace("'", "''")
        parts.append(f"Invoke-Step -StepNo {idx} -Command '{escaped}'")

    parts.append("")
    parts.append("exit 0")
    return "\n".join(parts)


def run_session(commands: list[str]) -> subprocess.CompletedProcess[str]:
    script_content = build_powershell_script(commands)

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".ps1",
        delete=False,
    ) as tmp:
        tmp.write(script_content)
        script_path = Path(tmp.name)

    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result
    finally:
        try:
            script_path.unlink(missing_ok=True)
        except OSError:
            # Best-effort cleanup only.
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run multiple commands in one PowerShell session and capture output/exit code."
    )
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        help="Command to run. Can be repeated; execution order is preserved.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    commands: list[str] = []
    commands.extend(args.command)

    if not commands:
        print("No commands provided. Use --command.", file=sys.stderr)
        return 2

    result = run_session(commands=commands)

    # Return full output to caller/CI/log collector.
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
