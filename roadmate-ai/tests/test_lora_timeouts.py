# 테스트
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from inference import LlamaClient


@pytest.mark.parametrize("budget", [8, 60, 120])
def test_model_request_transport_uses_its_remaining_deadline(budget):
    observed = []
    def handler(request):
        observed.append(request.extensions["timeout"])
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
    async def run():
        client = LlamaClient("http://127.0.0.1:8081/v1", "synthetic")
        await client.http.aclose()
        client.http = httpx.AsyncClient(timeout=30, transport=httpx.MockTransport(handler))
        try:
            assert await client.chat_json([], {}, 16, {}, budget) == {}
        finally:
            await client.aclose()
    asyncio.run(run())
    assert observed == [{"connect": 2.0, "read": budget, "write": budget, "pool": budget}]


def test_evaluation_transport_allows_teacher_budget_from_ready(tmp_path, monkeypatch):
    from eval import evaluate
    case = {"id": "synthetic", "task": "translate", "source_locale": "en-US", "target_locale": "ko-KR",
            "scenario": "general", "text": "Synthetic source."}
    corpus = tmp_path / "dev.jsonl"
    corpus.write_text(json.dumps(case), encoding="utf-8")
    ready = {"ready": True, "backend": "llama", "model_id": "synthetic", "artifact_hashes": {},
             "model_profile": "14b-q4", "runtime_config": {"model_profile": "14b-q4", "model_timeout_s": 60}}
    observed = []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=ready)
        observed.append(request.extensions["timeout"])
        return httpx.Response(200, json={"status": "ok", "translated_text": "합성 시험이에요.", "latency_ms": 1})
    real_client = httpx.Client
    monkeypatch.setattr(evaluate.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(evaluate, "ROOT", tmp_path)
    monkeypatch.setattr(evaluate, "model_profile", lambda: "14b-q4")
    monkeypatch.setattr(evaluate, "load_manifest", lambda: {"engine_bundle": "synthetic"})
    monkeypatch.setattr(evaluate, "artifact_hashes", lambda: {})
    monkeypatch.setattr(evaluate.sys.stdout, "reconfigure", lambda **kwargs: None, raising=False)
    monkeypatch.setattr(evaluate.sys, "argv", ["evaluate.py", "--file", str(corpus)])
    evaluate.main()
    assert observed[0]["read"] == 130
    assert observed[0]["connect"] == 2


@pytest.mark.parametrize("budget,expected", [(8, 60), (60, 130)])
def test_smoke_transport_keeps_normal_default_and_allows_teacher_budget(monkeypatch, budget, expected):
    from scripts import smoke
    ready = {"ready": True, "backend": "mock", "model_profile": "8b-q4", "runtime_config": {"model_timeout_s": budget}}
    observed = []
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=ready)
        observed.append(request.extensions["timeout"])
        return httpx.Response(200, json={"status": "ok"})
    real_client = httpx.Client
    monkeypatch.setattr(smoke.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(smoke.Settings, "from_env", lambda: SimpleNamespace(model_profile="8b-q4", model_timeout_s=budget))
    monkeypatch.setattr(smoke, "M", {"engine_bundle": "synthetic", "runtime": {}})
    monkeypatch.setattr(smoke.sys.stdout, "reconfigure", lambda **kwargs: None, raising=False)
    assert smoke.main() == 0
    assert len(observed) == 6
    assert all(timeout["read"] == expected and timeout["connect"] == 2 for timeout in observed)
