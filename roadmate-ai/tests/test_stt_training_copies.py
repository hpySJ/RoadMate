"""STT augmentation contracts use synthetic records, never evaluation files."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))

from train import prepare_data as D  # noqa: E402
from eval.stt_format import STT_FORMAT_VERSION, normalize_source  # noqa: E402
from train.source_mix import check_source_mix  # noqa: E402


def row(index, origin="teacher_generated", **kwargs):
    marker = chr(97 + index // 26) + chr(97 + index % 26)
    return {"id": f"synthetic-{index}", "group_id": f"group-{index}", "task": "translate",
            "source_locale": "en-US", "target_locale": "ko-KR", "scenario": "general",
            "text": f"Please label the synthetic {marker} drawer.", "source_origin": origin,
            "source_generation": {"fixture": f"raw-source-{index}"},
            "output": {"status": "ok", "translated_text": "합성 시험용 표지예요.", "clarification_question": None},
            **kwargs}


@pytest.fixture
def selection_only(monkeypatch):
    # These rows exercise selection/provenance, not translation quality.
    monkeypatch.setattr(D, "check", lambda record: [])
    monkeypatch.setattr(D, "record_overlaps", lambda record, blocked: [])


def copies(records):
    return [record for record in records if record.get("stt_copy")]


def test_ratio_is_stratified_by_split_and_origin_and_recorded(selection_only):
    originals = [row(i, "teacher_generated" if i % 10 < 8 else "authored_source") for i in range(30)]
    splits = ("train", "validation", "test")
    assign = {record["group_id"]: splits[i // 10] for i, record in enumerate(originals)}
    result, summary = D.augment_stt(originals, .5, 42, set(), assign)
    assert result[:len(originals)] == originals
    assert len(copies(result)) == 15
    assert summary["requested_ratio"] == .5 and summary["seed"] == 42
    assert summary["format_version"] == STT_FORMAT_VERSION
    assert summary["transform_sha256"] == hashlib.sha256((ROOT / "eval/stt_format.py").read_bytes()).hexdigest()
    assert summary["original_translation_rows"] == 30
    assert summary["selected_rows"] == summary["added_rows"] == 15
    assert summary["realized_ratio"] == .5
    assert summary["by_split"] == {split: 5 for split in splits}
    for split in splits:
        assert check_source_mix([r for r in result if assign[r["group_id"]] == split])["teacher_share"] == .8


def test_selection_is_order_independent_and_seeded(selection_only):
    originals = [row(i) for i in range(24)]
    assign = {r["group_id"]: "train" for r in originals}
    def selected(records, seed):
        result, _ = D.augment_stt(records, .5, seed, set(), assign)
        return {r["stt_copy"]["parent_id"] for r in copies(result)}
    assert selected(originals, 42) == selected(list(reversed(originals)), 42)
    assert len(selected(originals, 42)) == 12
    assert selected(originals, 42) != selected(originals, 43)


def test_copies_preserve_labels_provenance_and_group_assignment(selection_only):
    originals = [row(i) for i in range(6)]
    before = copy.deepcopy(originals)
    assign = D.split_groups([r["group_id"] for r in originals], 42)
    result, _ = D.augment_stt(originals, 1, 42, set(), assign)
    assert originals == before
    parents = {r["id"]: r for r in originals}
    for cloned in copies(result):
        parent = parents[cloned["stt_copy"]["parent_id"]]
        assert cloned["text"] == normalize_source(parent["text"], parent["source_locale"])
        assert cloned["output"] == parent["output"]
        assert cloned["group_id"] == parent["group_id"]
        assert cloned["source_origin"] == parent["source_origin"]
        assert cloned["source_generation"] == parent["source_generation"]
        built, original = D.build(cloned), D.build(parent)
        assert built["completion"] == original["completion"]
        assert "stt_copy" not in original["meta"]
        assert built["meta"]["stt_copy"] == {"parent_id": parent["id"], "parent_text": parent["text"],
                                               "format_version": STT_FORMAT_VERSION}
        assert assign[built["meta"]["group_id"]] == assign[original["meta"]["group_id"]]


def test_destination_rows_are_never_copied_and_zero_disables(selection_only):
    originals = [row(0), row(1, task="destination", target_locale=None)]
    assign = {r["group_id"]: "train" for r in originals}
    result, summary = D.augment_stt(originals, 1, 42, set(), assign)
    assert summary["original_translation_rows"] == summary["added_rows"] == 1
    assert all(r["task"] == "translate" for r in copies(result))
    disabled, summary = D.augment_stt(originals, 0, 42, set(), assign)
    assert disabled == originals and not copies(disabled)
    assert summary["selected_rows"] == summary["added_rows"] == summary["realized_ratio"] == 0


def test_unchanged_or_invalid_copies_are_skipped(selection_only, monkeypatch):
    originals = [row(0), row(1), row(2), row(3)]
    originals[0]["text"] = normalize_source(originals[0]["text"], "en-US")
    monkeypatch.setattr(D, "check", lambda r: ["synthetic validation failure"] if r["id"] == originals[1]["id"] else [])
    monkeypatch.setattr(D, "record_overlaps", lambda r, blocked: ["boundary_source"] if "ac drawer" in r["text"] else [])
    result, summary = D.augment_stt(originals, 1, 42, {"synthetic-protected"}, {r["group_id"]: "train" for r in originals})
    assert summary["selected_rows"] == 4 and summary["added_rows"] == 1
    assert summary["skipped"]["unchanged"] == summary["skipped"]["validation"] == summary["skipped"]["boundary"] == 1
    assert copies(result)[0]["stt_copy"]["parent_id"] == originals[3]["id"]


def test_copy_cannot_collide_with_another_original_group(selection_only):
    original = row(0, text="Please mark 64,700 won on the blue form.")
    other = row(1, text=normalize_source(original["text"], "en-US"))
    assert D.fingerprint(original["text"], "en-US") != D.fingerprint(other["text"], "en-US")
    result, summary = D.augment_stt([original, other], 1, 42, set(), {"group-0": "train", "group-1": "validation"})
    assert not copies(result)
    assert summary["skipped"]["duplicate_group"] == 1


def test_rejected_teacher_copies_cannot_lower_teacher_share(selection_only):
    originals = [row(i, "teacher_generated" if i < 8 else "authored_source") for i in range(10)]
    for original in originals[:4]:
        original["text"] = normalize_source(original["text"], "en-US")
    result, summary = D.augment_stt(originals, 1, 42, set(), {r["group_id"]: "train" for r in originals})
    assert summary["added_rows"] == 5
    assert summary["skipped"]["unchanged"] == 4 and summary["skipped"]["source_mix"] == 1
    assert check_source_mix(result)["teacher_share"] == .8


def test_observed_time_and_money_formats_keep_teacher_completion():
    original = row(0, text="Please record 9:25 a.m. and 64,700 won on this receipt.",
                   output={"status": "ok", "translated_text": "이 영수증에 오전 9시 25분과 6만 4천700원을 적어 주세요.",
                           "clarification_question": None})
    assert D.check(original) == []
    result, summary = D.augment_stt([original], 1, 42, set(), {original["group_id"]: "train"})
    assert summary["added_rows"] == 1
    cloned, = copies(result)
    assert "925 a m" in cloned["text"] and "64 thousand 700 won" in cloned["text"]
    assert D.check(cloned) == []
    assert D.build(cloned)["completion"] == D.build(original)["completion"]


@pytest.mark.parametrize("ratio", [-.1, 1.1, float("nan"), float("inf"), float("-inf")])
def test_helper_rejects_invalid_ratios(ratio):
    with pytest.raises(ValueError):
        D.augment_stt([], ratio, 42, set(), {})


@pytest.mark.parametrize("option,added", [(None, 4), ("0", 0)])
def test_preparation_writes_stt_counts_and_preserves_splits(selection_only, monkeypatch, tmp_path, option, added):
    originals = [row(i) for i in range(8)]
    draft = tmp_path / "draft.jsonl"
    draft.write_text("".join(json.dumps(r) + "\n" for r in originals), encoding="utf-8")
    (tmp_path / "train").mkdir()
    (tmp_path / "train/training_config.json").write_text('{"max_length":4096}', encoding="utf-8")
    out = tmp_path / "prepared"
    assign = {r["group_id"]: "train" if i < 4 else "validation" for i, r in enumerate(originals)}
    boundary_calls = []
    monkeypatch.setattr(D, "ROOT", tmp_path)
    monkeypatch.setattr(D, "protected_templates", lambda root, **kwargs: boundary_calls.append(kwargs) or set())
    monkeypatch.setattr(D, "server_prompt_hashes", lambda root: {})
    monkeypatch.setattr(D, "split_groups", lambda groups, seed: assign)
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kwargs: None, raising=False)
    args = ["prepare_data.py", "--inputs", str(draft), "--out", str(out), "--allow-draft"]
    if option is not None:
        args += ["--stt-copies-ratio", option]
    monkeypatch.setattr(sys, "argv", args)
    D.main()
    manifest = json.loads((out / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert boundary_calls == [{"include_locked": False, "include_stt": True}]
    assert manifest["stt_copies"]["added_rows"] == added
    assert manifest["stt_copies"]["realized_ratio"] == added / len(originals)
    assert manifest["counts"] == {"train": 4 + added // 2, "validation": 4 + added // 2, "test": 0}
    saved = []
    for split in ("train", "validation", "test"):
        rows = D.load_jsonl(out / f"{split}.jsonl")
        assert all(assign[r["meta"]["group_id"]] == split for r in rows)
        saved.extend(rows)
    assert sum("stt_copy" in r["meta"] for r in saved) == added
    assert manifest["source_mix"]["overall"]["teacher_rows"] == len(saved)
