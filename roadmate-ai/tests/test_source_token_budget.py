"""Opt-in test with pinned 14B metadata, no weights/inference/evaluation or training data."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from train import distill as D


# Synthetic length fixtures, deliberately repeated with numbers; not evidence of semantic diversity.
SHORT = {
    "en-US": ["The display on pump {n} has gone blank.", "Please leave the key in locker {n}.",
        "Could you bring the child seat to bay {n}?", "The car in space {n} has a loose mirror.",
        "I left my umbrella beside door {n}.", "Can we use entrance {n} with this vehicle?",
        "There is a small spill near pump {n}.", "The ticket machine shows error {n}."],
    "es-US": ["La pantalla del surtidor {n} no funciona.", "Deje la llave en la taquilla {n}, por favor.",
        "Puede traer la silla infantil al puesto {n}?", "El coche del lugar {n} tiene un espejo flojo.",
        "Dejé mi paraguas junto a la puerta {n}.", "Podemos usar la entrada {n} con este vehículo?",
        "Hay un pequeño derrame junto al surtidor {n}.", "La máquina de boletos muestra el error {n}."],
    "ko-KR": ["{n}번 주유기 화면이 꺼졌어요.", "열쇠는 {n}번 보관함에 넣어 주세요.",
        "아동용 좌석을 {n}번 칸으로 가져다주세요.", "{n}번 주차 칸에 있는 차의 거울이 헐거워요.",
        "우산을 {n}번 문 옆에 두고 왔어요.", "이 차로 {n}번 입구를 이용해도 돼요?",
        "{n}번 주유기 옆에 기름이 조금 샜어요.", "주차권 기계에 오류 번호 {n}이 떠요."],
}
LONG_TAIL = {
    "en-US": " The employee and I are waiting by the entrance while another vehicle is being moved out of the way.",
    "es-US": " Estamos esperando junto a la entrada mientras otro empleado mueve el vehículo que bloquea el paso.",
    "ko-KR": " 다른 직원분이 입구에 세워진 차를 옮기는 동안 안내를 기다리고 있어요.",
}


@pytest.fixture(scope="module")
def pinned_tokenizer():
    folder = os.environ.get("SOURCE_TOKENIZER_DIR")
    if not folder:
        pytest.skip("set SOURCE_TOKENIZER_DIR to the pinned 14B tokenizer and GGUF template directory")
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    from tokenizers import Tokenizer
    folder = Path(folder)
    tokenizer = folder / "tokenizer.json"
    template = folder / "gguf-chat-template.jinja"
    # Qwen3-14B base 40c0698 / GGUF 530227a metadata, independently downloaded from each pinned repo.
    assert hashlib.sha256(tokenizer.read_bytes()).hexdigest() == "aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4"
    assert hashlib.sha256(template.read_bytes()).hexdigest() == "57f1fd00f0013a2be96aa79b857391f27e23df5b5f847072b524c897e24d0361"
    manifest = json.loads((D.ROOT / "models/manifest.qwen3-14b.json").read_text(encoding="utf-8"))
    assert manifest["model"]["base_revision"] == "40c069824f4251a91eefaf281ebe4c544efd3e18"
    assert manifest["model"]["gguf_revision"] == "530227a7d994db8eca5ab5ced2fb692b614357fd"
    jinja = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True).from_string(template.read_text(encoding="utf-8"))
    return Tokenizer.from_file(str(tokenizer)), jinja, manifest["runtime"]


@pytest.mark.parametrize("locale", D.SOURCE_LOCALES)
def test_79_sources_fit_with_output_reserve_and_long_history_needs_guard(pinned_tokenizer, locale):
    tokenizer, template, runtime = pinned_tokenizer
    lang = "es-US" if locale == "es-MX" else locale
    short = [SHORT[lang][i % len(SHORT[lang])].format(n=i + 1) for i in range(79)]
    long = [(text + LONG_TAIL[lang] * 30)[:400] for text in short]
    for scenario in D.SCENARIOS:
        for batch in range(16):
            for history, should_fit in ((short, True), (long, False)):
                messages = D.source_messages(locale, scenario, 1, batch, history)
                assert json.loads(messages[-1]["content"])["already_used"] == history
                rendered = template.render(messages=messages, add_generation_prompt=True, tools=None,
                                           **runtime["chat_template_kwargs"])
                total = len(tokenizer.encode(rendered, add_special_tokens=False).ids) + D.SOURCE_MAX_TOKENS
                assert (total <= runtime["ctx"]) is should_fit, (locale, scenario, batch, total)
