#!/usr/bin/env python3
"""Native runner: the five processes of docker-compose.yml without Docker.

It exists for machines where Docker is not available. The container file remains
the deployment of record; this script starts the same processes with the same
environment so that the experiments can be repeated anywhere.

    python smart/run_native.py up        edge broker, server broker, collector, gateway
    python smart/run_native.py stop
    python smart/run_native.py break     the server broker disappears (link down)
    python smart/run_native.py restore   the server broker comes back
    python smart/run_native.py clean     stop, then remove databases and boot counter
    python smart/run_native.py status

Environment: MQ (mosquitto binary), UPLINK_KBPS, BUFFER_MAX, SITE, ROOM.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LOGS = DATA / "logs"
RUN = DATA / "run"
REGISTRY = RUN / "processes.json"

PY = sys.executable
MQ = os.getenv("MQ", "mosquitto")
UPLINK_KBPS = os.getenv("UPLINK_KBPS", "1000")
BUFFER_MAX = os.getenv("BUFFER_MAX", "50000")
SITE = os.getenv("SITE", "stadium")
ROOM = os.getenv("ROOM", "pitch")


def broker_conf(port: int) -> Path:
    """The container configuration, adjusted for this build and this host."""
    src = (ROOT / "smart" / "broker" / "mosquitto.conf").read_text(encoding="utf-8")
    out = []
    for line in src.splitlines():
        if line.startswith("listener "):
            out.append(f"listener {port} 127.0.0.1")
            continue
        if line.startswith("tcp_nodelay"):
            continue            # added in Mosquitto 2.1, unknown to 2.0
        out.append(line.replace("/mosquitto/data/", str(DATA) + "/"))
    path = DATA / f"broker-{port}.conf"
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def wait_port(port: int, timeout: float = 15.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def load_registry() -> dict:
    try:
        return json.loads(REGISTRY.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_registry(reg: dict) -> None:
    RUN.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, indent=2))


def spawn(name: str, args: list[str], env: dict | None = None) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    fh = open(LOGS / f"{name}.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        args, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1", **(env or {})},
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
    reg = load_registry()
    reg[name] = proc.pid
    save_registry(reg)
    return proc.pid


def kill(name: str) -> None:
    reg = load_registry()
    pid = reg.pop(name, None)
    save_registry(reg)
    if pid is None:
        return
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass


def cmd_up() -> int:
    for port in (1883, 1884):
        try:
            socket.create_connection(("127.0.0.1", port), 0.3).close()
            print(f"port {port} is already in use; run `stop` first", file=sys.stderr)
            return 1
        except OSError:
            pass

    spawn("broker-edge", [MQ, "-c", str(broker_conf(1883))])
    spawn("broker-cloud", [MQ, "-c", str(broker_conf(1884))])
    if not (wait_port(1883) and wait_port(1884)):
        print("a broker did not start", file=sys.stderr)
        return 1

    spawn("collector", [PY, "smart/collector.py"],
          {"MQTT_HOST": "127.0.0.1", "MQTT_PORT": "1884", "QUIET": "1", "SITE": SITE})
    spawn("gateway", [PY, "smart/gateway.py"],
          {"EDGE_HOST": "127.0.0.1", "EDGE_PORT": "1883",
           "CLOUD_HOST": "127.0.0.1", "CLOUD_PORT": "1884", "SITE": SITE,
           "UPLINK_KBPS": UPLINK_KBPS, "BUFFER_MAX": BUFFER_MAX})
    time.sleep(2)
    print(f"up: edge:1883 cloud:1884 uplink={UPLINK_KBPS} kbit/s buffer={BUFFER_MAX}")
    return 0


def cmd_stop() -> int:
    for name in ("gateway", "collector", "broker-cloud", "broker-edge"):
        kill(name)
    print("stopped")
    return 0


def cmd_break() -> int:
    kill("broker-cloud")
    print("link down: the server broker is gone, the gateway keeps buffering")
    return 0


def cmd_restore() -> int:
    spawn("broker-cloud", [MQ, "-c", str(broker_conf(1884))])
    if wait_port(1884):
        print("link up")
        return 0
    print("the server broker did not come back", file=sys.stderr)
    return 1


def cmd_clean() -> int:
    cmd_stop()
    time.sleep(0.5)
    for pattern in ("*.db", "*.db-wal", "*.db-shm", "boot.txt", "gateway_stats.json"):
        for f in DATA.glob(pattern):
            try:
                f.unlink()
            except OSError:
                pass
    print("state cleared")
    return 0


def cmd_status() -> int:
    reg = load_registry()
    for name, pid in reg.items():
        try:
            os.kill(pid, 0)
            alive = "alive"
        except OSError:
            alive = "gone"
        print(f"{name:14} pid {pid:>7} {alive}")
    stats = {}
    try:
        stats = json.loads((DATA / "gateway_stats.json").read_text())
    except (OSError, json.JSONDecodeError):
        pass
    if stats:
        print("gateway:", json.dumps(stats))
    return 0


COMMANDS = {"up": cmd_up, "stop": cmd_stop, "break": cmd_break,
            "restore": cmd_restore, "clean": cmd_clean, "status": cmd_status}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd not in COMMANDS:
        print(__doc__)
        sys.exit(2)
    sys.exit(COMMANDS[cmd]())
