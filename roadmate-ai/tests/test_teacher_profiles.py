import re

import pytest

from config import ROOT, load_manifest


@pytest.mark.parametrize("size, file_size", [(8, 5027783488), (14, 9001752960), (32, 19762149024)])
def test_official_teacher_profiles_are_pinned_and_non_thinking(size, file_size):
    manifest = load_manifest(ROOT / "models" / f"manifest.qwen3-{size}b.json")
    model = manifest["model"]
    assert model["base_repo"] == f"Qwen/Qwen3-{size}B"
    assert model["gguf_repo"] == f"Qwen/Qwen3-{size}B-GGUF"
    assert model["gguf_file"] == f"Qwen3-{size}B-Q4_K_M.gguf"
    assert model["gguf_size"] == file_size
    assert model["license"] == "apache-2.0"
    assert all(re.fullmatch(r"[0-9a-f]{40}", model[k]) for k in ("base_revision", "gguf_revision"))
    assert re.fullmatch(r"[0-9a-f]{64}", model["gguf_sha256"])
    assert manifest["runtime"]["chat_template_kwargs"] == {"enable_thinking": False}
    assert manifest["versions"] == load_manifest(ROOT / "models" / "manifest.qwen3-8b.json")["versions"]
