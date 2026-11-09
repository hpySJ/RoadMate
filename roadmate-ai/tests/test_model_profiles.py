# 테스트
import asyncio
import copy
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from config import MODEL_PROFILES, Settings, artifact_hashes, load_manifest, manifest_path, model_profile
from inference import LlamaClient, ModelBadOutput

ROOT = Path(__file__).resolve().parents[1]
PROFILE_8B = ROOT / "models" / "manifest.qwen3-8b.json"
PROFILE_Q8 = ROOT / "models" / "manifest.qwen3-4b-q8.json"


def test_optional_8b_profile_keeps_common_runtime_and_artifact_versions():
    active, candidate = load_manifest(ROOT / "models" / "manifest.json"), load_manifest(PROFILE_8B)
    assert active["model"]["alias"] == "roadmate-qwen4b-q4"
    assert candidate["engine_bundle"] == "roadmate-ko-en-q4-8b-v1" != active["engine_bundle"]
    assert candidate["versions"] == active["versions"]
    assert candidate["adapter"] is active["adapter"] is None
    runtime = dict(candidate["runtime"])
    assert runtime.pop("chat_template_kwargs") == {"enable_thinking": False}
    assert runtime == active["runtime"]
    assert candidate["model"] == {
        "alias": "roadmate-qwen8b-q4",
        "base_repo": "Qwen/Qwen3-8B",
        "base_revision": "b968826d9c46dd6066d109eabc6255188de91218",
        "gguf_repo": "Qwen/Qwen3-8B-GGUF",
        "gguf_revision": "7c41481f57cb95916b40956ab2f0b139b296d974",
        "gguf_file": "Qwen3-8B-Q4_K_M.gguf",
        "gguf_sha256": "d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785",
        "gguf_size": 5027783488,
        "license": "apache-2.0",
    }
    for key in ("base_repo", "base_revision", "gguf_repo", "gguf_revision", "gguf_file", "gguf_sha256"):
        assert candidate["model"][key] != active["model"][key]


def test_q8_profile_changes_only_quantization_and_bundle():
    baseline = load_manifest(ROOT / "models" / "manifest.json")
    candidate = load_manifest(PROFILE_Q8)
    expected = copy.deepcopy(baseline)
    expected["engine_bundle"] = "roadmate-ko-en-q8-v1"
    expected["adapter"] = None
    expected["model"].update(alias="roadmate-qwen4b-q8", gguf_file="Qwen3-4B-Instruct-2507-Q8_0.gguf",
                             gguf_sha256="391c1e410fd9f4cf2de2b510273b56a84c19ce18f4fa3bfb3774031dac4ef068",
                             gguf_size=4280405600)
    assert candidate == expected


@pytest.mark.parametrize("name", MODEL_PROFILES)
def test_profile_selection_matches_settings_and_artifact_hashes(name, monkeypatch):
    monkeypatch.setenv("MODEL_PROFILE", name)
    monkeypatch.delenv("MODEL_ID", raising=False)
    selected = ROOT / "models" / MODEL_PROFILES[name]
    assert model_profile() == name and manifest_path() == selected
    assert load_manifest() == load_manifest(selected)
    settings = Settings.from_env()
    assert settings.model_profile == name
    assert settings.model_id == settings.manifest["model"]["alias"]
    assert settings.engine_bundle == settings.manifest["engine_bundle"]
    manifest_hashes = [path for path in artifact_hashes() if path.startswith("models/manifest")]
    assert manifest_hashes == [selected.relative_to(ROOT).as_posix()]


def test_profile_defaults_to_baseline_and_rejects_unknown_values(monkeypatch):
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    monkeypatch.delenv("MODEL_PROFILE", raising=False)
    assert model_profile() == "8b-q4"
    for name in ("8b", "", "../manifest.json"):
        monkeypatch.setenv("MODEL_PROFILE", name)
        with pytest.raises(ValueError, match="unknown MODEL_PROFILE"):
            load_manifest()


def test_profile_selection_loads_dotenv_without_overriding_shell(monkeypatch):
    monkeypatch.delenv("MODEL_PROFILE", raising=False)
    monkeypatch.setattr("config.load_dotenv", lambda: os.environ.setdefault("MODEL_PROFILE", "4b-q8"))
    assert model_profile() == "4b-q8"
    monkeypatch.setenv("MODEL_PROFILE", "8b-q4")
    assert model_profile() == "8b-q4"


def test_adapter_converter_has_no_base_manifest_mutation_options(monkeypatch):
    from train import convert_adapter
    manifest = ROOT / "models" / "manifest.qwen3-8b.json"
    before = manifest.read_bytes()
    monkeypatch.setattr(sys, "argv", ["convert_adapter.py", "--remove"])
    with pytest.raises(SystemExit):
        convert_adapter.main()
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("profile", [None, ROOT / "models" / "manifest.json", PROFILE_Q8, PROFILE_8B])
def test_chat_request_includes_only_configured_template_kwargs(profile, monkeypatch):
    manifest = load_manifest(profile) if profile else None

    async def verified(self):
        return {"verified": True}

    monkeypatch.setattr(LlamaClient, "runtime_identity", verified)

    async def run():
        client = LlamaClient("http://127.0.0.1:8081/v1", "test-model", manifest)
        await client.http.aclose()
        requests = []

        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{
                "finish_reason": "stop", "message": {"content": '{"status":"ok"}'}}]})

        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            assert await client.chat_json([{"role": "user", "content": "hello"}], {"type": "object"},
                                          16, {"temperature": 0.2}, 5) == {"status": "ok"}
        finally:
            await client.aclose()
        assert len(requests) == 1
        payload = requests[0]
        if profile == PROFILE_8B:
            assert payload.pop("chat_template_kwargs") == {"enable_thinking": False}
        assert payload == {
            "model": "test-model", "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 16, "stream": False, "temperature": 0.2,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "output", "schema": {"type": "object"}, "strict": True}},
        }

    asyncio.run(run())


@pytest.mark.parametrize("message,raises", [
    ({"content": '{"status":"ok"}', "reasoning_content": "hidden reasoning"}, True),
    ({"content": '{"status":"ok"}', "reasoning": "hidden reasoning"}, True),
    ({"content": '<think>reasoning</think>{"status":"ok"}'}, True),
    ({"content": '{"status":"ok","translated_text":"<think>"}'}, True),
    ({"content": '{"status":"ok"}', "reasoning_content": ""}, False),
    ({"content": '{"status":"ok"}', "reasoning_content": None}, False),
])
def test_non_thinking_profile_rejects_reasoning_without_stripping(message, raises, monkeypatch):
    async def verified(self):
        return {"verified": True}
    monkeypatch.setattr(LlamaClient, "runtime_identity", verified)

    async def run():
        client = LlamaClient("http://127.0.0.1:8081/v1", "roadmate-qwen8b-q4", load_manifest(PROFILE_8B))
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": message}]})))
        try:
            if raises:
                with pytest.raises(ModelBadOutput, match="thinking output"):
                    await client.chat_json([], {}, 16, {}, 5)
            else:
                assert await client.chat_json([], {}, 16, {}, 5) == {"status": "ok"}
        finally:
            await client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("rejected", [False, True])
def test_8b_smoke_requires_non_thinking_chat(rejected, monkeypatch):
    from scripts import smoke
    monkeypatch.setenv("MODEL_PROFILE", "8b-q4")
    monkeypatch.setattr(smoke, "M", load_manifest(PROFILE_8B))
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kwargs: None, raising=False)
    calls = []

    async def chat():
        calls.append("chat")
        if rejected:
            raise ModelBadOutput("thinking output received with enable_thinking=false")
        return {"status": "ok", "translated_text": "안녕하세요."}

    monkeypatch.setattr(smoke, "non_thinking_chat", chat)
    def handler(request):
        return httpx.Response(200, json={"backend": "llama", "model_profile": "8b-q4"})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(smoke.httpx, "Client", lambda **kwargs: client)
        assert smoke.main() == int(rejected)
    assert calls == ["chat"]


@pytest.mark.parametrize("profile", [ROOT / "models" / "manifest.json", PROFILE_Q8, PROFILE_8B])
@pytest.mark.parametrize("exact_template", [False, True])
def test_budget_checker_uses_selected_profile_and_template(profile, exact_template, tmp_path, monkeypatch, capsys):
    pytest.importorskip("jinja2")
    tokenizers = pytest.importorskip("tokenizers")
    from scripts import check_prompt_budget as budget

    tokenizer_dir = tmp_path / "tokenizer"
    tokenizer_dir.mkdir()
    tok = tokenizers.Tokenizer(tokenizers.models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tok.save(str(tokenizer_dir / "tokenizer.json"))
    condition = "{% if enable_thinking is defined and not enable_thinking %}disabled{% else %}enabled default{% endif %}"
    (tokenizer_dir / "tokenizer_config.json").write_text(json.dumps({"chat_template": "HF " + condition}), encoding="utf-8")
    gguf_template = tmp_path / "gguf.jinja"
    gguf_template.write_text("GGUF extra " + condition, encoding="utf-8")
    (tmp_path / "eval").mkdir()
    for name in ("dev", "general", "general_es", "naturalness.dev"):
        (tmp_path / "eval" / f"{name}.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(budget, "ROOT", tmp_path)
    args = ["check_prompt_budget.py", "--tokenizer-dir", str(tokenizer_dir), "--manifest", str(profile)]
    if exact_template:
        args += ["--chat-template", str(gguf_template)]
    monkeypatch.setattr(sys, "argv", args)
    assert budget.main() == 0
    expected = load_manifest(profile)["runtime"].get("chat_template_kwargs", {})
    report = json.loads(capsys.readouterr().out)
    expected_tokens = 2 + int(exact_template) + int(profile != PROFILE_8B)
    assert report["largest"] and all(row["prompt"] == expected_tokens for row in report["largest"])
    assert report["manifest"] == str(profile)
    assert report["model"] == load_manifest(profile)["model"]["alias"]
    assert report["chat_template_kwargs"] == expected
    assert report["context"] == load_manifest(profile)["runtime"]["ctx"]
    assert report["chat_template"] == str(gguf_template if exact_template else tokenizer_dir / "tokenizer_config.json")
    assert json.loads((tmp_path / "run" / "prompt_budget.json").read_text(encoding="utf-8")) == report
