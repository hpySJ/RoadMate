"""Local orchestration checks use temporary artifacts, never model or training processes."""
import hashlib
import json
from pathlib import Path
import socket
import sys
import tarfile

import pytest

from scripts import lora_pipeline as L


def test_live_pid_refuses_without_stopping_it(tmp_path, monkeypatch):
    (tmp_path / "run").mkdir()
    pidfile = tmp_path / "run" / "ai.pid"
    pidfile.write_text("12345")
    monkeypatch.setattr(L, "alive", lambda pid: True)
    with pytest.raises(ValueError, match="left running"):
        L.ensure_idle(tmp_path, [])
    assert pidfile.read_text() == "12345"


def test_listening_port_is_left_running(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        with pytest.raises(ValueError, match="occupied"):
            L.ensure_idle(tmp_path, [port])
        assert listener.fileno() >= 0


def owned_setup(tmp_path):
    (tmp_path / "run").mkdir()
    pidfile = tmp_path / "run" / "ai.pid"
    pidfile.write_text("12345")
    log = tmp_path / "start.log"
    log.write_text("ai started (pid 12345), logs: logs/ai.*.log\n")
    return pidfile, log


@pytest.mark.parametrize("mismatch", ["pidfile", "command"])
def test_cleanup_refuses_unconfirmed_process(tmp_path, monkeypatch, mismatch):
    pidfile, log = owned_setup(tmp_path)
    if mismatch == "pidfile":
        pidfile.write_text("12346")
    monkeypatch.setattr(L, "alive", lambda pid: True)
    monkeypatch.setattr(Path, "read_bytes", lambda p: b"another-program" if mismatch == "command" else b"python -m uvicorn")
    monkeypatch.setattr(L.os, "kill", lambda *a: pytest.fail("must not kill an unowned process"))
    with pytest.raises(ValueError, match="Cannot confirm"):
        L.stop_owned(tmp_path, log, tmp_path / "saved-logs")
    assert pidfile.exists()


def test_cleanup_stops_only_recorded_pid_and_waits(tmp_path, monkeypatch):
    pidfile, log = owned_setup(tmp_path)
    running, killed = {12345}, []
    monkeypatch.setattr(L, "alive", lambda pid: pid in running)
    monkeypatch.setattr(Path, "read_bytes", lambda p: b"python\0-m\0uvicorn\0app:app")
    def kill(pid, signal):
        killed.append(pid)
        running.remove(pid)
    monkeypatch.setattr(L.os, "kill", kill)
    L.stop_owned(tmp_path, log, tmp_path / "saved-logs")
    assert killed == [12345]
    assert not pidfile.exists()


def test_cleanup_timeout_preserves_pidfile(tmp_path, monkeypatch):
    pidfile, log = owned_setup(tmp_path)
    monkeypatch.setattr(L, "alive", lambda pid: True)
    monkeypatch.setattr(Path, "read_bytes", lambda p: b"python -m uvicorn")
    monkeypatch.setattr(L.os, "kill", lambda *a: None)
    ticks = iter([0, 31])
    monkeypatch.setattr(L.time, "monotonic", lambda: next(ticks))
    with pytest.raises(ValueError, match="has not exited"):
        L.stop_owned(tmp_path, log, tmp_path / "saved-logs")
    assert pidfile.exists()


def test_archive_contains_only_explicit_review_files(tmp_path):
    review, key, rubric = tmp_path / "review.csv", tmp_path / "review.key.json", tmp_path / "rubric.md"
    review.write_text("id,A,B\n")
    key.write_text('{"model":"hidden"}')
    rubric.write_text("AI review only")
    archive = L.review_archive([(review, "codex/review.csv"), (rubric, "rubric.md")], tmp_path)
    with tarfile.open(archive) as tar:
        assert tar.getnames() == ["codex/review.csv", "rubric.md"]
    with pytest.raises(ValueError, match="Not a blind"):
        L.review_archive([(key, "key.json")], tmp_path)


def test_existing_stage_is_not_overwritten(tmp_path, monkeypatch):
    out = tmp_path / "stage"
    out.mkdir()
    (out / "stage.json").write_text("{}")
    prior = out / "stage_summary.md"
    prior.write_text("prior evidence")
    monkeypatch.setattr(L, "ROOT", tmp_path)
    assert L.main(["teacher-check", "--out", str(out)]) == 1
    assert prior.read_text() == "prior evidence"


@pytest.mark.parametrize("teacher", ["14b-q4", "32b-q4"])
def test_teacher_check_runs_only_8b_and_selected_teacher(tmp_path, monkeypatch, teacher):
    calls = []
    class FakePipeline:
        def __init__(self, root, out, stage, teacher):
            self.python = root / "python"
        def disk_preflight(self, profiles, **kwargs):
            calls.append(("disk", profiles))
        def ports(self, profiles):
            calls.append(("ports", profiles))
            return []
        def evaluate(self, *args):
            calls.append(("evaluate", args))
        def compare(self):
            calls.append(("compare",))
        def summary(self, *args):
            calls.append(("summary", args))
    (tmp_path / "python").touch()
    monkeypatch.setattr(L, "ROOT", tmp_path)
    monkeypatch.setattr(L, "Pipeline", FakePipeline)
    monkeypatch.setattr(L, "ensure_idle", lambda *args: None)
    arguments = [] if teacher == "14b-q4" else ["--teacher", teacher]
    assert L.main(["teacher-check", "--out", str(tmp_path / "stage"), *arguments]) == 0
    assert [args for name, *rest in calls if name == "evaluate" for args in rest] == [("8b-q4", "baseline"), (teacher, "candidate")]
    assert calls.index(("disk", ("8b-q4", teacher))) < calls.index(("evaluate", ("8b-q4", "baseline")))
    assert json.loads((tmp_path / "stage/stage.json").read_text())["teacher"] == teacher


def test_finish_only_calls_review_gate(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(L, "ROOT", tmp_path)
    monkeypatch.setattr(L, "ensure_idle", lambda *args: pytest.fail("review finalization must not touch servers"))
    monkeypatch.setattr(L.subprocess, "call", lambda command, **kwargs: calls.append(command) or 0)
    assert L.main(["train-eval", "--finish", str(tmp_path / "stage"), "--reviews", str(tmp_path / "reviews")]) == 0
    assert calls[0][1].endswith("check_adoption.py")
    assert "--reviews" in calls[0]


@pytest.mark.parametrize("teacher", ["14b-q4", "32b-q4"])
@pytest.mark.parametrize("ratio", [0, .5, .75])
def test_train_commands_keep_adapter_identity_for_comparison(tmp_path, monkeypatch, teacher, ratio):
    (tmp_path / "train").mkdir()
    cfg = {"base_model": "Qwen/Qwen3-8B", "base_revision": "pinned", "max_length": 4096}
    (tmp_path / "train" / "training_config.json").write_text(json.dumps(cfg))
    for name in (".venv-train", ".venv-convert"):
        folder = tmp_path / name / "bin"
        folder.mkdir(parents=True)
        (folder / "python").touch()
    data, out = tmp_path / "data", tmp_path / "stage"
    data.mkdir()
    out.mkdir()
    for name in ("distill.draft.jsonl", "sample.evidence.json"):
        (data / name).write_text("{}")
    pipeline, calls = L.Pipeline(tmp_path, out, "train-eval", teacher), []
    pipeline.stt_copies_ratio = ratio
    monkeypatch.setattr(pipeline, "run", lambda command, name, env=None: calls.append((name, command, env)))
    monkeypatch.setattr(pipeline, "evaluate", lambda *args: calls.append(("evaluate", args)))
    monkeypatch.setattr(pipeline, "compare", lambda *args: calls.append(("compare", args)))
    pipeline.train_eval(data)
    prepare = next(c[1] for c in calls if c[0] == "prepare")
    assert prepare[prepare.index("--ai-review") + 1] == data / "sample.evidence.json"
    assert prepare[prepare.index("--tokenizer-revision") + 1] == "pinned"
    assert prepare[prepare.index("--teacher") + 1] == teacher
    assert prepare[prepare.index("--stt-copies-ratio") + 1] == str(ratio)
    train = next(c[1] for c in calls if c[0] == "train")
    assert train[train.index("--teacher") + 1] == teacher
    assert json.loads((out / "training_config.json").read_text())["teacher_profile"] == teacher
    assert ("evaluate", ("8b-q4", "baseline")) in calls
    assert ("evaluate", ("8b-q4", "candidate", out / "adapter.gguf")) in calls
    assert next(c[2] for c in calls if c[0] == "convert")["MODEL_PROFILE"] == "8b-q4"
    pipeline.summary()
    assert json.loads((out / "stage_summary.json").read_text())["stt_copies_ratio"] == ratio
    assert f"requested STT copies ratio: {ratio}" in (out / "stage_summary.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("value,expected", [(None, .5), ("0", 0), ("1", 1)])
def test_train_eval_cli_records_stt_ratio_before_preparation(tmp_path, monkeypatch, value, expected):
    calls = []
    class FakePipeline:
        def __init__(self, root, out, stage, teacher):
            self.python = root / "python"
        def ports(self, profiles):
            return []
        def disk_preflight(self, profiles, **kwargs):
            pass
        def train_eval(self, data):
            calls.append((data, self.stt_copies_ratio))
        def summary(self):
            pass
    (tmp_path / "python").touch()
    monkeypatch.setattr(L, "ROOT", tmp_path)
    monkeypatch.setattr(L, "Pipeline", FakePipeline)
    monkeypatch.setattr(L, "ensure_idle", lambda *args: None)
    out, data = tmp_path / "stage", tmp_path / "data"
    args = ["train-eval", "--out", str(out), "--data", str(data)]
    if value is not None:
        args += ["--stt-copies-ratio", value]
    assert L.main(args) == 0
    assert calls == [(data.resolve(), expected)]
    assert json.loads((out / "stage.json").read_text())["stt_copies_ratio"] == expected


@pytest.mark.parametrize("value", ["-0.1", "1.01", "nan", "inf", "-inf", "not-a-number"])
def test_invalid_stt_ratio_fails_before_creating_stage(tmp_path, value):
    out = tmp_path / "stage"
    with pytest.raises(SystemExit) as error:
        L.main(["train-eval", "--data", str(tmp_path / "data"), "--out", str(out), f"--stt-copies-ratio={value}"])
    assert error.value.code == 2
    assert not out.exists()


@pytest.mark.parametrize("args", [["teacher-check"], ["generate"], ["train-eval", "--finish", "stage", "--reviews", "reviews"]])
def test_stt_ratio_is_only_a_preparation_option(args):
    with pytest.raises(SystemExit) as error:
        L.main([*args, "--stt-copies-ratio", "0"])
    assert error.value.code == 2


def test_start_failure_still_cleans_only_owned_processes(tmp_path, monkeypatch):
    pipeline = L.Pipeline(tmp_path, tmp_path, "teacher-check")
    calls = []
    monkeypatch.setattr(pipeline, "ports", lambda profiles: [])
    monkeypatch.setattr(pipeline, "manifest", lambda profile: {"model": {"alias": "test"}, "runtime": {"port": 8081}})
    monkeypatch.setattr(L, "ensure_idle", lambda *a: None)
    def run(command, name, env):
        if name.endswith("-start"):
            raise ValueError("start failed")
    monkeypatch.setattr(pipeline, "run", run)
    monkeypatch.setattr(L, "stop_owned", lambda *args: calls.append(args))
    with pytest.raises(ValueError, match="start failed"):
        with pipeline.server("8b-q4", "baseline"):
            pytest.fail("server never started")
    assert calls[0][1] == tmp_path / "logs" / "baseline-start.log"


def test_comparison_exports_real_blind_review_tools_and_key_free_archive(tmp_path, monkeypatch):
    out = tmp_path / "stage"
    out.mkdir()
    pipeline = L.Pipeline(L.ROOT, out, "teacher-check")
    pipeline.python = Path(sys.executable)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for arm in ("baseline", "candidate"):
        for dataset in L.DATASETS:
            folder = out / "evaluations" / arm / dataset
            folder.mkdir(parents=True)
            row = {"case": {"id": "synthetic", "task": "translate", "text": "Synthetic utterance",
                            "source_locale": "en-US", "target_locale": "ko-KR"}, "http": 200,
                   "body": {"status": "ok", "translated_text": "합성 시험 출력이에요."}, "pass": True, "flags": []}
            raw = json.dumps(row, ensure_ascii=False).encode("utf-8")
            (folder / "results.jsonl").write_bytes(raw)
            (folder / "run.json").write_text(json.dumps({"label": f"private-{arm}", "dataset": f"{dataset}.jsonl",
                                                        "model_profile": f"private-{arm}",
                                                        "results_sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")
            pipeline.runs[(arm, dataset)] = folder
    pipeline.compare()
    manifest = json.loads((out / "comparison_manifest.json").read_text())
    assert len(manifest["comparisons"]) == 3
    assert all(Path(entry["key"]).exists() for entry in manifest["comparisons"])
    with tarfile.open(pipeline.archive) as tar:
        assert len(tar.getnames()) == 13
        for member in tar:
            assert "key" not in member.name and member.name.endswith((".csv", ".md"))
            assert "private-baseline" not in tar.extractfile(member).read().decode("utf-8-sig")
    for dataset in L.DATASETS:
        score = json.loads((out / "scores" / dataset / "review_summary.json").read_text(encoding="utf-8"))
        assert score["paired_comparison"]["reviewers"]["codex"]["complete_rows"] == 0
