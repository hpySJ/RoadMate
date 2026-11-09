# 테스트
import asyncio
import copy
import json
import random
import string
from collections import Counter

import pytest

from train import distill as D
from train import prepare_data


def test_source_prompt_rotates_one_topic_and_preserves_full_history():
    history = [f"Synthetic accepted utterance {index}." for index in range(79)]
    for scenario in D.SCENARIOS:
        for batch in range(2 * len(D.SOURCE_TOPICS[scenario])):
            messages = D.source_messages("ko-KR", scenario, 1, batch, history)
            spec = json.loads(messages[-1]["content"])
            assert "topics" not in spec
            assert spec["topic"] == D.SOURCE_TOPICS[scenario][batch % len(D.SOURCE_TOPICS[scenario])]
            assert spec["turn_form"] == D.SOURCE_FORMS[batch % len(D.SOURCE_FORMS)]
            assert spec["focus"] == D.SOURCE_FOCUS[(batch + D.SCENARIOS.index(scenario)) % len(D.SOURCE_FOCUS)]
            assert spec["already_used"] == history
    assert json.loads(D.source_messages("en-US", "general", 1, 0)[-1]["content"])["already_used"] == []


def test_batches_cover_directions_without_replicating_templates(monkeypatch, tmp_path):
    calls = []
    class Teacher:
        async def call(self, messages, schema, max_tokens, sampling):
            spec = json.loads(messages[-1]["content"])
            calls.append((spec, schema, max_tokens, sampling))
            rng = random.Random(sampling["seed"])
            texts = ["Synthetic " + "".join(rng.choices(string.ascii_lowercase, k=40)) + " test fixture."
                     for _ in range(spec["count"])]
            return {"utterances": texts}, {"synthetic": True}
    monkeypatch.setattr(D, "looks_like_other_language", lambda *args: False)
    rows = asyncio.run(D.source_augmentation(Teacher(), tmp_path, set(), 11))
    assert len(rows) == 330
    assert max(spec["count"] for spec, *_ in calls) == 10
    assert len({sampling["seed"] for *_, sampling in calls}) == len(calls)
    assert len({spec["turn_form"] for spec, *_ in calls}) > 1
    counts = Counter((r["source_locale"], r["target_locale"], r["scenario"]) for r in rows)
    assert set(counts) == {(src, tgt, sc) for src, tgt in D.DIRECTIONS for sc in D.SCENARIOS}
    assert set(counts.values()) == {11}
    groups = {r["group_id"] for r in rows}
    assert len(groups) == 40
    assignments = prepare_data.split_groups(list(groups), 42)
    assert "train" in {assignments[r["group_id"]] for r in rows if r["scenario"] == "rental_counter"}
    for text in {r["text"] for r in rows if r["source_locale"] == "ko-KR"}:
        variants = [r for r in rows if r["text"] == text]
        assert len(variants) == 3 and len({r["group_id"] for r in variants}) == 1
    logs = [json.loads(s) for s in (tmp_path / "source_generation.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(logs) == len(calls) and all(log["sampling"]["seed"] for log in logs)
    summary = json.loads((tmp_path / "source_summary.json").read_text(encoding="utf-8"))
    assert summary["unique_sources"] == 220 and summary["translation_rows"] == 330
    for spec, *_ in calls:
        originals = list(dict.fromkeys(r["text"] for r in rows
            if r["source_locale"] == spec["locale"] and r["scenario"] == spec["scenario"]))
        assert spec["already_used"] == originals[:10 * spec["independent_batch"]]
        topics = D.SOURCE_TOPICS[spec["scenario"]]
        assert summary["accepted_by_topic"][spec["locale"]][spec["scenario"]] == {
            topic: (10 if index == 0 else 1 if index == 1 else 0) for index, topic in enumerate(topics)}
    assert [json.loads(log["messages"][-1]["content"]) for log in logs] == [spec for spec, *_ in calls]
    for record in rows:
        messages = record["source_generation"]["messages"]
        assert record["text"] not in json.loads(messages[-1]["content"])["already_used"]


def test_numeric_and_locale_variants_are_removed_before_expansion(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "SOURCE_LOCALES", ("es-US", "es-MX"))
    monkeypatch.setattr(D, "SCENARIOS", ("general",))
    replies = iter([
        ["El recibo muestra 17 pesos.", "El recibo muestra 29 pesos."],
        ["La puerta del despacho está cerrada."],
        ["El recibo muestra 42 pesos.", "¿Puede revisar el mapa del barrio?"],
        ["No encuentro mi chaqueta azul."]])
    calls = []
    class Teacher:
        async def call(self, messages, *args):
            calls.append(json.loads(messages[-1]["content"]))
            return {"utterances": next(replies)}, {"synthetic": True}
    rows = asyncio.run(D.source_augmentation(Teacher(), tmp_path, set(), 2))
    assert len(rows) == 4
    assert sum("recibo" in r["text"] for r in rows) == 1
    summary = json.loads((tmp_path / "source_summary.json").read_text(encoding="utf-8"))
    assert summary["rejections"]["duplicate_generated_source"] == 2
    assert [spec["already_used"] for spec in calls] == [[], ["El recibo muestra 17 pesos."],
                                                       [], ["¿Puede revisar el mapa del barrio?"]]
    assert sum(sum(topics.values()) for scenarios in summary["accepted_by_topic"].values()
               for topics in scenarios.values()) == 4


def test_invalid_and_boundary_rejections_never_enter_history(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "SOURCE_LOCALES", ("en-US",))
    monkeypatch.setattr(D, "SCENARIOS", ("general",))
    protected = {D.fingerprint("Synthetic protected wording.", "en-US")}
    calls = []
    class Teacher:
        async def call(self, messages, *args):
            calls.append(json.loads(messages[-1]["content"]))
            reply = (["", "Synthetic protected wording.", "The gate control is stuck."] if len(calls) == 1
                     else ["Could someone carry my bag?", "An orange umbrella is beside the desk."])
            return {"utterances": reply}, {"synthetic": True}
    rows = asyncio.run(D.source_augmentation(Teacher(), tmp_path, protected, 3))
    assert len(rows) == 3
    assert calls[1]["already_used"] == ["The gate control is stuck."]
    summary = json.loads((tmp_path / "source_summary.json").read_text(encoding="utf-8"))
    assert summary["rejections"] == {"invalid_source": 1, "boundary_source": 1}
    assert summary["accepted_by_topic"]["en-US"]["general"]["road signs"] == 1
    assert summary["accepted_by_topic"]["en-US"]["general"]["junctions"] == 2


def test_quota_exhaustion_does_not_fill_with_templates(monkeypatch, tmp_path):
    monkeypatch.setattr(D, "SOURCE_LOCALES", ("en-US",))
    monkeypatch.setattr(D, "SCENARIOS", ("general",))
    calls = []
    class Teacher:
        async def call(self, messages, *args):
            spec = json.loads(messages[-1]["content"])
            calls.append(spec)
            return {"utterances": ["The scanner is broken."] * spec["count"]}, {"synthetic": True}
    with pytest.raises(ValueError, match="insufficient diverse sources"):
        asyncio.run(D.source_augmentation(Teacher(), tmp_path, set(), 2))
    assert len(calls) == 2
    assert (tmp_path / "source_generation.jsonl").is_file()
    assert not (tmp_path / "source_summary.json").exists()


def row(index, origin="teacher_generated", task="translate"):
    return {"id": str(index), "text": f"Synthetic token {index}", "source_locale": "en-US", "task": task,
            "source_origin": origin, "group_id": f"batch-{index // 10}"}


def test_safety_filter_cannot_leave_a_template_majority():
    rows = [row(i) for i in range(8)] + [row(i+8, "authored_source") for i in range(40)]
    rows[-1]["task"] = "destination"
    selected = D.balanced_sources(rows, 42)
    assert len(selected) == 10 and D.source_mix(selected)["teacher_share"] == .8
    assert any(r["task"] == "destination" for r in selected)
    with pytest.raises(ValueError, match="80%"):
        D.balanced_sources(rows[8:], 42)


def source_fixture():
    manifest = {"model": {"alias": "synthetic-teacher", "gguf_sha256": "a"*64}, "adapter": None}
    text = "Synthetic original wording for a provenance test."
    messages = D.source_messages("en-US", "general", 1, 0)
    raw = json.dumps({"model": manifest["model"]["alias"], "choices": [{"finish_reason": "stop",
        "message": {"content": json.dumps({"utterances": [text]})}}]})
    evidence = {"model": manifest["model"], "manifest_sha256": D.digest(manifest), "prompt_hashes": {},
                "runtime": {"model_sha256": "a"*64, "adapter_sha256": None,
                            "chat_template_kwargs": {"enable_thinking": False}},
                "messages_sha256": D.digest(messages), "raw_response": raw, "raw_response_sha256": D.digest(raw.encode())}
    return {"source_origin": "teacher_generated", "text": text, "source_locale": "en-US", "scenario": "general",
            "source_generation": {"version": D.SOURCE_VERSION, "messages": messages, "teacher": evidence, "index": 0}}, manifest


@pytest.mark.parametrize("change", ["text", "raw", "messages", "model", "missing"])
def test_teacher_source_origin_requires_raw_generation_evidence(change):
    original, manifest = source_fixture()
    assert D.verify_source_record(original, manifest, {}) == []
    changed = copy.deepcopy(original)
    if change == "text":
        changed["text"] = "An authored replacement."
    elif change == "raw":
        changed["source_generation"]["teacher"]["raw_response"] += " "
    elif change == "messages":
        changed["source_generation"]["messages"][-1]["content"] = "{}"
    elif change == "model":
        changed["source_generation"]["teacher"]["model"]["alias"] = "different-model"
    else:
        del changed["source_generation"]
    assert D.verify_source_record(changed, manifest, {})
