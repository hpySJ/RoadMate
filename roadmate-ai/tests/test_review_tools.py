import csv
import hashlib
import json

import pytest

from eval import export_review as export
from eval.score_review import main as score_main, score, summary_markdown


def record(cid, text="서류를 확인해 주세요.", *, target="ko-KR", source="en-US", http=200, status="ok", passed=True):
    return {"case": {"id": cid, "task": "translate", "text": f"Synthetic source {cid}",
                     "source_locale": source, "target_locale": target},
            "http": http, "body": {"status": status, "translated_text": text,
                                   "clarification_question": "어느 서류인가요?"},
            "pass": passed, "flags": []}


def run_folder(tmp_path, name, records):
    folder = tmp_path / name
    folder.mkdir()
    raw = "\n".join(json.dumps(row, ensure_ascii=False) for row in records).encode("utf-8")
    (folder / "results.jsonl").write_bytes(raw)
    (folder / "run.json").write_text(json.dumps({"label": f"private-{name}", "dataset": "synthetic.jsonl",
                                               "model_profile": f"private-profile-{name}",
                                               "results_sha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")
    return folder


def csv_rows(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader), reader.fieldnames


def write_csv(path, rows, fields):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def side_for(key, cid, rid):
    return next(side for side, state in key["rows"][cid]["sides"].items() if state["run"] == rid)


def test_meaning_error_cannot_receive_positive_style_grade(tmp_path):
    rows, key = export.export_runs([run_folder(tmp_path, "one", [record("x")])])
    rows[0]["codex_verdict"] = json.dumps({"A": "좋음"})
    rows[0]["codex_issue"] = json.dumps({"A": ["뜻 오류"]})
    path = tmp_path / "contradictory.csv"
    write_csv(path, rows, export.FIELDS)
    with pytest.raises(ValueError, match="meaning errors"):
        export.read_review(path, key)


def test_export_blinds_each_row_and_preserves_all_output_statuses(tmp_path, monkeypatch):
    calls = []

    class Randomizer:
        def shuffle(self, order):
            if len(calls) % 2:
                order.reverse()
            calls.append(1)

    monkeypatch.setattr(export.secrets, "SystemRandom", Randomizer)
    a = [record("01", passed=False), record("02", http=502), record("03", status="clarification_required"),
         record("04", target="en-US", source="ko-KR"), record("05", target="es-US", source="ko-KR"),
         record("06", http=503), record("07", text=""), record("ignored", target="fr-FR")]
    a.append({"case": {"id": "destination", "task": "destination"}})
    b = [record("01", text="문서를 확인해 주세요."), record("02"), record("03"),
         record("04", target="en-US", source="ko-KR"), record("06"), record("07")]
    rows, key = export.export_runs([run_folder(tmp_path, "alpha", a), run_folder(tmp_path, "beta", b)])
    assert len(rows) == len(calls) == 7
    assert side_for(key, "01", "run1") == "A"
    assert side_for(key, "02", "run1") == "B"
    assert key["rows"]["01"]["sides"]["A"]["auto_pass"] is False
    status = {cid: entry["sides"][side_for(key, cid, "run1")]["status"] for cid, entry in key["rows"].items()}
    assert status == {"01": "ok", "02": "blocked_502", "03": "clarification", "04": "ok", "05": "ok", "06": "error", "07": "missing"}
    assert key["rows"]["05"]["sides"][side_for(key, "05", "run2")]["status"] == "missing"
    paths = export.write_review(rows, key, tmp_path / "review", "pair01")
    for kind in ("csv", "md"):
        text = paths[kind].read_text(encoding="utf-8-sig")
        assert "private-" not in text and str(tmp_path) not in text and "run1" not in text
        assert "502" in text and "확인 질문" in text and "출력 없음" in text
    restored_key = export.read_key(paths["key"])
    assert restored_key["runs"]["run1"]["metadata"]["model_profile"] == "private-profile-alpha"
    assert export.read_review(paths["csv"], restored_key)[0] == rows


def test_single_run_and_no_grades_never_become_good(tmp_path):
    rows, key = export.export_runs([run_folder(tmp_path, "one", [record("a"), record("b", http=502)])])
    assert all(row["B"] == "" for row in rows)
    assert all(set(entry["sides"]) == {"A"} for entry in key["rows"].values())
    result = score(rows, key)
    config = result["configurations"]["run1"]
    assert config["reviewers"]["codex"]["reviewed"] == 0
    assert config["reviewers"]["codex"]["unreviewed"] == 1
    assert config["reviewers"]["codex"]["rates"]["좋음"] is None
    assert config["final_adjudication"]["reviewed"] == 0
    assert result["agreement"]["jointly_reviewed"] == 0
    assert "N/A" in summary_markdown(result)


@pytest.mark.parametrize("field,value", [("text", "Different source"), ("source_locale", "es-US"),
                                        ("target_locale", "es-US"), ("task", "destination")])
def test_alignment_rejects_changed_source_direction_or_task(tmp_path, field, value):
    a, b = record("x"), record("x")
    b["case"][field] = value
    with pytest.raises(ValueError, match="mismatch"):
        export.export_runs([run_folder(tmp_path, "a", [a]), run_folder(tmp_path, "b", [b])])


def test_duplicate_ids_hash_change_and_locked_metadata_are_rejected(tmp_path):
    folder = run_folder(tmp_path, "duplicate", [record("x"), record("x")])
    with pytest.raises(ValueError, match="duplicate"):
        export.export_runs([folder])
    folder = run_folder(tmp_path, "changed", [record("x")])
    with (folder / "results.jsonl").open("a", encoding="utf-8") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        export.export_runs([folder])
    folder = tmp_path / "locked"
    folder.mkdir()
    (folder / "run.json").write_text('{"dataset":"test.locked.synthetic.jsonl"}', encoding="utf-8")
    # No result file: reject its metadata before trying to access locked result contents.
    with pytest.raises(ValueError, match="Locked"):
        export.export_runs([folder])


@pytest.mark.parametrize("field", export.BASE_FIELDS)
def test_workbook_identity_edits_are_rejected(tmp_path, field):
    rows, key = export.export_runs([run_folder(tmp_path, "run", [record("x")])])
    paths = export.write_review(rows, key, tmp_path / "book", "original")
    rows[0][field] += " tampered"
    write_csv(paths["csv"], rows, export.FIELDS)
    with pytest.raises(ValueError, match="edited|unknown"):
        export.read_review(paths["csv"], key)


def test_key_integrity_and_status_validation(tmp_path):
    rows, key = export.export_runs([run_folder(tmp_path, "run", [record("x")])])
    paths = export.write_review(rows, key, tmp_path / "book", "original")
    stored = json.loads(paths["key"].read_text(encoding="utf-8"))
    stored["rows"]["x"]["sides"]["A"]["status"] = "blocked_502"
    paths["key"].write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        export.read_key(paths["key"])


def test_independent_workbooks_merge_filter_and_score_without_leaking_opinions(tmp_path):
    first = run_folder(tmp_path, "first", [record("x"), record("y"), record("z", http=502)])
    second = run_folder(tmp_path, "second", [record("x", text="다른 표현이에요."), record("y"), record("z", status="clarification_required")])
    rows, key = export.export_runs([first, second])
    sx, tx = side_for(key, "x", "run1"), side_for(key, "x", "run2")
    sy, ty = side_for(key, "y", "run1"), side_for(key, "y", "run2")
    rows[0]["claude_verdict"] = export.packed({sx: "좋음", tx: "나쁨"})
    rows[0]["claude_issue"] = export.packed({tx: ["뜻 오류", "뜻 오류"]})
    rows[0]["claude_rewrite"] = export.packed({tx: "OTHER_REVIEWER_SECRET"})
    rows[0]["codex_verdict"] = export.packed({sx: "좋음", tx: "어색함"})
    rows[0]["codex_issue"] = export.packed({tx: {"types": ["직역 표현"], "note": "REVIEW_REASON"}})
    rows[0]["final"] = export.packed({tx: "나쁨"})
    rows[0]["notes"] = "USER_FINAL_SECRET"
    rows[1]["claude_verdict"] = export.packed({sy: "어색함", ty: "좋음"})
    rows[1]["codex_verdict"] = export.packed({sy: "어색함"})
    master = export.write_review(rows, key, tmp_path / "master", "pair")
    copies = {}
    for reviewer in export.REVIEWERS:
        folder = tmp_path / reviewer
        export.main(["--review", str(master["csv"]), "--key", str(master["key"]), "--reviewer", reviewer,
                     "--out-dir", str(folder), "--label", "pair"])
        copies[reviewer] = folder / "review_pair.csv"
        assert sorted(path.suffix for path in folder.iterdir()) == [".csv", ".md"]
        _, fields = csv_rows(copies[reviewer])
        assert fields == export.BASE_FIELDS + [f"{reviewer}_{kind}" for kind in ("verdict", "issue", "rewrite")]
    codex_text = copies["codex"].read_text(encoding="utf-8-sig")
    assert "OTHER_REVIEWER_SECRET" not in codex_text and "USER_FINAL_SECRET" not in codex_text
    assert "REVIEW_REASON" in codex_text and "claude" not in codex_text
    merged = export.merge_reviews([copies["claude"], copies["codex"]], key, rows)
    assert merged[0]["final"] == rows[0]["final"] and merged[0]["notes"] == rows[0]["notes"]
    disagreements = export.select_rows(merged, key, "disagreements")
    bad = export.select_rows(merged, key, "bad")
    assert disagreements == bad == merged[:1]
    filtered = export.write_review(disagreements, key, tmp_path / "filtered", "disagreements")
    restored, _ = export.read_review(filtered["csv"], export.read_key(filtered["key"]))
    assert restored == disagreements
    result = score(merged, key)
    one, two = (result["configurations"][rid] for rid in ("run1", "run2"))
    assert one["reviewers"]["claude"]["counts"] == {"좋음": 1, "어색함": 1, "나쁨": 0}
    assert two["reviewers"]["codex"]["reviewed"] == 1
    assert two["reviewers"]["codex"]["rates"]["어색함"] == 1
    assert two["reviewers"]["codex"]["unreviewed"] == 1
    assert two["reviewers"]["claude"]["issue_counts"]["뜻 오류"] == 1
    assert two["reviewers"]["codex"]["issue_counts"]["직역 표현"] == 1
    assert two["final_adjudication"]["counts"]["나쁨"] == 1
    assert one["statuses_original"]["blocked_502"] == two["statuses_original"]["clarification"] == 1
    assert result["agreement"] == {"jointly_reviewed": 3, "agreed": 2, "rate": 2 / 3}
    partial = score(restored, key)
    assert partial["coverage"] == {"workbook_rows": 1, "original_rows": 3, "subset": True}
    assert "부분집합" in summary_markdown(partial)
    assert partial["configurations"]["run1"]["statuses_original"]["blocked_502"] == 1
    assert "blocked_502" not in partial["configurations"]["run1"]["statuses_in_workbook"]


def test_merge_never_overwrites_prior_opinions_or_silently_accepts_partial_pair(tmp_path):
    rows, key = export.export_runs([run_folder(tmp_path, "run", [record("x"), record("y")])])
    paths = []
    for reviewer in export.REVIEWERS:
        fields = export.BASE_FIELDS + [f"{reviewer}_{kind}" for kind in ("verdict", "issue", "rewrite")]
        paths.append(export.write_review(rows, key, tmp_path / reviewer, "pair", fields)["csv"])
    edited, fields = csv_rows(paths[0])
    edited[0]["claude_verdict"] = '{"A":"나쁨"}'
    write_csv(paths[0], edited, fields)
    rows[0]["claude_verdict"] = '{"A":"좋음"}'
    with pytest.raises(ValueError, match="existing opinion"):
        export.merge_reviews(paths, key, rows)
    write_csv(paths[0], edited[:1], fields)
    with pytest.raises(ValueError, match="same rows"):
        export.merge_reviews(paths, key)


@pytest.mark.parametrize("cid,field,value", [
    ("ok", "codex_verdict", "좋음"),
    ("ok", "codex_verdict", '{"A":"excellent"}'),
    ("ok", "codex_verdict", '{"B":"좋음"}'),
    ("blocked", "codex_verdict", '{"A":"나쁨"}'),
    ("blocked", "codex_rewrite", '{"A":"candidate"}'),
    ("ok", "codex_issue", '{"A":["unknown type"]}'),
    ("ok", "codex_issue", '{"A":{"types":["문법"],"note":1}}'),
])
def test_invalid_judgments_fail_instead_of_being_silently_scored(tmp_path, cid, field, value):
    rows, key = export.export_runs([run_folder(tmp_path, "run", [record("ok"), record("blocked", http=502)])])
    row = next(row for row in rows if row["id"] == cid)
    row[field] = value
    paths = export.write_review(rows, key, tmp_path / "book", "pair")
    with pytest.raises(ValueError):
        export.read_review(paths["csv"], key)


def test_cli_round_trip_scores_without_modifying_input_or_overwriting_outputs(tmp_path):
    run = run_folder(tmp_path, "run", [record("x")])
    out = tmp_path / "export"
    export.main([str(run), "--label", "pair", "--out-dir", str(out)])
    csv_path, key_path = out / "review_pair.csv", out / "review_pair.key.json"
    before = {path: path.read_bytes() for path in out.iterdir()}
    score_main([str(csv_path), "--key", str(key_path), "--out-dir", str(tmp_path / "scores")])
    result = json.loads((tmp_path / "scores" / "review_summary.json").read_text(encoding="utf-8"))
    assert result["coverage"]["workbook_rows"] == 1
    with pytest.raises(SystemExit) as exc:
        export.main([str(run), "--label", "pair", "--out-dir", str(out)])
    assert exc.value.code == 2
    with pytest.raises(SystemExit):
        score_main([str(csv_path), "--key", str(key_path), "--out-dir", str(tmp_path / "scores")])
    assert all(path.read_bytes() == contents for path, contents in before.items())


def test_empty_filter_preserves_schema_and_reports_zero_coverage(tmp_path):
    rows, key = export.export_runs([run_folder(tmp_path, "run", [record("x")])])
    assert export.select_rows(rows, key, "disagreements") == []
    paths = export.write_review([], key, tmp_path / "book", "empty")
    restored, fields = export.read_review(paths["csv"], key)
    assert restored == [] and fields == export.FIELDS
    assert score(restored, key)["coverage"] == {"workbook_rows": 0, "original_rows": 1, "subset": True}


def test_paired_comparison_uses_same_fully_reviewed_rows_and_exposes_regressions(tmp_path):
    a = [record("x", passed=False), record("y"), record("z"), record("blocked", http=502, passed=False)]
    b = [record("x"), record("y", passed=False), record("z"), record("blocked")]
    for i, (left, right) in enumerate(zip(a, b)):
        left["body"]["latency_ms"], right["body"]["latency_ms"] = 100 + i * 10, 500 + i * 10
        left["wall_ms"], right["wall_ms"] = 150 + i * 10, 570 + i * 10
    rows, key = export.export_runs([run_folder(tmp_path, "a", a), run_folder(tmp_path, "b", b)])
    for row in rows:
        cid = row["id"]
        left, right = side_for(key, cid, "run1"), side_for(key, cid, "run2")
        if cid in {"x", "y"}:
            row["codex_verdict"] = export.packed({left: "나쁨" if cid == "x" else "좋음", right: "좋음" if cid == "x" else "나쁨"})
            row["codex_issue"] = export.packed({left: ["뜻 오류"] if cid == "x" else [], right: [] if cid == "x" else ["뜻 오류"]})
        elif cid == "z":
            row["codex_verdict"] = export.packed({left: "나쁨"})
    result = score(rows, key)
    paired = result["paired_comparison"]
    codex = paired["reviewers"]["codex"]
    assert paired["aligned_rows"] == 4 and paired["both_ok_rows"] == 3
    assert codex["complete_rows"] == 2
    assert codex["missing_ratings_on_both_ok"] == {"run1": 0, "run2": 1}
    assert codex["bad_counts"] == {"run1": 1, "run2": 1}
    assert codex["bad_rates"] == {"run1": .5, "run2": .5}
    assert codex["bad_rate_delta_run2_minus_run1"] == 0
    assert codex["bad_transitions"] == {"improved": 1, "worsened": 1, "both": 0, "neither": 0}
    assert codex["meaning_errors"]["complete_rows"] == 2
    assert codex["meaning_errors"]["transitions"]["worsened"] == 1
    assert paired["reviewers"]["claude"]["complete_rows"] == 0
    assert paired["reviewers"]["claude"]["bad_rates"]["run1"] is None
    assert paired["safety"]["auto_comparable_rows"] == 4
    assert paired["safety"]["auto_fail_transitions"]["improved"] == 2
    assert paired["safety"]["auto_fail_transitions"]["worsened"] == 1
    assert paired["safety"]["blocked_502_transitions"]["improved"] == 1
    assert paired["paired_latency"]["latency_ms"]["complete_rows"] == 3
    assert paired["paired_latency"]["latency_ms"]["p95_delta_run2_minus_run1"] == 400
    assert paired["paired_latency"]["wall_ms"]["p95_delta_run2_minus_run1"] == 420
    summary = summary_markdown(result)
    assert "300ms" in summary and "자동 채택 판정은 하지 않습니다" in summary
    assert "approved" not in result


def test_paired_meaning_and_latency_missing_measurements_are_not_zero_errors(tmp_path):
    rows, key = export.export_runs([run_folder(tmp_path, "a", [record("x")]), run_folder(tmp_path, "b", [record("x")])])
    row = rows[0]
    row["codex_verdict"] = export.packed({"A": "좋음", "B": "좋음"})
    row["codex_issue"] = export.packed({"A": []})
    paired = score(rows, key)["paired_comparison"]
    assert paired["reviewers"]["codex"]["meaning_errors"]["complete_rows"] == 0
    assert sum(paired["reviewers"]["codex"]["meaning_errors"]["missing_issue_ratings"].values()) == 1
    assert paired["paired_latency"]["wall_ms"]["complete_rows"] == 0
    assert paired["paired_latency"]["wall_ms"]["p95_delta_run2_minus_run1"] is None


def test_configuration_flags_are_explicit_and_missing_is_not_false(tmp_path):
    rows, key = export.export_runs([run_folder(tmp_path, "run", [record("x")])])
    metadata = key["runs"]["run1"]["metadata"]
    metadata["two_step_ko"] = False
    metadata["ready"] = {"runtime_config": {"natural_style_ko": True}}
    result = score(rows, key)
    assert result["configurations"]["run1"]["experiment_flags"] == {"two_step_ko": False, "natural_style_ko": True, "field_glossary": None}
    assert '"two_step_ko": false' in summary_markdown(result)
