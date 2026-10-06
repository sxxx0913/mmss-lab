# Quality Attribute Scenarios for Sports Event Streaming Platform

This document presents two quality attribute scenarios according to ISO/IEC/IEEE 42010 and SEI ATAM standards, validating the platform's response under high-concurrency spikes and node failures.

---

## QS-01 (Performance & Scalability Under Traffic Surge)

* **Attribute**: Performance under load / Scalability
* **Stimulus source**: 100,000 viewers connect within 5 seconds during a critical match moment.
* **Stimulus**: High-density burst of stream connection and keyframe requests.
* **Environment**: Normal operation during live tournament broadcasting.
* **Artifact**: CDN delivery network, WebRTC Signaling Engine, and LL-HLS Origin Server.
* **Response**: Platform maintains continuous streaming; non-interactive viewers are dynamically offloaded to LL-HLS with adaptive bitrate (ABR) to preserve system stability.
* **Response measure**: 
  1. $L_{total} \le 1.5\text{ s}$ for 95% of segments on the LL-HLS fallback tier.
  2. Ultra-low latency tier ($L_{total} \le 150\text{ ms}$) preserved for primary WebRTC interactive users.
  3. $\le 2$ stalls per session; $0\%$ failed connection rate.
* **Provided by**: LL-HLS adaptive bitrate fallback (ADR-0003), GPU hardware acceleration for multi-bitrate encoding (ADR-0002), and Edge Distribution Nodes (Component & Deployment View).

---

## QS-02 (Reliability & High Availability)

* **Attribute**: Availability / Fault Tolerance / Resilience
* **Stimulus source**: Primary GPU-accelerated edge encoder node crashes mid-match.
* **Stimulus**: Heartbeat timeout / Encoder failure signal detected by orchestrator.
* **Environment**: High-bitrate live broadcasting of an ongoing sports event.
* **Artifact**: Stream Controller, Primary Encoder, and Hot-Standby Encoder.
* **Response**: Controller instantly reroutes video pipeline to the secondary hot-standby encoder; live video playback continues without session termination.
* **Response measure**: 
  1. Failover execution completed within $\le 3\text{ s}$.
  2. Frame drop visible to end-users $\le 1$ second; active connection drop = $0\%$.
* **Provided by**: Standby Encoder + Stream Controller orchestration (FR-08, ADR-0001 WebRTC Transport, ADR-0002 GPU Encoder Pipeline).
* **Known weakness**: If the primary sports venue network uplink physically cuts off, live ingestion halts.
* **Mitigation to evaluate in next lab**: Multi-homed bonded cellular/satellite redundant uplink strategy.
