#!/usr/bin/env python3
"""Sensor emulator for the sports streaming platform (Lab 3, domain 8).

Three wearable channels of one athlete are emulated: pulse, speed and
acceleration, at HZ readings per second each. Every reading is published as
telemetry with QoS 0, because the loss of a single reading is recovered from the
sequence number and never worth a retransmission. Each sensor owns its MQTT
client, publishes a retained online state and registers an offline will.

Environment:
    SITE, ROOM          topic levels, default stadium / pitch
    ATHLETE             device prefix, default ath07
    MQTT_HOST, MQTT_PORT
    HZ                  readings per second per sensor, default 3
    DURATION            seconds to run, 0 = until Ctrl+C
    DROP, DUP, LATE, BAD  proportions of injected faults, 0..1

Fault semantics:
    DROP  the reading is not published, its sequence number is consumed
    DUP   the same reading is published twice
    LATE  the reading is published several seconds after it was measured
    BAD   the reading is published with a schema violation
"""

from __future__ import annotations

import json
import math
import os
import random
import signal
import zlib
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import paho.mqtt.client as mqtt

SITE = os.getenv("SITE", "stadium")
ROOM = os.getenv("ROOM", "pitch")
ATHLETE = os.getenv("ATHLETE", "ath07")
HOST = os.getenv("MQTT_HOST", "127.0.0.1")
PORT = int(os.getenv("MQTT_PORT", "1883"))
HZ = float(os.getenv("HZ", "3"))
DURATION = float(os.getenv("DURATION", "0"))
DROP = float(os.getenv("DROP", "0"))
DUP = float(os.getenv("DUP", "0"))
LATE = float(os.getenv("LATE", "0"))
BAD = float(os.getenv("BAD", "0"))

DATA = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))

# value ranges come from the wearable datasheet and the Lab 1 data register
SENSORS = [
    {"dev": f"{ATHLETE}-pulse", "qty": "pulse", "unit": "bpm",  "lo": 30.0, "hi": 220.0},
    {"dev": f"{ATHLETE}-speed", "qty": "speed", "unit": "m/s",  "lo": 0.0,  "hi": 12.0},
    {"dev": f"{ATHLETE}-acc",   "qty": "acc",   "unit": "m/s2", "lo": -16.0, "hi": 16.0},
]

COUNT = {"sent": 0, "dropped": 0, "dup": 0, "late": 0, "bad": 0}
TIMERS: list[threading.Timer] = []


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def next_boot() -> int:
    """The boot counter lives outside the repository, one line in data/boot.txt."""
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "boot.txt"
    boot = 0
    if path.exists():
        try:
            boot = int(path.read_text().strip() or 0)
        except ValueError:
            boot = 0
    boot += 1
    path.write_text(f"{boot}\n")
    return boot


# --------------------------------------------------------------------------- #
# value models: a warm-up, a rally pattern and sensor noise
# --------------------------------------------------------------------------- #
def value_pulse(t: float, rnd: random.Random) -> float:
    warm = 68 + 62 * (1 - math.exp(-t / 45.0))          # heart rate settles near 130
    effort = 9 * math.sin(2 * math.pi * t / 37.0)        # bursts of effort
    return max(30.0, min(220.0, warm + effort + rnd.gauss(0, 1.6)))


def value_speed(t: float, rnd: random.Random) -> float:
    sprint = 7.4 * max(0.0, math.sin(2 * math.pi * t / 23.0)) ** 1.6
    walk = 1.1 if math.sin(2 * math.pi * t / 61.0) > 0.2 else 0.0
    return max(0.0, min(12.0, sprint + walk + rnd.gauss(0, 0.25)))


def value_acc(t: float, rnd: random.Random) -> float:
    change = 3.4 * math.cos(2 * math.pi * t / 23.0)      # derivative of the sprint
    return max(-16.0, min(16.0, change + rnd.gauss(0, 0.9)))


MODEL = {"pulse": value_pulse, "speed": value_speed, "acc": value_acc}


class Sensor:
    def __init__(self, spec: dict, boot: int, seed: int):
        self.spec = spec
        self.boot = boot
        self.seq = 0
        self.rnd = random.Random(seed)
        self.topic = f"{SITE}/{ROOM}/{spec['dev']}/tele"
        self.state_topic = f"{SITE}/{ROOM}/{spec['dev']}/state"
        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"emu-{spec['dev']}-{boot}",
            protocol=mqtt.MQTTv311,
        )
        self.client.will_set(
            self.state_topic,
            json.dumps({"dev": spec["dev"], "boot": boot, "state": "offline", "ts": iso(time.time())}),
            qos=1,
            retain=True,
        )
        self.ready = threading.Event()
        self.client.on_connect = self.on_connect
        self.client.connect(HOST, PORT, keepalive=10)
        self.client.loop_start()
        if not self.ready.wait(timeout=5):
            raise RuntimeError(f"{spec['dev']} did not connect to {HOST}:{PORT}")
        self.client.publish(
            self.state_topic,
            json.dumps({"dev": spec["dev"], "boot": boot, "state": "online", "ts": iso(time.time())}),
            qos=1,
            retain=True,
        )

    def on_connect(self, client, userdata, flags, reason_code, properties):
        self.ready.set()

    def reading(self, t: float) -> dict:
        v = MODEL[self.spec["qty"]](t, self.rnd)
        if self.spec["qty"] == "pulse":
            v = round(v, 0)
        else:
            v = round(v, 3)
        return {
            "dev": self.spec["dev"],
            "boot": self.boot,
            "seq": self.seq,
            "ts": iso(time.time()),
            "qty": self.spec["qty"],
            "val": v,
            "unit": self.spec["unit"],
        }

    def publish(self, payload, delay: float = 0.0) -> None:
        body = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
        if delay > 0:
            # a late reading keeps its measurement timestamp, so its delivery
            # latency is larger than that of the readings published after it
            timer = threading.Timer(delay, self.client.publish, args=(self.topic, body, 0))
            timer.start()
            TIMERS.append(timer)
        else:
            self.client.publish(self.topic, body, qos=0)

    def corrupt(self, payload: dict):
        """One schema violation, chosen at random, for the subscriber check."""
        broken = dict(payload)
        kind = self.rnd.choice(["json", "unit", "range", "qty", "dev", "ts"])
        if kind == "json":
            return '{"dev":"%s","boot":%d,"seq":%d,"ts":"%s","qty":"%s","val":%s' % (
                payload["dev"], payload["boot"], payload["seq"], payload["ts"],
                payload["qty"], payload["val"])          # truncated, no closing brace
        if kind == "unit":
            broken["unit"] = "C"                       # wrong unit for this quantity
        elif kind == "range":
            broken["val"] = 900.0 if self.spec["qty"] != "acc" else 90.0
        elif kind == "qty":
            broken["qty"] = "co2"                      # quantity of another domain
        elif kind == "dev":
            broken["dev"] = "ath99-ghost"              # does not match the topic
        elif kind == "ts":
            broken["ts"] = "2026-11-19 08:41:20"       # not ISO 8601 UTC
        broken["_bad"] = kind
        return broken


def main() -> int:
    boot = next_boot()
    sensors = [Sensor(s, boot, seed=zlib.crc32(s["dev"].encode())) for s in SENSORS]
    print(f"boot={boot} HZ={HZ} sensors: {len(sensors)}", flush=True)
    for s in sensors:
        print(f"  {s.topic}", flush=True)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    period = 1.0 / HZ if HZ > 0 else 1.0
    started = time.monotonic()
    due = [started] * len(sensors)
    rnd = random.Random(1234)

    while not stop.is_set():
        now = time.monotonic()
        if DURATION and now - started >= DURATION:
            break
        for i, sensor in enumerate(sensors):
            while due[i] <= now:
                t = due[i] - started
                payload = sensor.reading(t)

                if rnd.random() < DROP:
                    COUNT["dropped"] += 1
                    sensor.seq += 1
                    due[i] += period
                    continue

                if rnd.random() < BAD:
                    sensor.publish(sensor.corrupt(payload))
                    COUNT["bad"] += 1
                elif rnd.random() < LATE:
                    sensor.publish(payload, delay=rnd.uniform(1.0, 3.0))
                    COUNT["late"] += 1
                else:
                    sensor.publish(payload)
                    COUNT["sent"] += 1
                    if rnd.random() < DUP:
                        sensor.publish(payload)
                        COUNT["dup"] += 1

                sensor.seq += 1
                due[i] += period
        time.sleep(0.0005)

    # let the late readings leave before the clients are closed
    for timer in TIMERS:
        timer.join(timeout=4.0)

    for s in sensors:
        s.client.publish(
            s.state_topic,
            json.dumps({"dev": s.spec["dev"], "boot": boot, "state": "offline", "ts": iso(time.time())}),
            qos=1,
            retain=True,
        )
    time.sleep(0.3)
    for s in sensors:
        s.client.loop_stop()
        s.client.disconnect()

    print(
        "Summary: boot={} sent={} dropped={} dup={} late={} bad={}".format(
            boot, COUNT["sent"], COUNT["dropped"], COUNT["dup"], COUNT["late"], COUNT["bad"]
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
