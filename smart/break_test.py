#!/usr/bin/env python3
"""Link break experiment on the container deployment.

The link between the edge and the server is broken the way the instructions
describe it: the gateway container is disconnected from the cloud network, so
its socket to the server broker dies while both brokers keep running. After
60 s the interface is attached again and the buffer drains.

    python smart/break_test.py

The numbers of the run are written to data/break.json and printed at the end.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
COMPOSE = ["docker", "compose", "-f", "smart/docker-compose.yml"]
GW = "mmss-lab3-gateway-1"
STATS = PROJ / "data" / "gateway_stats.json"
BREAK_SECONDS = 60


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=PROJ, capture_output=True, text=True, **kw)


def stats() -> dict:
    try:
        return json.loads(STATS.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def clear_data() -> None:
    for name in ("telemetry.db", "telemetry.db-wal", "telemetry.db-shm",
                 "gateway.db", "gateway.db-wal", "gateway.db-shm",
                 "boot.txt", "gateway_stats.json"):
        (PROJ / "data" / name).unlink(missing_ok=True)


def main() -> None:
    run(COMPOSE + ["down", "-v"])
    clear_data()
    run(COMPOSE + ["up", "-d", "broker-edge", "broker-cloud", "collector", "gateway"])
    time.sleep(6)

    env = {**os.environ, "HZ": "100", "DURATION": str(BREAK_SECONDS + 70)}
    run(COMPOSE + ["up", "-d", "--no-deps", "--force-recreate", "sensors"], env=env)
    print("300 messages/s running", flush=True)
    time.sleep(10)

    t_break = time.time()
    run(["docker", "network", "disconnect", "mmss-lab3_cloud", GW])
    detected = None
    for i in range(1, 41):
        time.sleep(1)
        if stats().get("link") is False:
            detected = i
            break
    print(f"link=no detected after {detected}s", flush=True)

    time.sleep(BREAK_SECONDS - detected if detected else BREAK_SECONDS)
    before = stats()
    t_restore = time.time()
    run(["docker", "network", "connect", "mmss-lab3_cloud", GW])
    print("at restore:", before, flush=True)

    drained = None
    for i in range(1, 241):
        time.sleep(1)
        if stats().get("buffer") == 0:
            drained = i
            break
    print(f"buffer empty {drained}s after restore", flush=True)

    time.sleep(3)
    print("final:", stats(), flush=True)
    out = {
        "break_seconds": BREAK_SECONDS,
        "detected_s": detected,
        "restore_to_empty_s": drained,
        "buffer_at_restore": before.get("buffer"),
        "peak": before.get("buffer_peak"),
    }
    (PROJ / "data" / "break.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2), flush=True)
    print(run([sys.executable, "smart/stats.py"]).stdout, flush=True)
    run(COMPOSE + ["down", "-v"])


if __name__ == "__main__":
    main()
