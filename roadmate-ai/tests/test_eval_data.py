import json
from pathlib import Path

import pytest

import prompting as P
from validation import looks_like_other_language, validate_translation

ROOT = Path(__file__).resolve().parents[1]
POI_IDS = {p["poi_id"] for p in json.loads((ROOT / "packs" / "poi.demo.json").read_text(encoding="utf-8"))["places"]}


def load(name):
    return [json.loads(line) for line in (ROOT / "eval" / name).read_text(encoding="utf-8").splitlines() if line.strip()]


DEV, TEST, GENERAL = load("dev.jsonl"), load("test.locked.jsonl"), load("general.jsonl")


def test_sizes_and_unique_ids():
    assert len(DEV) == 100 and len(TEST) == 60 and len(GENERAL) == 180
    ids = [c["id"] for c in DEV + TEST + GENERAL]
    assert len(ids) == len(set(ids))


def test_no_leak_between_splits_and_prompt_examples():
    # 검증: 그룹·문장이 dev/test/general/few-shot 사이에 겹치지 않는다
    assert not {c["group_id"] for c in DEV} & {c["group_id"] for c in TEST}
    norm = lambda s: "".join(s.lower().split())  # noqa: E731
    shots = {norm(e["input"]["utterance"]) for e in P.examples("translate")} | {
        norm(e["input"]["utterance"]) for e in P.examples("destination")} | {
        norm(e["input"]["english"]) for e in P.examples("polish")}
    texts = [norm(c["text"]) for c in DEV + TEST + GENERAL]
    assert len(texts) == len(set(texts)) and not set(texts) & shots


def test_general_set_is_in_declared_language():
    # 검증: 일반 평가셋 문장이 버튼 언어와 같아야 정상 번역 경로를 시험한다
    assert not [c["id"] for c in GENERAL if looks_like_other_language(c["text"], c["source_locale"])]


@pytest.mark.parametrize("c", [c for c in DEV + TEST if c["task"] == "translate" and c.get("reference")], ids=lambda c: c["id"])
def test_references_pass_validators(c):
    out = {"status": "ok", "translated_text": c["reference"], "clarification_question": None}
    assert validate_translation(out, c["text"], c["source_locale"], c["target_locale"]) == []
    for term in c.get("must_include", []):
        assert any(t.lower() in c["reference"].lower() for t in term.split("|")), term
    for term in c.get("must_not_include", []):
        assert term.lower() not in c["reference"].lower(), term


@pytest.mark.parametrize("c", [c for c in DEV + TEST + GENERAL if c["task"] == "destination"], ids=lambda c: c["id"])
def test_destination_cases_reference_known_pois(c):
    for p in [c.get("expect_poi"), *c.get("expect_poi_any", [])]:
        assert p is None or p in POI_IDS


def test_wrong_language_cases_detected_by_code():
    for c in DEV + TEST:
        if c["group_id"] in ("wrong_lang_ko_in_en", "wrong_lang_en_in_ko", "t_wrong_lang"):
            assert looks_like_other_language(c["text"], c["source_locale"])
