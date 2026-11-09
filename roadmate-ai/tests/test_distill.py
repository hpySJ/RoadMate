import asyncio
import csv
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))

import data_boundary as B
import distill as D
import prepare_data
from inference import LlamaClient
from sources.source_templates import DIRECTIONS, SCENARIOS


def manifest(teacher_profile="14b-q4"):
    # Pinned metadata only; all inference responses and training examples below are synthetic.
    return D.load_manifest(ROOT / "models" / D.MODEL_PROFILES[teacher_profile])


def record(index=0, teacher_profile="14b-q4"):
    # Synthetic unit-test answers never become repository training labels.
    row = {"id": f"synthetic-{index}", "group_id": f"synthetic-group-{index}", "task": "translate",
           "source_locale": "en-US", "target_locale": "ko-KR", "scenario": "general",
           "text": "Could you stamp this luggage label?", "source": "teacher_distillation", "review_status": D.PENDING,
           "prompt_version": D.P.prompt("translate")[0],
           "output": {"status": "ok", "translated_text": "이 수하물 표에 도장을 찍어 주실 수 있나요?",
                      "clarification_question": None}, "filters": {"passed": True, "issues": []}}
    model = manifest(teacher_profile)
    raw = json.dumps({"model": model["model"]["alias"], "choices": [{"finish_reason": "stop",
        "message": {"content": json.dumps(row["output"], ensure_ascii=False)}}]}, ensure_ascii=False)
    row["teacher"] = {"model": model["model"], "manifest_sha256": D.digest(model),
                      "runtime": {"model_sha256": model["model"]["gguf_sha256"], "adapter_sha256": None,
                                  "chat_template_kwargs": {"enable_thinking": False}},
                      "prompt_hashes": {"fixture": "synthetic"}, "messages_sha256": D.digest(D.messages_for(row)),
                      "raw_response": raw, "raw_response_sha256": D.digest(raw.encode())}
    return row


def save_draft(path, rows):
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def fill_review(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row["ai_verdict"] = "좋음"
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=D.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def test_pool_is_a_bounded_safety_supplement_without_targets():
    rows = D.read_jsonl(ROOT / "train/sources/pool.jsonl")
    translations = [r for r in rows if r["task"] == "translate"]
    counts = Counter((r["group_id"], r["source_locale"], r.get("target_locale")) for r in rows)
    assert 0 < len(rows) <= 300
    assert max(counts.values()) <= 2
    assert {r["scenario"] for r in translations} == set(SCENARIOS)
    destinations = Counter(r["source_locale"] for r in rows if r["task"] == "destination")
    assert set(destinations) == {"en-US", "es-US", "es-MX", "ko-KR"}
    assert max(destinations.values()) <= 2
    assert not any(r["group_id"].startswith("distill_") for r in translations)
    assert all("output" not in r and "ko_natural" not in r for r in rows)
    assert len({(r["source_locale"], r.get("target_locale"), r["text"].casefold()) for r in rows}) == len(rows)


def test_boundary_rejects_both_source_and_output_and_near_duplicates():
    row = record()
    protected = {B.fingerprint(row["text"], "en-US"), B.fingerprint(row["output"]["translated_text"], "ko-KR")}
    assert B.record_overlaps(row, protected) == ["boundary_source", "boundary_output"]
    assert B.overlaps("Could you stamp that luggage label?", "en-US", protected)
    assert not B.overlaps("The kettle is unplugged.", "en-US", protected)


@pytest.mark.parametrize("change", ["output", "raw", "model", "prompt", "human"])
def test_targets_must_be_unedited_verified_teacher_output(change):
    row = record()
    assert D.verify_teacher_record(row, manifest(), {"fixture": "synthetic"}) == []
    if change == "output":
        row["output"]["translated_text"] = "다른 번역입니다."
    elif change == "raw":
        row["teacher"]["raw_response"] += " "
    elif change == "model":
        row["teacher"]["runtime"]["model_sha256"] = "c" * 64
    elif change == "prompt":
        row["teacher"]["prompt_hashes"] = {}
    else:
        row["source"] = "human_fixed"
        row["review_status"] = "approved"
    assert D.verify_teacher_record(row, manifest(), {"fixture": "synthetic"})


def test_filters_keep_safety_and_translationese_requirements():
    row = record()
    assert D.filter_issues(row, set()) == []
    row["output"]["translated_text"] = "나는 이 수하물 표에 도장을 찍어 줄 수 있나요?"
    assert "translationese:first_person_pronoun" in D.filter_issues(row, set())
    row["output"]["translated_text"] = "이 수하물 표에는 도장을 찍지 마세요."
    assert "negation_mismatch" in D.filter_issues(row, set())


def test_review_evidence_is_blind_and_bound_to_immutable_random_sample(tmp_path):
    rows = [record(i) for i in range(160)]
    draft = tmp_path / "distill.draft.jsonl"
    save_draft(draft, rows)
    D.export_sample(draft, rows, tmp_path, 42)
    csv_path = tmp_path / "sample.review.csv"
    assert manifest()["model"]["alias"] not in csv_path.read_text(encoding="utf-8-sig")
    assert "gguf_sha256" not in (tmp_path / "sample.review.md").read_text(encoding="utf-8")
    filled = fill_review(csv_path)
    assert len(filled) == 150
    evidence_path = tmp_path / "sample.evidence.json"
    D.record_review(draft, csv_path, tmp_path / "sample.key.json", evidence_path, "synthetic-ai-reviewer")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence["status"] == D.REVIEWED and evidence["human_reviewed"] is False
    D.verify_ai_review(draft, rows, evidence)
    evidence["sample"][0]["record_sha256"] = "tampered"
    with pytest.raises(ValueError, match="complete random sample"):
        D.verify_ai_review(draft, rows, evidence)
    draft.write_text(draft.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="different draft"):
        D.verify_ai_review(draft, rows, evidence)


@pytest.mark.parametrize("change", ["translation", "bad", "blank"])
def test_review_cannot_change_targets_or_skip_bad_sample(tmp_path, change):
    draft = tmp_path / "distill.draft.jsonl"
    save_draft(draft, [record()])
    D.export_sample(draft, [record()], tmp_path, 42)
    path = tmp_path / "sample.review.csv"
    rows = fill_review(path)
    if change == "translation":
        rows[0]["output"] = "검토자가 쓴 새 번역"
    else:
        rows[0]["ai_verdict"] = "나쁨" if change == "bad" else ""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=D.FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError):
        D.record_review(draft, path, tmp_path / "sample.key.json", tmp_path / "evidence.json", "AI reviewer")


@pytest.mark.parametrize("teacher_profile", D.TEACHER_PROFILES)
def test_teacher_captures_raw_response_and_rejects_wrong_response_model(monkeypatch, teacher_profile):
    selected = manifest(teacher_profile)
    async def identity(self):
        return record(teacher_profile=teacher_profile)["teacher"]["runtime"]
    monkeypatch.setattr(LlamaClient, "runtime_identity", identity)

    async def run(alias):
        client = LlamaClient("http://127.0.0.1:8081/v1", selected["model"]["alias"], selected)
        await client.http.aclose()
        response = json.loads(record(teacher_profile=teacher_profile)["teacher"]["raw_response"])
        response["model"] = alias
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=(
            {"input_tokens": 123} if request.url.path.endswith("/input_tokens") else response))))
        teacher = D.Teacher(client, selected, {"fixture": "synthetic"}, teacher_profile)
        try:
            output, provenance = await teacher.call(D.messages_for(record()), D.P.TRANSLATE_SCHEMA, 256, {})
            assert output == record()["output"]
            assert provenance["raw_response_sha256"] == D.digest(provenance["raw_response"].encode())
            assert provenance["teacher_profile"] == teacher_profile
        finally:
            await client.aclose()
    asyncio.run(run(selected["model"]["alias"]))
    with pytest.raises(ValueError, match="alias mismatch"):
        asyncio.run(run("different-model"))


@pytest.mark.parametrize("count,status,allowed", [(3072, 200, True), (3073, 200, False),
                                                (-1, 200, False), (True, 200, False), (0, 503, False)])
def test_teacher_counts_complete_history_before_inference(monkeypatch, count, status, allowed):
    async def identity(self):
        return record()["teacher"]["runtime"]
    monkeypatch.setattr(LlamaClient, "runtime_identity", identity)
    history = [f"Synthetic accepted source {index}." for index in range(79)]
    messages = D.source_messages("en-US", "general", 1, 8, history)
    schema = {"type": "object", "properties": {"utterances": {"type": "array", "items": {"type": "string"}}}}
    output = {"utterances": ["Synthetic new source."]}
    paths = []

    def respond(request):
        paths.append(request.url.path)
        body = json.loads(request.content)
        assert body["messages"] == messages
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["response_format"]["json_schema"]["schema"] == schema
        if request.url.path.endswith("/input_tokens"):
            return httpx.Response(status, json={"input_tokens": count})
        return httpx.Response(200, json={"model": manifest()["model"]["alias"], "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(output)}}]})

    async def run():
        client = LlamaClient("http://127.0.0.1:8081/v1", manifest()["model"]["alias"], manifest())
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            teacher = D.Teacher(client, manifest(), {})
            if allowed:
                actual, evidence = await teacher.call(messages, schema, D.SOURCE_MAX_TOKENS, {})
                assert actual == output
                assert evidence["token_budget"] == {"prompt": count, "generation_reserved": 1024, "context": 4096}
            else:
                with pytest.raises((ValueError, httpx.HTTPStatusError)):
                    await teacher.call(messages, schema, D.SOURCE_MAX_TOKENS, {})
        finally:
            await client.aclose()
    asyncio.run(run())
    assert paths == ["/v1/chat/completions/input_tokens"] + (["/v1/chat/completions"] if allowed else [])
    assert json.loads(messages[-1]["content"])["already_used"] == history


def test_prepare_rejects_legacy_human_approval_as_training_target(tmp_path):
    row = record()
    row.update(source="human_fixed", review_status="approved")
    source = tmp_path / "legacy.jsonl"
    save_draft(source, [row])
    result = subprocess.run([sys.executable, str(ROOT / "train/prepare_data.py"), "--inputs", str(source),
                             "--out", str(tmp_path / "prepared")], capture_output=True)
    assert result.returncode != 0 and b"teacher_only" in result.stdout + result.stderr
    assert not (tmp_path / "prepared").exists()


@pytest.mark.parametrize("teacher_profile", ("14b-q4", "32b-q4"))
@pytest.mark.parametrize("change", [None, "different_teacher", "mixed_teacher", "revision", "target_profile", "source_profile"])
def test_prepare_verified_teacher_draft_records_ai_review_and_group_split(monkeypatch, tmp_path, teacher_profile, change):
    pinned = manifest(teacher_profile)
    rows = [record(0), record(1)]
    rows[1]["text"] = "The display on this handheld scanner is broken."
    rows[1]["output"]["translated_text"] = "이 휴대용 스캐너 화면이 고장 났어요."
    assignments = prepare_data.split_groups([f"fixture-{index}" for index in range(100)], 42)
    for row, split in zip(rows, ("train", "validation")):
        row["group_id"] = next(group for group, assigned in assignments.items() if assigned == split)
        raw = json.dumps({"model": pinned["model"]["alias"], "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(row["output"], ensure_ascii=False)}}]}, ensure_ascii=False)
        row["teacher"].update(model=pinned["model"], manifest_sha256=D.digest(pinned),
            prompt_hashes=B.server_prompt_hashes(ROOT), messages_sha256=D.digest(D.messages_for(row)),
            raw_response=raw, raw_response_sha256=D.digest(raw.encode()))
        row["teacher"]["runtime"]["model_sha256"] = pinned["model"]["gguf_sha256"]
        row["source_origin"] = "teacher_generated"
        messages = D.source_messages(row["source_locale"], row["scenario"], 1, 0)
        source_raw = json.dumps({"model": pinned["model"]["alias"], "choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps({"utterances": [row["text"]]})}}]})
        row["source_generation"] = {"version": D.SOURCE_VERSION, "index": 0, "messages": messages,
            "teacher": {**row["teacher"], "messages_sha256": D.digest(messages),
                        "raw_response": source_raw, "raw_response_sha256": D.digest(source_raw.encode())}}
    draft = tmp_path / "distill.draft.jsonl"
    if change == "mixed_teacher":
        rows[0]["teacher"]["model"] = manifest("32b-q4" if teacher_profile == "14b-q4" else "14b-q4")["model"]
    elif change == "revision":
        rows[0]["teacher"]["model"]["base_revision"] = "f" * 40
    elif change in ("target_profile", "source_profile"):
        provenance = rows[0]["teacher"] if change == "target_profile" else rows[0]["source_generation"]["teacher"]
        provenance["teacher_profile"] = "32b-q4" if teacher_profile == "14b-q4" else "14b-q4"
    save_draft(draft, rows)
    D.export_sample(draft, rows, tmp_path, 42)
    fill_review(tmp_path / "sample.review.csv")
    evidence = tmp_path / "sample.evidence.json"
    D.record_review(draft, tmp_path / "sample.review.csv", tmp_path / "sample.key.json", evidence, "synthetic-AI")
    prepared = tmp_path / "prepared"
    monkeypatch.setattr(sys, "argv", ["prepare_data.py", "--inputs", str(draft), "--ai-review", str(evidence),
                                     "--out", str(prepared), "--stt-copies-ratio", "1", "--teacher", (
        "32b-q4" if teacher_profile == "14b-q4" else "14b-q4") if change == "different_teacher" else teacher_profile])
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kw: None, raising=False)
    if change:
        with pytest.raises(SystemExit, match="invalid records"):
            prepare_data.main()
        assert not prepared.exists()
        return
    prepare_data.main()
    meta = json.loads((prepared / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert meta["review_status"] == D.REVIEWED and meta["target_source"] == "qwen_teacher"
    assert meta["enable_thinking"] is False and meta["human_reviewed"] is False and meta["allow_draft"] is False
    assert meta["counts"] == {"train": 2, "validation": 2, "test": 0}
    assert meta["stt_copies"]["added_rows"] == 2 and meta["stt_copies"]["copy_share"] == .5
    for split in ("train", "validation"):
        original, copy = prepare_data.load_jsonl(prepared / f"{split}.jsonl")
        assert original["completion"] == copy["completion"]
        assert original["meta"]["group_id"] == copy["meta"]["group_id"]
        assert copy["meta"]["stt_copy"]["parent_text"] == original["meta"]["text"]
    from train.train_qlora import check_groups
    check_groups(prepared, meta)
    assert meta["ai_review_sha256"] == D.digest(evidence.read_bytes())
    assert meta["teacher_profile"] == teacher_profile and meta["teacher_model"] == pinned["model"]


@pytest.mark.parametrize("teacher_profile", D.TEACHER_PROFILES)
@pytest.mark.parametrize("change", [None, "other_profile", "base_revision", "gguf_revision", "gguf_sha256", "adapter", "thinking"])
def test_teacher_requires_exact_selected_pinned_model(teacher_profile, change):
    selected = manifest(teacher_profile)
    if change == "other_profile":
        selected = manifest("32b-q4" if teacher_profile == "14b-q4" else "14b-q4")
    elif change == "adapter":
        selected["adapter"] = {"sha256": "f" * 64}
    elif change == "thinking":
        selected["runtime"]["chat_template_kwargs"]["enable_thinking"] = True
    elif change:
        selected["model"][change] = "f" * 64
    if change:
        with pytest.raises(ValueError, match="pinned, unadapted non-thinking"):
            D.assert_teacher(selected, teacher_profile)
    else:
        D.assert_teacher(selected, teacher_profile)
        assert not D.verify_teacher_record(record(teacher_profile=teacher_profile), selected,
                                           {"fixture": "synthetic"}, teacher_profile)


@pytest.mark.parametrize("teacher_profile", D.TEACHER_PROFILES)
@pytest.mark.parametrize("health_matches", [True, False])
def test_generation_uses_selected_teacher_and_refuses_other_ready_server(monkeypatch, tmp_path, teacher_profile, health_matches):
    selected = manifest(teacher_profile)
    settings = SimpleNamespace(manifest=selected, model_profile=teacher_profile, model_backend="llama",
        model_base_url="http://127.0.0.1:8081/v1", model_id=selected["model"]["alias"])
    monkeypatch.setattr(D.Settings, "from_env", lambda: settings)
    identity = record(teacher_profile=teacher_profile)["teacher"]["runtime"]
    class Client:
        async def prepare_runtime(self):
            pass
        async def runtime_identity(self):
            return identity
        async def aclose(self):
            pass
    monkeypatch.setattr(D, "LlamaClient", lambda *args: Client())
    seen = []
    monkeypatch.setattr(D, "Teacher", lambda client, manifest, hashes, profile: seen.append(profile))
    actual_http_client = httpx.AsyncClient
    other = "32b-q4" if teacher_profile == "14b-q4" else "14b-q4"
    health = {"ready": True, "backend": "llama", "model_profile": teacher_profile if health_matches else other,
              "model_runtime": identity, "artifact_hashes": D.artifact_hashes()}
    monkeypatch.setattr(D.httpx, "AsyncClient", lambda **kwargs: actual_http_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=health))))
    async def stop_at_source(*args):
        raise RuntimeError("source generation reached")
    monkeypatch.setattr(D, "source_augmentation", stop_at_source)
    monkeypatch.setattr(D, "protected_templates", lambda root, **kwargs: set())
    args = SimpleNamespace(teacher=teacher_profile, out=tmp_path, model_url=None,
                           url="http://127.0.0.1:8000", source_count=80, seed=42)
    with pytest.raises(RuntimeError if health_matches else ValueError,
                       match="source generation reached" if health_matches else "AI ready identity"):
        asyncio.run(D.generate(args))
    assert seen == [teacher_profile]
