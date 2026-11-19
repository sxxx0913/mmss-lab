#!/usr/bin/env python3
"""Subscriber and validator of the telemetry channel (Lab 3).

Every message is checked against the schema of docs/telemetry.md before it is
stored. A reading that passes goes into `readings`, where the primary key
(dev, boot, seq) makes the database itself discard duplicates. A reading that
fails goes into `rejected` with its reason: MQTT has no dead letter queue, so the
application provides one. Gaps, out-of-order readings and duplicates are written
to `anomalies`, each with the count that formula (1) gives.

Environment:
    MQTT_HOST, MQTT_PORT, SITE
    DB_PATH             default data/telemetry.db
    QUIET               1 = no periodic status line
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

SITE = os.getenv("SITE", "stadium")
HOST = os.getenv("MQTT_HOST", "127.0.0.1")
PORT = int(os.getenv("MQTT_PORT", "1883"))
DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).resolve().parents[1] / "data" / "telemetry.db"))
QUIET = os.getenv("QUIET", "0") == "1"

QTY = {
    "pulse": ("bpm", 30.0, 220.0),
    "speed": ("m/s", 0.0, 12.0),
    "acc": ("m/s2", -16.0, 16.0),
}
REQUIRED = ("dev", "boot", "seq", "ts", "qty", "val", "unit")

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS readings (
    dev       TEXT    NOT NULL,
    boot      INTEGER NOT NULL,
    seq       INTEGER NOT NULL,
    ts_ms     INTEGER NOT NULL,
    rx_ms     INTEGER NOT NULL,
    qty       TEXT    NOT NULL,
    val       REAL    NOT NULL,
    unit      TEXT    NOT NULL,
    latency_ms REAL   NOT NULL,
    PRIMARY KEY (dev, boot, seq)
);
CREATE INDEX IF NOT EXISTS readings_boot ON readings (boot, dev);
CREATE TABLE IF NOT EXISTS anomalies (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    dev     TEXT    NOT NULL,
    boot    INTEGER NOT NULL,
    seq     INTEGER NOT NULL,
    kind    TEXT    NOT NULL,
    missing INTEGER NOT NULL DEFAULT 0,
    rx_ms   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS rejected (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    topic   TEXT NOT NULL,
    payload TEXT NOT NULL,
    reason  TEXT NOT NULL,
    rx_ms   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS states (
    dev     TEXT    NOT NULL,
    boot    INTEGER NOT NULL,
    state   TEXT    NOT NULL,
    ts_ms   INTEGER NOT NULL,
    rx_ms   INTEGER NOT NULL
);
"""


def parse_ts(text: str) -> int:
    """ISO 8601 with milliseconds and a Z suffix, as the schema requires."""
    if not isinstance(text, str) or not text.endswith("Z"):
        raise ValueError("ts is not an ISO 8601 UTC string")
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("ts has no time zone")
    return int(dt.timestamp() * 1000)


def validate(topic: str, raw: str, now_ms: int) -> tuple[dict | None, str]:
    """Return (payload, "") when the message is valid, else (None, reason)."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return None, "not JSON"
    if not isinstance(msg, dict):
        return None, "not a JSON object"

    missing = [k for k in REQUIRED if k not in msg]
    if missing:
        return None, "missing fields: " + ",".join(missing)

    for key, typ in (("dev", str), ("boot", int), ("seq", int), ("qty", str), ("unit", str)):
        if not isinstance(msg[key], typ) or isinstance(msg[key], bool):
            return None, f"{key} is not {typ.__name__}"

    if not isinstance(msg["val"], (int, float)) or isinstance(msg["val"], bool):
        return None, "val is not a number"

    if msg["qty"] not in QTY:
        return None, f"unknown qty {msg['qty']}"

    unit, lo, hi = QTY[msg["qty"]]
    if msg["unit"] != unit:
        return None, f"unit {msg['unit']} does not belong to {msg['qty']}"
    if not (lo <= float(msg["val"]) <= hi):
        return None, f"val {msg['val']} outside the datasheet range {lo}..{hi}"

    parts = topic.split("/")
    if len(parts) < 4:
        return None, "topic has fewer than four levels"
    if parts[2] != msg["dev"]:
        return None, f"dev {msg['dev']} does not match the topic {parts[2]}"

    try:
        ts_ms = parse_ts(msg["ts"])
    except ValueError as exc:
        return None, str(exc)
    if abs(now_ms - ts_ms) > 24 * 3600 * 1000:
        return None, "ts is more than a day away from the receive time"

    msg["ts_ms"] = ts_ms
    return msg, ""


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.lock = threading.Lock()
        self.readings: list[tuple] = []
        self.anomalies: list[tuple] = []
        self.rejected: list[tuple] = []
        self.states: list[tuple] = []
        self.max_seq: dict[tuple[str, int], int] = {}
        self.seen: set[tuple[str, int, int]] = set()
        self.counters = {"rx": 0, "dup": 0, "gap": 0, "ooo": 0, "rejected": 0}

    def add_reading(self, msg: dict, rx_ms: int) -> None:
        key = (msg["dev"], msg["boot"], msg["seq"])
        if key in self.seen:
            self.anomalies.append((msg["dev"], msg["boot"], msg["seq"], "duplicate", 0, rx_ms))
            self.counters["dup"] += 1
            return
        self.seen.add(key)

        stream = (msg["dev"], msg["boot"])
        seq = msg["seq"]
        last = self.max_seq.get(stream)

        if last is None:
            if seq > 0:
                self.anomalies.append((msg["dev"], msg["boot"], seq, "gap", seq, rx_ms))
                self.counters["gap"] += seq
            self.max_seq[stream] = seq
        elif seq > last:
            missing = seq - last - 1
            if missing > 0:
                self.anomalies.append((msg["dev"], msg["boot"], seq, "gap", missing, rx_ms))
                self.counters["gap"] += missing
            self.max_seq[stream] = seq
        else:
            # below the highest number already seen and not a duplicate: the
            # reading was counted as missing by (1) and arrived afterwards
            self.anomalies.append((msg["dev"], msg["boot"], seq, "out_of_order", 0, rx_ms))
            self.counters["ooo"] += 1

        self.readings.append((
            msg["dev"], msg["boot"], seq, msg["ts_ms"], rx_ms,
            msg["qty"], float(msg["val"]), msg["unit"], rx_ms - msg["ts_ms"],
        ))

    def flush(self) -> None:
        with self.lock:
            if self.readings:
                self.db.executemany(
                    "INSERT OR IGNORE INTO readings "
                    "(dev,boot,seq,ts_ms,rx_ms,qty,val,unit,latency_ms) VALUES (?,?,?,?,?,?,?,?,?)",
                    self.readings,
                )
                self.readings.clear()
            if self.anomalies:
                self.db.executemany(
                    "INSERT INTO anomalies (dev,boot,seq,kind,missing,rx_ms) VALUES (?,?,?,?,?,?)",
                    self.anomalies,
                )
                self.anomalies.clear()
            if self.rejected:
                self.db.executemany(
                    "INSERT INTO rejected (topic,payload,reason,rx_ms) VALUES (?,?,?,?)",
                    self.rejected,
                )
                self.rejected.clear()
            if self.states:
                self.db.executemany(
                    "INSERT INTO states (dev,boot,state,ts_ms,rx_ms) VALUES (?,?,?,?,?)",
                    self.states,
                )
                self.states.clear()
            self.db.commit()


def main() -> int:
    store = Store(DB_PATH)
    stop = threading.Event()

    def on_connect(client, userdata, flags, reason_code, properties):
        client.subscribe(f"{SITE}/+/+/tele", qos=0)
        client.subscribe(f"{SITE}/+/+/state", qos=1)
        print(f"subscribed to {SITE}/+/+/tele and {SITE}/+/+/state on {HOST}:{PORT}", flush=True)

    def on_message(client, userdata, message):
        rx_ms = int(time.time() * 1000)
        store.counters["rx"] += 1
        raw = message.payload.decode("utf-8", errors="replace")

        if message.topic.endswith("/state"):
            try:
                st = json.loads(raw)
                store.states.append((st.get("dev", "?"), int(st.get("boot", 0)),
                                     st.get("state", "?"), parse_ts(st.get("ts", "")), rx_ms))
            except (json.JSONDecodeError, ValueError, TypeError):
                store.rejected.append((message.topic, raw, "state is not valid JSON", rx_ms))
                store.counters["rejected"] += 1
            return

        msg, reason = validate(message.topic, raw, rx_ms)
        if msg is None:
            store.rejected.append((message.topic, raw[:500], reason, rx_ms))
            store.counters["rejected"] += 1
            return
        store.add_reading(msg, rx_ms)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="collector", protocol=mqtt.MQTTv311)
    client.on_connect = on_connect
    client.on_message = on_message
    client.max_queued_messages_set(20000)
    client.connect(HOST, PORT, keepalive=10)
    client.loop_start()

    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    last_status = time.monotonic()
    while not stop.is_set():
        time.sleep(0.2)
        store.flush()
        if not QUIET and time.monotonic() - last_status > 5:
            last_status = time.monotonic()
            c = store.counters
            print(f"rx={c['rx']} gap={c['gap']} ooo={c['ooo']} dup={c['dup']} "
                  f"rejected={c['rejected']}", flush=True)

    client.loop_stop()
    client.disconnect()
    store.flush()
    print("collector stopped: " + " ".join(f"{k}={v}" for k, v in store.counters.items()), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
