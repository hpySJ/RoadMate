import asyncio
import dataclasses
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app
from config import Settings
from engine import Engine, WRONG_LANG
from inference import MockClient


SETTINGS = dataclasses.replace(Settings.from_env(), model_backend="mock", model_timeout_s=30.0,
                               busy_wait_s=0.01, cancel_settle_s=0.01, max_text_chars=400,
                               internal_token="internal-only")
APP_BODY = {"text": "Please fill it with gasoline.", "from": "en-US", "to": "ko-KR", "situation": "rental"}


class FakeLlama(MockClient):
    is_mock = False
    model_id = SETTINGS.model_id


def internal_body():
    return {"request_id": "internal1", "session_id": "session1", "session_epoch": 0, "turn_id": 0,
            "source_locale": APP_BODY["from"], "target_locale": APP_BODY["to"], "scenario": "rental_counter",
            "text": APP_BODY["text"], "engine_bundle": SETTINGS.engine_bundle}


@pytest.fixture
def client():
    with TestClient(create_app(SETTINGS, MockClient())) as c:
        yield c


@pytest.mark.parametrize("source,target", [
    ("en-US", "ko-KR"), ("es-US", "ko-KR"), ("es-MX", "ko-KR"),
    ("ko-KR", "en-US"), ("ko-KR", "es-US"), ("ko-KR", "es-MX"),
])
@pytest.mark.parametrize("situation,scenario", [
    ("rental", "rental_counter"), ("rental_counter", "rental_counter"), ("gas_station", "gas_station"),
    ("parking", "parking"), ("accident", "accident"), ("general", "general"),
])
def test_app_mapping_uses_server_identity_and_shared_deadline(monkeypatch, source, target, situation, scenario):
    requests = []

    async def translate(_self, req, deadline):
        requests.append(req)
        assert time.monotonic() < deadline <= time.monotonic() + SETTINGS.model_timeout_s
        return {"status": "ok", "translated_text": "  mapped result  "}

    monkeypatch.setattr(Engine, "translate", translate)
    payload = {"text": "  Final   utterance.  ", "from": source, "to": target, "situation": situation}
    with TestClient(create_app(SETTINGS, MockClient())) as c:
        for _ in range(2):
            response = c.post("/translate", json=payload)
            assert response.status_code == 200 and response.json() == {"text": "mapped result"}
    for req in requests:
        assert (req.source_locale, req.target_locale, req.scenario) == (source, target, scenario)
        assert req.text == "Final utterance." and req.engine_bundle == SETTINGS.engine_bundle
        assert req.session_epoch == req.turn_id == 0
        assert req.request_id and req.session_id
    assert requests[0].request_id != requests[1].request_id
    assert requests[0].session_id != requests[1].session_id


def test_app_mock_and_internal_contract_remain_separate(client):
    response = client.post("/translate", json=APP_BODY)
    assert response.status_code == 200
    assert response.json() == {"text": "[모의 번역] " + APP_BODY["text"]}
    assert response.headers["content-type"].endswith("charset=utf-8")

    headers = {"X-Internal-Token": SETTINGS.internal_token}
    for path, payload in [
        ("translate", internal_body()),
        ("parse-destination", {k: v for k, v in internal_body().items() if k not in ("target_locale", "scenario")}),
        ("cancel", {"session_id": "session1", "session_epoch": 0}),
    ]:
        denied = client.post("/internal/v1/" + path, json=payload)
        assert denied.status_code == 401 and denied.json()["error_code"] == "unauthorized"
    result = client.post("/internal/v1/translate", json=internal_body(), headers=headers)
    assert result.status_code == 200
    assert set(result.json()) == {"request_id", "session_id", "session_epoch", "turn_id", "status",
                                  "translated_text", "clarification_question", "engine_bundle", "model_id",
                                  "prompt_version", "glossary_version", "latency_ms"}
    assert result.json()["request_id"] == "internal1" and result.json()["status"] == "ok"
    assert client.post("/internal/v1/translate", json=APP_BODY, headers=headers).status_code == 422


@pytest.mark.parametrize("source,text,target", [
    ("en-US", "안녕하세요", "ko-KR"), ("es-MX", "안녕하세요", "ko-KR"),
    ("ko-KR", "Hello there.", "es-US"),
])
def test_app_clarification_omits_text_and_keeps_source_locale(source, text, target):
    mock = MockClient()
    with TestClient(create_app(SETTINGS, mock)) as c:
        result = c.post("/translate", json={**APP_BODY, "text": text, "from": source, "to": target,
                                          "situation": "parking"})
    assert result.status_code == 200
    assert result.json() == {"clarification": WRONG_LANG[source], "clarification_locale": source}
    assert mock.calls == 0


@pytest.mark.parametrize("patch,code", [
    ({"from": "fr-CA"}, "invalid_input"), ({"to": "ja-JP"}, "invalid_input"),
    ({"from": "en"}, "invalid_input"), ({"to": "en-US"}, "invalid_input"),
    ({"to": "es-US"}, "invalid_input"), ({"situation": "unknown"}, "invalid_input"),
    ({"text": "  \n "}, "invalid_input"), ({"text": None}, "invalid_input"),
    ({"text": "a" * 401}, "text_too_long"), ({"text": "a" * 2001}, "invalid_input"),
    ({"source_locale": "en-US"}, "invalid_input"), ({"request_id": "supplied"}, "invalid_input"),
    ({"engine_bundle": "supplied"}, "invalid_input"),
])
def test_app_rejects_invalid_inputs_with_existing_error_codes(client, patch, code):
    result = client.post("/translate", json={**APP_BODY, **patch})
    assert result.status_code == 422 and result.json()["error_code"] == code


def test_app_validation_retry_still_blocks_wrong_fuel():
    fake = FakeLlama(outputs=[{"status": "ok", "translated_text": "경유를 넣어 주세요.",
                               "clarification_question": None}] * 2)
    with TestClient(create_app(SETTINGS, fake)) as c:
        result = c.post("/translate", json=APP_BODY)
    assert result.status_code == 502 and result.json()["error_code"] == "output_validation_failed"
    assert fake.calls == 2 and result.json()["request_id"]


def test_app_timeout_and_slot_release_match_internal_route():
    settings = dataclasses.replace(SETTINGS, model_timeout_s=0.02)
    with TestClient(create_app(settings, MockClient(delay_s=0.1))) as c:
        for _ in range(2):
            result = c.post("/translate", json=APP_BODY)
            assert result.status_code == 504 and result.json()["error_code"] == "model_timeout"


def test_app_and_internal_route_share_busy_slot():
    async def run():
        app = create_app(SETTINGS, MockClient(delay_s=0.1))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ai") as c:
                first = asyncio.create_task(c.post("/translate", json=APP_BODY))
                await asyncio.sleep(0.02)
                busy = await c.post("/internal/v1/translate", json=internal_body(),
                                    headers={"X-Internal-Token": SETTINGS.internal_token})
                assert busy.status_code == 429 and busy.json()["error_code"] == "busy"
                assert busy.headers["retry-after"] == "1"
                assert (await first).status_code == 200
    asyncio.run(run())
