import csv
import copy
import hashlib
import json
import pytest
from pathlib import Path

from config import Settings, artifact_hashes
from eval.release_gate import check_run
from eval.evaluate import attach_attempts, reserve_final, retry_summary


def test_retry_measurement_counts_only_traced_attempts(tmp_path):
    rows = [{"case": {"task": "translate"}, "request_id": rid} for rid in ("fixed", "same", "error", "first", "missing")]
    bad = {"status": "ok", "translated_text": "wrong"}
    good = {"status": "ok", "translated_text": "correct"}
    attempts = [("fixed", 1, ["term_missing"], bad), ("fixed", 2, [], good),
                ("same", 1, ["term_missing"], bad), ("same", 2, ["term_missing"], bad),
                ("error", 1, ["bad_output"], None), ("error", 2, ["model_timeout"], None),
                ("first", 1, [], good), ("unrelated", 1, [], good)]
    log = tmp_path / "ai.err.log"
    log.write_text("startup\n" + "".join("2026-11-08 12:00:00,123 WARNING roadmate.ai translation_attempt " + json.dumps(
        {"request_id": rid, "attempt": attempt, "issues": issues, "output": out}) + "\n"
        for rid, attempt, issues, out in attempts), encoding="utf-8")
    assert attach_attempts(rows, log) == {"translate_cases": 5, "traced_cases": 4, "retried": 3,
                                         "recovered": 1, "identical": 1, "incomplete": 0, "log_errors": 0,
                                         "first_attempts": 4, "first_wrong_language": 0,
                                         "first_by_direction": {"?>?": {"cases": 5, "traced": 4, "wrong_language": 0}},
                                         "retry_by_first_issue": {
                                             "wrong_language": {"retried": 0, "recovered": 0, "identical": 0},
                                             "other": {"retried": 3, "recovered": 1, "identical": 1},
                                             "mixed": {"retried": 0, "recovered": 0, "identical": 0}}}
    assert rows[0]["translation_attempts"][0]["output"] == bad
    assert rows[-1]["translation_attempts"] == []


def test_corrupt_or_missing_attempt_log_does_not_lose_http_results(tmp_path):
    row = {"case": {"task": "translate"}, "request_id": "case-1", "http": 200, "body": {"status": "ok"}}
    log = tmp_path / "ai.err.log"
    log.write_text('rejected case-x | Please print translation_attempt on a receipt.\n'
                   'translation_attempt {"request_id": "case-1",\n'
                   'translation_attempt {"request_id": "case-1", "attempt": "invalid"}\n', encoding="utf-8")
    stats = attach_attempts([row], log)
    assert stats["traced_cases"] == 0 and stats["log_errors"] == 2
    assert row["http"] == 200 and row["body"] == {"status": "ok"}
    assert attach_attempts([row], tmp_path / "missing.log")["log_errors"] == 1


def test_retry_groups_do_not_attribute_mixed_language_errors_to_content_recovery(tmp_path):
    rows = [{"case": {"task": "translate", "source_locale": "ko-KR", "target_locale": "es-US"}, "request_id": rid}
            for rid in ("language", "content", "mixed", "accepted", "incomplete", "missing")]
    rows.append({"case": {"task": "destination"}, "request_id": "destination"})
    bad = {"status": "ok", "translated_text": "bad"}
    good = {"status": "ok", "translated_text": "good"}
    attempts = [("language", 1, ["wrong_language"], bad), ("language", 2, [], good),
                ("content", 1, ["number_missing:60"], bad), ("content", 2, ["number_missing:60"], bad),
                ("mixed", 1, ["wrong_language", "number_missing:60"], bad), ("mixed", 2, [], good),
                ("accepted", 1, [], good), ("incomplete", 2, [], good)]
    log = tmp_path / "ai.err.log"
    log.write_text("".join("translation_attempt " + json.dumps(
        {"request_id": rid, "attempt": attempt, "issues": issues, "output": out}) + "\n"
        for rid, attempt, issues, out in attempts), encoding="utf-8")
    stats = attach_attempts(rows, log)
    assert stats["first_attempts"] == 4 and stats["first_wrong_language"] == 2
    assert stats["incomplete"] == 1 and stats["translate_cases"] == 6
    assert stats["first_by_direction"] == {"ko-KR>es-US": {"cases": 6, "traced": 4, "wrong_language": 2}}
    assert stats["retry_by_first_issue"] == {
        "wrong_language": {"retried": 1, "recovered": 1, "identical": 0},
        "other": {"retried": 1, "recovered": 0, "identical": 1},
        "mixed": {"retried": 1, "recovered": 1, "identical": 0}}
    assert "- first-attempt wrong_language ko-KR>es-US: 2/4 (first-attempt coverage: 4/6)" in retry_summary(stats)
    assert "| other | 1 | 0/1 (0.0%) | 1 |" in retry_summary(stats)
    assert "| mixed | 1 | 1/1 (100.0%) | 0 |" in retry_summary(stats)


def test_retry_summary_without_logs_does_not_claim_zero_percent_recovery(tmp_path):
    row = {"case": {"task": "translate"}, "request_id": "missing"}
    lines = retry_summary(attach_attempts([row], tmp_path / "missing.log"))
    assert "| other | 0 | 0/0 (N/A) | 0 |" in lines
    assert "- retry validator recovery: 0/0 (N/A)" in lines


def test_quality_gate_rejects_mock_unreviewed_and_changed_evidence(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    settings = Settings.from_env()
    cases = [{"id": "synthetic-case", "task": "translate", "source_locale": "en-US", "target_locale": "ko-KR",
              "text": "synthetic test fixture", "must_include": ["example"]}]
    # 승인 표시는 합성 테스트 자료에만 넣는다. 실제 초안 평가셋을 승인하지 않는다.
    for c in cases:
        c.update(review_status="approved", reviewer="synthetic-test-reviewer", reviewed_at="2026-11-08")
    local_eval = tmp_path / "eval" / "general_es.jsonl"
    local_eval.parent.mkdir()
    local_eval.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    monkeypatch.setattr("eval.release_gate.ROOT", tmp_path)
    rows = [{"case": c, "body": {"status": "ok", "translated_text": "example", "model_id": settings.model_id}, "http": 200,
             "pass": True, "wall_ms": 100} for c in cases]
    results = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")
    (tmp_path / "results.jsonl").write_bytes(results)
    meta = {"dataset": "general_es.jsonl", "dataset_sha256": hashlib.sha256(local_eval.read_bytes()).hexdigest(),
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
    meta["ready"]["model_runtime"] = {"model_sha256": settings.manifest["model"]["gguf_sha256"],
        "adapter_sha256": None, "context": settings.manifest["runtime"]["ctx"], "slots": 1,
        "process": {"pid": 123, "started": "test"}, "build": "b12076", "default_parameters": {"seed": 1}}
    meta["ready"]["instance_id"] = "synthetic-test-instance"
    meta["ready_end"] = copy.deepcopy(meta["ready"])
    (tmp_path / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    review(True)
    assert check_run(tmp_path)[1] == []
    unreviewed = [dict(c, review_status="draft") for c in cases]
    local_eval.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in unreviewed), encoding="utf-8")
    assert "dataset_review_required" in check_run(tmp_path)[1]
    local_eval.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    meta["ready"]["model_runtime"]["model_sha256"] = "different"
    (tmp_path / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    assert "model_runtime_not_verified" in check_run(tmp_path)[1]
    meta["ready"]["model_runtime"]["model_sha256"] = settings.manifest["model"]["gguf_sha256"]
    (tmp_path / "run.json").write_text(json.dumps(meta), encoding="utf-8")
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


def test_final_attempt_cannot_be_repeated(tmp_path, monkeypatch):
    monkeypatch.setattr("eval.evaluate.ROOT", tmp_path)
    corpus = tmp_path / "test.locked.v2.jsonl"
    row = {"task": "translate", "text": "synthetic-test-data", "source_locale": "en-US", "target_locale": "ko-KR"}
    corpus.write_text(json.dumps(row) + "\n", encoding="utf-8")
    assert reserve_final(corpus, {"ready": True})
    with pytest.raises(FileExistsError):
        reserve_final(corpus, {"ready": True})
    row["reviewed_at"] = "changed metadata"
    corpus.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(FileExistsError):
        reserve_final(corpus, {"ready": True})
