"""QWEN 구현: manifest에 고정한 revision의 GGUF 1개를 내려받고 SHA256을 확인한다.

기본 프로필: 8b-q4. MODEL_PROFILE로 4b-q4/4b-q8 대안을 선택한다.
사용: .venv\\Scripts\\python.exe scripts\\download_model.py
Q5 비교: ... --file Qwen3-4B-Instruct-2507-Q5_K_M.gguf --sha256 5bde5e9d883622acb02bf77fe7dcbc56a8b9a9ad4be78a72ca23a532658b4ecb
"""
import argparse
import hashlib
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_manifest  # noqa: E402


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    m = load_manifest()["model"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=m["gguf_file"])
    ap.add_argument("--sha256", default=None)
    a = ap.parse_args()
    expected = a.sha256 or (m["gguf_sha256"] if a.file == m["gguf_file"] else None)
    path = Path(hf_hub_download(repo_id=m["gguf_repo"], filename=a.file, revision=m["gguf_revision"],
                                local_dir=ROOT / "models"))
    got = sha256(path)
    print(f"{path.name} sha256={got}")
    if expected and got != expected.lower():
        sys.exit(f"SHA256 mismatch: expected {expected}")
    print("OK" if expected else "WARNING: no expected sha256 to compare")


if __name__ == "__main__":
    main()
