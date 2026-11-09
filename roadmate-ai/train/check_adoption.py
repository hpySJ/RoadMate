"""Compare a completed, blinded LoRA review. AI advice; never installs an adapter."""
import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_manifest  # noqa: E402
from eval.evaluate import judge, pct  # noqa: E402
from eval.export_review import cell, load_run, read_key, read_review  # noqa: E402

DATASETS = {"general", "general_es", "naturalness.dev"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def quality(rows, key):
    counts = [Counter(), Counter()]
    pending = []
    if {r["id"] for r in rows} != set(key["rows"]):
        raise ValueError("Review does not cover every exported row")
    for row in rows:
        sides = key["rows"][row["id"]]["sides"]
        if not all(s["status"] == "ok" for s in sides.values()):
            continue
        verdicts = [cell(row, f"{reviewer}_verdict", key) for reviewer in ("claude", "codex")]
        final = cell(row, "final", key)
        resolved = {}
        for side, state in sides.items():
            a, b = (v.get(side) for v in verdicts)
            if a is None or b is None:
                pending.append(f"{row['id']}:{side}:independent_reviews_required")
            elif a != b and side not in final:
                pending.append(f"{row['id']}:{side}:disagreement_needs_final")
            else:
                resolved[state["run"]] = final.get(side, a)
        if len(resolved) == 2:
            for i, rid in enumerate(("run1", "run2")):
                counts[i][resolved[rid]] += 1
    return counts, pending


def automatic(records):
    rows = list(records.values())
    if any(type(r.get("pass")) is not bool or r["pass"] != judge(r["case"], r["http"], r["body"])[0] for r in rows):
        raise ValueError("Stored automatic judgments do not match outputs")
    latencies = [r.get("wall_ms") for r in rows if r["http"] == 200]
    if not latencies or any(type(x) not in (float, int) or not math.isfinite(x) or x < 0 for x in latencies):
        raise ValueError("Missing or invalid successful-request latency")
    return {"FAIL": sum(not r["pass"] for r in rows), "502": sum(r["http"] == 502 for r in rows),
            "destination_FAIL": sum(not r["pass"] for r in rows if r["case"]["task"] == "destination"),
            "p95_ms": pct(latencies, .95)}


def verify_run(records, run, dataset, adapter_sha, model):
    meta = run["metadata"]
    ready, end = meta.get("ready", {}), meta.get("ready_end", {})
    runtime = ready.get("model_runtime", {})
    expected_path = ROOT / "eval" / f"{dataset}.jsonl"
    expected = [json.loads(line) for line in expected_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if (meta.get("dataset") != expected_path.name or meta.get("dataset_sha256") != sha(expected_path)
            or [r["case"] for r in records.values()] != expected):
        raise ValueError(f"{dataset}: incomplete or changed evaluation input")
    if (meta.get("model_profile") != "8b-q4" or meta.get("model_profile_consistent") is not True
            or ready.get("backend") != "llama" or not ready.get("ready") or not end.get("ready")
            or not ready.get("instance_id") or not ready.get("artifact_hashes")
            or any(ready.get(k) != end.get(k) for k in
                   ("instance_id", "model_id", "model_profile", "runtime_config", "artifact_hashes", "model_runtime"))
            or runtime.get("model_sha256") != model["gguf_sha256"]
            or runtime.get("adapter_sha256") != adapter_sha
            or not runtime.get("process") or not runtime.get("build") or not runtime.get("default_parameters")):
        raise ValueError(f"{dataset}: real, consistent 8B model/adapter evidence required")


def decide(stage_dir, reviews=None):
    manifest = json.loads((stage_dir / "comparison_manifest.json").read_text(encoding="utf-8"))
    pairs = manifest["comparisons"]
    if manifest.get("stage") != "train-eval" or len(pairs) != 3 or {p["dataset"] for p in pairs} != DATASETS:
        raise ValueError("Expected train-eval comparisons for the three development datasets only")
    adapter = Path(manifest["adapter"])
    adapter_hash = sha(adapter)
    sidecar_path = Path(str(adapter) + ".json")
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    if sidecar.get("sha256", sidecar.get("adapter_sha256")) != adapter_hash:
        raise ValueError("Adapter differs from its conversion sidecar")
    model = load_manifest(ROOT / "models" / "manifest.qwen3-8b.json")["model"]
    totals, pending, failures, reports = [Counter(), Counter()], [], [], []
    for pair in pairs:
        dataset = pair["dataset"]
        key = read_key(pair["key"])
        if set(key["runs"]) != {"run1", "run2"}:
            raise ValueError("Expected a two-model blind comparison")
        measures, loaded = [], []
        for i, name in enumerate(("baseline", "candidate")):
            records, run = load_run(pair[name])
            verify_run(records, run, dataset, None if i == 0 else adapter_hash, model)
            if key["runs"][f"run{i+1}"]["results_sha256"] != run["results_sha256"] or key["runs"][f"run{i+1}"]["metadata"] != run["metadata"]:
                raise ValueError("Review key is for different evaluation results")
            loaded.append(run)
            measures.append(automatic(records))
        # Same code and runtime settings; only the loaded adapter may differ.
        if any(loaded[0]["metadata"]["ready"].get(k) != loaded[1]["metadata"]["ready"].get(k)
               for k in ("artifact_hashes", "runtime_config")):
            raise ValueError("Baseline and adapter were evaluated with different code or runtime settings")
        a, b = measures
        for metric in ("FAIL", "502", "destination_FAIL"):
            if b[metric] > a[metric]:
                failures.append(f"{dataset}:{metric}:{a[metric]}->{b[metric]}")
        if b["p95_ms"] - a["p95_ms"] > 300:
            failures.append(f"{dataset}:p95_increase_over_300ms")
        review = reviews / f"review_{pair['label']}.csv" if reviews else Path(pair["review"])
        if not review.exists():
            pending.append(f"{dataset}:review_missing")
        else:
            rows, _ = read_review(review, key)
            counts, missing = quality(rows, key)
            pending.extend(f"{dataset}:{p}" for p in missing)
            for total, count in zip(totals, counts):
                total.update(count)
        reports.append({"dataset": dataset, "baseline": a, "candidate": b})
    if not pending:
        if not sum(totals[0].values()):
            raise ValueError("No paired successful translations were reviewed")
        if totals[1]["나쁨"] > totals[0]["나쁨"] or totals[1]["어색함"] >= totals[0]["어색함"]:
            failures.append("quality_requires_bad_nonincrease_and_awkward_decrease")
    status = "rejected" if failures else "pending" if pending else "passed"
    result = {"status": status, "notice": "AI 검토(참고). 자동 배포·사람 검수 승인이 아닙니다.",
              "adapter_sha256": adapter_hash, "quality": {"baseline": dict(totals[0]), "candidate": dict(totals[1])},
              "automatic": reports, "failures": failures, "pending": pending}
    if status == "rejected":
        sidecar["adoption_status"] = "rejected"
        sidecar["adoption_failures"] = failures
        sidecar_path.write_text(json.dumps(sidecar, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage-dir", type=Path, required=True)
    ap.add_argument("--reviews", type=Path)
    args = ap.parse_args(argv)
    try:
        result = decide(args.stage_dir, args.reviews)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        result = {"status": "invalid", "failures": [str(exc)]}
    args.stage_dir.mkdir(parents=True, exist_ok=True)
    (args.stage_dir / "decision.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    message = "# LoRA 채택 검사 — AI 검토(참고)\n\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    (args.stage_dir / "decision.md").write_text(message, encoding="utf-8")
    print(message)
    return int(result["status"] in {"invalid", "rejected"})


if __name__ == "__main__":
    raise SystemExit(main())
