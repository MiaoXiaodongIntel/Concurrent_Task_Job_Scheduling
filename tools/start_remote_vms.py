#!/usr/bin/env python3
"""Start multiple QEMU VMs on a remote host using SSH.

The script reads connection settings from remote_machine_template.json, connects to
the remote machine, and launches VMs with SSH port forwarding so each VM is
reachable via: ssh <vm_user>@<remote_ip> -p <forward_port>

Usage examples:
    Batch start 10 VMs:
        .\\.venv\\Scripts\\python.exe .\\start_remote_vms.py --count 10

    Batch stop VMs started by this script (remote command):
        ssh <remote_user>@<remote_ip> -p <remote_ssh_port> "pkill -f 'qemu-kvm.*-name vm[0-9][0-9]' || true; rm -f <vm_img_path>/instances/vm*.pid"
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import paramiko


class ConfigError(Exception):
    """Raised when the JSON config is invalid."""


@dataclass
class RemoteConfig:
    ip: str
    port: int
    user: str
    password: str


@dataclass
class VmConfig:
    vm_img_path: str
    vm_img_file_name: str
    vm_user: str
    vm_password: str


@dataclass
class AppConfig:
    remote: RemoteConfig
    vm: VmConfig


def _required_str(obj: dict[str, Any], key: str, section: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"Missing or empty '{section}.{key}' in config.")
    return value.strip()


def load_config(path: Path) -> AppConfig:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Invalid JSON in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Root of JSON config must be an object.")

    remote_raw = raw.get("remote")
    vm_raw = raw.get("vm")

    if not isinstance(remote_raw, dict):
        raise ConfigError("Config must contain object: remote")
    if not isinstance(vm_raw, dict):
        raise ConfigError("Config must contain object: vm")

    port_text = _required_str(remote_raw, "port", "remote")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ConfigError("remote.port must be an integer string.") from exc

    remote = RemoteConfig(
        ip=_required_str(remote_raw, "ip", "remote"),
        port=port,
        user=_required_str(remote_raw, "user", "remote"),
        password=_required_str(remote_raw, "password", "remote"),
    )

    vm = VmConfig(
        vm_img_path=_required_str(vm_raw, "vm_img_path", "vm"),
        vm_img_file_name=_required_str(vm_raw, "vm_img_file_name", "vm"),
        vm_user=_required_str(vm_raw, "vm_user", "vm"),
        vm_password=_required_str(vm_raw, "vm_password", "vm"),
    )

    return AppConfig(remote=remote, vm=vm)


def q(value: str) -> str:
    """Shell-quote a value for remote command composition."""
    return shlex.quote(value)


class RemoteHost:
    def __init__(self, cfg: RemoteConfig) -> None:
        self.cfg = cfg
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    def __enter__(self) -> "RemoteHost":
        self.client.connect(
            hostname=self.cfg.ip,
            port=self.cfg.port,
            username=self.cfg.user,
            password=self.cfg.password,
            look_for_keys=False,
            allow_agent=False,
            timeout=20,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
        self.client.close()

    def run(self, command: str, check: bool = True) -> tuple[int, str, str]:
        stdin, stdout, stderr = self.client.exec_command(command)
        stdin.close()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()
        code = stdout.channel.recv_exit_status()
        if check and code != 0:
            raise RuntimeError(
                f"Remote command failed (code {code}):\n{command}\nSTDOUT:\n{out}\nSTDERR:\n{err}"
            )
        return code, out, err


def ensure_remote_prerequisites(host: RemoteHost) -> None:
    cmd = "test -x /usr/libexec/qemu-kvm && command -v qemu-img >/dev/null 2>&1"
    host.run(cmd, check=True)


def start_vm(
    host: RemoteHost,
    vm_cfg: VmConfig,
    index: int,
    forward_port: int,
    memory_mb: int,
    cpus: int,
) -> dict[str, str | int]:
    base_dir = vm_cfg.vm_img_path.rstrip("/")
    base_img = f"{base_dir}/{vm_cfg.vm_img_file_name}"
    instances_dir = f"{base_dir}/instances"

    vm_name = f"vm{index:02d}"
    overlay = f"{instances_dir}/{vm_name}.qcow2"
    pidfile = f"{instances_dir}/{vm_name}.pid"
    serial_log = f"{instances_dir}/{vm_name}.serial.log"

    prep_cmd = (
        f"set -e; "
        f"mkdir -p {q(instances_dir)}; "
        f"test -f {q(base_img)}; "
        f"backing_fmt=$(qemu-img info --output=json {q(base_img)} 2>/dev/null | "
        f"sed -n 's/.*\"format\"[[:space:]]*:[[:space:]]*\"\\([^\"]*\\)\".*/\\1/p' | head -n1); "
        f"if [ -z \"$backing_fmt\" ]; then "
        f"backing_fmt=$(qemu-img info {q(base_img)} 2>/dev/null | sed -n 's/^file format: //p' | head -n1); "
        f"fi; "
        f"test -n \"$backing_fmt\"; "
        f"if [ ! -f {q(overlay)} ]; then "
        f"qemu-img create -f qcow2 -F \"$backing_fmt\" -b {q(base_img)} {q(overlay)} >/dev/null; "
        f"fi"
    )
    host.run(prep_cmd, check=True)

    start_cmd = (
        f"set -e; "
        f"if [ -f {q(pidfile)} ] && kill -0 \"$(cat {q(pidfile)})\" 2>/dev/null; then "
        f"echo already-running; exit 0; "
        f"fi; "
        f"set +e; "
        f"nohup /usr/libexec/qemu-kvm "
        f"-enable-kvm "
        f"-global kvm-apic.vapic=false "
        f"-name {q(vm_name)} "
        f"-m {memory_mb} "
        f"-cpu host "
        f"-smp {cpus} "
        f"-drive file={q(overlay)},if=virtio,format=qcow2 "
        f"-nic user,hostfwd=tcp::{forward_port}-:22 "
        f"-display none -daemonize -pidfile {q(pidfile)} "
        f">>{q(serial_log)} 2>&1; "
        f"launch_rc=$?; "
        f"set -e; "
        f"if [ $launch_rc -ne 0 ]; then "
        f"echo failed; "
        f"echo __QEMU_LOG_BEGIN__; "
        f"tail -n 80 {q(serial_log)} 2>/dev/null || true; "
        f"echo __QEMU_LOG_END__; "
        f"exit 1; "
        f"fi; "
        f"sleep 1; "
        f"if [ -f {q(pidfile)} ] && kill -0 \"$(cat {q(pidfile)})\" 2>/dev/null; then "
        f"echo started; "
        f"else "
        f"echo failed; "
        f"echo __QEMU_LOG_BEGIN__; "
        f"tail -n 80 {q(serial_log)} 2>/dev/null || true; "
        f"echo __QEMU_LOG_END__; "
        f"exit 1; "
        f"fi"
    )
    _, out, _ = host.run(start_cmd, check=True)

    status = "started"
    if "already-running" in out:
        status = "already-running"

    return {
        "name": vm_name,
        "forward_port": forward_port,
        "status": status,
        "overlay": overlay,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch VMs on a remote host using QEMU and SSH port forwarding."
    )
    parser.add_argument(
        "--config",
        default="remote_machine.json",
        help="Path to config JSON file (default: remote_machine.json)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of VMs to start (default: 10)",
    )
    parser.add_argument(
        "--start-forward-port",
        type=int,
        default=2201,
        help="Starting host forward port on remote machine (default: 2201)",
    )
    parser.add_argument(
        "--memory-mb",
        type=int,
        default=2048,
        help="Memory per VM in MB (default: 2048)",
    )
    parser.add_argument(
        "--cpus",
        type=int,
        default=2,
        help="vCPUs per VM (default: 2)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned SSH endpoints only; do not start VMs.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.count <= 0:
        print("--count must be > 0", file=sys.stderr)
        return 2
    if args.start_forward_port <= 0:
        print("--start-forward-port must be > 0", file=sys.stderr)
        return 2

    try:
        cfg = load_config(Path(args.config))
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    print(f"Remote host: {cfg.remote.user}@{cfg.remote.ip}:{cfg.remote.port}")
    print(f"Base image: {cfg.vm.vm_img_path.rstrip('/')}/{cfg.vm.vm_img_file_name}")
    print(f"VM count: {args.count}")
    print("")

    if args.dry_run:
        for idx in range(1, args.count + 1):
            port = args.start_forward_port + idx - 1
            print(
                f"vm{idx:02d}: ssh {cfg.vm.vm_user}@{cfg.remote.ip} -p {port} "
                f"(password in config: vm.vm_password)"
            )
        return 0

    try:
        with RemoteHost(cfg.remote) as host:
            ensure_remote_prerequisites(host)
            results: list[dict[str, str | int]] = []
            for idx in range(1, args.count + 1):
                port = args.start_forward_port + idx - 1
                result = start_vm(
                    host=host,
                    vm_cfg=cfg.vm,
                    index=idx,
                    forward_port=port,
                    memory_mb=args.memory_mb,
                    cpus=args.cpus,
                )
                results.append(result)
                print(
                    f"[{result['status']}] {result['name']}: "
                    f"ssh {cfg.vm.vm_user}@{cfg.remote.ip} -p {result['forward_port']}"
                )
    except Exception as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
