from types import SimpleNamespace

import pytest

from engine import _normalize_korean_pronouns


def normalized(text, source="en-US", target="ko-KR"):
    req = SimpleNamespace(source_locale=source, target_locale=target)
    out = {"status": "ok", "translated_text": text, "clarification_question": None}
    return _normalize_korean_pronouns(out, req)["translated_text"]


@pytest.mark.parametrize("source", ["en-US", "es-US", "es-MX"])
@pytest.mark.parametrize("before,after", [
    ("나는 예약했어요.", "저는 예약했어요."),
    ("내가 나의 가방을 챙길게요. 나를 불러 주세요.", "제가 제 가방을 챙길게요. 저를 불러 주세요."),
    ("내 휴대전화를 찾고 있어요.", "제 휴대전화를 찾고 있어요."),
    ("우리는 기다려요. 우리가 왔으니 우리를 불러 주세요.", "저희는 기다려요. 저희가 왔으니 저희를 불러 주세요."),
    ("우리 회사와 우리 일행에 알려 주세요.", "저희 회사와 저희 일행에 알려 주세요."),
    ("그들께 부탁하고 그들에게 알려 주세요.", "그분들께 부탁하고 그분들에게 알려 주세요."),
])
def test_polite_pronouns_for_driver_to_staff(source, before, after):
    assert normalized(before, source) == after


@pytest.mark.parametrize("text", [
    "기름 냄새가 나는 것 같아요.", "소리가 나는데요.", "돈을 내 주세요.", "내 드릴게요.",
    "내일 내부를 확인할게요.", "우리나라에 있어요.", "당신이 확인해 주세요.",
    "우리말로 알려 주세요.", "우리은행으로 가 주세요.", "우리회사에 연락해 주세요.",
    "돈을 내 볼게요.", "주차비를 내 주려고 해요.", "돈을 내 줍니다.", "내 볼게요.",
    "차를 우리는 동안 기다려요.", "나는 게 무서워요.",
    '직원이 "나는 내가 할게요"라고 했어요.', "‘내 차와 우리 회사’라고 했어요.",
    "그는 「나는 내일 가요」라고 했어요.", '직원이 "나는 내 차를',
    '경고음이 "삐삐" 나는 이유를 알려 주세요.',
])
def test_verbs_compounds_and_quotations_are_preserved(text):
    assert normalized(text) == text


def test_quoted_content_stays_unchanged_while_surrounding_pronouns_change():
    assert normalized('내가 "나는 내 차로 갈게요"라고 말했어요.') == '제가 "나는 내 차로 갈게요"라고 말했어요.'


@pytest.mark.parametrize("source,target", [("ko-KR", "en-US"), ("ko-KR", "es-US"), ("ko-KR", "ko-KR")])
def test_other_directions_are_not_normalized(source, target):
    assert normalized("나는 내 차로 갈게요.", source, target) == "나는 내 차로 갈게요."


def test_non_translation_outputs_are_untouched():
    req = SimpleNamespace(source_locale="en-US", target_locale="ko-KR")
    out = {"status": "clarification_required", "translated_text": None, "clarification_question": "내가 맞나요?"}
    assert _normalize_korean_pronouns(out, req) is out
