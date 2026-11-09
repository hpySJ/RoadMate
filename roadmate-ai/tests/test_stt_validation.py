"""Synthetic STT cases; evaluation sentences are not fixtures here."""
import pytest

from validation import extract_numbers, fuel_polarity, neg_level, space_english_number_boundaries, validate_translation


def issues(source, target):
    return validate_translation(
        {"status": "ok", "translated_text": target, "clarification_question": None},
        source, "en-US", "ko-KR",
    )


@pytest.mark.parametrize("contracted", [
    "don't", "doesn't", "didn't", "can't", "couldn't", "won't", "wouldn't",
    "isn't", "aren't", "wasn't", "weren't", "shouldn't", "mustn't", "needn't",
    "haven't", "hasn't", "hadn't", "shan't", "daren't",
])
def test_missing_apostrophe_preserves_negation(contracted):
    for spelling in (contracted, contracted.replace("'", ""), contracted.replace("'", "’")):
        assert neg_level(f"we {spelling} use it", "en-US") == 2


@pytest.mark.parametrize("word", ["cantilever", "wontons", "dontknow", "scant", "isntit", "account"])
def test_negative_contraction_requires_a_whole_word(word):
    assert neg_level(word, "en-US") == 0
    assert fuel_polarity(f"{word} diesel", "en-US") == {"fuel_diesel": {True}}


def test_stt_prohibition_accepts_translation_and_still_blocks_lost_negation():
    source = "please dont fill the tank just forty two thousand won"
    assert issues(source, "가득 넣지 말고 4만 2천 원어치만 넣어 주세요.") == []
    assert "negation_mismatch" in issues(source, "가득 넣고 4만 2천 원어치 넣어 주세요.")


@pytest.mark.parametrize("source", [
    "dont use diesel use gasoline instead",
    "we cant use diesel use gasoline instead",
    "we wont take diesel use gasoline instead",
    "diesel isnt allowed use gasoline instead",
    "diesel wasnt allowed use gasoline instead",
])
def test_stt_fuel_negation_keeps_direction(source):
    assert fuel_polarity(source, "en-US") == {"fuel_diesel": {False}, "fuel_gasoline": {True}}
    assert issues(source, "경유 말고 휘발유를 넣어 주세요.") == []
    assert any(i.startswith("fuel_polarity_mismatch:") for i in issues(source, "휘발유 말고 경유를 넣어 주세요."))


@pytest.mark.parametrize("stt,clock", [
    ("925 a m", "9:25 a.m."), ("1235 p m", "12:35 p.m."),
    ("1200 a m", "12:00 a.m."), ("1200 p m", "12:00 p.m."),
    ("105 p m", "1:05 p.m."), ("0925 A M", "9:25 a.m."),
])
def test_compact_clock_matches_standard_clock(stt, clock):
    assert extract_numbers(f"return at {stt}", "en-US") == extract_numbers(f"return at {clock}", "en-US")


@pytest.mark.parametrize("source", [
    "925", "at 925", "925 minutes", "A925 a m", "925x a m", "925 a month",
    "9.25 a m", "925-123-4567", "925-123-4567 a m", "1925 a m",
    "1360 p m", "1265 p m", "0000 a m", "+925 a m", "-925 a m",
])
def test_compact_clock_does_not_guess_from_quantities_or_invalid_hours(source):
    assert not any(n.kind == "time" for n in extract_numbers(source, "en-US"))


def test_compact_clock_validates_minutes_and_meridiem():
    source = "please arrive at 925 a m"
    assert issues(source, "오전 9시 25분에 와 주세요.") == []
    assert any(i.startswith("number_") for i in issues(source, "오후 9시 25분에 와 주세요."))
    assert any(i.startswith("number_") for i in issues(source, "오전 9시 35분에 와 주세요."))


@pytest.mark.parametrize("stt,normal", [
    ("42 thousand", "42000"), ("42 thousand won", "42,000 won"),
    ("63 thousand 250 won", "63,250 won"), ("63 thousand and 250 won", "63,250 won"),
    ("63 thousand 250", "63250"), ("2 thousand 7 dollars", "2,007 dollars"),
    ("63 thousand 250 won.", "63,250 won."), ("1.5 thousand won", "1,500 won"),
])
def test_digit_thousand_compounds_preserve_value_and_currency(stt, normal):
    assert extract_numbers(stt, "en-US") == extract_numbers(normal, "en-US")


@pytest.mark.parametrize("source", [
    "63 thousand cars 250 won", "63 thousand, 250 won", "63 thousand 1250 won",
    "63 thousand 250.5 won", "63 thousand 250 100 won",
])
def test_thousand_compound_does_not_merge_unrelated_or_malformed_numbers(source):
    assert 63250 not in [n.value for n in extract_numbers(source, "en-US")]


@pytest.mark.parametrize("tail", ["hundred won", "million won", "/4 won", " / 4 won", "and a half won", "twenty won"])
def test_thousand_remainder_does_not_consume_part_of_a_longer_number(tail):
    assert 2003 not in [n.value for n in extract_numbers(f"2 thousand 3{'' if tail.startswith('/') else ' '}{tail}", "en-US")]


def test_thousand_compound_does_not_join_different_currencies():
    source = "please pay $2 thousand and 300 won"
    numbers = extract_numbers(source, "en-US")
    assert [(n.value, n.unit) for n in numbers] == [(2000, "USD"), (300, "KRW")]
    assert any(i.startswith("number_") for i in issues(source, "2천300달러를 내 주세요."))


def test_compound_money_validation_still_checks_amount_and_currency():
    source = "please charge 63 thousand 250 won"
    assert issues(source, "6만 3천250원을 청구해 주세요.") == []
    assert any(i.startswith("number_") for i in issues(source, "6만 3천500원을 청구해 주세요."))
    assert any(i.startswith("number_") for i in issues(source, "6만 3천250달러를 청구해 주세요."))


@pytest.mark.parametrize("source", ["42 thousand one", "42 thousand1", "63 thousand250red one", "one red car"])
def test_ambiguous_one_or_digit_one_is_not_restored_to_currency(source):
    assert all(n.unit != "KRW" for n in extract_numbers(source, "en-US"))


@pytest.mark.parametrize("joined, spaced, value, unit", [
    ("just42 thousand1", "just 42 thousand 1", 42001, None),
    ("is63 thousand250red one", "is 63 thousand 250 red one", 63250, None),
    ("63 thousand 250red one", "63 thousand 250 red one", 63250, None),
    ("about42thousand250won", "about 42 thousand 250 won", 42250, "KRW"),
    ("PAY18thousand75DOLLARS", "PAY 18 thousand 75 DOLLARS", 18075, "USD"),
])
def test_scale_anchored_boundaries_preserve_every_word_digit_and_currency(joined, spaced, value, unit):
    assert space_english_number_boundaries(joined) == spaced
    assert joined.replace(" ", "") == spaced.replace(" ", "")
    assert space_english_number_boundaries(spaced) == spaced
    nums = extract_numbers(joined, "en-US")
    assert nums == extract_numbers(spaced, "en-US")
    assert [(n.value, n.unit) for n in nums] == [(value, unit)]


@pytest.mark.parametrize("source", [
    "B2 A320 GPS", "b2 a320 gps", "abc42thousand250", "ABC42thousand250", "gps42thousand250",
    "B2 thousand250", "A320 thousand250", "part42 thousand250", "just42", "is63red", "42kg",
    "9:25am", "925am", "2 thousand 3/4 won",
])
def test_joined_boundaries_do_not_reinterpret_identifiers_or_unanchored_digits(source):
    assert space_english_number_boundaries(source) == source


def test_joined_boundaries_preserve_decimal_remainder_without_guessing_its_value():
    source = "12thousand250.5won"
    assert space_english_number_boundaries(source) == "12 thousand 250.5 won"
    assert 12250.5 not in [n.value for n in extract_numbers(source, "en-US")]


def test_attached_digit_one_is_not_dropped_as_a_misheard_currency():
    source = "please charge just42 thousand1"
    assert issues(source, "4만 2천1원만 청구해 주세요.") == []
    assert "number_missing:42001" in issues(source, "4만 2천 원만 청구해 주세요.")


def test_joined_money_preserves_amount_currency_and_fuel_checks():
    source = "please dont add diesel just42 thousand250won of gasoline"
    assert issues(source, "경유 말고 휘발유를 4만 2천250원어치 넣어 주세요.") == []
    assert any(i.startswith("number_") for i in issues(source, "경유 말고 휘발유를 4만 2천500원어치 넣어 주세요."))
    assert any(i.startswith("number_") for i in issues(source, "경유 말고 휘발유를 4만 2천250달러어치 넣어 주세요."))
    assert any(i.startswith("fuel_polarity_mismatch:") for i in issues(source, "휘발유 말고 경유를 4만 2천250원어치 넣어 주세요."))


def test_joined_money_does_not_merge_distinct_currencies():
    assert [(n.value, n.unit) for n in extract_numbers("pay $2thousand and 300won", "en-US")] == [
        (2000, "USD"), (300, "KRW")]
