# 테스트
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

import config
from train import convert_adapter, train_qlora
from train.source_mix import POLICY_VERSION


def save(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROFILE", "8b-q4")
    monkeypatch.delenv("LORA_ADAPTER", raising=False)
    base = config.load_manifest()
    adapter = tmp_path / "candidate.gguf"
    adapter.write_bytes(b"synthetic adapter")
    record = {"sha256": hashlib.sha256(adapter.read_bytes()).hexdigest(),
              "base": {key: base["model"][key] for key in ("base_repo", "base_revision", "gguf_sha256")},
              "dataset_sha256": "a" * 64, "training_meta_sha256": "b" * 64, "ai_review_sha256": "d" * 64,
              "target_source": "qwen_teacher", "review_status": "auto_filtered_ai_sample_reviewed",
              "smoke": False, "allow_draft": False, "enable_thinking": False, "adoption_status": "pending"}
    sidecar = Path(str(adapter) + ".json")
    save(sidecar, record)
    monkeypatch.setenv("LORA_ADAPTER", str(adapter))
    return adapter, sidecar, record, base


def test_overlay_preserves_base_profile_and_tracks_candidate(candidate):
    adapter, _, record, base = candidate
    before = config.manifest_path().read_bytes()
    manifest = config.load_manifest()
    assert manifest["model"] == base["model"]
    assert manifest["adapter"]["file"] == str(adapter)
    assert manifest["adapter"]["sha256"] == record["sha256"]
    assert manifest["engine_bundle"] == base["engine_bundle"] + "-lora-" + record["sha256"][:12]
    assert config.load_manifest(config.manifest_path()) == base
    assert config.manifest_path().read_bytes() == before


@pytest.mark.parametrize("change, message", [
    ({"adoption_status": "rejected"}, "rejected"),
    ({"smoke": True}, "smoke/draft"),
    ({"allow_draft": True}, "smoke/draft"),
    ({"enable_thinking": True}, "enable_thinking"),
    ({"sha256": "0" * 64}, "SHA256 mismatch"),
    ({"dataset_sha256": ""}, "provenance"),
    ({"base": {}}, "base model/revision/hash"),
    ({"target_source": "human"}, "teacher targets"),
    ({"review_status": "approved"}, "AI sample review"),
])
def test_overlay_rejects_invalid_sidecar(candidate, change, message):
    _, sidecar, record, _ = candidate
    save(sidecar, {**record, **change})
    with pytest.raises(ValueError, match=message):
        config.load_manifest()


def test_overlay_rejects_changed_adapter(candidate):
    adapter, _, _, _ = candidate
    adapter.write_bytes(b"changed weights")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        config.load_manifest()


def test_training_format_disables_thinking_before_trl():
    calls = []

    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            calls.append(kwargs)
            return "prefix" if len(messages) == 1 else "prefix{}<|im_end|>"

    row = {"prompt": [{"role": "user", "content": "synthetic"}],
           "completion": [{"role": "assistant", "content": "{}"}]}
    assert train_qlora.render_training_row(row, Tokenizer()) == {"prompt": "prefix", "completion": "{}<|im_end|>"}
    assert all(c["enable_thinking"] is False for c in calls)


@pytest.mark.parametrize("full", ["changed-prefix{}", "prefix<think>reason</think>{}"])
def test_training_format_rejects_changed_prefix_or_reasoning(full):
    class Tokenizer:
        def apply_chat_template(self, messages, **kwargs):
            return "prefix" if len(messages) == 1 else full

    with pytest.raises(SystemExit):
        train_qlora.render_training_row({"prompt": [{}], "completion": [{}]}, Tokenizer())


def test_training_rejects_groups_crossing_splits(tmp_path):
    for split in ("train", "validation", "test"):
        (tmp_path / f"{split}.jsonl").write_text(json.dumps({"meta": {"group_id": "same-template"}}), encoding="utf-8")
    with pytest.raises(SystemExit, match="crosses"):
        train_qlora.check_groups(tmp_path, {"enable_thinking": False})


def training_artifacts(tmp_path, teacher_profile="14b-q4"):
    base = config.load_manifest(config.ROOT / "models" / "manifest.qwen3-8b.json")
    adapter = tmp_path / "training" / "adapter"
    adapter.mkdir(parents=True)
    weights = adapter / "adapter_model.safetensors"
    weights.write_bytes(b"synthetic PEFT weights")
    save(adapter / "adapter_config.json", {"base_model_name_or_path": base["model"]["base_repo"]})
    data = tmp_path / "prepared"
    data.mkdir()
    (data / "train.jsonl").write_text("synthetic data", encoding="utf-8")
    dataset = {"target_source": "qwen_teacher", "review_status": "auto_filtered_ai_sample_reviewed",
               "source_mix_policy": POLICY_VERSION,
               "human_reviewed": False, "ai_review_sha256": "d" * 64,
               "teacher_profile": teacher_profile,
               "teacher_model": config.load_manifest(config.ROOT / "models" / config.MODEL_PROFILES[teacher_profile])["model"],
               "allow_draft": False, "outputs": {"train.jsonl": convert_adapter.sha256(data / "train.jsonl")}}
    save(data / "dataset_manifest.json", dataset)
    meta = {"config": {"base_model": base["model"]["base_repo"], "base_revision": base["model"]["base_revision"],
                       "data_dir": str(data), "enable_thinking": False, "teacher_profile": teacher_profile},
            "dataset": dataset, "dataset_sha256": convert_adapter.sha256(data / "dataset_manifest.json"),
            "smoke": False, "enable_thinking": False, "adapter_sha256": convert_adapter.sha256(weights),
            "chat_template_sha256": "c" * 64}
    save(adapter.parent / "run_meta.json", meta)
    return adapter, base, meta, data


@pytest.mark.parametrize("changed", ["weights", "dataset", "manifest", "base", "smoke", "thinking", "human"])
def test_conversion_rejects_changed_training_evidence(tmp_path, changed):
    adapter, base, meta, data = training_artifacts(tmp_path)
    if changed == "weights":
        (adapter / "adapter_model.safetensors").write_bytes(b"changed")
    elif changed == "dataset":
        (data / "train.jsonl").write_text("changed", encoding="utf-8")
    elif changed == "manifest":
        save(data / "dataset_manifest.json", {})
    else:
        if changed == "base":
            meta["config"]["base_revision"] = "other-revision"
        elif changed == "smoke":
            meta["smoke"] = True
        elif changed == "human":
            meta["dataset"].update(target_source="human", review_status="approved", human_reviewed=True)
        else:
            meta["enable_thinking"] = True
        save(adapter.parent / "run_meta.json", meta)
    with pytest.raises(SystemExit):
        convert_adapter.check_training(adapter, base)


def test_full_training_rejects_legacy_human_approved_dataset(tmp_path):
    _, _, meta, _ = training_artifacts(tmp_path)
    meta["dataset"].update(target_source="human", review_status="approved", human_reviewed=True)
    with pytest.raises(SystemExit, match="Qwen teacher targets"):
        train_qlora.check_teacher_evidence(meta["dataset"])


@pytest.mark.parametrize("teacher_profile", config.TEACHER_PROFILES)
def test_conversion_writes_sidecar_without_changing_base(tmp_path, monkeypatch, teacher_profile):
    adapter, base, meta, _ = training_artifacts(tmp_path, teacher_profile)
    out = tmp_path / "candidate.gguf"
    root = tmp_path / "tools-root"
    executable = root / ".venv-convert" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    executable.parent.mkdir(parents=True)
    executable.touch()
    script = root / "tools" / "llama-src" / "convert_lora_to_gguf.py"
    script.parent.mkdir(parents=True)
    script.touch()
    monkeypatch.setattr(convert_adapter, "ROOT", root)
    monkeypatch.setattr(convert_adapter, "load_manifest", lambda path: copy.deepcopy(base))
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(convert_adapter.subprocess, "run", lambda *a, **k: out.write_bytes(b"synthetic GGUF"))
    monkeypatch.setattr(sys, "argv", ["convert_adapter.py", "--adapter", str(adapter), "--out", str(out)])
    convert_adapter.main()
    record = json.loads(Path(str(out) + ".json").read_text())
    assert record["sha256"] == convert_adapter.sha256(out)
    assert record["base"]["base_revision"] == base["model"]["base_revision"]
    assert record["adoption_status"] == "pending" and record["enable_thinking"] is False
    assert record["teacher_profile"] == teacher_profile
    assert record["teacher_model"] == meta["dataset"]["teacher_model"]
    assert not (root / "models").exists()


@pytest.mark.parametrize("teacher_profile", config.TEACHER_PROFILES)
@pytest.mark.parametrize("change", [None, "profile", "model", "base_revision", "gguf_revision", "gguf_sha256"])
def test_training_requires_selected_teacher_identity(tmp_path, teacher_profile, change):
    _, _, meta, _ = training_artifacts(tmp_path, teacher_profile)
    dataset = meta["dataset"]
    other = "32b-q4" if teacher_profile == "14b-q4" else "14b-q4"
    if change == "profile":
        dataset["teacher_profile"] = other
    elif change == "model":
        dataset["teacher_model"] = config.load_manifest(config.ROOT / "models" / config.MODEL_PROFILES[other])["model"]
    elif change:
        dataset["teacher_model"][change] = "f" * 64
    if change:
        with pytest.raises(SystemExit, match="Qwen teacher targets"):
            train_qlora.check_teacher_evidence(dataset, teacher_profile)
    else:
        train_qlora.check_teacher_evidence(dataset, teacher_profile)


@pytest.mark.parametrize("teacher_profile", config.TEACHER_PROFILES)
def test_conversion_rejects_teacher_selection_changed_after_training(tmp_path, teacher_profile):
    adapter, base, meta, _ = training_artifacts(tmp_path, teacher_profile)
    meta["config"]["teacher_profile"] = "32b-q4" if teacher_profile == "14b-q4" else "14b-q4"
    save(adapter.parent / "run_meta.json", meta)
    with pytest.raises(SystemExit, match="Qwen teacher targets"):
        convert_adapter.check_training(adapter, base)


def test_training_rejects_cli_and_config_teacher_disagreement_before_model_load(tmp_path, monkeypatch):
    cfg = tmp_path / "training_config.json"
    save(cfg, {"teacher_profile": "32b-q4"})
    monkeypatch.setattr(sys, "argv", ["train_qlora.py", "--config", str(cfg), "--teacher", "14b-q4"])
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kwargs: None, raising=False)
    with pytest.raises(SystemExit, match="conflicts with the training config"):
        train_qlora.main()


@pytest.mark.parametrize("teacher_profile", config.TEACHER_PROFILES)
def test_missing_profile_only_preserves_verified_legacy_14b(tmp_path, teacher_profile):
    adapter, base, meta, data = training_artifacts(tmp_path, teacher_profile)
    del meta["dataset"]["teacher_profile"]
    del meta["config"]["teacher_profile"]
    save(data / "dataset_manifest.json", meta["dataset"])
    meta["dataset_sha256"] = convert_adapter.sha256(data / "dataset_manifest.json")
    save(adapter.parent / "run_meta.json", meta)
    if teacher_profile == "14b-q4":
        train_qlora.check_teacher_evidence(meta["dataset"])
        convert_adapter.check_training(adapter, base)
    else:
        with pytest.raises(SystemExit, match="Qwen teacher targets"):
            train_qlora.check_teacher_evidence(meta["dataset"], teacher_profile)
        with pytest.raises(SystemExit, match="Qwen teacher targets"):
            convert_adapter.check_training(adapter, base)
