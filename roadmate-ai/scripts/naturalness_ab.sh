#!/usr/bin/env bash
# Run only after the current DLPC comparison has finished. Never stop an existing run.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${1:-$ROOT/eval/results/naturalness-ab-$STAMP}"
[ -x "$PY" ] || { echo 'Run scripts/run.sh setup first.' >&2; exit 1; }
[ ! -e "$OUT" ] || { echo "Output already exists: $OUT" >&2; exit 1; }
for name in ai llama; do
    if [ -f "run/$name.pid" ] && kill -0 "$(cat "run/$name.pid")" 2>/dev/null; then
        echo "An existing $name run is active; finish it before this comparison." >&2
        exit 1
    fi
done
# Check the configured ports before claiming ownership of any processes.
for port in "${AI_PORT:-8000}" "$("$PY" -c 'from config import load_manifest; print(load_manifest()["runtime"]["port"])')"; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
        echo "Port $port is occupied; existing runs are left running." >&2
        exit 1
    fi
done
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
if [ -d logs ]; then
    mkdir -p "$OUT/logs-before"
    cp -a logs/. "$OUT/logs-before/"
fi
export POLISH_KO=0 LOG_REJECTED=1 NATURAL_STYLE_KO=0 FIELD_GLOSSARY=0
unset MODEL_ID
owned=0
arm=""
stop_owned() {
    [ "$owned" = 1 ] || return 0
    owned=0
    local pids=() name pid tag cmd until refused=0
    for name in ai llama; do
        for stream in out err; do
            [ ! -f "logs/$name.$stream.log" ] || cp "logs/$name.$stream.log" "$OUT/$arm-$name.$stream.log"
        done
    done
    # A concurrent start can win the port race. Only our start log establishes PID ownership.
    while read -r name pid; do
        if ! kill -0 "$pid" 2>/dev/null; then
            if [ -f "run/$name.pid" ] && [ "$(cat "run/$name.pid")" = "$pid" ]; then rm -f "run/$name.pid"; fi
            continue
        fi
        tag=uvicorn
        [ "$name" != llama ] || tag=llama-server
        cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
        if [ -f "run/$name.pid" ] && [ "$(cat "run/$name.pid")" = "$pid" ] && [[ "$cmd" == *"$tag"* ]]; then
            kill "$pid" 2>/dev/null || true
            pids+=("$pid")
            rm -f "run/$name.pid"
        else
            echo "Cannot confirm ownership of $name PID $pid; leaving it running and stopping comparison." >&2
            refused=1
        fi
    done < <(sed -nE 's/^(ai|llama) started \(pid ([0-9]+)\),.*/\1 \2/p' "$OUT/$arm-start.log")
    until=$((SECONDS + 30))
    for pid in "${pids[@]}"; do
        while kill -0 "$pid" 2>/dev/null; do
            [ "$SECONDS" -lt "$until" ] || { echo "Process $pid has not exited; stopping comparison." >&2; return 1; }
            sleep 1
        done
    done
    [ "$refused" = 0 ]
}
trap stop_owned EXIT
declare -A RUNS
for arm in baseline two-step larger; do
    export MODEL_PROFILE=4b-q4 TWO_STEP_KO=0
    [ "$arm" != two-step ] || export TWO_STEP_KO=1
    [ "$arm" != larger ] || export MODEL_PROFILE=8b-q4
    "$PY" scripts/download_model.py
    owned=1
    bash scripts/run.sh start > "$OUT/$arm-start.log" 2>&1 || {
        cat "$OUT/$arm-start.log" >&2; exit 1;
    }
    for dataset in general general_es naturalness.dev; do
        log="$OUT/$arm-$dataset.log"
        "$PY" eval/evaluate.py --file "eval/$dataset.jsonl" --label "natural-$STAMP-$arm" \
            --url "http://127.0.0.1:${AI_PORT:-8000}" --attempt-log logs/ai.err.log > "$log" 2>&1 || {
            cat "$log" >&2; exit 1;
        }
        run="$(sed -n 's/^saved: //p' "$log")"
        [ -f "$run/run.json" ] || { echo "Missing evaluation output: $log" >&2; exit 1; }
        RUNS["$arm:$dataset"]="$run"
        echo "=== $arm / $dataset: $run ==="
        # Keep full raw output separately; show failures, latency, retry and advisory tables here.
        grep -E '^\[.*FAIL|^- (model_profile|TWO_STEP_KO|NATURAL_STYLE_KO|FIELD_GLOSSARY|model latency|HTTP wall|errors:|502 reasons:|first-attempt|retry|Korean translationese)|^\|' "$log" || true
    done
    stop_owned
done
for dataset in general general_es naturalness.dev; do
    for pair in two-step larger; do
        label=pair01
        [ "$pair" != larger ] || label=pair02
        "$PY" eval/export_review.py "${RUNS[baseline:$dataset]}" "${RUNS[$pair:$dataset]}" \
            --label "$label-$dataset" --out-dir "$OUT/review"
    done
done
echo "AI review files and separate keys: $OUT/review"
echo 'Keep .key.json files away from reviewers. Follow docs/naturalness_rubric.md for independent review.'
