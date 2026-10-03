# ADR-0003. Object storage with lifecycle tiering for archives

## Status
Accepted, 2026-09-17

## Context
NFR-05 caps archive cost at 0.10 EUR per event-hour; an event is ~21.6 GB
across 4 cameras.

## Options
1. Block storage on the encoder (fast, expensive, does not scale).
2. Object storage, hot tier only (simple, but cost grows).
3. Object storage with lifecycle tiering to cold after 30 days (meets cost).

## Decision
Option 3. Archives go to object storage; a lifecycle rule moves them to cold
tier after 30 days.

## Consequences
+ Satisfies NFR-05 and FR-05 (portal replay within 30 min stays in hot tier).
+ Scales to a full season without redesign.
- Cold-tier replays have higher retrieval latency.
- Requires a lifecycle policy to be defined and monitored.
