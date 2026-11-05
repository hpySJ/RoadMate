"""학습: 검수 자료 → 서버와 같은 프롬프트의 prompt/completion + group_id 단위 80/10/10 분할.

사용: .venv-train\\Scripts\\python.exe train\\prepare_data.py --tokenizer Qwen/Qwen3-4B-Instruct-2507
      (스모크 시험만) --allow-draft
"""
import argparse
import glob
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import prompting as P  # noqa: E402
from validation import looks_like_other_language, validate_destination, validate_translation  # noqa: E402


def norm(s: str) -> str:
    return "".join(s.lower().split())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def build(rec: dict) -> dict:
    # 학습: 서버 추론과 같은 빌더(prompting.py)로 prompt 생성 → 학습·추론 프롬프트 불일치 방지
    if rec["task"] == "translate":
        prompt = P.translate_messages(rec["source_locale"], rec["target_locale"], rec["scenario"], rec["text"])
    else:
        prompt = P.destination_messages(rec["source_locale"], rec["text"])
    meta = {k: rec.get(k) for k in ("group_id", "task", "source_locale", "target_locale", "scenario", "text")}
    return {"prompt": prompt, "completion": [{"role": "assistant", "content": P.completion_text(rec["output"])}], "meta": meta}


def check(rec: dict) -> list[str]:
    for k in ("group_id", "task", "source_locale", "text", "output"):
        if k not in rec:
            return [f"missing:{k}"]
    if rec["task"] == "translate":
        if looks_like_other_language(rec["text"], rec["source_locale"]):
            return ["wrong_language_case_is_handled_by_code"]
        return validate_translation(rec["output"], rec["text"], rec["source_locale"], rec["target_locale"])
    if rec["task"] == "destination":
        return validate_destination(rec["output"])
    return [f"bad_task:{rec['task']}"]


def split_groups(groups: list[str], seed: int) -> dict[str, str]:
    # 학습: group_id 해시로 80/10/10 분할 → 자료를 추가해도 기존 그룹의 분할이 바뀌지 않는다(같은 원문·변형은 같은 분할)
    def bucket(g: str) -> int:
        return int(hashlib.sha256(f"{seed}:{g}".encode()).hexdigest(), 16) % 100
    return {g: "validation" if bucket(g) < 10 else "test" if bucket(g) < 20 else "train" for g in groups}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="*", default=sorted(glob.glob(str(ROOT / "train" / "data" / "*.jsonl"))))
    ap.add_argument("--out", default=str(ROOT / "train" / "out"))
    ap.add_argument("--allow-draft", action="store_true", help="검수 전 자료 포함(스모크 시험 전용)")
    ap.add_argument("--tokenizer", default=None)
    ap.add_argument("--max-length", type=int, default=json.loads((ROOT / "train" / "training_config.json").read_text())["max_length"])
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    blocked = {norm(c["text"]) for f in ("dev.jsonl", "test.locked.jsonl", "general.jsonl")
               for c in load_jsonl(ROOT / "eval" / f)}
    blocked |= {norm(e["input"]["utterance"]) for n in ("translate", "destination") for e in P.examples(n)}
    recs, errors = [], []
    for path in map(Path, a.inputs):
        for i, r in enumerate(load_jsonl(path)):
            where = f"{path.name}:{i + 1}"
            if r.get("review_status") != "approved" and not a.allow_draft:
                continue
            if norm(r.get("text", "")) in blocked:
                errors.append(f"{where} leak: text overlaps eval or few-shot")
                continue
            issues = check(r)
            if issues:
                errors.append(f"{where} {issues}")
                continue
            recs.append(r)
    if errors:
        print("\n".join(errors))
        sys.exit(f"{len(errors)} invalid records. Fix them before training.")
    if not recs:
        sys.exit("no records (approved 자료가 없으면 --allow-draft는 스모크 시험에만 사용)")

    rows = [build(r) for r in recs]
    if a.tokenizer:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(a.tokenizer)
        lens = [len(tok(tok.apply_chat_template(r["prompt"] + r["completion"], tokenize=False),
                        add_special_tokens=False)["input_ids"]) for r in rows]
        too_long = [i for i, n in enumerate(lens) if n > a.max_length]
        s = sorted(lens)
        print(f"tokens p50={s[len(s) // 2]} p95={s[int(len(s) * .95) - 1 if len(s) > 1 else 0]} max={s[-1]} limit={a.max_length}")
        if too_long:
            sys.exit(f"{len(too_long)} rows exceed max_length (정답 잘림 방지): {too_long[:10]}")

    assign = split_groups(list({r["meta"]["group_id"] for r in rows}), a.seed)
    if "validation" not in assign.values():
        sys.exit(f"validation split is empty ({len(assign)} groups): add more groups or change --seed")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    for split in ("train", "validation", "test"):
        part = [r for r in rows if assign[r["meta"]["group_id"]] == split]
        counts[split] = len(part)
        with (out / f"{split}.jsonl").open("w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "split_manifest.json").write_text(json.dumps({"seed": a.seed, "groups": assign}, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "dataset_manifest.json").write_text(json.dumps({
        "inputs": {Path(p).name: sha(Path(p)) for p in a.inputs},
        "allow_draft": a.allow_draft,
        "prompt_versions": {"translate": P.prompt("translate")[0], "destination": P.prompt("destination")[0]},
        "glossary_version": P.glossary()["version"],
        "counts": counts,
        "groups": len(assign),
        "outputs": {f"{s}.jsonl": sha(out / f"{s}.jsonl") for s in counts},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"records={len(rows)} groups={len(assign)} split={counts} -> {out}")


if __name__ == "__main__":
    main()
