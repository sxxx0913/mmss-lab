# System Requirements: Sports Event Streaming Platform
## Purpose
The Sports Event Streaming Platform captures a live sports event from multiple cameras and microphones, encodes the feeds, and broadcasts them over the internet to remote viewers. It also archives the event for on-demand replay in the club's media portal.
## Classification
- **Purpose**: information delivery (live broadcast) + monitoring (event capture).
- **Autonomy**: semi-autonomous (auto camera framing and auto bitrate, humans run the production).
- **Interaction with the environment**: high (continuous sensing of video, audio, and telemetry; continuous feedback to viewers).
## Stakeholders
| Party | Interest | What is critical for them |
|---|---|---|
| Event broadcaster | Run the live show | Cameras switch on time, no dead air |
| Remote viewer | Watch the match live | Latency <= 1.5 s, no freezing |
| Platform administrator | Configure and maintain | Simple setup, reliable recording |
| Club / media-portal owner | Store and re-publish the event | No data loss, stable replay links |
## Data Register
| Stream | Source | Modality | Format | Volume per unit time | Rate | Criticality | Acceptable losses |
|---|---|---|---|---|---|---|---|
| Main camera | Cam A 1080p50 | video | RGB 24-bit | 1920 × 1080 × 50 × 24 bit | 2.49 Gbit/s (raw) | critical | 0% (live) |
| Secondary camera | Cam B 1080p50 | video | RGB 24-bit | 1920 × 1080 × 50 × 24 bit | 2.49 Gbit/s (raw) | critical | 0% |
| Commentary | Commentary booth | audio | PCM 48 kHz | 48000 × 16 bit | 0.77 Mbit/s | high | <1% |
| Telemetry | Wearable sensors | numeric | JSON | ~20 msg/s × 200 B | 32 kbit/s | low | <5% |
| Archive file | Encoder output | video+audio | H.264/AAC | ~9 MB per minute | 8 Mbit/s (compressed) | high | 0% (archive) |
**Data Volume Calculation:**
- **Main camera uncompressed**: R = w × h × f × d = 1920 × 1080 × 50 × 24 bit = 2,488,320,000 bit/s ≈ **2.49 Gbit/s**
- **Compressed (H.264, ~8 Mbit/s)**: A 90-minute event = 8 Mbit/s × 5400 s = 5.4 GB per camera; with 4 cameras = ~21.6 GB per event.
## Functional Requirements
- **FR-01.** As an event broadcaster, I want to switch between camera feeds so that the viewer always sees the best angle.
- *Acceptance:* A manual switch completes within 200 ms in 9 out of 10 cases.
- **FR-02.** As a remote viewer, I want to watch the match without buffering so that I follow the play continuously.
- *Acceptance:* <= 2 stalls in a 90-minute session on a 10 Mbit/s connection.
- **FR-03.** As a remote viewer, I want adaptive quality so that the stream keeps playing on a slow connection.
- *Acceptance:* Player drops from 1080p to 480p within 5 s of bandwidth loss.
- **FR-04.** As an event broadcaster, I want the commentary mixed into the broadcast so that viewers hear the play-by-play.
- *Acceptance:* Audio is present and A/V offset <= 80 ms.
- **FR-05.** As a club owner, I want the event archived automatically so that it can be replayed later.
- *Acceptance:* An archive is available in the portal within 30 min after the event.
- **FR-06.** As a platform administrator, I want a health dashboard so that I see every encoder and node in one place.
- *Acceptance:* Dashboard refresh <= 5 s; 100% of nodes represented.
- **FR-07.** As a remote viewer, I want telemetry overlays (score, speed) so that I get richer context.
- *Acceptance:* Overlay lag behind real event <= 2 s for 95% of updates.
- **FR-08.** As an event broadcaster, I want a failover encoder so that the show survives one encoder crash.
- *Acceptance:* Failover completes within 10 s with no viewer-visible drop.
## Non-functional Requirements
- **NFR-01 (real time).** End-to-end glass-to-glass latency L_total <= 1.5 s for 95% of segments. *Measurement:* Reference clock captured, timestamp at display, 10-min sample.
- **NFR-02 (scalability).** Going from 10k to 50k concurrent viewers raises L_total by <= 10% and stalls by <= +2 per session. *Measurement:* Load test.
- **NFR-03 (availability).** Broadcast success rate >= 99.9% during a scheduled event. *Measurement:* Per-event uptime over a season.
- **NFR-04 (reliability).** Single encoder/node failure does not interrupt the show; failover <= 10 s. *Measurement:* Chaos drill on staging.
- **NFR-05 (storage cost).** Archive cost <= 0.10 EUR per event-hour on hot storage; cold storage tier after 30 days. *Measurement:* Monthly billing report.
- **NFR-06 (maintainability).** Adding a new camera position requires no change to the core encoder. *Measurement:* Change-impact review.
**Latency Budget Allocation (Target L_total <= 1500 ms):**
- L_capture = 100 ms (sensor + camera pipeline)
- L_encode = 400 ms (H.264 encode + LL-HLS segmenting)
- L_net = 700 ms (CDN delivery, the most variable part)
- L_player = 300 ms (client buffer + decode + render)
- **Sum = 1500 ms**
## View to Stakeholder Mapping
| View | Viewpoint | Stakeholder | Concern it addresses |
|---|---|---|---|
| Context | C4 level-1 / context | Remote viewer | Getting the stream without loss |
| Component | Module decomposition | Platform administrator | Maintainability of subsystems |
| Deployment | Deployment view | Event broadcaster | Low latency via edge placement |
