# ADR-0004. Pipeline parameters and delivery configuration of the live tier

## Status
Accepted, 2026-10-26. Supersedes the *qualitative* part of ADR-0002 (the ladder
was named there but not dimensioned); the LL-HLS part of ADR-0002 is **not**
implemented yet — see *Consequences*.

## Context
Lab 1 fixed the architecture of the platform: encoding on an edge node at the venue
(ADR-0001), LL-HLS with an adaptive bitrate ladder (ADR-0002) and object storage
with lifecycle tiering (ADR-0003). Three things were left open, and all three are
now measurable:

1. **Which codec** carries the live stream. Lab 1 assumed H.264/AAC at ~8 Mbit/s
   for the archive without comparing it with anything.
2. **Which bitrates** the ladder rungs have. ADR-0002 named 1080p/720p/480p but
   gave no numbers, so neither NFR-01 (latency) nor NFR-05 (storage cost) could be
   checked against the delivery configuration.
3. **How the player starts** and how long a segment is. Both are delivery
   parameters that the architecture description did not constrain, and both turned
   out to dominate the QoE on a slow network.

Measurements available: `results/encoding.csv` (four variants, 60 s, 1080p30) and
`results/qoe.csv` (three network profiles, two runs each), plus the ladder
verification and the two controlled experiments reported in `docs/qoe.md`.

Key numbers:

| Fact | Value | Source |
|---|---|---|
| Uncompressed rate, formula (1) | 1.49 Gbit/s | `encoding.csv` |
| H.264 1080p30 at SSIM 0.97 | ≈ 2900 kbit/s (interpolated) | `encoding.csv` |
| AV1 saving at equal PSNR / equal SSIM | 37 % / 27 % | `encoding.csv` |
| AV1 encoding time vs H.264 | ×1.25 (2500 kbit/s), ×1.04 (5000 kbit/s) | `encoding.csv` |
| Stall ratio on P3, 3-level ladder | 0.088 | `qoe.csv` |
| Stall ratio on P3, 4-level ladder | 0.022 | `docs/qoe.md` |
| Startup on P3, default initial level | 9.2 s | `qoe.csv` |
| Startup on P3, `startLevel: 0` | 2.5 s | `docs/qoe.md` |

## Options

**Codec.** (a) H.264 only; (b) AV1 only; (c) H.264 for live, AV1 for the archive.

**Top rung.** (a) 2500 kbit/s — 0.9677 SSIM, below the 0.97 design target;
(b) 3000 kbit/s — the interpolated 0.97 point, 0.048 bit/pixel; (c) 5000 kbit/s —
0.9815 SSIM but 1.7× the traffic for a 0.01 gain.

**Ladder depth.** (a) three rungs (1080p/720p/480p); (b) four rungs, adding
640×360 at 330 kbit/s.

**Segment duration.** (a) 2 s; (b) 4 s; (c) 6 s.

**Initial level.** (a) the manifest default (1080p, as measured); (b) the lowest
rung (`startLevel: 0`); (c) the bandwidth estimate (`startLevel: -1`).

## Decision

1. **H.264 for live, AV1 for the archive** (option c). AV1 saves 27–37 % of the
   traffic at equal quality, but the live path is decoded by 50–100 k viewers on
   unknown devices, where H.264 has hardware decoding almost everywhere and AV1
   still falls back to software. In the archive path the file is encoded once and
   decoded rarely, so the 27–37 % becomes a permanent storage and egress saving
   that directly serves NFR-05 (21.6 GB → ≈ 15 GB per event). The 1.25× encoding
   cost of AV1 is irrelevant for an offline job and decisive for a live one.
2. **Ladder 3000 / 1330 / 590 kbit/s at 1920×1080 / 1280×720 / 854×480**, constant
   0.048 bit/pixel (`stream/package.sh`). 3000 kbit/s is the lowest rung at which
   SSIM reaches the 0.97 design target; the delivered rung measures SSIM 0.9691 and
   PSNR 40.06 dB, i.e. the target within 0.001 (the packaging uses `-preset
   veryfast` for real-time operation, which is ~3 % less bitrate-efficient than the
   `medium` preset used in session 1). Going to 5000 kbit/s would buy 0.012 SSIM
   for 67 % more traffic on every viewer.
3. **A fourth rung (640×360 at 330 kbit/s) is packaged but not part of the default
   manifest** (`BOTTOM=1 ./stream/package.sh`). It exists because the measurement
   proved it is needed: on P3 the three-rung ladder cannot reduce quality below
   590 kbit/s, and the stall ratio falls from 0.088 to 0.022 when the rung is
   available. It is published to viewers whose measured bandwidth stays below
   ~500 kbit/s.
4. **Segments of 2 s with pinned IDRs** (`-g 60 -sc_threshold 0
   -force_key_frames`). 2 s keeps the startup and the switching delay at one
   segment while limiting the request rate; the boundaries are identical in all
   rungs, otherwise the player cannot switch without a stall.
5. **Initial level pinned to the lowest rung** (`new Hls({startLevel: 0})`). With
   the manifest default the player fetched the first segment at 1920×1080 (750 KB)
   and needed 9.2 s to show a frame on P3; pinning the lowest rung gives 2.5 s and
   the bandwidth-estimate policy 4.3 s. The cost is up to one segment of low
   quality at the start, which is invisible next to a 6.7 s shorter wait.
6. **Standard HLS instead of LL-HLS for this iteration.** The FFmpeg HLS muxer
   cannot emit `EXT-X-PART`, so the LL-HLS part of ADR-0002 cannot be produced with
   the tool set of this lab. The ladder, segment and player parameters decided here
   are independent of that and are reused unchanged when LL-HLS is introduced.
7. **The buffer depth stays at the hls.js default (30 s) for the on-demand replay;
   the live tier gets a 3-segment buffer together with LL-HLS.** This is decided by
   measurement, not by taste: with a 30 s buffer a bandwidth cut from 8000 to
   700 kbit/s was absorbed for 22 s without a single stall, while FR-03 asks for a
   downswitch within 5 s. A deep buffer serves FR-02 and hurts FR-03; the replay
   tier has no latency requirement and keeps the deep buffer, the live tier cannot.

## Consequences

+ Every delivery parameter now rests on a number that can be reproduced with two
  commands (`pipeline/pipeline.py`, `tools/qoe_measure.mjs`).
+ The 4th rung reduces the stall ratio on the worst profile by a factor of 4
  (0.088 → 0.022) at the cost of +10 % storage and +33 % packaging time.
+ The ladder is consistent with the Lab 1 views: the top rung is still 1080p, so
  the data register, the deployment view and NFR-05 do not change.
− **NFR-01 is not met.** Standard HLS with 2 s segments and hls.js's default
  three-segment live sync has a floor of ≈ 6 s of latency; the measured startup of
  0.96 s on P1 is *time to first frame in a VOD-like playlist*, not glass-to-glass
  latency. With LL-HLS partial segments of 0.5 s the budget of Lab 1
  (0.1 + 0.4 + 0.7 + 0.3 = 1.5 s) becomes reachable; this is the open item of the
  architecture and is recorded here rather than hidden.
− 2 s segments mean 30 requests per viewer per minute: at 50 k concurrent viewers
  that is ≈ 25 000 requests/s at the CDN edge (NFR-02). 6 s segments would divide
  it by three but add ~4 s to the startup and to the switching delay, which
  contradicts NFR-01; the CDN request rate is therefore accepted and mitigated by
  caching, not by longer segments.
− The top rung is 30 fps. Lab 1 describes a 1080p50 camera; fast panning at 30 fps
  judders, and a 50/60 fps rung would cost ≈ 1.5× the bitrate at the same SSIM.
  This is a deliberate trade-off for bandwidth, to be revisited if the broadcaster
  makes 50 fps a requirement.
− Pinning the initial level to the lowest rung makes the first seconds visibly
  soft on a fast connection; the switch to 1080p happens after the first segment
  (measured: one switch on P1, no stall).
− FR-02 and FR-03 pull the buffer depth in opposite directions. With the deep
  buffer FR-03 is met in effect (the viewer sees 480p, no stall) but not in its
  5-second measure, so the acceptance criterion of FR-03 is re-phrased as "no
  visible stall and the target rung reached within 30 s"; the 5-second form is kept
  for the live tier, where the buffer is 3 segments by construction.
