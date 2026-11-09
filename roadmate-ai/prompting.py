"""서버 추론과 학습 데이터가 같은 프롬프트를 쓰도록 하는 단일 빌더."""
import json
import math
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LANG_KEY = {"en-US": "en", "ko-KR": "ko", "es-US": "es", "es-MX": "es"}
LANG_NAME = {"ko-KR": "Korean", "en-US": "English", "es-US": "Spanish", "es-MX": "Spanish"}
LATIN = "A-Za-zÀ-ÿ"


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
    return json.loads((ROOT / "packs" / "glossary.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def optional_field_terms() -> dict:
    return json.loads((ROOT / "packs" / "field_glossary.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def places() -> tuple:
    return tuple(json.loads((ROOT / "packs" / "poi.demo.json").read_text(encoding="utf-8"))["places"])


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


_FOLD = str.maketrans("áéíóúüñàèìòùâêîôûäëïöçÁÉÍÓÚÜÑ", "aeiouunaeiouaeiouaeiocAEIOUUN")


def fold(text: str) -> str:
    # 검증: 스페인어 강세 표기가 빠져도 같은 말로 본다(diésel = diesel). 글자 수가 그대로라 위치를 원문에 쓸 수 있다
    return text.translate(_FOLD)


def _find_terms(text: str, terms: list[str], lang: str) -> list[tuple[int, int, str]]:
    hits = []
    for t in terms:
        if lang != "ko":
            pat = rf"(?<![{LATIN}])" + re.escape(fold(t)) + rf"(?![{LATIN}])"
            hits += [(m.start(), m.end(), t) for m in re.finditer(pat, fold(text), re.IGNORECASE)]
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


EN_TIME = re.compile(r"(?<![\w.,:])(\d{1,2})(?::(\d{2}))?\s*([ap])(?:\.\s?m\.|\s?m)(?![A-Za-z])", re.IGNORECASE)
KO_TIME = re.compile(r"(오전|오후)\s?(\d{1,2})시(?:\s?(\d{1,2})분|\s?(반))?")
# 프론트 연동: 목표 언어별 시각·원화 표기(TTS가 그대로 읽는다)
PM_FMT = {"en": ("a.m.", "p.m."), "es": ("a. m.", "p. m.")}
WON = {"en": "won", "es": "wones"}


KO_MONEY = re.compile(r"(?<![가-힣\d])([\d,.\s일이삼사오육칠팔구십백천만억]+?)\s?원")


def _money_hints(text: str, src: str, tgt: str) -> dict[str, str]:
    # 검증기와 같은 숫자·통화 해석을 사용한다. 외국어의 won/wones도 금액 전체를 원으로 알려 준다.
    from validation import CUR, DIGIT_RE, EN_WORD_RE, ES_WORD_RE, _es_prep, extract_numbers
    out = {}
    if src == "ko":
        surfaces = [m.group().strip() for m in KO_MONEY.finditer(text)
                    if re.search(r"[\d십백천만억]", m.group(1))]
    else:
        prepared = _es_prep(fold(text)) if src == "es" else text
        words = ES_WORD_RE if src == "es" else EN_WORD_RE
        surfaces = []
        covered = []
        for pattern in (DIGIT_RE, words):
            for m in pattern.finditer(prepared):
                currency = CUR[src].match(prepared, m.end())
                if not currency or currency.group(1).lower() not in ("won", "wons", "wones", "krw"):
                    continue
                if any(m.start() < end and start < currency.end() for start, end in covered):
                    continue  # 28 thousand won 안의 thousand won을 별도 금액으로 넣지 않는다.
                if pattern is words and re.search(r"\d[\d.,]*\s+$", prepared[:m.start()]):
                    continue
                # 달러 기호와 원화 이름이 충돌하는 입력은 원화 힌트로 단정하지 않는다.
                if re.search(r"\$\s*$", text[:m.start()]):
                    continue
                surfaces.append(text[m.start():currency.end()].strip())
                covered.append((m.start(), currency.end()))
    locale = {"ko": "ko-KR", "en": "en-US", "es": "es-US"}[src]
    for surface in surfaces:
        nums = extract_numbers(surface, locale)
        # shortcut: 1.234,50처럼 공용 파서가 못 읽는 혼합 표기는 힌트를 생략한다. 숫자 파서 확장 후 지원한다.
        if len(nums) == 1 and nums[0].unit == "KRW" and math.isfinite(nums[0].value):
            amount = f"{nums[0].value:,}".removesuffix(".0")
            out[surface] = f"{amount} {'원' if tgt == 'ko' else WON[tgt]}"
    return out


def _time_hints(text: str, src: str, tgt: str) -> dict[str, str]:
    # QWEN 구현: 오전·오후가 붙은 시각은 목표 언어 표기를 직접 알려 준다(6 p.m. = 오후 6시, 오후 3시 = 3 p. m.)
    out = {}
    if src != "ko":
        if src == "es":
            from validation import spanish_times
            times = [(text[start:end], int(hour.value) % 24, mins)
                     for start, end, hour, mins in spanish_times(text) if hour.exact]
        else:
            from validation import english_times
            spoken = list(english_times(text))
            for start, end, hour, mins in spoken:
                if not hour.exact:
                    out[text[start:end]] = f"{int(hour.value)}시 {mins}분"
            times = [(text[start:end], int(hour.value) % 24, mins)
                     for start, end, hour, mins in spoken if hour.exact]
            times += [(m.group(0), int(m.group(1)) % 12 + (12 if m.group(3).lower() == "p" else 0),
                       int(m.group(2) or 0)) for m in EN_TIME.finditer(text)
                      if 1 <= int(m.group(1)) <= 12 and int(m.group(2) or 0) < 60
                      if not any(m.start() < end and start < m.end() for start, end, _, _ in spoken)]
        for surface, h, mins in times:
            minute = f" {mins}분" if mins else ""
            if h in (0, 12):
                out[surface] = ("정오" if h else "자정") if not minute else (f"낮 12시{minute}" if h else f"0시{minute}")
            else:
                out[surface] = f"{'오후' if h >= 12 else '오전'} {h % 12}시{minute}"
    else:
        for m in KO_TIME.finditer(text):
            minute = "30" if m.group(4) else m.group(3)
            am, pm = PM_FMT[tgt]
            out[m.group(0)] = f"{int(m.group(2))}{':' + minute.zfill(2) if minute else ''} {pm if m.group(1) == '오후' else am}"
    return out


def select_glossary(text: str, source_locale: str, target_locale: str, *, field_glossary: bool = False) -> dict[str, str]:
    # QWEN 구현: 발화에 나온 용어·데모 장소 이름만 넣어 프롬프트를 짧게 유지(성산일출봉 = Seongsan Ilchulbong)
    by_id = {e["id"]: e for e in glossary()["entries"]}
    src, tgt = LANG_KEY[source_locale], LANG_KEY[target_locale]
    out = {surface: by_id[cid].get("preferred", {}).get(tgt, by_id[cid][tgt][0])
           for cid, surface in match_concepts(text, source_locale).items()}
    for term, targets in glossary().get(f"{src}_only_hints", {}).items():
        for start, end, _ in _find_terms(text, [term], src):
            out[text[start:end]] = targets[tgt]
    if field_glossary:
        # 실험용 현장 표현은 기존 안전 개념과 분리한다. 긴 표현에 포함된 짧은 힌트는 중복하지 않는다.
        for entry in optional_field_terms()["entries"]:
            for start, end, _ in _find_terms(text, entry[src], src):
                surface = text[start:end]
                out = {k: v for k, v in out.items() if fold(k).lower() not in fold(surface).lower()}
                out[surface] = entry.get("preferred", {}).get(tgt, entry[tgt][0])
    found = {}
    for p in places():
        # 스페인어 화자도 영어 표기(Jeju Airport)를 말할 수 있어 외국어 쪽은 로마자 이름을 모두 본다
        names = [p[f"name_{src}"]] if src == "ko" else [p["name_en"], p["name_es"]]
        names += [a for a in p.get("aliases", []) if bool(re.search("[가-힣]", a)) == (src == "ko")]
        hits = sorted(_find_terms(text, names, src), key=lambda h: h[0] - h[1])
        if hits:
            found.setdefault(text[hits[0][0]:hits[0][1]], p[f"name_{tgt}"])
    # 장소 이름 안에 든 지명(성산일출봉 속 성산)은 장소 이름으로만 알려 준다
    out = {k: v for k, v in out.items() if not any(k != pk and k in pk for pk in found)}
    for k, v in found.items():
        out.setdefault(k, v)
    for k, v in _time_hints(text, src, tgt).items():
        out.setdefault(k, v)
    for k, v in _money_hints(text, src, tgt).items():
        out.setdefault(k, v)
    return out


def foreign_lang(source_locale: str, target_locale: str) -> str:
    return LANG_KEY[target_locale if source_locale == "ko-KR" else source_locale]


def _few_shot(name: str, lang: str | None = None, *, two_step_ko: bool = False) -> list[dict]:
    # QWEN 구현: 번역 예시는 같은 외국어 쌍(한↔영, 한↔스)만 넣는다. 다른 언어 예시가 섞이면 출력 언어가 흔들린다
    msgs = []
    for ex in examples(name):
        i = ex["input"]
        if lang and "source_locale" in i and foreign_lang(i["source_locale"], i["target_locale"]) != lang:
            continue
        msgs.append({"role": "user", "content": _dump(i)})
        output = {key: value for key, value in ex["output"].items() if key != "literal"}
        if two_step_ko and i.get("target_locale") == "ko-KR":
            output = {"status": output["status"], "literal": ex["output"]["literal"], **output}
        msgs.append({"role": "assistant", "content": _dump(output)})
    return msgs


NATURAL_KO_RULE = (
    "Korean style: use polite -요 endings consistently. Omit redundant pronouns only when the referent remains clear. "
    "Avoid English-like pronouns and stiff ~는 것이, ~하기 위해, 여부 when a simple spoken expression keeps the meaning. "
    "Use ordinary on-site verbs such as 넣다, 빼다, 내다, 반납하다 where they fit; never alter facts or roles."
)
TWO_STEP_KO_RULE = (
    '10. For a Korean target, output JSON keys in this order: '
    '{"status":"ok","literal":"faithful Korean translation","translated_text":"natural spoken Korean with the same facts",'
    '"clarification_question":null}. Write literal first, then express exactly those facts as Korean staff or a driver would say them. '
    'Keep roles, numbers, negation and uncertainty unchanged. For clarification output '
    '{"status":"clarification_required","literal":null,"translated_text":null,"clarification_question":"..."}. '
    'For other targets keep the ordinary format without literal. Output JSON only.'
)


def translate_messages(source_locale: str, target_locale: str, scenario: str, text: str, *,
                       two_step_ko: bool = False, natural_style_ko: bool = False,
                       field_glossary: bool = False) -> list[dict]:
    # QWEN 구현: system + few-shot + 실제 발화(JSON으로 감싸 지시문 주입을 분리)
    _, system = prompt("translate")
    korean = target_locale == "ko-KR"
    if two_step_ko and korean:
        system = system.rsplit("\n10. ", 1)[0] + "\n" + TWO_STEP_KO_RULE
    if natural_style_ko and korean:
        system += "\n" + NATURAL_KO_RULE
    payload = {
        "source_locale": source_locale,
        "target_locale": target_locale,
        "target_language": LANG_NAME[target_locale],
        "scenario": scenario,
        "glossary": select_glossary(text, source_locale, target_locale, field_glossary=field_glossary),
        "utterance": text,
    }
    lang = foreign_lang(source_locale, target_locale)
    shots = _few_shot("translate", lang, two_step_ko=two_step_ko and korean)
    if natural_style_ko and korean:
        shots += _few_shot("translate.natural", lang, two_step_ko=two_step_ko)
    return [{"role": "system", "content": system}, *shots, {"role": "user", "content": _dump(payload)}]


def polish_messages(source_locale: str, source: str, korean: str, glossary_: dict) -> list[dict]:
    # QWEN 구현: 영·스→한 번역 초안을 자연스러운 한국어로 다듬는 2차 요청(뜻·숫자·부정은 그대로)
    _, system = prompt("polish")
    payload = {"source_locale": source_locale, "source": source, "korean": korean, "glossary": glossary_}
    return [{"role": "system", "content": system}, *_few_shot("polish"), {"role": "user", "content": _dump(payload)}]


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


def translation_schema(target_locale: str, two_step_ko: bool = False) -> dict:
    if not two_step_ko or target_locale != "ko-KR":
        return TRANSLATE_SCHEMA
    props = TRANSLATE_SCHEMA["properties"]
    # dict 순서는 llama.cpp JSON 문법의 생성 순서이기도 하다.
    return {**TRANSLATE_SCHEMA,
            "properties": {"status": props["status"], "literal": _TEXT_OR_NULL,
                           "translated_text": props["translated_text"],
                           "clarification_question": props["clarification_question"]},
            "required": ["status", "literal", "translated_text", "clarification_question"]}


def translation_max_tokens(target_locale: str, two_step_ko: bool = False) -> int:
    return 512 if two_step_ko and target_locale == "ko-KR" else TRANSLATE_MAX_TOKENS

POLISH_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string", "pattern": NO_CJK_TEXT}},
    "required": ["text"],
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
POLISH_MAX_TOKENS = 192
DESTINATION_MAX_TOKENS = 128
