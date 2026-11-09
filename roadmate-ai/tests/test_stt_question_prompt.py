import json

import pytest

import prompting as P


PREFIXES = [
    *(f"{pronoun} {verb}" for pronoun in ("me", "nos", "le")
      for verb in ("puede", "pueden", "podría", "podrían")),
    "puede", "pueden", "podría", "podrían", "puedo", "podemos", "se puede", "hay", "ya",
    "dónde", "cuánto", "cuánta", "cuántos", "cuántas", "qué", "cómo", "a qué", "por qué", "cuál",
]


@pytest.mark.parametrize("locale", ["es-US", "es-MX"])
@pytest.mark.parametrize("prefix", PREFIXES)
def test_spanish_stt_question_prefixes_have_word_boundaries(locale, prefix):
    text = prefix + " una consulta sobre el equipaje"
    assert P.restore_spanish_question(text, locale) == f"¿{text}?"
    nonword = prefix + "xyz una consulta sobre el equipaje"
    assert P.restore_spanish_question(nonword, locale) == nonword


@pytest.mark.parametrize("text", [
    "  ME\tPODRÍA revisar la cremallera  ",
    "PODRIA revisar la cremallera",
    "do\u0301nde guardan las fundas",
    "Ａ ＱＵÉ altura queda la percha",
])
def test_match_normalization_preserves_the_original_source_inside_question_marks(text):
    assert P.restore_spanish_question(text, "es-MX") == f"¿{text}?"


@pytest.mark.parametrize("text", [
    "¿Hay una funda para la almohada?", "hay una funda para la almohada?", "¿hay una funda para la almohada",
    "Hay una funda para la almohada.", "¡Ya guardé la bolsa!", "ya guardé la bolsa!",
    "hay una funda. necesito la grande", "hay una funda a las 9 a.m.",
    "hay un problema con el carro.", "hay una funda…", "hay una funda！", "hay una funda。",
    "hay una funda, es grande", "hay una funda; es grande", "hay una funda (grande)",
    "hay una funda de color «marfil»", "hay una funda de color 'marfil'",
])
def test_existing_sentence_punctuation_is_unchanged(text):
    assert P.restore_spanish_question(text, "es-US") == text


@pytest.mark.parametrize("text", [
    "hay 2.5 metros de tela", "puedo recoger la funda a las 8:25", "hay 1,250 fundas",
])
def test_numeric_punctuation_also_keeps_original_input(text):
    assert P.restore_spanish_question(text, "es-US") == text


def test_unpunctuated_numbers_do_not_block_question_restoration():
    text = "hay 250 fundas en la caja"
    assert P.restore_spanish_question(text, "es-US") == f"¿{text}?"


@pytest.mark.parametrize("text", [
    "hay un problema con el cierre de mi mochila", "ya guardé el folleto en la carpeta",
])
def test_known_false_positives_for_clear_statements_remain_visible(text):
    assert P.restore_spanish_question(text, "es-US") == f"¿{text}?"


@pytest.mark.parametrize("locale,text", [
    ("en-US", "hay bales are stacked by the entrance"),
    ("ko-KR", "ya 이 표지는 그대로 두세요"),
    ("es-US", "haystack es el nombre escrito en la etiqueta"),
    ("es-MX", "cualquier funda sirve para esa almohada"),
    ("es-US", "yard es la unidad indicada aquí"),
    ("es-US", "necesito una funda para la almohada"),
    ("es-US", "me puedo ocupar de la maleta"),
])
def test_other_locales_and_unlisted_starts_are_unchanged(locale, text):
    assert P.restore_spanish_question(text, locale) == text


def test_shared_prompt_builder_wraps_only_current_input_and_preserves_glossary():
    text = "puedo llevar gasolina en este recipiente"
    messages = P.translate_messages("es-US", "ko-KR", "gas_station", text)
    payload = json.loads(messages[-1]["content"])
    assert payload["utterance"] == f"¿{text}?"
    assert payload["glossary"] == P.select_glossary(text, "es-US", "ko-KR")
    assert messages[1:-1] == P._few_shot("translate", "es")
