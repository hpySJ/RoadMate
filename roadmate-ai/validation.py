"""번역 출력 검증: 구조·언어·숫자·부정·연료·좌우. 실패하면 재생하지 않는다."""
import re
from dataclasses import dataclass

from prompting import match_concepts

HANGUL = re.compile(r"[가-힣]")
LATIN = re.compile(r"[A-Za-z]")
HAN_KANA = re.compile(r"[぀-ヿ一-鿿]")


def script_counts(text: str) -> tuple[int, int]:
    return len(HANGUL.findall(text)), len(LATIN.findall(text))


def in_locale_script(text: str, locale: str) -> bool:
    h, l = script_counts(text)
    if locale == "ko-KR":
        return h > 0 and h >= l
    return l > 0 and h == 0


def looks_like_other_language(text: str, locale: str) -> bool:
    # 검증: 버튼 방향과 실제 발화 언어가 반대인 경우
    h, l = script_counts(text)
    if locale == "en-US":
        return h > max(2, l)
    return h == 0 and l >= 3


# ---------------------------------------------------------------- numbers
@dataclass(frozen=True)
class Num:
    value: float
    kind: str  # n | time | phone


EN_UNITS = {
    "zero": 0, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "one": 1,
}
EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
EN_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000}
EN_WORD_RE = re.compile(
    r"\b(?:(?:" + "|".join([*EN_UNITS, *EN_TENS, *EN_SCALES]) + r")(?:[\s-]+(?:and[\s-]+)?|\b))+",
    re.IGNORECASE,
)
EN_ORD = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10}
EN_ORD_RE = re.compile(
    r"\b(" + "|".join(EN_ORD) + r")\s+(floor|exit|spot|space|lane|turn|street|gate|row|level|stop|light|traffic light"
    r"|intersection|corner|day|time|car|door|seat|building|parking space)s?\b", re.IGNORECASE)
SINO = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9}
SINO_SMALL = {"십": 10, "백": 100, "천": 1000}
SINO_BIG = {"만": 10_000, "억": 100_000_000}
KO_COUNTER = r"(?:원|달러|명|분|킬로미터|킬로|키로|km|리터|L|대|번|개|층|퍼센트|%|살|시간|일|주|박|달)"
NATIVE = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
          "열한": 11, "열두": 12, "스무": 20, "스물": 20}
NATIVE_RE = re.compile(
    r"(?<![가-힣])(열한|열두|다섯|여섯|일곱|여덟|아홉|스무|스물|한|두|세|네|열)\s?(시간|시|명|개|대|번|살|사람|장|병|잔|곳|군데|달)"
)
PHONE_RE = re.compile(r"(?<!\d)(?:\+?82[\s-]?)?0?\d{1,3}[\s-]\d{3,4}[\s-]\d{4}(?!\d)")
CLOCK_RE = re.compile(r"(?<!\d)([01]?\d|2[0-4]):([0-5]\d)(?!\d)")
DIGIT_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\d,])")


def _parse_sino(s: str) -> int | None:
    total, chunk, digit = 0, 0, None
    for ch in s:
        if ch in SINO:
            if digit is not None:
                return None
            digit = SINO[ch]
        elif ch in SINO_SMALL:
            chunk += (digit or 1) * SINO_SMALL[ch]
            digit = None
        elif ch in SINO_BIG:
            chunk += digit or 0
            total += (chunk or 1) * SINO_BIG[ch]
            chunk, digit = 0, None
        else:
            return None
    return total + chunk + (digit or 0)


def _parse_en_words(s: str) -> int | None:
    total, cur, seen = 0, 0, False
    for w in re.split(r"[\s-]+", s.lower()):
        if w in ("", "and"):
            continue
        seen = True
        if w in EN_UNITS:
            cur += EN_UNITS[w]
        elif w in EN_TENS:
            cur += EN_TENS[w]
        elif w == "hundred":
            cur = (cur or 1) * 100
        elif w in ("thousand", "million"):
            total += (cur or 1) * EN_SCALES[w]
            cur = 0
    return total + cur if seen else None


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    chars = list(text)
    for s, e in spans:
        for i in range(s, e):
            chars[i] = " "
    return "".join(chars)


def extract_numbers(text: str, locale: str) -> list[Num]:
    out: list[Num] = []
    spans: list[tuple[int, int]] = []
    for m in PHONE_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if digits.startswith("82"):
            digits = "0" + digits[2:]
        out.append(Num(float(digits), "phone"))
        spans.append(m.span())
    text = _mask(text, spans)
    spans = []
    for m in CLOCK_RE.finditer(text):
        out.append(Num(float(m.group(1)), "time"))
        if int(m.group(2)):
            out.append(Num(float(m.group(2)), "n"))
        spans.append(m.span())
    text = _mask(text, spans)

    if locale == "en-US":
        for m in re.finditer(r"(?<![\d.])(\d{1,2})\s*(?:a\.?m\.?|p\.?m\.?|o'clock)(?![A-Za-z])", text, re.IGNORECASE):
            out.append(Num(float(m.group(1)), "time"))
            spans.append(m.span())
        text = _mask(text, spans)
        for m in DIGIT_RE.finditer(text):
            v = float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))
            tail = text[m.end():m.end() + 9].lower()
            if re.match(r"\s?k\b", tail):
                v *= 1000
            elif re.match(r"\s?(million|mil)\b", tail):
                v *= 1_000_000
            elif re.match(r"\s?thousand\b", tail):
                v *= 1000
            out.append(Num(v, "n"))
        for m in EN_ORD_RE.finditer(text):
            out.append(Num(float(EN_ORD[m.group(1).lower()]), "n"))
        for m in EN_WORD_RE.finditer(_mask(text, [mm.span() for mm in DIGIT_RE.finditer(text)])):
            words = m.group().strip(" -")
            if re.fullmatch(r"(one|a|and)", words, re.IGNORECASE):
                continue
            v = _parse_en_words(words)
            if v is not None:
                kind = "time" if re.match(r"\s*o'clock", text[m.end():], re.IGNORECASE) else "n"
                out.append(Num(float(v), kind))
    else:
        for m in re.finditer(r"(\d{1,2})\s?시(?!간)(\s?반)?", text):
            out.append(Num(float(m.group(1)), "time"))
            if m.group(2):
                out.append(Num(30.0, "n"))
            spans.append(m.span())
        for m in NATIVE_RE.finditer(text):
            kind = "time" if m.group(2) == "시" else "n"
            out.append(Num(float(NATIVE[m.group(1)]), kind))
            if kind == "time" and re.match(r"\s?반", text[m.end():]):
                out.append(Num(30.0, "n"))
            spans.append(m.span())
        text = _mask(text, spans)
        # 3만 5천 / 15만 / 1.5만 같은 아라비아 숫자 + 한국어 단위
        tok = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(억|만|천|백)?")
        rank = {"억": 4, "만": 3, "천": 2, "백": 1, None: 0}
        mult = {"억": 1e8, "만": 1e4, "천": 1e3, "백": 1e2, None: 1}
        pending, last_end, last_rank = None, -10, 99
        for m in tok.finditer(text):
            v = float(m.group(1).replace(",", "")) * mult[m.group(2)]
            r = rank[m.group(2)]
            if pending is not None and m.start() - last_end <= 1 and r < last_rank and last_rank > 0:
                pending += v
            else:
                if pending is not None:
                    out.append(Num(pending, "n"))
                pending = v
            last_end, last_rank = m.end(), r
        if pending is not None:
            out.append(Num(pending, "n"))
        text = _mask(text, [m.span() for m in tok.finditer(text)])
        sino = r"[일이삼사오육칠팔구십백천만억]{1,8}"
        for m in re.finditer(rf"(?<![가-힣])({sino}(?:\s{sino})*)(?=\s?" + KO_COUNTER + ")", text):
            s = m.group(1).replace(" ", "")
            if len(s) < 2 and s not in SINO_SMALL and s not in SINO_BIG:
                continue
            v = _parse_sino(s)
            if v:
                out.append(Num(float(v), "n"))
    return [n for n in out if not (n.kind == "n" and n.value == 1)]


def _num_match(a: Num, b: Num) -> bool:
    if a.kind == "phone" or b.kind == "phone":
        return a.kind == b.kind and a.value == b.value
    if abs(a.value - b.value) < 1e-6:
        return True
    if "time" in (a.kind, b.kind) and a.value <= 24 and b.value <= 24:
        return a.value % 12 == b.value % 12
    return False


def compare_numbers(src: list[Num], tgt: list[Num]) -> list[str]:
    issues, pool = [], list(tgt)
    for s in src:
        hit = next((t for t in pool if _num_match(s, t)), None)
        if hit is None:
            issues.append(f"number_missing:{s.value:g}")
        else:
            pool.remove(hit)
    issues += [f"number_added:{t.value:g}" for t in pool]
    return issues


# ---------------------------------------------------------------- negation
NEG = {
    "en-US": (
        re.compile(r"\b(not|no|never|none|nobody|nothing|nowhere|neither|nor|cannot|without|no one|unhurt|uninjured|unharmed)\b|n't\b", re.I),
        re.compile(r"\b(instead|rather than|other than|except|only|stop|refuse|fine|okay|ok|all right|free)\b", re.I),
    ),
    "ko-KR": (
        re.compile(r"않|못|없|말고|마세요|마십시오|말아|지 ?마|아니|아닌|금지|불가"
                   r"|(?<![가-힣])안\s?(?!녕|내|전|에|쪽|경|심|과|개|건|부|마|방|색|약|면|정|팎|으로|까)[가-힣]"),
        re.compile(r"무사|괜찮|모르|몰라|싫|아직|대신|그만|무료"),
    ),
}


def neg_level(text: str, locale: str) -> int:
    hard, soft = NEG[locale]
    return 2 if hard.search(text) else 1 if soft.search(text) else 0


# ---------------------------------------------------------------- left / right
DIR_KO = {"left": re.compile(r"왼|좌회전|좌측|좌로"), "right": re.compile(r"오른|우회전|우측|우로")}
DIR_EN_PHRASE = {
    d: re.compile(
        rf"\b(turn|on|to|keep|the|go|your|my|far|bear)\s+{d}\b|\b{d}[- ](turn|side|lane|hand|exit|corner|spot|seat|door|mirror|lights?)\b|\b{d}\s+(at|after|onto|into|here)\b",
        re.I,
    )
    for d in ("left", "right")
}
DIR_EN_WORD = {d: re.compile(rf"\b{d}\b", re.I) for d in ("left", "right")}


def check_direction(en: str, ko: str) -> list[str]:
    issues = []
    for d in ("left", "right"):
        if DIR_EN_PHRASE[d].search(en) and not DIR_KO[d].search(ko):
            issues.append(f"direction_missing:{d}")
        if DIR_KO[d].search(ko) and not DIR_EN_WORD[d].search(en):
            issues.append(f"direction_mismatch:{d}")
    return issues


# ---------------------------------------------------------------- critical terms
def check_critical_terms(src: str, src_locale: str, tgt: str, tgt_locale: str) -> list[str]:
    a = set(match_concepts(src, src_locale, {"fuel"}))
    b = set(match_concepts(tgt, tgt_locale, {"fuel"}))
    return [f"term_missing:{c}" for c in sorted(a - b)] + [f"term_added:{c}" for c in sorted(b - a)]


LEAK_RE = re.compile(r"^\s*(translation|translated|here is|note|번역|통역|참고)\s*[:：]|\n", re.I)


def validate_translation(out: dict, source: str, src_locale: str, tgt_locale: str, structural_only: bool = False) -> list[str]:
    # 검증: 모델 출력 JSON 계약
    status = out.get("status")
    tt, cq = out.get("translated_text"), out.get("clarification_question")
    if status == "clarification_required":
        if not isinstance(cq, str) or not cq.strip() or len(cq) > 300:
            return ["bad_clarification"]
        if tt not in (None, ""):
            return ["mixed_output"]
        if not structural_only and not in_locale_script(cq, src_locale):
            return ["clarification_wrong_language"]
        return []
    if status != "ok":
        return ["bad_status"]
    if not isinstance(tt, str) or not tt.strip():
        return ["empty_translation"]
    if cq not in (None, ""):
        return ["mixed_output"]
    if structural_only:
        return []
    issues = []
    if LEAK_RE.search(tt):
        issues.append("extra_text")
    if len(tt) > 3 * len(source) + 60:
        issues.append("too_long")
    if not in_locale_script(tt, tgt_locale):
        issues.append("wrong_language")
    if HAN_KANA.search(tt) and not HAN_KANA.search(source):
        issues.append("foreign_script")
    if tt.strip().lower() == source.strip().lower():
        issues.append("untranslated")
    issues += compare_numbers(extract_numbers(source, src_locale), extract_numbers(tt, tgt_locale))
    a, b = neg_level(source, src_locale), neg_level(tt, tgt_locale)
    if max(a, b) == 2 and min(a, b) == 0:
        issues.append("negation_mismatch")
    issues += check_critical_terms(source, src_locale, tt, tgt_locale)
    en, ko = (source, tt) if src_locale == "en-US" else (tt, source)
    issues += check_direction(en, ko)
    return issues


COORD_RE = re.compile(r"\d{1,3}\.\d{4,}")


def validate_destination(out: dict) -> list[str]:
    if out.get("intent") not in ("set_destination", "other"):
        return ["bad_intent"]
    issues = []
    for k in ("query_original", "query_ko_candidate", "region_hint"):
        v = out.get(k)
        if v is not None and (not isinstance(v, str) or len(v) > 80 or COORD_RE.search(v)):
            issues.append(f"bad_field:{k}")
    ko = out.get("query_ko_candidate")
    if isinstance(ko, str) and ko and not HANGUL.search(ko):
        issues.append("query_ko_not_korean")
    if not isinstance(out.get("needs_confirmation"), bool):
        issues.append("bad_field:needs_confirmation")
    return issues
