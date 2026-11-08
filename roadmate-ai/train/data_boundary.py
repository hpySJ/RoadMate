"""학습 경계: 슬롯 치환 틀 비교와 실제 추론 입력 파일의 식별값."""
import hashlib
import json
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import prompting as P  # noqa: E402
from validation import EN_WORD_RE, ES_WORD_RE, KO_COUNTER, NATIVE_RE, KO_ORD  # noqa: E402


@lru_cache(maxsize=8192)
def fingerprint(text: str, locale: str) -> str:
    # shortcut: 명시된 슬롯과 군더더기만 지운다. 의역·관용구의 의미 중복은 사람이 검수해야 한다.
    lang = P.LANG_KEY[locale]
    text = P.fold(text).lower()
    spans = [(s, e, "[fuel]") for s, e, _, cat in P.concept_spans(text, locale) if cat == "fuel"]
    for place in P.places():
        names = [place[f"name_{lang}"]]
        names += place.get("aliases", [])
        if lang != "ko":
            names += [place["name_en"], place["name_es"]]
        spans += [(s, e, "[place]") for s, e, _ in P._find_terms(text, names, lang)]
    taken = []
    for s, e, slot in sorted(spans, key=lambda x: x[0] - x[1]):
        if not any(s < te and ts < e for ts, te, _ in taken):
            taken.append((s, e, slot))
    for s, e, slot in sorted(taken, reverse=True):
        text = text[:s] + slot + text[e:]
    if lang == "ko":
        text = re.sub(rf"(?<![가-힣])([\d,.일이삼사오육칠팔구십백천만억]+(?:\s+[일이삼사오육칠팔구십백천만억]+)*)\s*(?={KO_COUNTER}|시)",
                      "[number]", text)
        text = NATIVE_RE.sub(lambda m: "[number]" + m.group(2), text)
        text = KO_ORD.sub("[number]번째", text)
        text = re.sub(r"기름|연료", "[fuel]", text)
    else:
        text = (EN_WORD_RE if lang == "en" else ES_WORD_RE).sub("[number] ", text)
        text = re.sub(r"(?<!\[)\b(?:gas|fuel|combustible)\b", "[fuel]", text)
        text = re.sub(r"\b(?:uh|um|eh)\b", " ", text)
        text = re.sub(r"\bthe\s*\.\.\.$", "", text)
    text = re.sub(r"\d+(?:[.,:/-]\d+)*", "[number]", text)
    return lang + ":" + re.sub(r"[\s.,!?¿¡:;\"'’‘“”()…-]+", "", text)


def prompt_templates(root: Path) -> set[str]:
    keys = set()
    for name in ("translate", "destination", "polish"):
        for ex in json.loads((root / "prompts" / f"{name}.examples.json").read_text(encoding="utf-8")):
            i, o = ex["input"], ex["output"]
            keys.add(fingerprint(i.get("utterance") or i["source"], i["source_locale"]))
            translated = o.get("translated_text") or o.get("text")
            if translated:
                keys.add(fingerprint(translated, i.get("target_locale", "ko-KR")))
    return keys


def protected_templates(root: Path) -> set[str]:
    keys = prompt_templates(root)
    for path in (root / "eval").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                keys.add(fingerprint(rec["text"], rec["source_locale"]))
                if rec.get("reference"):
                    keys.add(fingerprint(rec["reference"], rec["target_locale"]))
    return keys


def server_prompt_hashes(root: Path) -> dict[str, str]:
    paths = ["prompting.py", "validation.py", "packs/glossary.json", "packs/poi.demo.json"]
    paths += [f"prompts/{name}.{suffix}" for name in ("translate", "destination", "polish")
              for suffix in ("txt", "examples.json")]
    return {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths}
