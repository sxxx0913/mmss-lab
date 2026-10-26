#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Lab 2, session 2 — quality ladder and HLS packaging
#
#   data/reference.mkv (1080p30, lossless)
#        -> v0 1920x1080 @ TOP_KBPS
#        -> v1 1280x720  @ TOP_KBPS * (1280*720) / (1920*1080)   constant bpp
#        -> v2  854x480  @ TOP_KBPS * ( 854*480) / (1920*1080)
#        -> stream/hls/vN/index.m3u8 + 2 s segments, stream/hls/master.m3u8
#
#   usage:  ./stream/package.sh                  # HLS, 3 levels (main deliverable)
#           ./stream/package.sh dash             # MPEG-DASH instead
#           BOTTOM=1 ./stream/package.sh         # optional 4-level ladder -> stream/hls4/
#           TOP_KBPS=2500 ./stream/package.sh
#
#   Why the key frames are pinned:
#   the player can only switch level on a segment boundary, so every level must
#   place an IDR exactly at t = 0, 2, 4, ...  -g fixes the GOP length,
#   -sc_threshold 0 stops x264 from inserting extra key frames on scene cuts,
#   -force_key_frames makes the boundaries identical in all three encodings.
# ---------------------------------------------------------------------------
set -euo pipefail

PROJ=$(cd "$(dirname "$0")/.." && pwd)
REF="$PROJ/data/reference.mkv"
OUT="$PROJ/stream"
FPS=30
SEG=2                                  # segment duration, s (Table 1)
TOP_KBPS=${TOP_KBPS:-3000}             # lowest bitrate with SSIM >= 0.97 (see docs/pipeline.md)
BOTTOM=${BOTTOM:-0}                    # 1 = append a fourth, lower rung (optional task)
GOP=$((FPS * SEG))                     # 60 frames = one segment

# (name, width, height, kbit/s) — bits per pixel kept constant across the ladder
# (rounded to 10 kbit/s, which is far below the accuracy of the ABR decision)
r10() { echo $(( ($1 + 5) / 10 * 10 )); }
V1_KBPS=$(r10 $(( TOP_KBPS * 1280 * 720 / (1920 * 1080) )))
V2_KBPS=$(r10 $(( TOP_KBPS *  854 * 480 / (1920 * 1080) )))
V3_KBPS=$(r10 $(( TOP_KBPS *  640 * 360 / (1920 * 1080) )))
LEVELS=(
  "v0 1920 1080 $TOP_KBPS"
  "v1 1280  720 $V1_KBPS"
  "v2  854  480 $V2_KBPS"
)
[ "$BOTTOM" = "1" ] && LEVELS+=("v3  640  360 $V3_KBPS")

# AVC level for the CODECS attribute: High profile, level 4.0 / 3.1 / 3.0
codec_for() {
  case "$1" in
    1080) echo "avc1.640028" ;;
    720)  echo "avc1.64001f" ;;
    *)    echo "avc1.64001e" ;;
  esac
}

FORMAT=${1:-hls}
if [ "$FORMAT" = "dash" ]; then
  DASH_DIR="$OUT/dash"; rm -rf "$DASH_DIR"; mkdir -p "$DASH_DIR"
else
  HLS_DIR="$OUT/hls"; [ "$BOTTOM" = "1" ] && HLS_DIR="$OUT/hls4"
  rm -rf "$HLS_DIR"; mkdir -p "$HLS_DIR"
fi

echo "ladder (top level ${TOP_KBPS} kbit/s, ${SEG} s segments, $((${#LEVELS[@]})) levels):"
for L in "${LEVELS[@]}"; do
  read -r name w h kbps <<< "$L"
  echo "  $name  ${w}x${h}  ${kbps} kbit/s  $(awk -v k="$kbps" -v w="$w" -v h="$h" -v f="$FPS" \
        'BEGIN{printf "%.3f bit/pixel", k*1000/(w*h*f)}')"
done

for L in "${LEVELS[@]}"; do
  read -r name w h kbps <<< "$L"
  if [ "$FORMAT" = "dash" ]; then
    echo "packaging $name (DASH)"
    ffmpeg -y -hide_banner -loglevel error -i "$REF" -an \
      -vf "scale=${w}:${h}:flags=lanczos" \
      -c:v libx264 -preset veryfast -profile:v high -pix_fmt yuv420p \
      -b:v "${kbps}k" -maxrate "$((kbps * 11 / 10))k" -bufsize "$((kbps * 2))k" \
      -g "$GOP" -keyint_min "$GOP" -sc_threshold 0 \
      -force_key_frames "expr:gte(t,n_forced*${SEG})" \
      -f dash -seg_duration "$SEG" -use_template 1 -use_timeline 1 \
      -adaptation_sets "id=0,streams=v" \
      "$DASH_DIR/manifest.mpd"
  else
    echo "packaging $name (HLS)"
    mkdir -p "$HLS_DIR/$name"
    ffmpeg -y -hide_banner -loglevel error -i "$REF" -an \
      -vf "scale=${w}:${h}:flags=lanczos" \
      -c:v libx264 -preset veryfast -profile:v high -pix_fmt yuv420p \
      -b:v "${kbps}k" -maxrate "$((kbps * 11 / 10))k" -bufsize "$((kbps * 2))k" \
      -g "$GOP" -keyint_min "$GOP" -sc_threshold 0 \
      -force_key_frames "expr:gte(t,n_forced*${SEG})" \
      -f hls -hls_time "$SEG" -hls_playlist_type vod \
      -hls_flags independent_segments \
      -hls_segment_filename "$HLS_DIR/$name/seg_%03d.ts" \
      "$HLS_DIR/$name/index.m3u8"
  fi
done

if [ "$FORMAT" = "hls" ]; then
  {
    echo "#EXTM3U"
    echo "#EXT-X-VERSION:3"
    echo "#EXT-X-INDEPENDENT-SEGMENTS"
    for L in "${LEVELS[@]}"; do
      read -r name w h kbps <<< "$L"
      echo "#EXT-X-STREAM-INF:BANDWIDTH=$((kbps * 1000)),RESOLUTION=${w}x${h},CODECS=\"$(codec_for "$h")\",FRAME-RATE=$FPS.000"
      echo "$name/index.m3u8"
    done
  } > "$HLS_DIR/master.m3u8"
  echo "wrote $HLS_DIR/master.m3u8"
  for L in "${LEVELS[@]}"; do read -r name _ _ _ <<< "$L"; \
    echo "  $name: $(ls "$HLS_DIR/$name"/*.ts 2>/dev/null | wc -l) segments"; done
else
  echo "wrote $DASH_DIR/manifest.mpd"
fi
