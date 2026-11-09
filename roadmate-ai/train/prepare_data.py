"""학습: 선생 모델 초안 + AI 표본 검토 기록 → group_id 단위 80/10/10 분할.

사용: python train/prepare_data.py --inputs train/data/distill.draft.jsonl --ai-review sample.evidence.json
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
from data_boundary import fingerprint, protected_templates, record_overlaps, server_prompt_hashes  # noqa: E402
from distill import PENDING, REVIEWED, filter_issues, verify_ai_review, verify_teacher_record  # noqa: E402


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


def duplicate_groups(recs: list[dict]) -> list[str]:
    # 검증: 같은 틀의 슬롯 변형도 group_id가 갈리면 분할을 넘을 수 있으니 멈춘다.
    groups_by_text: dict[str, set] = {}
    for r in recs:
        groups_by_text.setdefault(fingerprint(r["text"], r["source_locale"]), set()).add(r["group_id"])
    return [f"same template in groups {sorted(g)}: {t}" for t, g in groups_by_text.items() if len(g) > 1]


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
    ap.add_argument("--ai-review", type=Path, help="동일한 선생 모델 초안 해시에 묶인 AI 표본 검토 기록")
    ap.add_argument("--tokenizer", default=None)
    config = json.loads((ROOT / "train" / "training_config.json").read_text(encoding="utf-8"))
    ap.add_argument("--tokenizer-revision", default=config.get("base_revision"))
    ap.add_argument("--max-length", type=int, default=json.loads((ROOT / "train" / "training_config.json").read_text())["max_length"])
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    blocked = protected_templates(ROOT)
    prompt_hashes = server_prompt_hashes(ROOT) if (ROOT / "prompting.py").exists() else {}
    teacher_path = ROOT / "models/manifest.qwen3-14b.json"
    teacher = json.loads(teacher_path.read_text(encoding="utf-8")) if teacher_path.exists() else None
    evidence = json.loads(a.ai_review.read_text(encoding="utf-8")) if a.ai_review else None
    input_hashes = {str(Path(path).resolve()): sha(Path(path)) for path in a.inputs}
    evidence_hash = sha(a.ai_review) if a.ai_review else None
    recs, errors = [], []
    for path in map(Path, a.inputs):
        source_rows = load_jsonl(path)
        if not a.allow_draft and any(r.get("review_status") == PENDING for r in source_rows):
            try:
                verify_ai_review(path, source_rows, evidence or {})
            except (KeyError, TypeError, ValueError) as error:
                errors.append(f"{path.name}: {error}")
                continue
        for i, r in enumerate(source_rows):
            where = f"{path.name}:{i + 1}"
            if r.get("review_status") not in ("approved", PENDING) and not a.allow_draft:
                continue
            if record_overlaps(r, blocked):
                errors.append(f"{where} leak: template overlaps eval or few-shot")
                continue
            if not a.allow_draft:
                if r.get("source") != "teacher_distillation" or teacher is None:
                    errors.append(f"{where} teacher_only: human/Claude/Codex or unverified targets are not training labels")
                    continue
                provenance = verify_teacher_record(r, teacher, prompt_hashes)
                if provenance:
                    errors.append(f"{where} {provenance}")
                    continue
            issues = check(r)
            if not a.allow_draft:
                issues += filter_issues(r, blocked)
            if issues:
                errors.append(f"{where} {issues}")
                continue
            recs.append(r)
    errors += duplicate_groups(recs)
    if errors:
        print("\n".join(errors))
        sys.exit(f"{len(errors)} invalid records. Fix them before training.")
    if not recs:
        sys.exit("no records (선생 모델 + AI 표본 검토 기록이 필요하며 --allow-draft는 스모크 시험 전용)")

    rows = [build(r) for r in recs]
    if a.tokenizer:
        from transformers import AutoTokenizer
        if not a.tokenizer_revision:
            sys.exit("--tokenizer-revision is required for reproducible preparation")
        tok = AutoTokenizer.from_pretrained(a.tokenizer, revision=a.tokenizer_revision)
        lens = [len(tok(tok.apply_chat_template(r["prompt"] + r["completion"], tokenize=False,
                                              add_generation_prompt=False, enable_thinking=False),
                        add_special_tokens=False)["input_ids"]) for r in rows]
        too_long = [i for i, n in enumerate(lens) if n > a.max_length]
        s = sorted(lens)
        print(f"tokens p50={s[len(s) // 2]} p95={s[int(len(s) * .95) - 1 if len(s) > 1 else 0]} max={s[-1]} limit={a.max_length}")
        if too_long:
            sys.exit(f"{len(too_long)} rows exceed max_length (정답 잘림 방지): {too_long[:10]}")

    assign = split_groups(list({r["meta"]["group_id"] for r in rows}), a.seed)
    for split in ("train", "validation"):
        if split not in assign.values():
            sys.exit(f"{split} split is empty ({len(assign)} groups): add more groups or change --seed")
    if prompt_hashes != server_prompt_hashes(ROOT):
        sys.exit("prompt inputs changed during preparation: run it again")
    if input_hashes != {str(Path(path).resolve()): sha(Path(path)) for path in a.inputs} or (
            a.ai_review and evidence_hash != sha(a.ai_review)):
        sys.exit("training inputs or AI review evidence changed during preparation: run it again")
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
        "inputs": input_hashes,
        "allow_draft": a.allow_draft,
        "enable_thinking": False,
        "tokenizer": a.tokenizer,
        "tokenizer_revision": a.tokenizer_revision,
        "review_status": "smoke_draft" if a.allow_draft else REVIEWED,
        "target_source": "synthetic_or_unreviewed" if a.allow_draft else "qwen_teacher",
        "human_reviewed": False,
        "ai_review_sha256": evidence_hash,
        "teacher_model": teacher["model"] if teacher and not a.allow_draft else None,
        "prompt_versions": {"translate": P.prompt("translate")[0], "destination": P.prompt("destination")[0]},
        "glossary_version": P.glossary()["version"],
        "prompt_hashes": prompt_hashes,
        "counts": counts,
        "groups": len(assign),
        "outputs": {f"{s}.jsonl": sha(out / f"{s}.jsonl") for s in counts},
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"records={len(rows)} groups={len(assign)} split={counts} -> {out}")


if __name__ == "__main__":
    main()
