"""학습: PEFT LoRA → GGUF LoRA(f16) 변환 후 manifest에 등록/해제.

변환·등록: .venv\\Scripts\\python.exe train\\convert_adapter.py --adapter train\\runs\\lora-v1\\adapter --apply
되돌리기:  .venv\\Scripts\\python.exe train\\convert_adapter.py --remove
등록 후 scripts\\stop_all.ps1 → scripts\\start_all.ps1 로 재시작하고 eval\\evaluate.py로 재평가한다.
"""
import argparse
import hashlib
import json
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
    a = ap.parse_args()
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if a.remove:
        m["adapter"], m["engine_bundle"] = None, BASE_BUNDLE
        save(m)
        print(f"adapter removed, bundle={BASE_BUNDLE}")
        return

    adapter = Path(a.adapter)
    cfg = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
    # 검증: 학습 원본과 배포 본체의 upstream 모델이 같은지 확인
    if cfg.get("base_model_name_or_path") not in (m["model"]["base_repo"], None):
        sys.exit(f"adapter base {cfg.get('base_model_name_or_path')} != manifest base {m['model']['base_repo']}")
    base_dir = Path(snapshot_download(
        m["model"]["base_repo"], revision=m["model"]["base_revision"], local_dir=ROOT / "train" / "base-config",
        allow_patterns=["config.json", "generation_config.json", "tokenizer*", "vocab.json", "merges.txt"]))
    conv_py = ROOT / ".venv-convert" / "Scripts" / "python.exe"
    script = ROOT / "tools" / "llama-src" / "convert_lora_to_gguf.py"
    for p, hint in ((conv_py, "scripts\\setup_convert_env.ps1"), (script, "scripts\\setup_llama.ps1")):
        if not p.exists():
            sys.exit(f"{p} missing: run {hint}")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # QWEN 구현: 어댑터는 f16 GGUF로 변환, 본체는 Q4_K_M 유지
    subprocess.run([str(conv_py), str(script), str(adapter), "--base", str(base_dir),
                    "--outfile", str(out), "--outtype", "f16"], check=True)
    digest = sha256(out)
    print(f"{out.name} sha256={digest}")
    if a.apply:
        m["adapter"] = {"file": out.relative_to(ROOT).as_posix(), "sha256": digest,
                        "source": adapter.relative_to(ROOT).as_posix() if adapter.is_relative_to(ROOT) else str(adapter),
                        "base_revision": m["model"]["base_revision"]}
        m["engine_bundle"] = a.bundle
        save(m)
        print(f"manifest updated: bundle={a.bundle}")


if __name__ == "__main__":
    main()
