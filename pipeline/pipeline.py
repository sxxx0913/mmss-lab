#!/usr/bin/env python3
"""
Lab 2, session 1 — media data processing pipeline for the
"Sports Event Streaming Platform" domain (Lab 1, domain no. 8).

    decoding (OpenCV) -> transformation (w x h, fps, process()) -> reference
    -> encoding (FFmpeg: H.264 / AV1) -> measurement (S, Ract, K, time, PSNR, SSIM)

Stages exchange data through two explicit interfaces:
  * OpenCV frame (numpy array, BGR, uint8) between decoding/transformation;
  * a rawvideo pipe (bgr24) between transformation and encoding.

Only process() is domain specific.  In Lab 4 an ONNX model is embedded into
process() and nothing else in this file has to change.

Usage
-----
    python pipeline/pipeline.py                       # full run (reference + 4 variants)
    python pipeline/pipeline.py --width 1280 --height 720
    python pipeline/pipeline.py --skip-reference      # reuse data/reference.mkv

FFmpeg must be on PATH (or set FFMPEG=/path/to/ffmpeg).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = DATA / "out"
RESULTS = ROOT / "results"
REFERENCE = DATA / "reference.mkv"
SOURCE = DATA / "raw.mov"

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "ffprobe")

# --------------------------------------------------------------------------- #
# Pipeline parameters (justified in docs/pipeline.md, "Pipeline" section)
# --------------------------------------------------------------------------- #
WIDTH, HEIGHT = 1920, 1080      # top rung of the Lab 1 ladder (ADR-0002)
FPS = 30                        # Table 3, domain 8; Lab 1 data register is 1080p50
COLOR_DEPTH = 24                # bit/pixel, RGB 4:4:4 equivalent for formula (1)

# Encoding variants: (name, encoder, extra args, target kbit/s)
VARIANTS = [
    ("h264_2500", "libx264",   ["-preset", "medium"], 2500),
    ("h264_5000", "libx264",   ["-preset", "medium"], 5000),
    ("av1_2500",  "libsvtav1", ["-preset", "8"],      2500),
    ("av1_5000",  "libsvtav1", ["-preset", "8"],      5000),
]

# --------------------------------------------------------------------------- #
# Domain constants used by process()
# --------------------------------------------------------------------------- #
HOME_TEAM = "DRT"
AWAY_TEAM = "URT"
SCORE_HOME, SCORE_AWAY = 12, 9


# --------------------------------------------------------------------------- #
# Stage 3 — transformation: content processing
# --------------------------------------------------------------------------- #
def _rounded_bar(img: np.ndarray, x: int, y: int, w: int, h: int, alpha: float = 0.62):
    """Semi-transparent rounded bar blended into the frame ROI."""
    r = h // 2
    patch = np.zeros((h, w, 3), dtype=np.uint8)
    cv2.rectangle(patch, (r, 0), (w - r, h), (18, 18, 18), -1)
    cv2.circle(patch, (r, r), r, (18, 18, 18), -1)
    cv2.circle(patch, (w - r, r), r, (18, 18, 18), -1)
    roi = img[y:y + h, x:x + w]
    cv2.addWeighted(patch, alpha, roi, 1.0 - alpha, 0, roi)
    return img


def process(frame: np.ndarray, t: float, frame_index: int) -> np.ndarray:
    """Domain transformation for the sports streaming platform.

    Two overlays, both typical of a live broadcast:
      * a scoreboard (teams, score, running match clock) — Table 3, domain 8;
      * a top-left wall-clock timestamp — required to verify frame identity in
        data/reference.mkv and to measure glass-to-glass latency in Lab 4.

    This is the only function that Lab 4 replaces/extends with an ONNX model.
    """
    h, w = frame.shape[:2]
    k = h / 1080.0                      # all sizes scale with the frame height
    img = frame.copy()

    # --- scoreboard -------------------------------------------------------- #
    bar_h = int(96 * k)
    bar_w = int(560 * k)
    x0 = (w - bar_w) // 2
    y0 = int(28 * k)
    _rounded_bar(img, x0, y0, bar_w, bar_h, alpha=0.62)

    font = cv2.FONT_HERSHEY_DUPLEX
    thick = max(1, int(2 * k))
    white, grey, accent = (245, 245, 245), (170, 170, 170), (60, 190, 255)
    base_y = y0 + int(bar_h * 0.68)
    cv2.putText(img, HOME_TEAM, (x0 + int(26 * k), base_y), font, 1.0 * k, white, thick, cv2.LINE_AA)
    cv2.putText(img, AWAY_TEAM, (x0 + bar_w - int(96 * k), base_y), font, 1.0 * k, white, thick, cv2.LINE_AA)
    cv2.putText(img, f"{SCORE_HOME:02d}", (x0 + int(140 * k), base_y), font, 1.15 * k, accent, thick + 1, cv2.LINE_AA)
    cv2.putText(img, f"{SCORE_AWAY:02d}", (x0 + int(330 * k), base_y), font, 1.15 * k, accent, thick + 1, cv2.LINE_AA)
    cv2.putText(img, ":", (x0 + int(243 * k), base_y), font, 1.0 * k, grey, thick, cv2.LINE_AA)

    # running match clock: mm:ss from the start of the clip
    mm, ss = divmod(int(t), 60)
    cv2.putText(img, f"{mm:02d}:{ss:02d}", (x0 + int(410 * k), base_y), font, 0.9 * k, grey, thick, cv2.LINE_AA)

    # LIVE badge
    bx, by = x0 + bar_w + int(18 * k), y0 + int(20 * k)
    cv2.circle(img, (bx + int(12 * k), by + int(14 * k)), int(9 * k), (40, 40, 220), -1, cv2.LINE_AA)
    cv2.putText(img, "LIVE", (bx + int(28 * k), by + int(22 * k)), font, 0.6 * k, white, thick, cv2.LINE_AA)

    # --- timestamp (top left) ---------------------------------------------- #
    ts = f"{int(t // 3600):02d}:{int(t // 60) % 60:02d}:{t % 60:06.3f}"
    org = (int(24 * k), int(40 * k))
    cv2.putText(img, ts, org, cv2.FONT_HERSHEY_SIMPLEX, 0.8 * k, (0, 0, 0), thick + 2, cv2.LINE_AA)
    cv2.putText(img, ts, org, cv2.FONT_HERSHEY_SIMPLEX, 0.8 * k, white, thick, cv2.LINE_AA)

    # frame counter, useful when comparing the reference with an encoded file
    cv2.putText(img, f"f{frame_index:05d}", (int(24 * k), int(72 * k)), cv2.FONT_HERSHEY_SIMPLEX,
                0.6 * k, (200, 200, 200), thick, cv2.LINE_AA)
    return img


# --------------------------------------------------------------------------- #
# Stage 1 + 2 — decoding and geometric/temporal transformation
# --------------------------------------------------------------------------- #
def build_reference(src: Path, dst: Path, width: int, height: int, fps: int) -> dict:
    """Decode -> transform -> write the lossless reference through a rawvideo pipe."""
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise SystemExit(f"cannot open source {src}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or fps
    src_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"source: {src.name}  {src_w}x{src_h}  {src_fps:.3f} fps  {src_frames} frames")

    interp = cv2.INTER_AREA if (width * height) < (src_w * src_h) else cv2.INTER_CUBIC

    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", str(fps), "-i", "-",
        # The reference is lossless in the *encoding* colour space: the single
        # RGB -> YUV 4:2:0 conversion belongs to the transformation stage and is
        # applied once, here, so that PSNR/SSIM measure the codec and not the
        # colour conversion (see docs/pipeline.md).
        "-an", "-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv420p",
        "-threads", "8", "-slices", "8", "-g", "60",
        str(dst),
    ]
    dst.parent.mkdir(parents=True, exist_ok=True)
    pipe = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert pipe.stdin is not None

    t0 = time.perf_counter()
    out_index = 0
    next_due = 0.0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        # temporal transformation: keep the frame only when its source time
        # reaches the next output slot (works for any ratio, not only integers)
        t_src = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if t_src + 1e-6 < next_due:
            continue
        next_due += 1.0 / fps

        frame = cv2.resize(frame, (width, height), interpolation=interp)
        t_out = out_index / fps
        frame = process(frame, t_out, out_index)
        pipe.stdin.write(frame.tobytes())
        out_index += 1
    pipe.stdin.close()
    if pipe.wait() != 0:
        raise SystemExit("ffmpeg failed while writing the reference")
    elapsed = time.perf_counter() - t0
    cap.release()

    duration = out_index / fps
    print(f"Reference: {out_index} frames, {duration:.1f} s, {width}x{height}, {fps} fps "
          f"({elapsed:.1f} s, {out_index / elapsed:.1f} fps, {dst.stat().st_size / 1e6:.0f} MB)")
    return {"frames": out_index, "duration": duration, "decode_transform_s": elapsed,
            "width": width, "height": height, "fps": fps}


# --------------------------------------------------------------------------- #
# Stage 4 — encoding (measured separately from decoding)
# --------------------------------------------------------------------------- #
def encode(reference: Path, variant: tuple) -> dict:
    name, encoder, extra, kbps = variant
    dst = OUT / f"{name}.mp4"
    cmd = [
        FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(reference), "-an",
        "-c:v", encoder, *extra,
        "-b:v", f"{kbps}k",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(dst),
    ]
    t0 = time.perf_counter()
    subprocess.run(cmd, check=True)
    encode_s = time.perf_counter() - t0
    size = dst.stat().st_size
    print(f"  {name:<10} {kbps:>5} kbit/s -> {size / 1e6:6.2f} MB in {encode_s:6.1f} s")
    return {"name": name, "encoder": encoder, "target_kbps": kbps,
            "size_bytes": size, "encode_s": encode_s, "path": dst}


def measure_quality(encoded: Path, reference: Path, frames: int, fps: int) -> tuple[float, float]:
    """PSNR (dB) and SSIM against the lossless reference, both computed by FFmpeg.

    The two files carry different container time bases (1/15360 for MP4,
    1/1000 for Matroska), so the filter graph re-stamps both streams with the
    frame index on a common time base.  Without this the framesync filter pairs
    frames by timestamp and compares frame N with frame N+1 — the resulting
    PSNR stops responding to the bitrate.
    """
    def run(filt: str, pattern: str) -> float:
        graph = (f"[0:v]settb=1/{fps},setpts=N[a];"
                 f"[1:v]settb=1/{fps},setpts=N[b];[a][b]{filt}")
        cmd = [FFMPEG, "-hide_banner", "-loglevel", "info",
               "-i", str(encoded), "-i", str(reference),
               "-frames:v", str(frames),
               "-lavfi", graph, "-f", "null", "-"]
        out = subprocess.run(cmd, capture_output=True, text=True).stderr
        for line in out.splitlines():
            if pattern in line:
                token = line.split(pattern)[1].split()[0].strip()
                return float(token)
        return float("nan")

    psnr = run("psnr", "average:")
    ssim = run("ssim", "All:")
    return psnr, ssim


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Lab 2 processing pipeline (sports streaming domain)")
    ap.add_argument("--source", default=str(SOURCE))
    ap.add_argument("--width", type=int, default=WIDTH)
    ap.add_argument("--height", type=int, default=HEIGHT)
    ap.add_argument("--fps", type=int, default=FPS)
    ap.add_argument("--skip-reference", action="store_true", help="reuse data/reference.mkv")
    ap.add_argument("--reference", default=str(REFERENCE), help="where to write the lossless reference")
    ap.add_argument("--reference-only", action="store_true", help="stop after the reference (stage check)")
    args = ap.parse_args(argv)

    reference = Path(args.reference)
    OUT.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)

    if args.skip_reference and reference.exists():
        info = json.loads((DATA / "reference.json").read_text())
        print(f"reusing reference: {info['frames']} frames, {info['duration']:.1f} s")
    else:
        info = build_reference(Path(args.source), reference, args.width, args.height, args.fps)
        (DATA / "reference.json").write_text(json.dumps(info, indent=2))
    if args.reference_only:
        return 0

    # formula (1): bit rate of the uncompressed stream
    r_raw = info["width"] * info["height"] * info["fps"] * COLOR_DEPTH

    rows = []
    print("encoding variants:")
    for variant in VARIANTS:
        enc = encode(reference, variant)
        psnr, ssim = measure_quality(enc["path"], reference, info["frames"], info["fps"])
        r_act = 8 * enc["size_bytes"] / info["duration"]           # formula (2)
        rows.append({
            "variant": enc["name"],
            "codec": "H.264" if enc["encoder"] == "libx264" else "AV1",
            "encoder": enc["encoder"],
            "target_kbps": enc["target_kbps"],
            "width": info["width"], "height": info["height"], "fps": info["fps"],
            "frames": info["frames"], "duration_s": round(info["duration"], 3),
            "size_bytes": enc["size_bytes"],
            "size_mb": round(enc["size_bytes"] / 1e6, 3),
            "actual_kbps": round(r_act / 1000, 1),
            "raw_mbps": round(r_raw / 1e6, 1),
            "compression_ratio": round(r_raw / r_act, 1),
            "encode_time_s": round(enc["encode_s"], 2),
            "psnr_db": round(psnr, 2),
            "ssim": round(ssim, 4),
        })
        print(f"             PSNR {psnr:5.2f} dB   SSIM {ssim:.4f}   Ract {r_act / 1000:7.1f} kbit/s")

    out_csv = RESULTS / "encoding.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {out_csv.relative_to(ROOT)} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
