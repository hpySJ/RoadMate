import asyncio
import copy
import hashlib
import json
import os
import threading
import subprocess
import sys

import httpx
import pytest

from inference import LlamaClient, ModelUnavailable
import runtime_identity as R
from runtime_identity import process_identity, verify_runtime


def fixture(tmp_path):
    model = tmp_path / "models" / "model.gguf"
    model.parent.mkdir()
    model.write_bytes(b"synthetic model")
    manifest = {"model": {"gguf_file": model.name, "gguf_sha256": hashlib.sha256(model.read_bytes()).hexdigest()},
                "runtime": {"ctx": 4096, "parallel": 1, "llama_cpp_tag": "b12076"}, "adapter": None}
    props = {"model_path": str(model), "default_generation_settings": {"n_ctx": 4096, "params": {"seed": 1}},
             "total_slots": 1, "build_info": "b12076"}
    return model, manifest, props


def test_pid_file_cannot_name_an_unrelated_running_process(tmp_path):
    pid_file = tmp_path / "llama.pid"
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    with pytest.raises(ValueError, match="not llama-server"):
        process_identity(pid_file)


def test_loaded_model_file_context_and_adapter_must_match(tmp_path):
    model, manifest, props = fixture(tmp_path)
    process = {"pid": 1, "started": "now"}
    assert verify_runtime(props, [], manifest, process, tmp_path)["model_sha256"] == manifest["model"]["gguf_sha256"]
    bad = copy.deepcopy(props)
    bad["default_generation_settings"]["n_ctx"] = 2048
    with pytest.raises(ValueError, match="context"):
        verify_runtime(bad, [], manifest, process, tmp_path)
    with pytest.raises(ValueError, match="build"):
        verify_runtime({**props, "build_info": "b12077"}, [], manifest, process, tmp_path)
    with pytest.raises(ValueError, match="unexpected loaded adapters"):
        verify_runtime(props, [{"path": "unconfigured", "scale": 1}], manifest, process, tmp_path)
    model.write_bytes(b"different model with same alias")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        verify_runtime(props, [], manifest, process, tmp_path)


def test_runtime_identity_records_template_and_request_kwargs(tmp_path):
    _, manifest, props = fixture(tmp_path)
    manifest["runtime"]["chat_template_kwargs"] = {"enable_thinking": False}
    props["chat_template"] = "an exact GGUF template"
    identity = verify_runtime(props, [], manifest, {"pid": 1, "started": "now"}, tmp_path)
    assert identity["chat_template_kwargs"] == {"enable_thinking": False}
    assert identity["chat_template_sha256"] == hashlib.sha256(props["chat_template"].encode()).hexdigest()
    props["chat_template"] = "a changed template"
    assert verify_runtime(props, [], manifest, {"pid": 1, "started": "now"}, tmp_path) != identity


def test_llama_restart_is_rejected_even_with_same_alias(tmp_path, monkeypatch):
    _, manifest, props = fixture(tmp_path)
    process = {"pid": 123, "started": "first"}
    monkeypatch.setattr("inference.process_identity", lambda path: dict(process))
    real_verify = verify_runtime
    R.prepare_runtime_files(manifest, tmp_path)
    monkeypatch.setattr("inference.verify_runtime", lambda p, a, m, proc, **kw: real_verify(p, a, m, proc, tmp_path, **kw))

    async def run():
        client = LlamaClient("http://127.0.0.1:8081/v1", "same-alias", manifest)
        await client.http.aclose()
        def handler(req):
            return httpx.Response(200, json=props if req.url.path == "/props" else [])
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        first = await client.runtime_identity()
        assert first["process"]["started"] == "first"
        process["started"] = "restarted-with-same-pid"
        with pytest.raises(ModelUnavailable, match="runtime changed"):
            await client.runtime_identity()
        await client.aclose()
    asyncio.run(run())


def test_hash_verification_does_not_block_the_event_loop(tmp_path, monkeypatch):
    _, manifest, props = fixture(tmp_path)
    monkeypatch.setattr("inference.process_identity", lambda path: {"pid": 123, "started": "test"})
    release = threading.Event()

    async def run():
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        def slow_verification(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(3):
                raise ValueError("event loop could not release hash worker")
            return {"verified": True}
        monkeypatch.setattr("inference.verify_runtime", slow_verification)
        client = LlamaClient("http://127.0.0.1:8081/v1", "alias", manifest)
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json=props if req.url.path == "/props" else [])))
        task = asyncio.create_task(client.runtime_identity())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            # 해시가 아직 대기 중이어도 다른 코루틴을 실행할 수 있어야 한다.
            assert not task.done()
            await asyncio.sleep(0)
            release.set()
            assert await task == {"verified": True}
        finally:
            release.set()
            await client.aclose()
    asyncio.run(run())


def test_hash_cache_is_shared_by_fresh_processes(tmp_path, caplog):
    model, manifest, _ = fixture(tmp_path)
    cache = tmp_path / "run/runtime-hashes"
    with caplog.at_level("INFO", logger="roadmate.runtime"):
        assert R.digest(model, cache) == manifest["model"]["gguf_sha256"]
        assert R.digest(model, cache, allow_hash=False) == manifest["model"]["gguf_sha256"]
    assert "hash cache miss" in caplog.text and "hash cache hit" in caplog.text and "elapsed_s=" in caplog.text
    script = ("from pathlib import Path; import runtime_identity as r; "
              "r._digest=lambda p: (_ for _ in ()).throw(AssertionError('must use disk cache')); "
              "print(r.digest(Path(__import__('sys').argv[1]), Path(__import__('sys').argv[2]), allow_hash=False))")
    result = subprocess.run([sys.executable, "-c", script, str(model), str(cache)], cwd=R.ROOT,
                            check=True, capture_output=True, text=True)
    assert result.stdout.strip() == manifest["model"]["gguf_sha256"]


@pytest.mark.parametrize("field", ["size", "mtime_ns", "ctime_ns", "inode", "path"])
def test_every_file_identity_field_invalidates_hash_cache(tmp_path, monkeypatch, field):
    model, _, _ = fixture(tmp_path)
    cache = tmp_path / "run/runtime-hashes"
    R.digest(model, cache)
    real_key = R._file_key
    def changed_key(path):
        key = real_key(path)
        key[field] = key[field] + (".other" if field == "path" else 1)
        return key
    monkeypatch.setattr(R, "_file_key", changed_key)
    calls, real_digest = [], R._digest
    monkeypatch.setattr(R, "_digest", lambda p: calls.append(p) or real_digest(p))
    with pytest.raises(ValueError, match="missing or stale"):
        R.digest(model, cache, allow_hash=False)
    assert not calls
    R.digest(model, cache)
    assert calls == [model.resolve()]
    R.digest(model, cache, allow_hash=False)
    assert len(calls) == 1


@pytest.mark.parametrize("contents", ["not json", "[]", '{"sha256":"invalid"}'])
def test_corrupt_hash_cache_never_proves_identity(tmp_path, contents):
    model, manifest, _ = fixture(tmp_path)
    cache = tmp_path / "run/runtime-hashes"
    R.digest(model, cache)
    path, = cache.glob("*.json")
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValueError, match="missing or stale"):
        R.digest(model, cache, allow_hash=False)
    assert R.digest(model, cache) == manifest["model"]["gguf_sha256"]


def test_file_changed_while_hashing_never_creates_cache(tmp_path, monkeypatch):
    model, _, _ = fixture(tmp_path)
    cache = tmp_path / "run/runtime-hashes"
    def changing_file(path):
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        path.write_bytes(b"changed while being hashed")
        return sha
    monkeypatch.setattr(R, "_digest", changing_file)
    with pytest.raises(ValueError, match="changed while computing"):
        R.digest(model, cache)
    assert not cache.exists()


def test_interrupted_cache_write_leaves_previous_record_intact(tmp_path, monkeypatch):
    model, _, _ = fixture(tmp_path)
    cache = tmp_path / "run/runtime-hashes"
    R.digest(model, cache)
    record, = cache.glob("*.json")
    previous = record.read_bytes()
    model.write_bytes(b"new model")
    monkeypatch.setattr(R.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("interrupted")))
    with pytest.raises(OSError, match="interrupted"):
        R.digest(model, cache)
    assert record.read_bytes() == previous
    assert list(cache.glob("*.tmp")) == []
    with pytest.raises(ValueError, match="missing or stale"):
        R.digest(model, cache, allow_hash=False)


def test_request_never_hashes_cold_or_changed_model(tmp_path, monkeypatch):
    model, manifest, props = fixture(tmp_path)
    monkeypatch.setattr("inference.process_identity", lambda path: {"pid": 123, "started": "test"})
    monkeypatch.setattr("inference.verify_runtime",
                        lambda p, a, m, proc, **kw: R.verify_runtime(p, a, m, proc, tmp_path, **kw))
    monkeypatch.setattr("inference.prepare_runtime_files", lambda m: R.prepare_runtime_files(m, tmp_path))
    posts = []
    def handler(request):
        if request.method == "POST":
            posts.append(request)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})
        return httpx.Response(200, json=props if request.url.path == "/props" else [])
    async def run():
        client = LlamaClient("http://127.0.0.1:8081/v1", "test", manifest)
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        real_digest = R._digest
        try:
            monkeypatch.setattr(R, "_digest", lambda p: (_ for _ in ()).throw(AssertionError("hash in request")))
            with pytest.raises(ModelUnavailable, match="missing or stale"):
                await client.chat_json([], {}, 16, {}, 1)
            assert not posts
            monkeypatch.setattr(R, "_digest", real_digest)
            await client.prepare_runtime()
            monkeypatch.setattr(R, "_digest", lambda p: (_ for _ in ()).throw(AssertionError("hash in request")))
            assert await client.chat_json([], {}, 16, {}, 1) == {}
            model.write_bytes(b"model replaced during service")
            with pytest.raises(ModelUnavailable, match="missing or stale"):
                await client.chat_json([], {}, 16, {}, 1)
            assert len(posts) == 1
        finally:
            await client.aclose()
    asyncio.run(run())
