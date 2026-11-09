# 테스트
import json
import sys
from pathlib import Path

import pytest

from train import train_qlora
from train.source_mix import POLICY_VERSION, check_source_mix, source_mix


def source(index, origin="teacher_generated", **kwargs):
    return {"group_id": f"group-{index}", "source_locale": "en-US", "target_locale": "ko-KR",
            "task": "translate", "scenario": "general", "text": f"Synthetic scanner number {index} is blinking.",
            "source_origin": origin, "source_generation": {"fixture": "synthetic only"}, **kwargs}


def test_source_mix_requires_teacher_majority_at_exact_boundary():
    rows = [source(i) for i in range(4)] + [source(4, "template")]
    stats = check_source_mix(rows)
    assert stats["total_rows"] == 5 and stats["teacher_rows"] == 4
    assert stats["teacher_share"] == .8 and stats["supplemental_rows"] == 1
    rows[0]["source_origin"] = "authored_source"
    with pytest.raises(ValueError, match="80%"):
        check_source_mix(rows)
    with pytest.raises(ValueError, match="80%"):
        check_source_mix([])


def test_mix_does_not_count_locale_and_number_variants_as_unique_sources():
    rows = [source(1, text="El escáner número 17 está apagado.", source_locale="es-US"),
            source(2, text="El escáner número 29 está apagado.", source_locale="es-MX")]
    assert source_mix(rows)["unique_sources"] == 1
    assert source_mix(rows)["groups"] == 2


def test_authored_destinations_are_supplemental_and_unknown_origins_do_not_count_as_teacher():
    rows = [source(0), source(1, "authored_source", task="destination"), source(2, None)]
    stats = source_mix(rows)
    assert stats["teacher_rows"] == 1 and stats["supplemental_rows"] == 2
    assert stats["by_origin"] == {"authored_source": 1, "teacher_generated": 1, "unspecified": 1}


def prepared(tmp_path, parts):
    for split, rows in parts.items():
        (tmp_path / f"{split}.jsonl").write_text(
            "".join(json.dumps({"meta": r}) + "\n" for r in rows), encoding="utf-8")
    return {"enable_thinking": False, "allow_draft": False, "source_mix_policy": POLICY_VERSION,
            "source_mix": {"overall": source_mix([r for part in parts.values() for r in part]),
                           "by_split": {split: source_mix(part) for split, part in parts.items()}}}


def test_training_rechecks_mix_in_every_nonempty_split(tmp_path):
    parts = {"train": [source(i) for i in range(4)] + [source(4, "template")],
             "validation": [source(5)], "test": []}
    manifest = prepared(tmp_path, parts)
    train_qlora.check_groups(tmp_path, manifest)
    manifest["source_mix"]["overall"]["teacher_rows"] += 1
    with pytest.raises(SystemExit, match="differs from dataset manifest"):
        train_qlora.check_groups(tmp_path, manifest)


def test_global_teacher_majority_cannot_hide_template_only_split(tmp_path):
    parts = {"train": [source(i) for i in range(9)],
             "validation": [source(10, "template")], "test": []}
    manifest = prepared(tmp_path, parts)
    assert manifest["source_mix"]["overall"]["teacher_share"] == .9
    with pytest.raises(SystemExit, match="80%"):
        train_qlora.check_groups(tmp_path, manifest)


def test_training_rejects_legacy_mix_policy_but_draft_smoke_remains_isolated(tmp_path):
    parts = {"train": [source(0, "authored_source")], "validation": [source(1, "template")], "test": []}
    manifest = prepared(tmp_path, parts)
    manifest.pop("source_mix_policy")
    with pytest.raises(SystemExit, match="policy missing"):
        train_qlora.check_groups(tmp_path, manifest)
    manifest["allow_draft"] = True
    train_qlora.check_groups(tmp_path, manifest)


def test_prepare_preserves_source_origin_and_generation_metadata():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "train"))
    from train import prepare_data
    row = source(0)
    row["output"] = {"status": "ok", "translated_text": "합성 시험용이에요.", "clarification_question": None}
    built = prepare_data.build(row)
    assert built["meta"]["source_origin"] == "teacher_generated"
    assert built["meta"]["source_generation"] == row["source_generation"]


def test_prepare_stops_before_output_when_a_group_split_is_template_only(tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "train"))
    from train import prepare_data as prepare
    rows = [source(i) for i in range(9)] + [source(10, "template")]
    for row in rows:
        row.update(source="teacher_distillation", review_status=prepare.PENDING,
                   output={"status": "ok", "translated_text": "합성 시험용이에요.", "clarification_question": None})
    draft = tmp_path / "draft.jsonl"
    draft.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}", encoding="utf-8")
    out = tmp_path / "prepared"
    monkeypatch.setattr(prepare, "protected_templates", lambda root, **kwargs: set())
    monkeypatch.setattr(prepare, "record_overlaps", lambda row, protected: [])
    monkeypatch.setattr(prepare, "verify_teacher_record", lambda *args: [])
    monkeypatch.setattr(prepare, "verify_ai_review", lambda *args: None)
    monkeypatch.setattr(prepare, "filter_issues", lambda *args: [])
    monkeypatch.setattr(prepare, "check", lambda row: [])
    monkeypatch.setattr(prepare, "duplicate_groups", lambda rows: [])
    monkeypatch.setattr(prepare, "split_groups", lambda groups, seed:
                        {group: "validation" if group == "group-10" else "train" for group in groups})
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kwargs: None, raising=False)
    monkeypatch.setattr(sys, "argv", ["prepare_data.py", "--inputs", str(draft), "--ai-review", str(evidence),
                                     "--out", str(out)])
    with pytest.raises(SystemExit, match="validation:.*80%"):
        prepare.main()
    assert not out.exists()
