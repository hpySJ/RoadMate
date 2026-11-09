import csv
import json
import sys

import pytest

from test_distill import B, D, ROOT, fill_review, manifest, prepare_data, record, save_draft


def source_record(index, target="8b-q4", source="32b-q4"):
    row = record(index, target)
    marker = chr(97 + index // 26) + chr(97 + index % 26)
    row["text"] = f"Could you stamp this luggage label {marker}?"
    row["teacher"].update(teacher_profile=target, prompt_hashes=B.server_prompt_hashes(ROOT),
                          messages_sha256=D.digest(D.messages_for(row)))
    old = manifest(source)
    old["versions"]["translate_prompt"] = "translate-v12"
    messages = D.source_messages("en-US", "general", 1, 0)
    raw = json.dumps({"model": old["model"]["alias"], "choices": [{"finish_reason": "stop",
        "message": {"content": json.dumps({"utterances": [row["text"]]})}}]})
    row["source_origin"] = "teacher_generated"
    row["source_generation"] = {"version": D.SOURCE_VERSION, "index": 0, "messages": messages,
        "teacher": {"teacher_profile": source, "model": old["model"], "manifest_sha256": D.digest(old),
                    "prompt_hashes": {"old": "source-prompt"}, "messages_sha256": D.digest(messages),
                    "runtime": {"model_sha256": old["model"]["gguf_sha256"], "adapter_sha256": None,
                                "chat_template_kwargs": {"enable_thinking": False}},
                    "raw_response": raw, "raw_response_sha256": D.digest(raw.encode())}}
    row["source_provenance"] = {"teacher_profile": source, "manifest": old,
                                "prompt_hashes": {"old": "source-prompt"}}
    return row


def reviewed(tmp_path, rows, bad):
    draft = tmp_path / "distill.draft.jsonl"
    save_draft(draft, rows)
    D.export_sample(draft, rows, tmp_path, 42)
    review = tmp_path / "sample.review.csv"
    sample = fill_review(review)
    for row in sample[:bad]:
        row["ai_verdict"] = "나쁨"
    with review.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=D.FIELDS)
        writer.writeheader()
        writer.writerows(sample)
    evidence = tmp_path / "sample.evidence.json"
    D.record_review(draft, review, tmp_path / "sample.key.json", evidence, "synthetic-reviewer")
    return draft, evidence


@pytest.mark.parametrize("count,bad,passes", [(150, 0, True), (150, 6, True), (150, 7, False), (149, 0, False)])
def test_self_teacher_review_requires_150_and_allows_at_most_six_bad(tmp_path, count, bad, passes):
    rows = [record(i, "8b-q4") for i in range(count)]
    if not passes:
        with pytest.raises(ValueError, match="150 rows and at most 6"):
            reviewed(tmp_path, rows, bad)
        assert not (tmp_path / "sample.evidence.json").exists()
        return
    draft, path = reviewed(tmp_path, rows, bad)
    evidence = json.loads(path.read_text(encoding="utf-8"))
    assert len(D.verify_ai_review(draft, rows, evidence)) == bad
    assert evidence["review_policy"] == "self_teacher_150_max_6_bad"
    if bad:
        evidence["excluded_ids"] = []
        with pytest.raises(ValueError, match="exclusions"):
            D.verify_ai_review(draft, rows, evidence)


@pytest.mark.parametrize("profile", ["14b-q4", "32b-q4"])
def test_large_teacher_still_blocks_one_bad(tmp_path, profile):
    with pytest.raises(ValueError, match="any bad"):
        reviewed(tmp_path, [record(i, profile) for i in range(150)], 1)


@pytest.mark.parametrize("change", [None, "source_model", "source_raw", "target_model", "target_text"])
def test_source_and_target_use_separate_historical_manifests(change):
    row = source_record(0)
    if change == "source_model":
        row["source_provenance"]["manifest"]["model"]["gguf_sha256"] = "f" * 64
    elif change == "source_raw":
        row["source_generation"]["teacher"]["raw_response"] += " "
    elif change == "target_model":
        row["teacher"]["model"] = manifest("32b-q4")["model"]
    elif change == "target_text":
        row["output"]["translated_text"] = "수정된 정답"
    issues = D.verify_teacher_record(row, manifest("8b-q4"), B.server_prompt_hashes(ROOT), "8b-q4")
    assert bool(issues) == bool(change)


def test_reuse_keeps_sources_and_removes_old_answers(tmp_path):
    rows = [source_record(i) for i in range(8)]
    supplement = D.read_jsonl(ROOT / "train/sources/pool.jsonl")[:2]
    rows += supplement
    save_draft(tmp_path / "pool.jsonl", rows)
    old = rows[0]["source_provenance"]
    D.write_json(tmp_path / "teacher.manifest.json", old["manifest"])
    D.write_json(tmp_path / "summary.json", {"teacher_profile": "32b-q4", "teacher_model": old["manifest"]["model"],
        "prompt_hashes": old["prompt_hashes"], "source_rows": len(rows), "source_mix_before_translation": D.source_mix(rows),
        "source_pool_sha256": D.digest((ROOT / "train/sources/pool.jsonl").read_bytes())})
    reused, provenance = D.reuse_sources(tmp_path)
    assert [r["text"] for r in reused] == [r["text"] for r in rows]
    assert all("teacher" not in r and "output" not in r and "review_status" not in r for r in reused)
    assert provenance["manifest"]["versions"]["translate_prompt"] == "translate-v12"
    rows[-1]["text"] = "A substituted supplemental source."
    save_draft(tmp_path / "pool.jsonl", rows)
    with pytest.raises(ValueError, match="supplemental_source_provenance"):
        D.reuse_sources(tmp_path)


@pytest.mark.parametrize("supplemental", [0, 40])
def test_preparation_excludes_bad_samples_keeps_targets_and_counts_tokens(tmp_path, monkeypatch, supplemental):
    rows = [source_record(i) for i in range(160)]
    for i in range(160, 160 + supplemental):
        row = source_record(i)
        row["source_origin"] = "authored_source"
        row.pop("source_generation")
        rows.append(row)
    draft, evidence = reviewed(tmp_path, rows, 6)
    excluded = set(json.loads(evidence.read_text(encoding="utf-8"))["excluded_ids"])
    out = tmp_path / "prepared"
    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return json.dumps(messages)
        def __call__(self, text, **kwargs):
            return {"input_ids": [0] * 37}
    monkeypatch.setitem(sys.modules, "transformers", type("Module", (), {
        "AutoTokenizer": type("AutoTokenizer", (), {"from_pretrained": staticmethod(lambda *a, **kw: Tokenizer())})}))
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kw: None, raising=False)
    monkeypatch.setattr(sys, "argv", ["prepare_data.py", "--teacher", "8b-q4", "--inputs", str(draft),
        "--ai-review", str(evidence), "--out", str(out), "--stt-copies-ratio", "1", "--tokenizer", "fixture-tokenizer"])
    prepare_data.main()
    metadata = json.loads((out / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert metadata["excluded_sample_ids"] == sorted(excluded)
    prepared = [r for split in ("train", "validation", "test") for r in D.read_jsonl(out / f"{split}.jsonl")]
    extra_excluded = set(metadata["supplemental_rebalance_ids"])
    expected = len(rows) - 6 - len(extra_excluded)
    assert len(prepared) == expected * 2
    assert all(r["source_origin"] == "authored_source" for r in rows if r["id"] in extra_excluded)
    assert metadata["stt_copies"]["added_rows"] == expected
    assert metadata["token_counts"]["total"] == expected * 2 * 37
    assert sum(metadata["token_counts"]["by_split"].values()) == expected * 2 * 37
    assert metadata["source_mix"]["overall"]["teacher_share"] >= .8
    assert all(r["meta"].get("stt_copy", {}).get("parent_id") not in excluded for r in prepared)
    assert all(r["completion"] == prepare_data.build(rows[0])["completion"] for r in prepared)


@pytest.mark.parametrize("text", ["¿Cuándo cierran la oficina?", "¿Quién tiene el formulario?", "¿Quiénes vienen mañana?"])
def test_spanish_stt_copy_uses_exact_inference_preprocessing(text):
    row = source_record(0)
    row.update(text=text, source_locale="es-US", target_locale="ko-KR")
    raw_stt = prepare_data.STT.normalize_source(text, "es-US")
    cloned = {**row, "text": raw_stt, "stt_copy": {"parent_id": row["id"]}}
    built = prepare_data.build(cloned)
    assert built["prompt"] == D.P.translate_messages("es-US", "ko-KR", row["scenario"], raw_stt)
    assert json.loads(built["prompt"][-1]["content"])["utterance"] == f"¿{raw_stt}?"
    assert built["completion"] == prepare_data.build(row)["completion"]
    assert built["meta"]["group_id"] == row["group_id"]
