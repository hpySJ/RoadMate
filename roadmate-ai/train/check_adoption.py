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
from eval.compare_stt import compare_pair, cohort_measures  # noqa: E402

DATASETS = {"general", "general_es", "naturalness.dev"}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def quality(rows, key):
    counts = [Counter(), Counter()]
    pending, new_bad = [], []
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
            if resolved["run1"] != "나쁨" and resolved["run2"] == "나쁨":
                new_bad.append(row["id"])
    return counts, pending, new_bad


def stt_comparison(manifest, pairs, enforce):
    stt_pairs = manifest.get("stt_comparisons", [])
    if not stt_pairs and not enforce:
        return None, []
    if len(stt_pairs) != 3 or {p["dataset"] for p in stt_pairs} != DATASETS:
        raise ValueError("Expected STT comparisons for the three development datasets")
    clean = {pair["dataset"]: pair for pair in pairs}
    rows, evidence, failures, signatures = {"baseline": [], "candidate": []}, [], [], {}
    for pair in stt_pairs:
        dataset, compared = pair["dataset"], {}
        for side in rows:
            current, provenance = compare_pair(clean[dataset][side], pair[side])
            if side in signatures and signatures[side] != provenance["signature"]:
                raise ValueError(f"STT {side}: mixed configurations across datasets")
            signatures[side] = provenance["signature"]
            rows[side].extend(current)
            compared[side] = {row["id"]: row for row in current}
            evidence.append({"dataset": dataset, "side": side, **provenance})
        if enforce:
            lost = [cid for cid, row in compared["baseline"].items()
                    if row["signals_eligible"] and not compared["candidate"][cid]["signals_eligible"]]
            failures.extend(f"{dataset}:STT_eligible_pair_lost:{cid}" for cid in lost)
    report = {side: cohort_measures(current) for side, current in rows.items()}
    report["es_to_ko"] = {side: cohort_measures([r for r in current
        if r["direction"].startswith("es-") and r["direction"].endswith("->ko-KR")])
        for side, current in rows.items()}
    report.update(historical_es_to_ko_question_loss=7,
                  reference="each model's clean output question punctuation; fresh paired baseline governs adoption",
                  pairs=evidence)
    if enforce:
        baseline, candidate = (report[side]["signals"] for side in ("baseline", "candidate"))
        es_base, es_candidate = (report["es_to_ko"][side]["signals"] for side in ("baseline", "candidate"))
        if not es_base["eligible_pairs"] or not es_candidate["eligible_pairs"]:
            failures.append("STT:es_to_ko:no_eligible_pairs")
        if es_candidate["question_loss"] >= es_base["question_loss"]:
            failures.append("STT:es_to_ko:question_loss_not_reduced")
        if candidate["question_loss"] > baseline["question_loss"]:
            failures.append("STT:question_loss_increased")
        if candidate["question_addition"]:
            failures.append("STT:question_addition_must_be_zero")
    return report, failures


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
    teacher_profile = sidecar.get("teacher_profile", manifest.get("teacher_profile", "14b-q4"))
    if manifest.get("teacher_profile", teacher_profile) != teacher_profile:
        raise ValueError("Comparison teacher profile differs from adapter training")
    self_teacher = teacher_profile == "8b-q4"
    model = load_manifest(ROOT / "models" / "manifest.qwen3-8b.json")["model"]
    totals, pending, failures, reports = [Counter(), Counter()], [], [], []
    for pair in pairs:
        dataset = pair["dataset"]
        key = read_key(pair["key"])
        if set(key["runs"]) != {"run1", "run2"}:
            raise ValueError("Expected a two-model blind comparison")
        measures, loaded, paired_records = [], [], []
        for i, name in enumerate(("baseline", "candidate")):
            records, run = load_run(pair[name])
            verify_run(records, run, dataset, None if i == 0 else adapter_hash, model)
            if key["runs"][f"run{i+1}"]["results_sha256"] != run["results_sha256"] or key["runs"][f"run{i+1}"]["metadata"] != run["metadata"]:
                raise ValueError("Review key is for different evaluation results")
            loaded.append(run)
            paired_records.append(records)
            measures.append(automatic(records))
        # Same code and runtime settings; only the loaded adapter may differ.
        if any(loaded[0]["metadata"]["ready"].get(k) != loaded[1]["metadata"]["ready"].get(k)
               for k in ("artifact_hashes", "runtime_config")):
            raise ValueError("Baseline and adapter were evaluated with different code or runtime settings")
        a, b = measures
        for metric in ("FAIL", "502", "destination_FAIL"):
            if b[metric] > a[metric]:
                failures.append(f"{dataset}:{metric}:{a[metric]}->{b[metric]}")
        if self_teacher:
            for cid, original in paired_records[0].items():
                candidate = paired_records[1][cid]
                for metric, before, after in (
                    ("FAIL", not original["pass"], not candidate["pass"]),
                    ("502", original["http"] == 502, candidate["http"] == 502),
                    ("destination_FAIL", original["case"]["task"] == "destination" and not original["pass"],
                     candidate["case"]["task"] == "destination" and not candidate["pass"])):
                    if after and not before:
                        failures.append(f"{dataset}:{metric}:new_row:{cid}")
        if b["p95_ms"] - a["p95_ms"] > 300:
            failures.append(f"{dataset}:p95_increase_over_300ms")
        review = reviews / f"review_{pair['label']}.csv" if reviews else Path(pair["review"])
        if not review.exists():
            pending.append(f"{dataset}:review_missing")
        else:
            rows, _ = read_review(review, key)
            counts, missing, new_bad = quality(rows, key)
            pending.extend(f"{dataset}:{p}" for p in missing)
            if self_teacher:
                failures.extend(f"{dataset}:new_bad:{cid}" for cid in new_bad)
            for total, count in zip(totals, counts):
                total.update(count)
        reports.append({"dataset": dataset, "baseline": a, "candidate": b})
    if not pending:
        if not sum(totals[0].values()):
            raise ValueError("No paired successful translations were reviewed")
        if not self_teacher and (totals[1]["나쁨"] > totals[0]["나쁨"] or totals[1]["어색함"] >= totals[0]["어색함"]):
            failures.append("quality_requires_bad_nonincrease_and_awkward_decrease")
    stt, stt_failures = stt_comparison(manifest, pairs, self_teacher)
    failures.extend(stt_failures)
    status = "rejected" if failures else "pending" if pending else "passed"
    result = {"status": status, "notice": "AI 검토(참고). 자동 배포·사람 검수 승인이 아닙니다.",
              "adapter_sha256": adapter_hash, "quality": {"baseline": dict(totals[0]), "candidate": dict(totals[1])},
              "automatic": reports, "failures": failures, "pending": pending}
    if self_teacher:
        result["criteria"] = {"clean": "no new bad/FAIL/502/destination_FAIL rows; p95 increase <= 300ms",
                              "stt": "es->ko question loss decreases versus fresh baseline; total loss does not increase; zero question additions",
                              "historical_es_to_ko_question_loss": 7,
                              "question_reference": "question punctuation in each model's clean output"}
    if stt is not None:
        result["stt"] = stt
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
