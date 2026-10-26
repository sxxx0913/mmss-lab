# Lab 2 — Adaptive streaming and QoE

Domain: **Sports Event Streaming Platform** (Lab 1, domain no. 8).
Everything below is produced by `stream/package.sh`, `stream/player.html` and
`tools/qoe_measure.mjs`; raw rows are in `results/qoe.csv`.

---

## Ladder

`stream/package.sh` builds the ladder from `data/reference.mkv`. The top rung is the
lowest bitrate at which the quality is still acceptable for the domain
(SSIM ≥ 0.97, interpolated from `results/encoding.csv`: ≈ 2900 kbit/s, rounded up to
3000); the lower rungs keep the number of bits per pixel constant, so the ladder
degrades uniformly:

| Rung | Resolution | Declared, kbit/s | bit/pixel | Measured actual, kbit/s | SSIM | PSNR, dB |
|---|---|---|---|---|---|---|
| v0 | 1920 × 1080 | 3000 | 0.048 | 3063 | 0.9691 | 40.06 |
| v1 | 1280 × 720 | 1330 | 0.048 | 1326 | 0.9558 | 38.40 |
| v2 | 854 × 480 | 590 | 0.048 | 610 | 0.9430 | 36.67 |
| v3 *(optional)* | 640 × 360 | 330 | 0.048 | 359 | 0.9302 | 35.31 |

The quality column was obtained by decoding the **packaged playlists** themselves
(`ffmpeg -i stream/hls/v0/index.m3u8 -i data/reference.mkv -lavfi ssim`), i.e. it is
the quality a viewer actually receives, not the quality of an intermediate file.
For v1–v3 the reference was downscaled to the rung resolution with the same Lanczos
filter the packager uses, so the number describes "this rung against the ideal at
this resolution".

v0 lands 0.001 below the 0.97 design target: the packager uses `-preset veryfast`
(it has to run in real time for a live event), which is ~3 % less
bitrate-efficient than the `medium` preset of session 1. The gap is recorded in
ADR-0004 instead of being tuned away with +300 kbit/s on every viewer.

Segment duration is **2 s**; every rung has 30 segments and an IDR exactly at each
segment boundary (`-g 60 -keyint_min 60 -sc_threshold 0
-force_key_frames "expr:gte(t,n_forced*2)"`), so the player can switch rung without
a gap. `master.m3u8`:

```
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080,CODECS="avc1.640028",FRAME-RATE=30.000
#EXT-X-STREAM-INF:BANDWIDTH=1330000,RESOLUTION=1280x720,CODECS="avc1.64001f",FRAME-RATE=30.000
#EXT-X-STREAM-INF:BANDWIDTH=590000,RESOLUTION=854x480,CODECS="avc1.64001e",FRAME-RATE=30.000
```

## Network profiles and the prediction

Three profiles, created in DevTools → Network → Throttling (the same values are
applied by `tools/qoe_measure.mjs` through `Network.emulateNetworkConditions`):

| Profile | Scenario | Download, kbit/s | Latency, ms | α·Ĉ (α = 0.7) | Predicted rung by (4) |
|---|---|---|---|---|---|
| P1 | good Wi-Fi / office LAN | 8000 | 40 | 5600 | **v0 1080p** (3000 ≤ 5600) |
| P2 | 4G in the stands | 2000 | 120 | 1400 | **v1 720p** (1330 ≤ 1400 < 3000) |
| P3 | congested 3G / venue Wi-Fi at half-time | 700 | 400 | 490 | **no rung fits** (590 > 490) → the player stays on v2 and the buffer drains |

The prediction is written **before** the measurement, as the instructions require.
P3 is deliberately chosen just below the lowest rung: it is the case in which an
adaptive ladder has nothing left to give, and it is the case that produced the
architectural change in ADR-0004.

## Measured QoE

60 s of content, hls.js 1.7.3 in Chrome, two runs per profile, cache disabled
(`results/qoe.csv`):

| Profile | Run | Startup, s | Switches | Stalls | Stall time, s | avg_kbps | ρ by (5) |
|---|---|---|---|---|---|---|---|
| P1 | 1 | 0.96 | 1 | 0 | 0.00 | 3000 | 0 |
| P1 | 2 | 0.96 | 1 | 0 | 0.00 | 3000 | 0 |
| P2 | 1 | 3.26 | 2 | 0 | 0.00 | 1388 | 0 |
| P2 | 2 | 3.28 | 2 | 1 | 0.04 | 1390 | 0.0007 |
| P3 | 1 | 9.20 | 2 | 23 | 5.76 | 712 | 0.088 |
| P3 | 2 | 9.15 | 2 | 24 | 5.82 | 710 | 0.088 |

Stall ratio ρ = T<sub>stall</sub> / (T<sub>play</sub> + T<sub>stall</sub>), formula (5),
with T<sub>play</sub> = 60 s.

**Prediction against measurement.** P1 and P2 land exactly on the predicted rung:
the time-averaged bitrate is 3000 kbit/s (v0) and 1389 kbit/s (v1 = 1330). P3 does
**not** match: the average of 711 kbit/s is between v2 (590) and v1 (1330), because
the player does not stay on the lowest rung. The level timeline shows the
mechanism: 84 % of the time on v2 and 16 % on v1 — once the buffer is full a
segment arrives faster than it is consumed, the ABR estimate rises above
1330/0.7, the player switches up to 720p, the buffer drains within one segment, a
stall follows and it drops back. The average bitrate is therefore *above* the
profile bandwidth, and the price is paid in stalls (ρ = 0.088, i.e. the viewer
waits 8.8 % of the session).

---

## Link to the architecture

Every noticeable effect below is attributed to a parameter of Table 1, with what
changes if the parameter changes.

**1. Startup of 9.2 s on P3 — caused by the initial level (Table 1, "Initial
level").**
The page log shows the first fragment being requested at **1920 × 1080**: with no
`startLevel` in the player configuration, hls.js starts from the first
`#EXT-X-STREAM-INF` of the manifest, i.e. from the 3000 kbit/s rung. That first
segment is 750 KB, and at 700 kbit/s it takes ≈ 8.6 s to arrive — the startup is
the download time of one segment, nothing else. Pinning the initial level changes
it by a factor of 3.7 on the same profile:

| Initial-level policy | Startup on P3, s (2 runs) | First fragment | Switches |
|---|---|---|---|
| manifest default (`startLevel` absent) | 9.20 / 9.15 | 1920 × 1080 | 2 |
| `startLevel: 0` (lowest rung) | 2.51 / 2.56 | 854 × 480 | 0 |
| `startLevel: -1` (bandwidth estimate) | 4.31 / 4.30 | 854 × 480 | 0 |

This is the trade-off of Table 1 in one line: a high initial level improves the
quality of the first seconds and ruins the time to the start of playback on a slow
network. For a live match the viewer forgives two soft seconds, not a nine-second
black screen, so ADR-0004 pins the lowest rung.

**2. 23 stalls on P3 — caused by the lowest rung of the ladder (Table 1, "Lowest
level of the ladder").**
The lowest rung (590 kbit/s) is only 16 % below the profile bandwidth (700), so a
controlled reduction of quality is impossible: there is no rung to fall back to and
the buffer keeps draining. Adding a fourth rung at 640 × 360 / 330 kbit/s and
repeating P3 (`BOTTOM=1 ./stream/package.sh`, then
`node tools/qoe_measure.mjs --profiles P3:700:400 --src hls4/master.m3u8`):

| Ladder | Startup, s | Switches | Stalls | Stall time, s | ρ |
|---|---|---|---|---|---|
| 3 rungs (3000/1330/590) | 9.20 / 9.15 | 2 / 2 | 23 / 24 | 5.76 / 5.82 | 0.088 |
| 4 rungs (+ 640×360@330) | 9.27 / 9.29 | 3 / 3 | **1 / 1** | **1.37 / 1.39** | **0.022** |

The stalls fall by a factor of 20 and the stall ratio by a factor of 4, for +10 %
storage and +33 % packaging time. That is a much better use of the encoder than
raising the top rung from 3000 to 5000 kbit/s (+67 % traffic for +0.012 SSIM).

**3. One switch on P1, two on P2 — the ladder and (4) are consistent.**
P1 has 5600 kbit/s of headroom over the top rung and needs a single switch (v2 →
v0) after the first segment; P2 sits at 1400 against the 1330 rung, i.e. 5 % of
headroom, and its second switch is the transient during which the estimate settles.
No stall on either profile. The prediction of (4) is therefore correct on both, and
the ABR works as an architectural mechanism, not as an accident.

**4. Why P3 depends so strongly on latency — the segment duration (Table 1,
"Segment duration").**
With 2 s segments a viewer issues 30 requests per minute. At 400 ms of latency
that is 30 × 0.4 = 12 s of pure round-trip time per minute of playback — 20 % of
the time is spent waiting for requests, before a single byte of video is counted.
This is why P3 (400 ms) is qualitatively worse than a bandwidth-only profile with
the same 700 kbit/s would be, and why the 4 s / 6 s alternatives in ADR-0004 were
rejected: they would cut the request rate but add 2–4 s to the startup and to every
switch, which contradicts NFR-01.

**5. Segment duration also sets the server load (NFR-02).**
30 requests per minute per viewer × 50 000 concurrent viewers ≈ 25 000 requests/s
at the CDN edge. With 6 s segments it would be 8 300 requests/s; the decision in
ADR-0004 accepts the higher request rate and mitigates it with caching, because the
alternative is a latency that breaks NFR-01.

**6. A bandwidth cut is absorbed by the buffer, not by the ABR — the buffer depth
(Table 1, "Buffer depth").**
Cutting the bandwidth from 8000 to 700 kbit/s at t = 12 s of playback
(`node tools/qoe_measure.mjs --profiles P1:8000:40 --drop-after 12 --drop-to P3`),
the player **stayed at 1080p for 22 s** and then dropped directly to 480p:

```
    1.7s  first frame visible · startup 0.94 s
    1.7s  level → 1920×1080 (3000 kbit/s) · abr
   [t = 12 s: 8000 → 700 kbit/s]
   33.8s  level → 854×480 (590 kbit/s) · abr
   61.8s  playback finished        switches 2 · stalls 0 · avg 1879 kbit/s
```

The reason is that hls.js had ≈ 20 s of content buffered (`maxBufferLength` = 30 s
by default): while the buffer is not empty the player has no reason to change the
level, so the switch is delayed by exactly the amount of content in the buffer.
The consequence is a genuine trade-off, and it points the other way from FR-03:
a **deeper buffer gives fewer stalls but a slower reaction**, a shallow buffer
reacts within one segment but stalls on every fluctuation. The measurement shows
both halves of Table 1's row for this parameter — the downswitch took 22 s (bad for
FR-03) and the viewer never saw a freeze (good for FR-02).

## Check of the Lab 1 requirements and quality scenarios

| Lab 1 item | Result of Lab 2 |
|---|---|
| **FR-02** "≤ 2 stalls in a 90-minute session on a 10 Mbit/s connection" | satisfied on P1 (0 stalls in 60 s, 0 switches after the first); P1's 8000 kbit/s is the closest measured proxy for the 10 Mbit/s of the requirement |
| **FR-03** "player drops from 1080p to 480p within 5 s of bandwidth loss" | **met in intent, not in the letter.** With the bandwidth cut from 8000 to 700 kbit/s at t = 12 s the player left 1080p only 22 s later — but it reached 480p and produced **0 stalls**: the drop was absorbed by ≈ 20 s of buffer. The 5-second criterion can be met only by limiting `maxBufferLength` to a few seconds, which trades stalls for switching speed (effect 6 below) |
| **NFR-01** "L_total ≤ 1.5 s for 95 % of segments" | **not met.** Standard HLS with 2 s segments has a floor of ≈ 6 s (three segments of live sync in hls.js); the 0.96 s measured on P1 is time-to-first-frame, not glass-to-glass latency. LL-HLS (ADR-0002) is required; the FFmpeg muxer cannot emit `EXT-X-PART`, so this is the open item recorded in ADR-0004 |
| **NFR-02** "10k → 50k viewers: L_total +≤10 %, stalls +≤2" | not measurable with one client; the delivery is static segments behind a CDN, and the request-rate estimate above (25 000 req/s) is the input for the load test |
| **NFR-05** "archive ≤ 0.10 EUR per event-hour" | improved: at equal quality AV1 needs 27–37 % fewer bits, so the 21.6 GB per event of Lab 1 becomes ≈ 15 GB — a direct saving on the object storage of ADR-0003 |
| **QS-01** (100 k viewers in 5 s, fall back to LL-HLS/ABR) | the ABR fallback is implemented and measured; the burst itself is not (needs a load generator) |
| **QS-02** (encoder crash, failover ≤ 3 s) | not affected by Lab 2 — the pipeline is a single-process batch job here; in production it runs on the hot-standby pair of ADR-0001 |

The honest summary: the **delivery** part of the Lab 1 architecture survives contact
with the measurements (the ladder works, the ABR works, FR-02 is met and FR-03 is
met in intent but not in its 5-second letter), while the **latency** part does not
— standard HLS cannot deliver the 1.5 s of NFR-01, and that is a gap in the
architecture description, not in its implementation.
