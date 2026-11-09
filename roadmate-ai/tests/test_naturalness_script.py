"""Execute the comparison orchestration with local stubs; no servers, models or networking."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def comparison(tmp_path):
    bash = shutil.which("bash")
    if not bash:
        candidate = Path("C:/Program Files/Git/bin/bash.exe")
        if candidate.is_file():
            bash = str(candidate)
    if not bash:
        pytest.skip("Bash is required for the Linux orchestration smoke")
    work = tmp_path / "isolated comparison"
    for folder in ("scripts", ".venv/bin", "run", "logs"):
        (work / folder).mkdir(parents=True, exist_ok=True)
    (work / "logs/ai.err.log").write_text("previous completed comparison\n", encoding="utf-8")
    source = (ROOT / "scripts/naturalness_ab.sh").read_text(encoding="utf-8")
    probe = '(exec 3<>"/dev/tcp/127.0.0.1/$port")'
    assert source.count(probe) == 1
    # The test deliberately bypasses only the network probe; all orchestration is unchanged.
    source = source.replace(probe, "false")
    assert "/dev/tcp" not in source
    (work / "scripts/naturalness_ab.sh").write_text(source, encoding="utf-8")
    (work / "scripts/run.sh").write_text(r'''#!/usr/bin/env bash
set -euo pipefail
printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$1" "$MODEL_PROFILE" "$TWO_STEP_KO" "$NATURAL_STYLE_KO" "$FIELD_GLOSSARY" "$POLISH_KO" >> events.tsv
if [ "$1" = start ]; then
    printf 'server log %s/%s\n' "$MODEL_PROFILE" "$TWO_STEP_KO" > logs/ai.err.log
    # Harmless sleep processes exercise real PID ownership without starting a server.
    bash -c 'exec -a uvicorn sleep 60' </dev/null >/dev/null 2>&1 &
    pid=$!
    echo "$pid" >> stub-pids.txt
    echo "$pid" > run/ai.pid
    if [ "${STUB_FAIL:-}" = race ] && [ "$TWO_STEP_KO" = 1 ]; then
        printf 'unowned\t%s\n' "$pid" >> events.tsv
        echo 'simulated concurrent server won port'
        exit 44
    fi
    echo "ai started (pid $pid), logs: logs/ai.*.log"
    printf 'owned\t%s\n' "$pid" >> events.tsv
    if [ "${STUB_FAIL:-}" = start ] && [ "$TWO_STEP_KO" = 1 ]; then
        echo 'simulated start failure'
        exit 42
    fi
fi
''', encoding="utf-8")
    fake_python = work / ".venv/bin/python"
    fake_python.write_text(r'''#!/usr/bin/env bash
set -euo pipefail
case "$1" in
    -c) printf 'probe\t%s\n' "$MODEL_PROFILE" >> events.tsv; echo 1 ;;
    scripts/download_model.py)
        printf 'download\t%s\n' "$MODEL_PROFILE" >> events.tsv ;;
    eval/evaluate.py)
        shift
        dataset=''
        while [ "$#" -gt 0 ]; do
            if [ "$1" = --file ]; then dataset="$2"; fi
            shift
        done
        printf 'evaluate\t%s\t%s\t%s\t%s\t%s\t%s\n' "$MODEL_PROFILE" "$TWO_STEP_KO" "$NATURAL_STYLE_KO" "$FIELD_GLOSSARY" "$POLISH_KO" "$dataset" >> events.tsv
        if [ "${STUB_FAIL:-}" = evaluate ] && [ "$dataset" = eval/general_es.jsonl ]; then
            echo 'simulated evaluation failure'
            exit 43
        fi
        folder="eval/results/$MODEL_PROFILE-$TWO_STEP_KO-$NATURAL_STYLE_KO-$FIELD_GLOSSARY-${dataset#eval/}"
        mkdir -p "$folder"
        echo '{}' > "$folder/run.json"
        echo '[2/4] FAIL sample 23ms http_502'
        echo '- model latency p50/p95 ms: 10 / 20'
        echo '- HTTP wall p50/p95 ms: 12 / 23'
        echo "- errors: {'http_502': 1}"
        echo "- 502 reasons: {'number_missing': 1}"
        echo '- first-attempt wrong_language: 0/4 traced first attempts'
        echo '- retry validator recovery: 1/2 (50.0%)'
        echo '- Korean translationese signals (advisory): 1/3 (33.3%)'
        echo '- two-step literal == translated_text: 1/3 final accepted outputs'
        echo '- two-step attempts literal == translated_text: 2/4 traced attempts'
        echo '| other | 2 | 1 |'
        printf 'saved: %s\n' "$folder"
        ;;
    eval/export_review.py)
        shift
        [ -f "$1/run.json" ] && [ -f "$2/run.json" ]
        printf 'review\t%s\t%s\t%s\n' "$1" "$2" "$4" >> events.tsv
        ;;
    *) echo "unexpected Python command: $*" >&2; exit 90 ;;
esac
''', encoding="utf-8")
    fake_python.chmod(0o755)

    def run(failure="", active=False, *, args=(), env_changes=None):
        env = {**os.environ, "STUB_FAIL": failure, "MODEL_PROFILE": "8b-q4", "TWO_STEP_KO": "1",
               "NATURAL_STYLE_KO": "1", "FIELD_GLOSSARY": "1", "POLISH_KO": "1"}
        env.pop("ARMS", None)
        env.update(env_changes or {})
        command = [bash, "scripts/naturalness_ab.sh", "output with spaces", *args]
        if active:
            command = [bash, "-c", 'echo $$ > run/ai.pid; bash scripts/naturalness_ab.sh "$@"',
                       "comparison", "output with spaces", *args]
        result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=30)
        log = work / "events.tsv"
        events = [line.split("\t") for line in log.read_text().splitlines()] if log.exists() else []
        alive = []
        if (work / "stub-pids.txt").exists():
            probe = subprocess.run([bash, "-c", 'while read -r pid; do kill -0 "$pid" 2>/dev/null && echo "$pid"; done < stub-pids.txt'],
                                   cwd=work, capture_output=True, text=True, timeout=10)
            alive = probe.stdout.splitlines()
        return result, events, work / "output with spaces", alive

    yield run
    # A race test deliberately leaves its unowned stub alive; only test-created PIDs are cleaned here.
    if (work / "stub-pids.txt").exists():
        subprocess.run([bash, "-c", 'while read -r pid; do kill "$pid" 2>/dev/null || true; done < stub-pids.txt'],
                       cwd=work, capture_output=True, timeout=10)


def test_requested_arms_and_blinded_exports_use_one_profile_and_isolated_flags(comparison):
    result, events, output, alive = comparison(args=("--model-profile", "4b-q8", "--arms", "two-step,style,glossary"),
                                              env_changes={"ARMS": "style"})
    assert result.returncode == 0, result.stdout + result.stderr
    starts = [e for e in events if e[0] == "start"]
    assert starts == [["start", "4b-q8", "0", "0", "0", "0"],
                      ["start", "4b-q8", "1", "0", "0", "0"],
                      ["start", "4b-q8", "0", "1", "0", "0"],
                      ["start", "4b-q8", "0", "0", "1", "0"]]
    assert [e for e in events if e[0] == "probe"] == [["probe", "4b-q8"]]
    assert [e for e in events if e[0] == "download"] == [["download", "4b-q8"]]
    evaluations = [e for e in events if e[0] == "evaluate"]
    assert len(evaluations) == 12
    for index, arm in enumerate(starts):
        assert [e[1:6] for e in evaluations[index * 3:index * 3 + 3]] == [arm[1:]] * 3
        assert [e[-1] for e in evaluations[index * 3:index * 3 + 3]] == [
            "eval/general.jsonl", "eval/general_es.jsonl", "eval/naturalness.dev.jsonl"]
    assert not any(e[0] == "stop" for e in events)  # never call the global stop command
    reviews = [e for e in events if e[0] == "review"]
    assert len(reviews) == 9
    for dataset in ("general", "general_es", "naturalness.dev"):
        for index, flags in enumerate(("1-0-0", "0-1-0", "0-0-1"), 1):
            assert ["review", f"eval/results/4b-q8-0-0-0-{dataset}.jsonl", f"eval/results/4b-q8-{flags}-{dataset}.jsonl",
                    f"pair{index:02d}-{dataset}"] in reviews
    for marker in ("FAIL sample", "http_502", "502 reasons", "model latency p50/p95", "HTTP wall p50/p95",
                   "first-attempt wrong_language", "retry validator recovery", "Korean translationese", "| other |",
                   "two-step literal == translated_text", "two-step attempts literal == translated_text"):
        assert marker in result.stdout
    assert all((output / f"{arm}-ai.err.log").is_file() for arm in ("baseline", "two-step", "style", "glossary"))
    assert (output / "logs-before/ai.err.log").read_text() == "previous completed comparison\n"
    assert not (output.parent / "run/ai.pid").exists()
    assert alive == []


@pytest.mark.parametrize("profile,arm,flags", [
    ("4b-q4", "style", ["0", "1", "0"]),
    ("4b-q8", "two-step", ["1", "0", "0"]),
    ("8b-q4", "glossary", ["0", "0", "1"]),
])
def test_single_requested_option_runs_baseline_once_and_only_selected_profile(comparison, profile, arm, flags):
    result, events, _, alive = comparison(args=("--model-profile", profile, "--arms", arm))
    assert result.returncode == 0, result.stdout + result.stderr
    assert [e for e in events if e[0] == "start"] == [
        ["start", profile, "0", "0", "0", "0"], ["start", profile, *flags, "0"]]
    assert [e for e in events if e[0] == "download"] == [["download", profile]]
    assert len([e for e in events if e[0] == "evaluate"]) == 6
    assert len([e for e in events if e[0] == "review"]) == 3
    assert alive == []


def test_environment_selection_keeps_requested_arm_order(comparison):
    result, events, _, alive = comparison(env_changes={"MODEL_PROFILE": "4b-q4", "ARMS": "glossary style"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert [e for e in events if e[0] == "start"] == [
        ["start", "4b-q4", "0", "0", "0", "0"],
        ["start", "4b-q4", "0", "0", "1", "0"],
        ["start", "4b-q4", "0", "1", "0", "0"]]
    assert alive == []


@pytest.mark.parametrize("args,env_changes", [
    (("--model-profile", "unknown"), {}),
    (("--arms", "larger"), {}),
    (("--arms", "two-step,style,unknown"), {}),
    (("--arms", "two-step,two-step"), {}),
    (("--arms", ""), {}),
    (("--arms",), {}),
    (("--unknown",), {}),
    ((), {"ARMS": "bad"}),
    ((), {"MODEL_PROFILE": "bad"}),
])
def test_invalid_selection_performs_no_actions(comparison, args, env_changes):
    result, events, output, alive = comparison(args=args, env_changes=env_changes)
    assert result.returncode == 2
    assert events == []
    assert not output.exists()
    assert alive == []


@pytest.mark.parametrize("failure,starts", [("start", 2), ("evaluate", 1)])
def test_failed_start_or_evaluation_stops_owned_run_and_preserves_logs(comparison, failure, starts):
    result, events, output, alive = comparison(failure)
    assert result.returncode != 0
    assert f"simulated {'evaluation' if failure == 'evaluate' else failure} failure" in result.stderr
    assert len([e for e in events if e[0] == "start"]) == starts
    assert not any(e[0] == "stop" for e in events)
    assert not any(e[0] == "review" for e in events)
    assert (output / "baseline-ai.err.log").is_file()
    assert not (output.parent / "run/ai.pid").exists()
    assert alive == []


def test_existing_process_is_left_alone(comparison):
    result, events, output, _ = comparison(active=True)
    assert result.returncode != 0
    assert "existing ai run is active" in result.stderr
    assert events == []
    assert not output.exists()


def test_concurrent_server_is_not_stopped_after_failed_start(comparison):
    result, events, output, alive = comparison("race")
    assert result.returncode != 0
    assert "simulated concurrent server won port" in result.stderr
    assert not any(e[0] == "stop" for e in events)
    unowned = [e[1] for e in events if e[0] == "unowned"]
    assert len(unowned) == 1
    assert (output.parent / "run/ai.pid").read_text().strip() == unowned[0]
    assert alive == unowned
