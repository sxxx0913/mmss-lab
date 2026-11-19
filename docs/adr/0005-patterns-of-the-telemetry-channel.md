# ADR-0005. Publish-subscribe and buffering at the edge

## Status
Accepted, 2026-11-19.

## Context
The telemetry channel carries three wearable streams of one athlete from the room node to the database of the building server (Lab 3). Two forces shape the design.

The number of consumers is not fixed. The subscriber that validates and stores readings is one consumer today; a dashboard, an alerting rule or a second building may join later, and the sensors must not be touched when that happens.

The link between the room node and the server is a single network path, and a break is a normal mode of a distributed system. The platform must survive a break without losing readings, because a lost reading of a wearable is not recoverable from anything else: the device has no memory and QoS 0 reports nothing.

The measurements that size both decisions are in `docs/deployment.md`: 300 messages/s of three channels, 152 bytes per record with the topic and the MQTT header, an uplink of 1000 kbit/s.

## Options

**Coupling between sensors and consumers.**
(a) Sensors write to a shared database or call the subscriber directly.
(b) Sensors publish to topics, consumers subscribe (publish-subscribe).
(c) Sensors publish to a fixed queue that only one consumer may read.

**Behaviour of the link.**
(a) Nothing: a break loses the readings produced during it.
(b) The sensor retries: it keeps its own buffer, which puts the history in the wearable and loses it when the battery dies.
(c) The gateway buffers at the edge and forwards after the break (store-and-forward).
(d) A Mosquitto bridge between the two brokers with `persistence true`.

## Decision

**Publish-subscribe with a four-level topic tree.** The sensors publish to `stadium/<zone>/<device>/tele` and know nothing about consumers; a new consumer subscribes to `stadium/+/+/tele` and the sensors stay unchanged. The device state is a retained message and the disappearance of a device is the will of the broker, so a late consumer learns the state of the room in one message. This is option (b): option (a) couples every new consumer to the sensor code, and option (c) makes the queue a single point of both failure and contention.

**Buffering at the edge in the gateway.** Every message the edge broker delivers is written to an SQLite outbox before anything else, sent to the server broker with QoS 1, and deleted only when the PUBACK arrives. Option (c) is chosen over (b) because the buffer lives on a node with a disk and a power supply rather than in a wearable, and over (a) because a break must not cost readings. Option (d) was rejected for this lab because the bridge hides the buffer from the application: the report has to show the queue, its occupancy and its drain time, and a bridge gives none of the three.

Sizing by formula (4): `Vbuf = r·s·Tbreak = 300 · 152 · 60 ≈ 2.7 MB` for a 60 s break, which is about 18 000 records. The buffer is configured at 50 000 records (`BUFFER_MAX`), about 7.6 MB or 167 s of a break at 300 messages/s.

The upload rate after a break follows (5): `μ1 = R/(8·s) = 10⁶/(8·152) ≈ 822 messages/s`. During the drain the gateway still takes in 300 messages per second and shares its core between the two directions, so the forward rate is 769 and the queue falls by 469: `17 835/469 ≈ 38 s`, which is what the break measured.

**Degradation when the buffer is full.** The oldest records are discarded and counted, so the newest readings survive and the loss is visible in the gateway statistics. The outbox fills whenever the link is slower than the node's ingest, and the 200 kbit/s run reproduces it: the outbox grows by 1 049 records per second, and past `BUFFER_MAX` the oldest records go first.

## Consequences

+ A consumer joins without a change to the sensors: verified with two extra subscribers on the telemetry topic and on the retained state topic.
+ A 60 s break at 300 messages/s costs no readings: 33 383 sent, 33 383 stored, loss ratio 0, and the buffer drained in 38 s.
+ The gateway notices the break in 15 s when the interface of its container disappears, because nothing resets the socket: the loss is noticed by the keep alive of 10 s and the retry after it. A cut cable looks the same to a client, which is why the instructions give 15 to 20 s for it.
− Every reading costs two writes: the outbox insert and the delete after the PUBACK. At 300 messages/s that is 600 writes per second on the edge disk, and the insert is committed in batches of 50 or every 20 ms, because one commit per record caps the node at about 600 records per second on a bind mount of a host directory.
− The outbox grows during a break by `r` records per second, so the disk of the room node must hold the longest break the platform promises to survive. With the 50 000-record setting that is 167 s at 300 messages/s, and beyond it the oldest readings are lost by design.
− A record whose PUBACK never arrived is sent again after a reconnect, and the subscriber discards the copy by the key `(dev, boot, seq)`. Deleting on acknowledgement is what makes the retry safe.
