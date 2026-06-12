from __future__ import annotations

import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, urlparse

from task_manager import TaskManager


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


class MonitorServer:
    def __init__(self, manager: TaskManager, host: str, port: int) -> None:
        self.manager = manager
        self.host = host
        self.port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    def start(self) -> None:
        manager = self.manager

        class Handler(BaseHTTPRequestHandler):
            def _write_json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0"))
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                if not raw:
                    return {}
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid json payload: {exc.msg}") from exc
                if not isinstance(payload, dict):
                    raise ValueError("json payload must be an object")
                return payload

            def log_message(self, _format: str, *_args: object) -> None:
                # Keep monitor endpoint noise out of main console logs.
                return

            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path
                query = parse_qs(parsed.query)

                if path == "/health":
                    self._write_json(200, manager.snapshot_health())
                    return

                if path == "/tasks":
                    self._write_json(200, {"tasks": manager.snapshot_tasks()})
                    return

                if path.startswith("/tasks/"):
                    suffix = path[len("/tasks/") :]
                    if suffix.endswith("/logs"):
                        task_id = suffix[: -len("/logs")]
                        try:
                            cursor = int(query.get("cursor", ["0"])[0])
                            limit = int(query.get("limit", ["200"])[0])
                        except ValueError:
                            self._write_json(400, {"error": "cursor and limit must be integers"})
                            return
                        data = manager.read_task_logs(task_id=task_id, cursor=cursor, limit=limit)
                        if data is None:
                            self._write_json(404, {"error": f"task not found: {task_id}"})
                        else:
                            self._write_json(200, data)
                        return

                    task_id = suffix
                    task = manager.snapshot_task(task_id)
                    if task is None:
                        self._write_json(404, {"error": f"task not found: {task_id}"})
                    else:
                        self._write_json(200, task)
                    return

                self._write_json(404, {"error": f"unknown endpoint: {path}"})

            def do_POST(self) -> None:
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/tasks/submit":
                    try:
                        payload = self._read_json()
                    except ValueError as exc:
                        self._write_json(400, {"error": str(exc)})
                        return

                    submit_mode_raw = payload.get("submit_mode", "append")
                    submit_mode = str(submit_mode_raw)
                    tasks_payload = payload.get("tasks")
                    host_state_before = manager.snapshot_health()["host_state"]
                    result = manager.submit_tasks(tasks_payload=tasks_payload, submit_mode=submit_mode)
                    host_state_after_expected = manager.snapshot_health()["host_state"]

                    status_code = 200 if result.get("accepted") else 400
                    response = {
                        "accepted": bool(result.get("accepted")),
                        "command": "submit_tasks",
                        "requested_at": now_iso(),
                        "host_state_before": host_state_before,
                        "host_state_after_expected": host_state_after_expected,
                        "message": result.get("message", ""),
                        "reason_code": result.get("reason_code", ""),
                        "affected_task_ids": result.get("accepted_task_ids", []),
                        "submit_mode": result.get("submit_mode", submit_mode),
                    }
                    self._write_json(status_code, response)
                    return

                control_map = {
                    "/control/start": "start",
                    "/control/graceful-stop": "graceful_stop",
                    "/control/force-stop": "force_stop",
                    "/control/rerun": "rerun",
                    "/control/shutdown": "shutdown",
                }
                command = control_map.get(path)
                if command is None:
                    self._write_json(404, {"error": f"unknown endpoint: {path}"})
                    return

                try:
                    payload = self._read_json()
                except ValueError as exc:
                    self._write_json(400, {"error": str(exc)})
                    return

                task_ids = payload.get("task_ids") if isinstance(payload, dict) else None
                if not isinstance(task_ids, list):
                    task_ids = []

                options: dict[str, Any] = {}
                if command == "shutdown":
                    options = {
                        "mode": payload.get("mode", "drain"),
                        "timeout_sec": payload.get("timeout_sec"),
                    }

                host_state_before = manager.snapshot_health()["host_state"]
                result = manager.control(command, task_ids, options=options)
                host_state_after_expected = manager.snapshot_health()["host_state"]

                response = {
                    "accepted": bool(result.get("accepted")),
                    "command": command,
                    "requested_at": now_iso(),
                    "host_state_before": host_state_before,
                    "host_state_after_expected": host_state_after_expected,
                    "message": result.get("message", "accepted" if result.get("accepted") else "ignored"),
                    "reason_code": result.get("reason_code", ""),
                    "affected_task_ids": result.get("affected_task_ids", []),
                }
                if "rejected_task_ids" in result:
                    response["rejected_task_ids"] = result["rejected_task_ids"]

                self._write_json(200 if result.get("accepted") else 400, response)

        self._httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        print(f"[MON] Monitor API serving at http://{self.host}:{self.port}")

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
