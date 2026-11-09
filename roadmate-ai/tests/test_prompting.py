import json
import unicodedata

import pytest

import prompting as P


def test_full_rental_coverage_hint_is_korean_source_only():
    assert P.select_glossary("완전 자차도 포함인가요?", "ko-KR", "en-US")["완전 자차"] == "full rental car damage coverage option"
    assert P.select_glossary("¿La cobertura total tiene deducible?", "es-MX", "ko-KR") == {"deducible": "면책금"}


def test_manifest_versions_match_served_artifacts():
    from config import load_manifest
    from guidance import load_pack
    versions = load_manifest()["versions"]
    for name in ("translate", "destination"):
        assert versions[f"{name}_prompt"] == P.prompt(name)[0]
    assert versions["glossary"] == P.glossary()["version"]
    assert versions["rules"] == load_pack()["version"]


@pytest.mark.parametrize("locale", ["es-US", "es-MX"])
@pytest.mark.parametrize("source,surface,korean", [
    ("Vuelva a las 3 y 10 de la tarde.", "3 y 10 de la tarde", "오후 3시 10분"),
    ("Vuelva a las tres y diez de la tarde.", "tres y diez de la tarde", "오후 3시 10분"),
    ("Vuelva a las 3:01 de la tarde.", "3:01 de la tarde", "오후 3시 1분"),
    ("Es un cuarto para las tres de la tarde.", "un cuarto para las tres de la tarde", "오후 2시 45분"),
    ("Vuelva a un cuarto para las doce de la noche.", "un cuarto para las doce de la noche", "오후 11시 45분"),
    ("Vuelva a las quince horas.", "quince", "오후 3시"),
])
def test_spanish_time_hints_use_the_whole_clock_expression(locale, source, surface, korean):
    from validation import validate_translation
    assert P.select_glossary(source, locale, "ko-KR") == {surface: korean}
    out = {"status": "ok", "translated_text": korean, "clarification_question": None}
    assert validate_translation(out, source, locale, "ko-KR") == []


@pytest.mark.parametrize("locale", ["es-US", "es-MX"])
@pytest.mark.parametrize("source,expected", [
    ("Necesito dos para las ocho de la noche.", {"ocho de la noche": "오후 8시"}),
    ("Necesito cinco para las ocho de la noche.", {"ocho de la noche": "오후 8시"}),
    ("Somos cuatro para las nueve.", {}),
    ("Faltan dos minutos para las ocho de la noche.", {"dos minutos para las ocho de la noche": "오후 7시 58분"}),
    ("Necesito un cuarto para las ocho de la noche.", {"ocho de la noche": "오후 8시"}),
])
def test_count_and_deadline_are_not_minutes_before_the_hour(locale, source, expected):
    assert P.select_glossary(source, locale, "ko-KR") == expected


@pytest.mark.parametrize("locale", ["es-US", "es-MX"])
@pytest.mark.parametrize("source,expected", [
    ("Salgo diez para las ocho de la noche.", {"diez para las ocho de la noche": "오후 7시 50분"}),
    ("Te veo cinco para las nueve de la noche.", {"cinco para las nueve de la noche": "오후 8시 55분"}),
    ("Abrimos un cuarto para las diez de la mañana.", {"un cuarto para las diez de la mañana": "오전 9시 45분"}),
    ("La cita es para un cuarto para las nueve.", {}),
    ("Mi turno es para cuarto para las diez.", {}),
    ("La reserva es para un cuarto para las ocho de la noche.", {"un cuarto para las ocho de la noche": "오후 7시 45분"}),
    ("Necesito diez para las ocho de la noche.", {"ocho de la noche": "오후 8시"}),
    ("Somos diez para las nueve de la noche.", {"nueve de la noche": "오후 9시"}),
    ("Tengo veinte para las ocho de la noche.", {"ocho de la noche": "오후 8시"}),
    ("Necesito cuatro para las ocho.", {}),
    ("Quiero una reserva para cinco para las nueve.", {}),
    ("Una mesa para diez para las ocho de la noche, por favor.", {"ocho de la noche": "오후 8시"}),
    ("Reservé para veinte para las nueve.", {}),
])
def test_time_hints_distinguish_typical_minutes_from_quantities(locale, source, expected):
    assert P.select_glossary(source, locale, "ko-KR") == expected


def test_messages_shape_and_glossary():
    m = P.translate_messages("en-US", "ko-KR", "gas_station", "Fill it up with gasoline at the gas station.")
    assert m[0]["role"] == "system" and m[-1]["role"] == "user"
    en_examples = [e for e in P.examples("translate") if "en-US" in (e["input"]["source_locale"], e["input"]["target_locale"])]
    assert len(m) == 2 + 2 * len(en_examples)
    payload = json.loads(m[-1]["content"])
    assert payload["utterance"] == "Fill it up with gasoline at the gas station."
    assert payload["glossary"] == {"gasoline": "휘발유", "gas station": "주유소", "Fill it up": "가득"}


@pytest.mark.parametrize("source,target,language", [
    ("ko-KR", "es-US", "Spanish"),
    ("ko-KR", "es-MX", "Spanish"),
    ("ko-KR", "en-US", "English"),
    ("en-US", "ko-KR", "Korean"),
    ("es-US", "ko-KR", "Korean"),
])
def test_initial_request_names_target_language(source, target, language):
    payload = json.loads(P.translate_messages(source, target, "general", "x")[-1]["content"])
    assert payload["target_locale"] == target
    assert payload["target_language"] == language


def test_gas_alone_not_mapped_to_fuel():
    assert P.select_glossary("Fill it with gas", "en-US", "ko-KR") == {}


@pytest.mark.parametrize("source,surface", [
    ("Please fill the tank with gasoline.", "fill the tank"),
    ("He fills the tank with gasoline.", "fills the tank"),
    ("I filled the tank with gasoline.", "filled the tank"),
    ("I'm filling the tank with gasoline.", "filling the tank"),
    ("FILL THE TANK with gasoline, please.", "FILL THE TANK"),
])
def test_fill_the_tank_hint_preserves_fuel_without_assuming_full_amount(source, surface):
    assert P.select_glossary(source, "en-US", "ko-KR") == {surface: "연료를 채우다", "gasoline": "휘발유"}
    assert P.match_concepts(source, "en-US", {"fuel"}) == {"fuel_gasoline": "gasoline"}


@pytest.mark.parametrize("source", [
    "Fill the tank halfway with gasoline.",
    "Partially fill the tank with gasoline.",
    "Fill the tank partway with gasoline.",
    "Fill the tank to one third with gasoline.",
    "Fill the tank with only 10 liters of gasoline.",
    "Fill the tank with just 10,000 won worth of gasoline.",
    "Don't fill the tank with gasoline.",
])
def test_fill_the_tank_does_not_override_partial_limited_or_negated_fill(source):
    hints = P.select_glossary(source, "en-US", "ko-KR")
    assert "연료를 채우다" in hints.values()
    assert "가득" not in hints.values()


@pytest.mark.parametrize("text", ["a full tank of gasoline", "fill it up with gasoline", "fill up with gasoline"])
def test_explicit_full_tank_hints_still_mean_full(text):
    assert "가득" in P.select_glossary(text, "en-US", "ko-KR").values()


@pytest.mark.parametrize("locale", ["es-US", "es-MX"])
@pytest.mark.parametrize("source,surface", [
    ("Ponga gasolina regular, por favor.", "gasolina regular"),
    ("Ponga GASOLINA REGULAR, por favor.", "GASOLINA REGULAR"),
])
def test_regular_gasoline_hint_preserves_grade_and_shared_fuel_identity(locale, source, surface):
    from validation import check_critical_terms, validate_translation
    assert P.select_glossary(source, locale, "ko-KR") == {surface: "일반 휘발유"}
    assert P.match_concepts(source, locale, {"fuel"}) == {"fuel_gasoline": surface}
    out = {"status": "ok", "translated_text": "일반 휘발유를 넣어 주세요.", "clarification_question": None}
    assert validate_translation(out, source, locale, "ko-KR") == []
    assert check_critical_terms(source, locale, "경유를 넣어 주세요.", "ko-KR") == [
        "term_missing:fuel_gasoline", "term_added:fuel_diesel"]


@pytest.mark.parametrize("locale", ["es-US", "es-MX"])
def test_regular_gasoline_hint_does_not_drop_a_later_surface_or_change_negation(locale):
    from validation import check_fuel_polarity
    assert P.select_glossary("gasolina, gasolina regular", locale, "ko-KR") == {
        "gasolina": "휘발유", "gasolina regular": "일반 휘발유"}
    assert P.select_glossary("gasolina", locale, "ko-KR") == {"gasolina": "휘발유"}
    assert P.select_glossary("gasolina premium", locale, "ko-KR") == {"gasolina premium": "고급 휘발유"}
    source = "No ponga diésel, ponga gasolina regular."
    assert check_fuel_polarity(source, locale, "경유 말고 일반 휘발유를 넣어 주세요.", "ko-KR") == []
    assert check_fuel_polarity(source, locale, "일반 휘발유 말고 경유를 넣어 주세요.", "ko-KR") == [
        "fuel_polarity_mismatch:fuel_diesel", "fuel_polarity_mismatch:fuel_gasoline"]


@pytest.mark.parametrize("source,target,text,expected", [
    ("ko-KR", "en-US", "셀프 주유인가요, 직원 주유인가요?", {"셀프": "self-service", "직원 주유": "full-service"}),
    ("ko-KR", "es-US", "셀프 주유인가요, 직원 주유인가요?", {"셀프": "autoservicio", "직원 주유": "servicio completo"}),
    ("ko-KR", "es-MX", "셀프인가요?", {"셀프": "autoservicio"}),
    ("en-US", "ko-KR", "Do you offer self-service or full service?", {"self-service": "셀프 주유", "full service": "직원 주유"}),
    ("en-US", "ko-KR", "Do you offer self service or full-service?", {"self service": "셀프 주유", "full-service": "직원 주유"}),
    ("es-US", "ko-KR", "¿Es de autoservicio o servicio completo?", {"autoservicio": "셀프 주유", "servicio completo": "직원 주유"}),
    ("es-MX", "ko-KR", "¿Es de autoservicio o servicio completo?", {"autoservicio": "셀프 주유", "servicio completo": "직원 주유"}),
])
def test_service_mode_glossary(source, target, text, expected):
    assert P.select_glossary(text, source, target) == expected


def test_service_mode_preserves_station_and_does_not_infer_fuel():
    assert P.select_glossary("셀프 주유소예요.", "ko-KR", "en-US") == {
        "셀프": "self-service", "주유소": "gas station"}
    assert P.select_glossary("self service gas station", "en-US", "ko-KR") == {
        "self service": "셀프 주유", "gas station": "주유소"}
    assert P.match_concepts("autoservicio", "es-US", {"fuel"}) == {}
    assert P.match_concepts("autogás", "es-US", {"fuel"}) == {"fuel_lpg": "autogás"}


@pytest.mark.parametrize("text", ["self", "full", "service", "selfish", "fuller service"])
def test_generic_english_words_do_not_choose_service_mode(text):
    assert P.select_glossary(text, "en-US", "ko-KR") == {}


def test_glossary_spacing_and_place_names():
    assert P.select_glossary("자차 보험에 가입하세요.", "ko-KR", "en-US") == {"자차 보험": "collision damage waiver"}
    assert P.select_glossary("성산일출봉까지 한 시간 걸려요.", "ko-KR", "en-US") == {"성산일출봉": "Seongsan Ilchulbong"}
    assert P.select_glossary("My shoulder hurts.", "en-US", "ko-KR") == {}


@pytest.mark.parametrize("source,target,text,expected", [
    ("ko-KR", "en-US", "정산기는 어디에 있나요?", {"정산기": "payment machine"}),
    ("ko-KR", "es-US", "정산기는 어디에 있나요?", {"정산기": "máquina de pago"}),
    ("ko-KR", "es-MX", "정산기는 어디에 있나요?", {"정산기": "máquina de pago"}),
    ("es-US", "ko-KR", "¿Dónde está la máquina de pago?", {"máquina de pago": "정산기"}),
    ("es-MX", "ko-KR", "¿Dónde está el cajero de pago?", {"cajero de pago": "정산기"}),
])
def test_payment_machine_glossary(source, target, text, expected):
    assert P.select_glossary(text, source, target) == expected


def test_place_hint_wins_over_area_inside_it():
    g = P.select_glossary("Seongsan Ilchulbong is far.", "en-US", "ko-KR")
    assert g == {"Seongsan Ilchulbong": "성산일출봉"}
    assert P.select_glossary("한라산 등산은 아침 일찍 하세요.", "ko-KR", "en-US") == {"한라산": "Hallasan"}


def test_korean_money_hints():
    assert P.select_glossary("만 오천 원 거슬러 드릴게요.", "ko-KR", "en-US") == {"만 오천 원": "15,000 won"}
    assert P.select_glossary("총 12만 3천 원 나왔습니다.", "ko-KR", "en-US") == {"12만 3천 원": "123,000 won"}
    assert P.select_glossary("회원이 아니면 여기에 서세요.", "ko-KR", "en-US") == {}
    assert P.select_glossary("구원 투수예요.", "ko-KR", "en-US") == {}


@pytest.mark.parametrize("source_locale,text,expected", [
    ("en-US", "The total is 28,540 won.", {"28,540 won": "28,540 원"}),
    ("en-US", "It costs twenty-eight thousand won.", {"twenty-eight thousand won": "28,000 원"}),
    ("en-US", "The amount is 28 thousand won.", {"28 thousand won": "28,000 원"}),
    ("en-US", "The adjustment is 12.5 won.", {"12.5 won": "12.5 원"}),
    ("es-US", "Son 28,540 wones.", {"28,540 wones": "28,540 원"}),
    ("es-MX", "Son veintidós mil wones.", {"veintidós mil wones": "22,000 원"}),
    ("es-MX", "El total es 28 mil wones.", {"28 mil wones": "28,000 원"}),
    ("es-US", "Son 28.540 wones.", {"28.540 wones": "28,540 원"}),
    ("es-MX", "La diferencia es 12,5 wones.", {"12,5 wones": "12.5 원"}),
])
def test_foreign_won_hints_preserve_complete_amounts(source_locale, text, expected):
    from validation import extract_numbers
    assert P.select_glossary(text, source_locale, "ko-KR") == expected
    for source, target in expected.items():
        assert extract_numbers(source, source_locale) == extract_numbers(target, "ko-KR")


@pytest.mark.parametrize("source_locale,text,expected", [
    ("en-US", "It costs 28 dollars or 35 euros.", {}),
    ("es-MX", "Cuesta 28 dólares o 35 pesos.", {}),
    ("en-US", "I won 28 dollars.", {}),
    ("en-US", "It says $28 thousand won.", {}),
    ("es-US", "Dice $28 mil wones.", {}),
    ("en-US", "I paid 28,540 won and 12 dollars.", {"28,540 won": "28,540 원"}),
    ("es-MX", "Pagué 28,540 wones y 12 dólares.", {"28,540 wones": "28,540 원"}),
])
def test_money_hints_do_not_convert_other_currencies(source_locale, text, expected):
    assert P.select_glossary(text, source_locale, "ko-KR") == expected


def test_unparsed_money_format_keeps_original_utterance_without_a_hint():
    text = "Son 1.234,50 wones."
    payload = json.loads(P.translate_messages("es-US", "ko-KR", "general", text)[-1]["content"])
    assert payload["utterance"] == text
    assert payload["glossary"] == {}


@pytest.mark.parametrize("source,target,text,expected", [
    ("ko-KR", "en-US", "우회전 다음에 좌회전하세요.", {"우회전": "right turn", "좌회전": "left turn"}),
    ("ko-KR", "es-MX", "우회전 다음에 좌회전하세요.", {"우회전": "vuelta a la derecha", "좌회전": "vuelta a la izquierda"}),
    ("en-US", "ko-KR", "Turn right, then take a left turn.", {"Turn right": "우회전", "left turn": "좌회전"}),
    ("es-MX", "ko-KR", "Gire a la derecha y luego dé vuelta a la izquierda.", {"Gire a la derecha": "우회전", "vuelta a la izquierda": "좌회전"}),
    ("es-US", "ko-KR", "Puede girar a la izquierda.", {"girar a la izquierda": "좌회전"}),
    ("en-US", "ko-KR", "The right door is open.", {}),
    ("es-MX", "ko-KR", "Siga derecho por esta calle.", {}),
])
def test_turn_terms_preserve_direction_without_guessing_an_action(source, target, text, expected):
    assert P.select_glossary(text, source, target) == expected


def test_time_hints():
    assert P.select_glossary("We'll be back by 6 p.m.", "en-US", "ko-KR") == {"6 p.m.": "오후 6시"}
    assert P.select_glossary("Meet at 3:30 pm.", "en-US", "ko-KR") == {"3:30 pm": "오후 3시 30분"}
    assert P.select_glossary("오전 10시까지 와 주세요.", "ko-KR", "en-US") == {"오전 10시": "10 a.m."}
    assert P.select_glossary("오후 3시 반에 만나요.", "ko-KR", "en-US") == {"오후 3시 반": "3:30 p.m."}


@pytest.mark.parametrize("source,surface,korean", [
    ("Could you come back at a quarter past four?", "quarter past four", "4시 15분"),
    ("Come back at quarter to 4.", "quarter to 4", "3시 45분"),
    ("Come back at half past four.", "half past four", "4시 30분"),
    ("Come back at half past 4 p.m.", "half past 4 p.m.", "오후 4시 30분"),
    ("Meet at a quarter to twelve p.m.", "quarter to twelve p.m.", "오전 11시 45분"),
    ("Meet at quarter to 12 a.m.", "quarter to 12 a.m.", "오후 11시 45분"),
    ("Meet at half past twelve.", "half past twelve", "12시 30분"),
    ("Meet at quarter past four in the afternoon.", "quarter past four in the afternoon", "오후 4시 15분"),
])
def test_english_spoken_clock_hints_preserve_time_without_guessing_daypart(source, surface, korean):
    from validation import extract_numbers
    assert P.select_glossary(source, "en-US", "ko-KR") == {surface: korean}
    assert extract_numbers(surface, "en-US") == extract_numbers(korean, "ko-KR")


def test_english_spoken_clock_hint_does_not_overlap_numeric_meridiem_hint():
    assert P.select_glossary("Meet at quarter to 12 p.m. or 6 p.m.", "en-US", "ko-KR") == {
        "quarter to 12 p.m.": "오전 11시 45분", "6 p.m.": "오후 6시"}


def test_english_idiom_and_duration_do_not_get_clock_hints():
    assert P.select_glossary("Please get in one by one.", "en-US", "ko-KR") == {}
    assert P.select_glossary("Come back in half an hour.", "en-US", "ko-KR") == {}


@pytest.mark.parametrize("text", [
    "Return at 13 p.m.", "Return at 6:99 p.m.", "Return at 0 a.m.",
    "Return at 1.5 p.m.", "Return at 3:6 p.m.", "Return at 1,000 p.m.",
])
def test_invalid_english_clock_does_not_get_a_normalizing_hint(text):
    assert P.select_glossary(text, "en-US", "ko-KR") == {}


@pytest.mark.parametrize("text", ["1.5 p.m.", "3:6 p.m.", "1,000 p.m."])
def test_english_clock_does_not_match_trailing_numeric_fragment(text):
    assert P.EN_TIME.search(text) is None


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


def test_alternative_question_example_keeps_both_choices_and_listener_role():
    source = "Do you want the blue folder or the green folder?"
    shots = [e for e in P.examples("translate") if e["input"]["utterance"] == source]
    assert len(shots) == 1
    example = shots[0]
    assert example["input"]["source_locale"] == "en-US"
    assert example["input"]["target_locale"] == "ko-KR"
    assert example["output"]["translated_text"] == "파란색 서류철을 원하세요, 아니면 초록색 서류철을 원하세요?"
    assert example["output"]["status"] == "ok" and example["output"]["clarification_question"] is None


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


@pytest.mark.parametrize("source,target,punctuated,spoken", [
    ("en-US", "ko-KR", "The zipper on my backpack is stuck. Please hold the map.",
     "the zipper on my backpack is stuck please hold the map"),
    ("es-US", "ko-KR", "¿Tiene una funda para este asiento?", "tiene una funda para este asiento"),
    ("ko-KR", "en-US", "목 받침이 너무 낮아서 불편하세요?", "목 받침이 너무 낮아서 불편하세요"),
])
def test_punctuation_and_stt_inputs_keep_original_payload_on_first_attempt_and_retry(source, target, punctuated, spoken):
    from engine import translation_repair

    for text in (punctuated, spoken):
        messages = P.translate_messages(source, target, "rental_counter", text)
        payload = json.loads(messages[-1]["content"])
        assert payload == {
            "source_locale": source, "target_locale": target, "target_language": P.LANG_NAME[target],
            "scenario": "rental_counter", "glossary": P.select_glossary(text, source, target), "utterance": text,
        }
        repaired = translation_repair(messages, ["too_short"])
        assert repaired[:-1] == messages[:-1]
        retry_payload = json.loads(repaired[-1]["content"])
        assert retry_payload.pop("repair")
        assert retry_payload == payload
        assert json.loads(messages[-1]["content"]) == payload


def test_stt_examples_contrast_questions_with_statements_and_keep_language_pair_selection():
    shots = [ex for ex in P.examples("translate")
             if not any(unicodedata.category(c).startswith("P") for c in ex["input"]["utterance"])]
    assert 2 <= len(shots) <= 4
    assert all(ex["input"]["utterance"] == ex["input"]["utterance"].lower() for ex in shots)
    assert sum(ex["output"]["translated_text"].endswith("?") for ex in shots) >= 2
    assert any(ex["output"]["translated_text"].endswith(".") and "?" not in ex["output"]["translated_text"]
               for ex in shots)
    assert any(ex["input"]["source_locale"] == "ko-KR" for ex in shots)
    for source in ("en-US", "es-US", "es-MX"):
        messages = P.translate_messages(source, "ko-KR", "general", "x")
        pairs = [(json.loads(user["content"]), json.loads(assistant["content"]))
                 for user, assistant in zip(messages[1:-1:2], messages[2:-1:2])]
        for ex in shots:
            i = ex["input"]
            same_pair = P.foreign_lang(i["source_locale"], i["target_locale"]) == P.LANG_KEY[source]
            assert ((i, ex["output"]) in pairs) == same_pair
