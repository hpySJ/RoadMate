# 데이터 경계
import hashlib
import json
import re
import sys
from functools import lru_cache
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import prompting as P  # noqa: E402
from validation import EN_WORD_RE, ES_WORD_RE, KO_COUNTER, NATIVE_RE, KO_ORD  # noqa: E402
from eval.stt_format import normalize_source  # noqa: E402


@lru_cache(maxsize=8192)
def fingerprint(text: str, locale: str) -> str:
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


def _template_keys(text: str, locale: str, include_stt: bool) -> set[str]:
    keys = {fingerprint(text, locale)}
    if include_stt:
        keys.add(fingerprint(normalize_source(text, locale), locale))
    return keys


def prompt_templates(root: Path, *, include_locked: bool = True, include_stt: bool = False) -> set[str]:
    keys = set()
    for path in (root / "prompts").glob("*.examples.json"):
        if not include_locked:
            if path.name.casefold().startswith("test.locked"):
                continue
            if path.resolve(strict=True) != root.resolve() / "prompts" / path.name:
                raise ValueError(f"protected prompt path was redirected: {path.name}")
        for ex in json.loads(path.read_text(encoding="utf-8")):
            i, o = ex["input"], ex["output"]
            keys.update(_template_keys(i.get("utterance") or i["source"], i["source_locale"], include_stt))
            translated = o.get("translated_text") or o.get("text")
            if translated:
                keys.update(_template_keys(translated, i.get("target_locale", "ko-KR"), include_stt))
    return keys


def protected_templates(root: Path, *, include_locked: bool = True, include_stt: bool = False) -> set[str]:
    keys = prompt_templates(root, include_locked=include_locked, include_stt=include_stt)
    for path in (root / "eval").glob("*.jsonl"):
        if not include_locked:
            if path.name.casefold().startswith("test.locked"):
                continue
            if path.resolve(strict=True) != root.resolve() / "eval" / path.name:
                raise ValueError(f"protected evaluation path was redirected: {path.name}")
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                keys.update(_template_keys(rec["text"], rec["source_locale"], include_stt))
                if rec.get("reference"):
                    keys.update(_template_keys(rec["reference"], rec["target_locale"], include_stt))
    return keys


def overlaps(text: str, locale: str, protected: set[str]) -> bool:
    candidate = fingerprint(text, locale)
    if candidate in protected:
        return True
    language, _, value = candidate.partition(":")
    if len(value) < 12:
        return False
    for item in protected:
        lang, _, other = item.partition(":")
        if lang != language or len(other) < 12:
            continue
        matcher = SequenceMatcher(None, value, other, autojunk=False)
        if matcher.real_quick_ratio() >= .9 and matcher.quick_ratio() >= .9 and matcher.ratio() >= .9:
            return True
    return False


def record_overlaps(record: dict, protected: set[str]) -> list[str]:
    issues = ["boundary_source"] if overlaps(record["text"], record["source_locale"], protected) else []
    output = record.get("output", {})
    if record["task"] == "translate":
        texts = [(output.get("translated_text"), record["target_locale"]),
                 (output.get("clarification_question"), record["source_locale"])]
    else:
        texts = [(output.get("query_original"), record["source_locale"]),
                 (output.get("query_ko_candidate"), "ko-KR")]
    if any(text and overlaps(text, locale, protected) for text, locale in texts):
        issues.append("boundary_output")
    return issues


def server_prompt_hashes(root: Path) -> dict[str, str]:
    paths = ["prompting.py", "validation.py"]
    paths += [p.relative_to(root).as_posix() for folder in ("prompts", "packs")
              for p in sorted((root / folder).glob("*.*")) if p.is_file()]
    return {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths}


def inference_overlaps(root: Path) -> list[dict[str, str]]:
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

    def key(text, locale):
        text = re.sub(r"\b(?:please|por favor)\b|(?<![가-힣])좀\s+", "", text, flags=re.I)
        return fingerprint(text, locale).partition(":")[2]

    def exact(text):
        return re.sub(r"[\W_]+", "", text.casefold())

    artifacts = []
    for folder in ("prompts", "packs"):
        for path in sorted((root / folder).glob("*.*")):
            if path.suffix not in (".txt", ".json"):
                continue
            raw = path.read_text(encoding="utf-8")
            values = list(strings(json.loads(raw))) if path.suffix == ".json" else raw.splitlines()
            artifacts.append((path.relative_to(root).as_posix(), values))
    cache = {}
    hits = []
    for path in sorted((root / "eval").glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            for field, locale in (("text", record["source_locale"]), ("reference", record.get("target_locale"))):
                if not locale or not record.get(field):
                    continue
                expected = key(record[field], locale)
                original = exact(record[field])
                substantive = len(re.sub(r"\[(?:fuel|place|number)\]", "", expected)) >= 6
                for artifact, values in artifacts:
                    if (artifact, locale) not in cache:
                        cache[artifact, locale] = [key(v, locale) for v in values]
                    candidates = cache[artifact, locale]
                    full_copy = original and any(original == exact(v) for v in values)
                    near_copy = substantive and len(expected) >= 12 and any(len(value) >= 12
                        and (SequenceMatcher(None, expected, value).ratio() >= .9 or expected in value)
                        for value in candidates)
                    if full_copy or near_copy:
                        hits.append({"dataset": path.name, "id": record["id"], "field": field, "artifact": artifact})
    return hits
