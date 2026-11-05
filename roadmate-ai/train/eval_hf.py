"""학습: Python(NF4) 경로에서 어댑터 적용 전/후를 같은 검증 자료로 비교한다(학습 자체의 효과).

사용: .venv-train\\Scripts\\python.exe train\\eval_hf.py --split validation                      # 기준(어댑터 없음)
      .venv-train\\Scripts\\python.exe train\\eval_hf.py --split validation --adapter train\\runs\\lora-v1\\adapter
최종 배포 품질은 GGUF+LoRA로 llama-server를 띄운 뒤 eval\\evaluate.py로 측정한다.
"""
import argparse
import json
import sys
from pathlib import Path

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from validation import validate_destination, validate_translation  # noqa: E402


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "train" / "training_config.json"))
    ap.add_argument("--split", default="validation", choices=["validation", "test"])
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--base-model", default=None)
    ap.add_argument("--no-4bit", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding="utf-8"))
    if a.base_model:
        cfg["base_model"], cfg["base_revision"] = a.base_model, None

    cuda = torch.cuda.is_available()
    dtype = (torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16) if cuda else torch.float32
    kw = {"revision": cfg.get("base_revision"), "dtype": dtype}
    if cuda and not a.no_4bit:
        kw["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                       bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
        kw["device_map"] = {"": 0}
    tok = AutoTokenizer.from_pretrained(cfg["base_model"], revision=cfg.get("base_revision"))
    model = AutoModelForCausalLM.from_pretrained(cfg["base_model"], **kw)
    if a.adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, a.adapter)
    model.eval()

    rows = [json.loads(x) for x in (ROOT / cfg["data_dir"] / f"{a.split}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = rows[: a.limit] if a.limit else rows
    stats = {"n": 0, "json_ok": 0, "status_match": 0, "exact": 0, "validator_pass": 0}
    results = []
    for r in rows:
        text = tok.apply_chat_template(r["prompt"], add_generation_prompt=True, tokenize=False)
        enc = tok(text, return_tensors="pt", add_special_tokens=False).to(model.device)
        with torch.no_grad():
            g = model.generate(**enc, max_new_tokens=256, do_sample=False)
        raw = tok.decode(g[0, enc["input_ids"].shape[1]:], skip_special_tokens=True).strip()
        ref = json.loads(r["completion"][0]["content"])
        m = r["meta"]
        stats["n"] += 1
        try:
            out = json.loads(raw)
            stats["json_ok"] += 1
        except json.JSONDecodeError:
            out = None
        if isinstance(out, dict):
            if m["task"] == "translate":
                issues = validate_translation(out, m["text"], m["source_locale"], m["target_locale"])
                stats["status_match"] += out.get("status") == ref["status"]
                stats["exact"] += out.get("translated_text") == ref["translated_text"]
            else:
                issues = validate_destination(out)
                stats["status_match"] += out.get("intent") == ref["intent"]
                stats["exact"] += out.get("query_ko_candidate") == ref["query_ko_candidate"]
            stats["validator_pass"] += not issues
        else:
            issues = ["invalid_json"]
        results.append({"meta": m, "reference": ref, "output": out if out is not None else raw, "issues": issues})

    out_path = ROOT / "train" / "runs" / f"eval_hf-{a.split}-{'adapter' if a.adapter else 'base'}.jsonl"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for x in results:
            f.write(json.dumps(x, ensure_ascii=False) + "\n")
    n = max(stats["n"], 1)
    print(json.dumps({k: (v if k == "n" else f"{v}/{n} ({v / n:.1%})") for k, v in stats.items()}, ensure_ascii=False))
    print(f"saved {out_path}")


if __name__ == "__main__":
    main()
