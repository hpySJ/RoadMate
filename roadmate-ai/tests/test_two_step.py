import dataclasses
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app import create_app
from config import Settings, experiment_flags
from inference import MockClient
import prompting as P


class RecordingModel(MockClient):
    is_mock = False
    model_id = "roadmate-qwen4b-q4"

    def __init__(self, outputs):
        super().__init__(outputs=outputs)
        self.requests = []

    async def chat_json(self, messages, schema, max_tokens, sampling, timeout_s):
        self.requests.append({"messages": messages, "schema": schema, "max_tokens": max_tokens})
        return await super().chat_json(messages, schema, max_tokens, sampling, timeout_s)


@pytest.fixture
def settings():
    return dataclasses.replace(Settings.from_env(), model_backend="mock", model_timeout_s=3,
                               internal_token="", log_rejected=False, polish_ko=False,
                               two_step_ko=False, natural_style_ko=False, field_glossary=False)


def request(settings, **overrides):
    return {"request_id": "two-step-test", "session_id": "test-session", "session_epoch": 1,
            "turn_id": 1, "source_locale": "en-US", "target_locale": "ko-KR", "scenario": "general",
            "text": "I will return at 8 p.m.", "engine_bundle": settings.engine_bundle, **overrides}


def output(text, literal):
    return {"status": "ok", "literal": literal, "translated_text": text, "clarification_question": None}


@pytest.mark.parametrize("log_rejected", [False, True])
def test_literal_is_private_unvalidated_and_unnormalized(settings, caplog, log_rejected):
    literal = "나는 9 p.m.에 돌아갈게요. LITERAL_ONLY_SENTINEL"
    fake = RecordingModel([output("나는 8 p.m.에 돌아갈게요.", literal)])
    configured = dataclasses.replace(settings, two_step_ko=True, log_rejected=log_rejected)
    with caplog.at_level(logging.WARNING, logger="roadmate.ai"):
        with TestClient(create_app(configured, fake)) as client:
            response = client.post("/internal/v1/translate", json=request(configured))
    assert response.status_code == 200
    assert response.json()["translated_text"] == "저는 오후 8시에 돌아갈게요."
    assert "literal" not in response.json() and "LITERAL_ONLY_SENTINEL" not in response.text
    assert fake.calls == 1  # The wrong time/foreign words in literal never enter validation.
    records = [record.getMessage() for record in caplog.records if record.name == "roadmate.ai"]
    if log_rejected:
        logged = [json.loads(line.removeprefix("translation_literal ")) for line in records
                  if line.startswith("translation_literal ")]
        assert logged == [{"request_id": "two-step-test", "attempt": 1, "literal": literal,
                           "translated_text": "나는 8 p.m.에 돌아갈게요."}]
        attempt = next(json.loads(line.removeprefix("translation_attempt ")) for line in records
                       if line.startswith("translation_attempt "))
        assert "literal" not in attempt["output"]
    else:
        assert "LITERAL_ONLY_SENTINEL" not in caplog.text
        assert not any(line.startswith("translation_literal ") for line in records)


def test_good_literal_cannot_hide_changed_facts_in_final_translation(settings):
    good_literal = "오후 8시에 돌아갈게요."
    fake = RecordingModel([output("오후 9시에 돌아갈게요.", good_literal)] * 2)
    configured = dataclasses.replace(settings, two_step_ko=True)
    with TestClient(create_app(configured, fake)) as client:
        response = client.post("/internal/v1/translate", json=request(configured))
    assert response.status_code == 502 and response.json()["error_code"] == "output_validation_failed"
    assert fake.calls == 2


@pytest.mark.parametrize("literal", [None, 8, {}, []])
def test_two_step_rejects_non_string_literal_for_ok_output(settings, literal):
    fake = RecordingModel([output("오후 8시에 돌아갈게요.", literal)] * 2)
    configured = dataclasses.replace(settings, two_step_ko=True)
    with TestClient(create_app(configured, fake)) as client:
        response = client.post("/internal/v1/translate", json=request(configured))
    assert response.status_code == 502 and response.json()["error_code"] == "output_validation_failed"
    assert fake.calls == 2


def test_two_step_missing_literal_retries_before_accepting_final_text(settings):
    good = output("오후 8시에 돌아갈게요.", "오후 8시에 돌아갈게요.")
    missing = {key: value for key, value in good.items() if key != "literal"}
    fake = RecordingModel([missing, good])
    configured = dataclasses.replace(settings, two_step_ko=True)
    with TestClient(create_app(configured, fake)) as client:
        response = client.post("/internal/v1/translate", json=request(configured))
    assert response.status_code == 200 and fake.calls == 2
    assert "literal" not in response.json()


@pytest.mark.parametrize("literal,expected_http", [(None, 200), ("Uncertain translation", 502)])
def test_two_step_clarification_requires_null_literal(settings, literal, expected_http):
    clarification = {"status": "clarification_required", "literal": literal, "translated_text": None,
                     "clarification_question": "Could you repeat the whole sentence, please?"}
    fake = RecordingModel([clarification] * 2)
    configured = dataclasses.replace(settings, two_step_ko=True)
    with TestClient(create_app(configured, fake)) as client:
        response = client.post("/internal/v1/translate", json=request(configured))
    assert response.status_code == expected_http and "literal" not in response.json()
    if expected_http == 200:
        assert response.json()["status"] == "clarification_required" and fake.calls == 1
    else:
        assert response.json()["error_code"] == "output_validation_failed" and fake.calls == 2


def test_retry_keeps_two_step_schema_token_limit_and_instruction(settings):
    fake = RecordingModel([output("I will return at 8 p.m.", "오후 8시에 돌아갈게요."),
                           output("8 p.m.에 돌아갈게요.", "나는 8 p.m.에 돌아갈게요.")])
    configured = dataclasses.replace(settings, two_step_ko=True)
    with TestClient(create_app(configured, fake)) as client:
        response = client.post("/internal/v1/translate", json=request(configured))
    assert response.status_code == 200 and response.json()["translated_text"] == "오후 8시에 돌아갈게요."
    assert fake.calls == 2
    first, retry = fake.requests
    assert first["schema"] == retry["schema"] == P.translation_schema("ko-KR", True)
    assert list(first["schema"]["properties"]).index("literal") < list(first["schema"]["properties"]).index("translated_text")
    assert first["max_tokens"] == retry["max_tokens"] == P.translation_max_tokens("ko-KR", True)
    assert first["max_tokens"] > P.TRANSLATE_MAX_TOKENS
    assert first["messages"][0] == retry["messages"][0]
    assert "literal" in retry["messages"][0]["content"]
    assert "repair" in json.loads(retry["messages"][-1]["content"])


@pytest.mark.parametrize("locale", ["en-US", "es-US", "es-MX"])
def test_two_step_and_korean_style_flags_do_not_change_non_korean_targets(locale):
    ordinary = P.translate_messages("ko-KR", locale, "general", "계약서를 확인해 주세요.")
    experimental = P.translate_messages("ko-KR", locale, "general", "계약서를 확인해 주세요.",
                                         two_step_ko=True, natural_style_ko=True)
    assert experimental == ordinary
    assert P.translation_schema(locale, True) == P.TRANSLATE_SCHEMA
    assert P.translation_max_tokens(locale, True) == P.TRANSLATE_MAX_TOKENS


def test_experiment_flags_default_off_and_are_independent(monkeypatch):
    monkeypatch.setattr("config.load_dotenv", lambda: None)
    names = ("two_step_ko", "natural_style_ko", "field_glossary")
    for name in names:
        monkeypatch.delenv(name.upper(), raising=False)
    assert experiment_flags() == dict.fromkeys(names, False)
    for enabled in names:
        with monkeypatch.context() as scoped:
            scoped.setenv(enabled.upper(), "1")
            expected = {name: name == enabled for name in names}
            assert experiment_flags() == expected
            configured = Settings.from_env()
            assert {name: getattr(configured, name) for name in names} == expected


@pytest.mark.parametrize("flags", [
    {"two_step_ko": False, "natural_style_ko": False, "field_glossary": False},
    {"two_step_ko": True, "natural_style_ko": False, "field_glossary": True},
])
def test_ready_reports_effective_experiment_flags(settings, flags):
    with TestClient(create_app(dataclasses.replace(settings, **flags), MockClient())) as client:
        response = client.get("/health/ready")
    assert response.status_code == 200
    assert {name: response.json()["runtime_config"][name] for name in flags} == flags
