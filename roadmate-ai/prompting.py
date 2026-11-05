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


@lru_cache(maxsize=None)
def places() -> tuple:
    return tuple(json.loads((ROOT / "packs" / "poi.demo.json").read_text(encoding="utf-8"))["places"])


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _find_terms(text: str, terms: list[str], lang: str) -> list[tuple[int, int, str]]:
    hits = []
    for t in terms:
        if lang == "en":
            pat = r"(?<![A-Za-z])" + re.escape(t) + r"(?![A-Za-z])"
            hits += [(m.start(), m.end(), t) for m in re.finditer(pat, text, re.IGNORECASE)]
        else:
            # 검증: 한국어는 띄어쓰기가 달라도 같은 용어로 본다(자차보험 = 자차 보험)
            pat = r"\s?".join(map(re.escape, t.replace(" ", "")))
            hits += [(m.start(), m.end(), t) for m in re.finditer(pat, text)]
    return hits


def concept_spans(text: str, locale: str) -> list[tuple[int, int, str, str]]:
    """(시작, 끝, 개념 id, category) 위치순. 전체 용어에서 긴 표현이 먼저 자리를 차지한다(petrol station > petrol)."""
    lang = LANG_KEY[locale]
    cands = sorted((-(end - s), s, end, e["id"], e["category"])
                   for e in glossary()["entries"] for s, end, _ in _find_terms(text, e[lang], lang))
    taken: list[tuple[int, int, str, str]] = []
    for _, s, end, cid, cat in cands:
        if not any(s < te and ts < end for ts, te, _, _ in taken):
            taken.append((s, end, cid, cat))
    return sorted(taken)


def match_concepts(text: str, locale: str, categories: set | None = None) -> dict[str, str]:
    """용어집 개념 id → 실제로 찾은 표현. 카테고리는 긴 표현 매칭이 끝난 뒤 거른다."""
    found: dict[str, str] = {}
    for s, end, cid, cat in concept_spans(text, locale):
        if not categories or cat in categories:
            found.setdefault(cid, text[s:end])
    return found


EN_TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])(?:\.\s?m\.|\s?m)(?![A-Za-z])", re.IGNORECASE)
KO_TIME = re.compile(r"(오전|오후)\s?(\d{1,2})시(?:\s?(\d{1,2})분|\s?(반))?")


def _time_hints(text: str, src: str) -> dict[str, str]:
    # QWEN 구현: 오전·오후가 붙은 시각은 목표 언어 표기를 직접 알려 준다(6 p.m. = 오후 6시)
    out = {}
    if src == "en":
        for m in EN_TIME.finditer(text):
            minute = f" {int(m.group(2))}분" if m.group(2) and int(m.group(2)) else ""
            out[m.group(0)] = f"{'오후' if m.group(3).lower() == 'p' else '오전'} {int(m.group(1))}시{minute}"
    else:
        for m in KO_TIME.finditer(text):
            minute = "30" if m.group(4) else m.group(3)
            out[m.group(0)] = f"{int(m.group(2))}{':' + minute.zfill(2) if minute else ''} {'p.m.' if m.group(1) == '오후' else 'a.m.'}"
    return out


def select_glossary(text: str, source_locale: str, target_locale: str) -> dict[str, str]:
    # QWEN 구현: 발화에 나온 용어·데모 장소 이름만 넣어 프롬프트를 짧게 유지(성산일출봉 = Seongsan Ilchulbong)
    by_id = {e["id"]: e for e in glossary()["entries"]}
    src, tgt = LANG_KEY[source_locale], LANG_KEY[target_locale]
    out = {surface: by_id[cid][tgt][0] for cid, surface in match_concepts(text, source_locale).items()}
    for p in places():
        names = [p[f"name_{src}"]] + [a for a in p.get("aliases", []) if bool(re.search("[가-힣]", a)) == (src == "ko")]
        hits = sorted(_find_terms(text, names, src), key=lambda h: h[0] - h[1])
        if hits:
            out.setdefault(text[hits[0][0]:hits[0][1]], p[f"name_{tgt}"])
    for k, v in _time_hints(text, src).items():
        out.setdefault(k, v)
    return out


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


# QWEN 구현: llama.cpp 문법 제약으로 생성 단계에서 한자·가나(와 따옴표·역슬래시·제어문자)를 막는다. ^...$, \uXXXX 형식 필수
NO_CJK_TEXT = r"^[^\x22\x5c\x00-\x1f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]*$"
_TEXT_OR_NULL = {"anyOf": [{"type": "string", "pattern": NO_CJK_TEXT}, {"type": "null"}]}

TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["ok", "clarification_required"]},
        "translated_text": _TEXT_OR_NULL,
        "clarification_question": _TEXT_OR_NULL,
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
