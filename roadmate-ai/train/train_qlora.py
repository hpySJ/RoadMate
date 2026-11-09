"""학습: Qwen3-8B NF4 QLoRA (Transformers + PEFT + TRL, completion-only loss).

본학습 전 스모크: .venv-train\\Scripts\\python.exe train\\train_qlora.py --smoke
본학습:         .venv-train\\Scripts\\python.exe train\\train_qlora.py
에뮬레이터·llama-server를 끄고 실행한다(같은 GPU 동시 사용 금지).
"""
import argparse
import hashlib
import json
import platform
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import prompting as P  # noqa: E402
from train.data_boundary import server_prompt_hashes  # noqa: E402
from config import MODEL_PROFILES, TEACHER_PROFILES, load_manifest  # noqa: E402
from train.source_mix import POLICY_VERSION, check_source_mix, source_mix  # noqa: E402


def check_lengths(ds, tok, max_length: int) -> None:
    for split in ("train", "validation"):
        if not len(ds[split]):
            raise SystemExit(f"{split} split is empty: re-run train/prepare_data.py")
        for i, row in enumerate(ds[split], 1):
            rendered = render_training_row(row, tok)
            text = rendered["prompt"] + rendered["completion"]
            n = len(tok(text, add_special_tokens=False, truncation=False)["input_ids"])
            if n > max_length:
                raise SystemExit(f"{split}:{i} has {n} tokens > max_length={max_length}: completion would be truncated")


def render_training_row(row, tok) -> dict:
    """Pre-render strings so TRL cannot silently choose its own thinking template."""
    prompt = tok.apply_chat_template(row["prompt"], tokenize=False, add_generation_prompt=True, enable_thinking=False)
    full = tok.apply_chat_template(row["prompt"] + row["completion"], tokenize=False, enable_thinking=False)
    if not full.startswith(prompt):
        raise SystemExit("chat template does not preserve the generation prefix")
    completion = full[len(prompt):]
    if not completion.strip() or "<think>" in completion or "</think>" in completion:
        raise SystemExit("training completion must be nonthinking JSON")
    return {"prompt": prompt, "completion": completion}


def check_teacher_evidence(manifest: dict, teacher_profile=None) -> None:
    # Legacy verified datasets were exclusively 14B; absence never implies 32B.
    recorded_profile = manifest.get("teacher_profile", "14b-q4")
    teacher_profile = teacher_profile or recorded_profile
    if teacher_profile not in TEACHER_PROFILES:
        raise SystemExit("full training requires a supported teacher_profile")
    teacher = load_manifest(ROOT / "models" / MODEL_PROFILES[teacher_profile])["model"]
    if (manifest.get("target_source") != "qwen_teacher"
            or manifest.get("review_status") != "auto_filtered_ai_sample_reviewed"
            or manifest.get("human_reviewed") is not False
            or recorded_profile != teacher_profile
            or manifest.get("teacher_model") != teacher
            or manifest.get("source_mix_policy") != POLICY_VERSION
            or not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("ai_review_sha256", "")))):
        raise SystemExit("full training requires Qwen teacher targets and recorded AI sample review")


def check_groups(data_dir: Path, manifest: dict) -> None:
    if manifest.get("enable_thinking") is not False:
        raise SystemExit("prepared dataset missing enable_thinking=False: re-run prepare_data.py")
    groups, metadata, by_split_mix = {}, [], {}
    for split in ("train", "validation", "test"):
        part = []
        for line in (data_dir / f"{split}.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line).get("meta", {})
            group = row.get("group_id")
            if not isinstance(group, str) or not group:
                raise SystemExit("prepared dataset missing group_id")
            if group in groups and groups[group] != split:
                raise SystemExit("group_id crosses training/validation/test splits")
            groups[group] = split
            part.append(row)
        metadata += part
        by_split_mix[split] = part
    if not manifest.get("allow_draft", False):
        if manifest.get("source_mix_policy") != POLICY_VERSION:
            raise SystemExit("source mix policy missing or outdated: re-run prepare_data.py")
        try:
            measured = {"overall": check_source_mix(metadata), "by_split": {
                split: check_source_mix(part) if part else source_mix(part)
                for split, part in by_split_mix.items()}}
        except (KeyError, ValueError) as error:
            raise SystemExit(f"invalid source mix: {error}") from error
        if measured != manifest.get("source_mix"):
            raise SystemExit("source mix differs from dataset manifest: re-run prepare_data.py")


def load_model(cfg: dict, use_4bit: bool):
    import torch
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    cuda = torch.cuda.is_available()
    dtype = getattr(torch, cfg["compute_dtype"]) if cuda else torch.float32
    if cuda and dtype == torch.bfloat16 and not torch.cuda.is_bf16_supported():
        dtype = torch.float16
    kw = {"revision": cfg.get("base_revision"), "dtype": dtype}
    if use_4bit:
        # QWEN 구현: 본체 NF4 4비트 + double quantization, 본체 가중치는 고정
        kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type=cfg["bnb_4bit_quant_type"],
            bnb_4bit_use_double_quant=cfg["bnb_4bit_use_double_quant"], bnb_4bit_compute_dtype=dtype)
    if cuda:
        kw["device_map"] = {"": 0}
    model = AutoModelForCausalLM.from_pretrained(cfg["base_model"], **kw)
    model.config.use_cache = False
    if use_4bit:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=cfg["gradient_checkpointing"])
    lc = cfg["lora"]
    # 파라미터 튜닝: LoRA rank/alpha/dropout/대상 모듈
    model = get_peft_model(model, LoraConfig(r=lc["r"], lora_alpha=lc["alpha"], lora_dropout=lc["dropout"],
                                             bias=lc["bias"], target_modules=lc["target_modules"], task_type="CAUSAL_LM"))
    return model, dtype


def check_mask(trainer, tok) -> dict:
    # 검증: prompt는 -100으로 가려지고 정답 JSON과 종료 토큰만 학습되는지 확인
    batch = next(iter(trainer.get_train_dataloader()))
    labels = batch["labels"][0]
    sup = labels[labels != -100]
    total = int((batch["attention_mask"][0] == 1).sum()) if "attention_mask" in batch else labels.numel()
    text = tok.decode(sup)
    if not 0 < sup.numel() < total:
        raise SystemExit(f"bad completion mask: supervised={sup.numel()} total={total}")
    if "<|im_end|>" not in text or '"' not in text:
        raise SystemExit(f"supervised tokens look wrong: {text[:200]!r}")
    return {"supervised_tokens": int(sup.numel()), "total_tokens": total, "supervised_text": text[:300]}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "train" / "training_config.json"))
    ap.add_argument("--teacher", choices=TEACHER_PROFILES, help="must match the prepared dataset and training config")
    ap.add_argument("--data-dir", type=Path)
    ap.add_argument("--output-dir", type=Path)
    ap.add_argument("--smoke", action="store_true", help="optimizer step 2회 + 저장·생성 확인")
    ap.add_argument("--base-model", default=None, help="config의 base_model 대신 사용(검증용)")
    ap.add_argument("--no-4bit", action="store_true", help="양자화 없이 로딩(CPU 검증용)")
    ap.add_argument("--max-steps", type=int, default=-1)
    a = ap.parse_args()

    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    teacher_profile = a.teacher or cfg.get("teacher_profile", "14b-q4")
    if teacher_profile not in TEACHER_PROFILES or cfg.get("teacher_profile", teacher_profile) != teacher_profile:
        raise SystemExit("--teacher conflicts with the training config teacher_profile")
    cfg["teacher_profile"] = teacher_profile
    if a.base_model:
        cfg["base_model"], cfg["base_revision"] = a.base_model, None
    if a.data_dir:
        cfg["data_dir"] = str(a.data_dir.resolve())
    if a.output_dir:
        cfg["output_dir"] = str(a.output_dir.resolve())
    if cfg.get("enable_thinking") is not False:
        raise SystemExit("training requires enable_thinking=False")
    use_4bit = cfg["load_in_4bit"] and not a.no_4bit
    out_dir = ROOT / cfg["output_dir"] / ("smoke" if a.smoke else "")
    data_dir = ROOT / cfg["data_dir"]

    # 검증: prepare_data가 승인한 자료와 해시만 사용한다(자동 검토와 사람 검수는 별도 상태).
    dm_path = data_dir / "dataset_manifest.json"
    if not dm_path.exists():
        raise SystemExit(f"{dm_path} missing: run train/prepare_data.py first")
    dm = json.loads(dm_path.read_text(encoding="utf-8"))
    if dm["allow_draft"] and not a.smoke:
        raise SystemExit("dataset was prepared with --allow-draft (smoke only). Re-run prepare_data.py without it.")
    for name, digest in dm["outputs"].items():
        if hashlib.sha256((data_dir / name).read_bytes()).hexdigest() != digest:
            raise SystemExit(f"{name} changed after prepare_data.py: re-run it")
    current_prompts = {name: P.prompt(name)[0] for name in ("translate", "destination")}
    if dm.get("prompt_versions") != current_prompts or dm.get("glossary_version") != P.glossary()["version"]:
        raise SystemExit("prompt/glossary versions changed: re-run train/prepare_data.py")
    if dm.get("prompt_hashes") != server_prompt_hashes(ROOT):
        raise SystemExit("prompt input hashes changed: re-run train/prepare_data.py")

    if not a.smoke:
        check_teacher_evidence(dm, teacher_profile)
    check_groups(data_dir, dm)
    if out_dir.exists():
        raise SystemExit(f"output directory already exists: {out_dir}")

    # 검수·무결성 확인 전에는 학습 의존성이나 모델을 불러오지 않는다.
    import torch
    from datasets import load_dataset
    from transformers import AutoTokenizer, set_seed
    from trl import SFTConfig, SFTTrainer

    set_seed(cfg["seed"])
    files = {"train": str(data_dir / "train.jsonl"), "validation": str(data_dir / "validation.jsonl")}
    ds = load_dataset("json", data_files=files).select_columns(["prompt", "completion"])
    tok = AutoTokenizer.from_pretrained(cfg["base_model"], revision=cfg.get("base_revision"))
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    check_lengths(ds, tok, cfg["max_length"])
    template_sha = hashlib.sha256(tok.chat_template.encode()).hexdigest()
    ds = ds.map(lambda row: render_training_row(row, tok))
    model, dtype = load_model(cfg, use_4bit)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    if not 0 < trainable < total:
        raise SystemExit("trainable parameter check failed")
    print(f"trainable={trainable:,} total={total:,} ({trainable / total:.4%}) dtype={dtype} 4bit={use_4bit}")

    cuda = torch.cuda.is_available()
    ga = 2 if a.smoke else cfg["gradient_accumulation_steps"]
    # 파라미터 튜닝: 배치 1 × 누적 16, 학습률, 스케줄러, epoch, 최대 길이
    args = SFTConfig(
        output_dir=str(out_dir),
        per_device_train_batch_size=cfg["per_device_train_batch_size"],
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=ga,
        learning_rate=cfg["learning_rate"],
        lr_scheduler_type=cfg["lr_scheduler_type"],
        warmup_steps=cfg["warmup_steps"],  # 0~1 실수는 전체 step 대비 비율
        num_train_epochs=cfg["num_train_epochs"],
        max_steps=2 if a.smoke else a.max_steps,
        max_length=cfg["max_length"],
        completion_only_loss=True,
        gradient_checkpointing=cfg["gradient_checkpointing"],
        bf16=cuda and dtype == torch.bfloat16,
        fp16=cuda and dtype == torch.float16,
        logging_steps=1 if a.smoke else cfg["logging_steps"],
        eval_strategy="steps",
        eval_steps=1 if a.smoke else cfg["eval_steps"],
        save_strategy="steps",
        save_steps=2 if a.smoke else cfg["save_steps"],
        save_total_limit=cfg["save_total_limit"],
        seed=cfg["seed"],
        report_to="none",
        use_cpu=not cuda,
    )
    trainer = SFTTrainer(model=model, args=args, train_dataset=ds["train"], eval_dataset=ds["validation"],
                         processing_class=tok)
    mask = check_mask(trainer, tok)
    print("mask ok:", mask["supervised_tokens"], "/", mask["total_tokens"], repr(mask["supervised_text"][:120]))

    if cuda:
        torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    result = trainer.train()
    secs = time.time() - t0
    adapter_dir = out_dir / "adapter"
    trainer.save_model(str(adapter_dir))
    tok.save_pretrained(str(adapter_dir))
    if not (adapter_dir / "adapter_model.safetensors").exists():
        raise SystemExit("adapter_model.safetensors not saved")

    # 검증: 학습된 어댑터로 검증 자료 1건 생성
    model.eval()
    model.config.use_cache = True
    row = load_dataset("json", data_files=files["validation"])["train"][0]
    text = tok.apply_chat_template(row["prompt"], add_generation_prompt=True, tokenize=False, enable_thinking=False)
    enc = tok(text, return_tensors="pt", add_special_tokens=False).to(model.device)
    with torch.no_grad():
        gen = model.generate(**enc, max_new_tokens=64, do_sample=False)
    sample = tok.decode(gen[0, enc["input_ids"].shape[1]:], skip_special_tokens=True)

    meta = {
        "config": cfg, "smoke": a.smoke, "dataset": dm,
        "dataset_sha256": hashlib.sha256(dm_path.read_bytes()).hexdigest(),
        "chat_template_sha256": template_sha, "enable_thinking": False,
        "adapter_sha256": hashlib.sha256((adapter_dir / "adapter_model.safetensors").read_bytes()).hexdigest(),
        "use_4bit": use_4bit, "dtype": str(dtype),
        "trainable_params": trainable, "total_params": total, "mask": mask,
        "train_seconds": round(secs, 1), "global_steps": result.global_step, "train_loss": result.training_loss,
        "peak_vram_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2) if cuda else None,
        "gpu": torch.cuda.get_device_name(0) if cuda else None,
        "versions": {m.__name__: m.__version__ for m in map(__import__, ["torch", "transformers", "peft", "trl", "datasets"])},
        "python": platform.python_version(), "log_history": trainer.state.log_history, "sample_generation": sample,
    }
    (out_dir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"done steps={result.global_step} loss={result.training_loss:.4f} secs={secs:.0f} "
          f"peak_vram={meta['peak_vram_gib']}GiB -> {adapter_dir}")
    print("sample:", sample[:200])


if __name__ == "__main__":
    main()
