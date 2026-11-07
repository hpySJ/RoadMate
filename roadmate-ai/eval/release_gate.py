"""실제 모델 평가와 독립 검토가 모두 끝났는지 확인한다. 누락·mock·오래된 결과는 실패한다."""
import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import Settings, artifact_hashes  # noqa: E402
from eval.evaluate import pct  # noqa: E402

REQUIRED = {"dev.jsonl", "general.jsonl", "general_es.jsonl", "naturalness.dev.jsonl", "test.locked.jsonl"}


def check_run(run: Path) -> tuple[dict, list[str]]:
    meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
    ready = meta["ready"]
    settings = Settings.from_env()
    target = {"model_id": settings.model_id, "polish_ko": settings.polish_ko,
              "model_timeout_s": settings.model_timeout_s, "max_text_chars": settings.max_text_chars}
    issues = []
    if ready.get("backend") != "llama" or ready.get("model_id") == "mock" or not ready.get("ready"):
        issues.append("real_model_required")
    if ready.get("runtime_config") != target or ready.get("model_id") != settings.model_id:
        issues.append("runtime_configuration_changed")
    end = meta.get("ready_end", {})
    if (not isinstance(ready.get("instance_id"), str) or not ready["instance_id"] or not end.get("ready")
            or any(ready.get(k) != end.get(k) for k in ("instance_id", "model_id", "backend", "runtime_config", "artifact_hashes"))):
        issues.append("server_changed_during_run_or_missing_provenance")
    if ready.get("artifact_hashes") != artifact_hashes():
        issues.append("artifacts_changed")
    dataset = ROOT / "eval" / Path(meta["dataset"]).name
    if not dataset.exists() or hashlib.sha256(dataset.read_bytes()).hexdigest() != meta["dataset_sha256"]:
        issues.append("dataset_changed")
    raw = (run / "results.jsonl").read_bytes()
    if hashlib.sha256(raw).hexdigest() != meta["results_sha256"]:
        issues.append("results_changed")
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    expected = [json.loads(line) for line in dataset.read_text(encoding="utf-8").splitlines() if line.strip()] if dataset.exists() else []
    if not rows or [r["case"] for r in rows] != expected:
        issues.append("incomplete_cases")
    with (run / "review.csv").open(encoding="utf-8-sig", newline="") as f:
        reviews = list(csv.DictReader(f))
    by_id = {r["id"]: r for r in reviews}
    if len(by_id) != len(rows) or len(reviews) != len(rows):
        issues.append("review_case_mismatch")
    meanings, natural = [], []
    for r in rows:
        c, b = r["case"], r["body"]
        if r["http"] == 200 and b.get("model_id") != ready.get("model_id"):
            issues.append(f"model_changed_during_run:{c['id']}")
        review = by_id.get(c["id"], {})
        output = b.get("translated_text") or b.get("clarification_question") or b.get("error_code") or ""
        if c["task"] == "destination":
            output = json.dumps({k: b.get(k) for k in ("status", "query_ko_candidate", "candidates")}, ensure_ascii=False)
        if review.get("text") != c["text"] or review.get("output") != output:
            issues.append(f"review_content_mismatch:{c['id']}")
        for key in ("human_meaning_ok", "critical_error"):
            if review.get(key) not in ("yes", "no"):
                issues.append(f"review_required:{c['id']}:{key}")
        if review.get("critical_error") == "yes":
            issues.append(f"critical_error:{c['id']}")
        meanings.append(review.get("human_meaning_ok") == "yes")
        if c["task"] == "translate":
            if review.get("human_naturalness_ok") not in ("yes", "no"):
                issues.append(f"review_required:{c['id']}:human_naturalness_ok")
            natural.append(review.get("human_naturalness_ok") == "yes")
    auto_rate = sum(r["pass"] for r in rows) / len(rows) if rows else 0
    meaning_rate = sum(meanings) / len(meanings) if meanings else 0
    natural_rate = sum(natural) / len(natural) if natural else 0
    p95 = pct([r["wall_ms"] for r in rows if r["http"] == 200], .95)
    if auto_rate < .95:
        issues.append("auto_pass_below_95pct")
    if meaning_rate < .95:
        issues.append("meaning_below_95pct")
    if natural and natural_rate < .90:
        issues.append("naturalness_below_90pct")
    if p95 is None or p95 > 8000:
        issues.append("p95_over_8s_or_missing")
    return {"dataset": meta["dataset"], "n": len(rows), "auto": auto_rate, "meaning": meaning_rate,
            "naturalness": natural_rate, "p95_ms": p95, "polish_ko": ready.get("polish_ko"),
            "runtime_config": ready.get("runtime_config")}, issues


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("runs", type=Path, nargs="+")
    args = ap.parse_args()
    reports, failures = [], []
    for run in args.runs:
        try:
            report, issues = check_run(run)
            reports.append(report)
            failures.extend(f"{run.name}: {i}" for i in issues)
        except (OSError, ValueError, KeyError, TypeError) as e:
            failures.append(f"{run.name}: invalid or missing evidence ({e})")
    names = [r["dataset"] for r in reports]
    failures.extend(f"missing_dataset:{name}" for name in sorted(REQUIRED - set(names)))
    if len(names) != len(set(names)):
        failures.append("duplicate_dataset: select one final configuration per dataset")
    if len({r["polish_ko"] for r in reports}) > 1:
        failures.append("mixed_polish_configuration")
    print(json.dumps({"passed": not failures, "reports": reports, "failures": failures}, ensure_ascii=False, indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
