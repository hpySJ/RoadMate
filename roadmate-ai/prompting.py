"""서버 추론과 학습 데이터가 같은 프롬프트를 쓰도록 하는 단일 빌더."""
import json
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LANG_KEY = {"en-US": "en", "ko-KR": "ko"}


def _load_prompt(name: str) -> tuple[str, str]:
    first, _, body = (ROOT / "prompts" / f"{name}.txt").read_text(encoding="utf-8").partition("\n")
    if not first.startswith("prompt_version:"):
        raise ValueError(f"prompts/{name}.txt: first line must be 'prompt_version: ...'")
    return first.split(":", 1)[1].strip(), body.strip()


@lru_cache(maxsize=None)
def prompt(name: str) -> tuple[str, str]:
    return _load_prompt(name)


@lru_cache(maxsize=None)
def examples(name: str) -> tuple:
    return tuple(json.loads((ROOT / "prompts" / f"{name}.examples.json").read_text(encoding="utf-8")))


@lru_cache(maxsize=None)
def glossary() -> dict:
    return json.loads((ROOT / "packs" / "glossary.ko-en.json").read_text(encoding="utf-8"))


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _find_terms(text: str, terms: list[str], lang: str) -> list[tuple[int, int, str]]:
    hits = []
    for t in terms:
        if lang == "en":
            pat = r"(?<![A-Za-z])" + re.escape(t) + r"(?![A-Za-z])"
            hits += [(m.start(), m.end(), t) for m in re.finditer(pat, text, re.IGNORECASE)]
        else:
            start = text.find(t)
            while start != -1:
                hits.append((start, start + len(t), t))
                start = text.find(t, start + 1)
    return hits


def match_concepts(text: str, locale: str, categories: set | None = None) -> dict[str, str]:
    """용어집 개념 id → 실제로 찾은 표현. 긴 표현이 먼저 자리를 차지한다(gas station > gas)."""
    lang = LANG_KEY[locale]
    cands = []
    for e in glossary()["entries"]:
        if categories and e["category"] not in categories:
            continue
        for s, end, t in _find_terms(text, e[lang], lang):
            cands.append((-(end - s), s, end, e["id"], text[s:end]))
    taken: list[tuple[int, int]] = []
    found: dict[str, str] = {}
    for _, s, end, cid, surface in sorted(cands):
        if any(s < te and ts < end for ts, te in taken):
            continue
        taken.append((s, end))
        found.setdefault(cid, surface)
    return found


def select_glossary(text: str, source_locale: str, target_locale: str) -> dict[str, str]:
    # QWEN 구현: 발화에 나온 용어만 넣어 프롬프트를 짧게 유지
    by_id = {e["id"]: e for e in glossary()["entries"]}
    tgt = LANG_KEY[target_locale]
    return {surface: by_id[cid][tgt][0] for cid, surface in match_concepts(text, source_locale).items()}


def _few_shot(name: str) -> list[dict]:
    msgs = []
    for ex in examples(name):
        msgs.append({"role": "user", "content": _dump(ex["input"])})
        msgs.append({"role": "assistant", "content": _dump(ex["output"])})
    return msgs


def translate_messages(source_locale: str, target_locale: str, scenario: str, text: str) -> list[dict]:
    # QWEN 구현: system + few-shot + 실제 발화(JSON으로 감싸 지시문 주입을 분리)
    _, system = prompt("translate")
    payload = {
        "source_locale": source_locale,
        "target_locale": target_locale,
        "scenario": scenario,
        "glossary": select_glossary(text, source_locale, target_locale),
        "utterance": text,
    }
    return [{"role": "system", "content": system}, *_few_shot("translate"), {"role": "user", "content": _dump(payload)}]


def destination_messages(source_locale: str, text: str) -> list[dict]:
    # QWEN 구현: 목적지 추출 프롬프트
    _, system = prompt("destination")
    payload = {"source_locale": source_locale, "utterance": text}
    return [{"role": "system", "content": system}, *_few_shot("destination"), {"role": "user", "content": _dump(payload)}]


def completion_text(output: dict) -> str:
    # 학습: 정답 completion도 같은 직렬화 형식
    return _dump(output)


TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "clarification_required"]},
        "translated_text": {"type": ["string", "null"]},
        "clarification_question": {"type": ["string", "null"]},
    },
    "required": ["status", "translated_text", "clarification_question"],
    "additionalProperties": False,
}

DESTINATION_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["set_destination", "other"]},
        "query_original": {"type": ["string", "null"]},
        "query_ko_candidate": {"type": ["string", "null"]},
        "region_hint": {"type": ["string", "null"]},
        "category_hint": {
            "enum": ["airport", "beach", "mountain", "market", "attraction", "rental_office", "gas_station", "other", None],
        },
        "needs_confirmation": {"type": "boolean"},
    },
    "required": ["intent", "query_original", "query_ko_candidate", "region_hint", "category_hint", "needs_confirmation"],
    "additionalProperties": False,
}

# 파라미터 튜닝: Qwen3-2507 권장값(top_p 0.8, top_k 20)에서 temperature만 번역용으로 낮춤
TRANSLATE_SAMPLING = {"temperature": 0.2, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "seed": 7}
RETRY_SAMPLING = {"temperature": 0.0, "top_p": 1.0, "top_k": 1, "min_p": 0.0, "seed": 7}
DESTINATION_SAMPLING = {"temperature": 0.0, "top_p": 1.0, "top_k": 1, "min_p": 0.0, "seed": 7}
# 파라미터 튜닝: 출력 상한
TRANSLATE_MAX_TOKENS = 256
DESTINATION_MAX_TOKENS = 128
