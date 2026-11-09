"""One artifact directory per LoRA preparation stage; actual model runs happen only when invoked."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import MODEL_PROFILES, TEACHER_PROFILES  # noqa: E402
from runtime_identity import process_identity  # noqa: E402

DATASETS = ("general", "general_es", "naturalness.dev")
STAGE_HEADROOM = 2 * 1024 ** 3


def existing_parent(path):
    path = path.resolve()
    while not path.exists():
        path = path.parent
    return path


def copy_process_logs(root, name, log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for stream in ("out", "err"):
        source = root / "logs" / f"{name}.{stream}.log"
        if source.is_file():
            target = log_dir / source.name
            shutil.copy2(source, target)
            copied.append(target)
    return copied


def alive(pid):
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A terminated process awaiting its parent does not own a listening server.
    status = Path(f"/proc/{pid}/stat")
    try:
        return status.read_text().rsplit(")", 1)[1].split()[0] != "Z" if status.exists() else True
    except (OSError, IndexError):
        return True


def ensure_idle(root, ports):
    for name in ("ai", "llama"):
        path = root / "run" / f"{name}.pid"
        if path.exists():
            try:
                pid = int(path.read_text().strip())
            except ValueError as exc:
                raise ValueError(f"Invalid PID file; inspect it before continuing: {path}") from exc
            if alive(pid):
                raise ValueError(f"An existing {name} process ({pid}) is active; it was left running.")
    for port in set(ports):
        with socket.socket() as sock:
            sock.settimeout(.2)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                raise ValueError(f"Port {port} is occupied; existing servers were left running.")


def stop_owned(root, start_log, log_dir):
    if not start_log.exists():
        return
    entries = re.findall(r"^(ai|llama) started \(pid (\d+)\),", start_log.read_text(encoding="utf-8"), re.M)
    waiting, refused, log_errors = [], [], []
    for name, raw_pid in entries:
        pid = int(raw_pid)
        pidfile = root / "run" / f"{name}.pid"
        matching = pidfile.exists() and pidfile.read_text().strip() == raw_pid
        if not alive(pid):
            if matching:
                try:
                    copy_process_logs(root, name, log_dir)
                except OSError as error:
                    log_errors.append(str(error))
                pidfile.unlink()
            continue
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            command = ""
        tag = "uvicorn" if name == "ai" else "llama-server"
        if not matching or tag not in command:
            refused.append(f"{name} PID {pid}")
            continue
        try:
            copy_process_logs(root, name, log_dir)
        except OSError as error:
            log_errors.append(str(error))
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        waiting.append((pid, pidfile, raw_pid))
    deadline = time.monotonic() + 30
    for pid, pidfile, raw_pid in waiting:
        while alive(pid):
            if time.monotonic() >= deadline:
                raise ValueError(f"Owned process {pid} has not exited; no next model was started.")
            time.sleep(.2)
        if pidfile.exists() and pidfile.read_text().strip() == raw_pid:
            pidfile.unlink()
    if refused:
        raise ValueError("Cannot confirm process ownership; left running: " + ", ".join(refused))
    if log_errors:
        raise ValueError("Owned processes stopped, but log archival failed: " + "; ".join(log_errors))


def review_archive(files, home):
    """An allow-list, not a recursive directory archive: keys and model metadata never enter it."""
    for source, _ in files:
        if source.suffix not in (".csv", ".md") or "key" in source.name.lower():
            raise ValueError(f"Not a blind review artifact: {source.name}")
    path = home / f"roadmate-review-{uuid.uuid4().hex[:12]}.tgz"
    with path.open("xb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for source, name in files:
            archive.add(source, arcname=name, recursive=False)
    return path


class Pipeline:
    def __init__(self, root, out, stage, teacher="14b-q4"):
        self.root, self.out, self.stage = root, out, stage
        self.teacher = teacher
        self.python = root / ".venv" / "bin" / "python"
        self.env = {**os.environ, "LOG_REJECTED": "1", "MODEL_BACKEND": "llama", "MODEL_PROFILE": "8b-q4",
                    "PYTHONIOENCODING": "utf-8"}
        self.env.pop("MODEL_ID", None)
        self.env["LORA_ADAPTER"] = ""
        self.runs, self.archive = {}, None
        self.disk_checks, self.offload_checks = [], {}
        self.started = time.monotonic()

    def manifest(self, profile):
        return json.loads((self.root / "models" / MODEL_PROFILES[profile]).read_text(encoding="utf-8"))

    def ports(self, profiles):
        return [int(self.env.get("AI_PORT", "8000")), *(self.manifest(p)["runtime"]["port"] for p in profiles)]

    def require_32b_settings(self):
        runtime = self.manifest("32b-q4")["runtime"]
        if runtime["ctx"] != 4096 or runtime["n_gpu_layers"] != 99:
            raise ValueError("32b-q4 requires manifest ctx=4096 and n_gpu_layers=99; no CPU fallback")
        if self.env.get("GPU_LAYERS", "99") != "99":
            raise ValueError("32b-q4 requires GPU_LAYERS=99; remove the override or set 99")
        if self.env.get("LLAMA_ARG_N_CPU_FFN", "0") not in ("", "0"):
            raise ValueError("32b-q4 refuses LLAMA_ARG_N_CPU_FFN: CPU tensor fallback is disabled")
        if self.env.get("LLAMA_ARG_OVERRIDE_TENSOR"):
            raise ValueError("32b-q4 refuses LLAMA_ARG_OVERRIDE_TENSOR: custom tensor placement is disabled")
        if "GGML_CUDA_ENABLE_UNIFIED_MEMORY" in self.env:
            raise ValueError("32b-q4 refuses GGML_CUDA_ENABLE_UNIFIED_MEMORY: CPU memory fallback is disabled")

    def disk_preflight(self, profiles, *, copy_old_logs=False):
        if self.teacher != "32b-q4" or self.stage not in ("teacher-check", "generate"):
            return
        self.require_32b_settings()
        entries = []
        for profile in dict.fromkeys(profiles):
            model = self.manifest(profile)["model"]
            target = self.root / "models" / model["gguf_file"]
            cached = target.is_file() and target.stat().st_size == model["gguf_size"]
            entries.append({"purpose": profile + " GGUF", "path": str(target.parent),
                            "cached_size_matches": cached, "required_bytes": 0 if cached else model["gguf_size"]})
        old_logs = sum(p.stat().st_size for p in (self.root / "logs").rglob("*") if p.is_file()) if copy_old_logs else 0
        entries.append({"purpose": "stage headroom and prior log copy", "path": str(self.out),
                        "required_bytes": STAGE_HEADROOM + old_logs})
        filesystems = {}
        for entry in entries:
            parent = existing_parent(Path(entry["path"]))
            device = str(parent.stat().st_dev)
            group = filesystems.setdefault(device, {"device": device, "probe_path": str(parent),
                "free_bytes": shutil.disk_usage(parent).free, "required_bytes": 0, "entries": []})
            group["required_bytes"] += entry["required_bytes"]
            group["entries"].append(entry)
        evidence = {"profiles": list(profiles), "stage_headroom_bytes": STAGE_HEADROOM,
                    "prior_logs_bytes": old_logs, "filesystems": list(filesystems.values())}
        self.disk_checks.append(evidence)
        for group in filesystems.values():
            if group["free_bytes"] < group["required_bytes"]:
                raise ValueError(f"Insufficient disk space at {group['probe_path']}: need {group['required_bytes']} bytes, "
                                 f"free {group['free_bytes']}; next download/start blocked; existing artifacts preserved")

    def inspect_32b_offload(self, start_log, arm, startup_succeeded):
        text = start_log.read_text(encoding="utf-8") if start_log.is_file() else ""
        pids = re.findall(r"^llama started \(pid (\d+)\),", text, re.M)
        pidfile = self.root / "run" / "llama.pid"
        owned = len(pids) == 1 and pidfile.is_file() and pidfile.read_text().strip() == pids[0]
        process, error = None, None
        if owned:
            try:
                process = process_identity(pidfile)
                if process["pid"] != int(pids[0]):
                    raise ValueError("model PID changed after startup")
            except (OSError, ValueError) as exc:
                process, error = None, str(exc)
        paths = copy_process_logs(self.root, "llama", self.out / "logs" / arm) if owned else []
        raw = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in paths)
        reports = [{"offloaded": int(a), "total": int(b)}
                   for a, b in re.findall(r"offloaded\s+(\d+)/(\d+)\s+layers to GPU", raw)]
        cuda = [float(n) for n in re.findall(r"CUDA\d+\s+model buffer size\s*=\s*([\d.]+)\s*MiB", raw)]
        failures = [line for line in raw.splitlines() if re.search(
            r"failed to load (?:the )?model|error loading model|failed to allocate|out of memory|CUDA error|cudaMalloc.*failed",
            line, re.I)]
        partial = bool(reports and not all(r["offloaded"] == r["total"] > 0 for r in reports))
        full_logs = bool(reports and not partial and any(n > 0 for n in cuda))
        verified = bool(startup_succeeded and process and full_logs and not failures)
        method = "offload_logs" if verified else None
        model_bytes = self.manifest("32b-q4")["model"]["gguf_size"]
        threshold = (model_bytes * 9 + 9) // 10
        gpu = {"command": ["nvidia-smi", "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
               "model_bytes": model_bytes, "minimum_bytes": threshold, "minimum_ratio": .9,
               "process_memory_mib": [], "maximum_device_bytes": None, "stdout": None, "stderr": None}
        if startup_succeeded and process and not verified and not partial and not failures and not (cuda and not any(cuda)):
            try:
                result = subprocess.run(gpu["command"], capture_output=True, text=True, timeout=10, env=self.env)
                gpu.update(stdout=result.stdout, stderr=result.stderr, returncode=result.returncode)
                if result.returncode:
                    raise ValueError(f"nvidia-smi failed ({result.returncode})")
                for line in result.stdout.splitlines():
                    columns = [part.strip() for part in line.split(",")]
                    if len(columns) != 2 or not columns[0].isdigit():
                        raise ValueError("nvidia-smi returned an invalid process-memory row")
                    if int(columns[0]) == process["pid"]:
                        if not re.fullmatch(r"\d+(?:\.\d+)?", columns[1]):
                            raise ValueError("GPU memory for llama-server is unavailable (N/A or invalid)")
                        gpu["process_memory_mib"].append(float(columns[1]))
                if process_identity(pidfile) != process:
                    raise ValueError("llama-server process identity changed during GPU memory query")
                if not gpu["process_memory_mib"]:
                    raise ValueError("llama-server PID absent from nvidia-smi; host/container PID mismatch or no GPU allocation")
                # One configured CUDA device must fit; allocations on different GPUs must not be summed.
                gpu["maximum_device_bytes"] = max(gpu["process_memory_mib"]) * 1024 ** 2
                verified = gpu["maximum_device_bytes"] >= threshold
                if verified:
                    method = "nvidia_smi_process_memory"
                else:
                    error = "llama-server GPU memory is below 90% of GGUF size; CPU fallback is not allowed"
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                error = str(exc)
        if not verified and not error:
            error = ("model startup failed" if not startup_succeeded else "startup PID ownership was not verified" if not process
                     else "explicit partial/invalid offload or loading failure in model logs")
        evidence = {"profile": "32b-q4", "startup_succeeded": startup_succeeded,
                    "requested_gpu_layers": 99, "device": "CUDA0", "fit": "off", "context": 4096,
                    "pid": int(pids[0]) if len(pids) == 1 else None, "pidfile_matches": owned,
                    "process": process, "verification_method": method, "gpu_memory": gpu, "error": error,
                    "raw_logs": [str(p) for p in paths], "offload_reports": reports,
                    "cuda_model_buffer_mib": cuda, "loading_failures": failures, "verified": verified}
        self.offload_checks[arm] = evidence
        return verified

    def run(self, command, name, env=None):
        log = self.out / "logs" / f"{name}.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        print(f"Running {name}; log: {log}", flush=True)
        with log.open("x", encoding="utf-8") as stream:
            result = subprocess.run([str(x) for x in command], cwd=self.root, env=env or self.env,
                                    stdout=stream, stderr=subprocess.STDOUT)
        if result.returncode:
            raise ValueError(f"{name} failed ({result.returncode}); see {log}")
        return log

    @contextmanager
    def server(self, profile, arm, adapter=None):
        ensure_idle(self.root, self.ports((profile,)))
        manifest = self.manifest(profile)
        env = {**self.env, "MODEL_PROFILE": profile, "MODEL_ID": manifest["model"]["alias"],
               "MODEL_BASE_URL": f"http://127.0.0.1:{manifest['runtime']['port']}/v1",
               "LORA_ADAPTER": str(adapter) if adapter else ""}
        if profile == "32b-q4":
            self.require_32b_settings()
            env.update(GPU_LAYERS="99", LLAMA_ARG_FIT="off", LLAMA_ARG_DEVICE="CUDA0")
        self.disk_preflight((profile,))
        (self.out / f"{arm}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        self.run([self.python, "scripts/download_model.py"], f"{arm}-download", env)
        start_log = self.out / "logs" / f"{arm}-start.log"
        startup_succeeded = False
        try:
            command = ["bash", "scripts/run.sh", "start"] + (["--lora", str(adapter)] if adapter else [])
            self.run(command, f"{arm}-start", env)
            startup_succeeded = True
            if profile == "32b-q4" and not self.inspect_32b_offload(start_log, arm, True):
                raise ValueError("32b-q4 full CUDA offload was not verified: " + self.offload_checks[arm]["error"]
                                 + "; no evaluation or generation run")
            yield env
        finally:
            try:
                if profile == "32b-q4":
                    self.inspect_32b_offload(start_log, arm, startup_succeeded)
            finally:
                stop_owned(self.root, start_log, self.out / "logs" / arm)

    def evaluate(self, profile, arm, adapter=None):
        with self.server(profile, arm, adapter) as env:
            for dataset in DATASETS:
                log = self.run([self.python, "eval/evaluate.py", "--file", f"eval/{dataset}.jsonl", "--label",
                                f"{self.out.name}-{arm}", "--url", f"http://127.0.0.1:{env.get('AI_PORT', '8000')}",
                                "--attempt-log", "logs/ai.err.log"], f"{arm}-{dataset}", env)
                paths = re.findall(r"^saved: (.+)$", log.read_text(encoding="utf-8"), re.M)
                if len(paths) != 1:
                    raise ValueError(f"Cannot locate this evaluation's artifacts: {log}")
                source = Path(paths[0].strip())
                source = source if source.is_absolute() else self.root / source
                source = source.resolve()
                source.relative_to((self.root / "eval" / "results").resolve())
                if not (source / "run.json").is_file():
                    raise ValueError(f"Evaluation metadata missing: {source}")
                target = self.out / "evaluations" / arm / dataset
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                self.runs[(arm, dataset)] = target

    def compare(self, adapter=None):
        entries, files = [], []
        for dataset in DATASETS:
            label = f"pair01-{dataset}"
            folder = self.out / "private"
            baseline, candidate = self.runs[("baseline", dataset)], self.runs[("candidate", dataset)]
            self.run([self.python, "eval/export_review.py", baseline, candidate, "--label", label,
                      "--out-dir", folder], f"export-{dataset}")
            review, key = folder / f"review_{label}.csv", folder / f"review_{label}.key.json"
            entries.append({"dataset": dataset, "label": label, "baseline": str(baseline), "candidate": str(candidate),
                            "key": str(key), "review": str(review)})
            for reviewer in ("claude", "codex"):
                dest = self.out / "reviews" / reviewer
                self.run([self.python, "eval/export_review.py", "--review", review, "--key", key,
                          "--reviewer", reviewer, "--out-dir", dest, "--label", label], f"export-{reviewer}-{dataset}")
                files += [(dest / f"review_{label}.{suffix}", f"{reviewer}/review_{label}.{suffix}") for suffix in ("csv", "md")]
            self.run([self.python, "eval/score_review.py", review, "--key", key, "--out-dir", self.out / "scores" / dataset],
                     f"score-{dataset}")
        (self.out / "comparison_manifest.json").write_text(json.dumps({"stage": self.stage, "adapter": str(adapter) if adapter else None,
                                                                      "comparisons": entries}, indent=2) + "\n", encoding="utf-8")
        self.archive_reviews(files)

    def archive_reviews(self, files):
        files.append((self.root / "docs" / "naturalness_rubric.md", "naturalness_rubric.md"))
        self.archive = review_archive(files, Path.home())

    def generate(self):
        with self.server(self.teacher, "teacher") as env:
            self.run([self.python, "train/distill.py", "generate", "--pool", "train/sources/pool.jsonl",
                      "--out", self.out, "--teacher", self.teacher,
                      "--url", f"http://127.0.0.1:{env.get('AI_PORT', '8000')}"], "generate", env)
        draft = self.out / "distill.draft.jsonl"
        canonical = self.root / "train" / "data" / "distill.draft.jsonl"
        if canonical.exists():
            shutil.copy2(canonical, self.out / "previous-distill.draft.jsonl")
        shutil.copy2(draft, canonical)
        files = [(self.out / "sample.review.csv", "sample.review.csv")]
        if (self.out / "sample.review.md").exists():
            files.append((self.out / "sample.review.md", "sample.review.md"))
        self.archive_reviews(files)

    def train_eval(self, data):
        config = json.loads((self.root / "train" / "training_config.json").read_text(encoding="utf-8"))
        config["teacher_profile"] = self.teacher
        draft, evidence = data / "distill.draft.jsonl", data / "sample.evidence.json"
        if not draft.is_file() or not evidence.is_file():
            raise ValueError(f"Teacher draft and completed AI sample evidence are required in {data}")
        train_python = self.root / ".venv-train" / "bin" / "python"
        for path in (train_python, self.root / ".venv-convert" / "bin" / "python"):
            if not path.is_file():
                raise ValueError(f"Missing environment: {path}. Run setup-train and setup-convert first.")
        snapshot = self.out / "training_config.json"
        snapshot.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        prepared = self.out / "prepared"
        self.run([train_python, "train/prepare_data.py", "--inputs", draft, "--ai-review", evidence, "--out", prepared,
                  "--tokenizer", config["base_model"], "--tokenizer-revision", config["base_revision"],
                  "--max-length", str(config["max_length"]), "--teacher", self.teacher], "prepare")
        self.run([train_python, "train/train_qlora.py", "--config", snapshot, "--data-dir", prepared,
                  "--output-dir", self.out / "training", "--teacher", self.teacher], "train")
        adapter = self.out / "adapter.gguf"
        self.run([self.python, "train/convert_adapter.py", "--adapter", self.out / "training" / "adapter",
                  "--out", adapter, "--profile", "8b-q4"], "convert", {**self.env, "MODEL_PROFILE": "8b-q4"})
        self.evaluate("8b-q4", "baseline")
        self.evaluate("8b-q4", "candidate", adapter)
        self.compare(adapter)
        self.run([self.python, "train/check_adoption.py", "--stage-dir", self.out], "adoption-pending")

    def summary(self, error=None):
        lines = [f"# LoRA {self.stage}", "", f"- artifacts: {self.out}",
                 f"- teacher: {self.teacher}",
                 f"- status: {'failed' if error else 'AI 검토 대기 (참고)'}",
                 f"- elapsed seconds: {time.monotonic() - self.started:.1f}"]
        if error:
            lines.append(f"- error: {error}")
        if self.archive:
            lines.append(f"- blind review archive (no keys): {self.archive}")
        for index, check in enumerate(self.disk_checks, 1):
            for fs in check["filesystems"]:
                lines.append(f"- disk check {index}: {fs['probe_path']}; required={fs['required_bytes']} bytes; free={fs['free_bytes']} bytes")
        for arm, check in self.offload_checks.items():
            lines.append(f"- {arm} CUDA offload verified: {check['verified']}; method={check['verification_method']}; reports={check['offload_reports']}; "
                         f"CUDA model buffers MiB={check['cuda_model_buffer_mib']}; startup succeeded={check['startup_succeeded']}")
            gpu = check["gpu_memory"]
            lines.append(f"- {arm} GPU memory: PID={check['pid']}; per-device MiB={gpu['process_memory_mib']}; "
                         f"maximum device bytes={gpu['maximum_device_bytes']}; required bytes={gpu['minimum_bytes']} (90% GGUF)")
            if check["error"]:
                lines.append(f"- {arm} offload check error: {check['error']}")
            lines.append(f"- {arm} raw logs: {', '.join(check['raw_logs']) or 'no owned startup log'}")
            if check["loading_failures"]:
                lines.append(f"- {arm} loading failures: {check['loading_failures']}")
        for (arm, dataset), run in self.runs.items():
            lines += ["", f"## {arm} / {dataset}", ""]
            rows = [json.loads(line) for line in (run / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            lines.append("- FAIL: " + (", ".join(r["case"]["id"] for r in rows if not r["pass"]) or "none"))
            lines.append(f"- 502: {sum(r['http'] == 502 for r in rows)}")
            prefixes = ("|", "- model_profile", "- model latency", "- HTTP wall", "- first-attempt", "- retry", "- Korean translationese")
            lines += [line for line in (run / "summary.md").read_text(encoding="utf-8").splitlines() if line.startswith(prefixes)]
        if self.stage == "generate" and (self.out / "summary.md").exists():
            lines += ["", (self.out / "summary.md").read_text(encoding="utf-8")]
        lines += ["", "검토 결과가 비어 있으면 선생 모델 우위·학습 자료 승인·어댑터 채택을 뜻하지 않습니다."]
        path = self.out / "stage_summary.md"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        (self.out / "stage_summary.json").write_text(json.dumps({"stage": self.stage, "teacher": self.teacher,
            "error": error, "disk_checks": self.disk_checks, "offload_checks": self.offload_checks},
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("\n".join(lines), flush=True)
        print(f"summary: {path}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("teacher-check", "generate", "train-eval"))
    parser.add_argument("--teacher", choices=TEACHER_PROFILES, default="14b-q4")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--data", type=Path, help="Completed generate folder with sample.evidence.json")
    parser.add_argument("--finish", type=Path, help="Existing train-eval folder; review decision only, no model execution")
    parser.add_argument("--reviews", type=Path, help="Merged review_pair01-*.csv directory, used with --finish")
    args = parser.parse_args(argv)
    if args.finish:
        if args.stage != "train-eval" or args.out or args.data or not args.reviews:
            parser.error("Use train-eval --finish STAGE_DIR --reviews MERGED_REVIEW_DIR")
        return subprocess.call([str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "train" / "check_adoption.py"),
                                "--stage-dir", str(args.finish.resolve()), "--reviews", str(args.reviews.resolve())], cwd=ROOT)
    if args.reviews or (args.stage == "train-eval") != bool(args.data):
        parser.error("--data is required only for train-eval; --reviews requires --finish")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = (args.out or ROOT / "train" / "runs" / f"{args.stage}-{stamp}-{uuid.uuid4().hex[:6]}").resolve()
    pipeline = Pipeline(ROOT, out, args.stage, args.teacher)
    created = False
    try:
        if out.exists():
            raise ValueError(f"Output already exists; prior evidence was preserved: {out}")
        if not pipeline.python.is_file():
            raise ValueError("Run bash scripts/run.sh setup first.")
        profiles = ("8b-q4", args.teacher) if args.stage == "teacher-check" else (args.teacher,) if args.stage == "generate" else ("8b-q4",)
        ensure_idle(ROOT, pipeline.ports(profiles))
        out.mkdir(parents=True)
        created = True
        pipeline.disk_preflight(profiles, copy_old_logs=True)
        if (ROOT / "logs").is_dir():
            shutil.copytree(ROOT / "logs", out / "logs-before")
        (out / "stage.json").write_text(json.dumps({"stage": args.stage, "created_utc": stamp, "profiles": profiles,
                                                    "teacher": args.teacher,
                                                    "gpu_layers_override": os.environ.get("GPU_LAYERS"),
                                                    "data": str(args.data.resolve()) if args.data else None}, indent=2) + "\n", encoding="utf-8")
        if args.stage == "teacher-check":
            pipeline.evaluate("8b-q4", "baseline")
            pipeline.evaluate(args.teacher, "candidate")
            pipeline.compare()
        elif args.stage == "generate":
            pipeline.generate()
        else:
            pipeline.train_eval(args.data.resolve())
        pipeline.summary()
        return 0
    except (ValueError, OSError, KeyboardInterrupt) as exc:
        if created:
            pipeline.summary(str(exc) or "interrupted")
        else:
            print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
