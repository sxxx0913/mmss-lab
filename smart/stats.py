#!/usr/bin/env python3
"""Summary of the telemetry channel (Lab 3).

For every boot of the emulator the script reports the message rate, the number
sent and received, the loss ratio of formula (2), the duplicates and
out-of-order readings found by formula (1), and the mean, 95th percentile and
maximum delivery latency of formula (3).

    python smart/stats.py                       # table for every boot in the database
    python smart/stats.py --boot 3              # one boot only
    python smart/stats.py --csv results/load.csv --step 4 --target 400
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
from pathlib import Path

DATA = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
DEFAULT_DB = DATA / "telemetry.db"


def quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return ordered[idx]


def boot_summary(db: sqlite3.Connection, boot: int) -> dict:
    rows = db.execute(
        "SELECT dev, MIN(seq), MAX(seq), COUNT(*) FROM readings WHERE boot = ? GROUP BY dev", (boot,)
    ).fetchall()

    sent = 0
    received = 0
    for _dev, first, last, count in rows:
        sent += (last - first + 1)          # Ntx from the sequence numbers, formula (2)
        received += count                   # Nrx, duplicates already removed by the key

    lat = [r[0] for r in db.execute("SELECT latency_ms FROM readings WHERE boot = ?", (boot,))]
    rx = db.execute("SELECT MIN(rx_ms), MAX(rx_ms) FROM readings WHERE boot = ?", (boot,)).fetchone()
    window = max(0.001, (rx[1] - rx[0]) / 1000.0) if rx[0] else 0.001

    dup = db.execute(
        "SELECT COUNT(*) FROM anomalies WHERE boot = ? AND kind = 'duplicate'", (boot,)
    ).fetchone()[0]
    ooo = db.execute(
        "SELECT COUNT(*) FROM anomalies WHERE boot = ? AND kind = 'out_of_order'", (boot,)
    ).fetchone()[0]
    gap = db.execute(
        "SELECT COALESCE(SUM(missing),0) FROM anomalies WHERE boot = ? AND kind = 'gap'", (boot,)
    ).fetchone()[0]
    rejected = db.execute(
        "SELECT COUNT(*) FROM rejected WHERE rx_ms BETWEEN ? AND ?",
        (rx[0], rx[1] + 3000),
    ).fetchone()[0]

    return {
        "boot": boot,
        "lambda_msg_s": round(received / window, 1),
        "sent": sent,
        "received": received,
        "loss_ratio": round(1 - received / sent, 4) if sent else 0.0,
        "gaps": gap,
        "duplicates": dup,
        "out_of_order": ooo,
        "rejected": rejected,
        "lat_mean_ms": round(statistics.fmean(lat), 2) if lat else 0.0,
        "lat_p95_ms": round(quantile(lat, 0.95), 2),
        "lat_max_ms": round(max(lat), 2) if lat else 0.0,
        "window_s": round(window, 1),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="telemetry channel summary")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--boot", type=int, default=None)
    ap.add_argument("--csv", default=None, help="append one row per boot to this CSV")
    ap.add_argument("--step", type=int, default=0)
    ap.add_argument("--target", type=int, default=0)
    ap.add_argument("--gateway", default=str(DATA / "gateway_stats.json"))
    args = ap.parse_args(argv)

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"database {db_path} does not exist", file=sys.stderr)
        return 1

    db = sqlite3.connect(db_path)
    boots = [r[0] for r in db.execute("SELECT DISTINCT boot FROM readings ORDER BY boot")]
    if args.boot is not None:
        boots = [b for b in boots if b == args.boot]
    if not boots:
        print("no readings in the database", file=sys.stderr)
        return 1

    summaries = [boot_summary(db, b) for b in boots]

    if args.csv:
        gw = {}
        try:
            gw = json.loads(Path(args.gateway).read_text())
        except (OSError, json.JSONDecodeError):
            pass
        row = summaries[-1]
        out = Path(args.csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        new = not out.exists()
        with out.open("a", encoding="utf-8") as fh:
            if new:
                fh.write("step,target_msg_s,lambda_msg_s,sent,received,loss_ratio,"
                         "lat_mean_ms,lat_p95_ms,lat_max_ms,buffer_peak,dropped\n")
            fh.write(f"{args.step},{args.target},{row['lambda_msg_s']},{row['sent']},"
                     f"{row['received']},{row['loss_ratio']},{row['lat_mean_ms']},"
                     f"{row['lat_p95_ms']},{row['lat_max_ms']},"
                     f"{gw.get('buffer_peak', '')},{gw.get('dropped', '')}\n")
        print(f"appended step {args.step} to {out}")
        return 0

    header = ("boot", "lambda", "sent", "recv", "loss", "gaps", "dup", "ooo", "rej",
              "mean ms", "p95 ms", "max ms")
    print("{:>5} {:>8} {:>7} {:>7} {:>7} {:>6} {:>5} {:>5} {:>5} {:>8} {:>8} {:>8}".format(*header))
    for s in summaries:
        print("{:>5} {:>8} {:>7} {:>7} {:>7} {:>6} {:>5} {:>5} {:>5} {:>8} {:>8} {:>8}".format(
            s["boot"], s["lambda_msg_s"], s["sent"], s["received"], s["loss_ratio"],
            s["gaps"], s["duplicates"], s["out_of_order"], s["rejected"],
            s["lat_mean_ms"], s["lat_p95_ms"], s["lat_max_ms"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
