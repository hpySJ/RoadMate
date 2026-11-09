"""고정 모델의 tokenizer 디렉터리로 개발 문장·최대 입력·교정 재시도의 토큰 예산을 잰다."""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import prompting as P  # noqa: E402
from config import load_manifest  # noqa: E402
from engine import REPAIR_HINT, translation_repair  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tokenizer-dir", type=Path, required=True, help="manifest base_revision의 tokenizer.json/config 경로")
    args = ap.parse_args()
    from jinja2.sandbox import ImmutableSandboxedEnvironment
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(args.tokenizer_dir / "tokenizer.json"))
    cfg = json.loads((args.tokenizer_dir / "tokenizer_config.json").read_text(encoding="utf-8"))
    template = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True).from_string(cfg["chat_template"])
    limit = load_manifest()["runtime"]["ctx"]
    rows = []

    def check(name, messages, generation):
        text = template.render(messages=messages, add_generation_prompt=True, tools=None)
        n = len(tok.encode(text, add_special_tokens=False).ids)
        rows.append({"case": name, "prompt": n, "generation_reserved": generation,
                     "total": n + generation, "fits": n + generation <= limit})

    cases = [json.loads(line) for name in ("dev.jsonl", "general.jsonl", "general_es.jsonl", "naturalness.dev.jsonl")
             for line in (ROOT / "eval" / name).read_text(encoding="utf-8").splitlines() if line.strip()]
    # 난해한 한국어 400자도 API가 허용하므로 짧은 평가 문장만 재지 않는다.
    for target in ("en-US", "es-US"):
        cases.append({"id": f"max-ko-{target}", "task": "translate", "source_locale": "ko-KR", "target_locale": target,
                      "scenario": "general", "text": ("가나다라마바사아자차카타파하" * 29)[:400]})
    for source, sentence in (("en-US", "Please explain the rental agreement and help with my luggage. "),
                             ("es-US", "Necesito ayuda con el contrato y el equipaje. ")):
        cases.append({"id": f"max-{source}-ko", "task": "translate", "source_locale": source, "target_locale": "ko-KR",
                      "scenario": "general", "text": (sentence * 12)[:400]})
    for c in cases:
        if c["task"] != "translate":
            check(c["id"], P.destination_messages(c["source_locale"], c["text"]), P.DESTINATION_MAX_TOKENS)
            continue
        messages = P.translate_messages(c["source_locale"], c["target_locale"], c["scenario"], c["text"])
        check(c["id"], messages, P.TRANSLATE_MAX_TOKENS)
        # 모든 교정 지시와 혼합 문자 교정의 원문 단어 전체를 예약해 상한을 잰다. 오답 초안은 넣지 않는다.
        issues = list(REPAIR_HINT) + [f"mixed_script:{w}" for w in re.findall(r"[A-Za-z]{2,}", P.fold(c["text"]))]
        check(c["id"] + ":retry", translation_repair(messages, issues), P.TRANSLATE_MAX_TOKENS)
        if c["target_locale"] == "ko-KR":
            # 초안 256토큰 전체를 예약한다. 짧은 정답/자리표시자를 실제 초안 상한으로 쓰지 않는다.
            check(c["id"] + ":polish", P.polish_messages(c["source_locale"], c["text"], "",
                  P.select_glossary(c["text"], c["source_locale"], "ko-KR")), P.POLISH_MAX_TOKENS + P.TRANSLATE_MAX_TOKENS)
    report = {"context": limit, "tokenizer_dir": str(args.tokenizer_dir), "checks": len(rows),
              "largest": sorted(rows, key=lambda r: -r["total"])[:5], "failures": [r for r in rows if not r["fits"]]}
    dest = ROOT / "run" / "prompt_budget.json"
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(report["failures"]))


if __name__ == "__main__":
    raise SystemExit(main())
