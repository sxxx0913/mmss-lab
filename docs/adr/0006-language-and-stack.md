# ADR-0006. Language and technology stack of the telemetry channel

## Status
Accepted, 2026-11-19.

## Context
The channel has five parts: the emulator, the edge broker, the gateway, the server broker and the subscriber (ADR-0005). Four of them are code or a broker, and each has a different constraint.

The emulator runs on a workstation and has to produce up to 1 500 messages/s while remaining easy to change, because the value models of the sensors are the part of the lab that a student edits most.

The gateway is the only component that must survive a crash without losing a record, hold a queue on a disk, and speak two MQTT connections at once. It is also the component that the load test identified as the ceiling of the channel.

The subscriber validates every message and writes it to a store that enforces the key `(dev, boot, seq)`.

The broker implements the MQTT contract: topics, wildcards, retained messages, wills, QoS 1 with PUBACK, Keep Alive.

The measurements that inform the decision: Θ ≈ 790 messages/s for the whole channel with a 1000 kbit/s uplink, a delivery latency of 3 to 6 ms below that ceiling, 1 500 messages/s offered by the emulator in step 12 of the load test, the gateway at 50 % of its one-core quota at the ceiling, and its limit at the token bucket rather than in its code.

## Options

**Gateway language.** (a) Python with `paho-mqtt` and `sqlite3`; (b) Go with an MQTT client and a channel as the queue; (c) Node.js with an event loop and SQLite.

**Store.** (a) SQLite in WAL mode; (b) PostgreSQL; (c) a log file with an index rebuilt at start.

**Broker.** (a) Eclipse Mosquitto; (b) RabbitMQ with the MQTT plugin; (c) a broker written for the lab.

**Emulator and subscriber.** (a) Python; (b) the same language as the gateway, for uniformity.

## Decision

**Python 3.13 with `paho-mqtt` 2.1 and `sqlite3` for all three programs of the lab.** The gateway's critical path is a copy from the network into a SQLite outbox and a delete after the PUBACK; at 790 messages/s the two writes are 1 580 rows per second, which SQLite sustains with WAL and `synchronous=NORMAL` on one connection, committed in batches. The channel saturates at the uplink before the language matters: the load test holds 770 to 800 messages/s whether the offered load is 900 or 1 500, which is the signature of a limit outside the code, and `docker stats` shows the gateway at half of its core while that happens.

**SQLite for the buffer and the readings.** It gives the three properties the design needs without a server: a durable write before the send, a primary key that discards duplicates, and a query for the oldest records with an index. PostgreSQL would be the answer for many concurrent subscribers writing to one store, which is not the topology here (one subscriber per zone, ADR-0005).

**Eclipse Mosquitto 2 as the broker.** It implements the whole MQTT contract the channel uses, it is a single small process, and its configuration file is part of the report because `set_tcp_nodelay` and the listener rules change the behaviour of the gateway. A broker written for the lab would make the measurements a test of the lab's own code rather than of the architecture.

**`matplotlib` for the chart**, because it reads the CSV of the test directly and produces the log axis of the latency panel without a second tool.

## Consequences

+ One toolchain, one image, one `requirements.txt`: the three programs share the client library and the database module, and a student can run the whole lab with `python`.
+ The gateway is 300 lines and its behaviour is visible in the report: the outbox table, the cursor, the token bucket and the PUBACK queue are ordinary code, not a framework.
+ The measured ceiling of 790 messages/s is the uplink, so the choice of language does not hide the real bottleneck of the system.
− Python's global interpreter lock means the gateway's two connections and its sender share one core. At 790 messages/s that core is half used, but a gateway asked to do more per record (aggregation, filtering, a second protocol) would hit it before the uplink does. The measured headroom is the reason the decision is revisitable rather than final.
− The emulator's timing is Python timing: at 1 500 messages/s the scheduling jitter of `time.sleep` is visible in the `ts` spacing of the readings, and above the ceiling of the channel the emulator's own outgoing queue holds readings that have been generated but not sent. For a test of the channel this is acceptable; for a test of the sensor it would not be.
− SQLite holds a write lock on the database file. A second writer on the same file (a second subscriber of the same zone) would serialise behind the first, which is why the scaling answer in `docs/deployment.md` partitions by zone and not by consumer.
