# ADR-0001. Placing encoding on an edge node at the venue

## Status
Accepted, 2026-09-15

## Context
NFR-01 caps latency at 1.5 s. Raw main-camera video is 2.49 Gbit/s, too much
to push over the venue uplink to a distant cloud region.

## Options
1. Encode in the cloud (easy model updates, but breaks the network budget and
   risks the uplink).
2. Encode on an edge node at the venue (fits the budget; only compressed
   streams leave the venue; harder to update encoder software).
3. Hybrid (costly, more moving parts).

## Decision
Option 2. Encoding runs on an edge node at the venue; only the compressed
H.264/AAC stream (~8 Mbit/s) goes over the network.

## Consequences
+ Only compressed stream and metadata cross the venue network.
+ Latency budget for the network is met.
- Encoder software updates must be pushed to edge devices.
- Edge resources (CPU, storage) become a constraint to plan for.
