import argparse
import hashlib
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_manifest  # noqa: E402


# 파일 검증
def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


# 모델 다운로드
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
