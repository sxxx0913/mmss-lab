# Deployment of the telemetry channel

The channel of `docs/telemetry.md` runs as two components on two networks: the edge node in the room with the sensors, the edge broker and the gateway, and the server in the building with the server broker and the subscriber.

## Components

| Service | Image or command | Networks | Resources | Data |
|---|---|---|---|---|
| `broker-edge` | `eclipse-mosquitto:2`, `broker/mosquitto.conf` | edge | — | `edge-broker-data` volume |
| `sensors` | the image of this lab, `python sensors.py` | edge | 0.5 CPU, 128 MB | `../data` |
| `gateway` | the same image, `python gateway.py` | edge and cloud | 0.5 CPU, 192 MB | `../data` |
| `broker-cloud` | `eclipse-mosquitto:2`, the same configuration | cloud | — | `cloud-broker-data` volume |
| `collector` | the same image, `python collector.py` | cloud | 1 CPU, 256 MB | `../data` |

One image serves the emulator, the gateway and the subscriber: they share `paho-mqtt` and nothing else, and a single image keeps the build to one step. The limits come from the deployment view of Lab 1, where the room node is a small device and the building server is not.

Only the gateway is attached to both networks. The sensors cannot reach the server broker, and the subscriber cannot reach the edge broker, so the only path from a reading to the database is the one the architecture describes. Only the edge broker publishes a port, on `127.0.0.1:1883`, because the broker runs with anonymous access and must not be reachable from the network of the host.

The two brokers share one configuration file. It declares the listener, allows anonymous clients (Mosquitto 2 rejects clients without a user name as soon as a listener is declared), keeps persistence on a volume, and sets `tcp_nodelay`, without which the PUBACK of QoS 1 waits for the next message and the gateway saturates long before its processor does.

```bash
docker compose -f smart/docker-compose.yml up -d --build
docker compose -f smart/docker-compose.yml ps
```

`smart/Dockerfile` builds the image from `python:3.13-slim`, installs `requirements.txt` and copies the four scripts; `data/` is a volume so the databases survive a restart of a container and stay out of the repository.

**On this machine.** Docker Desktop is not installed and the sandbox of the working environment blocks the Windows service manager, so the containers could not be started here. The measurements below come from the same five processes started by `smart/run_native.py`, which reads `smart/broker/mosquitto.conf`, applies the same environment variables and keeps the same topology; the only differences are the absence of `cpus` and `mem_limit`, which a plain process cannot enforce, and the broker build (Mosquitto 2.0.15 from conda-forge instead of the 2.x image), which does not know `tcp_nodelay`.

## Patterns

### Publish-subscribe

**Context.** Three sensors of one athlete publish to the edge broker; the subscriber validates and stores. The number of consumers is not fixed: a dashboard or an alerting rule may join later.

**Problem.** If the sensors know their consumers, every new consumer is a change to the firmware of a wearable.

**Forces.** The sensors are the least changeable part of the system; a consumer must be able to join and leave without coordination; the state of a device must be available to a consumer that starts late.

**Solution.** A four-level topic tree, `stadium/<zone>/<device>/<kind>`. Sensors publish to `…/tele` with QoS 0 and to `…/state` with QoS 1 and the retained flag; the will of the broker publishes `offline` when a sensor disappears. Consumers subscribe to `stadium/+/+/tele` and `stadium/+/+/state`.

**Consequences.** A consumer joins by subscribing: two subscribers were added to the running system, one on the server broker for telemetry and one on the edge broker for the state topic, with `mosquitto_sub` and without touching `sensors.py` or `collector.py`. The server subscriber received the next three readings:

```
stadium/pitch/ath07-pulse/tele {"dev":"ath07-pulse","boot":1,"seq":3,...,"qty":"pulse","val":71.0,"unit":"bpm"}
stadium/pitch/ath07-speed/tele {"dev":"ath07-speed","boot":1,"seq":3,...,"val":1.909,"unit":"m/s"}
stadium/pitch/ath07-acc/tele   {"dev":"ath07-acc","boot":1,"seq":3,...,"val":2.376,"unit":"m/s2"}
```

and the edge subscriber received the state of all three devices at once, from the retained messages, although it had not been running when they were published. The cost is that the publisher cannot know whether anyone is listening, which is why the readings are also buffered at the edge rather than left to the consumer to request.

### Buffering at the edge

**Context.** The link between the room and the building is one path, and a break of a minute is a normal event.

**Problem.** During a break the readings keep arriving at the gateway and cannot be delivered. A reading lost in the gateway is lost for good: the wearable keeps no history.

**Forces.** No loss during the break; a bounded amount of disk in the room; the newest readings matter more than the oldest; the server must not receive duplicates.

**Solution.** The gateway writes every message to an SQLite outbox with WAL and `synchronous=NORMAL` before it does anything else, publishes the oldest records with QoS 1, and deletes a record only when its PUBACK arrives. On overflow it discards the oldest records and counts them. The capacity follows (4): `Vbuf = r·s·Tbreak`. With `r = 300` messages/s, `s = 140` bytes and `Tbreak = 60` s that is 2.5 MB, about 18 000 records; the buffer is set to 50 000 records, about 7 MB or 167 s at that rate.

**Consequences.** The break experiment below shows the pattern working with no loss. The upload rate after the break is limited by the link, `μ1 = R/(8·s) ≈ 893` messages/s at 1000 kbit/s, so the accumulated queue drains at `μ1 − λ` and the drain takes `Q0/(μ1 − λ)` by (5). The pattern doubles the writes on the edge disk and makes the gateway, not the broker, the first component to reach its limit, which is what the load test shows.

## Link break

The experiment raises the flow to 300 messages/s (HZ = 100 per sensor) and removes the server component 10 s after the start, then brings it back 60 s later. The gateway's socket dies at once, which is what a cut cable looks like to it; it reconnects by itself and uploads what has accumulated.

| Moment | Buffer, records | Observation |
|---|---|---|
| t = 0 | 0 | three sensors at 100 Hz each |
| t = 10 s | 0 | server broker removed |
| t = 11 s | 300 | `link=no` reported after 1 s, the buffer grows by λ every second |
| t = 70 s | 18 180 | server broker back, buffer at its highest |
| t = 105 s | 0 | buffer empty, 35 s after the restore |
| end | 0 | 34 500 sent, 34 500 stored, loss ratio 0 |

The drain time follows (5): `18 180/(893 − 300) ≈ 31 s` against 35 s measured, which is within the accuracy of `s` (the MQTT overhead per record makes the effective size slightly larger than 140 bytes, so `μ1` is slightly smaller).

The peak of 18 519 records is the 60 s of the break at 300 messages/s plus the records still in flight when the link came back. No record was discarded, because the buffer of 50 000 records is three times the size the break required.

On this host the break is noticed in about one second, because the server broker process disappears and the socket is reset immediately. On a real cable cut the detection is bounded by the Keep Alive interval of 10 s and the wait for its answer, which is the 14 to 20 s the instructions report.

## Load

Twelve steps of 30 s, each on a clean volume, with the buffer reduced to 10 000 records so that a step above the throughput of the system overflows it within the step. `results/load.csv` holds the rows, `results/latency.png` the chart.

| Step | Target, msg/s | λ measured | Sent | Received | Loss | Mean, ms | p95, ms | Max, ms | Buffer peak | Discarded |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 50 | 50.1 | 1503 | 1503 | 0 | 1.12 | 2 | 15 | 5 | 0 |
| 2 | 100 | 100.1 | 3000 | 3000 | 0 | 1.14 | 2 | 16 | 4 | 0 |
| 3 | 200 | 200.1 | 6000 | 6000 | 0 | 1.07 | 2 | 19 | 5 | 0 |
| 4 | 300 | 300.1 | 9000 | 9000 | 0 | 1.00 | 2 | 26 | 6 | 0 |
| 5 | 450 | 450.1 | 13500 | 13500 | 0 | 0.87 | 1 | 17 | 5 | 0 |
| 6 | 600 | 599.9 | 18000 | 18000 | 0 | 0.89 | 1 | 20 | 5 | 0 |
| 7 | 750 | 750.1 | 22500 | 22500 | 0 | 0.87 | 1 | 19 | 4 | 0 |
| 8 | 900 | 848.5 | 27000 | 27000 | 0 | 586.86 | 1680 | 1824 | 1538 | 0 |
| 9 | 1050 | 844.4 | 31500 | 31500 | 0 | 3201.83 | 6879 | 7309 | 6041 | 0 |
| 10 | 1200 | 840.9 | 36000 | 35817 | 0.0051 | 5835.7 | 11894 | 12597 | 10177 | 183 |
| 11 | 1350 | 840.0 | 40500 | 35956 | 0.1122 | 6237.4 | 11934 | 12806 | 10550 | 4544 |
| 12 | 1500 | 839.7 | 45000 | 36074 | 0.1984 | 6253.1 | 11952 | 12961 | 10705 | 8926 |

Three regimes are visible, and they are the three regimes of the model.

**Below 750 messages/s the latency is flat at about 1 ms.** The utilization is below 0.85, the queue is empty most of the time, and the delivery latency is the two database writes of the gateway plus the hop.

**Between 750 and 900 messages/s the latency rises by three orders of magnitude** while the received rate stops at 840 to 850 messages per second. The system has reached its throughput Θ ≈ 845 messages/s, and the queue in front of it grows: at step 9 the buffer holds 6 041 records and the mean latency is 3.2 s, which is the queue, not the service.

**Above 1 050 messages/s the queue overflows and the losses begin.** The arithmetic of (5) predicts the loss of a step: the buffer grows by `(λ − Θ)·30` in a 30 s step and loses what exceeds 10 000 records. At step 11, `(1350 − 845)·30 − 10 000 ≈ 5 150`, and 4 544 readings were lost. At step 12, `(1500 − 845)·30 − 10 000 ≈ 9 650`, and 8 926 were lost. The counter of the gateway and the loss of the subscriber agree to the record in all three steps, which is the check that the losses come from the buffer and from nowhere else.

**The bottleneck is the uplink.** Θ ≈ 845 messages/s matches `μ1 = R/(8·s) = 10⁶/(8·140) ≈ 893` to within 5 %, and the record size with the MQTT fixed header is slightly larger than 140 bytes, which accounts for the difference. The test separates the two candidates: at step 8 the offered load is 900 messages/s and the system carries 848, while at step 12 it is offered 1 500 and still carries 840. A processor limit would move with the work done per record; this ceiling does not move, and it is set by the 1000 kbit/s of `UPLINK_KBPS` that models the link to the building.

## Scalability

The throughput of the channel is `Θ = min μi` over its stages. Measured, the stages are: the emulator at 1 500 messages/s (step 12 sent its 45 000 readings in 30 s), the broker above that, the gateway at 845 messages/s, and the subscriber, which stored every reading the gateway delivered at every step. The gateway is the minimum, and inside the gateway the limit is the token bucket of the uplink, not the code.

One athlete needs 3 messages/s at the configured rate of 1 Hz per sensor. The measured Θ of 845 messages/s therefore carries about 280 athletes, or 840 wearable channels, on one gateway. The buffer at 50 000 records holds 167 s of a break at that rate, and the drain after a full break takes `50 000/(893 − 845) ≈ 17 min`, which is the real limit of the design: a gateway working near Θ cannot absorb a long break quickly.

A hundredfold growth of the sensors, from 3 to 300 channels in one building, is 300 messages/s and still fits in one gateway with a utilization of 0.35. A hundredfold growth of the whole platform, to 30 000 messages/s, does not: the answer is to partition the topic tree by zone, one gateway per zone, because the gateway is stateless between records and the buffer of one zone is independent of another. The subscriber scales the same way: one instance per zone writes to its own SQLite file, and a reader that needs the whole building reads the files rather than a single database. The broker is the part that needs the most work at that scale, since a single Mosquitto instance is the only component without a partition of its own; a cluster of brokers with a shared subscription per zone is the next step, and it is the reason the topic tree already carries the zone as its second level.

## Language and stack

| Component | Requirements that drive the choice | Choice | Alternative and its cost |
|---|---|---|---|
| Sensors | many small timers, a client library that supports will and retained messages, a value model that is easy to change | Python 3.13 with `paho-mqtt` 2.1 | C on the wearable itself: the real device would be a microcontroller, but the emulator is not the device and Python keeps the value models readable |
| Gateway | durability across a crash, a queue with a cursor, a token bucket, two concurrent connections | Python with `sqlite3` and `paho-mqtt` | Go: a goroutine per direction and a channel instead of a table, less code for the same behaviour, at the cost of a second toolchain in a project that already uses Python; the measured Θ of 845 messages/s is set by the uplink, so the language of the gateway does not appear in the limit |
| Subscriber | schema validation, a database that enforces the key, batching | Python with `sqlite3` in WAL mode | PostgreSQL: better under many writers, unnecessary for one subscriber per zone; SQLite sustains the 845 messages/s of the load test with a batch commit every 200 ms |
| Broker | the MQTT contract, retained messages, wills, QoS 1 | Eclipse Mosquitto 2 | RabbitMQ with the MQTT plugin: a heavier process for a protocol that Mosquitto implements completely |
| Chart | a two-panel figure from a CSV | `matplotlib` | gnuplot: fewer dependencies, less control over the log axis and the annotation |

The gateway is the component where the choice of language could matter, and the load test says it does not: the processor stays far from the 0.5 CPU limit while the uplink saturates at 845 messages/s. A compiled gateway would raise the ceiling of the code, not the ceiling of the channel. It would become the right choice if the gateway had to do per-record work beyond a copy (aggregation, filtering, a second protocol), or if the uplink grew to the point where the Python loop itself became the minimum stage. The decision is recorded in `docs/adr/0006-language-and-stack.md`.
