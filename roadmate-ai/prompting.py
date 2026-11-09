import json
import math
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LANG_KEY = {"en-US": "en", "ko-KR": "ko", "es-US": "es", "es-MX": "es"}
LANG_NAME = {"ko-KR": "Korean", "en-US": "English", "es-US": "Spanish", "es-MX": "Spanish"}
LATIN = "A-Za-zÀ-ÿ"


# 프롬프트
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


# 용어집
@lru_cache(maxsize=None)
def glossary() -> dict:
    return json.loads((ROOT / "packs" / "glossary.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def places() -> tuple:
    return tuple(json.loads((ROOT / "packs" / "poi.demo.json").read_text(encoding="utf-8"))["places"])


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


_FOLD = str.maketrans("áéíóúüñàèìòùâêîôûäëïöçÁÉÍÓÚÜÑ", "aeiouunaeiouaeiouaeiocAEIOUUN")


def fold(text: str) -> str:

    return text.translate(_FOLD)


def _find_terms(text: str, terms: list[str], lang: str) -> list[tuple[int, int, str]]:
    hits = []
    for t in terms:
        if lang != "ko":
            pat = rf"(?<![{LATIN}])" + re.escape(fold(t)) + rf"(?![{LATIN}])"
            hits += [(m.start(), m.end(), t) for m in re.finditer(pat, fold(text), re.IGNORECASE)]
        else:

            pat = r"\s?".join(map(re.escape, t.replace(" ", "")))
            hits += [(m.start(), m.end(), t) for m in re.finditer(pat, text)]
    return hits


def concept_spans(text: str, locale: str) -> list[tuple[int, int, str, str]]:

    lang = LANG_KEY[locale]
    cands = sorted((-(end - s), s, end, e["id"], e["category"])
                   for e in glossary()["entries"] for s, end, _ in _find_terms(text, e[lang], lang))
    taken: list[tuple[int, int, str, str]] = []
    for _, s, end, cid, cat in cands:
        if not any(s < te and ts < end for ts, te, _, _ in taken):
            taken.append((s, end, cid, cat))
    return sorted(taken)


def match_concepts(text: str, locale: str, categories: set | None = None) -> dict[str, str]:

    found: dict[str, str] = {}
    for s, end, cid, cat in concept_spans(text, locale):
        if not categories or cat in categories:
            found.setdefault(cid, text[s:end])
    return found


EN_TIME = re.compile(r"(?<![\w.,:])(\d{1,2})(?::(\d{2}))?\s*([ap])(?:\.\s?m\.|\s?m)(?![A-Za-z])", re.IGNORECASE)
KO_TIME = re.compile(r"(오전|오후)\s?(\d{1,2})시(?:\s?(\d{1,2})분|\s?(반))?")

PM_FMT = {"en": ("a.m.", "p.m."), "es": ("a. m.", "p. m.")}
WON = {"en": "won", "es": "wones"}


KO_MONEY = re.compile(r"(?<![가-힣\d])([\d,.\s일이삼사오육칠팔구십백천만억]+?)\s?원")


# 금액
def _money_hints(text: str, src: str, tgt: str) -> dict[str, str]:

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
                    continue
                if pattern is words and re.search(r"\d[\d.,]*\s+$", prepared[:m.start()]):
                    continue

                if re.search(r"\$\s*$", text[:m.start()]):
                    continue
                surfaces.append(text[m.start():currency.end()].strip())
                covered.append((m.start(), currency.end()))
    locale = {"ko": "ko-KR", "en": "en-US", "es": "es-US"}[src]
    for surface in surfaces:
        nums = extract_numbers(surface, locale)

        if len(nums) == 1 and nums[0].unit == "KRW" and math.isfinite(nums[0].value):
            amount = f"{nums[0].value:,}".removesuffix(".0")
            out[surface] = f"{amount} {'원' if tgt == 'ko' else WON[tgt]}"
    return out


# 시각
def _time_hints(text: str, src: str, tgt: str) -> dict[str, str]:

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


# 용어 선택
def select_glossary(text: str, source_locale: str, target_locale: str) -> dict[str, str]:

    by_id = {e["id"]: e for e in glossary()["entries"]}
    src, tgt = LANG_KEY[source_locale], LANG_KEY[target_locale]
    out = {surface: by_id[cid].get("preferred", {}).get(tgt, by_id[cid][tgt][0])
           for cid, surface in match_concepts(text, source_locale).items()}
    for term, targets in glossary().get(f"{src}_only_hints", {}).items():
        for start, end, _ in _find_terms(text, [term], src):
            out[text[start:end]] = targets[tgt]
    found = {}
    for p in places():

        names = [p[f"name_{src}"]] if src == "ko" else [p["name_en"], p["name_es"]]
        names += [a for a in p.get("aliases", []) if bool(re.search("[가-힣]", a)) == (src == "ko")]
        hits = sorted(_find_terms(text, names, src), key=lambda h: h[0] - h[1])
        if hits:
            found.setdefault(text[hits[0][0]:hits[0][1]], p[f"name_{tgt}"])

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


# 예시
def _few_shot(name: str, lang: str | None = None) -> list[dict]:

    msgs = []
    for ex in examples(name):
        i = ex["input"]
        if lang and "source_locale" in i and foreign_lang(i["source_locale"], i["target_locale"]) != lang:
            continue
        msgs.append({"role": "user", "content": _dump(i)})
        msgs.append({"role": "assistant", "content": _dump(ex["output"])})
    return msgs


_ES_STT_QUESTION_START = re.compile(
    r"^(?:(?:(?:me|nos|le)\s+)?(?:puede|pueden|podria|podrian)|puedo|podemos|se\s+puede|"
    r"hay|ya|donde|cuanto|cuanta|cuantos|cuantas|cuando|quien|quienes|que|como|a\s+que|por\s+que|cual)\b"
)


# 질문 복원
def restore_spanish_question(text: str, source_locale: str) -> str:
    if (source_locale not in ("es-US", "es-MX")
            or any(unicodedata.category(c).startswith("P") for c in text)):
        return text
    normalized = " ".join(fold(unicodedata.normalize("NFKC", text)).lower().split())

    return f"¿{text}?" if _ES_STT_QUESTION_START.match(normalized) else text


# 프롬프트 호출
def translate_messages(source_locale: str, target_locale: str, scenario: str, text: str) -> list[dict]:

    _, system = prompt("translate")
    payload = {
        "source_locale": source_locale,
        "target_locale": target_locale,
        "target_language": LANG_NAME[target_locale],
        "scenario": scenario,
        "glossary": select_glossary(text, source_locale, target_locale),
        "utterance": restore_spanish_question(text, source_locale),
    }
    lang = foreign_lang(source_locale, target_locale)
    shots = _few_shot("translate", lang)
    return [{"role": "system", "content": system}, *shots, {"role": "user", "content": _dump(payload)}]


def destination_messages(source_locale: str, text: str) -> list[dict]:

    _, system = prompt("destination")
    payload = {"source_locale": source_locale, "utterance": text}
    return [{"role": "system", "content": system}, *_few_shot("destination"), {"role": "user", "content": _dump(payload)}]


def completion_text(output: dict) -> str:

    return _dump(output)


NO_CJK_TEXT = r"^[^\x22\x5c\x00-\x1f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]*$"
_TEXT_OR_NULL = {"anyOf": [{"type": "string", "pattern": NO_CJK_TEXT}, {"type": "null"}]}

# 출력 형식
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


# 모델 설정
TRANSLATE_SAMPLING = {"temperature": 0.2, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "seed": 7}
RETRY_SAMPLING = {"temperature": 0.0, "top_p": 1.0, "top_k": 1, "min_p": 0.0, "seed": 7}
DESTINATION_SAMPLING = {"temperature": 0.0, "top_p": 1.0, "top_k": 1, "min_p": 0.0, "seed": 7}

TRANSLATE_MAX_TOKENS = 256
DESTINATION_MAX_TOKENS = 128
