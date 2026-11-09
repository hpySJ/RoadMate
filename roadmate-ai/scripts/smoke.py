"""연기 시험: 준비 확인 → 한↔영·한↔스 번역 → 목적지 → 취소. 품질 평가는 eval에서 별도 실행."""
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_dotenv, load_manifest, Settings  # noqa: E402
from inference import LlamaClient, ModelError  # noqa: E402
import prompting as P  # noqa: E402

load_dotenv()
M = load_manifest()
AI = os.environ.get("AI_URL", "http://127.0.0.1:8000")
HEADERS = {"X-Internal-Token": os.environ["AI_INTERNAL_TOKEN"]} if os.environ.get("AI_INTERNAL_TOKEN") else {}


async def non_thinking_chat():
    settings = Settings.from_env()
    client = LlamaClient(settings.model_base_url, settings.model_id, settings.manifest)
    try:
        await client.prepare_runtime()
        return await client.chat_json(P.translate_messages("en-US", "ko-KR", "general", "Hello, thank you."),
                                      P.TRANSLATE_SCHEMA, P.TRANSLATE_MAX_TOKENS, P.TRANSLATE_SAMPLING,
                                      max(settings.model_timeout_s, 20))
    finally:
        await client.aclose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    sys.stdout.reconfigure(encoding="utf-8")
    c = httpx.Client(timeout=60, headers=HEADERS)
    ready = c.get(f"{AI}/health/ready", timeout=httpx.Timeout(300, connect=2))
    print("ready:", ready.status_code, ready.json())
    if ready.status_code != 200:
        return 1
    model_timeout = ready.json().get("runtime_config", {}).get("model_timeout_s", Settings.from_env().model_timeout_s)
    c.timeout = httpx.Timeout(max(60, 2 * float(model_timeout) + 10), connect=2)
    if ready.json().get("model_profile") != Settings.from_env().model_profile:
        print("SMOKE FAIL: server MODEL_PROFILE differs from local selection")
        return 1
    if M["runtime"].get("chat_template_kwargs", {}).get("enable_thinking") is False and ready.json().get("backend") != "mock":
        try:
            output = asyncio.run(non_thinking_chat())
            if output.get("status") != "ok" or not output.get("translated_text"):
                raise ModelError("non-thinking chat did not return a translation")
        except ModelError as e:
            print("SMOKE FAIL: non-thinking chat", e.code, str(e))
            return 1
        print("non-thinking chat: OK (no think tags or reasoning content)")
    sid, bad = f"smoke-{uuid.uuid4().hex[:8]}", 0
    base = {"session_id": sid, "session_epoch": 1, "engine_bundle": M["engine_bundle"]}
    cases = [
        ("/internal/v1/translate", {"source_locale": "en-US", "target_locale": "ko-KR", "scenario": "gas_station",
                                    "text": "Please put in 50,000 won of gasoline, not diesel."}),
        ("/internal/v1/translate", {"source_locale": "ko-KR", "target_locale": "en-US", "scenario": "accident",
                                    "text": "다친 사람은 없어요. 렌터카 회사에 연락해 주세요."}),
        ("/internal/v1/parse-destination", {"source_locale": "en-US", "text": "Take me to Seongsan Sunrise Peak, please."}),
        ("/internal/v1/translate", {"source_locale": "es-MX", "target_locale": "ko-KR", "scenario": "gas_station",
                                    "text": "Póngale 50,000 wones de gasolina, no de diésel, por favor."}),
        ("/internal/v1/translate", {"source_locale": "ko-KR", "target_locale": "es-US", "scenario": "parking",
                                    "text": "오후 6시 이후에는 주차비가 무료예요."}),
    ]
    for i, (path, extra) in enumerate(cases):
        t0 = time.perf_counter()
        r = c.post(f"{AI}{path}", json={**base, "request_id": f"{sid}-{i}", "turn_id": i, **extra})
        ms = int((time.perf_counter() - t0) * 1000)
        print(f"{path} {r.status_code} {ms}ms", json.dumps(r.json(), ensure_ascii=False))
        bad += r.status_code != 200
    r = c.post(f"{AI}/internal/v1/cancel", json={"session_id": sid, "session_epoch": 1})
    print("cancel:", r.status_code, r.json())
    bad += r.status_code != 200
    print("SMOKE", "FAIL" if bad else "OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
