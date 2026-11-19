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
# With Docker the stack comes from smart/docker-compose.yml; without Docker the
# same processes are started by smart/run_native.py.
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
export BUFFER_MAX UPLINK_KBPS

COMPOSE="smart/docker-compose.yml"
USE_DOCKER=0
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    USE_DOCKER=1
fi

stack_up() {
    if [ "$USE_DOCKER" = "1" ]; then
        docker compose -f "$COMPOSE" down -v >/dev/null 2>&1
        docker compose -f "$COMPOSE" up -d --build broker-edge broker-cloud gateway collector
        sleep 4
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

run_step() {
    local hz="$1"
    if [ "$USE_DOCKER" = "1" ]; then
        HZ="$hz" DURATION="$STEP_SECONDS" \
            docker compose -f "$COMPOSE" up -d --no-deps --force-recreate sensors >/dev/null
        sleep "$STEP_SECONDS"
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

rm -f "$CSV"
echo "step,target_msg_s,lambda_msg_s,sent,received,loss_ratio,lat_mean_ms,lat_p95_ms,lat_max_ms,buffer_peak,dropped" > "$CSV"

n=0
for target in $STEPS; do
    n=$((n + 1))
    hz=$(awk -v t="$target" 'BEGIN{printf "%.3f", t/3}')
    echo "step $n: target $target messages/s (HZ=$hz per sensor, $STEP_SECONDS s)"
    stack_up
    run_step "$hz"
    wait_drain
    "$PY" smart/stats.py --csv "$CSV" --step "$n" --target "$target"
    stack_down
done

echo "wrote $CSV"
"$PY" smart/plot.py --csv "$CSV" --out results/latency.png
