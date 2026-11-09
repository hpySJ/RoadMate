# 어댑터
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import MODEL_PROFILES, load_manifest  # noqa: E402
from train.train_qlora import check_teacher_evidence  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def check_training(adapter: Path, manifest: dict) -> tuple[dict, Path]:
    cfg = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
    meta_path = adapter.parent / "run_meta.json"
    if not meta_path.exists():
        raise SystemExit(f"{meta_path} missing: use an adapter saved by train/train_qlora.py")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    trained = (meta["config"]["base_model"], meta["config"].get("base_revision"))
    base = manifest["model"]
    if trained != (base["base_repo"], base["base_revision"]) or cfg.get("base_model_name_or_path") != base["base_repo"]:
        raise SystemExit("adapter training base/revision differs from the serving base")
    if meta.get("smoke") or meta.get("dataset", {}).get("allow_draft", True):
        raise SystemExit("smoke/draft adapters cannot enter paired quality evaluation")
    check_teacher_evidence(meta["dataset"], meta["config"].get("teacher_profile", "14b-q4"))
    if meta.get("enable_thinking") is not False or meta["config"].get("enable_thinking") is not False:
        raise SystemExit("adapter training must use enable_thinking=False")
    if meta.get("adapter_sha256") != sha256(adapter / "adapter_model.safetensors"):
        raise SystemExit("PEFT adapter changed after training")
    data_dir = ROOT / meta["config"]["data_dir"]
    dataset = data_dir / "dataset_manifest.json"
    if meta.get("dataset_sha256") != sha256(dataset):
        raise SystemExit("dataset manifest changed after training")
    if json.loads(dataset.read_text(encoding="utf-8")) != meta["dataset"]:
        raise SystemExit("training dataset provenance mismatch")
    for name, expected in meta["dataset"]["outputs"].items():
        if sha256(data_dir / name) != expected:
            raise SystemExit(f"prepared dataset changed after training: {name}")
    return meta, meta_path


def main():
    ap = argparse.ArgumentParser(description='검증된 Qwen3-8B 어댑터를 GGUF로 변환합니다.')
    ap.add_argument("--adapter", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--profile", default="8b-q4", choices=["8b-q4"])
    a = ap.parse_args()
    manifest = load_manifest(ROOT / "models" / MODEL_PROFILES[a.profile])
    adapter, out = a.adapter.resolve(), a.out.resolve()
    sidecar = Path(str(out) + ".json")
    if out.exists() or sidecar.exists():
        raise SystemExit("conversion output already exists; preserve the previous artifact")
    meta, meta_path = check_training(adapter, manifest)
    conv_py = ROOT / ".venv-convert" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    script = ROOT / "tools" / "llama-src" / "convert_lora_to_gguf.py"
    if not conv_py.exists() or not script.exists():
        raise SystemExit("conversion tools missing: bash scripts/run.sh setup-convert")
    from huggingface_hub import snapshot_download
    base_dir = Path(snapshot_download(
        manifest["model"]["base_repo"], revision=manifest["model"]["base_revision"],
        local_dir=ROOT / "train" / "base-config" / a.profile,
        allow_patterns=["config.json", "generation_config.json", "tokenizer*", "vocab.json", "merges.txt"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(conv_py), str(script), str(adapter), "--base", str(base_dir),
                    "--outfile", str(out), "--outtype", "f16"], check=True)
    record = {"format_version": 1, "file": out.name, "sha256": sha256(out),
              "base": {k: manifest["model"][k] for k in ("base_repo", "base_revision", "gguf_sha256")},
              "dataset_sha256": meta["dataset_sha256"], "training_meta_sha256": sha256(meta_path),
              "chat_template_sha256": meta["chat_template_sha256"], "enable_thinking": False,
              "smoke": False, "allow_draft": False, "adoption_status": "pending",
              "target_source": meta["dataset"]["target_source"], "review_status": meta["dataset"]["review_status"],
              "teacher_profile": meta["dataset"].get("teacher_profile", "14b-q4"),
              "teacher_model": meta["dataset"]["teacher_model"],
              "ai_review_sha256": meta["dataset"]["ai_review_sha256"],
              "source_adapter_sha256": meta["adapter_sha256"], "llama_cpp_tag": manifest["runtime"]["llama_cpp_tag"]}
    sidecar.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"adapter": str(out), "sidecar": str(sidecar), "sha256": record["sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
