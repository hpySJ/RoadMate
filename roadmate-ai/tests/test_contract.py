# 테스트
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
SETTINGS = dataclasses.replace(BASE, model_backend="mock", model_timeout_s=30.0, busy_wait_s=0.05, cancel_settle_s=0.1,
                               dedupe_ttl_s=30, max_text_chars=400, internal_token="")


class FakeLlama(MockClient):

    is_mock = False
    model_id = SETTINGS.model_id


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
    assert d["prompt_version"] == "translate-v11" and r.headers["content-type"].endswith("charset=utf-8")
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
    ({"source_locale": "es-US", "target_locale": "en-US"}, "invalid_input"),
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


@pytest.mark.parametrize("locale", ["en-US", "es-US", "es-MX"])
@pytest.mark.parametrize("text", [
    "Bonjour, je voudrais de l'essence s'il vous plaît.",
    "Nous sommes perdus, pouvez-vous nous aider ?",
])
def test_unsupported_french_clarifies_without_model(locale, text):
    from engine import WRONG_LANG
    fake = FakeLlama()
    with TestClient(create_app(SETTINGS, fake)) as c:
        r = c.post("/internal/v1/translate", json=body(source_locale=locale, text=text))
    assert r.status_code == 200
    assert r.json()["status"] == "clarification_required"
    assert r.json()["translated_text"] is None
    assert r.json()["clarification_question"] == WRONG_LANG[locale]
    assert fake.calls == 0


def test_pronouns_are_normalized_after_time_and_before_validation():
    fake = FakeLlama(outputs=[ok("나는 6 p.m.까지 돌아올게요.")])
    with TestClient(create_app(SETTINGS, fake)) as c:
        r = c.post("/internal/v1/translate", json=body(text="I'll be back by 6 p.m."))
    assert r.status_code == 200 and r.json()["translated_text"] == "저는 오후 6시까지 돌아올게요."
    assert fake.calls == 1


def test_normalized_pronouns_do_not_bypass_validation_on_either_attempt():
    fake = FakeLlama(outputs=[ok("나는 가방 세 개가 있어요.")] * 2)
    with TestClient(create_app(SETTINGS, fake)) as c:
        r = c.post("/internal/v1/translate", json=body(text="I have two bags."))
    assert r.status_code == 502 and r.json()["error_code"] == "output_validation_failed" and fake.calls == 2


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


@pytest.mark.parametrize("text,surface,korean", [
    ("We'll be back by 6 p.m.", "6 p.m.", "오후 6시"),
    ("I'll return at quarter past four.", "quarter past four", "4시 15분"),
])
def test_retry_replaces_misread_numbers_with_concrete_hints(text, surface, korean):
    import prompting as P
    from engine import REPAIR_HINT, translation_repair
    original = P.translate_messages("en-US", "ko-KR", "general", text)
    repair = json.loads(translation_repair(original, ["number_missing:16", "number_added:4"])[-1]["content"])
    assert repair["glossary"][surface] == korean
    assert surface in repair["repair"] and korean in repair["repair"]
    assert "exact supplied equivalents" in repair["repair"]
    assert REPAIR_HINT["number_added"] not in repair["repair"]
    assert repair["utterance"] == text


def test_retry_single_added_number_keeps_its_hint():
    import prompting as P
    from engine import REPAIR_HINT, translation_repair
    original = P.translate_messages("en-US", "ko-KR", "general", "Please wait.")
    repair = json.loads(translation_repair(original, ["number_added:6"])[-1]["content"])
    assert REPAIR_HINT["number_added"] in repair["repair"]


@pytest.mark.parametrize("source", [
    "Is this pump self-service or full-service?",
    "If the car breaks down again, who should I call first, you or the insurance company?",
    "Do you want the blue folder or the green folder",
])
def test_retry_keeps_nonnegative_alternative_questions(source):
    import prompting as P
    from engine import translation_repair
    original = P.translate_messages("en-US", "ko-KR", "general", source)
    repair = json.loads(translation_repair(original, ["negation_mismatch"])[-1]["content"])
    assert "A-or-B choice" in repair["repair"] and "아니면" in repair["repair"]
    assert repair["utterance"] == source


@pytest.mark.parametrize("source,issues", [
    ("Don't use gasoline or diesel.", ["negation_mismatch"]),
    ("Doesn’t this car use gasoline or diesel?", ["negation_mismatch"]),
    ("Is it free or paid?", ["negation_mismatch"]),
    ("Please call me or the insurance company.", ["negation_mismatch"]),
    ("Could you help me?", ["negation_mismatch"]),
    ("Do you want tea or coffee?", ["too_short"]),
])
def test_choice_retry_hint_is_not_applied_to_other_cases(source, issues):
    import prompting as P
    from engine import translation_repair
    original = P.translate_messages("en-US", "ko-KR", "general", source)
    repair = json.loads(translation_repair(original, issues)[-1]["content"])
    assert "A-or-B choice" not in repair["repair"]


def test_mixed_script_retry_names_source_word_without_copying_bad_draft():
    source = "Could you tell the staff that my wife is allergic to peanuts?"
    bad = "제 아내가 페anut 알레르기가 있다고 직원에게 알려 주시겠어요?"
    good = "제 아내가 땅콩 알레르기가 있다고 직원에게 알려 주시겠어요?"
    fake = RecordingLlama([ok(bad), ok(good)])
    with TestClient(create_app(SETTINGS, fake)) as c:
        r = c.post("/internal/v1/translate", json=body(text=source, scenario="general"))
    assert r.status_code == 200 and r.json()["translated_text"] == good and fake.calls == 2
    repair = json.loads(fake.messages[1][-1]["content"])
    assert '["peanuts"]' in repair["repair"]
    assert "complete natural Korean words" in repair["repair"]
    assert "페anut" not in json.dumps(fake.messages[1], ensure_ascii=False)
    assert repair["utterance"] == source


def test_mixed_script_retry_matches_only_complete_source_tokens_and_deduplicates():
    import prompting as P
    from engine import translation_repair
    original = P.translate_messages("en-US", "ko-KR", "general", "peanuts, peanuts; walnuts.")
    repair = json.loads(translation_repair(original, ["mixed_script:ANUT", "mixed_script:anut", "mixed_script:alnu"])[-1]["content"])
    assert '["peanuts", "walnuts"]' in repair["repair"]


@pytest.mark.parametrize("source,issue,word", [
    ("¿Está aquí?", "mixed_script:QUÍ", "aquí"),
    ("Choose option x.", "mixed_script:x", "x"),
])
def test_mixed_script_retry_accepts_latin_accents_and_single_letters(source, issue, word):
    import prompting as P
    from engine import translation_repair
    original = P.translate_messages("es-US" if "¿" in source else "en-US", "ko-KR", "general", source)
    repair = json.loads(translation_repair(original, [issue])[-1]["content"])
    assert json.dumps([word], ensure_ascii=False) in repair["repair"]


@pytest.mark.parametrize("source,bad,good", [
    ("¿Ya llamó a la policía?", "경찰에 already 전화했나요?", "경찰에 이미 전화했나요?"),
    ("Disculpe, ¿alguien aquí habla inglés?", "여기 Anyone aquí가 영어를 말합니까?", "실례지만 여기 영어를 하는 분이 있나요?"),
])
def test_unexpected_latin_words_retry_before_they_can_be_played(source, bad, good):
    fake = FakeLlama(outputs=[ok(bad), ok(good)])
    with TestClient(create_app(SETTINGS, fake)) as c:
        r = c.post("/internal/v1/translate", json=body(source_locale="es-US", text=source, scenario="general"))
    assert r.status_code == 200 and r.json()["translated_text"] == good and fake.calls == 2


@pytest.mark.parametrize("issue", ["mixed_script", "mixed_script:zzzz", "mixed_script:a.*"])
def test_mixed_script_retry_does_not_invent_source_words(issue):
    import prompting as P
    from engine import REPAIR_HINT, translation_repair
    original = P.translate_messages("en-US", "ko-KR", "general", "Please wait.")
    repair = json.loads(translation_repair(original, [issue])[-1]["content"])
    assert REPAIR_HINT["mixed_script"] in repair["repair"]
    assert "Translate these source words" not in repair["repair"]


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


def test_dropped_pm_is_filled_without_retry():
    fake = FakeLlama(outputs=[ok("6시까지 돌아올 예정이에요.")])
    with TestClient(create_app(SETTINGS, fake)) as c:
        d = c.post("/internal/v1/translate", json=body(text="We'll be back by 6 p.m.", scenario="parking")).json()
    assert d["translated_text"] == "오후 6시까지 돌아올 예정이에요." and fake.calls == 1


@pytest.mark.parametrize("time_text,korean", [
    ("6 p.m.", "오후 6시"), ("6:15 PM", "오후 6시 15분"),
    ("9 a. m.", "오전 9시"), ("12 a.m.", "자정"), ("12 p.m.", "정오"),
])
def test_english_clock_in_korean_is_normalized_without_retry(time_text, korean):
    fake = FakeLlama(outputs=[ok(f"{time_text}까지 돌아올 거예요.")])
    with TestClient(create_app(SETTINGS, fake)) as c:
        r = c.post("/internal/v1/translate", json=body(text=f"We'll be back by {time_text}", scenario="parking"))
    assert r.status_code == 200 and r.json()["translated_text"] == f"{korean}까지 돌아올 거예요."
    assert fake.calls == 1


@pytest.mark.parametrize("draft,expected", [
    ("오후 6 p.m.까지 돌아올 거예요.", "오후 6시까지 돌아올 거예요."),
    ("오후 quarter past six", "오후 6시 15분"),
    ("quarter to 12 p.m. 또는 12 p.m.에 만나요.", "오전 11시 45분 또는 정오에 만나요."),
])
def test_clock_normalization_preserves_complete_expressions(draft, expected):
    from types import SimpleNamespace
    from engine import _with_meridiem
    req = SimpleNamespace(text="", source_locale="en-US", target_locale="ko-KR")
    assert _with_meridiem(ok(draft), req)["translated_text"] == expected


@pytest.mark.parametrize("draft", ["오전 6 p.m.까지 돌아올 거예요.", "오전 12 p.m.까지 돌아올 거예요."])
def test_conflicting_clock_markers_require_retry(draft):
    fake = FakeLlama(outputs=[ok(draft), ok(draft)])
    with TestClient(create_app(SETTINGS, fake)) as c:
        r = c.post("/internal/v1/translate", json=body(text="We'll be back by 6 p.m.", scenario="parking"))
    assert r.status_code == 502 and r.json()["error_code"] == "output_validation_failed"
    assert fake.calls == 2


def test_normalized_clock_still_rejects_changed_time():
    bad = ok("7 p.m.까지 돌아올 거예요.")
    fake = FakeLlama(outputs=[bad, bad])
    with TestClient(create_app(SETTINGS, fake)) as c:
        r = c.post("/internal/v1/translate", json=body(text="We'll be back by 6 p.m.", scenario="parking"))
    assert r.status_code == 502 and r.json()["error_code"] == "output_validation_failed"


@pytest.mark.parametrize("text", ["13 p.m.까지요.", "6:99 p.m.까지요.", "1.5 p.m.까지요.", "3:6 p.m.까지요."])
def test_invalid_clocks_are_not_normalized(text):
    from types import SimpleNamespace
    from engine import _with_meridiem
    req = SimpleNamespace(text="Please wait.", source_locale="en-US", target_locale="ko-KR")
    assert _with_meridiem(ok(text), req)["translated_text"] == text


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
        assert c.post("/internal/v1/translate", json=body(request_id="r2")).status_code == 504


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
                same = asyncio.create_task(c.post("/internal/v1/translate", json=body()))
                cancel = await c.post("/internal/v1/cancel", json={"session_id": "s1", "session_epoch": 1})
                assert cancel.json()["cancelled_inflight"] == 1
                r1, r1b = await first, await same
                assert r1.status_code == 409 and r1.json()["error_code"] == "epoch_cancelled"
                assert r1b.status_code == 409
                again = await c.post("/internal/v1/translate", json=body(request_id="r3", session_epoch=2))
                assert again.status_code == 200

    asyncio.run(run())


def test_slot_waiters_dedupe_conflict_and_cancel():
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
        assert m.calls == 3

    asyncio.run(run())


class StuckModel(MockClient):

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
        assert a["needs_confirmation"] is True
        b = c.post("/internal/v1/parse-destination", json={**db, "request_id": "r2", "text": "Go to Lotte World"}).json()
        assert b["status"] == "not_found" and b["candidates"] == []
        o = c.post("/internal/v1/parse-destination", json={**db, "request_id": "r3", "text": "Turn on the AC"}).json()
        assert o["status"] == "not_destination"


def test_destination_found_from_utterance_when_query_is_wrong():
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
    ([], "만장굴 어떻게 가?", "manjanggul"),
    ([], "Quiero ver el amanecer en Seongsan.", "seongsan-ilchulbong"),
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
