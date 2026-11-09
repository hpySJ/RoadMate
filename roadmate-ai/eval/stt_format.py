"""Create punctuation/case variants of the three development evaluation sets; no inference."""
import argparse
import hashlib
import json
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ("general.jsonl", "general_es.jsonl", "naturalness.dev.jsonl")
STT_FORMAT_VERSION = "stt-format-v1"
APOSTROPHES = frozenset("'’‘＇")
RULES = [
    "Unicode str.lower; preserve accents, digits and symbols (category S).",
    "Replace Unicode punctuation (category P) with spaces to avoid joining words.",
    "Drop straight, curly and fullwidth apostrophes between letters/combining marks without a space.",
    "Collapse Unicode whitespace and strip leading/trailing whitespace.",
    "Change only text; retain all other case fields and case order.",
]


def normalize_source(text: str) -> str:
    text = text.lower()
    chars = []
    for index, char in enumerate(text):
        if not unicodedata.category(char).startswith("P"):
            chars.append(char)
        elif (char in APOSTROPHES and 0 < index < len(text) - 1
              and all(unicodedata.category(neighbor)[0] in "LM" for neighbor in (text[index - 1], text[index + 1]))):
            continue
        else:
            chars.append(" ")
    return " ".join("".join(chars).split())


def variant_bytes(cases: list[dict]) -> bytes:
    """Deterministic serialization shared by the variant writer and paired comparison."""
    return "".join(json.dumps({**case, "text": normalize_source(case["text"])}, ensure_ascii=False,
                              separators=(",", ":")) + "\n" for case in cases).encode("utf-8")


def build_variants(out_dir: Path) -> dict:
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise FileExistsError(f"output directory already exists: {out_dir}")
    variants, datasets, seen = {}, [], set()
    for name in DATASETS:
        expected = ROOT.resolve() / "eval" / name
        source = expected.resolve(strict=True)
        # A canonical filename must not be redirected to another set or outside eval/.
        if source != expected or "test.locked" in source.name:
            raise ValueError(f"canonical evaluation path was redirected: {expected}")
        raw = source.read_bytes()
        cases = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
        if not cases:
            raise ValueError(f"empty canonical dataset: {name}")
        changed = 0
        for case in cases:
            if (not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]
                    or not isinstance(case.get("text"), str)):
                raise ValueError(f"invalid case in {name}: id and text must be strings")
            if case["id"] in seen:
                raise ValueError(f"duplicate case id: {case['id']}")
            seen.add(case["id"])
            normalized = normalize_source(case["text"])
            if not normalized:
                raise ValueError(f"empty normalized source: {case['id']}")
            changed += normalized != case["text"]
        variants[name] = variant_bytes(cases)
        datasets.append({"source": f"eval/{name}", "source_sha256": hashlib.sha256(raw).hexdigest(),
                         "variant": name, "variant_sha256": hashlib.sha256(variants[name]).hexdigest(),
                         "count": len(cases), "changed_count": changed})
    manifest = {"transform_version": STT_FORMAT_VERSION, "rules": RULES, "datasets": datasets}
    # Validate every source before creating any output or parent directories.
    out_dir.mkdir(parents=True)
    for name, content in variants.items():
        (out_dir / name).write_bytes(content)
    (out_dir / "stt_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(build_variants(args.out_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
