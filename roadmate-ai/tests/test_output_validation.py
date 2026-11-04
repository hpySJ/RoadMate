import pytest

from validation import extract_numbers, validate_translation


def v(src, sl, tgt, tl):
    return validate_translation({"status": "ok", "translated_text": tgt, "clarification_question": None}, src, sl, tl)


@pytest.mark.parametrize("src,sl,tgt,tl", [
    ("Please put in 30,000 won worth of gasoline, not diesel.", "en-US", "경유 말고 휘발유로 3만 원어치 넣어 주세요.", "ko-KR"),
    ("5만 원이에요.", "ko-KR", "It is fifty thousand won.", "en-US"),
    ("다친 사람이 없어요.", "ko-KR", "No one is hurt.", "en-US"),
    ("Turn right at the gas station.", "en-US", "주유소에서 우회전하세요.", "ko-KR"),
    ("Can I park here until 3 p.m.?", "en-US", "오후 3시까지 여기 주차해도 돼요?", "ko-KR"),
    ("15:30에 반납할게요.", "ko-KR", "I will return it at 3:30 p.m.", "en-US"),
    ("Call 010-1234-5678.", "en-US", "010-1234-5678로 전화하세요.", "ko-KR"),
    ("삼만 오천 원입니다.", "ko-KR", "It is 35,000 won.", "en-US"),
    ("I left my phone in the car.", "en-US", "차에 휴대폰을 두고 왔어요.", "ko-KR"),
    ("That is right.", "en-US", "맞아요.", "ko-KR"),
    ("여기로 오지 마세요.", "ko-KR", "Please don't come here.", "en-US"),
    ("Fill it up with gas.", "en-US", "기름 가득 넣어 주세요.", "ko-KR"),
])
def test_valid_pairs(src, sl, tgt, tl):
    assert v(src, sl, tgt, tl) == []


@pytest.mark.parametrize("src,sl,tgt,tl,issue", [
    ("Please put in 30,000 won worth of gasoline, not diesel.", "en-US", "경유로 3만 원어치 넣어 주세요.", "ko-KR", "term_missing:fuel_gasoline"),
    ("5만 원이에요.", "ko-KR", "It is 150,000 won.", "en-US", "number_missing:50000"),
    ("가득 말고 3만 원만 넣어 주세요.", "ko-KR", "Fill it up, please.", "en-US", "number_missing:30000"),
    ("다친 사람이 없어요.", "ko-KR", "Someone is hurt.", "en-US", "negation_mismatch"),
    ("여기로 오지 마세요.", "ko-KR", "Please come here.", "en-US", "negation_mismatch"),
    ("Turn right at the gas station.", "en-US", "주유소에서 좌회전하세요.", "ko-KR", "direction_missing:right"),
    ("왼쪽 차선으로 가세요.", "ko-KR", "Go to the right lane.", "en-US", "direction_mismatch:left"),
    ("Fill it up with gas.", "en-US", "휘발유 가득 넣어 주세요.", "ko-KR", "term_added:fuel_gasoline"),
    ("Hello.", "en-US", "Hello.", "ko-KR", "wrong_language"),
    ("Hello.", "en-US", "Translation: 안녕하세요.", "ko-KR", "extra_text"),
    ("Hello.", "en-US", "你好 안녕하세요.", "ko-KR", "foreign_script"),
])
def test_invalid_pairs(src, sl, tgt, tl, issue):
    assert issue in v(src, sl, tgt, tl)


def test_clarification_rules():
    q = {"status": "clarification_required", "translated_text": None, "clarification_question": "Could you say it again?"}
    assert validate_translation(q, "Fill it with um", "en-US", "ko-KR") == []
    assert validate_translation({**q, "clarification_question": "다시 말씀해 주세요."}, "Fill it with um", "en-US", "ko-KR") == ["clarification_wrong_language"]
    assert validate_translation({**q, "translated_text": "x"}, "x", "en-US", "ko-KR") == ["mixed_output"]
    assert validate_translation({"status": "maybe"}, "x", "en-US", "ko-KR") == ["bad_status"]


@pytest.mark.parametrize("text,loc,values", [
    ("3만 5천 원이요", "ko-KR", [35000]),
    ("만 원만 넣어 주세요.", "ko-KR", [10000]),
    ("이 사람이 운전했어요.", "ko-KR", []),
    ("오후 두 시 반에 만나요.", "ko-KR", [2, 30]),
    ("thirty-five thousand won", "en-US", [35000]),
    ("50k won", "en-US", [50000]),
    ("twenty one people", "en-US", [21]),
    ("someone left", "en-US", []),
])
def test_number_parsing(text, loc, values):
    assert [n.value for n in extract_numbers(text, loc)] == values
