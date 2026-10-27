# ADR-0004. Pipeline parameters and delivery configuration of the live tier

## Status
Accepted, 2026-10-26. Dimensions the ladder that ADR-0002 named without numbers. The LL-HLS part of ADR-0002 is not part of this iteration.

## Context
Lab 1 fixed the architecture of the platform: encoding on an edge node at the venue (ADR-0001), LL-HLS with an adaptive bitrate ladder (ADR-0002), object storage with lifecycle tiering (ADR-0003). Three delivery parameters stayed open, and Lab 2 makes all three measurable:

1. the codec that carries the live stream, where Lab 1 assumed H.264 at about 8 Mbit/s for the archive without comparing it with anything;
2. the bitrates of the ladder rungs, which ADR-0002 named as 1080p, 720p and 480p without numbers, so neither NFR-01 nor NFR-05 could be checked against the delivery configuration;
3. the segment duration and the level the player starts on, which the architecture description did not constrain and which turned out to dominate QoE on a slow network.

The evidence is `results/encoding.csv` (four variants, 60 s, 1080p30), `results/qoe.csv` (three network profiles, two runs each), the ladder verification and the three controlled experiments reported in `docs/qoe.md`.

| Fact | Value | Source |
|---|---|---|
| Uncompressed rate by formula (1) | 1.49 Gbit/s | `encoding.csv` |
| H.264 at 1080p30 reaching SSIM 0.97 | about 2900 kbit/s, interpolated | `encoding.csv` |
| AV1 saving at equal PSNR / equal SSIM | 37 % / 27 % | `encoding.csv` |
| AV1 encoding time against H.264 | ×1.25 at 2500 kbit/s, ×1.04 at 5000 | `encoding.csv` |
| Stall ratio on P3, three rungs | 0.088 | `qoe.csv` |
| Stall ratio on P3, four rungs | 0.022 | `docs/qoe.md` |
| Startup on P3, default initial level | 9.2 s | `qoe.csv` |
| Startup on P3, `startLevel: 0` | 2.5 s | `docs/qoe.md` |

## Options

**Codec.** (a) H.264 only; (b) AV1 only; (c) H.264 for live and AV1 for the archive.

**Top rung.** (a) 2500 kbit/s, SSIM 0.9677, below the 0.97 target; (b) 3000 kbit/s, the interpolated 0.97 point at 0.048 bit/pixel; (c) 5000 kbit/s, SSIM 0.9815 for 1.7 times the traffic.

**Ladder depth.** (a) three rungs at 1080p, 720p and 480p; (b) four rungs, adding 640 × 360 at 330 kbit/s.

**Segment duration.** (a) 2 s; (b) 4 s; (c) 6 s.

**Initial level.** (a) the manifest default, which measured at 1080p; (b) the lowest rung through `startLevel: 0`; (c) the bandwidth estimate through `startLevel: -1`.

## Decision

1. **H.264 for live, AV1 for the archive.** AV1 saves 27 % to 37 % of the traffic at equal quality, and the live path is decoded by 50,000 to 100,000 viewers on unknown devices, where H.264 has hardware decoding almost everywhere and AV1 still falls back to software. In the archive path the file is encoded once and decoded rarely, so the saving becomes permanent storage and egress, which serves NFR-05: 21.6 GB per event becomes about 14.4 GB. The 1.25× encoding cost of AV1 is irrelevant for an offline job and decisive for a live one.
2. **Ladder at 3000, 1330 and 590 kbit/s for 1920 × 1080, 1280 × 720 and 854 × 480**, constant 0.048 bit/pixel, produced by `stream/package.sh`. 3000 kbit/s is the lowest rung at which SSIM reaches 0.97; the packaged rung measures 0.9691 and 40.06 dB. The packager runs `-preset veryfast` to keep up with a live event and that preset is about 3 % less bitrate-efficient than the `medium` preset of session 1, which accounts for the last 0.001. Raising the rung to 5000 kbit/s buys 0.012 SSIM for 67 % more traffic on every viewer.
3. **A fourth rung at 640 × 360 and 330 kbit/s is packaged but kept out of the default manifest** (`BOTTOM=1 ./stream/package.sh`). On P3 the three-rung ladder cannot reduce quality below 590 kbit/s and the stall ratio is 0.088; with the fourth rung available it is 0.022. The rung is published to viewers whose measured bandwidth stays below about 500 kbit/s.
4. **Segments of 2 s with pinned IDRs** (`-g 60 -sc_threshold 0 -force_key_frames`). Two seconds keeps the startup and the switching delay at one segment while holding the request rate at 30 per viewer per minute. The boundaries coincide in all rungs, which is what lets the player switch without a stall.
5. **Initial level pinned to the lowest rung** (`new Hls({startLevel: 0})`). With the manifest default the player fetches the first segment at 1920 × 1080, 750 kB, and needs 9.2 s to show a frame on P3. Pinning the lowest rung gives 2.5 s and the bandwidth estimate 4.3 s. The cost is up to one segment of low quality at the start, which is invisible next to a 6.7 s shorter wait.
6. **Standard HLS in this iteration.** The FFmpeg HLS muxer cannot emit `EXT-X-PART`, so LL-HLS as declared in ADR-0002 cannot be produced with the tool set of this lab. The ladder, segment and player parameters decided here do not depend on it and carry over unchanged.
7. **Buffer depth follows the tier.** The replay tier keeps the hls.js default of 30 s, which absorbed a cut from 8000 to 700 kbit/s for 22 s with zero stalls. The live tier gets a three-segment buffer together with LL-HLS, because FR-03 asks for a reaction within 5 s and a deep buffer cannot deliver it.

## Consequences

+ Every delivery parameter now rests on a number that two commands reproduce (`pipeline/pipeline.py` and `tools/qoe_measure.mjs`).
+ The fourth rung cuts the stall ratio on the worst profile by a factor of 4, at 10 % more storage and 33 % more packaging time.
+ The ladder stays consistent with the Lab 1 views: the top rung is still 1080p, so the data register, the deployment view and NFR-05 keep their shape.
− The 1.5 s latency of NFR-01 needs LL-HLS partial segments. Standard HLS with 2 s segments has a floor of about 6 s, and the 0.96 s measured on P1 is time to first frame rather than glass-to-glass latency. With partial segments of 0.5 s the Lab 1 budget (0.1 + 0.4 + 0.7 + 0.3 = 1.5 s) becomes reachable, and the ladder and player configuration measured here are reused as they are.
− 2 s segments mean 30 requests per viewer per minute, about 25,000 requests/s at the CDN edge for 50,000 viewers (NFR-02). Six-second segments would divide that by three and add 4 s to the startup and to every switch, so the request rate is mitigated by caching instead.
− The top rung is 30 fps. Lab 1 describes a 1080p50 camera, fast panning at 30 fps judders, and a 50 or 60 fps rung costs about 1.5 times the bitrate at the same SSIM. The trade-off favours bandwidth and is revisited if the broadcaster makes 50 fps a requirement.
− Pinning the initial level to the lowest rung makes the first seconds visibly soft on a fast connection. The switch to 1080p follows the first segment, measured as one switch and no stall on P1.
− FR-02 and FR-03 pull the buffer depth in opposite directions. The deep buffer serves FR-02 and delays the downswitch that FR-03 measures, which is why the two tiers carry different buffer settings and why FR-03 is read as "no visible stall, target rung reached within 30 s" for replay.
