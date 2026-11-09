# 테스트
import json
from pathlib import Path

import pytest

from train.data_boundary import inference_overlaps

ROOT = Path(__file__).resolve().parents[1]


def test_inference_inputs_have_no_evaluation_sentence_leaks():
    assert inference_overlaps(ROOT) == []


@pytest.mark.parametrize("artifact", ["prompts/translate.txt", "prompts/demo.examples.json",
                                     "packs/glossary.json", "packs/poi.demo.json"])
def test_boundary_catches_sentence_and_slot_variants(tmp_path, artifact):
    (tmp_path / "eval").mkdir()
    p = tmp_path / artifact
    p.parent.mkdir(parents=True)
    source = "Please reserve seven towels for the upstairs lockers."
    copied = "Reserve nine towels for the upstairs lockers!"
    p.write_text(json.dumps({"sample": copied}) if p.suffix == ".json" else copied, encoding="utf-8")
    (tmp_path / "eval/test.locked.synthetic.jsonl").write_text(json.dumps({
        "id": "sealed-synthetic", "text": source, "source_locale": "en-US"}), encoding="utf-8")
    hits = inference_overlaps(tmp_path)
    assert hits == [{"dataset": "test.locked.synthetic.jsonl", "id": "sealed-synthetic",
                     "field": "text", "artifact": artifact}]
    assert source not in json.dumps(hits) and copied not in json.dumps(hits)


def test_boundary_allows_shared_vocabulary_but_catches_full_sentence(tmp_path):
    (tmp_path / "eval").mkdir()
    (tmp_path / "packs").mkdir()
    (tmp_path / "packs/glossary.json").write_text(json.dumps(["towels", "lockers"]), encoding="utf-8")
    (tmp_path / "eval/dev.jsonl").write_text(json.dumps({"id": "synthetic", "text": "Where are the lockers?",
        "source_locale": "en-US"}), encoding="utf-8")
    assert inference_overlaps(tmp_path) == []
