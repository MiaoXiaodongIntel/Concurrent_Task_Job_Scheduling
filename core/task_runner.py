from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


def _task_env() -> dict[str, str]:
    """Build a clean environment for task subprocesses.

    task_host is normally launched from the project's own virtual environment,
    so the inherited environment carries venv-activation artifacts
    (VIRTUAL_ENV, _OLD_VIRTUAL_PATH, ...) and the project's .venv\\Scripts on
    PATH.  When a task command activates *its own* venv (e.g. kayak's
    setup script doing `. .venv\\Scripts\\Activate.ps1`), that activation
    rebuilds PATH from the stale _OLD_VIRTUAL_PATH, silently dropping any PATH
    entries the task added (such as the poetry directory) and breaking tools
    like `poetry install`.
    
    """
    env = os.environ.copy()

    # Drop venv-activation artifacts inherited from the parent process.
    for var in (
        "VIRTUAL_ENV",
        "_OLD_VIRTUAL_PATH",
        "_OLD_VIRTUAL_PROMPT",
        "_OLD_VIRTUAL_PYTHONHOME",
    ):
        env.pop(var, None)

    # Remove the project's own venv Scripts/bin directories from PATH so a
    # task's own venv activation starts from a clean base.
    project_venv = str((Path(__file__).resolve().parent.parent / ".venv").resolve()).lower()
    path_entries = [
        entry
        for entry in env.get("PATH", "").split(os.pathsep)
        if entry and not entry.lower().startswith(project_venv)
    ]

    env["PATH"] = os.pathsep.join(path_entries)
    return env

@dataclass
class RunningTaskHandle:
    process: subprocess.Popen[str]
    script_path: Path


def build_powershell_script(commands: list[str]) -> str:
    if not commands:
        raise ValueError("No commands provided.")

    parts: list[str] = [
        "$ErrorActionPreference = 'Stop'",
        "Set-StrictMode -Version Latest",
        "",
        "function Invoke-Step {",
        "    param([int]$StepNo, [string]$Command)",
        "",
        "    Write-Host (\"[STEP {0}] {1}\" -f $StepNo, $Command)",
        "",
        "    # Auto-expose a local virtual environment (.venv) in the current",
        "    # directory so its console scripts (e.g. kayak) are runnable without",
        "    # a manual `. .venv\\Scripts\\Activate.ps1` step.",
        "    $venvScripts = Join-Path (Get-Location).Path '.venv\\Scripts'",
        "    if (Test-Path $venvScripts) {",
        "        $pathEntries = $env:PATH -split ';'",
        "        if ($pathEntries -notcontains $venvScripts) {",
        "            $env:PATH = \"$venvScripts;$env:PATH\"",
        "        }",
        "    }",
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


def _write_temp_script(commands: list[str]) -> Path:
    script_content = build_powershell_script(commands)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".ps1",
        delete=False,
    ) as tmp:
        tmp.write(script_content)
        return Path(tmp.name)


def run_session(commands: list[str]) -> subprocess.CompletedProcess[str]:
    script_path = _write_temp_script(commands)
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
            pass


class TaskRunner:
    """Runs one task job and returns a handle for lifecycle management."""

    def start_task(self, commands: list[str]) -> RunningTaskHandle:
        script_path = _write_temp_script(commands)
        try:
            proc = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script_path),
                ],
                env=_task_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError:
            try:
                script_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

        return RunningTaskHandle(process=proc, script_path=script_path)

    def cleanup(self, handle: RunningTaskHandle) -> None:
        try:
            handle.script_path.unlink(missing_ok=True)
        except OSError:
            pass
