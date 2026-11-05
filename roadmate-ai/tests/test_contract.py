import asyncio
import dataclasses
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import create_app
from config import Settings
from inference import MockClient, ModelBadOutput

ROOT = Path(__file__).resolve().parents[1]
BASE = Settings.from_env()
SETTINGS = dataclasses.replace(BASE, model_backend="mock", model_timeout_s=2.0, busy_wait_s=0.05, cancel_settle_s=0.1,
                               dedupe_ttl_s=30, max_text_chars=400, internal_token="")  # 개발 PC의 .env와 무관하게


class FakeLlama(MockClient):
    """의미 검증까지 실행되는 가짜 모델(출력을 직접 지정)."""

    is_mock = False
    model_id = "roadmate-qwen4b-q4"


def body(**kw):
    b = {"request_id": "r1", "session_id": "s1", "session_epoch": 1, "turn_id": 1, "source_locale": "en-US",
         "target_locale": "ko-KR", "scenario": "gas_station", "text": "Please fill it up with gasoline.",
         "engine_bundle": SETTINGS.engine_bundle}
    b.update(kw)
    return b


def ok(text):
    return {"status": "ok", "translated_text": text, "clarification_question": None}


@pytest.fixture
def client():
    with TestClient(create_app(SETTINGS, MockClient())) as c:
        yield c


def test_translate_ok_and_ready(client):
    r = client.post("/internal/v1/translate", json=body())
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "ok" and d["model_id"] == "mock" and d["translated_text"].startswith("[모의 번역]")
    assert d["prompt_version"] == "translate-v2" and r.headers["content-type"].endswith("charset=utf-8")
    assert client.get("/health/live").json() == {"status": "ok"}
    ready = client.get("/health/ready")
    assert ready.status_code == 200 and ready.json()["ready"] is True


def test_dedupe_and_conflict():
    mock = MockClient()
    with TestClient(create_app(SETTINGS, mock)) as c:
        a = c.post("/internal/v1/translate", json=body()).json()
        b = c.post("/internal/v1/translate", json=body()).json()
        assert a == b and mock.calls == 1
        r = c.post("/internal/v1/translate", json=body(text="Diesel, please."))
        assert r.status_code == 409 and r.json()["error_code"] == "request_id_conflict"


@pytest.mark.parametrize("patch,code", [
    ({"target_locale": "en-US"}, "invalid_input"),
    ({"source_locale": "ja-JP"}, "invalid_input"),
    ({"text": "   "}, "invalid_input"),
    ({"extra": 1}, "invalid_input"),
    ({"engine_bundle": "other"}, "bundle_mismatch"),
    ({"text": "a" * 401}, "text_too_long"),
])
def test_input_errors(client, patch, code):
    r = client.post("/internal/v1/translate", json=body(**patch))
    assert r.status_code == 422 and r.json()["error_code"] == code and r.json()["request_id"] == "r1"


def test_cancel_epoch_monotonic(client):
    assert client.post("/internal/v1/cancel", json={"session_id": "s1", "session_epoch": 3}).json()["cancelled_through_epoch"] == 3
    r = client.post("/internal/v1/translate", json=body(session_epoch=3))
    assert r.status_code == 409 and r.json()["error_code"] == "epoch_cancelled"
    assert client.post("/internal/v1/translate", json=body(request_id="r2", session_epoch=4)).status_code == 200
    # 늦게 도착한 epoch 2 취소가 epoch 4를 취소하지 않는다
    late = client.post("/internal/v1/cancel", json={"session_id": "s1", "session_epoch": 2}).json()
    assert late["cancelled_through_epoch"] == 3
    assert client.post("/internal/v1/translate", json=body(request_id="r3", session_epoch=4, turn_id=2)).status_code == 200


def test_auth_token():
    s = dataclasses.replace(SETTINGS, internal_token="secret")
    with TestClient(create_app(s, MockClient())) as c:
        assert c.post("/internal/v1/translate", json=body()).json()["error_code"] == "unauthorized"
        assert c.post("/internal/v1/translate", json=body(), headers={"X-Internal-Token": "secret"}).status_code == 200


def test_wrong_language_skips_model():
    mock = MockClient()
    with TestClient(create_app(SETTINGS, mock)) as c:
        d = c.post("/internal/v1/translate", json=body(text="휘발유 가득 넣어 주세요")).json()
        assert d["status"] == "clarification_required" and mock.calls == 0


def test_validation_retry_then_success():
    fake = FakeLlama(outputs=[ok("경유 가득 넣어 주세요."), ok("휘발유 가득 넣어 주세요.")])
    with TestClient(create_app(SETTINGS, fake)) as c:
        d = c.post("/internal/v1/translate", json=body()).json()
        assert d["translated_text"] == "휘발유 가득 넣어 주세요." and fake.calls == 2


class RecordingLlama(FakeLlama):
    def __init__(self, outputs):
        super().__init__(outputs=outputs)
        self.messages = []

    async def chat_json(self, messages, *a, **kw):
        self.messages.append(messages)
        return await super().chat_json(messages, *a, **kw)


def test_retry_explains_what_to_fix():
    fake = RecordingLlama([ok("信用카드로 결제할 수 있어요?"), ok("신용카드로 결제할 수 있어요?")])
    with TestClient(create_app(SETTINGS, fake)) as c:
        d = c.post("/internal/v1/translate", json=body(text="Can I pay by credit card?")).json()
    assert d["translated_text"] == "신용카드로 결제할 수 있어요?"
    assert "Hangul only" in fake.messages[1][-1]["content"]


def test_bad_clarification_becomes_fixed_question():
    q = {"status": "clarification_required", "translated_text": None, "clarification_question": "Could you say it again?"}
    fake = FakeLlama(outputs=[q])
    with TestClient(create_app(SETTINGS, fake)) as c:
        d = c.post("/internal/v1/translate", json=body(text="저기 그거... 어...", source_locale="ko-KR",
                                                     target_locale="en-US")).json()
    assert d["status"] == "clarification_required" and fake.calls == 1
    assert d["clarification_question"] == "잘 알아듣지 못했어요. 문장 전체를 다시 말씀해 주세요."


def test_validation_failure_is_502_and_not_cached():
    fake = FakeLlama(outputs=[ok("3만 원어치 넣어 주세요."), ok("5만 원어치 넣어 주세요.")])
    with TestClient(create_app(SETTINGS, fake)) as c:
        r = c.post("/internal/v1/translate", json=body(text="Put in 30,000 won worth, not more."))
        assert r.status_code == 502 and r.json()["error_code"] == "output_validation_failed"


def test_bad_json_retry():
    fake = FakeLlama(outputs=[ModelBadOutput("x"), ok("휘발유 가득 넣어 주세요.")])
    with TestClient(create_app(SETTINGS, fake)) as c:
        assert c.post("/internal/v1/translate", json=body()).status_code == 200


def test_timeout_504():
    s = dataclasses.replace(SETTINGS, model_timeout_s=0.2)
    with TestClient(create_app(s, MockClient(delay_s=1.0))) as c:
        r = c.post("/internal/v1/translate", json=body())
        assert r.status_code == 504 and r.json()["error_code"] == "model_timeout"
        assert c.post("/internal/v1/translate", json=body(request_id="r2")).status_code == 504  # 슬롯이 다시 열림


async def _async_client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ai")


def test_busy_429_and_inflight_cancel():
    async def run():
        app = create_app(SETTINGS, MockClient(delay_s=0.6))
        async with app.router.lifespan_context(app):
            async with await _async_client(app) as c:
                first = asyncio.create_task(c.post("/internal/v1/translate", json=body()))
                await asyncio.sleep(0.1)
                busy = await c.post("/internal/v1/translate", json=body(request_id="r2", session_id="s2"))
                assert busy.status_code == 429 and busy.headers["retry-after"] == "1"
                same = asyncio.create_task(c.post("/internal/v1/translate", json=body()))  # 같은 요청은 같은 작업을 기다림
                cancel = await c.post("/internal/v1/cancel", json={"session_id": "s1", "session_epoch": 1})
                assert cancel.json()["cancelled_inflight"] == 1
                r1, r1b = await first, await same
                assert r1.status_code == 409 and r1.json()["error_code"] == "epoch_cancelled"
                assert r1b.status_code == 409
                again = await c.post("/internal/v1/translate", json=body(request_id="r3", session_epoch=2))
                assert again.status_code == 200

    asyncio.run(run())


def test_slot_waiters_dedupe_conflict_and_cancel():
    # 검증: 슬롯을 기다리는 동안 같은 ID는 합류, 다른 내용은 409, 취소된 대기 요청은 모델을 호출하지 않음
    async def run():
        m = MockClient(delay_s=0.3)
        app = create_app(dataclasses.replace(SETTINGS, busy_wait_s=2.0), m)
        async with app.router.lifespan_context(app):
            async with await _async_client(app) as c:
                post = lambda **kw: asyncio.create_task(c.post("/internal/v1/translate", json=body(**kw)))
                first = post(request_id="x", session_id="sx")
                await asyncio.sleep(0.05)
                a1, a2 = post(request_id="r"), post(request_id="r")
                await asyncio.sleep(0.05)
                q = post(request_id="q", session_id="sq", text="Hello there.")
                await asyncio.sleep(0.05)
                conflict = await c.post("/internal/v1/translate", json=body(request_id="q", session_id="sq", text="Bye."))
                w = post(request_id="w", session_id="sw")
                await asyncio.sleep(0.05)
                await c.post("/internal/v1/cancel", json={"session_id": "sw", "session_epoch": 1})
                rs = await asyncio.gather(first, a1, a2, q, w)
        assert conflict.status_code == 409 and conflict.json()["error_code"] == "request_id_conflict"
        assert [r.status_code for r in rs] == [200, 200, 200, 200, 409]
        assert m.calls == 3  # x, r(1회), q

    asyncio.run(run())


class StuckModel(MockClient):
    """timeout 뒤에도 llama-server가 계산 중인 상태."""

    async def wait_idle(self, max_s):
        return False


def test_slot_stays_busy_until_model_idle():
    s = dataclasses.replace(SETTINGS, model_timeout_s=0.2, busy_wait_s=0)
    m = StuckModel(delay_s=5.0)
    with TestClient(create_app(s, m)) as c:
        assert c.post("/internal/v1/translate", json=body()).status_code == 504
        r = c.post("/internal/v1/translate", json=body(request_id="r2"))
        assert r.status_code == 429 and m.calls == 1


def test_destination_match_and_not_found():
    dest = {"intent": "set_destination", "query_original": "Seongsan Sunrise Peak", "query_ko_candidate": "성산일출봉",
            "region_hint": "성산", "category_hint": "attraction", "needs_confirmation": False}
    none = {**dest, "query_original": "Lotte World", "query_ko_candidate": "롯데월드", "region_hint": "서울"}
    other = {**dest, "intent": "other", "query_original": None, "query_ko_candidate": None, "region_hint": None}
    fake = FakeLlama(outputs=[dest, none, other])
    db = {k: v for k, v in body().items() if k not in ("target_locale", "scenario")}
    with TestClient(create_app(SETTINGS, fake)) as c:
        a = c.post("/internal/v1/parse-destination", json={**db, "text": "Take me to Seongsan Sunrise Peak"}).json()
        assert a["status"] == "ok" and a["candidates"][0]["poi_id"] == "seongsan-ilchulbong"
        assert a["needs_confirmation"] is True  # 좌표 미검증 장소는 항상 확인
        b = c.post("/internal/v1/parse-destination", json={**db, "request_id": "r2", "text": "Go to Lotte World"}).json()
        assert b["status"] == "not_found" and b["candidates"] == []
        o = c.post("/internal/v1/parse-destination", json={**db, "request_id": "r3", "text": "Turn on the AC"}).json()
        assert o["status"] == "not_destination"


def test_destination_found_from_utterance_when_query_is_wrong():
    # 검증: 모델 검색어가 틀려도(하라산) 발화에 있는 장소 이름(Seongpanak)으로 찾는다
    wrong = {"intent": "set_destination", "query_original": "Hallasan", "query_ko_candidate": "하라산",
             "region_hint": None, "category_hint": "mountain", "needs_confirmation": False}
    falls = {**wrong, "query_original": "the waterfall in Seogwipo", "query_ko_candidate": "서귀포의 물결"}
    fake = FakeLlama(outputs=[wrong, falls])
    db = {k: v for k, v in body().items() if k not in ("target_locale", "scenario")}
    with TestClient(create_app(SETTINGS, fake)) as c:
        a = c.post("/internal/v1/parse-destination", json={**db, "text": "I want to climb Hallasan from Seongpanak."}).json()
        b = c.post("/internal/v1/parse-destination",
                   json={**db, "request_id": "r2", "text": "Take me to the waterfall in Seogwipo."}).json()
    assert a["candidates"][0]["poi_id"] == "seongpanak-trail"
    assert {x["poi_id"] for x in b["candidates"][:2]} == {"cheonjiyeon-falls", "jeongbang-falls"}
    assert b["needs_confirmation"] is True


def test_openapi_committed_matches():
    # 백엔드 연동: docs/openapi.json이 실제 서버 계약과 같아야 한다(변경 시 scripts/export_openapi.py)
    committed = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    assert committed == create_app(SETTINGS, MockClient()).openapi()
