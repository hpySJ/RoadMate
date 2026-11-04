"""QWEN 구현: 검수된 en-US 안내 문구를 로컬 모델로 다른 언어 초안으로 만든다(사람 검수 전 배포 금지).

사용(llama-server 실행 중): .venv\\Scripts\\python.exe tools\\draft_pack.py --locale ja-JP
결과: packs\\drafts\\rules.<locale>.draft.json  (review.status=draft)
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import Settings  # noqa: E402
from inference import LlamaClient, ModelError  # noqa: E402

LANG = {"ja-JP": "Japanese", "zh-TW": "Traditional Chinese (Taiwan)", "zh-CN": "Simplified Chinese",
        "fr-CA": "Canadian French", "es-US": "US Spanish", "es-MX": "Mexican Spanish", "th-TH": "Thai",
        "vi-VN": "Vietnamese", "id-ID": "Indonesian", "ms-MY": "Malay"}
SYSTEM = (
    "You localize short in-car voice prompts for foreign drivers in Korea. Translate the English prompt into {lang}. "
    "Keep every {{placeholder}} exactly as written, keep every number, keep the meaning and the polite instruction tone. "
    "Keep it short and natural for text-to-speech while driving. Do not add advice. Output JSON: {{\"text\": \"...\"}}"
)
SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"], "additionalProperties": False}
# 파라미터 튜닝: 문구 초안은 결정적 생성
SAMPLING = {"temperature": 0.0, "top_p": 1.0, "top_k": 1, "min_p": 0.0, "seed": 7}


def slots(s: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", s))


def check(src: str, out: str) -> list[str]:
    issues = []
    if not out.strip():
        issues.append("empty")
    if slots(src) != slots(out):
        issues.append("slot_mismatch")
    if sorted(re.findall(r"\d+", src)) != sorted(re.findall(r"\d+", out)):
        issues.append("number_mismatch")
    return issues


async def run(locale: str):
    s = Settings.from_env()
    pack = json.loads((ROOT / "packs" / "rules.ko-en.json").read_text(encoding="utf-8"))
    client = LlamaClient(s.model_base_url, s.model_id)
    system = SYSTEM.format(lang=LANG[locale])

    async def tr(text: str) -> dict:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps({"prompt": text})}]
        try:
            out = (await client.chat_json(msgs, SCHEMA, 160, SAMPLING, 30))["text"].strip()
        except ModelError as e:
            return {"text": "", "issues": [e.code]}
        return {"text": out, "issues": check(text, out)}

    draft = {"version": f"{pack['version']}-{locale}-draft", "review": {"status": "draft", "reviewer": None},
             "locale": locale, "source_locale": "en-US", "rules": {}, "briefing": {}}
    for r in pack["rules"]:
        draft["rules"][r["rule_id"]] = {"source": r["templates"]["en-US"], **await tr(r["templates"]["en-US"])}
    for b in pack["briefing"]:
        draft["briefing"][b["id"]] = {"source": b["en-US"], **await tr(b["en-US"])}
    await client.aclose()
    out = ROOT / "packs" / "drafts" / f"rules.{locale}.draft.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    bad = [k for sec in ("rules", "briefing") for k, v in draft[sec].items() if v["issues"]]
    print(f"saved {out}  issues={bad or 'none'}  (사람 검수 후 rules 팩에 반영)")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--locale", required=True, choices=sorted(LANG))
    asyncio.run(run(ap.parse_args().locale))


if __name__ == "__main__":
    main()
