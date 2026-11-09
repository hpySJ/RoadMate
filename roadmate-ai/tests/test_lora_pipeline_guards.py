"""32B launch guards use synthetic manifests, logs and free-space reports only."""
from contextlib import contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import lora_pipeline as L


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    root, out = tmp_path / "repo", tmp_path / "stage"
    (root / "models").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "run").mkdir()
    out.mkdir()
    for profile, size in (("8b-q4", 50), ("14b-q4", 90), ("32b-q4", 197)):
        (root / "models" / L.MODEL_PROFILES[profile]).write_text(json.dumps({
            "model": {"alias": profile, "gguf_file": profile + ".gguf", "gguf_size": size},
            "runtime": {"port": 8081, "ctx": 4096, "n_gpu_layers": 99}}))
    monkeypatch.setattr(L, "STAGE_HEADROOM", 1000)
    monkeypatch.setattr(L.shutil, "disk_usage", lambda path: SimpleNamespace(free=100000))
    result = L.Pipeline(root, out, "teacher-check", "32b-q4")
    for variable in ("GPU_LAYERS", "LLAMA_ARG_N_CPU_FFN", "LLAMA_ARG_OVERRIDE_TENSOR", "GGML_CUDA_ENABLE_UNIFIED_MEMORY"):
        result.env.pop(variable, None)
    return result


def test_disk_sums_missing_models_headroom_and_log_copy_on_same_filesystem(pipeline):
    (pipeline.root / "logs" / "previous.log").write_bytes(b"1234567")
    pipeline.disk_preflight(("8b-q4", "32b-q4"), copy_old_logs=True)
    check = pipeline.disk_checks[-1]
    assert check["prior_logs_bytes"] == 7
    assert len(check["filesystems"]) == 1
    assert check["filesystems"][0]["required_bytes"] == 50 + 197 + 1000 + 7
    (pipeline.root / "models" / "32b-q4.gguf").write_bytes(b"x" * 197)
    pipeline.disk_preflight(("8b-q4", "32b-q4"))
    assert pipeline.disk_checks[-1]["filesystems"][0]["required_bytes"] == 1050
    (pipeline.root / "models" / "32b-q4.gguf").write_bytes(b"partial")
    pipeline.disk_preflight(("8b-q4", "32b-q4"))
    assert pipeline.disk_checks[-1]["filesystems"][0]["required_bytes"] == 1247


def test_disk_separate_filesystems_each_need_enough_space(pipeline, monkeypatch):
    class Probe:
        def __init__(self, path):
            self.path = path
        def stat(self):
            return SimpleNamespace(st_dev=1 if self.path == pipeline.root / "models" else 2)
        def __str__(self):
            return str(self.path)
    monkeypatch.setattr(L, "existing_parent", Probe)
    monkeypatch.setattr(L.shutil, "disk_usage", lambda path: SimpleNamespace(free=246 if path.stat().st_dev == 1 else 5000))
    with pytest.raises(ValueError, match="Insufficient disk"):
        pipeline.disk_preflight(("8b-q4", "32b-q4"))
    fs = pipeline.disk_checks[-1]["filesystems"]
    assert [f["required_bytes"] for f in fs] == [247, 1000]
    assert [f["free_bytes"] for f in fs] == [246, 5000]


def test_low_disk_fails_before_download_and_preserves_old_logs(pipeline, monkeypatch):
    python = pipeline.root / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.touch()
    (pipeline.root / "logs" / "previous.log").write_text("prior evidence")
    out = pipeline.out / "new-stage"
    monkeypatch.setattr(L, "ROOT", pipeline.root)
    monkeypatch.setattr(L.shutil, "disk_usage", lambda path: SimpleNamespace(free=10))
    monkeypatch.setattr(L, "ensure_idle", lambda *args: None)
    monkeypatch.setattr(L.Pipeline, "run", lambda *args, **kwargs: pytest.fail("must fail before download/start"))
    assert L.main(["teacher-check", "--teacher", "32b-q4", "--out", str(out)]) == 1
    assert not (out / "logs-before").exists()
    summary = json.loads((out / "stage_summary.json").read_text(encoding="utf-8"))
    assert summary["disk_checks"] and "Insufficient disk" in summary["error"]
    assert (pipeline.root / "logs" / "previous.log").read_text() == "prior evidence"


@pytest.mark.parametrize("variable,value", [
    ("GPU_LAYERS", "20"), ("GPU_LAYERS", "-1"), ("LLAMA_ARG_N_CPU_FFN", "1"),
    ("LLAMA_ARG_OVERRIDE_TENSOR", "blk.*=CPU"), ("GGML_CUDA_ENABLE_UNIFIED_MEMORY", "0"),
])
def test_32b_refuses_cpu_or_memory_fallback_overrides(pipeline, variable, value):
    pipeline.env[variable] = value
    with pytest.raises(ValueError, match=variable):
        pipeline.disk_preflight(("8b-q4", "32b-q4"))


@pytest.mark.parametrize("field,value", [("ctx", 2048), ("n_gpu_layers", 20)])
def test_32b_refuses_changed_launch_contract(pipeline, field, value):
    path = pipeline.root / "models" / L.MODEL_PROFILES["32b-q4"]
    manifest = json.loads(path.read_text())
    manifest["runtime"][field] = value
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="ctx=4096"):
        pipeline.disk_preflight(("32b-q4",))


def startup(pipeline, text, *, marker=True, matching=True):
    (pipeline.out / "logs").mkdir(exist_ok=True)
    start = pipeline.out / "logs" / "teacher-start.log"
    start.write_text("llama started (pid 12345), logs: logs/llama.*.log\n" if marker else "launch refused\n")
    (pipeline.root / "run" / "llama.pid").write_text("12345" if matching else "67890")
    (pipeline.root / "logs" / "llama.err.log").write_text(text)
    (pipeline.root / "logs" / "ai.err.log").write_text("stale previous-arm AI log")
    return start


FULL = "load_tensors: offloaded 65/65 layers to GPU\nload_tensors: CUDA0 model buffer size = 18500 MiB\nCPU input buffer size = 16 MiB\n"


@pytest.mark.parametrize("text,success,expected", [
    (FULL, True, True), (FULL.replace("65/65", "20/65"), True, False),
    ("CUDA0 model buffer size = 18500 MiB\n", True, False),
    ("offloaded 65/65 layers to GPU\n", True, False), (FULL.replace("18500", "0"), True, False),
    (FULL + "error loading model: CUDA error: out of memory\n", True, False),
    (FULL, False, False), (FULL.replace("65/65", "0/0"), True, False),
])
def test_actual_current_logs_require_full_gpu_buffers_and_success(pipeline, text, success, expected):
    start = startup(pipeline, text)
    assert pipeline.inspect_32b_offload(start, "teacher", success) is expected
    fact = pipeline.offload_checks["teacher"]
    assert fact["raw_logs"] == [str(pipeline.out / "logs/teacher/llama.err.log")]
    assert not (pipeline.out / "logs/teacher/ai.err.log").exists()
    assert Path(fact["raw_logs"][0]).read_text() == text


@pytest.mark.parametrize("marker,matching", [(False, True), (True, False)])
def test_old_or_unowned_logs_never_prove_offload(pipeline, marker, matching):
    start = startup(pipeline, FULL, marker=marker, matching=matching)
    assert not pipeline.inspect_32b_offload(start, "teacher", True)
    assert pipeline.offload_checks["teacher"]["raw_logs"] == []


@pytest.mark.parametrize("text,raises", [(FULL, None), (FULL.replace("65/65", "20/65"), "full CUDA"), (FULL, "launch failed")])
def test_server_guards_before_generation_and_records_failed_startup(pipeline, monkeypatch, text, raises):
    stopped, calls = [], []
    monkeypatch.setattr(L, "ensure_idle", lambda *args: None)
    monkeypatch.setattr(L, "stop_owned", lambda *args: stopped.append(args))
    def run(command, name, env):
        calls.append((name, env))
        if name.endswith("-start"):
            startup(pipeline, text)
            if raises == "launch failed":
                raise ValueError(raises)
    monkeypatch.setattr(pipeline, "run", run)
    def execute():
        with pipeline.server("32b-q4", "teacher") as env:
            assert raises is None
            assert env["GPU_LAYERS"] == "99"
            assert env["LLAMA_ARG_FIT"] == "off" and env["LLAMA_ARG_DEVICE"] == "CUDA0"
    if raises:
        with pytest.raises(ValueError, match=raises):
            execute()
    else:
        execute()
    assert len(stopped) == 1
    pipeline.summary(raises)
    summary = json.loads((pipeline.out / "stage_summary.json").read_text(encoding="utf-8"))
    assert summary["offload_checks"]["teacher"]["verified"] is (raises is None)
    assert summary["offload_checks"]["teacher"]["raw_logs"]
    assert [name for name, env in calls] == ["teacher-download", "teacher-start"]


def test_dead_owned_startup_keeps_crash_log_and_ignores_stale_ai(pipeline, monkeypatch):
    start = startup(pipeline, "model load crashed")
    monkeypatch.setattr(L, "alive", lambda pid: False)
    monkeypatch.setattr(L.os, "kill", lambda *args: pytest.fail("already exited"))
    saved = pipeline.out / "logs" / "teacher"
    L.stop_owned(pipeline.root, start, saved)
    assert (saved / "llama.err.log").read_text() == "model load crashed"
    assert not (saved / "ai.err.log").exists()
    assert not (pipeline.root / "run/llama.pid").exists()


def test_log_capture_error_still_cleans_owned_server(pipeline, monkeypatch):
    stopped = []
    monkeypatch.setattr(L, "ensure_idle", lambda *args: None)
    monkeypatch.setattr(L, "stop_owned", lambda *args: stopped.append(args))
    monkeypatch.setattr(pipeline, "run", lambda *args: None)
    def fail_capture(*args):
        raise OSError("cannot copy log")
    monkeypatch.setattr(pipeline, "inspect_32b_offload", fail_capture)
    with pytest.raises(OSError, match="cannot copy"):
        with pipeline.server("32b-q4", "teacher"):
            pytest.fail("capture failed before generation")
    assert len(stopped) == 1


def test_cleanup_log_copy_error_does_not_leave_live_owned_process(pipeline, monkeypatch):
    start = startup(pipeline, FULL)
    running = {12345}
    monkeypatch.setattr(L, "alive", lambda pid: pid in running)
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"llama-server --model synthetic")
    monkeypatch.setattr(L.os, "kill", lambda pid, sig: running.remove(pid))
    def cannot_copy(*args):
        raise OSError("disk full while copying")
    monkeypatch.setattr(L, "copy_process_logs", cannot_copy)
    with pytest.raises(ValueError, match="stopped, but log archival failed"):
        L.stop_owned(pipeline.root, start, pipeline.out / "saved")
    assert not running
    assert not (pipeline.root / "run/llama.pid").exists()


@pytest.mark.parametrize("teacher", ["14b-q4", "32b-q4"])
def test_generate_propagates_selected_teacher(pipeline, monkeypatch, teacher):
    pipeline.teacher = teacher
    calls = []
    (pipeline.root / "train/data").mkdir(parents=True)
    (pipeline.out / "distill.draft.jsonl").write_text("synthetic draft")
    @contextmanager
    def server(profile, arm):
        calls.append(("server", profile, arm))
        yield {}
    monkeypatch.setattr(pipeline, "server", server)
    monkeypatch.setattr(pipeline, "run", lambda command, name, env: calls.append((name, command)))
    monkeypatch.setattr(pipeline, "archive_reviews", lambda files: None)
    pipeline.generate()
    assert calls[0] == ("server", teacher, "teacher")
    command = calls[1][1]
    assert command[command.index("--teacher") + 1] == teacher


def test_8b_and_14b_launches_keep_existing_environment(pipeline, monkeypatch):
    pipeline.teacher = "14b-q4"
    pipeline.env["GPU_LAYERS"] = "20"
    calls = []
    monkeypatch.setattr(L, "ensure_idle", lambda *args: None)
    monkeypatch.setattr(L, "stop_owned", lambda *args: None)
    monkeypatch.setattr(pipeline, "run", lambda command, name, env: calls.append(env))
    for profile in ("8b-q4", "14b-q4"):
        with pipeline.server(profile, profile) as env:
            assert env["GPU_LAYERS"] == "20"
            assert env.get("LLAMA_ARG_DEVICE") == pipeline.env.get("LLAMA_ARG_DEVICE")
    assert not pipeline.disk_checks and not pipeline.offload_checks
