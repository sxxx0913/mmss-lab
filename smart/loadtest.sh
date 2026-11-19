#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Load test: raise the rate in steps of 30 s and record one row per step.
#
# Every step starts from clean volumes, so each step is one boot of the emulator
# and one line of stats.py. The buffer is reduced to 10 000 records: a step above
# the throughput of the system overflows it within 30 s, which is what produces
# the losses of the last steps.
#
#   ./smart/loadtest.sh
#   STEPS="100 300 600 900" ./smart/loadtest.sh
#   BUFFER_MAX=10000 UPLINK_KBPS=1000 ./smart/loadtest.sh
#
# With Docker the stack comes from smart/docker-compose.yml and the resource use
# of every container is sampled into results/docker_stats.csv; without Docker the
# same processes are started by smart/run_native.py and no sampling is possible.
# ---------------------------------------------------------------------------
set -uo pipefail

PROJ=$(cd "$(dirname "$0")/.." && pwd)
cd "$PROJ"

PY=${PY:-python}
STEP_SECONDS=${STEP_SECONDS:-30}
BUFFER_MAX=${BUFFER_MAX:-10000}
UPLINK_KBPS=${UPLINK_KBPS:-1000}
STEPS=${STEPS:-"50 100 200 300 450 600 750 900 1050 1200 1350 1500"}
CSV="$PROJ/results/load.csv"
DSTATS="$PROJ/results/docker_stats.csv"
export BUFFER_MAX UPLINK_KBPS

COMPOSE="smart/docker-compose.yml"
USE_DOCKER=0
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    USE_DOCKER=1
fi

# The data directory is a bind mount, so `docker compose down -v` does not clear
# it: the databases and the boot counter have to be removed by hand, otherwise a
# step would continue the boot of the previous one.
clear_data() {
    rm -f data/*.db data/*.db-wal data/*.db-shm data/boot.txt data/gateway_stats.json 2>/dev/null
}

stack_up() {
    if [ "$USE_DOCKER" = "1" ]; then
        docker compose -f "$COMPOSE" down -v >/dev/null 2>&1
        clear_data
        docker compose -f "$COMPOSE" up -d --build broker-edge broker-cloud gateway collector
        sleep 5
    else
        "$PY" smart/run_native.py clean >/dev/null 2>&1
        rm -f data/logs/*.log
        "$PY" smart/run_native.py up
    fi
}

stack_down() {
    if [ "$USE_DOCKER" = "1" ]; then
        docker compose -f "$COMPOSE" down -v >/dev/null 2>&1
    else
        "$PY" smart/run_native.py stop >/dev/null 2>&1
    fi
}

sample_stats() {
    local step="$1" tmp="$2"
    : > "$tmp"
    local i
    for i in $(seq 1 7); do
        docker stats --no-stream --format '{{.Name}},{{.CPUPerc}},{{.MemUsage}}' >> "$tmp" 2>/dev/null
        sleep 4
    done
    "$PY" - "$tmp" "$step" <<'PYEOF' >> "$DSTATS"
import sys
src, step = sys.argv[1], sys.argv[2]
best = {}
for line in open(src, encoding="utf-8"):
    parts = line.strip().split(",")
    if len(parts) < 3:
        continue
    name, cpu, mem = parts[0], parts[1].rstrip("%"), parts[2]
    try:
        cpu = float(cpu)
    except ValueError:
        continue
    used = mem.split("/")[0].strip()
    if name not in best or cpu > best[name][0]:
        best[name] = (cpu, used)
for name, (cpu, mem) in sorted(best.items()):
    print(f"{step},{name},{cpu:.1f},{mem}")
PYEOF
}

run_step() {
    local hz="$1" step="$2"
    if [ "$USE_DOCKER" = "1" ]; then
        HZ="$hz" DURATION="$STEP_SECONDS" \
            docker compose -f "$COMPOSE" up -d --no-deps --force-recreate sensors >/dev/null
        sample_stats "$step" "$PROJ/results/_stats.tmp"
    else
        MQTT_PORT=1883 HZ="$hz" DURATION="$STEP_SECONDS" "$PY" smart/sensors.py > data/logs/sensors-step.log 2>&1
    fi
}

wait_drain() {
    local buf
    for _ in $(seq 1 45); do
        buf=$("$PY" -c "import json;print(json.load(open('data/gateway_stats.json'))['buffer'])" 2>/dev/null || echo 1)
        [ "$buf" = "0" ] && return 0
        sleep 1
    done
}

rm -f "$CSV" "$DSTATS"
echo "step,target_msg_s,lambda_msg_s,sent,received,loss_ratio,lat_mean_ms,lat_p95_ms,lat_max_ms,buffer_peak,dropped" > "$CSV"
echo "step,service,cpu_max_pct,mem_peak" > "$DSTATS"

n=0
for target in $STEPS; do
    n=$((n + 1))
    hz=$(awk -v t="$target" 'BEGIN{printf "%.3f", t/3}')
    echo "step $n: target $target messages/s (HZ=$hz per sensor, $STEP_SECONDS s)"
    stack_up
    run_step "$hz" "$n"
    wait_drain
    "$PY" smart/stats.py --csv "$CSV" --step "$n" --target "$target"
    stack_down
done

rm -f "$PROJ/results/_stats.tmp"
echo "wrote $CSV and $DSTATS"
"$PY" smart/plot.py --csv "$CSV" --out results/latency.png
