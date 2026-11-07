"""번역·목적지 추론 서비스: 프롬프트 → 로컬 모델 → 검증 → 1회 교정 재시도."""
import json
import logging
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import prompting as P
from inference import ModelBadOutput, ModelError, ModelTimeout, ModelTruncated, ModelUnavailable
from validation import add_meridiem, looks_like_other_language, validate_destination, validate_translation

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
LANG_NAME = {"ko-KR": "Korean", "en-US": "English", "es-US": "Spanish"}
for _d in (WRONG_LANG, SAY_AGAIN, WHICH_PLACE, LANG_NAME):
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
    "mixed_script": "Do not mix English letters into Korean words. Write each word fully in Hangul.",
}


class ValidationFailed(ModelError):
    code = "output_validation_failed"

    def __init__(self, issues: list[str]):
        super().__init__(", ".join(issues))
        self.issues = issues


def _with_meridiem(out, req):
    # QWEN 구현: 모델이 원문의 오전·오후를 빼면 원문 기준으로 채운다(모델 재호출 없이)
    if isinstance(out, dict) and out.get("status") == "ok" and isinstance(out.get("translated_text"), str):
        return {**out, "translated_text": add_meridiem(out["translated_text"], req.text, req.source_locale,
                                                       req.target_locale)}
    return out


def _norm(s: str) -> str:
    s = P.fold(unicodedata.normalize("NFKC", s).lower())
    return re.sub(r"[\s\-_'’.,()·]", "", s)


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
                    "clarification_question": WRONG_LANG[req.source_locale]}
        messages = P.translate_messages(req.source_locale, req.target_locale, req.scenario, req.text)
        structural = self.client.is_mock
        issues: list[str] = []
        out: dict | None = None
        try:
            out = _with_meridiem(await self.client.chat_json(messages, P.TRANSLATE_SCHEMA, P.TRANSLATE_MAX_TOKENS,
                                                             P.TRANSLATE_SAMPLING, deadline - time.monotonic()), req)
            issues = validate_translation(out, req.text, req.source_locale, req.target_locale, structural)
        except (ModelBadOutput, ModelTruncated) as e:
            issues = [e.code]
        if not issues:
            return await self._polish(req, out, deadline)
        self._log_rejected(req, issues, out)
        if set(issues) <= CLARIFY_ISSUES:
            # QWEN 구현: 확인 질문만 잘못 썼으면 다시 생성하지 않고 말한 사람 언어의 고정 문구로 바꾼다(재생할 번역 없음)
            return {"status": "clarification_required", "translated_text": None,
                    "clarification_question": SAY_AGAIN[req.source_locale]}
        # QWEN 구현: 검증 실패 시 이유를 알려 주고 한 번만 결정적(temperature 0)으로 다시 생성
        if deadline - time.monotonic() < MIN_RETRY_S:
            raise ValidationFailed(issues)
        hints = dict.fromkeys(REPAIR_HINT[i.split(":")[0]] for i in issues if i.split(":")[0] in REPAIR_HINT)
        repair = messages + ([{"role": "assistant", "content": P.completion_text(out)}] if out else []) + [{
            "role": "user",
            # QWEN 구현: 재시도 때 원문을 그대로 돌려주지 않도록 번역 방향과 원문을 다시 적는다
            "content": json.dumps({"repair": "The previous output broke these checks: " + ", ".join(issues) + ". "
                                   + " ".join(hints) + f" Translate the utterance from {LANG_NAME[req.source_locale]} "
                                   f"into {LANG_NAME[req.target_locale]} again, following every rule. Do not copy the "
                                   "utterance. JSON only.", "utterance": req.text}, ensure_ascii=False),
        }]
        try:
            out = _with_meridiem(await self.client.chat_json(repair, P.TRANSLATE_SCHEMA, P.TRANSLATE_MAX_TOKENS,
                                                             P.RETRY_SAMPLING, deadline - time.monotonic()), req)
        except (ModelBadOutput, ModelTruncated) as e:
            raise ValidationFailed(issues + [e.code]) from e
        issues = validate_translation(out, req.text, req.source_locale, req.target_locale, structural)
        if issues:
            self._log_rejected(req, issues, out)
            raise ValidationFailed(issues)
        return await self._polish(req, out, deadline)

    async def _polish(self, req, out: dict, deadline: float) -> dict:
        # QWEN 구현: 영·스→한 번역을 한 번 더 자연스러운 한국어로 다듬는다. 다듬은 문장도 같은 검사를 통과해야만 바꾼다
        if not (self.s.polish_ko and not self.client.is_mock and req.target_locale == "ko-KR"
                and out.get("status") == "ok" and deadline - time.monotonic() > MIN_RETRY_S):
            return out
        messages = P.polish_messages(req.source_locale, req.text, out["translated_text"],
                                     P.select_glossary(req.text, req.source_locale, req.target_locale))
        try:
            pol = await self.client.chat_json(messages, P.POLISH_SCHEMA, P.POLISH_MAX_TOKENS, P.RETRY_SAMPLING,
                                              deadline - time.monotonic())
        except (ModelBadOutput, ModelTruncated, ModelUnavailable):
            return out  # 다듬기가 실패해도 이미 검사를 통과한 번역은 돌려준다
        except ModelTimeout:
            # QWEN 구현: 모델 계산이 끝난 게 확인되면 초안을 돌려주고, 아니면 timeout으로 올려 슬롯을 계속 잡아 둔다
            if await self.client.wait_idle(self.s.cancel_settle_s):
                return out
            raise
        text = pol.get("text") if isinstance(pol, dict) else None
        if not isinstance(text, str) or not text.strip():
            return out
        cand = _with_meridiem({**out, "translated_text": text.strip()}, req)
        issues = validate_translation(cand, req.text, req.source_locale, req.target_locale)
        # 파라미터 튜닝: 다듬기는 손질이어야 한다. 초안과 너무 다르면(문장 유사도 0.5 미만) 다시 쓴 것으로 보고 버린다
        if SequenceMatcher(None, out["translated_text"], cand["translated_text"]).ratio() < 0.5:
            issues.append("polish_rewrote")
        if issues:
            self._log_rejected(req, ["polish"] + issues, cand)
            return out
        return cand

    def _log_rejected(self, req, issues: list[str], out: dict | None):
        if self.s.log_rejected:
            log.warning("rejected %s %s | %s | %s", req.request_id, ",".join(issues), req.text,
                        P.completion_text(out) if out else None)

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
