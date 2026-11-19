# Deployment of the telemetry channel

The channel of `docs/telemetry.md` runs as two components on two networks: the edge node in the room with the sensors, the edge broker and the gateway, and the server in the building with the server broker and the subscriber.

## Components

| Service | Image or command | Networks | Resources | Data |
|---|---|---|---|---|
| `broker-edge` | `eclipse-mosquitto:2.1.4-alpine`, `broker/mosquitto.conf` | edge | — | `edge-broker-data` volume |
| `sensors` | the image of this lab, `python smart/sensors.py` | edge | 0.5 CPU, 128 MB | `../data` |
| `gateway` | the same image, `python smart/gateway.py` | edge and cloud | 1 CPU, 192 MB | `../data` |
| `broker-cloud` | `eclipse-mosquitto:2.1.4-alpine`, the same configuration | cloud | — | `cloud-broker-data` volume |
| `collector` | the same image, `python smart/collector.py` | cloud | 1 CPU, 256 MB | `../data` |

One image serves the emulator, the gateway and the subscriber: they share `paho-mqtt` and nothing else, and a single image keeps the build to one step.

The scripts keep the layout of the repository inside the image, `smart/<script>.py`, and the data directory is resolved as `parents[1]/data`. Copying the scripts straight into `/app` would make that expression resolve to `/data`, which is the writable layer of the container and not the mounted volume: the first container run wrote its database there, the host could not read it and `docker compose down` deleted it. The image therefore copies `smart/`, and the volume is `/app/data`.

The gateway gets one core and not half. At 0.5 CPU it takes in about 645 messages per second, below the 822 that the uplink can carry, so the node cannot saturate its own link and the queue forms in the broker instead of the outbox. With one core it keeps up with the link, and with the link throttled it takes in 1 221 messages per second.

Only the gateway is attached to both networks. The sensors cannot reach the server broker, and the subscriber cannot reach the edge broker, so the only path from a reading to the database is the one the architecture describes. Only the edge broker publishes a port, on `127.0.0.1:1883`, because the broker runs with anonymous access and must not be reachable from the network of the host.

The two brokers share one configuration file. It declares the listener, allows anonymous clients (Mosquitto 2 rejects clients without a user name as soon as a listener is declared), keeps persistence on a volume, and sets `set_tcp_nodelay`, without which the PUBACK of QoS 1 waits for the next message and the gateway saturates long before its processor does. The keyword is `set_tcp_nodelay` in 2.1.4: earlier builds of 2.1 accept `tcp_nodelay` and reject this one, so the image tag and the line move together.

```bash
docker compose -f smart/docker-compose.yml up -d --build
docker compose -f smart/docker-compose.yml ps
```

`smart/Dockerfile` builds the image from `python:3.13-slim`, installs `requirements.txt` and copies the four scripts; `data/` is a volume so the databases survive a restart of a container and stay out of the repository. The measurements below come from this deployment: five services `Up`, the limits above enforced by the container runtime, and `docker stats` sampled at every step of the load test into `results/docker_stats.csv`.

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

**Consequences.** The break experiment below shows the pattern working with no loss. The upload rate after the break is limited by the link, `μ1 = R/(8·s) = 10⁶/(8·152) ≈ 822` messages/s at 1000 kbit/s, so the accumulated queue drains at `μ1 − λ` and the drain takes `Q0/(μ1 − λ)` by (5). The pattern doubles the writes on the edge disk and makes the gateway the component that carries the queue, which is what the load test and the slow-link run below show.

## Link break

The experiment raises the flow to 300 messages/s (HZ = 100 per sensor) and cuts the link 10 s after the start, then brings it back 60 s later. The cut is the one the instructions describe: `docker network disconnect mmss-lab3_cloud mmss-lab3-gateway-1` removes the interface of the gateway container, so its socket to the server broker dies while both brokers keep running.

| Moment | Buffer, records | Observation |
|---|---|---|
| t = 0 | 0 | three sensors at 100 Hz each |
| t = 10 s | 0 | the gateway is disconnected from the cloud network |
| t = 25 s | 4 500 | `link=no` reported 15 s after the cut, the buffer grows by 300 every second |
| t = 70 s | 17 865 | the interface is attached again, buffer at its highest |
| t = 111 s | 0 | buffer empty, 41 s after the restore |
| end | 0 | 33 840 sent, 33 840 stored, loss ratio 0, no duplicate |

The buffer holds 59.5 s of readings, which is the break itself: nothing was discarded and nothing was lost, so the pattern did what it promises.

The 15 s before `link=no` is the price of a silent cut. The interface disappears without resetting the socket, so the client learns of the break from the keep alive of 10 s and the retry after it, not from an error of the operating system. A cut cable looks the same to a client, and the 15 to 20 s the instructions give for it is this interval.

The drain follows (5) with one correction: `17 865/(822 − 300) ≈ 34 s`, but the measured drain is 41 s. The gateway is taking in 300 messages per second while it drains, and ingest and forward share one core, so the forward rate during the drain is 736 messages per second and not 822. The formula treats the two directions as independent, and on a node with one core they are not.

## Load

Twelve steps of 30 s, each on a clean volume, with the buffer reduced to 10 000 records. `results/load.csv` holds the rows, `results/docker_stats.csv` the resource use sampled during every step, `results/latency.png` the chart.

| Step | Target, msg/s | λ measured | Sent | Received | Loss | Mean, ms | p95, ms | Max, ms | Buffer peak | Gateway CPU |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 50 | 50.1 | 1503 | 1503 | 0 | 3.9 | 6 | 15 | 3 | 4.7 % |
| 2 | 100 | 100.1 | 3000 | 3000 | 0 | 5.1 | 8 | 22 | 6 | 10.6 % |
| 3 | 200 | 200.1 | 6003 | 6003 | 0 | 3.5 | 8 | 37 | 8 | 16.7 % |
| 4 | 300 | 299.9 | 9000 | 9000 | 0 | 2.7 | 5 | 44 | 7 | 23.8 % |
| 5 | 450 | 450.1 | 13500 | 13500 | 0 | 3.1 | 6 | 53 | 9 | 34.8 % |
| 6 | 600 | 600.1 | 18000 | 18000 | 0 | 5.6 | 14 | 64 | 24 | 42.9 % |
| 7 | 750 | 749.2 | 22500 | 22500 | 0 | 26.5 | 74 | 129 | 31 | 48.9 % |
| 8 | 900 | 786.5 | 27000 | 27000 | 0 | 2299 | 4219 | 4354 | 55 | 50.0 % |
| 9 | 1050 | 797.1 | 31500 | 31500 | 0 | 5018 | 9224 | 9532 | 37 | 49.0 % |
| 10 | 1200 | 798.2 | 27076 | 27076 | 0 | 5791 | 10807 | 11378 | 37 | 49.7 % |
| 11 | 1350 | 782.2 | 31169 | 26488 | 0.150 | 6524 | 11333 | 11958 | 54 | 49.9 % |
| 12 | 1500 | 770.9 | 45000 | 26602 | 0.409 | 4640 | 10381 | 11209 | 32 | 48.5 % |

Three regimes are visible, and they are the three regimes of the model.

**Below 750 messages/s the latency is flat at 3 to 6 ms.** The utilization is below 0.95, the queue is empty, and the delivery latency is the two database writes of the gateway plus the hop. The 26 ms of step 7 is the first sign that the queue is no longer empty.

**Between 750 and 900 messages/s the latency rises by three orders of magnitude** while the received rate stops at 780 to 800 messages per second. The chain has reached its throughput Θ ≈ 790 messages/s and the queue in front of it grows: at step 9 the mean latency is 5.0 s, which is 4 000 readings waiting, not a slower service.

**Above 1 200 messages/s the losses begin.** At step 11, 4 681 of 31 169 readings did not arrive, and at step 12, 18 398 of 45 000. The queue that overflows is the per-client queue of the edge broker, 1 000 messages, not the outbox: the buffer peak stays below 60 records and the gateway's own counter of discarded records is 0 at every step. The readings that are dropped there never reach the gateway, so the subscriber sees them as gaps in the sequence and the gateway cannot count them.

**The bottleneck is the uplink, and `docker stats` says so.** Θ ≈ 790 messages/s against `μ1 = R/(8·s) = 10⁶/(8·152) ≈ 822`, a difference of 4 %. The gateway's processor rises with the load and then stops at 50 % of its one-core quota, with 192 MB of memory untouched: at the ceiling it is waiting on the link, not working. A processor limit would show as a container pinned at 100 % of its quota and a ceiling that moves when the work per record changes; neither happens.

**The outbox is not exercised by this test, and the reason matters.** Ingest and forward are the same 790 messages per second here, so nothing accumulates between them. The outbox becomes the queue as soon as the link is slower than the node can take records in, which is the case the pattern is for. Throttling the uplink to 200 kbit/s at an offered load of 1 200 messages/s shows it: the gateway took in 1 221 messages per second, forwarded 170, and the buffer grew by 1 049 records every second, 14 374 of them in 15 s, with nothing lost. That is the same behaviour the break produces, without the break.

## Scalability

The throughput of the channel is `Θ = min μi` over its stages. Measured, the stages are: the emulator at 1 500 messages/s (it published 45 000 readings in 30 s at step 12), the broker above that, the gateway at 790, and the subscriber, which stored every reading the gateway delivered at every step. The gateway is the minimum, and inside the gateway the limit is the token bucket of the uplink: the processor of the node stays at half of its quota while the link is saturated.

One athlete needs 3 messages/s at the configured rate of 1 Hz per sensor. Θ of 790 messages/s therefore carries about 260 athletes, or 790 wearable channels, on one gateway. The buffer at 50 000 records holds 167 s of a break at that rate, and draining a full buffer takes `50 000/(822 − 300) ≈ 96 s`.

A hundredfold growth of the sensors in one building, from 3 to 300 channels, is 300 messages/s and fits in one gateway at a utilization of 0.38. A hundredfold growth of the whole platform, to 30 000 messages/s, does not, and the measurement says what to do first. The node's processor is at half its quota at the ceiling, so a faster processor buys nothing; the link is what binds, and the first move is either more bandwidth or a second gateway, because the gateway is stateless between records and the topic tree already carries the zone as its second level. One gateway per zone divides both the link and the queue, and the subscriber scales the same way: one instance per zone writing to its own SQLite file. The broker is the part that needs the most work at that scale, since a single Mosquitto instance is the only component without a partition of its own; a cluster of brokers with a shared subscription per zone is the next step.

## Language and stack

| Component | Requirements that drive the choice | Choice | Alternative and its cost |
|---|---|---|---|
| Sensors | many small timers, a client library that supports will and retained messages, a value model that is easy to change | Python 3.13 with `paho-mqtt` 2.1 | C on the wearable itself: the real device would be a microcontroller, but the emulator is not the device and Python keeps the value models readable |
| Gateway | durability across a crash, a queue with a cursor, a token bucket, two concurrent connections | Python with `sqlite3` and `paho-mqtt` | Go: a goroutine per direction and a channel instead of a table, less code for the same behaviour, at the cost of a second toolchain in a project that already uses Python; the measured Θ of 790 messages/s is set by the uplink, so the language of the gateway does not appear in the limit |
| Subscriber | schema validation, a database that enforces the key, batching | Python with `sqlite3` in WAL mode | PostgreSQL: better under many writers, unnecessary for one subscriber per zone; SQLite sustained every reading the load test delivered, at 11 % of one core |
| Broker | the MQTT contract, retained messages, wills, QoS 1 | Eclipse Mosquitto 2.1.4 | RabbitMQ with the MQTT plugin: a heavier process for a protocol that Mosquitto implements completely |
| Chart | a two-panel figure from a CSV | `matplotlib` | gnuplot: fewer dependencies, less control over the log axis and the annotation |

The gateway is the component where the choice of language could matter, and the load test says it does not: the processor stays at half of its quota while the uplink saturates at 790 messages/s. A compiled gateway would raise the ceiling of the code, not the ceiling of the channel. It would become the right choice if the gateway had to do per-record work beyond a copy (aggregation, filtering, a second protocol), or if the link grew to the point where the Python loop itself became the minimum stage. The decision is recorded in `docs/adr/0006-language-and-stack.md`.
