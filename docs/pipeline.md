# Lab 2 — Processing pipeline and quality measurements

Domain: **Sports Event Streaming Platform** (Lab 1, domain no. 8).
Repository: `mmss-lab` (continuation of the Lab 1 repository).

---

## Source material

| Property | Value |
|---|---|
| File in the repository | `data/raw.mov` (media is excluded from Git by `.gitignore`) |
| Original | *Durango Rugby Taldea jugando vídeo.ogv* |
| Author | Txo, own work, 2015-03-15 |
| Source | Wikimedia Commons — <https://commons.wikimedia.org/wiki/File:Durango_Rugby_Taldea_jugando_v%C3%ADdeo.ogv> |
| Licence | **CC BY-SA 4.0** (attribution + share-alike) |
| Content | Rugby match DRT vs Uribaldea, División de Honor B, Arripozueta (Durango) — a dynamic scene with camera panning, grass texture and moving players |
| Original parameters | 1920 × 1080, 25 fps, 106.7 s, Theora/Vorbis, 47.7 MB |

The clip was chosen because it is a *real* broadcast-style recording of a ball game:
high motion, camera panning and a large textured background — exactly the content
that stresses an encoder. A synthetic test pattern (e.g. `testsrc`) would have made
every codec look better than it is, and the comparison of H.264 with AV1 would
have lost its meaning.

Normalisation to the single working format required by the instructions
(≤ 1080 rows, 30 fps, 60 s, no audio):

```bash
ffmpeg -i data/raw_original.ogv -t 60 \
       -vf "fps=30,scale=1920:1080:flags=lanczos" -an \
       -c:v libx264 -crf 14 -preset medium -pix_fmt yuv420p \
       -movflags +faststart data/raw.mov
```

`-crf 14` keeps the mezzanine visually transparent, so the compression artefacts of
the original Theora file are not amplified a second time before the measurement.

Result (`ffprobe`): `width=1920 height=1080 r_frame_rate=30/1 duration=60.000000`,
1800 frames, 120.3 MB, no audio stream.

> The original clip is 25 fps and was resampled to 30 fps by the `fps` filter.
> Lab 1 describes a 1080p50 camera; the pipeline models the **delivery-side**
> transformation, where the capture rate is reduced to the rate of the ladder.
> A 50/60 fps rung would be the next step for fast panning (see *Consequences* in
> ADR-0004).

---

## Pipeline

```
data/raw.mov ──► decoding ──► transformation ──► data/reference.mkv ──► encoding ──► measurement
                 OpenCV      1920×1080, 30 fps     FFV1, lossless        FFmpeg        PSNR, SSIM
                             process() overlay                            H.264/AV1     results/encoding.csv
```

Stages and their interfaces:

| Stage | Implementation | Interface to the next stage | Replaceable? |
|---|---|---|---|
| Decoding | `cv2.VideoCapture` (FFmpeg backend) | `numpy.ndarray`, BGR, `uint8` | yes — any decoder that yields frames |
| Transformation | `cv2.resize` + keep-if-due resampler + `process()` | the same array, fixed geometry | **yes — this is where the Lab 4 ONNX model goes** |
| Reference writer | rawvideo pipe (`bgr24`) → FFV1 | `data/reference.mkv` | yes |
| Encoding | `ffmpeg` subprocess per variant | `.mp4` file | yes — the codec is a parameter |
| Measurement | `ffmpeg` `psnr` / `ssim` filters | `results/encoding.csv` | yes |

### Chosen parameters and their justification

| Parameter | Value | Why |
|---|---|---|
| `WIDTH × HEIGHT` | 1920 × 1080 | the top rung of the Lab 1 ladder (ADR-0002) and the resolution of the Lab 1 data register; measuring it keeps session 2 consistent with the architecture description |
| `FPS` | 30 | Table 3 (domain 8) and the Lab 1 delivery rate; halves the bitrate with respect to the 50 fps capture while keeping motion acceptable for the "watch the match" scenario |
| Colour depth in formula (1) | 24 bit/pixel | RGB, as in the Lab 1 data register |
| Reference codec | FFV1, `-pix_fmt yuv420p`, `-g 60` | lossless, so PSNR/SSIM measure the encoder only; ~1.07 GB for 60 s |
| Variants | H.264 `libx264 -preset medium`, AV1 `libsvtav1 -preset 8`, 2500 and 5000 kbit/s | the two points required for the "same bitrate / same quality" comparison; the presets are the ones named in the instructions |
| `process()` | scoreboard overlay + wall-clock timestamp + frame counter | Table 3, domain 8 ("overlay of the scoreboard"); the timestamp is needed to prove frame identity between the reference and an encoded file |

`process()` is the only domain-specific function: it draws a semi-transparent
scoreboard (teams, score, running match clock, LIVE badge) and a top-left
`HH:MM:SS.mmm` stamp whose text is derived from the output frame index, so a
mismatch between `data/reference.mkv` and an encoded file is visible by eye.

The temporal part of the transformation was verified separately, because at the
chosen parameters the output rate equals the normalised source rate and the stage
passes every frame:

```bash
python pipeline/pipeline.py --source data/raw.mov --fps 15 --reference /tmp/r15.mkv --reference-only
# Reference: 46 frames, 3.1 s, 1920x1080, 15 fps   (from a 3 s, 92-frame clip)
```

### Why the reference is stored in yuv420p and not in RGB

The transformed frames are RGB. Writing them losslessly as RGB (FFV1 `bgr0`) is
possible, but then every comparison against a 4:2:0 encoder would be dominated by
the RGB→YUV chroma subsampling and not by the codec: in a first attempt that
artefact alone held PSNR at 30.4 dB **independently of the target bitrate**
(30.37 dB at 2500 kbit/s and 30.51 dB at 5000 kbit/s). The colour conversion is
therefore done once, inside the transformation stage, before the reference is
written; the reference is lossless *with respect to the frames that are handed to
the encoder*, which is the property the measurement needs.

### A second measurement pitfall: container time bases

MP4 stores a 1/15360 time base, Matroska 1/1000. The `psnr`/`ssim` filters pair
frames by timestamp, so with two different time bases frame *n* was compared with
frame *n±1* and PSNR again stopped responding to the bitrate. The measurement
command therefore re-stamps both inputs with the frame index on a common time base:

```bash
ffmpeg -i variant.mp4 -i data/reference.mkv -lavfi \
  "[0:v]settb=1/30,setpts=N[a];[1:v]settb=1/30,setpts=N[b];[a][b]psnr" -f null -
```

Verification: encoding the reference losslessly with `libx264 -qp 0` and measuring
it against the reference now gives `PSNR = inf`, as it must.

---

## Comparison of the variants

Command: `python pipeline/pipeline.py` (reference built in 16.9 s, 106.5 fps).
Reference: 1800 frames, 60.0 s, 1920 × 1080, 30 fps, FFV1, 1071 MB.

Uncompressed bit rate, formula (1):

```
R = w · h · f · d = 1920 · 1080 · 30 · 24 = 1 492 992 000 bit/s ≈ 1.49 Gbit/s
```

| Variant | Codec | Target, kbit/s | Size, MB | R<sub>act</sub>, kbit/s | K = R/R<sub>act</sub> | Encode, s | PSNR, dB | SSIM |
|---|---|---|---|---|---|---|---|---|
| `h264_2500` | H.264 (libx264, medium) | 2500 | 18.52 | 2469.7 | 604.5 | 20.61 | 40.13 | 0.9677 |
| `h264_5000` | H.264 (libx264, medium) | 5000 | 36.84 | 4911.3 | 304.0 | 25.46 | 43.30 | 0.9815 |
| `av1_2500` | AV1 (libsvtav1, preset 8) | 2500 | 18.66 | 2487.3 | 600.2 | 25.67 | 41.96 | 0.9727 |
| `av1_5000` | AV1 (libsvtav1, preset 8) | 5000 | 37.20 | 4959.4 | 301.0 | 26.49 | 43.93 | 0.9797 |

R<sub>act</sub> = 8·S/T by formula (2); K is the compression ratio; both encoders
receive the same input — the reference produced by the transformation stage.

### Conclusion

**At the same bitrate.** At 2500 kbit/s AV1 gives 41.96 dB against 40.13 dB for
H.264 (+1.83 dB) and SSIM 0.9727 against 0.9677 (+0.0050). At 5000 kbit/s the gap
in PSNR narrows to +0.63 dB (43.93 vs 43.30) and **SSIM is slightly worse**
(0.9797 vs 0.9815).

**At the same quality.** Interpolating linearly between the two measured points of
each codec:

* equal PSNR 41.96 dB → H.264 needs ≈ 2500 + (41.96−40.13)/(43.30−40.13) · 2500 ≈ **3940 kbit/s**;
  AV1 reaches it at 2500 kbit/s → **AV1 saves ≈ 37 % of the traffic**;
* equal SSIM 0.9727 → H.264 needs ≈ 2500 + (0.9727−0.9677)/(0.9815−0.9677) · 2500 ≈ **3410 kbit/s**
  → **AV1 saves ≈ 27 %**.

**What has to be paid for it.** Encoding time grows only by a factor of 1.25 at
2500 kbit/s (25.7 s vs 20.6 s) and by 1.04 at 5000 kbit/s, because preset 8 of
SVT-AV1 is a *fast* preset; a slower preset would buy a few more percent of
bitrate at several times the encoding time, which is the real trade-off for a live
event. The second cost is decoding: AV1 in a browser needs `dav1d`-class software
decoding or hardware support, while H.264 is decoded in hardware almost
everywhere, including older set-top boxes. For a **live** broadcast that is the
decisive argument (see ADR-0004).

**The PSNR/SSIM disagreement at 5000 kbit/s is not a defect of the measurement.**
PSNR is a per-pixel error and rewards AV1's better rate allocation, while SSIM
compares local luminance, contrast and structure: AV1's in-loop filters (CDEF,
restoration) smooth the grain of the grass and of the crowd, which lowers the
local contrast correlation even though the picture looks cleaner. This is exactly
the case in which the two metrics lead to different conclusions — and the reason
why the ladder rung below is chosen by SSIM (structural fidelity of the scoreboard
and of the players' silhouettes), with PSNR as a cross-check.

**Best variant by compression ratio:** `av1_5000`, K ≈ 301 (i.e. the stream is
301 times smaller than the uncompressed 1.49 Gbit/s stream); `h264_5000` reaches
K ≈ 304 at a lower encoding cost.

**Which rung goes to the ladder.** SSIM ≥ 0.97 for H.264 at 1080p is reached at
≈ 2500 + (0.97−0.9677)/(0.9815−0.9677)·2500 ≈ **2900 kbit/s**; the top rung is
therefore set to **3000 kbit/s** (0.048 bit/pixel), and the lower rungs follow the
constant-bits-per-pixel rule (see `docs/qoe.md`).
