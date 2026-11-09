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
from config import MODEL_PROFILES  # noqa: E402

DATASETS = ("general", "general_es", "naturalness.dev")


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
    waiting, refused = [], []
    for name, raw_pid in entries:
        pid = int(raw_pid)
        pidfile = root / "run" / f"{name}.pid"
        matching = pidfile.exists() and pidfile.read_text().strip() == raw_pid
        if not alive(pid):
            if matching:
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
        log_dir.mkdir(parents=True, exist_ok=True)
        for stream in ("out", "err"):
            source = root / "logs" / f"{name}.{stream}.log"
            if source.exists():
                shutil.copy2(source, log_dir / source.name)
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
    def __init__(self, root, out, stage):
        self.root, self.out, self.stage = root, out, stage
        self.python = root / ".venv" / "bin" / "python"
        self.env = {**os.environ, "LOG_REJECTED": "1", "MODEL_BACKEND": "llama", "MODEL_PROFILE": "8b-q4",
                    "PYTHONIOENCODING": "utf-8"}
        self.env.pop("MODEL_ID", None)
        self.env["LORA_ADAPTER"] = ""
        self.runs, self.archive = {}, None
        self.started = time.monotonic()

    def manifest(self, profile):
        return json.loads((self.root / "models" / MODEL_PROFILES[profile]).read_text(encoding="utf-8"))

    def ports(self, profiles):
        return [int(self.env.get("AI_PORT", "8000")), *(self.manifest(p)["runtime"]["port"] for p in profiles)]

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
        (self.out / f"{arm}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        self.run([self.python, "scripts/download_model.py"], f"{arm}-download", env)
        start_log = self.out / "logs" / f"{arm}-start.log"
        try:
            command = ["bash", "scripts/run.sh", "start"] + (["--lora", str(adapter)] if adapter else [])
            self.run(command, f"{arm}-start", env)
            yield env
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
        with self.server("14b-q4", "teacher") as env:
            self.run([self.python, "train/distill.py", "generate", "--pool", "train/sources/pool.jsonl",
                      "--out", self.out, "--url", f"http://127.0.0.1:{env.get('AI_PORT', '8000')}"], "generate", env)
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
                  "--max-length", str(config["max_length"])], "prepare")
        self.run([train_python, "train/train_qlora.py", "--config", snapshot, "--data-dir", prepared,
                  "--output-dir", self.out / "training"], "train")
        adapter = self.out / "adapter.gguf"
        self.run([self.python, "train/convert_adapter.py", "--adapter", self.out / "training" / "adapter",
                  "--out", adapter, "--profile", "8b-q4"], "convert", {**self.env, "MODEL_PROFILE": "8b-q4"})
        self.evaluate("8b-q4", "baseline")
        self.evaluate("8b-q4", "candidate", adapter)
        self.compare(adapter)
        self.run([self.python, "train/check_adoption.py", "--stage-dir", self.out], "adoption-pending")

    def summary(self, error=None):
        lines = [f"# LoRA {self.stage}", "", f"- artifacts: {self.out}",
                 f"- status: {'failed' if error else 'AI 검토 대기 (참고)'}",
                 f"- elapsed seconds: {time.monotonic() - self.started:.1f}"]
        if error:
            lines.append(f"- error: {error}")
        if self.archive:
            lines.append(f"- blind review archive (no keys): {self.archive}")
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
        print("\n".join(lines), flush=True)
        print(f"summary: {path}", flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("teacher-check", "generate", "train-eval"))
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
    pipeline = Pipeline(ROOT, out, args.stage)
    created = False
    try:
        if out.exists():
            raise ValueError(f"Output already exists; prior evidence was preserved: {out}")
        if not pipeline.python.is_file():
            raise ValueError("Run bash scripts/run.sh setup first.")
        profiles = ("8b-q4", "14b-q4") if args.stage == "teacher-check" else ("14b-q4",) if args.stage == "generate" else ("8b-q4",)
        ensure_idle(ROOT, pipeline.ports(profiles))
        out.mkdir(parents=True)
        created = True
        if (ROOT / "logs").is_dir():
            shutil.copytree(ROOT / "logs", out / "logs-before")
        (out / "stage.json").write_text(json.dumps({"stage": args.stage, "created_utc": stamp, "profiles": profiles,
                                                    "gpu_layers_override": os.environ.get("GPU_LAYERS"),
                                                    "data": str(args.data.resolve()) if args.data else None}, indent=2) + "\n", encoding="utf-8")
        if args.stage == "teacher-check":
            pipeline.evaluate("8b-q4", "baseline")
            pipeline.evaluate("14b-q4", "candidate")
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
