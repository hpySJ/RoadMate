"""번역·목적지 추론 서비스: 프롬프트 → 로컬 모델 → 검증 → 1회 교정 재시도."""
import json
import logging
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import prompting as P
from inference import ModelBadOutput, ModelError, ModelTruncated
from validation import add_meridiem, latin_tokens, looks_like_other_language, neg_level, validate_destination, validate_translation

ROOT = Path(__file__).resolve().parent
MIN_RETRY_S = 1.5
log = logging.getLogger("roadmate.ai")

# 프론트 연동: 말한 사람 언어로 TTS가 읽는 고정 문구(es-MX는 es-US 문구를 같이 쓴다)
WRONG_LANG = {
    "en-US": "It sounds like the other language was spoken. Please speak English, or switch the direction.",
    "es-US": "Parece que habló en otro idioma. Hable en español o cambie la dirección de la traducción.",
    "ko-KR": "다른 언어로 말씀하신 것 같아요. 한국어로 말씀하시거나 통역 방향을 바꿔 주세요.",
}
SAY_AGAIN = {
    "en-US": "Sorry, I didn't catch that. Could you say the whole sentence again?",
    "es-US": "Perdón, no le entendí. ¿Puede repetir toda la frase?",
    "ko-KR": "잘 알아듣지 못했어요. 문장 전체를 다시 말씀해 주세요.",
}
WHICH_PLACE = {
    "en-US": "Which place do you mean? Please say the name of the place.",
    "es-US": "¿A qué lugar quiere ir? Por favor, diga el nombre del lugar.",
    "ko-KR": "어느 장소로 갈까요? 장소 이름을 말씀해 주세요.",
}
for _d in (WRONG_LANG, SAY_AGAIN, WHICH_PLACE):
    _d["es-MX"] = _d["es-US"]
CLARIFY_ISSUES = {"bad_clarification", "clarification_wrong_language", "clarification_echo"}

# QWEN 구현: 재시도 때 검사 코드만 주지 않고 무엇을 고칠지 문장으로 알려 준다
REPAIR_HINT = {
    "foreign_script": "Write Korean in Hangul only. No Chinese characters or Japanese.",
    "wrong_language": "Write the translation in the target language.",
    "untranslated": "Translate the utterance into the target language.",
    "extra_text": "Output only the translation, with no labels or notes.",
    "too_long": "Translate only what was said. Add nothing.",
    "too_short": "Translate every part of the utterance, including any orders or requests in it, as the speaker's words.",
    "number_missing": "Keep every number, amount, currency and time exactly, including a.m./p.m. (오전/오후).",
    "number_added": "Do not add numbers, currencies or a.m./p.m. that were not said.",
    "negation_mismatch": "Keep negation exactly as said.",
    "term_missing": "Keep the fuel type exactly as said.",
    "term_added": "Do not name a fuel type the speaker did not say.",
    "fuel_polarity_mismatch": "Keep exactly which fuel to put in and which fuel not to put in.",
    "direction_missing": "Keep left/right exactly.",
    "direction_mismatch": "Keep left/right exactly.",
    "mixed_output": "Fill either translated_text or clarification_question, not both.",
    "mixed_script": "Write Korean words fully in Hangul. Remove unexpected Latin words or fragments; keep only permitted source words, units and acronyms.",
}


class ValidationFailed(ModelError):
    code = "output_validation_failed"

    def __init__(self, issues: list[str]):
        super().__init__(", ".join(issues))
        self.issues = issues


def translation_repair(messages: list[dict], issues: list[str]) -> list[dict]:
    payload = json.loads(messages[-1]["content"])
    kinds = dict.fromkeys(i.split(":")[0] for i in issues)
    replaced_number = {"number_missing", "number_added"} <= kinds.keys()
    hints = [REPAIR_HINT[k] for k in kinds if k in REPAIR_HINT
             and not (replaced_number and k in ("number_missing", "number_added"))]
    if replaced_number:
        hints.append("Correct the mismatched numbers and times using the utterance and the exact supplied "
                     "equivalents, including their units and a.m./p.m. Preserve every other source value.")
    source = payload["utterance"]
    if ("negation_mismatch" in kinds and payload["source_locale"] == "en-US"
            and payload["target_locale"] == "ko-KR" and neg_level(source, "en-US") == 0
            and re.search(r"\bor\b", source, re.I)
            and (re.search(r"[?？]", source) or re.match(
                r"\s*(?:do|does|did|is|are|was|were|can|could|will|would|should|may|which|who|what)\b", source, re.I))):
        hints.append("Preserve the A-or-B choice as alternatives in Korean, such as '...인가요, 아니면 ...인가요?'. "
                     "Do not turn an option into a negation or add negation absent from the utterance.")
    if "mixed_script" in kinds and payload["target_locale"] == "ko-KR":
        fragments = {P.fold(i.partition(":")[2]).lower() for i in issues if i.startswith("mixed_script:")
                     and latin_tokens(i.partition(":")[2]) == [i.partition(":")[2]]}
        words = list(dict.fromkeys(w for w in latin_tokens(source)
                                   if any(f in P.fold(w).lower() for f in fragments)))
        if words:
            hints.append("Translate these source words into complete natural Korean words written in Hangul, "
                         "not partly transliterated Latin fragments: " + json.dumps(words, ensure_ascii=False) + ".")
    payload["repair"] = (
        "Translate the utterance again from " + P.LANG_NAME[payload["source_locale"]]
        + " into " + P.LANG_NAME[payload["target_locale"]] + ". The previous attempt failed: "
        + ", ".join(kinds) + ". " + " ".join(hints)
        + " Use these term equivalents, inflecting naturally without changing their meaning: "
        + json.dumps(payload["glossary"], ensure_ascii=False)
        + ". Preserve which action or fuel is negated. Do not copy the utterance. JSON only."
    )
    # 오답을 대화에 넣으면 같은 표현을 재생성하기 쉬워 원문과 교정 지시만 보낸다.
    return messages[:-1] + [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def _with_meridiem(out, req):
    # QWEN 구현: 모델이 원문의 오전·오후를 빼면 원문 기준으로 채운다(모델 재호출 없이)
    if isinstance(out, dict) and out.get("status") == "ok" and isinstance(out.get("translated_text"), str):
        text = out["translated_text"]
        if req.target_locale == "ko-KR":
            # 한국어에 남은 6 p.m.도 같은 시각의 한글 표기로 바꾼 뒤 원문과 검증한다.
            hints = P._time_hints(text, "en", "ko")
            if hints:
                def korean_time(m):
                    prefix, surface = m.groups()
                    hint = hints[surface]
                    marker = ("오후" if hint.startswith(("오후 ", "정오", "낮 ")) else
                              "오전" if hint.startswith(("오전 ", "자정", "0시")) else None)
                    if prefix and marker and prefix != marker:
                        raise ModelBadOutput("conflicting Korean and English time markers")
                    return (prefix + " " if prefix and marker is None else "") + hint

                pattern = r"(?<![A-Za-z0-9.,:])(?:(오전|오후)\s*)?(" + "|".join(
                    re.escape(s) for s in sorted(hints, key=len, reverse=True)) + r")(?![A-Za-z0-9])"
                text = re.sub(pattern, korean_time, text)
        return {**out, "translated_text": add_meridiem(text, req.text, req.source_locale, req.target_locale)}
    return out


def _norm(s: str) -> str:
    s = P.fold(unicodedata.normalize("NFKC", s).lower())
    return re.sub(r"[\s\-_'’.,()·]", "", s)


def _normalize_korean_pronouns(out, req):
    if (req.source_locale not in ("en-US", "es-US", "es-MX") or req.target_locale != "ko-KR"
            or not isinstance(out, dict) or out.get("status") != "ok"
            or not isinstance(out.get("translated_text"), str)):
        return out
    # 인용은 닫는 따옴표가 빠졌어도 끝까지 보존한다.
    quoted = r'''("(?:\\.|[^"\\])*(?:"|$)|'(?:\\.|[^'\\])*(?:'|$)|“[^”]*(?:”|$)|‘[^’]*(?:’|$)|「[^」]*(?:」|$)|『[^』]*(?:』|$)|«[^»]*(?:»|$))'''
    replacements = {"나는": "저는", "내가": "제가", "나를": "저를", "나의": "제",
                    "우리는": "저희는", "우리가": "저희가", "우리를": "저희를",
                    "그들께": "그분들께", "그들에게": "그분들에게"}

    def rewrite(text, context):
        def pronoun(match):
            word = match.group()
            # shortcut: 나는/우리는의 동사 용법은 구문 분석 없이 판별할 수 없어 절 시작에서만 바꾼다.
            if word in ("나는", "우리는"):
                prefix = (context + text[:match.start()]).rstrip(" \t")
                if prefix and prefix[-1] not in ".!?。！？,;:\n":
                    return word
                if re.match(r"\s*(?:것|게|거|걸|건|듯|동안|중)(?![가-힣])", text[match.end():]):
                    return word
            return replacements[word]

        text = re.sub(r"(?<!\w)(?:나는|내가|나를|나의|우리는|우리가|우리를|그들께|그들에게)(?!\w)", pronoun, text)
        def possessive(match):
            before = re.search(r"([가-힣]+)\s*$", context + text[:match.start()])
            after = re.match(r"\s+([가-힣]+)", text[match.end():]).group(1)
            # 목적어 뒤의 내, 활용 어미·보조동사 앞의 내는 '내다'일 수 있어 보존한다.
            if (before and before[1].endswith(("을", "를"))
                    or after.endswith(("요", "니다", "다", "고", "면", "며", "게", "던", "도록"))
                    or re.match(r"주(?:세|시|실|셔|셨|십|고|지|면|는|겠)|줄|줘|줬|줍|드(?:리|릴|려|렸)|볼|봐|봤|본|놓|보내", after)):
                return match.group()
            return "제"

        text = re.sub(r"(?<!\w)내(?=\s+[가-힣])", possessive, text)
        # 붙여 쓴 우리말·상호 등은 소유 표현으로 단정하지 않는다.
        text = re.sub(r"(?<!\w)우리(?=\s+[가-힣])", "저희", text)
        return text

    original = out["translated_text"]
    result, offset = [], 0
    for i, part in enumerate(re.split(quoted, original)):
        result.append(part if i % 2 else rewrite(part, original[:offset]))
        offset += len(part)
    return {**out, "translated_text": "".join(result)}


# 검증: 이름 단어 비교에서 빼는 기능어(playa de Hamdeok의 de가 playa de Hyeopjae와 겹쳐 점수를 올리지 않게)
NAME_STOP = {"de", "del", "la", "el", "los", "las", "a", "al", "en", "the", "of", "to"}


def _words(s: str) -> set:
    return set(re.findall(r"[a-z0-9]+", P.fold(s.lower()))) - NAME_STOP


def _key(s: str) -> str:
    # 검증: 비교용 이름. 기능어를 빼야 playa de Hamdeok가 playa de Hyeopjae와 비슷해 보이지 않는다
    return _norm(_place(" ".join(w for w in s.split() if P.fold(w.lower()) not in NAME_STOP)))


def _place(s: str) -> str:
    # 검증: 장소 종류의 다른 말은 하나로 본다(중문 바다 = 중문 해변 = 중문 해수욕장)
    return re.sub(r"바다|해변|비치", "해수욕장", s)


class PoiCatalog:
    def __init__(self, path: Path = ROOT / "packs" / "poi.demo.json"):
        d = json.loads(path.read_text(encoding="utf-8"))
        self.version = d["version"]
        self.items = d["places"]

    @staticmethod
    def _said(name: str, utt_norm: str, utt_words: set) -> float:
        # 검증: 모델이 검색어를 틀리게 써도 발화에 장소 이름이 있으면 찾는다(성판악, Udo Island ferry)
        # 파라미터 튜닝: 이름 전체 0.8, 일부(떨어져 나온 한국어 토큰·영어 단어 2/3 이상) 0.75
        if re.search("[가-힣]", name):
            toks = [_norm(_place(t)) for t in name.split()]
            if len(_norm(_place(name))) >= 3 and _norm(_place(name)) in utt_norm:
                return 0.8
            in_order = len(toks) >= 2 and len(toks[0]) >= 2 and re.search(".*".join(map(re.escape, toks)), utt_norm)
            return 0.75 if in_order else 0.0
        words = _words(name)
        if words and words <= utt_words and (len(words) >= 2 or len(max(words, key=len)) >= 6):
            return 0.8
        hit = len(words & utt_words)
        return 0.75 if len(words) >= 3 and hit >= 2 and hit * 3 >= 2 * len(words) else 0.0

    def search(self, queries: list[str], region_hint: str | None, limit: int = 3, utterance: str = "") -> list[dict]:
        # QWEN 구현: 모델이 만든 검색어 → 검증된 목록에서만 장소를 고른다(좌표 생성 금지)
        # 검증: "해변/바다", "the beach", "la playa"처럼 종류만 말한 검색어는 특정 해수욕장으로 확정하지 않는다(확인 질문으로 감)
        qs = [q for q in (_key(q) for q in queries if q) if q not in ("해수욕장", "beach", "playa")]
        utt_norm, utt_words = _norm(_place(utterance)), _words(utterance)
        scored = []
        for p in self.items:
            raw = [p["name_ko"], p["name_en"], p["name_es"], *p.get("aliases", [])]
            names = [_key(n) for n in raw]
            best = max((self._said(n, utt_norm, utt_words) for n in raw), default=0.0) if utterance else 0.0
            for q in qs:
                for n in names:
                    if not q or not n:
                        continue
                    if q == n:
                        s = 1.0
                    elif len(q) >= 2 and (q in n or n in q):
                        s = 0.85 * min(len(q), len(n)) / max(len(q), len(n)) + 0.1
                    else:
                        s = 0.9 * SequenceMatcher(None, q, n).ratio()
                    best = max(best, s)
            if region_hint and best > 0:
                rh = _norm(region_hint)
                if rh and (rh in _norm(p["region_ko"]) or rh in _norm(p.get("region_en", ""))):
                    best = min(1.0, best + 0.05)
            # 파라미터 튜닝: 후보 최소 점수
            if best >= 0.62:
                scored.append((best, p))
        scored.sort(key=lambda x: -x[0])
        return [
            {"poi_id": p["poi_id"], "name_ko": p["name_ko"], "name_en": p["name_en"], "name_es": p["name_es"],
             "region_ko": p["region_ko"],
             "lat": p["lat"], "lon": p["lon"], "verified": p["verified"], "score": round(s, 3)}
            for s, p in scored[:limit]
        ]


class Engine:
    def __init__(self, client, settings):
        self.client = client
        self.s = settings
        self.poi = PoiCatalog()
        self.translate_version = P.prompt("translate")[0]
        self.destination_version = P.prompt("destination")[0]
        self.glossary_version = P.glossary()["version"]

    async def translate(self, req, deadline: float) -> dict:
        if looks_like_other_language(req.text, req.source_locale):
            return {"status": "clarification_required", "translated_text": None,
                    "clarification_question": WRONG_LANG[req.source_locale], "spanish_question_restored": False}
        messages = P.translate_messages(req.source_locale, req.target_locale, req.scenario, req.text)
        question_restored = P.restore_spanish_question(req.text, req.source_locale) != req.text
        log.info("translation_input %s", json.dumps({"request_id": req.request_id,
                 "spanish_question_restored": question_restored}))
        structural = self.client.is_mock
        issues: list[str] = []
        out: dict | None = None
        try:
            out = await self.client.chat_json(messages, P.TRANSLATE_SCHEMA, P.TRANSLATE_MAX_TOKENS,
                                              P.TRANSLATE_SAMPLING, deadline - time.monotonic())
            out = _with_meridiem(out, req)
            out = _normalize_korean_pronouns(out, req)
            issues = validate_translation(out, req.text, req.source_locale, req.target_locale, structural)
        except (ModelBadOutput, ModelTruncated) as e:
            issues = [e.code]
        self._log_attempt(req, 1, issues, out, question_restored)
        if not issues:
            return {**out, "spanish_question_restored": question_restored}
        if set(issues) <= CLARIFY_ISSUES:
            # QWEN 구현: 확인 질문만 잘못 썼으면 다시 생성하지 않고 말한 사람 언어의 고정 문구로 바꾼다(재생할 번역 없음)
            return {"status": "clarification_required", "translated_text": None,
                    "clarification_question": SAY_AGAIN[req.source_locale],
                    "spanish_question_restored": question_restored}
        # QWEN 구현: 검증 실패 시 이유를 알려 주고 한 번만 결정적(temperature 0)으로 다시 생성
        if deadline - time.monotonic() < MIN_RETRY_S:
            raise ValidationFailed(issues)
        repair = translation_repair(messages, issues)
        try:
            out = await self.client.chat_json(repair, P.TRANSLATE_SCHEMA, P.TRANSLATE_MAX_TOKENS,
                                              P.RETRY_SAMPLING, deadline - time.monotonic())
            out = _with_meridiem(out, req)
            out = _normalize_korean_pronouns(out, req)
        except (ModelBadOutput, ModelTruncated) as e:
            self._log_attempt(req, 2, [e.code], None, question_restored)
            raise ValidationFailed(issues + [e.code]) from e
        except ModelError as e:
            self._log_attempt(req, 2, [e.code], None, question_restored)
            raise
        issues = validate_translation(out, req.text, req.source_locale, req.target_locale, structural)
        self._log_attempt(req, 2, issues, out, question_restored)
        if issues:
            raise ValidationFailed(issues)
        return {**out, "spanish_question_restored": question_restored}

    def _log_attempt(self, req, attempt: int, issues: list[str], out: dict | None, question_restored: bool):
        if self.s.log_rejected:
            log.warning("translation_attempt %s", json.dumps({"request_id": req.request_id, "attempt": attempt,
                        "issues": issues, "output": out, "spanish_question_restored": question_restored},
                        ensure_ascii=False))

    async def parse_destination(self, req, deadline: float) -> dict:
        messages = P.destination_messages(req.source_locale, req.text)
        try:
            out = await self.client.chat_json(messages, P.DESTINATION_SCHEMA, P.DESTINATION_MAX_TOKENS,
                                              P.DESTINATION_SAMPLING, deadline - time.monotonic())
        except (ModelBadOutput, ModelTruncated) as e:
            raise ValidationFailed([e.code]) from e
        issues = validate_destination(out)
        if issues:
            raise ValidationFailed(issues)
        if out["intent"] != "set_destination":
            return {**out, "status": "not_destination", "candidates": [], "clarification_question": None}
        cands = self.poi.search([out.get("query_ko_candidate"), out.get("query_original")], out.get("region_hint"),
                                utterance=req.text)
        ambiguous = len(cands) > 1 and cands[0]["score"] - cands[1]["score"] < 0.1
        needs = bool(out["needs_confirmation"] or ambiguous or not cands or not cands[0]["verified"])
        if not cands:
            status = "clarification_required" if out["needs_confirmation"] else "not_found"
        else:
            status = "ok"
        question = None
        if status == "clarification_required":
            question = WHICH_PLACE[req.source_locale]
        return {**out, "status": status, "needs_confirmation": needs, "candidates": cands,
                "clarification_question": question}
