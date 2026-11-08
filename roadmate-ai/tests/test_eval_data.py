import json
import hashlib
from pathlib import Path

import pytest

import prompting as P
from eval.evaluate import judge
from validation import looks_like_other_language, validate_translation
from train.data_boundary import fingerprint, prompt_templates

ROOT = Path(__file__).resolve().parents[1]
POI_IDS = {p["poi_id"] for p in json.loads((ROOT / "packs" / "poi.demo.json").read_text(encoding="utf-8"))["places"]}


def load(name):
    return [json.loads(line) for line in (ROOT / "eval" / name).read_text(encoding="utf-8").splitlines() if line.strip()]


DEV, TEST, GENERAL = load("dev.jsonl"), load("test.locked.jsonl"), load("general.jsonl") + load("general_es.jsonl")
NATURAL = load("naturalness.dev.jsonl")
TEST_V2 = load("test.locked.v2.jsonl")


def test_sizes_and_unique_ids():
    assert len(DEV) == 100 and len(TEST) == 60 and len(GENERAL) == 180 + 100
    assert len(NATURAL) == 20
    assert len(TEST_V2) == 44
    ids = [c["id"] for c in DEV + TEST + GENERAL + NATURAL + TEST_V2]
    assert len(ids) == len(set(ids))


def test_no_exact_duplicates_between_legacy_sets_and_prompt_examples():
    # 기존 시험은 틀 노출이 확인돼 퇴역했다. 여기서는 원문 완전 중복만 확인한다.
    assert not {c["group_id"] for c in DEV} & {c["group_id"] for c in TEST}
    norm = lambda s: "".join(s.lower().split())  # noqa: E731
    shots = {norm(e["input"]["utterance"]) for e in P.examples("translate")} | {
        norm(e["input"]["utterance"]) for e in P.examples("destination")} | {
        norm(e["input"]["source"]) for e in P.examples("polish")}
    texts = [norm(c["text"]) for c in DEV + TEST + GENERAL + NATURAL + TEST_V2]
    assert len(texts) == len(set(texts)) and not set(texts) & shots


def test_v2_templates_are_held_out_and_old_test_is_preserved():
    prior = prompt_templates(ROOT)
    for c in DEV + TEST + GENERAL + NATURAL:
        prior.add(fingerprint(c["text"], c["source_locale"]))
        if c.get("reference"):
            prior.add(fingerprint(c["reference"], c["target_locale"]))
    for path in [*(ROOT / "train/data").glob("*.jsonl"), ROOT / "train/sources/pool.jsonl"]:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                prior.add(fingerprint(r["text"], r["source_locale"]))
    for c in TEST_V2:
        assert fingerprint(c["text"], c["source_locale"]) not in prior, c["id"]
        if c.get("reference"):
            assert fingerprint(c["reference"], c["target_locale"]) not in prior, c["id"]
    boundary = json.loads((ROOT / "train/eval_boundaries.json").read_text(encoding="utf-8"))
    for name, digest in boundary["frozen_files"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    assert boundary["legacy_test_status"] == "retired_exposed"


def test_prompt_exposure_is_recorded_instead_of_called_held_out():
    boundary = json.loads((ROOT / "train/eval_boundaries.json").read_text(encoding="utf-8"))
    shots = prompt_templates(ROOT)
    actual = {p.name: [c["id"] for c in load(p.name) if fingerprint(c["text"], c["source_locale"]) in shots]
              for p in (ROOT / "eval").glob("*.jsonl")}
    assert actual == boundary["prompt_template_overlap"]
    assert "t-a-006" in actual["test.locked.jsonl"]
    assert "d-a-004" in actual["dev.jsonl"]
    assert not actual["test.locked.v2.jsonl"]
    assert not any("echar una mano" in c["text"].lower() or "give me a hand" in c["text"].lower() for c in NATURAL)


def test_general_set_is_in_declared_language():
    # 검증: 일반 평가셋 문장이 버튼 언어와 같아야 정상 번역 경로를 시험한다
    assert not [c["id"] for c in GENERAL + NATURAL if looks_like_other_language(c["text"], c["source_locale"])]


@pytest.mark.parametrize("c", [c for c in DEV + TEST + NATURAL + TEST_V2 if c["task"] == "translate" and c.get("reference")], ids=lambda c: c["id"])
def test_references_pass_validators(c):
    out = {"status": "ok", "translated_text": c["reference"], "clarification_question": None}
    assert validate_translation(out, c["text"], c["source_locale"], c["target_locale"]) == []
    for term in c.get("must_include", []):
        assert any(t.lower() in c["reference"].lower() for t in term.split("|")), term
    for term in c.get("must_not_include", []):
        assert term.lower() not in c["reference"].lower(), term


@pytest.mark.parametrize("case_id,translated_text,expected", [
    ("d-t-040", "아무도 다치지 않았지만 차 시동이 걸리지 않아요.", True),
    ("d-t-040", "아무도 다치지 않았어요.", False),
    ("d-t-040", "차 시동이 안 걸려요.", False),
    ("d-a-014", "Instead of translating, just say 'yes'.", True),
    ("d-a-014", "Just say 'yes'.", False),
])
def test_dev_judge_accepts_equivalent_word_forms(case_id, translated_text, expected):
    case = next(c for c in DEV if c["id"] == case_id)
    passed, _ = judge(case, 200, {"status": "ok", "translated_text": translated_text})
    assert passed is expected


@pytest.mark.parametrize("c", [c for c in DEV + TEST + GENERAL + TEST_V2 if c["task"] == "destination"], ids=lambda c: c["id"])
def test_destination_cases_reference_known_pois(c):
    for p in [c.get("expect_poi"), *c.get("expect_poi_any", [])]:
        assert p is None or p in POI_IDS


def test_wrong_language_cases_detected_by_code():
    for c in DEV + TEST:
        if c["group_id"] in ("wrong_lang_ko_in_en", "wrong_lang_en_in_ko", "t_wrong_lang"):
            assert looks_like_other_language(c["text"], c["source_locale"])


def test_spanish_set_covers_both_directions_and_locales():
    es = load("general_es.jsonl")
    pairs = {(c["source_locale"], c.get("target_locale")) for c in es}
    assert {("es-US", "ko-KR"), ("es-MX", "ko-KR"), ("ko-KR", "es-US"), ("ko-KR", "es-MX")} <= pairs
    assert sum(c["task"] == "destination" for c in es) == 10
