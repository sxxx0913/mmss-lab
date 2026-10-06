# ADR-0002. Low-latency HLS with adaptive bitrate

## Status
Accepted, 2026-09-16

## Context
Viewers join on very different connections (NFR-02) and expect near-live
latency (NFR-01, <= 1.5 s) at scale (up to 100k concurrent).

## Options
1. Single fixed bitrate (simple, but breaks on slow connections).
2. Progressive download MP4 (robust but too slow, tens of seconds latency).
3. LL-HLS + adaptive bitrate ladder (meets latency and scalability).

## Decision
Option 3. Use LL-HLS with a bitrate ladder (1080p/720p/480p) so the player
adapts automatically.

## Consequences
+ Meets NFR-01 and NFR-02 together.
+ Viewer player degrades gracefully on slow links (FR-03).
- Requires segment-based packaging and CDN support.
- Slightly higher infrastructure complexity than a single stream.
