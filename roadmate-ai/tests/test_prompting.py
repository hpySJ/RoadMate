import json

import prompting as P


def test_messages_shape_and_glossary():
    m = P.translate_messages("en-US", "ko-KR", "gas_station", "Fill it up with gasoline at the gas station.")
    assert m[0]["role"] == "system" and m[-1]["role"] == "user"
    assert len(m) == 2 + 2 * len(P.examples("translate"))
    payload = json.loads(m[-1]["content"])
    assert payload["utterance"] == "Fill it up with gasoline at the gas station."
    assert payload["glossary"] == {"gasoline": "휘발유", "gas station": "주유소", "Fill it up": "가득"}


def test_gas_alone_not_mapped_to_fuel():
    assert P.select_glossary("Fill it with gas", "en-US", "ko-KR") == {}


def test_glossary_spacing_and_place_names():
    assert P.select_glossary("자차 보험에 가입하세요.", "ko-KR", "en-US") == {"자차 보험": "collision damage waiver"}
    assert P.select_glossary("성산일출봉까지 한 시간 걸려요.", "ko-KR", "en-US") == {"성산일출봉": "Seongsan Ilchulbong"}
    assert P.select_glossary("My shoulder hurts.", "en-US", "ko-KR") == {}


def test_examples_pass_own_validators():
    from validation import validate_translation
    for ex in P.examples("translate"):
        i = ex["input"]
        assert validate_translation(ex["output"], i["utterance"], i["source_locale"], i["target_locale"]) == []
        assert P.select_glossary(i["utterance"], i["source_locale"], i["target_locale"]) == i["glossary"]
