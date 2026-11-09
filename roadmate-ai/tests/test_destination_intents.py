# 테스트
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from engine import Engine
from inference import MockClient


OTHER = {"intent": "other", "query_original": None, "query_ko_candidate": None,
         "region_hint": None, "category_hint": None, "needs_confirmation": False}


class RecordingModel(MockClient):
    is_mock = False

    async def chat_json(self, messages, *args, **kwargs):
        self.messages = messages
        return await super().chat_json(messages, *args, **kwargs)


def parse(text, locale, model_output=OTHER):
    model = RecordingModel(outputs=[dict(model_output)])
    engine = Engine(model, SimpleNamespace())
    request = SimpleNamespace(text=text, source_locale=locale)
    out = asyncio.run(engine.parse_destination(request, time.monotonic() + 5))
    assert model.calls == 1
    assert json.loads(model.messages[-1]["content"]) == {"source_locale": locale, "utterance": text}
    return out


@pytest.mark.parametrize("text,locale", [
    ("근처 편의점 찾아 줘", "ko-KR"),
    ("가까운 약국을 찾아 주세요.", "ko-KR"),
    ("근처 충전소를 찾아 주세요.", "ko-KR"),
    ("가까운 주차장을 찾아 줘.", "ko-KR"),
    ("Find a convenience store near here.", "en-US"),
    ("Please find a pharmacy near here.", "en-US"),
    ("Find a charging station nearby.", "en-US"),
    ("Find a gas station near here.", "en-US"),
    ("Busque una farmacia cerca.", "es-MX"),
    ("Busque una gasolinera cerca, por favor.", "es-US"),
    ("Busque un estacionamiento cerca.", "es-MX"),
])
def test_nearby_category_requests_keep_model_other(text, locale):
    out = parse(text, locale)
    assert {key: out[key] for key in OTHER} == OTHER
    assert out["status"] == "not_destination"
    assert out["candidates"] == [] and out["clarification_question"] is None


@pytest.mark.parametrize("text,locale,original,category", [
    ("가까운 약국을 찾아 주세요.", "ko-KR", "약국", "약국"),
    ("Find a charging station nearby.", "en-US", "charging station", "충전소"),
    ("Busque un estacionamiento cerca.", "es-MX", "estacionamiento", "주차장"),
])
def test_model_extracted_category_requires_confirmation(text, locale, original, category):
    extracted = {**OTHER, "intent": "set_destination", "query_original": original,
                 "query_ko_candidate": category, "category_hint": "other", "needs_confirmation": True}
    out = parse(text, locale, extracted)
    assert out["intent"] == "set_destination"
    assert out["query_original"] == original and out["query_ko_candidate"] == category
    assert out["needs_confirmation"] is True
    assert out["status"] == "clarification_required"
    assert out["candidates"] == [] and out["clarification_question"]


@pytest.mark.parametrize("text,locale", [
    ("근처 약국은 찾지 마세요.", "ko-KR"),
    ("어제 가까운 약국을 찾아갔어요.", "ko-KR"),
    ("근처 약국을 찾고 싶지 않아요.", "ko-KR"),
    ("Don't find a gas station near here.", "en-US"),
    ("I found a pharmacy near here yesterday.", "en-US"),
    ("There is a parking lot near here.", "en-US"),
    ("No busque una farmacia cerca.", "es-MX"),
    ("Ayer busqué una farmacia cerca.", "es-US"),
])
def test_category_fact_history_and_negation_do_not_create_navigation(text, locale):
    out = parse(text, locale)
    assert out["intent"] == "other" and out["status"] == "not_destination"
    assert out["candidates"] == []


@pytest.mark.parametrize("text", [
    "Find Mirae Pharmacy near here.",
    "Don't find a pharmacy near here; take me to Mirae Pharmacy.",
])
def test_model_extracted_named_place_is_preserved(text):
    named = {**OTHER, "intent": "set_destination", "query_original": "Mirae Pharmacy",
             "query_ko_candidate": "미래약국", "category_hint": "other", "needs_confirmation": True}
    out = parse(text, "en-US", named)
    assert out["query_original"] == "Mirae Pharmacy"
    assert out["query_ko_candidate"] == "미래약국"
    assert out["needs_confirmation"] is True
