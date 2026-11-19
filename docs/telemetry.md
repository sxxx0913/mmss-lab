# Telemetry channel of the sports streaming platform

The athlete wearables of the platform (Lab 1, domain 8) publish pulse, speed and acceleration to the broker of the edge node. The subscriber on the server validates every reading against the schema below and stores it in SQLite.

## Topics

A topic has four levels: site, zone, device and kind.

| Topic | Kind | QoS | Retained | Publisher |
|---|---|---|---|---|
| `stadium/pitch/ath07-pulse/tele` | telemetry, 3 readings per second | 0 | no | the pulse sensor |
| `stadium/pitch/ath07-speed/tele` | telemetry, 3 readings per second | 0 | no | the speed sensor |
| `stadium/pitch/ath07-acc/tele` | telemetry, 3 readings per second | 0 | no | the acceleration sensor |
| `stadium/pitch/ath07-<qty>/state` | device state, `online` or `offline` | 1 | yes | the sensor, and the broker for the will |

The subscriber takes `stadium/+/+/tele` and `stadium/+/+/state`. The gateway takes the same wildcard on the edge broker and republishes everything to the server broker. A new consumer joins by subscribing to the wildcard, and nothing in the sensors changes.

Telemetry goes out with QoS 0. A single reading of a wearable is worth less than the cost of a round trip, and a loss is recovered from the sequence number: the subscriber reports it as a gap and the platform keeps working with the readings it has. The state messages and the gateway use QoS 1, because a lost `online` or a lost record in the buffer is not recoverable from anything else.

The state topic is retained, so a consumer that starts late learns the state of every device at once. The will is published by the broker when a sensor disappears without a clean disconnect, which is what a crash of the emulator looks like from the broker.

## Message schema

One reading is one JSON object of 110 bytes, 140 bytes with its topic:

```json
{"dev":"ath07-pulse","boot":1,"seq":4182,"ts":"2026-11-19T08:41:20.123Z","qty":"pulse","val":142,"unit":"bpm"}
```

| Field | Type | Unit or format | Purpose | Range or check |
|---|---|---|---|---|
| `dev` | string | `<athlete>-<quantity>` | names the device, so a consumer can route without the topic | must equal the third topic level |
| `boot` | integer | 1, 2, 3 ... | the run of the emulator; a restart resets `seq` | ≥ 1, one value per run |
| `seq` | integer | 0, 1, 2 ... | per-device counter inside a boot; the only source of loss information | ≥ 0, monotonic per device |
| `ts` | string | ISO 8601, UTC, milliseconds, `Z` | the moment of measurement; the base of the latency of formula (3) | within a day of the receive time |
| `qty` | string | `pulse`, `speed`, `acc` | the quantity, so one schema serves three sensors | one of the three |
| `val` | number | bpm, m/s, m/s² | the reading itself | datasheet range, see below |
| `unit` | string | `bpm`, `m/s`, `m/s2` | guards against a value without meaning | must belong to `qty` |

Datasheet ranges: pulse 30 to 220 bpm, speed 0 to 12 m/s, acceleration −16 to 16 m/s². A value outside its range is rejected rather than stored, because a wearable that reports 900 bpm is broken and not merely inaccurate.

`boot` and `seq` together answer the question the protocol does not: MQTT with QoS 0 reports nothing about a lost message and with QoS 1 it may deliver the same message twice. The pair makes both visible, and `dev` separates the streams that share a topic tree.

## Error handling policy

| Situation | Detected by | Action | Recorded in |
|---|---|---|---|
| payload is not JSON | `json.JSONDecodeError` in the subscriber | reject the message | `rejected`, reason `not JSON` |
| a required field is missing | key check against the schema | reject | `rejected`, reason lists the fields |
| a field has the wrong type | type check, `bool` excluded from numbers | reject | `rejected` |
| `dev` does not match the topic | comparison with the third topic level | reject | `rejected` |
| `qty` is unknown | membership in the quantity table | reject | `rejected` |
| `unit` does not belong to `qty` | unit table | reject | `rejected` |
| `val` is outside the datasheet range | range check per quantity | reject | `rejected` |
| `ts` is not ISO 8601 UTC | `datetime.fromisoformat`, `Z` required | reject | `rejected` |
| the same reading arrives twice | the primary key `(dev, boot, seq)`, plus the in-memory set of the subscriber | store once, count the copy | `anomalies`, kind `duplicate` |
| a sequence number is skipped | formula (1), `n = s_k − s_(k−1) − 1` | store the reading, count the gap | `anomalies`, kind `gap`, column `missing` |
| a reading arrives below the highest number of its boot | formula (1), `n < 0` and not a duplicate | store it, count it | `anomalies`, kind `out_of_order` |
| a sensor disappears without disconnecting | the will of the broker | mark the device offline | `states`, state `offline` |
| the link to the server breaks | the gateway loses its connection to the server broker | keep every record in the buffer, upload after the reconnect | `gateway.db`, `outbox` |
| the buffer is full | record count above `BUFFER_MAX` | discard the oldest records, count them | gateway log and `data/gateway_stats.json` |
| the clocks of two nodes disagree | assumed synchronised by NTP | not implemented; on one host the two timestamps come from the same clock | — |
| a reading arrives late but within the schema | latency above the threshold of the domain | not implemented as a separate action; it shows in the latency of `stats.py` and in `out_of_order` | `readings.latency_ms` |

The subscriber plays the role of the dead letter queue that MQTT does not define: a rejected message is stored with its raw payload and its reason, so a fault in a sensor can be diagnosed after the fact instead of disappearing.

## Channel check

The emulator ran for 30 s at 20 readings per second from each of the three sensors, 1800 readings in total, with four kinds of injected fault: 2 % dropped, 1 % duplicated, 1 % late, 1 % corrupted.

```bash
python smart/sensors.py            # HZ=20 DURATION=30 DROP=0.02 DUP=0.01 LATE=0.01 BAD=0.01
python smart/stats.py
```

| Injected | Count | Detected | Count |
|---|---|---|---|
| dropped readings | 37 | missing from the sequence numbers | 37 |
| corrupted readings | 17 | rejected with a reason | 17 |
| duplicated readings | 14 | duplicates discarded by the key | 14 |
| late readings | 12 | out of order | 11 |

```
 boot   lambda    sent    recv    loss   gaps   dup   ooo   rej  mean ms   p95 ms   max ms
    1     55.8    1800    1746    0.03     65    14    11    17     13.9      1.0   2964.0
```

The loss ratio of formula (2) is 1 − 1746/1800 = 0.030, which is exactly the 2 % of dropped readings plus the 1 % of rejected ones: a rejected reading is lost for the database even though the subscriber saw it. The 65 gaps are larger than the 37 dropped readings because a late reading is counted as a gap when the next number arrives and as out of order when it finally comes, and a rejected reading leaves a permanent hole in the sequence.

The twelve late readings also explain the latency: the 95th percentile is 1.0 ms, while the mean of 13.9 ms is pulled up by the readings published up to three seconds after they were measured, and the largest value of 2964 ms is one of them. A run of 20 s without injected faults gives 1200 of 1200 readings, no gaps, and a mean latency of 0.42 ms with a maximum of 2 ms.

The rejected messages, by reason:

| Reason | Count |
|---|---|
| `not JSON` | 4 |
| `ts` is not an ISO 8601 UTC string | 3 |
| `val` outside the datasheet range | 3 |
| `dev` does not match the topic | 3 |
| `qty` unknown | 2 |
| `unit` does not belong to `qty` | 2 |
