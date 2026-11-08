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
    assert d["prompt_version"] == "translate-v7" and r.headers["content-type"].endswith("charset=utf-8")
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
    ({"source_locale": "es-US", "target_locale": "en-US"}, "invalid_input"),  # 통역은 한국어와 외국어 한 쌍
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
    assert fake.messages[1][:-1] == fake.messages[0][:-1]
    assert "信用" not in json.dumps(fake.messages[1], ensure_ascii=False)


def test_retry_deduplicates_checks_without_dropping_the_source():
    import prompting as P
    from engine import translation_repair
    text = ("Read these numbers: " + "2 " * 200)[:400]
    original = P.translate_messages("en-US", "ko-KR", "general", text)
    repair = json.loads(translation_repair(original, ["number_missing:2"] * 190)[-1]["content"])
    assert repair["utterance"] == text
    assert repair["repair"].count("number_missing") == 1


@pytest.mark.parametrize("text,source,target,bad,good,term,equivalent", [
    ("Gire a la derecha.", "es-MX", "ko-KR", "유턴하세요.", "우회전하세요.", "Gire a la derecha", "우회전"),
    ("우회전해 주세요.", "ko-KR", "en-US", "Please make a U-turn.", "Please turn right.", "우회전", "right turn"),
    ("No pagué la gasolina.", "es-US", "ko-KR", "가스 비용을 내지 않았어요.", "휘발유 비용을 내지 않았어요.",
     "gasolina", "휘발유"),
])
def test_retry_uses_concrete_source_glossary(text, source, target, bad, good, term, equivalent):
    fake = RecordingLlama([ok(bad), ok(good)])
    with TestClient(create_app(SETTINGS, fake)) as c:
        r = c.post("/internal/v1/translate", json=body(text=text, source_locale=source, target_locale=target))
    assert r.status_code == 200 and r.json()["translated_text"] == good
    repair = json.loads(fake.messages[1][-1]["content"])
    assert repair["glossary"][term] == equivalent
    assert term in repair["repair"] and equivalent in repair["repair"]
    assert bad not in fake.messages[1][-1]["content"]


def test_attempt_log_only_when_enabled(caplog):
    bad = ok("휘발유와 경유로 5만 원어치 넣어 주세요.")
    text = "Please put in 50,000 won of gasoline, not diesel."
    for flag, expect in ((False, 0), (True, 2)):
        caplog.clear()
        with TestClient(create_app(dataclasses.replace(SETTINGS, log_rejected=flag), FakeLlama(outputs=[bad, bad]))) as c:
            assert c.post("/internal/v1/translate", json=body(text=text)).status_code == 502
        records = [json.loads(r.getMessage().split("translation_attempt ", 1)[1]) for r in caplog.records
                   if r.getMessage().startswith("translation_attempt ")]
        assert len(records) == expect
        if records:
            assert [r["attempt"] for r in records] == [1, 2]
            assert all(r["issues"] and r["output"] == bad for r in records)


def test_recovered_retry_is_logged_but_failed_output_not_returned(caplog):
    bad, good = ok("경유 가득 넣어 주세요."), ok("휘발유 가득 넣어 주세요.")
    with TestClient(create_app(dataclasses.replace(SETTINGS, log_rejected=True), FakeLlama(outputs=[bad, good]))) as c:
        response = c.post("/internal/v1/translate", json=body())
    assert response.status_code == 200 and response.json()["translated_text"] == good["translated_text"]
    assert "경유" not in response.text
    attempts = [json.loads(r.getMessage().split("translation_attempt ", 1)[1]) for r in caplog.records
                if r.getMessage().startswith("translation_attempt ")]
    assert attempts[0]["issues"] and attempts[0]["output"] == bad
    assert attempts[1]["issues"] == [] and attempts[1]["output"] == good


def test_polish_replaces_only_when_checks_pass():
    s = dataclasses.replace(SETTINGS, polish_ko=True)
    text = "Can you write down your phone number for me?"
    draft, good, bad = "전화번호를 적어 주시겠습니까?", "전화번호 좀 적어 주시겠어요?", "전화번호를 적지 마세요."
    for polished, expect in ((good, good), (bad, draft)):
        fake = RecordingLlama([ok(draft), {"text": polished}])
        with TestClient(create_app(s, fake)) as c:
            d = c.post("/internal/v1/translate", json=body(text=text, scenario="general")).json()
        assert d["translated_text"] == expect and fake.calls == 2
        assert '"korean"' in fake.messages[1][-1]["content"]  # 2차 요청은 다듬기 프롬프트


def test_polish_rewrite_or_model_errors_keep_draft():
    from inference import ModelTimeout, ModelUnavailable
    s = dataclasses.replace(SETTINGS, polish_ko=True)
    draft = "차 안에서 기다려 주세요."
    for second in ({"text": "차 밖에서 기다려 주세요."},  # 반대말로 바뀜
                   {"text": "날씨가 좋으니 잠깐 산책하고 오세요, 천천히요."},  # 통째로 다시 씀
                   ModelUnavailable("down"), ModelTimeout("slow")):
        fake = FakeLlama(outputs=[ok(draft), second])
        with TestClient(create_app(s, fake)) as c:
            r = c.post("/internal/v1/translate", json=body(text="Please wait inside the car.", scenario="general"))
        assert r.status_code == 200 and r.json()["translated_text"] == draft, second


def test_polish_off_by_default_and_not_for_english_target():
    fake = FakeLlama(outputs=[ok("전화번호 좀 적어 주시겠어요?")])
    with TestClient(create_app(SETTINGS, fake)) as c:
        c.post("/internal/v1/translate", json=body(text="Can you write down your phone number?", scenario="general"))
    assert fake.calls == 1
    fake = FakeLlama(outputs=[ok("Please write down your number.")])
    with TestClient(create_app(dataclasses.replace(SETTINGS, polish_ko=True), fake)) as c:
        c.post("/internal/v1/translate", json=body(text="번호 좀 적어 주세요.", source_locale="ko-KR",
                                                     target_locale="en-US", scenario="general"))
    assert fake.calls == 1


@pytest.mark.parametrize("source,draft,changed", [
    ("May I park here?", "여기에 주차해도 될까요?", "여기에 주차해도 돼요."),
    ("I'll bring the car.", "제가 차를 가져올게요.", "제가 차를 가져와 주세요."),
    ("The clerk said it was paid.", "직원이 결제됐다고 했어요.", "직원이 결제됐어요."),
    ("I'm not sure it was there before.", "원래 있었는지는 모르겠어요.", "원래 있었어요."),
    ("I signed the contract.", "제가 계약서에 서명했어요.", "고객님이 계약서에 서명했어요."),
])
def test_polish_keeps_draft_when_intent_markers_change(source, draft, changed):
    fake = FakeLlama(outputs=[ok(draft), {"text": changed}])
    with TestClient(create_app(dataclasses.replace(SETTINGS, polish_ko=True), fake)) as c:
        r = c.post("/internal/v1/translate", json=body(text=source, scenario="general"))
    assert r.status_code == 200 and r.json()["translated_text"] == draft


def test_dropped_pm_is_filled_without_retry():
    fake = FakeLlama(outputs=[ok("6시까지 돌아올 예정이에요.")])
    with TestClient(create_app(SETTINGS, fake)) as c:
        d = c.post("/internal/v1/translate", json=body(text="We'll be back by 6 p.m.", scenario="parking")).json()
    assert d["translated_text"] == "오후 6시까지 돌아올 예정이에요." and fake.calls == 1


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


@pytest.mark.parametrize("queries,utterance,top", [
    (["성산", "the sunrise at Seongsan"], "I want to see the sunrise at Seongsan.", "seongsan-ilchulbong"),
    (["우도에 들어가는 배를 타는 곳"], "우도 들어가는 배 타는 곳으로 가자", "seongsanpo-ferry"),
    (["중문 바다"], "중문 바다 쪽으로 가자", "jungmun-saekdal-beach"),
    (["해변", "a beach"], "Take me to a beach.", None),
    (["바다 보이는 데"], "바다 보이는 데로 가자", None),
])
def test_place_search_partial_names_and_synonyms(queries, utterance, top):
    from engine import PoiCatalog
    got = PoiCatalog().search(queries, None, utterance=utterance)
    assert (got[0]["poi_id"] if got else None) == top


def test_openapi_committed_matches():
    # 백엔드 연동: docs/openapi.json이 실제 서버 계약과 같아야 한다(변경 시 scripts/export_openapi.py)
    committed = json.loads((ROOT / "docs" / "openapi.json").read_text(encoding="utf-8"))
    assert committed == create_app(SETTINGS, MockClient()).openapi()


def test_spanish_translate_and_fixed_phrases():
    fake = FakeLlama(outputs=[ok("경유 말고 휘발유로 3만 원어치 넣어 주세요.")])
    mock = MockClient()
    with TestClient(create_app(SETTINGS, fake)) as c:
        d = c.post("/internal/v1/translate", json=body(source_locale="es-MX",
                                                     text="Póngale 30,000 wones de gasolina, no de diésel.")).json()
        assert d["status"] == "ok" and d["translated_text"].startswith("경유 말고")
    with TestClient(create_app(SETTINGS, mock)) as c:
        w = c.post("/internal/v1/translate", json=body(source_locale="es-US", text="휘발유 가득 넣어 주세요")).json()
        assert w["clarification_question"].startswith("Parece que habló en otro idioma") and mock.calls == 0
    q = {"status": "clarification_required", "translated_text": None, "clarification_question": "다시 말씀해 주세요."}
    with TestClient(create_app(SETTINGS, FakeLlama(outputs=[q]))) as c:
        r = c.post("/internal/v1/translate", json=body(source_locale="es-US", text="¿Me lo llena con, eh...?")).json()
        assert r["clarification_question"] == "Perdón, no le entendí. ¿Puede repetir toda la frase?"


def test_spanish_destination_has_spanish_name_and_question():
    dest = {"intent": "set_destination", "query_original": "playa de Hamdeok", "query_ko_candidate": "함덕해수욕장",
            "region_hint": None, "category_hint": "beach", "needs_confirmation": False}
    vague = {**dest, "query_original": "la playa", "query_ko_candidate": None, "needs_confirmation": True}
    db = {k: v for k, v in body(source_locale="es-MX").items() if k not in ("target_locale", "scenario")}
    with TestClient(create_app(SETTINGS, FakeLlama(outputs=[dest, vague]))) as c:
        a = c.post("/internal/v1/parse-destination", json={**db, "text": "Llévenos a la playa de Hamdeok."}).json()
        assert a["candidates"][0]["poi_id"] == "hamdeok-beach" and a["candidates"][0]["name_es"] == "Playa Hamdeok"
        assert len(a["candidates"]) == 1
        b = c.post("/internal/v1/parse-destination", json={**db, "request_id": "r2", "text": "Llévame a la playa."}).json()
        assert b["status"] == "clarification_required" and b["clarification_question"].startswith("¿A qué lugar")
