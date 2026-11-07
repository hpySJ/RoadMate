import csv
import copy
import hashlib
import json
from pathlib import Path

from config import Settings, artifact_hashes
from eval.release_gate import check_run


def test_quality_gate_rejects_mock_unreviewed_and_changed_evidence(tmp_path):
    root = Path(__file__).resolve().parents[1]
    settings = Settings.from_env()
    cases = [json.loads(line) for line in (root / "eval" / "general_es.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [{"case": c, "body": {"status": "ok", "translated_text": "example", "model_id": settings.model_id}, "http": 200,
             "pass": True, "wall_ms": 100} for c in cases]
    results = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    (tmp_path / "results.jsonl").write_bytes(results)
    meta = {"dataset": "general_es.jsonl", "dataset_sha256": hashlib.sha256((root / "eval" / "general_es.jsonl").read_bytes()).hexdigest(),
            "results_sha256": hashlib.sha256(results).hexdigest(),
            "ready": {"ready": True, "backend": "mock", "model_id": "mock", "artifact_hashes": artifact_hashes(),
                      "runtime_config": {"model_id": settings.model_id, "polish_ko": settings.polish_ko,
                                         "model_timeout_s": settings.model_timeout_s, "max_text_chars": settings.max_text_chars}}}
    (tmp_path / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    fields = ["id", "text", "output", "human_meaning_ok", "human_naturalness_ok", "critical_error"]

    def review(fill):
        with (tmp_path / "review.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for c in cases:
                output = "example" if c["task"] == "translate" else json.dumps({"status": "ok", "query_ko_candidate": None, "candidates": None}, ensure_ascii=False)
                w.writerow({"id": c["id"], "text": c["text"], "output": output,
                            "human_meaning_ok": "yes" if fill else "", "human_naturalness_ok": "yes" if fill else "",
                            "critical_error": "no" if fill else ""})

    review(False)
    _, issues = check_run(tmp_path)
    assert "real_model_required" in issues and any(i.startswith("review_required:") for i in issues)
    meta["ready"].update(backend="llama", model_id=settings.model_id)
    meta["ready"]["instance_id"] = "synthetic-test-instance"
    meta["ready_end"] = copy.deepcopy(meta["ready"])
    (tmp_path / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    review(True)
    assert check_run(tmp_path)[1] == []
    rows[0]["body"]["model_id"] = "mock"
    mixed = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    (tmp_path / "results.jsonl").write_bytes(mixed)
    assert any(i.startswith("model_changed_during_run:") for i in check_run(tmp_path)[1])
    (tmp_path / "results.jsonl").write_bytes(results)
    meta["ready_end"]["instance_id"] = "restarted"
    (tmp_path / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    assert "server_changed_during_run_or_missing_provenance" in check_run(tmp_path)[1]
    meta["ready_end"] = copy.deepcopy(meta["ready"])
    meta["ready"]["runtime_config"]["polish_ko"] = not settings.polish_ko
    (tmp_path / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    assert "runtime_configuration_changed" in check_run(tmp_path)[1]
    (tmp_path / "results.jsonl").write_bytes(results + b"\n")
    assert "results_changed" in check_run(tmp_path)[1]
    meta["ready"]["artifact_hashes"] = {}
    (tmp_path / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    assert "artifacts_changed" in check_run(tmp_path)[1]
