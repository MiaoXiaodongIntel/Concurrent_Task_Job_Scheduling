"""Shared helpers and host process management for e2e tests.

All e2e test modules import from this file directly (sys.path is set by each
test module so that `from conftest import ...` works both when run as a script
and via pytest).
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

# Prefer venv Python; fall back to current interpreter.
_VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
PYTHON = str(_VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable


# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------

def free_port() -> int:
    """Return a free TCP port on loopback."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def http_get(port: int, path: str, timeout: float = 5) -> dict:
    url = f"http://127.0.0.1:{port}{path}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def http_post(port: int, path: str, body: dict | None = None, timeout: float = 5) -> dict:
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Wait helpers
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = {"succeeded", "failed", "aborted"}


def wait_for_server(port: int, timeout: float = 15.0) -> bool:
    """Return True when /health responds, False on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            http_get(port, "/health")
            return True
        except Exception:
            time.sleep(0.3)
    return False


def wait_for_all_done(port: int, timeout: float = 30.0) -> list | None:
    """Poll until every task is in a terminal state; return task list or None on timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = http_get(port, "/tasks")
            tasks = data.get("tasks", [])
            if tasks and all(t["status"] in TERMINAL_STATUSES for t in tasks):
                return tasks
        except Exception:
            pass
        time.sleep(0.5)
    return None


def wait_for_host_state(port: int, state: str, timeout: float = 15.0) -> bool:
    """Poll until host_state matches the given value."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            health = http_get(port, "/health")
            if health.get("host_state") == state:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def wait_for_task_state(port: int, task_id: str, state: str, timeout: float = 15.0) -> bool:
    """Poll until a specific task reaches the given state."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            data = http_get(port, f"/tasks/{task_id}")
            if data.get("status") == state:
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


# ---------------------------------------------------------------------------
# Host process context manager
# ---------------------------------------------------------------------------

class HostProcess:
    """Context manager that starts task_host.py and shuts it down on exit.

    Usage::

        with HostProcess(tasks_file=FIXTURES / "sample_tasks.json") as port:
            http_get(port, "/health")
    """

    def __init__(
        self,
        tasks_file: Path | None = None,
        resources_file: Path | None = None,
        registry_file: Path | None = None,
        max_concurrency: int = 2,
        extra_args: list[str] | None = None,
    ) -> None:
        self._tasks_file = tasks_file
        self._resources_file = resources_file
        self._registry_file = registry_file
        self._max_concurrency = max_concurrency
        self._extra_args = extra_args or []
        self._port: int = free_port()
        self._proc: subprocess.Popen | None = None

    @property
    def port(self) -> int:
        return self._port

    def __enter__(self) -> int:
        cmd = [
            PYTHON, str(ROOT / "core" / "task_host.py"),
            "--monitor-port", str(self._port),
            "--max-concurrency", str(self._max_concurrency),
            "--log-dir", str(ROOT / "logs"),
        ]
        if self._tasks_file:
            cmd += ["--tasks-file", str(self._tasks_file)]
        if self._resources_file:
            cmd += ["--resources-file", str(self._resources_file)]
        if self._registry_file:
            cmd += ["--registry-file", str(self._registry_file)]
        cmd += self._extra_args

        self._proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self._port

    def __exit__(self, *_) -> None:
        if self._proc is None:
            return
        try:
            http_post(self._port, "/control/shutdown", {"mode": "force"})
        except Exception:
            pass
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
