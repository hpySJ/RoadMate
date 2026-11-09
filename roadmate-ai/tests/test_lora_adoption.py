import copy
import hashlib
import json
from pathlib import Path

import pytest

from eval import export_review as export
from train import check_adoption as gate
from eval import compare_stt
from eval.stt_format import normalize_source, variant_bytes


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def stage(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "ROOT", tmp_path)
    monkeypatch.setattr(gate, "load_manifest", lambda path: {"model": {"gguf_sha256": "model-hash"}})
    out = tmp_path / "stage"
    out.mkdir()
    (tmp_path / "eval").mkdir()
    adapter = out / "adapter.gguf"
    adapter.write_bytes(b"synthetic adapter, never train with this")
    adapter_sha = gate.sha(adapter)
    sidecar = adapter.with_suffix(".gguf.json")
    sidecar.write_text(json.dumps({"sha256": adapter_sha, "adoption_status": "pending"}))
    pairs = []
    for dataset in sorted(gate.DATASETS):
        cases = [{"id": "synthetic-translation", "task": "translate", "source_locale": "en-US",
                  "target_locale": "ko-KR", "text": "Synthetic test source, not training data."},
                 {"id": "synthetic-destination", "task": "destination", "source_locale": "en-US",
                  "text": "Synthetic routing test.", "expect_status": "ok"}]
        raw = "".join(json.dumps(c) + "\n" for c in cases).encode()
        (tmp_path / "eval" / f"{dataset}.jsonl").write_bytes(raw)
        folders = []
        for i in range(2):
            folder = out / dataset / str(i)
            folder.mkdir(parents=True)
            ready = {"backend": "llama", "ready": True, "instance_id": f"instance{i}",
                     "model_profile": "8b-q4", "model_id": "synthetic-8b",
                     "runtime_config": {"model_profile": "8b-q4"}, "artifact_hashes": {"engine.py": "code-hash"},
                     "model_runtime": {"model_sha256": "model-hash", "adapter_sha256": adapter_sha if i else None,
                                       "process": {"pid": i+1}, "build": "synthetic", "default_parameters": {"ctx": 4096}}}
            records = [{"case": c, "http": 200, "pass": True, "flags": [], "wall_ms": 100+i*20,
                        "body": {"status": "ok", "translated_text": "합성 시험용 출력이에요."}} for c in cases]
            results = "".join(json.dumps(r) + "\n" for r in records).encode()
            (folder / "results.jsonl").write_bytes(results)
            meta = {"dataset": f"{dataset}.jsonl", "dataset_sha256": digest(raw),
                    "results_sha256": digest(results), "model_profile": "8b-q4", "model_profile_consistent": True,
                    "ready": ready, "ready_end": copy.deepcopy(ready)}
            (folder / "run.json").write_text(json.dumps(meta))
            folders.append(folder)
        rows, key = export.export_runs(folders)
        for row in rows:
            ratings = {side: "어색함" if state["run"] == "run1" else "좋음"
                       for side, state in key["rows"][row["id"]]["sides"].items()}
            for reviewer in export.REVIEWERS:
                row[f"{reviewer}_verdict"] = json.dumps(ratings, ensure_ascii=False)
        label = "pair-" + dataset
        paths = export.write_review(rows, key, out / "private", label)
        pairs.append({"dataset": dataset, "label": label, "baseline": str(folders[0]), "candidate": str(folders[1]),
                      "key": str(paths["key"]), "review": str(paths["csv"])})
    manifest = {"stage": "train-eval", "adapter": str(adapter), "comparisons": pairs}
    (out / "comparison_manifest.json").write_text(json.dumps(manifest))
    return out, manifest, sidecar


def rewrite_review(pair, change):
    key = export.read_key(pair["key"])
    rows, _ = export.read_review(pair["review"], key)
    change(rows, key)
    import csv
    with open(pair["review"], "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, export.FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_completed_paired_improvement_passes_without_installing(stage):
    out, _, sidecar = stage
    original = sidecar.read_bytes()
    result = gate.decide(out)
    assert result["status"] == "passed"
    assert result["quality"]["baseline"]["어색함"] == 3
    assert result["quality"]["candidate"]["좋음"] == 3
    assert sidecar.read_bytes() == original


def test_missing_independent_verdict_is_pending(stage):
    out, manifest, _ = stage
    rewrite_review(manifest["comparisons"][0], lambda rows, key: rows[0].update(codex_verdict=""))
    assert gate.decide(out)["status"] == "pending"


def test_disagreement_requires_final_decision(stage):
    out, manifest, _ = stage
    pair = manifest["comparisons"][0]
    def disagree(rows, key):
        rows[0]["codex_verdict"] = json.dumps({side: "좋음" for side in key["rows"][rows[0]["id"]]["sides"]}, ensure_ascii=False)
    rewrite_review(pair, disagree)
    assert gate.decide(out)["status"] == "pending"
    rewrite_review(pair, lambda rows, key: rows[0].update(final=rows[0]["claude_verdict"]))
    assert gate.decide(out)["status"] == "passed"


def test_no_quality_improvement_disables_adapter(stage):
    out, manifest, sidecar = stage
    def equal(rows, key):
        for row in rows:
            for reviewer in export.REVIEWERS:
                row[f"{reviewer}_verdict"] = json.dumps({side: "좋음" for side in key["rows"][row["id"]]["sides"]}, ensure_ascii=False)
    for pair in manifest["comparisons"]:
        rewrite_review(pair, equal)
    assert gate.decide(out)["status"] == "rejected"
    assert json.loads(sidecar.read_text(encoding="utf-8"))["adoption_status"] == "rejected"


def test_adapter_tamper_is_rejected_as_invalid_evidence(stage):
    out, manifest, _ = stage
    from pathlib import Path
    Path(manifest["adapter"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="sidecar"):
        gate.decide(out)


def test_cannot_omit_dataset(stage):
    out, manifest, _ = stage
    manifest["comparisons"].pop()
    (out / "comparison_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="three development"):
        gate.decide(out)


def test_changed_results_or_incomplete_review_cannot_pass(stage):
    out, manifest, _ = stage
    from pathlib import Path
    path = Path(manifest["comparisons"][0]["candidate"]) / "results.jsonl"
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        gate.decide(out)


@pytest.mark.parametrize("candidate_ms, expected", [(400, "passed"), (401, "rejected")])
def test_p95_limit(stage, candidate_ms, expected):
    out, manifest, _ = stage
    from pathlib import Path
    for pair in manifest["comparisons"]:
        folder = Path(pair["candidate"])
        records = [json.loads(x) for x in (folder / "results.jsonl").read_text(encoding="utf-8").splitlines()]
        for r in records:
            r["wall_ms"] = candidate_ms
        raw = "".join(json.dumps(r) + "\n" for r in records).encode()
        (folder / "results.jsonl").write_bytes(raw)
        meta = json.loads((folder / "run.json").read_text(encoding="utf-8"))
        meta["results_sha256"] = digest(raw)
        (folder / "run.json").write_text(json.dumps(meta))
        key = json.loads(Path(pair["key"]).read_text(encoding="utf-8"))
        key["runs"]["run2"].update(metadata=meta, results_sha256=digest(raw))
        key["integrity_sha256"] = export.key_hash(key)
        Path(pair["key"]).write_text(json.dumps(key))
    assert gate.decide(out)["status"] == expected


@pytest.mark.parametrize("task", ["translate", "destination"])
def test_safety_regression_rejects_even_before_quality_review(stage, task):
    out, manifest, sidecar = stage
    from pathlib import Path
    pair = manifest["comparisons"][0]
    folder = Path(pair["candidate"])
    records = [json.loads(x) for x in (folder / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    changed = next(r for r in records if r["case"]["task"] == task)
    changed.update(http=502, body={"error_code": "synthetic_block"}, **{"pass": False})
    raw = "".join(json.dumps(r) + "\n" for r in records).encode()
    (folder / "results.jsonl").write_bytes(raw)
    meta = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    meta["results_sha256"] = digest(raw)
    (folder / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    rows, key = export.export_runs([pair["baseline"], pair["candidate"]])
    new = export.write_review(rows, key, out / "updated-private", pair["label"])
    pair.update(review=str(new["csv"]), key=str(new["key"]))
    (out / "comparison_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = gate.decide(out)
    assert result["status"] == "rejected"
    assert any(":502:" in reason for reason in result["failures"])
    assert any(":FAIL:" in reason for reason in result["failures"])
    if task == "destination":
        assert any(":destination_FAIL:" in reason for reason in result["failures"])
    assert json.loads(sidecar.read_text(encoding="utf-8"))["adoption_status"] == "rejected"


def write_records(folder, records):
    for row in records:
        row["pass"], row["flags"] = gate.judge(row["case"], row["http"], row["body"])
    raw = "".join(json.dumps(row) + "\n" for row in records).encode()
    (folder / "results.jsonl").write_bytes(raw)
    meta = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    meta["results_sha256"] = digest(raw)
    (folder / "run.json").write_text(json.dumps(meta), encoding="utf-8")


def refresh_review(pair, out):
    rows, key = export.export_runs([pair["baseline"], pair["candidate"]])
    for row in rows:
        for reviewer in export.REVIEWERS:
            row[f"{reviewer}_verdict"] = json.dumps({side: "좋음" for side, state in key["rows"][row["id"]]["sides"].items()
                                                   if state["status"] == "ok"}, ensure_ascii=False)
    paths = export.write_review(rows, key, out, pair["label"])
    pair.update(key=str(paths["key"]), review=str(paths["csv"]))


@pytest.fixture
def self_stage(stage, monkeypatch):
    out, manifest, sidecar = stage
    monkeypatch.setattr(compare_stt, "ROOT", gate.ROOT)
    monkeypatch.setattr(gate, "load_manifest", lambda path: {"model": {"gguf_sha256": "b" * 64}})
    manifest.update(teacher_profile="8b-q4", stt_comparisons=[])
    for pair in manifest["comparisons"]:
        canonical = gate.ROOT / "eval" / f"{pair['dataset']}.jsonl"
        cases = [json.loads(line) for line in canonical.read_text().splitlines()]
        cases[0].update(source_locale="es-MX", text="¿Puede mostrarme la entrada?")
        cases.append({"id": "synthetic-statement", "task": "translate", "source_locale": "en-US",
                      "target_locale": "ko-KR", "text": "This is a synthetic statement."})
        raw = "".join(json.dumps(case) + "\n" for case in cases).encode()
        canonical.write_bytes(raw)
        stt_pair = {"dataset": pair["dataset"]}
        for side in ("baseline", "candidate"):
            folder = Path(pair[side])
            meta = json.loads((folder / "run.json").read_text())
            ready = meta["ready"]
            ready.update(engine_bundle="synthetic-bundle", versions={"translate_prompt": "translate-v11"},
                         artifact_hashes={"engine.py": "a" * 64})
            ready["runtime_config"].update(model_id=ready["model_id"], model_timeout_s=8, max_text_chars=400)
            ready["model_runtime"].update(model_sha256="b" * 64, context=4096, slots=1,
                                          chat_template_sha256="d" * 64, chat_template_kwargs={"enable_thinking": False})
            meta.update(dataset_sha256=digest(raw), ready_end=copy.deepcopy(ready))
            (folder / "run.json").write_text(json.dumps(meta))
            records = [{"case": case, "http": 200, "wall_ms": 100, "body": {
                "status": "ok", "translated_text": "입구를 보여 주시겠어요?" if index == 0 else "합성 문장입니다.",
                "model_id": ready["model_id"]}} for index, case in enumerate(cases)]
            write_records(folder, records)
            stt_folder = folder.parent / (side + "-stt")
            stt_folder.mkdir()
            meta.update(dataset_sha256=digest(variant_bytes(cases)))
            (stt_folder / "run.json").write_text(json.dumps(meta))
            stt_records = copy.deepcopy(records)
            for row in stt_records:
                row["case"]["text"] = normalize_source(row["case"]["text"], row["case"]["source_locale"])
            if side == "baseline":
                stt_records[0]["body"]["translated_text"] = "입구를 보여 주세요."
            write_records(stt_folder, stt_records)
            stt_pair[side] = str(stt_folder)
        refresh_review(pair, out / "self-review")
        manifest["stt_comparisons"].append(stt_pair)
    (out / "comparison_manifest.json").write_text(json.dumps(manifest))
    return out, manifest, sidecar


def edit_run(folder, edit):
    folder = Path(folder)
    rows = [json.loads(line) for line in (folder / "results.jsonl").read_text(encoding="utf-8").splitlines()]
    edit(rows)
    write_records(folder, rows)


def test_self_teacher_equal_clean_quality_and_reduced_stt_loss_pass(self_stage):
    out, _, sidecar = self_stage
    before = sidecar.read_bytes()
    result = gate.decide(out)
    assert result["status"] == "passed"
    assert result["stt"]["es_to_ko"]["baseline"]["signals"]["question_loss"] == 3
    assert result["stt"]["es_to_ko"]["candidate"]["signals"]["question_loss"] == 0
    assert result["criteria"]["historical_es_to_ko_question_loss"] == 7
    assert sidecar.read_bytes() == before


def test_self_teacher_requires_all_stt_datasets(self_stage):
    out, manifest, _ = self_stage
    manifest["stt_comparisons"].pop()
    (out / "comparison_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="three development datasets"):
        gate.decide(out)


def test_self_teacher_profile_cannot_be_relabelled_to_skip_stt_gate(self_stage):
    out, manifest, sidecar = self_stage
    record = json.loads(sidecar.read_text())
    record["teacher_profile"] = "8b-q4"
    sidecar.write_text(json.dumps(record))
    manifest["teacher_profile"] = "32b-q4"
    (out / "comparison_manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="differs from adapter training"):
        gate.decide(out)


@pytest.mark.parametrize("failure", ["loss", "addition", "blocked", "clarification"])
def test_self_teacher_stt_failures_cannot_pass(self_stage, failure):
    out, manifest, _ = self_stage
    for pair in manifest["stt_comparisons"]:
        def change(rows):
            if failure == "loss":
                rows[0]["body"]["translated_text"] = "입구를 보여 주세요."
            elif failure == "addition":
                rows[2]["body"]["translated_text"] = "합성 문장인가요?"
            elif failure == "blocked":
                rows[0].update(http=502, body={"error_code": "synthetic"})
            else:
                rows[0]["body"].update(status="clarification_required", clarification_question="어느 입구인가요?")
        edit_run(pair["candidate"], change)
    result = gate.decide(out)
    assert result["status"] == "rejected"
    expected = {"loss": "question_loss_not_reduced", "addition": "question_addition_must_be_zero",
                "blocked": "STT_eligible_pair_lost", "clarification": "STT_eligible_pair_lost"}[failure]
    assert any(expected in reason for reason in result["failures"])


def test_self_teacher_cannot_balance_new_bad_with_recovery(self_stage):
    out, manifest, _ = self_stage
    def swap(rows, key):
        for index, row in enumerate(rows):
            for reviewer in export.REVIEWERS:
                row[f"{reviewer}_verdict"] = json.dumps({side: "나쁨" if state["run"] == ("run1" if index == 0 else "run2") else "좋음"
                    for side, state in key["rows"][row["id"]]["sides"].items()}, ensure_ascii=False)
    rewrite_review(manifest["comparisons"][0], swap)
    result = gate.decide(out)
    assert result["quality"]["baseline"]["나쁨"] == result["quality"]["candidate"]["나쁨"] == 1
    assert result["status"] == "rejected" and any(":new_bad:" in reason for reason in result["failures"])


def test_self_teacher_rejects_changed_stt_cases(self_stage):
    out, manifest, _ = self_stage
    edit_run(manifest["stt_comparisons"][0]["candidate"], lambda rows: rows[0]["case"].update(text="edited input"))
    with pytest.raises(ValueError, match="case source/expectations"):
        gate.decide(out)


def test_self_teacher_cannot_balance_new_failure_with_recovery(self_stage):
    out, manifest, _ = self_stage
    pair = manifest["comparisons"][0]
    edit_run(pair["baseline"], lambda rows: rows[0].update(http=502, body={"error_code": "synthetic"}))
    edit_run(pair["candidate"], lambda rows: rows[2].update(http=502, body={"error_code": "synthetic"}))
    refresh_review(pair, out / "failure-review")
    (out / "comparison_manifest.json").write_text(json.dumps(manifest))
    result = gate.decide(out)
    first = result["automatic"][0]
    assert first["baseline"]["FAIL"] == first["candidate"]["FAIL"] == 1
    assert first["baseline"]["502"] == first["candidate"]["502"] == 1
    assert result["status"] == "rejected"
    assert any(":FAIL:new_row:synthetic-statement" in reason for reason in result["failures"])
    assert any(":502:new_row:synthetic-statement" in reason for reason in result["failures"])
