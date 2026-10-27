# Lab 2, session 1: processing pipeline and quality measurements

Domain: **Sports Event Streaming Platform** (Lab 1, domain 8). The work continues the `mmss-lab` repository from Lab 1.

---

## Source material

| Property | Value |
|---|---|
| File in the repository | `data/raw.mov` (excluded from Git by `.gitignore`) |
| Original | *Durango Rugby Taldea jugando vídeo.ogv* |
| Author | Txo, own work, 2015-03-15 |
| Source | Wikimedia Commons, <https://commons.wikimedia.org/wiki/File:Durango_Rugby_Taldea_jugando_v%C3%ADdeo.ogv> |
| Licence | CC BY-SA 4.0 (attribution and share-alike) |
| Content | Rugby match DRT vs Uribaldea, División de Honor B, Arripozueta (Durango): camera panning, textured grass, players moving across the frame |
| Original parameters | 1920 × 1080, 25 fps, 106.7 s, Theora/Vorbis, 47.7 MB |

The clip is a real broadcast-style recording of a ball game. High motion, camera panning and a large textured background are the content that stresses an encoder, and a synthetic test pattern would have flattered both codecs and flattened the comparison between them.

Normalisation to the single working format the instructions require (at most 1080 rows, 30 fps, 60 s, no audio):

```bash
ffmpeg -i data/raw_original.ogv -t 60 \
       -vf "fps=30,scale=1920:1080:flags=lanczos" -an \
       -c:v libx264 -crf 14 -preset medium -pix_fmt yuv420p \
       -movflags +faststart data/raw.mov
```

`-crf 14` keeps the mezzanine visually transparent, so the artefacts of the original Theora file are not amplified a second time before the measurement.

`ffprobe` on the result: `width=1920 height=1080 r_frame_rate=30/1 duration=60.000000`, 1800 frames, 120.3 MB, no audio stream.

The original clip runs at 25 fps and the `fps` filter resamples it to 30. Lab 1 describes a 1080p50 camera, so the pipeline models the delivery side of the chain, where the capture rate is reduced to the rate of the ladder. A 50 or 60 fps rung is the next step for fast panning (ADR-0004, Consequences).

---

## Pipeline

```
data/raw.mov ──► decoding ──► transformation ──► data/reference.mkv ──► encoding ──► measurement
                 OpenCV      1920×1080, 30 fps     FFV1, lossless        FFmpeg        PSNR, SSIM
                             process() overlay                            H.264/AV1     results/encoding.csv
```

| Stage | Implementation | Interface to the next stage | Replaceable |
|---|---|---|---|
| Decoding | `cv2.VideoCapture` (FFmpeg backend) | `numpy.ndarray`, BGR, `uint8` | yes, any decoder that yields frames |
| Transformation | `cv2.resize`, keep-if-due resampler, `process()` | the same array, fixed geometry | yes, and this is where the Lab 4 ONNX model goes |
| Reference writer | rawvideo pipe (`bgr24`) to FFV1 | `data/reference.mkv` | yes |
| Encoding | one `ffmpeg` subprocess per variant | `.mp4` file | yes, the codec is a parameter |
| Measurement | `psnr` and `ssim` filters of FFmpeg | `results/encoding.csv` | yes |

### Parameters and justification

| Parameter | Value | Why |
|---|---|---|
| `WIDTH × HEIGHT` | 1920 × 1080 | top rung of the Lab 1 ladder (ADR-0002) and the resolution of the Lab 1 data register, so session 2 stays consistent with the architecture description |
| `FPS` | 30 | Table 3 of the instructions for domain 8, and the Lab 1 delivery rate; halves the bitrate against the 50 fps capture while keeping motion acceptable for the "watch the match" scenario |
| Colour depth in formula (1) | 24 bit/pixel | RGB, as in the Lab 1 data register |
| Reference codec | FFV1, `-pix_fmt yuv420p`, `-g 60` | lossless for the frames handed to the encoder; 1.07 GB for 60 s |
| Variants | H.264 `libx264 -preset medium`, AV1 `libsvtav1 -preset 8`, at 2500 and 5000 kbit/s | two points per codec, which is what the same-bitrate and same-quality comparisons need; presets as named in the instructions |
| `process()` | scoreboard overlay, wall-clock timestamp, frame counter | Table 3 of the instructions for domain 8; the timestamp proves frame identity between the reference and an encoded file |

`process()` is the only domain-specific function. It draws a semi-transparent scoreboard (teams, score, running match clock, LIVE badge) and a top-left `HH:MM:SS.mmm` stamp derived from the output frame index, so a frame shift between `data/reference.mkv` and an encoded file is visible by eye.

The temporal part of the transformation was checked on its own, since at the chosen parameters the output rate equals the normalised source rate and the stage passes every frame:

```bash
python pipeline/pipeline.py --source data/raw.mov --fps 15 --reference /tmp/r15.mkv --reference-only
# Reference: 46 frames, 3.1 s, 1920x1080, 15 fps   (from a 3 s, 92-frame clip)
```

### Reference colour space

The reference is written in the colour space the encoders receive (yuv420p). An RGB reference compared against a 4:2:0 encoder measures the chroma subsampling rather than the codec: with FFV1 stored as RGB, PSNR sat at 30.4 dB at both 2500 and 5000 kbit/s and did not move with the bitrate. The RGB to YUV conversion therefore belongs to the transformation stage and happens once, before the reference is written, so every variant is compared with frames in the format it encodes. The same pair of variants then gives 40.13 and 43.30 dB.

### Frame alignment

MP4 stores a 1/15360 time base and Matroska 1/1000. The `psnr` and `ssim` filters pair frames by timestamp, so two different time bases shift the comparison by one frame and PSNR stops tracking the bitrate. The measurement re-stamps both inputs with the frame index on a common time base:

```bash
ffmpeg -i variant.mp4 -i data/reference.mkv -lavfi \
  "[0:v]settb=1/30,setpts=N[a];[1:v]settb=1/30,setpts=N[b];[a][b]psnr" -f null -
```

Encoding the reference losslessly with `libx264 -qp 0` and measuring it against the reference with this graph returns `PSNR = inf`, which is the check that the alignment holds.

---

## Comparison of the variants

Command: `python pipeline/pipeline.py`. The reference is built in 14.6 s at 106 fps: 1800 frames, 60.0 s, 1920 × 1080, 30 fps, FFV1, 1071 MB.

Uncompressed bit rate by formula (1):

```
R = w · h · f · d = 1920 · 1080 · 30 · 24 = 1 492 992 000 bit/s ≈ 1.49 Gbit/s
```

| Variant | Codec | Target, kbit/s | Size, MB | R<sub>act</sub>, kbit/s | K = R/R<sub>act</sub> | Encode, s | PSNR, dB | SSIM |
|---|---|---|---|---|---|---|---|---|
| `h264_2500` | H.264 (libx264, medium) | 2500 | 18.52 | 2469.7 | 604.5 | 20.61 | 40.13 | 0.9677 |
| `h264_5000` | H.264 (libx264, medium) | 5000 | 36.84 | 4911.3 | 304.0 | 25.46 | 43.30 | 0.9815 |
| `av1_2500` | AV1 (libsvtav1, preset 8) | 2500 | 18.66 | 2487.3 | 600.2 | 25.67 | 41.96 | 0.9727 |
| `av1_5000` | AV1 (libsvtav1, preset 8) | 5000 | 37.20 | 4959.4 | 301.0 | 26.49 | 43.93 | 0.9797 |

R<sub>act</sub> = 8·S/T by formula (2), K is the compression ratio, and both encoders receive the same input, the reference produced by the transformation stage.

**At the same bitrate.** At 2500 kbit/s AV1 gives 41.96 dB against 40.13 dB for H.264, a gain of 1.83 dB, and SSIM 0.9727 against 0.9677. At 5000 kbit/s the PSNR gain narrows to 0.63 dB (43.93 against 43.30) and SSIM is slightly lower (0.9797 against 0.9815).

**At the same quality.** Interpolating linearly between the two measured points of each codec:

- equal PSNR 41.96 dB: H.264 needs 2500 + (41.96 − 40.13)/(43.30 − 40.13) · 2500 ≈ **3940 kbit/s**, and AV1 reaches the same value at 2500 kbit/s, so AV1 saves about **37 %** of the traffic;
- equal SSIM 0.9727: H.264 needs 2500 + (0.9727 − 0.9677)/(0.9815 − 0.9677) · 2500 ≈ **3410 kbit/s**, so AV1 saves about **27 %**.

**Cost of the saving.** Encoding time grows by a factor of 1.25 at 2500 kbit/s (25.7 s against 20.6 s) and 1.04 at 5000 kbit/s, because preset 8 of SVT-AV1 is a fast preset; a slower preset buys a few more percent of bitrate at several times the encoding time. The second cost is decoding: AV1 in a browser needs software decoding of the `dav1d` class or hardware support, while H.264 is decoded in hardware almost everywhere, including older set-top boxes. For a live broadcast that is the argument that decides the codec (ADR-0004).

**The two metrics rank the codecs differently at 5000 kbit/s.** PSNR is a per-pixel error and rewards the rate allocation of AV1, while SSIM compares local luminance, contrast and structure: the in-loop filters of AV1 (CDEF and restoration) smooth the grain of the grass and of the crowd, which lowers the local contrast correlation although the picture looks cleaner. The two metrics therefore answer different questions, and the ladder rung below is chosen by SSIM, which tracks the structural fidelity of the scoreboard and of the players' silhouettes, with PSNR as a cross-check.

**Compression ratio.** `av1_5000` reaches K ≈ 301, so the stream is 301 times smaller than the uncompressed 1.49 Gbit/s. `h264_5000` reaches K ≈ 304 at a lower encoding cost.

**Choice of the top rung.** H.264 at 1080p reaches SSIM 0.97 at 2500 + (0.97 − 0.9677)/(0.9815 − 0.9677) · 2500 ≈ 2900 kbit/s, so the top rung of the ladder is set to 3000 kbit/s (0.048 bit/pixel). The lower rungs follow the constant bits-per-pixel rule, see `docs/qoe.md`.
