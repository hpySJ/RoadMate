import json

import prompting as P


def test_messages_shape_and_glossary():
    m = P.translate_messages("en-US", "ko-KR", "gas_station", "Fill it up with gasoline at the gas station.")
    assert m[0]["role"] == "system" and m[-1]["role"] == "user"
    en_examples = [e for e in P.examples("translate") if "en-US" in (e["input"]["source_locale"], e["input"]["target_locale"])]
    assert len(m) == 2 + 2 * len(en_examples)
    payload = json.loads(m[-1]["content"])
    assert payload["utterance"] == "Fill it up with gasoline at the gas station."
    assert payload["glossary"] == {"gasoline": "휘발유", "gas station": "주유소", "Fill it up": "가득"}


def test_gas_alone_not_mapped_to_fuel():
    assert P.select_glossary("Fill it with gas", "en-US", "ko-KR") == {}


def test_glossary_spacing_and_place_names():
    assert P.select_glossary("자차 보험에 가입하세요.", "ko-KR", "en-US") == {"자차 보험": "collision damage waiver"}
    assert P.select_glossary("성산일출봉까지 한 시간 걸려요.", "ko-KR", "en-US") == {"성산일출봉": "Seongsan Ilchulbong"}
    assert P.select_glossary("My shoulder hurts.", "en-US", "ko-KR") == {}


def test_polish_examples_keep_meaning_checks():
    from validation import validate_translation
    for ex in P.examples("polish"):
        i, o = ex["input"], ex["output"]
        for ko in (i["korean"], o["text"]):
            out = {"status": "ok", "translated_text": ko, "clarification_question": None}
            assert validate_translation(out, i["source"], i["source_locale"], "ko-KR") == [], ko
        assert P.select_glossary(i["source"], i["source_locale"], "ko-KR") == i["glossary"]
    m = P.polish_messages("es-US", "Hola.", "안녕하세요.", {})
    assert m[0]["role"] == "system" and json.loads(m[-1]["content"]) == {
        "source_locale": "es-US", "source": "Hola.", "korean": "안녕하세요.", "glossary": {}}


def test_place_hint_wins_over_area_inside_it():
    g = P.select_glossary("Seongsan Ilchulbong is far.", "en-US", "ko-KR")
    assert g == {"Seongsan Ilchulbong": "성산일출봉"}
    assert P.select_glossary("한라산 등산은 아침 일찍 하세요.", "ko-KR", "en-US") == {"한라산": "Hallasan"}


def test_korean_money_hints():
    assert P.select_glossary("만 오천 원 거슬러 드릴게요.", "ko-KR", "en-US") == {"만 오천 원": "15,000 won"}
    assert P.select_glossary("총 12만 3천 원 나왔습니다.", "ko-KR", "en-US") == {"12만 3천 원": "123,000 won"}
    assert P.select_glossary("회원이 아니면 여기에 서세요.", "ko-KR", "en-US") == {}
    assert P.select_glossary("구원 투수예요.", "ko-KR", "en-US") == {}


def test_time_hints():
    assert P.select_glossary("We'll be back by 6 p.m.", "en-US", "ko-KR") == {"6 p.m.": "오후 6시"}
    assert P.select_glossary("Meet at 3:30 pm.", "en-US", "ko-KR") == {"3:30 pm": "오후 3시 30분"}
    assert P.select_glossary("오전 10시까지 와 주세요.", "ko-KR", "en-US") == {"오전 10시": "10 a.m."}
    assert P.select_glossary("오후 3시 반에 만나요.", "ko-KR", "en-US") == {"오후 3시 반": "3:30 p.m."}


def test_schema_blocks_cjk_in_generated_text():
    import re
    branch = P.TRANSLATE_SCHEMA["properties"]["translated_text"]["anyOf"][0]
    pat = branch["pattern"]
    assert pat.startswith("^") and pat.endswith("$")  # llama.cpp 문법 변환 조건
    assert re.fullmatch(pat, "신용카드로 결제할 수 있어요? It's 3 p.m.")
    assert not re.fullmatch(pat, "信用카드로 결제할 수 있어요?")
    assert not re.fullmatch(pat, "クレジットカード")
    assert not re.fullmatch(pat, 'He said "hi"')


def test_examples_pass_own_validators():
    from validation import validate_translation
    for ex in P.examples("translate"):
        i = ex["input"]
        assert validate_translation(ex["output"], i["utterance"], i["source_locale"], i["target_locale"]) == []
        assert P.select_glossary(i["utterance"], i["source_locale"], i["target_locale"]) == i["glossary"]


def test_spanish_glossary_hints():
    assert P.select_glossary("Póngale diesel, por favor.", "es-MX", "ko-KR") == {"diesel": "경유"}  # 강세 없이 말해도
    assert P.select_glossary("Tengo una llanta ponchada.", "es-US", "ko-KR") == {"llanta ponchada": "펑크"}
    assert P.select_glossary("Nos vemos a las 3 de la tarde.", "es-US", "ko-KR") == {"3 de la tarde": "오후 3시"}
    assert P.select_glossary("Llévenos al Aeropuerto de Jeju.", "es-US", "ko-KR") == {"Aeropuerto de Jeju": "제주국제공항"}
    assert P.select_glossary("함덕해수욕장 가 주세요.", "ko-KR", "es-US") == {"함덕해수욕장": "Playa Hamdeok"}
    assert P.select_glossary("오후 3시 반에 만나요.", "ko-KR", "es-US") == {"오후 3시 반": "3:30 p. m."}
    assert P.select_glossary("만 오천 원 거슬러 드릴게요.", "ko-KR", "es-MX") == {"만 오천 원": "15,000 wones"}


def test_few_shot_uses_same_language_pair_only():
    for src, tgt, other in (("es-US", "ko-KR", "en-US"), ("ko-KR", "es-MX", "en-US"), ("en-US", "ko-KR", "es-US")):
        shots = [json.loads(m["content"]) for m in P.translate_messages(src, tgt, "general", "x")[1:-1] if m["role"] == "user"]
        assert shots and not [s for s in shots if other in (s["source_locale"], s["target_locale"])]


def test_midnight_hints_and_ambiguous_spanish_words():
    assert P.select_glossary("Vuelva a las 12 de la noche.", "es-US", "ko-KR") == {"12 de la noche": "자정"}
    assert P.select_glossary("Come back at 12 p.m.", "en-US", "ko-KR") == {"12 p.m.": "정오"}
    # 검증: 문맥 없이는 뜻이 갈리는 단어(통행료/요금소, 연료통/보증금)와 보장 범위가 다른 보험 이름은 힌트로 강제하지 않는다
    assert P.select_glossary("Me cobraron un peaje.", "es-US", "ko-KR") == {}
    assert P.select_glossary("El depósito está vacío.", "es-US", "ko-KR") == {}
    assert "cobertura amplia" not in P.select_glossary("Quiero cobertura amplia.", "es-US", "ko-KR")
    assert P.select_glossary("보증금은 30만 원이에요.", "ko-KR", "es-US")["보증금"] == "depósito de garantía"
