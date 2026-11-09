import asyncio
import json
import time
from urllib.parse import urlsplit

import httpx
from runtime_identity import ROOT, prepare_runtime_files, process_identity, verify_runtime


# 오류
class ModelError(Exception):
    code = "model_error"
    status = 502


class ModelTimeout(ModelError):
    code = "model_timeout"
    status = 504


class ModelUnavailable(ModelError):
    code = "model_not_ready"
    status = 503


class ModelTruncated(ModelError):
    code = "output_truncated"


class ModelBadOutput(ModelError):
    code = "invalid_model_output"


# 모델
class LlamaClient:
    is_mock = False

    def __init__(self, base_url: str, model_id: str, manifest: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.root_url = self.base_url.removesuffix("/v1")
        self.model_id = model_id
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=2.0))
        self.manifest, self._identity = manifest, None

    async def prepare_runtime(self) -> None:

        if self.manifest:
            try:
                await asyncio.to_thread(prepare_runtime_files, self.manifest)
            except (OSError, ValueError, KeyError, TypeError) as e:
                raise ModelUnavailable(f"model runtime preparation failed: {e}") from e

    async def runtime_identity(self) -> dict:
        if not self.manifest or urlsplit(self.root_url).hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ModelUnavailable("verified local model launch required")
        try:
            props = await self.http.get(f"{self.root_url}/props", timeout=2.0)
            props.raise_for_status()
            current = await asyncio.to_thread(verify_runtime, props.json(), await self.lora_adapters(), self.manifest,
                                              process_identity(ROOT / "run" / "llama.pid"), allow_hash=False)
        except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError) as e:
            raise ModelUnavailable(f"model runtime verification failed: {e}") from e
        if self._identity is not None and current != self._identity:
            raise ModelUnavailable("model runtime changed; restart AI server before using/evaluating it")
        self._identity = current
        return current

    async def chat_json(self, messages: list[dict], schema: dict, max_tokens: int, sampling: dict, timeout_s: float) -> dict:
        started = time.monotonic()
        deadline = started + timeout_s
        if self.manifest:
            try:
                await asyncio.wait_for(self.runtime_identity(), max(0.0, timeout_s))
            except asyncio.TimeoutError as e:
                raise ModelTimeout("runtime verification exceeded deadline") from e
            timeout_s -= time.monotonic() - started

        body = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
            "response_format": {"type": "json_schema", "json_schema": {"name": "output", "schema": schema, "strict": True}},
            **sampling,
        }
        template_kwargs = (self.manifest or {}).get("runtime", {}).get("chat_template_kwargs")
        if template_kwargs is not None:
            body["chat_template_kwargs"] = template_kwargs
        if timeout_s <= 0:
            raise ModelTimeout("no time left")
        try:
            r = await asyncio.wait_for(self.http.post(f"{self.base_url}/chat/completions", json=body,
                timeout=httpx.Timeout(timeout_s, connect=min(2.0, timeout_s))), timeout_s)
        except asyncio.TimeoutError as e:
            raise ModelTimeout("model call exceeded deadline") from e
        except httpx.TransportError as e:
            raise ModelUnavailable(f"llama-server unreachable: {e!r}") from e
        if r.status_code == 503:
            raise ModelUnavailable("llama-server loading")
        if r.status_code != 200:
            raise ModelError(f"llama-server http {r.status_code}: {r.text[:200]}")
        choice = r.json()["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ModelTruncated("hit max_tokens")
        try:
            message = choice["message"]
            content = message["content"]
            if template_kwargs and template_kwargs.get("enable_thinking") is False:
                if message.get("reasoning_content") or message.get("reasoning") or (
                        isinstance(content, str) and any(tag in content.lower() for tag in ("<think>", "</think>"))):
                    raise ModelBadOutput("thinking output received with enable_thinking=false")
            out = json.loads(content)
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            raise ModelBadOutput("non-JSON output") from e
        if not isinstance(out, dict):
            raise ModelBadOutput("JSON is not an object")
        if self.manifest:
            try:
                await asyncio.wait_for(self.runtime_identity(), max(0.0, deadline - time.monotonic()))
            except asyncio.TimeoutError as e:
                raise ModelTimeout("runtime verification exceeded deadline") from e
        return out

    async def health(self) -> bool:
        try:
            return (await self.http.get(f"{self.root_url}/health", timeout=2.0)).status_code == 200
        except httpx.HTTPError:
            return False

    async def model_ids(self) -> list[str]:
        r = await self.http.get(f"{self.base_url}/models", timeout=2.0)
        r.raise_for_status()
        d = r.json()
        ids = [m.get("id") for m in d.get("data", [])]
        ids += [a for m in d.get("models", []) for a in m.get("aliases", [])]
        return ids

    async def lora_adapters(self) -> list[dict]:
        r = await self.http.get(f"{self.root_url}/lora-adapters", timeout=2.0)
        r.raise_for_status()
        return r.json()

    async def wait_idle(self, max_s: float) -> bool:

        deadline = time.monotonic() + max_s
        while time.monotonic() < deadline:
            try:
                r = await self.http.get(f"{self.root_url}/slots", timeout=1.0)
                if r.status_code == 200 and not any(s.get("is_processing") for s in r.json()):
                    return True
            except (httpx.HTTPError, ValueError):
                pass
            await asyncio.sleep(0.1)
        return False

    async def aclose(self):
        await self.http.aclose()


# 모의 모델
class MockClient:


    is_mock = True
    model_id = "mock"

    def __init__(self, delay_s: float = 0.0, outputs: list | None = None):
        self.delay_s = delay_s
        self.outputs = list(outputs or [])
        self.calls = 0

    async def chat_json(self, messages, schema, max_tokens, sampling, timeout_s):
        self.calls += 1
        if self.delay_s:
            if self.delay_s > timeout_s:
                await asyncio.sleep(timeout_s)
                raise ModelTimeout("mock timeout")
            await asyncio.sleep(self.delay_s)
        if self.outputs:
            out = self.outputs.pop(0)
            if isinstance(out, Exception):
                raise out
            return out
        req = json.loads(messages[-1]["content"])
        if "target_locale" in req:
            prefix = "[모의 번역] " if req["target_locale"] == "ko-KR" else "[MOCK] "
            return {"status": "ok", "translated_text": prefix + req["utterance"], "clarification_question": None}
        return {"intent": "set_destination", "query_original": req["utterance"], "query_ko_candidate": None,
                "region_hint": None, "category_hint": None, "needs_confirmation": True}

    async def health(self):
        return True

    async def model_ids(self):
        return [self.model_id]

    async def runtime_identity(self):
        return None

    async def lora_adapters(self):
        return []

    async def wait_idle(self, max_s):
        return True

    async def aclose(self):
        pass
