#!/usr/bin/env python3
"""Chart of delivery latency and loss against the load (Lab 3).

Reads results/load.csv, writes results/latency.png. The vertical line marks the
throughput of the system, taken from the plateau of the received rate unless
--theta is given.

    python smart/plot.py
    python smart/plot.py --csv results/load.csv --out results/latency.png
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]


def read_rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return [{k: float(v) if v not in ("", None) else float("nan") for k, v in row.items()}
                for row in csv.DictReader(fh)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(ROOT / "results" / "load.csv"))
    ap.add_argument("--out", default=str(ROOT / "results" / "latency.png"))
    ap.add_argument("--theta", type=float, default=None)
    args = ap.parse_args(argv)

    rows = read_rows(Path(args.csv))
    if not rows:
        print("no rows", file=sys.stderr)
        return 1

    # the offered load is what the test controls; the achieved rate is what the
    # system delivers. Plotting against the offered load separates the steps and
    # shows the knee, which plotting against the achieved rate cannot do because
    # that rate stops at the ceiling.
    load = [r["target_msg_s"] for r in rows]
    achieved = [r["lambda_msg_s"] for r in rows]
    mean = [r["lat_mean_ms"] for r in rows]
    p95 = [r["lat_p95_ms"] for r in rows]
    worst = [r["lat_max_ms"] for r in rows]
    loss = [100 * r["loss_ratio"] for r in rows]

    theta = args.theta
    if theta is None:
        theta = max(achieved) if achieved else 0.0

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(9, 6.4), sharex=True, gridspec_kw={"height_ratios": [2.1, 1]}
    )

    ax1.plot(load, mean, "o-", color="#1f6feb", lw=2, ms=5, label="mean latency")
    ax1.plot(load, p95, "s--", color="#f0883e", lw=1.6, ms=4, label="95th percentile")
    ax1.plot(load, worst, "^:", color="#8b949e", lw=1.2, ms=4, label="maximum")
    ax1.set_ylabel("delivery latency $L_d$, ms")
    ax1.set_yscale("log")
    ax1.grid(True, which="both", alpha=0.25, lw=0.6)
    ax1.legend(frameon=False, loc="upper left", fontsize=9)

    ax2.plot(load, loss, "o-", color="#da3633", lw=2, ms=5)
    ax2.set_ylabel("lost readings, %")
    ax2.set_xlabel("offered load, messages/s")
    ax2.grid(True, alpha=0.25, lw=0.6)
    ax2.set_ylim(bottom=-1)

    for ax in (ax1, ax2):
        ax.axvline(theta, color="#2ea043", ls="--", lw=1.4, alpha=0.9)
    ax1.annotate(f"$\\Theta \\approx$ {theta:.0f} msg/s", xy=(theta, ax1.get_ylim()[0] * 3),
                 xytext=(8, 0), textcoords="offset points", ha="left", va="center",
                 color="#2ea043", fontsize=9)

    ax1.set_title("Delivery latency and loss against the load (three wearable channels)", fontsize=11)
    fig.tight_layout()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
