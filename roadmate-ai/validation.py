"""번역 출력 검증: 구조·언어·숫자·부정·연료·좌우. 실패하면 재생하지 않는다."""
import re
from dataclasses import dataclass

from prompting import concept_spans, match_concepts

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
    exact: bool = False  # time: 오전·오후(a.m./p.m.)나 24시간제로 확정된 값
    unit: str | None = None  # 금액: KRW | USD


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
DIGIT_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?!\d|,\d)")
_EN_DAYPART = r"(in the morning|in the afternoon|in the evening|at night)"
EN_TIME_TAIL = re.compile(rf"\s*(?:o'clock(?:\s+{_EN_DAYPART})?|([ap])\.?\s?m\.?(?![A-Za-z])|{_EN_DAYPART})", re.IGNORECASE)
EN_ORD_ANY = re.compile(r"\b(" + "|".join(EN_ORD) + r")\s+(?=[A-Za-z])", re.IGNORECASE)
KO_ORD = re.compile(r"(?<![가-힣])(첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열|\d{1,2})\s?번째")
KO_MERIDIEM = re.compile(r"(오전|새벽|아침|오후|저녁|낮)\s*$")
EN_PAST_RE = re.compile(r"\b(quarter|half)\s+(past|after|to)\s+(\d{1,2}|" + "|".join(EN_UNITS) + r")\b", re.IGNORECASE)
EN_SPAN_RE = re.compile(r"\b(?:(half)\s+(?:an\s+)?hour|(?:a\s+)?(quarter)\s+(?:of\s+an\s+)?hour"
                        r"|(?:an|one|a)\s+hour(\s+and\s+a\s+half)?)\b", re.IGNORECASE)
DUR_EN = re.compile(r"\s?(?:(minutes?|mins?)|(hours?|hrs?))\b", re.IGNORECASE)
DUR_KO = re.compile(r"\s?(?:(분)|(시간)(\s?반)?)")
UNIT_ABBR = {"km", "kg", "cc", "ml", "cm", "mm", "kw", "kwh", "mph", "kmh", "gb"}
PLATE_RE = re.compile(r"\d{2,3}\s?[가-힣]\s?\d{4}")
CUR_EN = re.compile(r"\s?(?:k|thousand|million|mil)?\s?(won|krw|dollars?|usd|bucks)\b", re.IGNORECASE)
CUR_KO = re.compile(r"\s?(원|달러|불)")


def _time(h: int, pm: bool | None) -> Num:
    # 검증: 오전·오후가 있거나 24시간제(0시, 13~24시)면 확정 값, 아니면 12시간 단위로만 비교
    if pm is None:
        return Num(float(h), "time", exact=h == 0 or 12 < h <= 24)
    return Num(float(h % 12 + (12 if pm else 0)), "time", exact=True)


def _en_pm(t: re.Match | None) -> bool | None:
    # 검증: a.m./p.m., in the morning/evening 등 → 오후 여부. o'clock만 있으면 미확정(None)
    part = t and (t.group(2) or t.group(1) or t.group(3))
    return None if not part else part.lower() == "p" or part.lower().endswith(("afternoon", "evening", "night"))


def _ko_pm(before: str) -> bool | None:
    m = KO_MERIDIEM.search(before)
    return None if not m else m.group(1) in ("오후", "저녁", "낮")


def _unit(before: str, after: str, locale: str) -> str | None:
    # 검증: 금액은 통화까지 비교(30,000 won ≠ 3만 달러)
    if re.search(r"\$\s?$", before):
        return "USD"
    if re.search(r"₩\s?$", before):
        return "KRW"
    m = (CUR_EN if locale == "en-US" else CUR_KO).match(after)
    if not m:
        return None
    return "KRW" if m.group(1).lower() in ("won", "krw", "원") else "USD"


def _num(v: float, before: str, after: str, locale: str) -> Num:
    # 검증: 금액은 통화, 시간 길이는 분 단위로 맞춰 비교한다(half an hour = 30분 = 반 시간 ≠ 30시간)
    cur = _unit(before, after, locale)
    if cur:
        return Num(v, "n", unit=cur)
    m = (DUR_EN if locale == "en-US" else DUR_KO).match(after)
    if not m:
        return Num(v, "n")
    if m.group(1):
        return Num(v, "n", unit="min")
    return Num(v * 60 + (30 if locale == "ko-KR" and m.group(3) else 0), "n", unit="min")


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
        end, pm = m.end(), _ko_pm(text[:m.start()]) if locale == "ko-KR" else None
        t = EN_TIME_TAIL.match(text, end) if locale == "en-US" else None
        if _en_pm(t) is not None:
            end, pm = t.end(), _en_pm(t)
        out.append(_time(int(m.group(1)), pm))
        if int(m.group(2)):
            out.append(Num(float(m.group(2)), "n"))
        spans.append((m.start(), end))
    text = _mask(text, spans)

    if locale == "en-US":
        # 검증: 말로 하는 시각(quarter past 4 p.m.)은 숫자 시각보다 먼저 읽어 두 번 세지 않는다
        for m in EN_PAST_RE.finditer(text):
            h = int(m.group(3)) if m.group(3).isdigit() else EN_UNITS[m.group(3).lower()]
            mins = 15 if m.group(1).lower() == "quarter" else 30
            t = EN_TIME_TAIL.match(text, m.end())
            anchor = _time(h, _en_pm(t))  # 오전·오후를 먼저 적용한 뒤 "to"를 뺀다(quarter to 12 p.m. = 11:45)
            if m.group(2).lower() == "to":
                val = (anchor.value - 1) % 24 if anchor.exact else float((h - 1) or 12)
                anchor, mins = Num(val, "time", exact=anchor.exact), 60 - mins
            out += [anchor, Num(float(mins), "n")]
            spans.append((m.start(), t.end() if t else m.end()))
        text = _mask(text, spans)
        for m in re.finditer(r"(?<![\d.,])(\d{1,2})\b", text):
            t = EN_TIME_TAIL.match(text, m.end())
            if t:
                out.append(_time(int(m.group(1)), _en_pm(t)))
                spans.append((m.start(), t.end()))
        # 검증: 정오·자정, 말로 하는 시간 길이(half an hour, an hour and a half)
        for m in re.finditer(r"\b(noon|midday|midnight)\b", text, re.IGNORECASE):
            out.append(Num(0.0 if m.group(1).lower() == "midnight" else 12.0, "time", exact=True))
            spans.append(m.span())
        for m in EN_SPAN_RE.finditer(text):
            v = 30.0 if m.group(1) else 15.0 if m.group(2) else 90.0 if m.group(3) else 60.0
            out.append(Num(v, "n", unit="min"))
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
            out.append(_num(v, text[:m.start()], text[m.end():], locale))
        ords = list(EN_ORD_RE.finditer(text))
        for m in ords:
            out.append(Num(float(EN_ORD[m.group(1).lower()]), "n"))
        for m in EN_ORD_ANY.finditer(_mask(text, [m.span() for m in ords])):
            out.append(Num(float(EN_ORD[m.group(1).lower()]), "ord"))
        for m in EN_WORD_RE.finditer(_mask(text, [mm.span() for mm in DIGIT_RE.finditer(text)])):
            words = m.group().strip(" -")
            t = EN_TIME_TAIL.match(text, m.end())
            if not t and re.fullmatch(r"(one|a|and)", words, re.IGNORECASE):
                continue
            v = _parse_en_words(words)
            if v is None:
                continue
            if t and v <= 12:
                out.append(_time(v, _en_pm(t)))
            else:
                out.append(_num(float(v), text[:m.start()], text[m.end():], locale))
    else:
        for m in KO_ORD.finditer(text):
            g = m.group(1)
            out.append(Num(float(g) if g.isdigit() else float({**NATIVE, "첫": 1}[g]), "ord"))
        text = _mask(text, [m.span() for m in KO_ORD.finditer(text)])
        for m in re.finditer(r"정오|자정", text):
            out.append(Num(12.0 if m.group() == "정오" else 0.0, "time", exact=True))
        for m in re.finditer(r"(?<![가-힣])반\s?시간", text):
            out.append(Num(30.0, "n", unit="min"))
            spans.append(m.span())
        for m in re.finditer(r"(\d{1,2})\s?시(?!간)(\s?반)?", text):
            out.append(_time(int(m.group(1)), _ko_pm(text[:m.start()])))
            if m.group(2):
                out.append(Num(30.0, "n"))
            spans.append(m.span())
        for m in NATIVE_RE.finditer(text):
            if m.group(1) == "두" and m.group(2) == "시" and re.search(r"[어아여해놔]\s?$", text[:m.start()]):
                continue  # 검증: "찍어 두시는·놔두시면"은 보조동사 '두다'라 시각이 아니다(지금 두시입니다는 시각)
            if m.group(2) == "시":
                out.append(_time(NATIVE[m.group(1)], _ko_pm(text[:m.start()])))
            elif m.group(2) == "시간":
                out.append(_num(float(NATIVE[m.group(1)]), "", text[m.start(2):], locale))
            else:
                out.append(Num(float(NATIVE[m.group(1)]), "n"))
            if m.group(2) == "시" and re.match(r"\s?반", text[m.end():]):
                out.append(Num(30.0, "n"))
            spans.append(m.span())
        text = _mask(text, spans)
        # 3만 5천 / 15만 / 1.5만 같은 아라비아 숫자 + 한국어 단위
        tok = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s?(억|만|천|백)?")
        rank = {"억": 4, "만": 3, "천": 2, "백": 1, None: 0}
        mult = {"억": 1e8, "만": 1e4, "천": 1e3, "백": 1e2, None: 1}
        pending, start, last_end, last_rank = None, 0, -10, 99
        for m in tok.finditer(text):
            v = float(m.group(1).replace(",", "")) * mult[m.group(2)]
            r = rank[m.group(2)]
            if pending is not None and m.start() - last_end <= 1 and r < last_rank and last_rank > 0:
                pending += v
            else:
                if pending is not None:
                    out.append(_num(pending, text[:start], text[last_end:], locale))
                pending, start = v, m.start()
            last_end, last_rank = m.end(), r
        if pending is not None:
            out.append(_num(pending, text[:start], text[last_end:], locale))
        text = _mask(text, [m.span() for m in tok.finditer(text)])
        sino = r"[일이삼사오육칠팔구십백천만억]{1,8}"
        for m in re.finditer(rf"(?<![가-힣])({sino}(?:\s{sino})*)(?=\s?" + KO_COUNTER + ")", text):
            s = m.group(1).replace(" ", "")
            if len(s) < 2 and s not in SINO_SMALL and s not in SINO_BIG:
                continue
            v = _parse_sino(s)
            if v:
                out.append(_num(float(v), text[:m.start()], text[m.end():], locale))
    return [n for n in out if not (n.kind == "n" and n.value == 1 and n.unit is None)]


def _num_match(a: Num, b: Num) -> bool:
    if a.kind == "phone" or b.kind == "phone":
        return a.kind == b.kind and a.value == b.value
    if a.unit and b.unit and a.unit != b.unit:
        return False
    if abs(a.value - b.value) < 1e-6:
        return True
    if "time" in (a.kind, b.kind) and a.value <= 24 and b.value <= 24:
        if a.exact and b.exact:
            return a.value % 24 == b.value % 24
        if a.exact:
            return False  # 원문의 오전·오후(또는 24시간제)가 번역에서 빠짐
        return a.value % 12 == b.value % 12
    return False


def compare_numbers(src: list[Num], tgt: list[Num]) -> list[str]:
    # 검증: 서수(두 번째)는 양쪽에 다 있을 때만 비교한다("a second driver" = "운전자 한 명 더")
    both_ord = any(n.kind == "ord" for n in src) and any(n.kind == "ord" for n in tgt)
    issues, pool = [], list(tgt)
    for s in src:
        hit = next((t for t in pool if _num_match(s, t)), None)
        if hit is not None:
            pool.remove(hit)
        elif (s.kind != "ord" or both_ord) and not _is_zero(s):
            issues.append(f"number_missing:{s.value:g}")
    issues += [f"number_added:{t.value:g}" for t in pool if (t.kind != "ord" or both_ord) and not _is_zero(t)]
    return issues


def _is_zero(n: Num) -> bool:
    # 검증: 0은 "없다"와 같은 뜻으로 옮겨질 수 있어 짝이 없어도 넘어간다(면책금이 없어요 = deductible is zero)
    return n.kind == "n" and n.value == 0


def add_meridiem(tt: str, source: str, src_locale: str, tgt_locale: str) -> str:
    # 검증: 원문에 오전·오후가 있는데 한국어 번역이 "6시"처럼 뺐으면 원문 기준으로 "오후 6시"를 채운다(같은 시각일 때만)
    if src_locale != "en-US" or tgt_locale != "ko-KR":
        return tt
    exact = [n.value for n in extract_numbers(source, src_locale) if n.kind == "time" and n.exact]
    for m in reversed(list(re.finditer(r"(?<!\d)(\d{1,2})\s?시(?!간)", tt))):
        h = int(m.group(1))
        hit = next((v for v in exact if 1 <= h <= 12 and h % 12 == v % 12), None)
        if hit is not None and _ko_pm(tt[:m.start()]) is None:
            tt = tt[:m.start()] + ("오후 " if hit >= 12 else "오전 ") + tt[m.start():]
    return tt


# ---------------------------------------------------------------- negation
NEG = {
    "en-US": (
        re.compile(r"\b(not|no|never|none|nobody|nothing|nowhere|neither|nor|cannot|without|no one|unhurt|uninjured|unharmed)\b|n't\b", re.I),
        re.compile(r"\b(instead|rather than|other than|except|excluding|apart from|zero|only|stop|refuse|fine|okay|ok|all right|free)\b", re.I),
    ),
    "ko-KR": (
        # 검증: "A, 아니면 B"(또는)는 부정이 아니지만 "회원이 아니면"(조건부 부정)은 부정이다
        re.compile(r"않|(?<!잘)못|없|말고|마세요|마십시오|말아|지 ?마|아니(?!면)|(?<=[이가게] )아니면|(?<=[이가게])아니면"
                   r"|아닌|아닙|아냐|아녜|금지|불가"
                   r"|(?<![가-힣])안\s?(?!녕|내|전|에|쪽|경|심|과|개|건|부|마|방|색|약|면|정|팎|으로|까)[가-힣]"),
        re.compile(r"무사|괜찮|모르|몰라|싫|아직|대신|그만|무료|제외|빼고"),
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


# 검증: 뒤바뀌기 쉬운 반대말(안/밖, 전/후). 원문에 한쪽만 있는데 번역에 반대쪽만 있으면 실패
OPPOSITES = [
    ("inside", r"\binside\b", r"안에|안으로|안쪽|내부", "outside", r"\boutside\b", r"밖|바깥|외부"),
    ("before", r"\bbefore\b", r"전에|전까지|이전", "after", r"\bafter\b", r"후에|다음에|이후"),
]


def check_opposites(en: str, ko: str) -> list[str]:
    issues = []
    for na, ea, ka, nb, eb, kb in OPPOSITES:
        for name, e1, k1, e2, k2 in ((na, ea, ka, eb, kb), (nb, eb, kb, ea, ka)):
            en_only = re.search(e1, en, re.I) and not re.search(e2, en, re.I)
            ko_flip = re.search(k2, ko) and not re.search(k1, ko)
            if en_only and ko_flip:
                issues.append(f"opposite_mismatch:{name}")
    return issues


# ---------------------------------------------------------------- critical terms
def check_critical_terms(src: str, src_locale: str, tgt: str, tgt_locale: str) -> list[str]:
    a = set(match_concepts(src, src_locale, {"fuel"}))
    b = set(match_concepts(tgt, tgt_locale, {"fuel"}))
    return [f"term_missing:{c}" for c in sorted(a - b)] + [f"term_added:{c}" for c in sorted(b - a)]


_EN_FUEL_VERB = r"(?:put(?:\s+in)?|use|want|need|take|pump|add|give\s+me|fill\s+(?:it\s+)?(?:up\s+)?with)"
FUEL_NEG_BEFORE = {
    "en-US": re.compile(
        r"(?:\b(?:not|no|never|instead\s+of|rather\s+than|other\s+than|except|excluding|apart\s+from|without)|n't"
        rf"|\b(?:not|cannot)\s+{_EN_FUEL_VERB}|n't\s+{_EN_FUEL_VERB})\s+(?:(?:any|the|a|an|in|it|some)\s+)*$", re.I),
    "ko-KR": None,
}
FUEL_NEG_AFTER = {
    "en-US": re.compile(r"^\s+(?:(?:is|was)\s+not|isn't|wasn't|is\s+(?:wrong|prohibited|forbidden)|not\s+allowed)\b", re.I),
    "ko-KR": re.compile(r"^(?:차)?(?:은|는|이|가|을|를|으로|로|도|만)?\s*(?:말고|아니(?!면)|아닙|아냐|아녜|대신|빼|제외|금지|절대|안\s?(?:돼|되|됩|넣|쓰)"
                        r"|(?:넣|주유하|주유|쓰|사용하|채우)(?:지\s?(?:마|말|않)|으?시?면\s?안))"),
}
CLAUSE = re.compile(r"[,.;!?]")


def fuel_polarity(text: str, locale: str) -> dict[str, set[bool]]:
    """연료 개념 id → {True(넣을 연료) / False(부정된 연료)}. "not diesel", "경유 말고"처럼 바로 붙은 부정만 본다."""
    spans = [(s, e, cid) for s, e, cid, cat in concept_spans(text, locale) if cat == "fuel"]
    out: dict[str, set[bool]] = {}
    for i, (s, e, cid) in enumerate(spans):
        before = CLAUSE.split(text[spans[i - 1][1] if i else 0:s])[-1]
        after = CLAUSE.split(text[e:spans[i + 1][0] if i + 1 < len(spans) else len(text)])[0]
        pre = FUEL_NEG_BEFORE[locale]
        out.setdefault(cid, set()).add(not ((pre and pre.search(before)) or FUEL_NEG_AFTER[locale].search(after)))
    return out


def check_fuel_polarity(src: str, src_locale: str, tgt: str, tgt_locale: str) -> list[str]:
    # 검증: "휘발유, 경유 말고"가 "경유, 휘발유 말고"로 뒤집힌 출력 차단(넣을 연료·부정 연료가 함께 나온 문장)
    a, b = fuel_polarity(src, src_locale), fuel_polarity(tgt, tgt_locale)
    if not any(len(set().union(*p.values())) == 2 for p in (a, b) if p):
        return []
    return [f"fuel_polarity_mismatch:{c}" for c in sorted(a.keys() & b.keys()) if a[c] != b[c]]


# 파라미터 튜닝: 번역/원문 글자 수 최소 비율(정답 자료 최솟값 영→한 0.30, 한→영 1.33보다 낮게). 짧은 원문은 검사 안 함
MIN_LEN_RATIO = {"en-US": (30, 0.22), "ko-KR": (12, 0.8)}
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
        if structural_only:
            return []
        if " ".join(cq.lower().split()) == " ".join(source.lower().split()):
            return ["clarification_echo"]
        if not in_locale_script(cq, src_locale):
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
    min_src, ratio = MIN_LEN_RATIO[src_locale]
    if len(source) >= min_src and len(tt) < ratio * len(source):
        issues.append("too_short")
    # 검증: 번호판(34하 5678)의 한글은 원문 그대로 옮겨도 된다
    script_tt = PLATE_RE.sub(" ", tt) if PLATE_RE.search(source) else tt
    if not in_locale_script(script_tt, tgt_locale):
        issues.append("wrong_language")
    if HAN_KANA.search(tt) and not HAN_KANA.search(source):
        issues.append("foreign_script")
    if tgt_locale == "ko-KR":
        # 검증: "페anut"처럼 한글에 붙은 영문 조각이 원문 단어가 아니면 깨진 출력(U턴·B동·GPS가처럼 원문·약어는 허용)
        src_words = {w.lower() for w in re.findall(r"[A-Za-z]+", source)}
        frags = re.findall(r"(?<=[가-힣])[A-Za-z]{2,}|(?<![\dA-Za-z])[A-Za-z]{2,}(?=[가-힣])", tt)
        if any(f.lower() not in src_words | UNIT_ABBR and not f.isupper() for f in frags):
            issues.append("mixed_script")
    if tt.strip().lower() == source.strip().lower():
        issues.append("untranslated")
    issues += compare_numbers(extract_numbers(source, src_locale), extract_numbers(tt, tgt_locale))
    a, b = neg_level(source, src_locale), neg_level(tt, tgt_locale)
    if max(a, b) == 2 and min(a, b) == 0:
        issues.append("negation_mismatch")
    issues += check_critical_terms(source, src_locale, tt, tgt_locale)
    issues += check_fuel_polarity(source, src_locale, tt, tgt_locale)
    en, ko = (source, tt) if src_locale == "en-US" else (tt, source)
    issues += check_direction(en, ko)
    issues += check_opposites(en, ko)
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
