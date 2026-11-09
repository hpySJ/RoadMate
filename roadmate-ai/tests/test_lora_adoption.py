import copy
import hashlib
import json

import pytest

from eval import export_review as export
from train import check_adoption as gate


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
