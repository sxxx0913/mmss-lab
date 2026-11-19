#!/usr/bin/env python3
"""Gateway of the edge node: store-and-forward buffer between the two brokers.

Every telemetry message the edge broker delivers is written to an SQLite outbox
before anything else happens. A sender thread publishes the oldest records to the
server broker with QoS 1 and deletes a record only after its PUBACK arrives, so a
link break leaves the history in the buffer and the upload resumes when the link
returns. The link bandwidth is modelled by UPLINK_KBPS with a token bucket: the
throughput of one handler is mu1 = R / (8 * s) from formula (5).

When the buffer is full the oldest records are discarded, which is the graceful
degradation of the buffering pattern: the newest readings survive.

Environment:
    EDGE_HOST, EDGE_PORT      default 127.0.0.1:1883
    CLOUD_HOST, CLOUD_PORT    default 127.0.0.1:1884
    SITE                      default stadium
    UPLINK_KBPS               default 1000
    BUFFER_MAX                default 50000 records
    DB_PATH, STATS_PATH       default data/gateway.db, data/gateway_stats.json
    INFLIGHT                  default 40 records in flight
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

EDGE_HOST = os.getenv("EDGE_HOST", "127.0.0.1")
EDGE_PORT = int(os.getenv("EDGE_PORT", "1883"))
CLOUD_HOST = os.getenv("CLOUD_HOST", "127.0.0.1")
CLOUD_PORT = int(os.getenv("CLOUD_PORT", "1884"))
SITE = os.getenv("SITE", "stadium")
UPLINK_KBPS = float(os.getenv("UPLINK_KBPS", "1000"))
BUFFER_MAX = int(os.getenv("BUFFER_MAX", "50000"))
INFLIGHT = int(os.getenv("INFLIGHT", "40"))
# One commit per record costs an fsync per record. On a local disk that is
# affordable, on a bind mount of a Windows directory it caps the gateway at about
# 600 records/s, which is below the uplink and would be measured instead of the
# architecture. The outbox therefore commits in batches: the durability window is
# the batch, and the ADR already accepts the same window for the last transaction
# after a power loss.
COMMIT_EVERY = int(os.getenv("COMMIT_EVERY", "50"))
COMMIT_MS = float(os.getenv("COMMIT_MS", "20"))

DATA = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DB_PATH = Path(os.getenv("DB_PATH", DATA / "gateway.db"))
STATS_PATH = Path(os.getenv("STATS_PATH", DATA / "gateway_stats.json"))

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS outbox (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    topic   TEXT    NOT NULL,
    payload BLOB    NOT NULL,
    ts_ms   INTEGER NOT NULL
);
"""


class Gateway:
    def __init__(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.lock = threading.Lock()

        self.buffer = 0
        self.pending = 0                       # inserts not yet committed
        self.last_commit = time.monotonic()
        self.rx_edge = 0
        self.peak = 0
        self.dropped = 0
        self.forwarded = 0
        self.acked = 0
        self.link = False
        self.stop = threading.Event()
        self.wake = threading.Event()   # set when a record arrives, so the sender does not poll

        self.inflight: dict[int, int] = {}
        self.ack_queue: list[int] = []
        self.cursor = 0        # the highest record already handed to the sender
        self.inflight_lock = threading.Lock()

        # token bucket for the uplink: UPLINK_KBPS kilobit per second
        self.budget = 0.0
        self.rate = UPLINK_KBPS * 1000.0 / 8.0     # bytes per second
        self.last_fill = time.monotonic()

        self.edge = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="gateway-edge",
                                protocol=mqtt.MQTTv311)
        self.edge.on_connect = self.on_edge_connect
        self.edge.on_message = self.on_edge_message
        self.edge.max_queued_messages_set(50000)

        self.cloud = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="gateway-cloud",
                                 protocol=mqtt.MQTTv311)
        self.cloud.on_connect = self.on_cloud_connect
        self.cloud.on_disconnect = self.on_cloud_disconnect
        self.cloud.on_publish = self.on_publish
        self.cloud.max_inflight_messages_set(INFLIGHT)
        self.cloud.max_queued_messages_set(20000)

    # ------------------------------------------------------------------ edge
    def on_edge_connect(self, client, userdata, flags, reason_code, properties):
        # QoS 0 on the edge hop, QoS 1 on the uplink. The edge hop is the one the
        # sensors already publish to at QoS 0, and a reading lost there is visible
        # as a gap in the sequence rather than as a broken guarantee. Acknowledging
        # every record twice costs a PUBACK per record and, on the room node, about
        # half of the rate at which the gateway can take records in.
        client.subscribe(f"{SITE}/+/+/tele", qos=0)
        print(f"edge connected, subscribed to {SITE}/+/+/tele", flush=True)

    def on_edge_message(self, client, userdata, message):
        # one malformed record must not stop the channel: the exception is
        # reported and the next message is handled normally
        try:
            self._store(message)
        except Exception as exc:                      # noqa: BLE001
            print(f"edge message dropped: {exc!r}", flush=True)

    def _store(self, message):
        now = int(time.time() * 1000)
        with self.lock:
            self.db.execute(
                "INSERT INTO outbox (topic, payload, ts_ms) VALUES (?,?,?)",
                (message.topic, bytes(message.payload), now),
            )
            self.pending += 1
            self.buffer += 1
            self.rx_edge += 1
            self.peak = max(self.peak, self.buffer)
            if self.buffer > BUFFER_MAX:
                excess = self.buffer - BUFFER_MAX
                self.db.execute(
                    "DELETE FROM outbox WHERE id IN (SELECT id FROM outbox ORDER BY id LIMIT ?)",
                    (excess,),
                )
                self.db.commit()
                self.pending = 0
                self.last_commit = time.monotonic()
                self.buffer -= excess
            elif (self.pending >= COMMIT_EVERY
                  or (time.monotonic() - self.last_commit) * 1000.0 >= COMMIT_MS):
                self.db.commit()
                self.pending = 0
                self.last_commit = time.monotonic()
        self.wake.set()

    # ----------------------------------------------------------------- cloud
    def on_cloud_connect(self, client, userdata, flags, reason_code, properties):
        self.link = True
        print("cloud connected: link=yes", flush=True)

    def on_cloud_disconnect(self, client, userdata, flags, reason_code, properties):
        self.link = False
        print("cloud disconnected: link=no", flush=True)

    def on_publish(self, client, userdata, mid, reason_code, properties):
        with self.inflight_lock:
            row = self.inflight.pop(mid, None)
        if row is not None:
            self.ack_queue.append(row)

    # ---------------------------------------------------------------- sender
    def take(self, limit: int) -> list[tuple[int, str, bytes]]:
        """Records not yet handed to the sender.

        The cursor advances with every batch, so a record waiting for its PUBACK
        is never read a second time. Without it the same record is republished on
        every loop iteration until the acknowledgement arrives, and the server
        sees each reading several times.
        """
        with self.lock:
            rows = self.db.execute(
                "SELECT id, topic, payload FROM outbox WHERE id > ? ORDER BY id LIMIT ?",
                (self.cursor, limit),
            ).fetchall()
        if rows:
            self.cursor = rows[-1][0]
        return rows

    def sender(self) -> None:
        while not self.stop.is_set():
            if not self.link:
                time.sleep(0.2)
                continue

            with self.inflight_lock:
                room = INFLIGHT - len(self.inflight)
            if room <= 0:
                time.sleep(0.005)
                continue

            rows = self.take(min(room, 64))
            if not rows:
                self.flush_acks()
                self.wake.wait(0.05)
                self.wake.clear()
                continue

            try:
                self._send_batch(rows)
            except Exception as exc:                      # noqa: BLE001
                print(f"sender error: {exc!r}", flush=True)
                time.sleep(0.2)
            self.flush_acks()

    def _send_batch(self, rows) -> None:
        for row_id, topic, payload in rows:
            size = len(topic) + len(payload) + 12
            # token bucket: the uplink carries UPLINK_KBPS kilobit per second
            while True:
                now = time.monotonic()
                self.budget = min(self.rate, self.budget + (now - self.last_fill) * self.rate)
                self.last_fill = now
                if self.budget >= size:
                    self.budget -= size
                    break
                time.sleep(max(0.001, (size - self.budget) / self.rate))

            info = self.cloud.publish(topic, payload, qos=1)
            with self.inflight_lock:
                self.inflight[info.mid] = row_id
            self.forwarded += 1

    def flush_acks(self) -> None:
        with self.inflight_lock:
            if not self.ack_queue:
                return
            ids, self.ack_queue = self.ack_queue, []
        with self.lock:
            self.db.executemany("DELETE FROM outbox WHERE id = ?", [(i,) for i in ids])
            self.db.commit()
            self.pending = 0
            self.last_commit = time.monotonic()
            self.buffer -= len(ids)
            self.acked += len(ids)

    # ----------------------------------------------------------------- stats
    def stats_writer(self) -> None:
        while not self.stop.is_set():
            with self.lock:
                # the counter is rebuilt from the table: a record dropped by the
                # overflow rule may still be in flight, and deleting it later
                # would otherwise drive the counter below zero
                self.buffer = self.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
                # records that entered the buffer and left it without an
                # acknowledgement: the ones discarded by the overflow rule. A
                # record that was published but not yet acknowledged is still in
                # the buffer, so it is not counted here.
                self.dropped = max(0, self.rx_edge - self.acked - self.buffer)
                snap = {
                    "buffer": self.buffer,
                    "buffer_peak": self.peak,
                    "dropped": self.dropped,
                    "forwarded": self.forwarded,
                    "acked": self.acked,
                    "rx_edge": self.rx_edge,
                    "link": self.link,
                    "uplink_kbps": UPLINK_KBPS,
                }
            STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATS_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(snap))
            tmp.replace(STATS_PATH)
            print(f"buffer={snap['buffer']} dropped={snap['dropped']} "
                  f"link={'yes' if snap['link'] else 'no'} rx={snap['rx_edge']} "
                  f"tx={snap['acked']}", flush=True)
            time.sleep(1.0)

    def run(self) -> int:
        self.edge.connect(EDGE_HOST, EDGE_PORT, keepalive=10)
        self.edge.loop_start()
        self.cloud.reconnect_delay_set(min_delay=1, max_delay=2)
        try:
            self.cloud.connect(CLOUD_HOST, CLOUD_PORT, keepalive=10)
        except OSError:
            print("cloud broker is not reachable yet, retrying in the background", flush=True)
        self.cloud.loop_start()

        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())

        threading.Thread(target=self.sender, daemon=True).start()
        threading.Thread(target=self.stats_writer, daemon=True).start()

        while not self.stop.is_set():
            time.sleep(0.2)

        self.stop.set()
        time.sleep(0.5)
        self.edge.loop_stop()
        self.cloud.loop_stop()
        print(f"gateway stopped: forwarded={self.forwarded} acked={self.acked} "
              f"dropped={self.dropped} peak={self.peak}", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(Gateway().run())
