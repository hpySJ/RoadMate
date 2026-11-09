# 평가
import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from validation import space_english_number_boundaries  # noqa: E402

DATASETS = ("general.jsonl", "general_es.jsonl", "naturalness.dev.jsonl")
STT_FORMAT_VERSION = "stt-format-v3"
APOSTROPHES = frozenset("'’‘＇")
_CLOCK = re.compile(r"(?<![\w:.,])(?P<hour>0?[1-9]|1[0-2]):(?P<minute>[0-5][0-9])\s*(?P<meridiem>[ap])\.?\s*m\.?(?!\w)")
_GROUPED_INTEGER = r"[1-9][0-9]{0,2}(?:,[0-9]{3})+"
_CURRENCY = r"won|krw|usd|dollars?|eur|euros?|gbp"
_MONEY_SUFFIX = re.compile(rf"(?<![\w.,])(?P<amount>{_GROUPED_INTEGER})\s*(?P<unit>{_CURRENCY})(?![\w'’‘＇])")
_MONEY_PREFIX = re.compile(rf"(?<!\w)(?P<unit>{_CURRENCY}|[$₩€£])\s*(?P<amount>{_GROUPED_INTEGER})(?![\w.,])")
RULES = [
    "Unicode str.lower; preserve accents, digits and symbols (category S).",
    "Preserve apostrophes, including contractions; replace other sentence punctuation with spaces.",
    "English only: valid 12-hour H:MM with am/pm becomes HMM a m or HMM p m.",
    "English only: comma-grouped integers in currency context become N thousand M; omit a zero remainder and retain the currency.",
    "English only: split letter/digit boundaries in scale-anchored number phrases; preserve unknown identifier prefixes and all words/digits (including one, 1 and red).",
    "Preserve other numeric internal punctuation, numeric minus signs, leading decimal separators and percent units.",
    "Do not apply English time/amount conversions to Korean or Spanish; retain their numeric formats and units.",
    "Never replace won with one, 1 or red; observed recognition errors are not deterministic formatting rules.",
    "Collapse Unicode whitespace and strip leading/trailing whitespace.",
    "Change only text; retain all other case fields and case order.",
]
OBSERVATIONS = {
    "source": "user_reported",
    "environment": {"emulator": "Pleos v2.1.0", "recognizer": "ON_DEVICE", "input": "TTS recorded audio injection"},
    "independently_reproduced": False,
    "english": {"lowercase": True, "sentence_punctuation": False, "apostrophe_retained": "don't",
                "joined_fragments": ["just30 thousand1", "is87 thousand500red one"],
                "examples": [{"input": "7:40 a.m.", "observed": ["740 a m"]},
                             {"input": "30,000 won", "observed": ["30 thousand one", "30 thousand1"]},
                             {"input": "87,500 won", "observed": ["87 thousand500red one"]}]},
    "korean": {"number_unit_examples": ["오후 7시 30분", "3만 5천원"], "question_mark": False},
    "limits": "Reported examples guide formatting only; this is not a recognizer replay or a measured error distribution. Won misrecognitions are recorded but not injected.",
}


def _thousands(amount: str) -> str:
    thousands, remainder = divmod(int(amount.replace(",", "")), 1000)
    return f"{thousands} thousand" + (f" {remainder}" if remainder else "")


def normalize_source(text: str, locale="en-US") -> str:
    text = text.lower()
    if locale.split("-", 1)[0] == "en":
        text = space_english_number_boundaries(text)
        text = _CLOCK.sub(lambda m: f"{int(m['hour'])}{m['minute']} {m['meridiem']} m", text)
        text = _MONEY_SUFFIX.sub(lambda m: f"{_thousands(m['amount'])} {m['unit']}", text)
        text = _MONEY_PREFIX.sub(lambda m: f"{m['unit']} {_thousands(m['amount'])}", text)
    chars = []
    for index, char in enumerate(text):
        before, after = text[index - 1:index] if index else "", text[index + 1:index + 2]
        numeric_punctuation = (before.isdecimal() and after.isdecimal()
            or char in "-.," and after.isdecimal() and not before.isalnum()
            or char == "-" and after in (".", ",") and text[index + 2:index + 3].isdecimal() and not before.isalnum()
            or char in "%‰‱" and before.isdecimal())
        if char in APOSTROPHES or numeric_punctuation or not unicodedata.category(char).startswith("P"):
            chars.append(char)
        else:
            chars.append(" ")
    return " ".join("".join(chars).split())


def variant_bytes(cases: list[dict]) -> bytes:
    return "".join(json.dumps({**case, "text": normalize_source(case["text"], case["source_locale"])}, ensure_ascii=False,
                              separators=(",", ":")) + "\n" for case in cases).encode("utf-8")


def build_variants(out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError(f"output directory already exists: {out_dir}")
    variants, datasets, seen = {}, [], set()
    for name in DATASETS:
        expected = ROOT.resolve() / "eval" / name
        source = expected.resolve(strict=True)
        if source != expected or "test.locked" in source.name:
            raise ValueError(f"canonical evaluation path was redirected: {expected}")
        raw = source.read_bytes()
        cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        if not cases:
            raise ValueError(f"empty canonical dataset: {name}")
        changed = 0
        for case in cases:
            if (not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]
                    or not isinstance(case.get("text"), str) or not isinstance(case.get("source_locale"), str)):
                raise ValueError(f"invalid case in {name}: id, text and source_locale must be strings")
            if case["id"] in seen:
                raise ValueError(f"duplicate case id: {case['id']}")
            seen.add(case["id"])
            normalized = normalize_source(case["text"], case["source_locale"])
            if not normalized:
                raise ValueError(f"empty normalized source: {case['id']}")
            changed += normalized != case["text"]
        variants[name] = variant_bytes(cases)
        datasets.append({"source": f"eval/{name}", "source_sha256": hashlib.sha256(raw).hexdigest(),
                         "variant": name, "variant_sha256": hashlib.sha256(variants[name]).hexdigest(),
                         "count": len(cases), "changed_count": changed})
    manifest = {"transform_version": STT_FORMAT_VERSION, "rules": RULES,
                "observations": OBSERVATIONS, "datasets": datasets}
    out_dir.mkdir(parents=True)
    for name, content in variants.items():
        (out_dir / name).write_bytes(content)
    (out_dir / "stt_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description='개발 평가셋의 STT 형식 사본을 만듭니다.')
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(build_variants(args.out_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
