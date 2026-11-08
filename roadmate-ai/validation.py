"""번역 출력 검증: 구조·언어·숫자·부정·연료·좌우. 실패하면 재생하지 않는다."""
import re
from dataclasses import dataclass

from prompting import LANG_KEY, concept_spans, fold, match_concepts

HANGUL = re.compile(r"[가-힣]")
LATIN = re.compile(r"[A-Za-z]")
HAN_KANA = re.compile(r"[぀-ヿ一-鿿]")
# 검증: 영어·스페인어는 같은 로마자라 자주 쓰는 기능어로 구분한다(겹치는 no, a, me는 뺌)
FUNC_WORDS = {
    "en": {"the", "is", "are", "you", "your", "please", "it", "to", "of", "and", "my", "this", "that", "can", "i",
           "we", "what", "where", "with", "for", "have", "do", "does", "was", "there", "here", "car"},
    "es": {"el", "la", "los", "las", "de", "del", "que", "y", "es", "por", "favor", "usted", "su", "una", "un", "se",
           "en", "con", "para", "esta", "donde", "aqui", "puede", "mi", "lo", "le", "al", "hay", "muy", "carro"},
}


def script_counts(text: str) -> tuple[int, int]:
    return len(HANGUL.findall(text)), len(LATIN.findall(text))


def latin_lang(text: str) -> str | None:
    """로마자 문장이 영어인지 스페인어인지. 확실하지 않으면 None(짧은 문장, 고유명사)."""
    words = re.findall(r"[a-z]+", fold(text).lower())
    en, es = (sum(w in FUNC_WORDS[k] for w in words) for k in ("en", "es"))
    if en >= 2 and en > 2 * es:
        return "en"
    if es >= 2 and es > 2 * en:
        return "es"
    return None


def in_locale_script(text: str, locale: str) -> bool:
    h, l = script_counts(text)
    if locale == "ko-KR":
        return h > 0 and h >= l
    return l > 0 and h == 0 and latin_lang(text) in (None, LANG_KEY[locale])


def looks_like_other_language(text: str, locale: str) -> bool:
    # 검증: 버튼 방향과 실제 발화 언어가 반대인 경우
    h, l = script_counts(text)
    if locale != "ko-KR":
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
    r"(?<![가-힣])(열한|열두|다섯|여섯|일곱|여덟|아홉|스무|스물|한|두|세|네|열)\s?(시간|시|명|분(?!화|류|석|야|노)|개|대|번|살|사람|장"
    r"|병|잔|곳|군데|달)"
)
# 검증: 단위 없이 쓴 고유어 수(어른 셋, 아이 둘). 열(열이 나요)·하나(하나도)는 뜻이 갈려 뺀다
NATIVE_ALONE = {"둘": 2, "셋": 3, "넷": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9}
NATIVE_ALONE_RE = re.compile(r"(?<![가-힣])(둘|셋|넷|다섯|여섯|일곱|여덟|아홉)(?=[\s,.?!]|$|이|은|만|도|요|입니|이에|이서)")
PHONE_RE = re.compile(r"(?<!\d)(?:\+?82[\s-]?)?0?\d{1,3}[\s-]\d{3,4}[\s-]\d{4}(?!\d)")
CLOCK_RE = re.compile(r"(?<!\d)([01]?\d|2[0-4]):([0-5]\d)(?!\d)")
DIGIT_RE = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?!\d|,\d)")
_EN_DAYPART = r"(in the morning|in the afternoon|in the evening|at night|tonight|this morning|this afternoon|this evening)"
EN_TIME_TAIL = re.compile(rf"\s*(?:o'clock(?:\s+{_EN_DAYPART})?|([ap])\.?\s?m\.?(?![A-Za-z])|{_EN_DAYPART})", re.IGNORECASE)
EN_ORD_ANY = re.compile(r"\b(" + "|".join(EN_ORD) + r")\s+(?=[A-Za-z])", re.IGNORECASE)
KO_ORD = re.compile(r"(?<![가-힣])(첫|두|세|네|다섯|여섯|일곱|여덟|아홉|열|\d{1,2})\s?번째")
KO_MERIDIEM = re.compile(r"(오전|새벽|아침|오후|저녁|낮|밤)\s*$")
EN_PAST_RE = re.compile(r"\b(quarter|half)\s+(past|after|to)\s+(\d{1,2}|" + "|".join(EN_UNITS) + r")\b", re.IGNORECASE)
EN_SPAN_RE = re.compile(r"\b(?:(half)\s+(?:an\s+)?hour|(?:a\s+)?(quarter)\s+(?:of\s+an\s+)?hour"
                        r"|(?:an|one|a)\s+hour(\s+and\s+a\s+half)?|(?:a|one)\s+(minutes?|mins?))\b", re.IGNORECASE)
DUR_EN = re.compile(r"\s?(?:(minutes?|mins?)|(hours?|hrs?))\b", re.IGNORECASE)
DUR_KO = re.compile(r"\s?(?:(분)|(시간)(\s?반)?)")
UNIT_ABBR = {"km", "kg", "cc", "ml", "cm", "mm", "kw", "kwh", "mph", "kmh", "gb"}
PLATE_RE = re.compile(r"\d{2,3}\s?[가-힣]\s?\d{4}")
CUR_EN = re.compile(r"\s?(?:k|thousand|million|mil)?\s?(won|krw|dollars?|usd|bucks)\b", re.IGNORECASE)
CUR_KO = re.compile(r"\s?(원|달러|불)")

# 스페인어 숫자는 강세를 뺀 글자(fold)로 읽는다(dieciséis = dieciseis, mañana = manana)
ES_UNITS = {
    "cero": 0, "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7,
    "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiun": 21,
    "veintiuno": 21, "veintiuna": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25,
    "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29, "treinta": 30, "cuarenta": 40,
    "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100, "ciento": 100,
    **{f"{p}ientos": v for p, v in (("dosc", 200), ("tresc", 300), ("cuatroc", 400), ("seisc", 600), ("setec", 700),
                                     ("ochoc", 800), ("novec", 900))},
    **{f"{p}ientas": v for p, v in (("dosc", 200), ("tresc", 300), ("cuatroc", 400), ("seisc", 600), ("setec", 700),
                                     ("ochoc", 800), ("novec", 900))},
    "quinientos": 500, "quinientas": 500,
}
ES_SCALES = {"mil": 1_000, "millon": 1_000_000, "millones": 1_000_000}
_ES_TENS = "treinta|cuarenta|cincuenta|sesenta|setenta|ochenta|noventa"
_ES_ONES = "uno|una|un|dos|tres|cuatro|cinco|seis|siete|ocho|nueve"
_ES_TOKEN = (rf"(?:(?:{_ES_TENS})\s+y\s+(?:{_ES_ONES})|"
             + "|".join(sorted([*ES_UNITS, *ES_SCALES], key=len, reverse=True)) + ")")
ES_WORD_RE = re.compile(rf"\b{_ES_TOKEN}(?:\s+{_ES_TOKEN})*\b", re.IGNORECASE)
_ES_DAYPART = r"(?:de|en|por)\s+la\s+(manana|madrugada|tarde|noche)"
ES_TIME_TAIL = re.compile(rf"\s*(?:en\s+punto(?:\s+{_ES_DAYPART})?|([ap])\.?\s?m\.?(?![a-z])|{_ES_DAYPART})", re.IGNORECASE)
_ES_HOUR = r"\d{1,2}|" + "|".join(sorted([w for w, v in ES_UNITS.items() if 1 <= v <= 24], key=len, reverse=True))
# 검증: "a las tres y media", "hasta las 5 menos cuarto"처럼 la/las가 붙은 말만 시각으로 본다(las dos personas는 숫자)
_ES_MIN = "|".join(["media", "cuarto", r"\d{1,2}", rf"(?:{_ES_TENS})\s+y\s+(?:{_ES_ONES})",
                    *sorted([w for w, v in ES_UNITS.items() if 1 <= v < 60], key=len, reverse=True)])
# 검증: 시·분·때를 한 덩어리로 읽는다(a las tres y diez de la tarde = 15:10, cinco menos cuarto = 4:45)
ES_HOUR_RE = re.compile(rf"\b(?:a|hasta|desde|son|es|para|de|entre|y|tipo|sobre|por)\s+las?\s+(?P<hour>{_ES_HOUR})\b"
                        rf"(?:\s+(?P<op>y|menos)\s+(?P<minute>{_ES_MIN})\b(?:\s+minutos?\b)?)?", re.IGNORECASE)
ES_TO_RE = re.compile(rf"\b(?:un\s+)?(?P<minute>{_ES_MIN})\b(?:\s+(?P<unit>minutos?))?\s+para\s+las?\s+"
                      rf"(?P<hour>{_ES_HOUR})\b", re.IGNORECASE)
ES_TO_BEFORE = re.compile(r"(?:^\s*|\b(?:son|es|faltan?|lleg\w*|vuelv\w*|regres\w*|vemos)\s+(?:a\s+)?)$", re.I)
# 검증: "a las dos personas"(두 사람에게)처럼 뒤에 세는 말이 오면 시각이 아니다
ES_COUNT_AFTER = re.compile(r"\s+(?:personas?|gente|adultos?|ninos?|ninas?|bebes?|senoras?|senores?|hombres?|mujeres"
                            r"|clientes?|pasajeros?|amigos?|chicos?|chicas?|carros?|autos?|coches?|maletas?|llaves?"
                            r"|tarjetas?|cosas?|veces|noches|dias?|semanas?|meses|anos?|litros?|asientos?|lugares?)\b",
                            re.IGNORECASE)
ES_SPAN_RE = re.compile(r"\b(?:(media)\s+hora|(?:un\s+)?(cuarto)\s+de\s+hora|(?:una\s+)?hora\s+y\s+(media|(?:un\s+)?cuarto)"
                        r"|una\s+hora)\b", re.IGNORECASE)
DUR_ES = re.compile(r"\s?(?:(minutos?|mins?)|(horas?|hrs?)(\s+y\s+(?:media|(?:un\s+)?cuarto))?)\b", re.IGNORECASE)
CUR_ES = re.compile(r"\s?(?:mil|millones|millon)?\s?(?:de\s+)?(wones|wons?|krw|dolares|dolar|usd)\b", re.IGNORECASE)
ES_ORD = {"primer": 1, "primero": 1, "primera": 1, "segundo": 2, "segunda": 2, "tercer": 3, "tercero": 3,
          "tercera": 3, "cuarto": 4, "cuarta": 4, "quinto": 5, "quinta": 5, "sexto": 6, "sexta": 6, "septimo": 7,
          "septima": 7, "octavo": 8, "octava": 8, "noveno": 9, "novena": 9, "decimo": 10, "decima": 10}
ES_ORD_RE = re.compile(
    r"\b(" + "|".join(ES_ORD) + r")\s+(piso|salida|carril|calle|puerta|fila|nivel|semaforo|cruce|esquina|dia|vez"
    r"|lugar|asiento|edificio|cajon|espacio|parada|glorieta|entrada|cuadra|auto|carro)s?\b", re.IGNORECASE)
ES_ORD_ANY = re.compile(r"\b(" + "|".join(ES_ORD) + r")\s+(?=[a-z])", re.IGNORECASE)


def _es_prep(text: str) -> str:
    # 검증: 30.000(점 천 단위) → 30,000, 3,5(쉼표 소수) → 3.5. 글자 수는 그대로
    text = re.sub(r"(?<![\d.,])\d{1,3}(?:\.\d{3})+(?![\d,]|\.\d)", lambda m: m.group().replace(".", ","), text)
    return re.sub(r"(?<![\d.,])(\d+),(\d{1,2})(?!\d)", r"\1.\2", text)


def _parse_es_words(s: str) -> int | None:
    total, cur, seen = 0, 0, False
    for w in s.lower().split():
        if w == "y":
            continue
        seen = True
        if w in ES_UNITS:
            cur += ES_UNITS[w]
        elif w in ES_SCALES:
            total += (cur or 1) * ES_SCALES[w]
            cur = 0
    return total + cur if seen else None


def _time(h: int, pm: bool | None) -> Num:
    # 검증: 오전·오후가 있거나 24시간제(0시, 13~24시)면 확정 값, 아니면 12시간 단위로만 비교
    if pm is None:
        return Num(float(h), "time", exact=h == 0 or 12 < h <= 24)
    return Num(float(h % 12 + (12 if pm else 0)), "time", exact=True)


def _night_pm(h: int | None) -> bool | None:
    # 검증: 밤 9시 = 21시, 밤 12시(12 de la noche, 12 at night) = 자정, 밤 1~4시 = 새벽
    return None if h is None else not (h == 12 or 1 <= h <= 4)


def _en_pm(t: re.Match | None, h: int | None = None) -> bool | None:
    # 검증: a.m./p.m., in the morning/evening 등 → 오후 여부. o'clock만 있으면 미확정(None)
    part = t and (t.group(2) or t.group(1) or t.group(3))
    if not part:
        return None
    part = part.lower()
    if part.endswith("night"):
        return _night_pm(h)
    return part == "p" or part.endswith(("afternoon", "evening"))


def _ko_pm(before: str, h: int | None = None) -> bool | None:
    m = KO_MERIDIEM.search(before)
    if not m:
        return None
    return _night_pm(h) if m.group(1) == "밤" else m.group(1) in ("오후", "저녁", "낮")


def _es_pm(t: re.Match | None, h: int | None = None) -> bool | None:
    # 검증: p. m., de la tarde → 오후, de la noche는 시각을 같이 본다(doce de la noche = 자정). en punto만 있으면 미확정
    part = t and (t.group(2) or t.group(1) or t.group(3))
    if not part:
        return None
    part = part.lower()
    return _night_pm(h) if part == "noche" else part in ("p", "tarde")


TIME_TAIL = {"en": (EN_TIME_TAIL, _en_pm), "es": (ES_TIME_TAIL, _es_pm)}
CUR = {"en": CUR_EN, "es": CUR_ES, "ko": CUR_KO}
DUR = {"en": DUR_EN, "es": DUR_ES, "ko": DUR_KO}


def _unit(before: str, after: str, lang: str) -> str | None:
    # 검증: 금액은 통화까지 비교(30,000 won ≠ 3만 달러)
    if re.search(r"\$\s?$", before):
        return "USD"
    if re.search(r"₩\s?$", before):
        return "KRW"
    m = CUR[lang].match(after)
    if not m:
        return None
    return "KRW" if m.group(1).lower() in ("won", "wons", "wones", "krw", "원") else "USD"


def _num(v: float, before: str, after: str, lang: str) -> Num:
    # 검증: 금액은 통화, 시간 길이는 분 단위로 맞춰 비교한다(half an hour = 30분 = 반 시간 = media hora ≠ 30시간)
    cur = _unit(before, after, lang)
    if cur:
        return Num(v, "n", unit=cur)
    m = DUR[lang].match(after)
    if not m:
        return Num(v, "n")
    if m.group(1):
        return Num(v, "n", unit="min")
    extra = m.group(3) if m.re.groups >= 3 else None  # 시간 반, y media = +30분, y cuarto = +15분
    return Num(v * 60 + (0 if not extra else 15 if "cuarto" in extra.lower() else 30), "n", unit="min")


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


def spanish_times(text: str):
    """검증과 프롬프트 힌트가 같은 시각·원문 범위를 쓴다. 반환: (시작, 끝, 시각, 분)."""
    text = fold(text)
    for pattern in (CLOCK_RE, ES_TO_RE, ES_HOUR_RE):
        spans = []
        for m in pattern.finditer(text):
            if pattern is ES_TO_RE and not (m.group("unit") or ES_TO_BEFORE.search(text[:m.start()])):
                continue  # 검증: necesito dos / somos cuatro para las ocho는 수량 + 시각이지 '몇 분 전'이 아니다
            if pattern is CLOCK_RE:
                h, mins, start, subtract = int(m.group(1)), int(m.group(2)), m.start(), False
            else:
                g, w = m.group("hour"), m.group("minute")
                h = int(g) if g.isdigit() else ES_UNITS[g.lower()]
                after = re.match(r"\s+(horas?|minutos?)\b", text[m.end():], re.I)
                if ES_COUNT_AFTER.match(text, m.end()) or (not w and after and h <= 12):
                    continue
                mins = (None if not w else 30 if w.lower() == "media" else 15 if w.lower() == "cuarto"
                        else int(w) if w.isdigit() else _parse_es_words(w))
                start = m.start() if pattern is ES_TO_RE else m.start("hour")
                subtract = pattern is ES_TO_RE or m.group("op") == "menos"
            t = ES_TIME_TAIL.match(text, m.end())
            anchor = _time(h, _es_pm(t, h))
            if subtract:
                val = (anchor.value - 1) % 24 if anchor.exact else float((h - 1) or 12)
                anchor, mins = Num(val, "time", exact=anchor.exact), 60 - mins
            end = t.end() if t else m.end()
            yield start, end, anchor, mins
            spans.append((start, end))
        text = _mask(text, spans)
    for m in re.finditer(r"(?<![\d.,])(\d{1,2})\b", text):
        t = ES_TIME_TAIL.match(text, m.end())
        if t:
            h = int(m.group(1))
            yield m.start(), t.end(), _time(h, _es_pm(t, h)), None


# 검증: 30 thousand won, 30 mil wones처럼 숫자 뒤 단위 말은 숫자에 곱하고, 같은 말을 다시 세지 않게 위치를 돌려준다
SCALES = {"en": {"k": 1e3, "thousand": 1e3, "million": 1e6, "mil": 1e6}, "es": {"mil": 1e3, "millon": 1e6, "millones": 1e6}}


def _digit_nums(text: str, lang: str, out: list) -> list[tuple[int, int]]:
    spans = []
    for m in DIGIT_RE.finditer(text):
        v = float(m.group(1).replace(",", "") + ("." + m.group(2) if m.group(2) else ""))
        end = m.end()
        sm = re.match(r"\s?([A-Za-z]+)\b", text[end:end + 12])
        if sm and sm.group(1).lower() in SCALES[lang]:
            v *= SCALES[lang][sm.group(1).lower()]
            end += sm.end()
        out.append(_num(v, text[:m.start()], text[m.end():], lang))
        spans.append((m.start(), end))
    return spans


def extract_numbers(text: str, locale: str) -> list[Num]:
    lang = LANG_KEY[locale]
    if lang == "es":
        text = _es_prep(fold(text))
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
    for m in CLOCK_RE.finditer(text) if lang != "es" else ():
        h = int(m.group(1))
        end, pm = m.end(), _ko_pm(text[:m.start()], h) if lang == "ko" else None
        if lang != "ko":
            tail_re, pm_of = TIME_TAIL[lang]
            t = tail_re.match(text, end)
            if pm_of(t, h) is not None:
                end, pm = t.end(), pm_of(t, h)
        out.append(_time(int(m.group(1)), pm))
        if int(m.group(2)):
            out.append(Num(float(m.group(2)), "n", unit="min"))
        spans.append((m.start(), end))
    text = _mask(text, spans)

    if lang == "en":
        # 검증: 말로 하는 시각(quarter past 4 p.m.)은 숫자 시각보다 먼저 읽어 두 번 세지 않는다
        for m in EN_PAST_RE.finditer(text):
            h = int(m.group(3)) if m.group(3).isdigit() else EN_UNITS[m.group(3).lower()]
            mins = 15 if m.group(1).lower() == "quarter" else 30
            t = EN_TIME_TAIL.match(text, m.end())
            anchor = _time(h, _en_pm(t, h))  # 오전·오후를 먼저 적용한 뒤 "to"를 뺀다(quarter to 12 p.m. = 11:45)
            if m.group(2).lower() == "to":
                val = (anchor.value - 1) % 24 if anchor.exact else float((h - 1) or 12)
                anchor, mins = Num(val, "time", exact=anchor.exact), 60 - mins
            out += [anchor, Num(float(mins), "n")]
            spans.append((m.start(), t.end() if t else m.end()))
        text = _mask(text, spans)
        for m in re.finditer(r"(?<![\d.,])(\d{1,2})\b", text):
            t = EN_TIME_TAIL.match(text, m.end())
            if t:
                out.append(_time(int(m.group(1)), _en_pm(t, int(m.group(1)))))
                spans.append((m.start(), t.end()))
        # 검증: 정오·자정, 말로 하는 시간 길이(half an hour, an hour and a half)
        for m in re.finditer(r"\b(noon|midday|midnight)\b", text, re.IGNORECASE):
            out.append(Num(0.0 if m.group(1).lower() == "midnight" else 12.0, "time", exact=True))
            spans.append(m.span())
        for m in EN_SPAN_RE.finditer(text):
            v = 30.0 if m.group(1) else 15.0 if m.group(2) else 90.0 if m.group(3) else 1.0 if m.group(4) else 60.0
            out.append(Num(v, "n", unit="min"))
            spans.append(m.span())
        text = _mask(text, spans)
        dspans = _digit_nums(text, lang, out)
        ords = list(EN_ORD_RE.finditer(text))
        for m in ords:
            out.append(Num(float(EN_ORD[m.group(1).lower()]), "n"))
        for m in EN_ORD_ANY.finditer(_mask(text, [m.span() for m in ords])):
            out.append(Num(float(EN_ORD[m.group(1).lower()]), "ord"))
        for m in EN_WORD_RE.finditer(_mask(text, dspans)):
            words = m.group().strip(" -")
            t = EN_TIME_TAIL.match(text, m.end())
            if not t and re.fullmatch(r"(one|a|and)", words, re.IGNORECASE):
                continue
            v = _parse_en_words(words)
            if v is None:
                continue
            if t and v <= 12:
                out.append(_time(v, _en_pm(t, v)))
            else:
                out.append(_num(float(v), text[:m.start()], text[m.end():], lang))
    elif lang == "es":
        for start, end, anchor, mins in spanish_times(text):
            out.append(anchor)
            if mins:
                out.append(Num(float(mins), "n", unit="min"))
            spans.append((start, end))
        for m in re.finditer(r"\b(mediodia|medianoche)\b", text, re.IGNORECASE):
            out.append(Num(0.0 if m.group(1).lower() == "medianoche" else 12.0, "time", exact=True))
            spans.append(m.span())
        for m in ES_SPAN_RE.finditer(text):
            v = 30.0 if m.group(1) else 15.0 if m.group(2) else 60.0
            if m.group(3):
                v = 90.0 if m.group(3).lower() == "media" else 75.0
            out.append(Num(v, "n", unit="min"))
            spans.append(m.span())
        text = _mask(text, spans)
        dspans = _digit_nums(text, lang, out)
        ords = list(ES_ORD_RE.finditer(text))
        for m in ords:
            out.append(Num(float(ES_ORD[m.group(1).lower()]), "n"))
        for m in ES_ORD_ANY.finditer(_mask(text, [m.span() for m in ords])):
            out.append(Num(float(ES_ORD[m.group(1).lower()]), "ord"))
        for m in ES_WORD_RE.finditer(_mask(text, dspans)):
            words = m.group()
            t = ES_TIME_TAIL.match(text, m.end())
            if not t and re.fullmatch(r"(un|uno|una)", words, re.IGNORECASE) and not DUR_ES.match(text, m.end()):
                continue  # 관사 un/una는 숫자로 세지 않는다
            v = _parse_es_words(words)
            if v is None:
                continue
            if t and _es_pm(t, v) is not None and v <= 12:
                out.append(_time(v, _es_pm(t, v)))
            else:
                out.append(_num(float(v), text[:m.start()], text[m.end():], lang))
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
            out.append(_time(int(m.group(1)), _ko_pm(text[:m.start()], int(m.group(1)))))
            if m.group(2):
                out.append(Num(30.0, "n"))
            spans.append(m.span())
        for m in NATIVE_RE.finditer(text):
            if m.group(1) == "두" and m.group(2) == "시" and re.search(r"[어아여해놔]\s?$", text[:m.start()]):
                continue  # 검증: "찍어 두시는·놔두시면"은 보조동사 '두다'라 시각이 아니다(지금 두시입니다는 시각)
            if m.group(2) == "시":
                out.append(_time(NATIVE[m.group(1)], _ko_pm(text[:m.start()], NATIVE[m.group(1)])))
            elif m.group(2) == "시간":
                out.append(_num(float(NATIVE[m.group(1)]), "", text[m.start(2):], lang))
            else:
                out.append(Num(float(NATIVE[m.group(1)]), "n"))
            if m.group(2) == "시" and re.match(r"\s?반", text[m.end():]):
                out.append(Num(30.0, "n"))
            spans.append(m.span())
        for m in NATIVE_ALONE_RE.finditer(_mask(text, spans)):
            out.append(Num(float(NATIVE_ALONE[m.group(1)]), "n"))
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
                    out.append(_num(pending, text[:start], text[last_end:], lang))
                pending, start = v, m.start()
            last_end, last_rank = m.end(), r
        if pending is not None:
            out.append(_num(pending, text[:start], text[last_end:], lang))
        text = _mask(text, [m.span() for m in tok.finditer(text)])
        sino = r"[일이삼사오육칠팔구십백천만억]{1,8}"
        for m in re.finditer(rf"(?<![가-힣])({sino}(?:\s{sino})*)(?=\s?" + KO_COUNTER + ")", text):
            s = m.group(1).replace(" ", "")
            if len(s) < 2 and s not in SINO_SMALL and s not in SINO_BIG:
                continue
            v = _parse_sino(s)
            if v:
                out.append(_num(float(v), text[:m.start()], text[m.end():], lang))
    return [n for n in out if not (n.kind == "n" and n.value == 1 and n.unit is None)]


def _num_match(a: Num, b: Num) -> bool:
    if a.kind == "phone" or b.kind == "phone":
        return a.kind == b.kind and a.value == b.value
    if (a.kind == "time" and b.unit) or (b.kind == "time" and a.unit):
        return False  # 검증: 같은 숫자여도 시각과 분·금액은 다르다(10분 ≠ 오전 10시)
    if a.unit and b.unit and a.unit != b.unit:
        return False
    if abs(a.value - b.value) < 1e-6:
        return True
    if "time" in (a.kind, b.kind) and a.value <= 24 and b.value <= 24:
        if a.kind != "time" or b.kind != "time":
            return False  # 검증: 시각끼리만 12시간 차이를 허용한다(10분 ≠ 오후 10시, 14명 ≠ 2시)
        if a.exact and b.exact:
            return a.value % 24 == b.value % 24
        if a.exact:
            return False  # 원문의 오전·오후(또는 24시간제)가 번역에서 빠짐
        return a.value % 12 == b.value % 12
    return False


def compare_numbers(src: list[Num], tgt: list[Num]) -> list[str]:
    # 검증: 서수(두 번째)는 양쪽에 다 있을 때만 비교한다("a second driver" = "운전자 한 명 더")
    both_ord = any(n.kind == "ord" for n in src) and any(n.kind == "ord" for n in tgt)
    missing, pool = [], list(tgt)
    for s in src:
        hit = next((t for t in pool if _num_match(s, t)), None)
        if hit is not None:
            pool.remove(hit)
        elif (s.kind != "ord" or both_ord) and not _is_zero(s):
            missing.append(s)
    _pair_durations(missing, pool)
    _pair_durations(pool, missing)
    return ([f"number_missing:{s.value:g}" for s in missing]
            + [f"number_added:{t.value:g}" for t in pool if (t.kind != "ord" or both_ord) and not _is_zero(t)])


def _pair_durations(whole: list[Num], parts: list[Num]) -> None:
    # 검증: 한쪽의 "1시간 + 30분" 두 값이 다른 쪽의 한 값(an hour and a half = 90분)과 같으면 짝으로 본다
    for w in [n for n in whole if n.unit == "min"]:
        hours = [n for n in parts if n.unit == "min" and n.value and n.value % 60 == 0]
        mins = [n for n in parts if n.unit == "min" and 0 < n.value < 60]
        pair = next(((h, m) for h in hours for m in mins if h.value + m.value == w.value), None)
        if pair:
            whole.remove(w)
            parts.remove(pair[0])
            parts.remove(pair[1])


def _is_zero(n: Num) -> bool:
    # 검증: 0은 "없다"와 같은 뜻으로 옮겨질 수 있어 짝이 없어도 넘어간다(면책금이 없어요 = deductible is zero)
    return n.kind == "n" and n.value == 0


def add_meridiem(tt: str, source: str, src_locale: str, tgt_locale: str) -> str:
    # 검증: 원문에 오전·오후가 있는데 한국어 번역이 "6시"처럼 뺐으면 원문 기준으로 "오후 6시"를 채운다(같은 시각일 때만)
    if src_locale == "ko-KR" or tgt_locale != "ko-KR":
        return tt
    exact = [n.value for n in extract_numbers(source, src_locale) if n.kind == "time" and n.exact]
    for m in reversed(list(re.finditer(r"(?<!\d)(\d{1,2})\s?시(?!간)", tt))):
        h = int(m.group(1))
        hit = next((v for v in exact if 1 <= h <= 12 and h % 12 == v % 12), None)
        if hit is not None and not KO_MERIDIEM.search(tt[:m.start()]):
            tt = tt[:m.start()] + ("오후 " if hit >= 12 else "오전 ") + tt[m.start():]
    return tt


# ---------------------------------------------------------------- negation
# 검증: (확실한 부정, 부정에 가까운 말). 한쪽만 확실한 부정이고 다른 쪽에 둘 다 없으면 실패. 스페인어는 강세를 뺀 글자로 본다
NEG = {
    "en": (
        re.compile(r"\b(not|no|never|none|nobody|nothing|nowhere|neither|nor|cannot|without|no one|unhurt|uninjured|unharmed"
                   r"|prohibit\w*|forbidden|banned)\b|n't\b", re.I),
        re.compile(r"\b(instead|rather than|other than|except|excluding|apart from|zero|only|stop|refuse|fine|okay|ok|all right|free"
                   r"|sick|unwell|bad)\b", re.I),
    ),
    "es": (
        # 검증: 되묻는 "¿no?", "de nada"(천만에요), "sin plomo"(무연)는 부정이 아니다
        re.compile(r"\b(no(?!\s*\?)|nunca|jamas|nadie|ningun|ninguno|ninguna|ningunos|ningunas|tampoco|ni|sin(?!\s+plomo)"
                   r"|prohib\w*)\b"
                   r"|(?<!de )\bnada\b", re.I),
        re.compile(r"\b(en lugar de|en vez de|excepto|salvo|menos|solo|solamente|unicamente|gratis|gratuit[oa]s?|cero|bien"
                   r"|mal|ilesos?|ilesas?|basta|alto|dej[ea] de|todavia|aun|ok|okey|de acuerdo|rechaz\w*|sino|desconozco)\b", re.I),
    ),
    "ko": (
        # 검증: "A, 아니면 B"(또는)는 부정이 아니지만 "회원이 아니면"(조건부 부정)은 부정이다
        re.compile(r"않|(?<!잘)못|없|말고|마세요|마십시오|말아|지 ?마|지\s?말(?:라[고는]|랬)|아니(?!면)|(?<=[이가게] )아니면|(?<=[이가게])아니면"
                   r"|아닌|아닙|아냐|아녜|금지|불가"
                   r"|(?<![가-힣])안\s?(?!녕|내|전|에|쪽|경|심|과|개|건|부|마|방|색|약|면|정|팎|으로|까)[가-힣]"),
        re.compile(r"무사|괜찮|모르|몰라|싫|아직|대신|그만|무료|제외|빼고"),
    ),
}


def neg_level(text: str, locale: str) -> int:
    hard, soft = NEG[LANG_KEY[locale]]
    text = fold(text)
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
# 검증: 스페인어 derecho는 문맥에 따라 직진·권리·오른쪽을 구분한다
_ES_STRAIGHT_BEFORE = re.compile(r"(?:^|[,.;:!?]|\b(?:sig\w*|segu\w*|todo|vay\w*|ve|vete|continu\w*|manej\w*"
                                 r"|condu\w*|camin\w*|ir|va|voy|vamos|van|avanc\w*|avanz\w*|siempre|teng\w*|tien\w*"
                                 r"|ten\w*|tuv\w*|dele|dale|tom[ae]\w*|agarr\w*|es|esta))\s*$", re.I)
_ES_RIGHTS_BEFORE = re.compile(r"\b(?:el|su|mi|tu|nuestro|vuestro|este|ese|un)\s*$", re.I)
_ES_RIGHTS_AFTER = re.compile(r"\s+(?:a|al|de|del)\b", re.I)


def _es_right(text: str) -> bool:
    # 검증: 직진·권리만 제외한다. "retrovisor derecho del carro"의 del은 오른쪽 의미를 지우지 않는다
    if re.search(r"\bderechas?\b", text, re.I):
        return True
    return any(not _ES_STRAIGHT_BEFORE.search(text[:m.start()])
               and not (_ES_RIGHTS_BEFORE.search(text[:m.start()]) and _ES_RIGHTS_AFTER.match(text, m.end()))
               for m in re.finditer(r"\bderecho\b", text, re.I))


DIR_ES_LEFT = re.compile(r"\bizquierd[ao]s?\b", re.I).search
# (번역에 꼭 있어야 하는 표현, 있으면 되는 표현). 스페인어는 양쪽 모두 직진·권리와 오른쪽을 구분한다
DIR_FOREIGN = {
    "en": ({d: r.search for d, r in DIR_EN_PHRASE.items()}, {d: r.search for d, r in DIR_EN_WORD.items()}),
    "es": ({"left": DIR_ES_LEFT, "right": _es_right},
           {"left": DIR_ES_LEFT, "right": _es_right}),
}


def check_direction(fx: str, ko: str, lang: str = "en") -> list[str]:
    issues = []
    phrase, word = DIR_FOREIGN[lang]
    fx = fold(fx)
    for d in ("left", "right"):
        if phrase[d](fx) and not DIR_KO[d].search(ko):
            issues.append(f"direction_missing:{d}")
        if DIR_KO[d].search(ko) and not word[d](fx):
            issues.append(f"direction_mismatch:{d}")
    return issues


# 검증: 뒤바뀌기 쉬운 반대말(안/밖, 전/후). 원문에 한쪽만 있는데 번역에 반대쪽만 있으면 실패
OPPOSITES = [
    ("inside", {"en": r"\binside\b", "es": r"\b(?:dentro|adentro)\b"}, r"안에|안으로|안쪽|내부",
     "outside", {"en": r"\boutside\b", "es": r"\b(?:fuera|afuera)\b"}, r"밖|바깥|외부"),
    ("before", {"en": r"\bbefore\b", "es": r"\bantes\b"}, r"전에|전까지|이전",
     "after", {"en": r"\bafter\b", "es": r"\b(?:despues|luego)\b"}, r"후에|다음에|이후"),
]


def check_opposites(fx: str, ko: str, lang: str = "en") -> list[str]:
    issues = []
    fx = fold(fx)
    for na, ea, ka, nb, eb, kb in OPPOSITES:
        for name, e1, k1, e2, k2 in ((na, ea[lang], ka, eb[lang], kb), (nb, eb[lang], kb, ea[lang], ka)):
            fx_only = re.search(e1, fx, re.I) and not re.search(e2, fx, re.I)
            ko_flip = re.search(k2, ko) and not re.search(k1, ko)
            if fx_only and ko_flip:
                issues.append(f"opposite_mismatch:{name}")
    return issues


# ---------------------------------------------------------------- critical terms
def check_critical_terms(src: str, src_locale: str, tgt: str, tgt_locale: str) -> list[str]:
    a = set(match_concepts(src, src_locale, {"fuel"}))
    b = set(match_concepts(tgt, tgt_locale, {"fuel"}))
    return [f"term_missing:{c}" for c in sorted(a - b)] + [f"term_added:{c}" for c in sorted(b - a)]


_EN_FUEL_VERB = r"(?:put(?:\s+in)?|use|want|need|take|pump|add|give\s+me|fill\s+(?:it\s+)?(?:up\s+)?with)"
_ES_FUEL_VERB = r"pon\w*|ech\w*|carg\w*|us[aeo]\w*|llen\w*|met\w*|surt\w*|llev\w*|acept\w*|admit\w*|sirv\w*"
# 검증: 보조동사는 연결어까지 읽어 부정의 범위를 유지한다(volver a, ir a, tener que + 주유 동사).
_ES_FUEL_AUX = (r"(?:vuelv\w*|volv\w*)\s+a|(?:ir|voy|vas?|vamos|van|vay\w*|ib[ao]\w*)\s+a"
                r"|(?:teng\w*|tien\w*|ten\w*|tuv\w*)\s+que|pued\w*|pod\w*|deb\w*")
_ES_FUEL_FILL = (rf"de|del|el|la|los|las|un|una|con|le|les|me|lo|nos|te|se|es|sea|mas|quier\w*|quer\w*|necesit\w*"
                 rf"|que|a|{_ES_FUEL_AUX}|{_ES_FUEL_VERB}")
FUEL_NEG_BEFORE = {
    "en": re.compile(
        r"(?:\b(?:not|no|never|instead\s+of|rather\s+than|other\s+than|except|excluding|apart\s+from|without)|n't"
        rf"|\b(?:not|cannot)\s+{_EN_FUEL_VERB}|n't\s+{_EN_FUEL_VERB})\s+(?:(?:any|the|a|an|in|it|some)\s+)*$", re.I),
    # "no le ponga diésel", "gasolina, no de diésel", "en lugar de diésel"
    "es": re.compile(rf"\b(?:no|nunca|sin|ni|nada|en\s+lugar|en\s+vez|excepto|salvo|menos)\s+(?:(?:{_ES_FUEL_FILL})\s+)*$", re.I),
    "ko": None,
}
FUEL_NEG_AFTER = {
    "en": re.compile(r"^\s+(?:(?:is|was)\s+not|isn't|wasn't|is\s+(?:wrong|prohibited|forbidden)|not\s+allowed)\b", re.I),
    "es": re.compile(rf"^\s+(?:no\s+(?:(?:se|le|les|me|lo|la|nos|te)\s+)*(?:es|esta|va|puede|permite|debe|{_ES_FUEL_VERB})\b"
                     r"|(?:esta|es|queda)\s+(?:prohibid[oa]|mal|incorrect[oa])\b)", re.I),
    "ko": re.compile(r"^(?:차)?(?:은|는|이|가|을|를|으로|로|도|만)?\s*(?:다시\s*)?(?:말고|아니(?!면)|아닙|아냐|아녜|대신|빼|제외|금지|절대|안\s?(?:돼|되|됩|넣|쓰)"
                     r"|(?:넣을|쓸|주유할|사용할|채울)\s*필요(?:가)?\s*없"
                     r"|(?:넣|주유하|주유|쓰|사용하|채우)(?:지\s?(?:마|말|않)|으?시?면\s?안))"),
}
# 검증: "diésel no, gasolina"처럼 끊어 말한 no만 앞 연료의 부정으로 본다("diésel no gasolina"의 no는 뒤 연료 몫)
ES_NO_END = re.compile(r"^\s+no\s*$", re.I)
CLAUSE = re.compile(r"[,.;!?]")


def fuel_polarity(text: str, locale: str) -> dict[str, set[bool]]:
    """연료 개념 id → {True(넣을 연료) / False(부정된 연료)}. "not diesel", "경유 말고"처럼 바로 붙은 부정만 본다."""
    lang = LANG_KEY[locale]
    spans = [(s, e, cid) for s, e, cid, cat in concept_spans(text, locale) if cat == "fuel"]
    text = fold(text)
    out: dict[str, set[bool]] = {}
    for i, (s, e, cid) in enumerate(spans):
        last = i + 1 == len(spans)
        between = text[e:len(text) if last else spans[i + 1][0]]
        before = CLAUSE.split(text[spans[i - 1][1] if i else 0:s])[-1]
        after = CLAUSE.split(between)[0]
        pre = FUEL_NEG_BEFORE[lang]
        neg = (pre and pre.search(before)) or FUEL_NEG_AFTER[lang].search(after)
        if lang == "es" and not neg and (last or CLAUSE.search(between)):
            neg = ES_NO_END.search(after)
        out.setdefault(cid, set()).add(not neg)
    return out


def check_fuel_polarity(src: str, src_locale: str, tgt: str, tgt_locale: str) -> list[str]:
    # 검증: "휘발유, 경유 말고"가 "경유, 휘발유 말고"로 뒤집힌 출력 차단(넣을 연료·부정 연료가 함께 나온 문장)
    a, b = fuel_polarity(src, src_locale), fuel_polarity(tgt, tgt_locale)
    if not any(len(set().union(*p.values())) == 2 for p in (a, b) if p):
        return []
    return [f"fuel_polarity_mismatch:{c}" for c in sorted(a.keys() & b.keys()) if a[c] != b[c]]


# 파라미터 튜닝: 번역/원문 글자 수 최소 비율(정답 자료 최솟값 영→한 0.30, 한→영 1.33보다 낮게). 짧은 원문은 검사 안 함
# 스페인어 원문은 영어보다 길어 영→한보다 낮게 둔다(검수 자료가 쌓이면 다시 잡는다)
MIN_LEN_RATIO = {"en": (30, 0.22), "es": (30, 0.18), "ko": (12, 0.8)}
LEAK_RE = re.compile(r"^\s*(translation|translated|here is|note|traducci[oó]n|traducido|nota|번역|통역|참고)\s*[:：]|\n", re.I)


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
    min_src, ratio = MIN_LEN_RATIO[LANG_KEY[src_locale]]
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
    fx, ko = (source, tt) if tgt_locale == "ko-KR" else (tt, source)
    lang = LANG_KEY[src_locale if tgt_locale == "ko-KR" else tgt_locale]
    issues += check_direction(fx, ko, lang)
    issues += check_opposites(fx, ko, lang)
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
