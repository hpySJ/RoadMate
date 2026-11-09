import copy
import csv
import json
from pathlib import Path

import pytest

from eval import compare_stt as C
from eval.evaluate import judge
from eval.stt_format import normalize_source, variant_bytes


def raw_rows(rows):
    return ("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)).encode("utf-8")


def save_run(folder, rows, dataset, dataset_raw, mock=False):
    folder.mkdir()
    model = "mock" if mock else "synthetic-model"
    ready = {"ready": True, "instance_id": folder.name, "backend": "mock" if mock else "llama", "model_id": model,
             "model_profile": "8b-q4", "engine_bundle": "synthetic-bundle", "versions": {"translate_prompt": "synthetic-v1"},
             "artifact_hashes": {"prompting.py": "a" * 64},
             "runtime_config": {"model_profile": "8b-q4", "model_id": "synthetic-model", "model_timeout_s": 8, "max_text_chars": 400},
             "model_runtime": None if mock else {"model_sha256": "b" * 64, "adapter_sha256": None, "context": 4096,
                 "slots": 1, "process": {"pid": len(folder.name) + 100, "started": folder.name}, "build": "b12076",
                 "default_parameters": {"seed": 42}, "chat_template_kwargs": {"enable_thinking": False},
                 "chat_template_sha256": "d" * 64}}
    for row in rows:
        if row["http"] == 200:
            row["body"]["model_id"] = model
        row["pass"], row["flags"] = judge(row["case"], row["http"], row["body"])
    raw = raw_rows(rows)
    (folder / "results.jsonl").write_bytes(raw)
    meta = {"dataset": dataset, "dataset_sha256": C.sha(dataset_raw), "results_sha256": C.sha(raw),
            "model_profile": "8b-q4", "model_profile_consistent": True, "ready": ready, "ready_end": copy.deepcopy(ready)}
    (folder / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder


@pytest.fixture
def pair(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "eval").mkdir(parents=True)
    monkeypatch.setattr(C, "ROOT", root)
    def create(dataset="general.jsonl", *, mock=False, sources=None):
        cases = [{"id": f"synthetic-{i}", "task": "translate", "text": text, "source_locale": "en-US",
                  "target_locale": "ko-KR", "scenario": "general", "reference": "문서 표를 확인해 주세요.",
                  "must_include": ["표"], "expect_status": "ok"} for i, text in enumerate(sources or [
                      "Could You Please Inspect This Label?", "Please Look At That Label.",
                      "Can You Examine A Label?", "Please Check The Other Label.",
                      "Is This The Label?", "Where Is A Spare Label?"])]
        raw = raw_rows(cases)
        (root / "eval" / dataset).write_bytes(raw)
        def ok(text):
            return {"http": 200, "body": {"status": "ok", "translated_text": text}}
        blocked = {"http": 502, "body": {"error_code": "output_validation_failed"}}
        clarifies = {"http": 200, "body": {"status": "clarification_required", "clarification_question": "어느 표인가요?"}}
        original = [ok("표를 확인해 주시겠어요?"), ok("이 표를 확인해 주세요."), blocked,
                    ok("저 표를 확인해 주세요."), clarifies, ok("이 표를 봐 주세요.")]
        stt = [ok("표를 확인해 주세요."), ok("다른 문서예요."), ok("그 표를 확인해 주세요."),
               blocked, clarifies, ok("그 표를 봐 주세요.")]
        original = [{"case": copy.deepcopy(case), **copy.deepcopy(value)} for case, value in zip(cases, original)]
        stt = [{"case": {**copy.deepcopy(case), "text": normalize_source(case["text"], case["source_locale"])}, **copy.deepcopy(value)}
               for case, value in zip(cases, stt)]
        return (save_run(tmp_path / (dataset + "-original"), original, dataset, raw, mock),
                save_run(tmp_path / (dataset + "-stt"), stt, dataset, variant_bytes(cases), mock))
    return create


def change_meta(folder, edit):
    path = folder / "run.json"
    meta = json.loads(path.read_text())
    edit(meta)
    path.write_text(json.dumps(meta), encoding="utf-8")


def change_rows(folder, edit):
    path = folder / "results.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    edit(rows)
    raw = raw_rows(rows)
    path.write_bytes(raw)
    change_meta(folder, lambda meta: meta.update(results_sha256=C.sha(raw)))


def test_paired_counts_recovery_blocking_and_all_changed_outputs(pair):
    rows, evidence = C.compare_pair(*pair())
    stats = C.measures(rows)
    assert stats["FAIL"] == {"original": 2, "stt": 3, "delta": 1}
    assert stats["502"] == {"original": 1, "stt": 1, "delta": 0}
    assert stats["new_failures"] == ["general.jsonl:synthetic-1", "general.jsonl:synthetic-3"]
    assert stats["recovered"] == ["general.jsonl:synthetic-2"]
    assert stats["502_transitions"] == {"False->False": 4, "True->False": 1, "False->True": 1}
    assert stats["signals"]["eligible_pairs"] == 3
    assert stats["signals"]["question_loss"] == 1
    assert stats["signals"]["question_addition"] == 0
    assert stats["signals"]["question_loss_rows"] == ["general.jsonl:synthetic-0"]
    assert rows[0]["original_signal"] == "both" and rows[0]["stt_signal"] == "request"
    assert rows[5]["output_changed"] and not rows[5]["signal_changed"]
    assert all(not rows[i]["signals_eligible"] and rows[i]["stt_signal"] == "unavailable" for i in (2, 3, 4))
    assert all(row["speech_act_changed"] == row["notes"] == "" for row in rows)
    assert evidence["original"]["metadata"]["ready"]["instance_id"] != evidence["stt"]["metadata"]["ready"]["instance_id"]


def test_cli_aggregates_datasets_and_preserves_existing_reports(pair, tmp_path):
    one, two = pair(), pair("general_es.jsonl")
    out = tmp_path / "report"
    arguments = ["--pair", *map(str, one), "--pair", *map(str, two), "--out-dir", str(out)]
    assert C.main(arguments) == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["aggregate"]["rows"] == 12
    assert summary["aggregate"]["FAIL"] == {"original": 4, "stt": 6, "delta": 2}
    assert summary["aggregate"]["502"]["stt"] == 2
    with (out / "changes.csv").open(encoding="utf-8-sig", newline="") as handle:
        exported = list(csv.DictReader(handle))
    assert len(exported) == 12 and exported[5]["output_changed"] == "True" and exported[5]["signal_changed"] == "False"
    assert "의미 변화 건수가 없습니다" in (out / "summary.md").read_text(encoding="utf-8")
    before = (out / "summary.json").read_bytes()
    assert C.main(arguments) == 1
    assert (out / "summary.json").read_bytes() == before


def test_numeric_cohort_is_separate_in_csv_dataset_and_total_statistics(pair, tmp_path):
    sources = ["Could You Inspect It At 4:15 p.m.?", "The Fee Is 8,400 Won.",
               "Can You Examine Label １２?", "Please Check Label ٣.",
               "Is This The Label?", "Where Are Three Spare Labels?"]
    first, second = pair(sources=sources), pair("general_es.jsonl", sources=sources)
    out = tmp_path / "numeric-report"
    assert C.main(["--pair", *map(str, first), "--pair", *map(str, second), "--out-dir", str(out)]) == 0
    report = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    numeric = report["datasets"]["general.jsonl"]["numeric"]
    assert numeric["rows"] == 4
    assert numeric["FAIL"] == {"original": 1, "stt": 2, "delta": 1}
    assert numeric["502"] == {"original": 1, "stt": 1, "delta": 0}
    assert numeric["new_failures"] == ["general.jsonl:synthetic-1", "general.jsonl:synthetic-3"]
    assert numeric["recovered"] == ["general.jsonl:synthetic-2"]
    assert numeric["signals"]["eligible_pairs"] == 2
    assert report["aggregate"]["numeric"]["rows"] == 8
    assert report["aggregate"]["numeric"]["FAIL"] == {"original": 2, "stt": 4, "delta": 2}
    assert report["aggregate"]["numeric"]["502"] == {"original": 2, "stt": 2, "delta": 0}
    with (out / "changes.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [r["numeric_source"] for r in rows[:6]] == ["True"] * 4 + ["False"] * 2
    assert rows[0]["original_source"] == sources[0] and rows[0]["stt_source"] == "could you inspect it at 415 p m"
    assert "숫자가 포함된 원문만 비교" in (out / "summary.md").read_text(encoding="utf-8")


def test_comparison_passes_source_locale_and_marks_korean_digit_amounts(pair):
    original, stt = pair()
    canonical = C.ROOT / "eval/general.jsonl"
    cases = [json.loads(line) for line in canonical.read_text(encoding="utf-8").splitlines()]
    cases[0].update(text="4:15에 5만 원어치 표를 보여 주세요.", source_locale="ko-KR", target_locale="en-US")
    raw = raw_rows(cases)
    canonical.write_bytes(raw)
    for folder, is_stt in ((original, False), (stt, True)):
        change_rows(folder, lambda rows: rows[0].update(case={**cases[0], "text":
            normalize_source(cases[0]["text"], "ko-KR") if is_stt else cases[0]["text"]}))
        change_meta(folder, lambda meta: meta.update(dataset_sha256=C.sha(variant_bytes(cases) if is_stt else raw)))
    rows, _ = C.compare_pair(original, stt)
    assert rows[0]["numeric_source"] is True
    assert rows[0]["stt_source"] == normalize_source(cases[0]["text"], "ko-KR")
    assert "4:15" in rows[0]["stt_source"]


@pytest.mark.parametrize("field,value", [("text", "changed source"), ("must_include", []),
    ("reference", "edited reference"), ("expect_status", "clarification_required"), ("scenario", "accident")])
def test_changed_source_or_expectations_fail_even_with_updated_result_hash(pair, field, value):
    original, stt = pair()
    change_rows(stt, lambda rows: rows[0]["case"].update({field: value}))
    with pytest.raises(ValueError, match="case source/expectations"):
        C.compare_pair(original, stt)


@pytest.mark.parametrize("edit", [lambda rows: rows.pop(), lambda rows: rows.append(copy.deepcopy(rows[0]))])
def test_missing_or_duplicate_ids_fail(pair, edit):
    original, stt = pair()
    change_rows(stt, edit)
    with pytest.raises(ValueError, match="IDs|duplicate"):
        C.compare_pair(original, stt)


@pytest.mark.parametrize("field", ["dataset_sha256", "results_sha256"])
def test_missing_or_tampered_hash_fails(pair, field):
    original, stt = pair()
    change_meta(stt, lambda meta: meta.pop(field))
    with pytest.raises(ValueError, match="hash"):
        C.compare_pair(original, stt)


def test_changed_raw_results_hash_and_stored_judgment_fail(pair):
    original, stt = pair()
    path = stt / "results.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        C.compare_pair(original, stt)
    change_rows(stt, lambda rows: rows[0].update({"pass": False}))
    with pytest.raises(ValueError, match="judgment mismatch"):
        C.compare_pair(original, stt)


@pytest.mark.parametrize("field,value", [("model_id", "other-model"), ("model_profile", "14b-q4"),
    ("artifact_hashes", {"prompting.py": "c" * 64}), ("runtime_config", {"model_profile": "8b-q4"})])
def test_model_profile_artifacts_and_runtime_changes_fail(pair, field, value):
    original, stt = pair()
    def edit(meta):
        meta["ready"][field] = value
        meta["ready_end"][field] = value
    change_meta(stt, edit)
    with pytest.raises(ValueError, match="evidence|required|mismatch"):
        C.compare_pair(original, stt)


def test_same_profile_changed_model_file_or_within_run_restart_fail(pair):
    original, stt = pair()
    def change_model(meta):
        for key in ("ready", "ready_end"):
            meta[key]["model_runtime"]["model_sha256"] = "c" * 64
    change_meta(stt, change_model)
    with pytest.raises(ValueError, match="runtime mismatch"):
        C.compare_pair(original, stt)
    change_meta(stt, lambda meta: meta["ready_end"].update(instance_id="restarted"))
    with pytest.raises(ValueError, match="ready/ready_end"):
        C.compare_pair(original, stt)


@pytest.mark.parametrize("field,value", [("chat_template_sha256", None), ("chat_template_sha256", "invalid"),
    ("chat_template_kwargs", None), ("chat_template_kwargs", "enable_thinking=False")])
def test_missing_or_invalid_template_provenance_fails_even_when_both_runs_match(pair, field, value):
    original, stt = pair()
    def edit(meta):
        for key in ("ready", "ready_end"):
            if value is None:
                meta[key]["model_runtime"].pop(field)
            else:
                meta[key]["model_runtime"][field] = value
    for folder in (original, stt):
        change_meta(folder, edit)
    with pytest.raises(ValueError, match="Verified real model runtime"):
        C.compare_pair(original, stt)


def test_canonical_directory_redirect_is_rejected_before_file_read(pair, monkeypatch):
    original, stt = pair()
    canonical = C.ROOT.resolve() / "eval/general.jsonl"
    redirect = C.ROOT / "redirected-eval/general.jsonl"
    original_resolve = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == canonical:
            return redirect
        return original_resolve(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(ValueError, match="must not redirect"):
        C.compare_pair(original, stt)


def test_mock_requires_explicit_contract_mode(pair, tmp_path):
    original, stt = pair(mock=True)
    with pytest.raises(ValueError, match="allow-mock"):
        C.compare_pair(original, stt)
    out = tmp_path / "mock-report"
    assert C.main(["--pair", str(original), str(stt), "--out-dir", str(out), "--allow-mock"]) == 0
    assert "MOCK 계약 시험 — 품질 근거 아님" in (out / "summary.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("dataset", ["test.locked.v2.jsonl", "dev.jsonl", "synthetic.jsonl"])
def test_disallowed_metadata_rejected_before_any_result_or_dataset_read(tmp_path, dataset):
    folder = tmp_path / "disallowed"
    folder.mkdir()
    (folder / "run.json").write_text(json.dumps({"dataset": dataset}))
    with pytest.raises(ValueError, match="Only canonical"):
        C.compare_pair(folder, folder)


@pytest.mark.parametrize("text,locale,label", [
    ("Could you move this?", "en-US", "both"), ("Please move this.", "en-US", "request"),
    ("Are we there?", "en-US", "question"), ("That one?", "en-US", "unmarked"),
    ("열어 주시겠어요?", "ko-KR", "both"), ("열어 주세요.", "ko-KR", "request"),
    ("이쪽이 입구인가요?", "ko-KR", "question"), ("이 표가 필요하신가요?", "ko-KR", "question"),
    ("¿Dónde está?", "es-US", "question"), ("Por favor, espere.", "es-MX", "request"),
    ("¿Puede esperar, por favor?", "es-US", "both"),
])
def test_transparent_multilingual_expression_cues(text, locale, label):
    assert C.speech_act_cues(text, locale)["label"] == label


def test_question_mark_alone_is_not_a_speech_act_change():
    a, b = (C.speech_act_cues(text, "en-US") for text in ("That one?", "That one."))
    assert a["label"] == b["label"] == "unmarked"
    assert a["question_punctuation"] != b["question_punctuation"]


def test_question_addition_and_direction_use_clean_output_marks(pair, tmp_path):
    original, stt = pair()
    change_rows(stt, lambda rows: rows[5]["body"].update(translated_text="그 표인가요?"))
    rows, _ = C.compare_pair(original, stt)
    stats = C.cohort_measures(rows)
    assert stats["signals"]["question_addition"] == 1
    assert stats["signals"]["question_addition_rows"] == ["general.jsonl:synthetic-5"]
    assert stats["directions"]["en-US->ko-KR"]["signals"]["question_loss"] == 1
    out = tmp_path / "questions"
    assert C.main(["--pair", str(original), str(stt), "--out-dir", str(out)]) == 0
    assert "| en-US->ko-KR | 3 | 1 | 1 |" in (out / "summary.md").read_text(encoding="utf-8")
