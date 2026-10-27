# Lab 2, session 2: adaptive streaming and QoE

Domain: **Sports Event Streaming Platform** (Lab 1, domain 8). Every number below comes from `stream/package.sh`, `stream/player.html` and `tools/qoe_measure.mjs`; the raw rows are in `results/qoe.csv`.

---

## Ladder

`stream/package.sh` builds the ladder from `data/reference.mkv`. The top rung is the lowest bitrate at which the quality is still acceptable for the domain, SSIM ≥ 0.97, which `results/encoding.csv` puts at about 2900 kbit/s; the rung is set to 3000 and the lower rungs keep the number of bits per pixel constant:

| Rung | Resolution | Declared, kbit/s | bit/pixel | Measured actual, kbit/s | SSIM | PSNR, dB |
|---|---|---|---|---|---|---|
| v0 | 1920 × 1080 | 3000 | 0.048 | 3063 | 0.9691 | 40.06 |
| v1 | 1280 × 720 | 1330 | 0.048 | 1326 | 0.9558 | 38.40 |
| v2 | 854 × 480 | 590 | 0.048 | 610 | 0.9430 | 36.67 |
| v3 (optional) | 640 × 360 | 330 | 0.048 | 359 | 0.9302 | 35.31 |

The quality column comes from decoding the packaged playlists themselves (`ffmpeg -i stream/hls/v0/index.m3u8 -i data/reference.mkv -lavfi ssim`), so it describes what a viewer receives. For v1 to v3 the reference is downscaled to the rung resolution with the same Lanczos filter the packager uses, which makes the number read as "this rung against the ideal at this resolution".

The top rung measures 0.9691. The packager runs `-preset veryfast` because it has to keep up with a live event, and that preset is about 3 % less bitrate-efficient than the `medium` preset of session 1. Reaching 0.97 exactly costs roughly 300 kbit/s on every viewer, and ADR-0004 records that as a decision.

Segments are 2 s and every rung places an IDR at each segment boundary (`-g 60 -keyint_min 60 -sc_threshold 0 -force_key_frames "expr:gte(t,n_forced*2)"`), so the player can change rung without a gap. `master.m3u8`:

```
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080,CODECS="avc1.640028",FRAME-RATE=30.000
#EXT-X-STREAM-INF:BANDWIDTH=1330000,RESOLUTION=1280x720,CODECS="avc1.64001f",FRAME-RATE=30.000
#EXT-X-STREAM-INF:BANDWIDTH=590000,RESOLUTION=854x480,CODECS="avc1.64001e",FRAME-RATE=30.000
```

## Network profiles and the prediction

Three profiles, created in DevTools under Network, Throttling, with the same values applied by `tools/qoe_measure.mjs` through `Network.emulateNetworkConditions`:

| Profile | Scenario | Download, kbit/s | Latency, ms | α·Ĉ (α = 0.7) | Predicted rung by (4) |
|---|---|---|---|---|---|
| P1 | good Wi-Fi or office LAN | 8000 | 40 | 5600 | v0 1080p (3000 ≤ 5600) |
| P2 | 4G in the stands | 2000 | 120 | 1400 | v1 720p (1330 ≤ 1400 < 3000) |
| P3 | congested 3G, or venue Wi-Fi at half-time | 700 | 400 | 490 | no rung fits (590 > 490), so the player stays on v2 and the buffer drains |

The prediction is written before the measurement. P3 sits deliberately just below the lowest rung, which is the case where an adaptive ladder has nothing left to give and the case that produced the change in ADR-0004.

## Measured QoE

60 s of content, hls.js 1.7.3 in Chrome, two runs per profile, cache disabled:

| Profile | Run | Startup, s | Switches | Stalls | Stall time, s | avg_kbps | ρ by (5) |
|---|---|---|---|---|---|---|---|
| P1 | 1 | 0.96 | 1 | 0 | 0.00 | 3000 | 0 |
| P1 | 2 | 0.96 | 1 | 0 | 0.00 | 3000 | 0 |
| P2 | 1 | 3.26 | 2 | 0 | 0.00 | 1388 | 0 |
| P2 | 2 | 3.28 | 2 | 1 | 0.04 | 1390 | 0.0007 |
| P3 | 1 | 9.20 | 2 | 23 | 5.76 | 712 | 0.088 |
| P3 | 2 | 9.15 | 2 | 24 | 5.82 | 710 | 0.088 |

Stall ratio ρ = T<sub>stall</sub> / (T<sub>play</sub> + T<sub>stall</sub>) by formula (5), with T<sub>play</sub> = 60 s.

The rung the player settled on is v0 on P1, v1 on P2, and mostly v2 on P3. P1 and P2 match the prediction exactly. On P3 the prediction was that no rung fits, and the next paragraph shows what the player does instead.

P1 and P2 land on the predicted rung: the time-averaged bitrate is 3000 kbit/s (v0) and 1389 kbit/s (v1 at 1330). P3 does not. Its average of 711 kbit/s sits between v2 (590) and v1 (1330) because the player spends about 84 % of the session on v2 and 16 % on v1: once the buffer is full a segment arrives faster than it is consumed, the bandwidth estimate rises above 1330/0.7, the player moves up to 720p, the buffer drains within one segment, a stall follows and it drops back. The average therefore runs above the profile bandwidth and the difference is paid in stalls.

---

## Link to the architecture

### Startup on P3

The page log records the first fragment being requested at **1920 × 1080**. With no `startLevel` in the player configuration, hls.js starts from the first `#EXT-X-STREAM-INF` of the manifest, which is the 3000 kbit/s rung. That segment is 750 kB, at 700 kbit/s it takes about 8.6 s to arrive, and the startup is that download plus the playlist round trips. Changing the initial level policy moves the number by a factor of 3.7 on the same profile:

| Initial level policy | Startup on P3, s (2 runs) | First fragment | Switches |
|---|---|---|---|
| manifest default | 9.20 / 9.15 | 1920 × 1080 | 2 |
| `startLevel: 0` (lowest rung) | 2.51 / 2.56 | 854 × 480 | 0 |
| `startLevel: -1` (bandwidth estimate) | 4.31 / 4.30 | 854 × 480 | 0 |

This is the trade-off of Table 1 in one line: a high initial level improves the quality of the first seconds and costs the time to first frame on a slow network. A viewer forgives two soft seconds at the start of a match, not a nine second wait, so ADR-0004 pins the lowest rung.

### Stalls on P3

The lowest rung (590 kbit/s) is only 16 % below the profile bandwidth (700), so the player cannot reduce quality any further and the buffer keeps draining. A fourth rung at 640 × 360 and 330 kbit/s (`BOTTOM=1 ./stream/package.sh`, measured with `node tools/qoe_measure.mjs --profiles P3:700:400 --src hls4/master.m3u8`) changes the outcome:

| Ladder | Startup, s | Switches | Stalls | Stall time, s | ρ |
|---|---|---|---|---|---|
| 3 rungs (3000/1330/590) | 9.20 / 9.15 | 2 / 2 | 23 / 24 | 5.76 / 5.82 | 0.088 |
| 4 rungs (with 640×360 at 330) | 9.27 / 9.29 | 3 / 3 | 1 / 1 | 1.37 / 1.39 | 0.022 |

Stalls fall from 23 to 1 and the stall ratio from 0.088 to 0.022, for 10 % more storage and 33 % more packaging time. The same encoder capacity spent on raising the top rung from 3000 to 5000 kbit/s would buy 0.012 SSIM and cost 67 % more traffic on every viewer.

### Switching on P1 and P2

P1 has 5600 kbit/s of headroom over the top rung and needs one switch (v2 to v0) after the first segment. P2 sits at 1400 against the 1330 rung, 5 % of headroom, and its second switch is the transient while the estimate settles. Neither profile stalls. Formula (4) predicts the steady state of both, and the player reaches it.

### Segment duration and latency

With 2 s segments a viewer issues 30 requests per minute. At 400 ms of latency that is 12 s of round-trip time per minute of playback, 20 % of the session spent waiting for requests before any video is counted. This is why P3 is qualitatively worse than a bandwidth-only profile at the same 700 kbit/s would be, and why the 4 s and 6 s alternatives in ADR-0004 lose: they cut the request rate but add 2 to 4 s to the startup and to every switch, which breaks the latency budget of Lab 1.

### Segment duration and server load

30 requests per minute per viewer at 50,000 concurrent viewers is about 25,000 requests/s at the CDN edge, against 8,300 with 6 s segments. ADR-0004 keeps 2 s and mitigates the request rate with caching, since longer segments would cost the latency that NFR-01 exists to protect.

### Buffer depth and a bandwidth cut

Cutting the bandwidth from 8000 to 700 kbit/s at t = 12 s of playback (`node tools/qoe_measure.mjs --profiles P1:8000:40 --drop-after 12 --drop-to P3`), the player stayed at 1080p for 22 s and then moved straight to 480p:

```
    1.7s  first frame visible · startup 0.94 s
    1.7s  level → 1920×1080 (3000 kbit/s) · abr
   [t = 12 s: 8000 → 700 kbit/s]
   33.8s  level → 854×480 (590 kbit/s) · abr
   61.8s  playback finished        switches 2 · stalls 0 · avg 1879 kbit/s
```

hls.js held about 20 s of content (`maxBufferLength` is 30 s by default) and has no reason to change level while the buffer is not empty, so the switch waits for the buffer to drain. The result is the trade-off Table 1 gives for buffer depth in both directions: the deep buffer absorbed the cut with zero stalls, and the reaction took 22 s. A buffer of a few seconds reacts within one segment and stalls on every fluctuation. ADR-0004 keeps the deep buffer for the replay tier and gives the live tier a three-segment buffer together with LL-HLS.

## Check of the Lab 1 requirements and quality scenarios

| Lab 1 item | Result of Lab 2 |
|---|---|
| FR-02, ≤ 2 stalls in a 90-minute session on 10 Mbit/s | satisfied on P1: 0 stalls in 60 s, one switch after the first segment. P1 at 8000 kbit/s is the closest measured proxy for the 10 Mbit/s of the requirement. |
| FR-03, drop from 1080p to 480p within 5 s of bandwidth loss | with the cut from 8000 to 700 kbit/s at t = 12 s the player reached 480p after 22 s with no stall, because 20 s of content sat in the buffer. A 5 s reaction needs `maxBufferLength` of a few seconds; ADR-0004 sets that for the live tier and keeps the deep buffer for replay. |
| NFR-01, L<sub>total</sub> ≤ 1.5 s for 95 % of segments | requires LL-HLS. Standard HLS with 2 s segments holds a floor of about 6 s (three segments of live sync in hls.js), and the 0.96 s on P1 is time to first frame, not glass-to-glass latency. The FFmpeg HLS muxer cannot emit `EXT-X-PART`, so this iteration delivers the ladder, segment and player configuration that LL-HLS reuses unchanged. |
| NFR-02, 10k to 50k viewers with ≤ 10 % more latency and ≤ 2 more stalls | needs a load generator. The request rate above (25,000 requests/s) is the input for that test. |
| NFR-05, archive ≤ 0.10 EUR per event-hour | improved: at equal quality AV1 needs 27 % to 37 % fewer bits, which turns the 21.6 GB per event of Lab 1 into about 14.4 GB on the object storage of ADR-0003. |
| QS-01, 100k viewers in 5 s with ABR fallback | the adaptive fallback is implemented and measured on three profiles; the burst itself needs the load generator. |
| QS-02, encoder crash with failover ≤ 3 s | outside the scope of this lab. The pipeline here is a single batch process; in production it runs on the hot-standby pair of ADR-0001. |

The delivery tier of the Lab 1 architecture holds up under measurement: the ladder reaches its predicted rungs on two of three profiles, FR-02 is met, and the one profile that misses the prediction fails in a way that names its own fix, which became the fourth rung and the initial level policy of ADR-0004. The latency budget of NFR-01 stays open until LL-HLS replaces the segmenter, and the ladder, segment and player parameters measured here carry over to it unchanged.
