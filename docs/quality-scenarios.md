# Quality Attribute Scenarios for Sports Event Streaming Platform

Two quality attribute scenarios in the six-element form of ISO/IEC/IEEE 42010 and SEI ATAM: the delivery tier under a connection burst, and the encoding chain under an encoder failure.

---

## QS-01 (Performance and scalability under a traffic surge)

* **Attribute**: performance under load, scalability
* **Stimulus source**: 100,000 viewers, twice the 50,000 of NFR-02, connect within 5 seconds during a critical match moment.
* **Stimulus**: high-density burst of manifest, playlist and segment requests, with keyframe-aligned requests at every segment boundary.
* **Environment**: normal operation during live tournament broadcasting.
* **Artifact**: packager and segment store at the edge, CDN delivery network, LL-HLS origin.
* **Response**: the platform serves the burst from cached segments at three quality levels; each player selects its level from its own bandwidth estimate by formula (4), so a slow client degrades its own quality without affecting the rest.
* **Response measure**:
  1. $L_{total} \le 1.5\text{ s}$ for 95% of segments on the LL-HLS tier.
  2. $\le 2$ stalls per session and 0% failed connections during the burst.
  3. Request rate at the CDN edge stays within the capacity planned from the segment duration (25,000 requests/s at 50,000 viewers with 2 s segments, docs/qoe.md).
* **Provided by**: LL-HLS with an adaptive bitrate ladder (ADR-0002), the measured ladder, segment duration and initial level policy (ADR-0004), and the placement of encoding and packaging on the edge node (ADR-0001, components and deployment views).

---

## QS-02 (Availability under an encoder failure)

* **Attribute**: availability, fault tolerance
* **Stimulus source**: the primary encoder node at the venue stops responding mid-match.
* **Stimulus**: heartbeat timeout reported by the stream controller.
* **Environment**: live broadcast of an ongoing event, top rung at 3.0 Mbit/s.
* **Artifact**: stream controller, primary encoder, hot-standby encoder.
* **Response**: the controller moves the pipeline to the standby encoder and the packager reconnects to it; the viewer keeps the same session and the same manifest.
* **Response measure**:
  1. Failover completes within 3 s.
  2. Visible frame drop at most 1 s, no dropped viewer connections.
* **Provided by**: hot-standby encoder and stream controller orchestration (FR-08), edge placement of the encoding chain (ADR-0001), and the segment configuration that keeps the playlist valid across the switch (ADR-0004).
* **Open item**: a physical cut of the venue uplink stops ingestion regardless of the standby encoder. Multi-homed bonded cellular or satellite uplink is the candidate mitigation, to be evaluated in a later iteration.
