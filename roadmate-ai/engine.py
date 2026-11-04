"""번역·목적지 추론 서비스: 프롬프트 → 로컬 모델 → 검증 → 1회 교정 재시도."""
import json
import re
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import prompting as P
from inference import ModelBadOutput, ModelError, ModelTruncated
from validation import looks_like_other_language, validate_destination, validate_translation

ROOT = Path(__file__).resolve().parent
MIN_RETRY_S = 1.5

WRONG_LANG = {
    "en-US": "It sounds like the other language was spoken. Please speak English, or switch the direction.",
    "ko-KR": "다른 언어로 말씀하신 것 같아요. 한국어로 말씀하시거나 통역 방향을 바꿔 주세요.",
}


class ValidationFailed(ModelError):
    code = "output_validation_failed"

    def __init__(self, issues: list[str]):
        super().__init__(", ".join(issues))
        self.issues = issues


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    return re.sub(r"[\s\-_'’.,()·]", "", s)


class PoiCatalog:
    def __init__(self, path: Path = ROOT / "packs" / "poi.demo.json"):
        d = json.loads(path.read_text(encoding="utf-8"))
        self.version = d["version"]
        self.items = d["places"]

    def search(self, queries: list[str], region_hint: str | None, limit: int = 3) -> list[dict]:
        # QWEN 구현: 모델이 만든 검색어 → 검증된 목록에서만 장소를 고른다(좌표 생성 금지)
        qs = [_norm(q) for q in queries if q]
        scored = []
        for p in self.items:
            names = [_norm(n) for n in [p["name_ko"], p["name_en"], *p.get("aliases", [])]]
            best = 0.0
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
            {"poi_id": p["poi_id"], "name_ko": p["name_ko"], "name_en": p["name_en"], "region_ko": p["region_ko"],
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
            out = await self.client.chat_json(messages, P.TRANSLATE_SCHEMA, P.TRANSLATE_MAX_TOKENS,
                                              P.TRANSLATE_SAMPLING, deadline - time.monotonic())
            issues = validate_translation(out, req.text, req.source_locale, req.target_locale, structural)
        except (ModelBadOutput, ModelTruncated) as e:
            issues = [e.code]
        if not issues:
            return out
        # QWEN 구현: 검증 실패 시 이유를 알려 주고 한 번만 결정적(temperature 0)으로 다시 생성
        if deadline - time.monotonic() < MIN_RETRY_S:
            raise ValidationFailed(issues)
        repair = messages + ([{"role": "assistant", "content": P.completion_text(out)}] if out else []) + [{
            "role": "user",
            "content": json.dumps({"repair": "The previous output broke these checks: " + ", ".join(issues)
                                   + ". Translate the same utterance again, following every rule. JSON only."}),
        }]
        try:
            out = await self.client.chat_json(repair, P.TRANSLATE_SCHEMA, P.TRANSLATE_MAX_TOKENS,
                                              P.RETRY_SAMPLING, deadline - time.monotonic())
        except (ModelBadOutput, ModelTruncated) as e:
            raise ValidationFailed(issues + [e.code]) from e
        issues = validate_translation(out, req.text, req.source_locale, req.target_locale, structural)
        if issues:
            raise ValidationFailed(issues)
        return out

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
        cands = self.poi.search([out.get("query_ko_candidate"), out.get("query_original")], out.get("region_hint"))
        ambiguous = len(cands) > 1 and cands[0]["score"] - cands[1]["score"] < 0.1
        needs = bool(out["needs_confirmation"] or ambiguous or not cands or not cands[0]["verified"])
        if not cands:
            status = "clarification_required" if out["needs_confirmation"] else "not_found"
        else:
            status = "ok"
        question = None
        if status == "clarification_required":
            question = ("어느 장소로 갈까요? 장소 이름을 말씀해 주세요." if req.source_locale == "ko-KR"
                        else "Which place do you mean? Please say the name of the place.")
        return {**out, "status": status, "needs_confirmation": needs, "candidates": cands,
                "clarification_question": question}
