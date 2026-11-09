"""Category search must survive a model's OTHER intent without selecting a branch."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from engine import Engine
from inference import MockClient


OTHER = {"intent": "other", "query_original": None, "query_ko_candidate": None,
         "region_hint": None, "category_hint": None, "needs_confirmation": False}


def parse(text, locale, model_output=OTHER):
    engine = Engine(MockClient(outputs=[dict(model_output)]), SimpleNamespace())
    request = SimpleNamespace(text=text, source_locale=locale)
    return asyncio.run(engine.parse_destination(request, time.monotonic() + 5))


@pytest.mark.parametrize("text,locale,category", [
    ("근처 편의점 찾아 줘", "ko-KR", "편의점"),
    ("가까운 약국을 찾아 주세요.", "ko-KR", "약국"),
    ("근처 충전소를 찾아 주세요.", "ko-KR", "충전소"),
    ("가까운 주차장을 찾아 줘.", "ko-KR", "주차장"),
    ("Find a convenience store near here.", "en-US", "편의점"),
    ("Please find a pharmacy near here.", "en-US", "약국"),
    ("Find a charging station nearby.", "en-US", "충전소"),
    ("Find a gas station near here.", "en-US", "주유소"),
    ("Busque una farmacia cerca.", "es-MX", "약국"),
    ("Busque una gasolinera cerca, por favor.", "es-US", "주유소"),
    ("Busque un estacionamiento cerca.", "es-MX", "주차장"),
])
def test_nearby_category_request_recovers_model_other(text, locale, category):
    out = parse(text, locale)
    assert out["intent"] == "set_destination"
    assert out["query_original"] and out["query_ko_candidate"] == category
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
def test_category_matching_keeps_requested_named_place(text):
    named = {**OTHER, "intent": "set_destination", "query_original": "Mirae Pharmacy",
             "query_ko_candidate": "미래약국", "category_hint": "other", "needs_confirmation": True}
    out = parse(text, "en-US", named)
    assert out["query_original"] == "Mirae Pharmacy"
    assert out["query_ko_candidate"] == "미래약국"
    assert out["needs_confirmation"] is True


def test_category_model_output_cannot_skip_confirmation_or_pick_a_landmark():
    invented = {**OTHER, "intent": "set_destination", "query_original": "Seongsan Sunrise Peak",
                "query_ko_candidate": "성산일출봉", "category_hint": "attraction"}
    out = parse("Please find a pharmacy near here.", "en-US", invented)
    assert out["query_ko_candidate"] == "약국" and out["needs_confirmation"] is True
    assert out["candidates"] == []
