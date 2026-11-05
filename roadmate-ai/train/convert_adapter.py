"""학습: PEFT LoRA → GGUF LoRA(f16) 변환 후 manifest에 등록/해제.

변환·등록: .venv\\Scripts\\python.exe train\\convert_adapter.py --adapter train\\runs\\lora-v1\\adapter --apply
되돌리기:  .venv\\Scripts\\python.exe train\\convert_adapter.py --remove
등록 후 scripts\\stop_all.ps1 → scripts\\start_all.ps1 로 재시작하고 eval\\evaluate.py로 재평가한다.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from huggingface_hub import snapshot_download

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "models" / "manifest.json"
BASE_BUNDLE = "roadmate-ko-en-q4-v1"


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def save(m: dict):
    MANIFEST.write_text(json.dumps(m, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adapter", default=str(ROOT / "train" / "runs" / "lora-v1" / "adapter"))
    ap.add_argument("--out", default=str(ROOT / "models" / "adapters" / "roadmate-lora-v1-f16.gguf"))
    ap.add_argument("--bundle", default="roadmate-ko-en-q4-lora-v1")
    ap.add_argument("--apply", action="store_true", help="manifest에 어댑터·새 bundle 등록")
    ap.add_argument("--remove", action="store_true", help="어댑터 해제, 기준 bundle로 복귀")
    ap.add_argument("--allow-smoke", action="store_true", help="스모크·초안 어댑터 등록 허용(변환·로딩 확인 전용)")
    a = ap.parse_args()
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if a.remove:
        m["adapter"], m["engine_bundle"] = None, BASE_BUNDLE
        save(m)
        print(f"adapter removed, bundle={BASE_BUNDLE}")
        return

    adapter, out = Path(a.adapter).resolve(), Path(a.out).resolve()
    if not out.is_relative_to(ROOT):
        sys.exit(f"--out must be inside {ROOT} (manifest stores a relative path)")
    cfg = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
    meta_path = adapter.parent / "run_meta.json"
    if not meta_path.exists():
        sys.exit(f"{meta_path} missing: use an adapter saved by train/train_qlora.py")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    trained = (meta["config"]["base_model"], meta["config"].get("base_revision"))
    # 검증: 학습한 원본(repo·revision)과 배포 본체가 정확히 같은지 확인
    if trained != (m["model"]["base_repo"], m["model"]["base_revision"]) or \
            cfg.get("base_model_name_or_path") not in (m["model"]["base_repo"], None):
        sys.exit(f"adapter trained on {trained} != manifest base {(m['model']['base_repo'], m['model']['base_revision'])}")
    draft = meta.get("smoke") or meta.get("dataset", {}).get("allow_draft", True)
    if a.apply and draft and not a.allow_smoke:
        sys.exit("smoke/draft adapter: add --allow-smoke only to test conversion and --lora loading")
    base_dir = Path(snapshot_download(
        m["model"]["base_repo"], revision=m["model"]["base_revision"], local_dir=ROOT / "train" / "base-config",
        allow_patterns=["config.json", "generation_config.json", "tokenizer*", "vocab.json", "merges.txt"]))
    conv_py = ROOT / ".venv-convert" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    script = ROOT / "tools" / "llama-src" / "convert_lora_to_gguf.py"
    for p, hint in ((conv_py, "scripts\\setup_convert_env.ps1 (Linux: bash scripts/run.sh setup-convert)"),
                    (script, "scripts\\setup_llama.ps1 (Linux: bash scripts/run.sh setup)")):
        if not p.exists():
            sys.exit(f"{p} missing: run {hint}")
    out.parent.mkdir(parents=True, exist_ok=True)
    # QWEN 구현: 어댑터는 f16 GGUF로 변환, 본체는 Q4_K_M 유지
    subprocess.run([str(conv_py), str(script), str(adapter), "--base", str(base_dir),
                    "--outfile", str(out), "--outtype", "f16"], check=True)
    digest = sha256(out)
    print(f"{out.name} sha256={digest}")
    if a.apply:
        m["adapter"] = {"file": out.relative_to(ROOT).as_posix(), "sha256": digest,
                        "source": adapter.relative_to(ROOT).as_posix() if adapter.is_relative_to(ROOT) else str(adapter),
                        "base_revision": trained[1]}
        m["engine_bundle"] = a.bundle
        save(m)
        print(f"manifest updated: bundle={a.bundle}")


if __name__ == "__main__":
    main()
