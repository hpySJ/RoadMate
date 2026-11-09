"""Pinned local Qwen teacher -> immutable filtered draft -> recorded AI sample review.

No supplied or edited translations are accepted as targets. AI review is advisory,
separate from human approval; an unchanged raw teacher answer is always retained.
"""
import argparse
import asyncio
import csv
import hashlib
import json
import logging
import os
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "train"))

import httpx
import prompting as P
from config import MODEL_PROFILES, TEACHER_PROFILES, Settings, artifact_hashes, load_manifest
from data_boundary import fingerprint, overlaps, protected_templates, record_overlaps, server_prompt_hashes
from eval.evaluate import translationese_stats
from inference import LlamaClient, ModelError
from sources.source_templates import DIRECTIONS, SCENARIOS
from source_mix import check_source_mix, source_mix
from validation import looks_like_other_language, validate_destination, validate_translation

PENDING = "auto_filtered_pending_ai_sample_review"
REVIEWED = "auto_filtered_ai_sample_reviewed"
SOURCE_VERSION = "distill-source-v3"
SOURCE_BATCH_SIZE = 10
SOURCE_MAX_TOKENS = 1024
SOURCE_LOCALES = ("en-US", "es-US", "es-MX", "ko-KR")
SOURCE_TOPICS = {
    "gas_station": ["pump controls", "payment", "fuel choice", "receipts", "staff assistance", "small spills"],
    "parking": ["entry barrier", "payment machine", "finding a space", "lost ticket", "loading bags", "exit"],
    "rental_counter": ["pickup", "return", "vehicle condition", "documents", "extra equipment", "changing plans"],
    "accident": ["calling for help", "exchanging details", "describing damage", "witnesses", "waiting safely", "paperwork"],
    "general": ["road signs", "junctions", "finding an entrance", "asking for directions", "a missed turn", "road access"],
}
SOURCE_FORMS = ("brief request", "simple question", "short factual statement", "polite correction",
                "checking what was heard", "casual follow-up with clear references", "small hesitation or self-correction",
                "ordinary practical question")
SOURCE_FOCUS = ("ordinary interaction", "who gives or receives something", "negation", "request versus question",
                "numbers or quantities", "time", "money", "fuel or left/right where relevant")
FIELDS = ["sample_id", "task", "direction", "source", "output", "ai_verdict", "ai_issue", "notes"]


def digest(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                          separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def messages_for(row):
    if row["task"] == "translate":
        return P.translate_messages(row["source_locale"], row["target_locale"], row["scenario"], row["text"])
    return P.destination_messages(row["source_locale"], row["text"])


def filter_issues(row: dict, protected: set[str]) -> list[str]:
    if row["task"] == "translate":
        issues = validate_translation(row["output"], row["text"], row["source_locale"], row["target_locale"])
        if row["output"].get("status") != "ok":
            issues.append("not_ok_translation")
        stats = translationese_stats([{"case": row, "http": 200, "body": row["output"]}])
        issues += [f"translationese:{name}" for name, count in stats["marker_outputs"].items() if count]
    else:
        issues = validate_destination(row["output"])
    return issues + record_overlaps(row, protected)


def assert_teacher(manifest: dict, teacher_profile="14b-q4"):
    if teacher_profile not in TEACHER_PROFILES:
        raise ValueError(f"unsupported teacher profile: {teacher_profile}")
    pinned = load_manifest(ROOT / "models" / MODEL_PROFILES[teacher_profile])
    model = manifest["model"]
    if (model != pinned["model"] or model.get("license") != "apache-2.0"
            or manifest.get("adapter") or manifest["runtime"].get("chat_template_kwargs") != {"enable_thinking": False}):
        raise ValueError(f"distillation requires the pinned, unadapted non-thinking {teacher_profile} teacher")


def raw_output(raw: str, alias: str) -> dict:
    response = json.loads(raw)
    if response.get("model") != alias:
        raise ValueError("teacher response model alias mismatch")
    choice = response["choices"][0]
    message = choice["message"]
    content = message["content"]
    if (choice.get("finish_reason") != "stop" or message.get("reasoning_content") or message.get("reasoning")
            or any(tag in content.lower() for tag in ("<think>", "</think>"))):
        raise ValueError("teacher response was truncated or contained reasoning")
    result = json.loads(content)
    if not isinstance(result, dict):
        raise ValueError("teacher output is not an object")
    return result


class Teacher:
    def __init__(self, client: LlamaClient, manifest: dict, prompt_hashes: dict, teacher_profile="14b-q4"):
        assert_teacher(manifest, teacher_profile)
        self.client, self.manifest, self.prompt_hashes = client, manifest, prompt_hashes
        self.teacher_profile = teacher_profile
        self.raw = None
        client.http.event_hooks["response"].append(self.capture)

    async def capture(self, response):
        if response.request.url.path.endswith("/chat/completions"):
            self.raw = (await response.aread()).decode("utf-8")

    async def call(self, messages, schema, max_tokens, sampling):
        self.raw = None
        # Count with the serving template/tokenizer; never truncate accepted-source history to fit.
        response = await self.client.http.post(f"{self.client.base_url}/chat/completions/input_tokens", json={
            "model": self.manifest["model"]["alias"], "messages": messages,
            "chat_template_kwargs": self.manifest["runtime"]["chat_template_kwargs"],
            "response_format": {"type": "json_schema", "json_schema": {"name": "output", "schema": schema,
                                                                       "strict": True}}}, timeout=30)
        response.raise_for_status()
        prompt_tokens = response.json()["input_tokens"]
        context = self.manifest["runtime"]["ctx"]
        if type(prompt_tokens) is not int or prompt_tokens < 0:
            raise ValueError("teacher returned an invalid prompt token count")
        if prompt_tokens + max_tokens > context:
            raise ValueError(f"teacher context budget exceeded: {prompt_tokens} + {max_tokens} > {context}; "
                             "history was not truncated; inspect source_generation.jsonl")
        output = await self.client.chat_json(messages, schema, max_tokens, sampling, 120)
        if not self.raw or raw_output(self.raw, self.manifest["model"]["alias"]) != output:
            raise ValueError("teacher raw response does not match parsed output")
        identity = await self.client.runtime_identity()
        return output, {"teacher_profile": self.teacher_profile,
                        "model": self.manifest["model"], "manifest_sha256": digest(self.manifest),
                        "token_budget": {"prompt": prompt_tokens, "generation_reserved": max_tokens, "context": context},
                        "runtime": identity, "prompt_hashes": self.prompt_hashes,
                        "messages_sha256": digest(messages), "raw_response_sha256": digest(self.raw.encode()),
                        "raw_response": self.raw}


def verify_teacher_record(row: dict, manifest: dict, prompt_hashes: dict, teacher_profile="14b-q4") -> list[str]:
    """Integrity checks are reproducibility checks, not a remote cryptographic attestation."""
    try:
        assert_teacher(manifest, teacher_profile)
        teacher = row["teacher"]
        if (row.get("source") != "teacher_distillation" or row.get("review_status") != PENDING
                or row.get("prompt_version") != P.prompt("translate" if row["task"] == "translate" else "destination")[0]
                or teacher.get("teacher_profile", teacher_profile) != teacher_profile
                or teacher["model"] != manifest["model"] or teacher["manifest_sha256"] != digest(manifest)
                or teacher["runtime"]["model_sha256"] != manifest["model"]["gguf_sha256"]
                or teacher["runtime"].get("adapter_sha256") is not None
                or teacher["runtime"].get("chat_template_kwargs") != {"enable_thinking": False}
                or teacher["prompt_hashes"] != prompt_hashes
                or teacher["messages_sha256"] != digest(messages_for(row))
                or teacher["raw_response_sha256"] != digest(teacher["raw_response"].encode())
                or raw_output(teacher["raw_response"], manifest["model"]["alias"]) != row["output"]
                or row.get("filters") != {"passed": True, "issues": []}):
            return ["teacher_provenance_mismatch"]
    except (KeyError, TypeError, ValueError):
        return ["teacher_provenance_missing_or_invalid"]
    return verify_source_record(row, manifest, prompt_hashes, teacher_profile)


def verify_source_record(row, manifest, prompt_hashes, teacher_profile=None):
    if row.get("source_origin") != "teacher_generated":
        return []
    try:
        source = row["source_generation"]
        evidence = source["teacher"]
        spec = json.loads(source["messages"][-1]["content"])
        utterances = raw_output(evidence["raw_response"], manifest["model"]["alias"])["utterances"]
        if (source["version"] != SOURCE_VERSION or evidence["model"] != manifest["model"]
                or (teacher_profile is not None and evidence.get("teacher_profile", teacher_profile) != teacher_profile)
                or evidence["manifest_sha256"] != digest(manifest) or evidence["prompt_hashes"] != prompt_hashes
                or evidence["runtime"]["model_sha256"] != manifest["model"]["gguf_sha256"]
                or evidence["runtime"].get("adapter_sha256") is not None
                or evidence["runtime"].get("chat_template_kwargs") != {"enable_thinking": False}
                or evidence["messages_sha256"] != digest(source["messages"])
                or evidence["raw_response_sha256"] != digest(evidence["raw_response"].encode())
                or spec["locale"] != row["source_locale"] or spec["scenario"] != row["scenario"]
                or utterances[source["index"]] != row["text"]):
            return ["source_generation_provenance_mismatch"]
    except (KeyError, TypeError, ValueError, IndexError):
        return ["source_generation_provenance_missing_or_invalid"]
    return []


def review_display(row):
    return row["output"].get("translated_text") if row["task"] == "translate" else P.completion_text(row["output"])


def export_sample(draft: Path, rows: list[dict], out: Path, seed: int):
    sample = random.Random(seed).sample(rows, min(150, len(rows)))
    key = {"draft_sha256": digest(draft.read_bytes()), "seed": seed, "sample": {}}
    displayed = []
    for index, row in enumerate(sample):
        sid = f"sample-{index + 1:03d}"
        displayed.append({"sample_id": sid, "task": row["task"],
                          "direction": f"{row['source_locale']}->{row.get('target_locale', 'destination')}",
                          "source": row["text"], "output": review_display(row),
                          "ai_verdict": "", "ai_issue": "", "notes": ""})
        key["sample"][sid] = {"id": row["id"], "record_sha256": digest(row)}
    with (out / "sample.review.csv").open("x", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(displayed)
    write_json(out / "sample.key.json", key)
    lines = ["# AI 검토(참고)", "", "원문과 출력을 비교해 좋음/어색함/나쁨을 CSV에 기록하세요. 뜻이 바뀌면 나쁨입니다.",
             "번역문 수정 칸은 없습니다. 검토 의견은 학습 정답으로 가져오지 않습니다.", "",
             "| sample | direction | source | output |", "|---|---|---|---|"]
    for row in displayed:
        lines.append("| " + " | ".join(str(row[k]).replace("|", "\\|").replace("\n", "<br>")
                                           for k in ("sample_id", "direction", "source", "output")) + " |")
    (out / "sample.review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def record_review(draft: Path, review: Path, key_path: Path, out: Path, reviewer: str):
    if not reviewer.strip():
        raise ValueError("AI reviewer name/model is required")
    rows = read_jsonl(draft)
    by_id = {r["id"]: r for r in rows}
    key = json.loads(key_path.read_text(encoding="utf-8"))
    if key["draft_sha256"] != digest(draft.read_bytes()):
        raise ValueError("draft changed after sample export")
    with review.open(encoding="utf-8-sig", newline="") as handle:
        reviewed = list(csv.DictReader(handle))
    if len(reviewed) != len(key["sample"]) or {r["sample_id"] for r in reviewed} != set(key["sample"]):
        raise ValueError("sample review is missing rows or contains duplicates")
    evidence = []
    verdicts = {"좋음": "good", "어색함": "awkward", "나쁨": "bad", "good": "good", "awkward": "awkward", "bad": "bad"}
    for review_row in reviewed:
        selected = key["sample"][review_row["sample_id"]]
        row = by_id[selected["id"]]
        if (digest(row) != selected["record_sha256"] or review_row["source"] != row["text"]
                or review_row["output"] != review_display(row)):
            raise ValueError("sample source or teacher answer was edited")
        verdict = verdicts.get(review_row["ai_verdict"].strip().lower())
        if verdict not in ("good", "awkward"):
            raise ValueError("all sample rows need AI review; any bad row blocks this draft")
        evidence.append({**selected, "verdict": verdict, "issue": review_row["ai_issue"], "notes": review_row["notes"]})
    result = {"kind": "ai_sample_review", "status": REVIEWED, "human_reviewed": False,
              "reviewer": reviewer, "draft_sha256": key["draft_sha256"], "seed": key["seed"],
              "review_csv_sha256": digest(review.read_bytes()), "sample": evidence}
    verify_ai_review(draft, rows, result)
    if out.exists():
        raise FileExistsError(out)
    write_json(out, result)
    print(f"AI 검토(참고): {len(evidence)} sampled rows; {REVIEWED} -> {out}")


def verify_ai_review(draft: Path, rows: list[dict], evidence: dict):
    if (evidence.get("kind") != "ai_sample_review" or evidence.get("status") != REVIEWED
            or evidence.get("human_reviewed") is not False or not evidence.get("reviewer")
            or evidence.get("draft_sha256") != digest(draft.read_bytes())):
        raise ValueError("AI sample evidence is missing or bound to a different draft")
    expected = {r["id"]: digest(r) for r in random.Random(evidence["seed"]).sample(rows, min(150, len(rows)))}
    sampled = evidence["sample"]
    if (len(sampled) != len(expected) or {r["id"]: r["record_sha256"] for r in sampled} != expected
            or any(r["verdict"] not in ("good", "awkward") for r in sampled)):
        raise ValueError("AI sample evidence does not match the complete random sample")


def source_messages(locale, scenario, count, batch, already_used=()):
    return [{"role": "system", "content":
        "Create independent, everyday spoken turns for a rental-car assistant. Do not translate. "
        "Write what someone actually says, not a description of their intention or a written explanation. "
        "Prefer short, plain speech, contractions where natural, varied openings and sentence lengths. "
        "Occasional hesitation or fragments are fine when the object and meaning remain clear without hidden context. "
        "Each item must involve a different concrete situation and wording; never make number/name/word swaps "
        "of one sentence or paraphrase the same request repeatedly. Stay within the requested topic. "
        "already_used contains accepted turns from earlier batches in this locale and scenario: do not restate "
        "any of those requests or meanings with different wording. Use no examples, quotations or benchmark material. "
        "Use only the requested locale: polite -요 speech in Korean, everyday usted in Latin American Spanish, "
        "plain conversational English. Do not force every item to contain a number or a negation. Return JSON only."},
        {"role": "user", "content": P._dump({"locale": locale, "scenario": scenario, "count": count,
            "speaker": "local employee" if locale == "ko-KR" else "visiting driver",
            "topic": SOURCE_TOPICS[scenario][batch % len(SOURCE_TOPICS[scenario])],
            "already_used": list(already_used), "turn_form": SOURCE_FORMS[batch % len(SOURCE_FORMS)],
            "focus": SOURCE_FOCUS[(batch + SCENARIOS.index(scenario)) % len(SOURCE_FOCUS)],
            "independent_batch": batch})}]


async def source_augmentation(teacher: Teacher, out: Path, protected: set[str], count: int, seed=42) -> list[dict]:
    rows, seen, rejected = [], set(), Counter()
    accepted_by_topic = {locale: {scenario: dict.fromkeys(SOURCE_TOPICS[scenario], 0)
                                 for scenario in SCENARIOS} for locale in SOURCE_LOCALES}
    # Dedup before target expansion: regional aliases and numeric swaps do not buy diversity.
    # shortcut: fingerprints/lexical similarity miss semantic paraphrases; inspect the blinded source sample too.
    with (out / "source_generation.jsonl").open("x", encoding="utf-8") as log:
        for locale_index, locale in enumerate(SOURCE_LOCALES):
            for scenario in SCENARIOS:
                accepted = 0
                already_used = []
                # Fixed two-times proposal budget; exhaustion stops instead of padding with templates.
                max_batches = 2 * ((count + SOURCE_BATCH_SIZE - 1) // SOURCE_BATCH_SIZE)
                for batch in range(max_batches):
                    size = min(SOURCE_BATCH_SIZE, count - accepted)
                    topic = SOURCE_TOPICS[scenario][batch % len(SOURCE_TOPICS[scenario])]
                    messages = source_messages(locale, scenario, size, batch, already_used)
                    schema = {"type": "object", "properties": {"utterances": {"type": "array", "items": {"type": "string"},
                              "minItems": size, "maxItems": size}}, "required": ["utterances"], "additionalProperties": False}
                    sampling = {"temperature": .7, "top_p": .8, "top_k": 20, "min_p": 0,
                                "seed": seed + locale_index * 10000 + SCENARIOS.index(scenario) * 1000 + batch}
                    output, provenance = await teacher.call(messages, schema, SOURCE_MAX_TOKENS, sampling)
                    utterances = output.get("utterances")
                    if not isinstance(utterances, list) or len(utterances) != size:
                        raise ValueError("teacher source generation returned the wrong count")
                    decisions = []
                    for index, text in enumerate(utterances):
                        reason = None
                        if not isinstance(text, str) or not 5 <= len(text) <= 400 or looks_like_other_language(text, locale):
                            reason = "invalid_source"
                        elif overlaps(text, locale, protected):
                            reason = "boundary_source"
                        elif overlaps(text, locale, seen):
                            reason = "duplicate_generated_source"
                        decisions.append(reason or "accepted")
                        if reason:
                            rejected[reason] += 1
                            continue
                        seen.add(fingerprint(text, locale))
                        accepted += 1
                        already_used.append(text)
                        accepted_by_topic[locale][scenario][topic] += 1
                        for source, target in DIRECTIONS:
                            if source == locale:
                                rows.append({"id": "teacher-source-" + digest([locale, target, text])[:16],
                                    "group_id": f"teacher-source-{SOURCE_VERSION}-{locale}-{scenario}-{batch}",
                                    "task": "translate", "source_locale": locale, "target_locale": target,
                                    "scenario": scenario, "text": text, "source_origin": "teacher_generated",
                                    "source_generation": {"version": SOURCE_VERSION, "messages": messages,
                                                          "teacher": provenance, "index": index}})
                    log.write(json.dumps({"version": SOURCE_VERSION, "messages": messages, "sampling": sampling,
                        "teacher": provenance, "output": output, "decisions": decisions}, ensure_ascii=False) + "\n")
                    log.flush()
                    print(f"sources {locale}/{scenario}: {accepted}/{count}", flush=True)
                    if accepted == count:
                        break
                if accepted < count:
                    raise ValueError(f"insufficient diverse sources for {locale}/{scenario}: {accepted}/{count}; "
                                     "inspect source_generation.jsonl; templates were not substituted")
    write_json(out / "source_summary.json", {"version": SOURCE_VERSION, "requested_per_locale_scenario": count,
               "unique_sources": len(seen), "translation_rows": len(rows), "rejections": dict(rejected),
               "groups": len({r["group_id"] for r in rows}), "accepted_by_topic": accepted_by_topic})
    return rows


def balanced_sources(rows, seed):
    """Retain teacher originals; bound supplemental rows before and after safety filtering."""
    teacher = [r for r in rows if r.get("source_origin") == "teacher_generated"]
    extra = [r for r in rows if r.get("source_origin") != "teacher_generated"]
    random.Random(seed).shuffle(extra)
    # Keep destination rehearsal when supplementary rows need trimming.
    extra.sort(key=lambda r: r.get("task") != "destination")
    selected = teacher + extra[:len(teacher) // 4]
    check_source_mix(selected)
    return selected


async def generate(args):
    settings = Settings.from_env()
    assert_teacher(settings.manifest, args.teacher)
    if settings.model_profile != args.teacher or settings.model_backend != "llama":
        raise ValueError(f"set MODEL_PROFILE={args.teacher} and MODEL_BACKEND=llama")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in ("distill.draft.jsonl", "distill.rejected.jsonl", "source_generation.jsonl", "source_summary.json", "pool.jsonl",
                 "sample.review.csv", "sample.review.md", "sample.key.json", "summary.json", "summary.md"):
        if (out / name).exists():
            raise FileExistsError(out / name)
    client = LlamaClient(args.model_url or settings.model_base_url, settings.model_id, settings.manifest)
    hashes = server_prompt_hashes(ROOT)
    teacher = Teacher(client, settings.manifest, hashes, args.teacher)
    try:
        await client.prepare_runtime()
        identity = await client.runtime_identity()
        async with httpx.AsyncClient(timeout=300) as http:
            ready = await http.get(args.url.rstrip("/") + "/health/ready")
            ready.raise_for_status()
            health = ready.json()
        if (not health.get("ready") or health.get("backend") != "llama" or health.get("model_profile") != args.teacher
                or health.get("model_runtime") != identity or health.get("artifact_hashes") != artifact_hashes()):
            raise ValueError("AI ready identity/artifacts differ from the verified teacher runtime")
        protected = protected_templates(ROOT)
        pool = await source_augmentation(teacher, out, protected, args.source_count, args.seed)
        pool += read_jsonl(Path(args.pool))
        pool = balanced_sources(pool, args.seed)
        pool = pool[:args.limit] if args.limit else pool
        selected, seen, groups = [], set(), {}
        for row in pool:
            row = dict(row, task=row.get("task", "translate"))
            key = (row["source_locale"], row.get("target_locale"), row["text"].casefold())
            if key in seen or overlaps(row["text"], row["source_locale"], protected):
                continue
            seen.add(key)
            fp = fingerprint(row["text"], row["source_locale"])
            row["group_id"] = groups.setdefault(fp, row["group_id"])
            selected.append(row)
        (out / "pool.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in selected), encoding="utf-8")
        accepted, reasons = [], Counter()
        with (out / "distill.draft.jsonl").open("x", encoding="utf-8") as draft, \
                (out / "distill.rejected.jsonl").open("x", encoding="utf-8") as rejected:
            for index, row in enumerate(selected):
                messages = messages_for(row)
                translation = row["task"] == "translate"
                try:
                    output, provenance = await teacher.call(messages,
                        P.TRANSLATE_SCHEMA if translation else P.DESTINATION_SCHEMA,
                        P.TRANSLATE_MAX_TOKENS if translation else P.DESTINATION_MAX_TOKENS,
                        P.TRANSLATE_SAMPLING if translation else P.DESTINATION_SAMPLING)
                    record = {**row, "output": output, "source": "teacher_distillation", "teacher": provenance,
                              "prompt_version": P.prompt("translate" if translation else "destination")[0],
                              "review_status": PENDING}
                    issues = filter_issues(record, protected)
                    record["filters"] = {"passed": not issues, "issues": issues}
                    if issues:
                        reasons.update(issues)
                        rejected.write(json.dumps(record, ensure_ascii=False) + "\n")
                    else:
                        accepted.append(record)
                        draft.write(json.dumps(record, ensure_ascii=False) + "\n")
                except ModelError as error:
                    reasons.update([error.code])
                    rejected.write(json.dumps({**row, "error": error.code, "raw_response": teacher.raw}, ensure_ascii=False) + "\n")
                print(f"[{index + 1}/{len(selected)}] accepted={len(accepted)} rejected={index + 1 - len(accepted)}", flush=True)
        if hashes != server_prompt_hashes(ROOT):
            raise ValueError("server prompt artifacts changed during generation")
        before_mix = source_mix(accepted)
        retained = balanced_sources(accepted, args.seed)
        retained_ids = {r["id"] for r in retained}
        with (out / "distill.rejected.jsonl").open("a", encoding="utf-8") as rejected:
            for record in accepted:
                if record["id"] not in retained_ids:
                    rejected.write(json.dumps({**record, "selection_rejection": "supplemental_share_cap"}, ensure_ascii=False) + "\n")
                    reasons.update(["supplemental_share_cap"])
        accepted = retained
        (out / "distill.draft.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in accepted), encoding="utf-8")
        export_sample(out / "distill.draft.jsonl", accepted, out, args.seed)
        summary = {"source_rows": len(selected), "accepted": len(accepted), "rejected": len(selected) - len(accepted),
                   "source_pool_sha256": digest(Path(args.pool).read_bytes()),
                   "source_code_sha256": {name: digest((ROOT / "train/sources" / name).read_bytes())
                                          for name in ("make_pool.py", "source_templates.py")},
                   "rejection_reasons": dict(reasons), "draft_sha256": digest((out / "distill.draft.jsonl").read_bytes()),
                   "teacher_profile": args.teacher, "teacher_model": settings.manifest["model"],
                   "runtime": identity, "prompt_hashes": hashes,
                   "review_status": PENDING, "ai_sample_size": min(150, len(accepted)),
                   "source_mix_before_translation": source_mix(selected), "source_mix_after_safety": before_mix,
                   "source_mix_final": check_source_mix(accepted),
                   "tasks": dict(Counter(r["task"] for r in accepted)),
                   "directions": dict(Counter(f"{r['source_locale']}->{r.get('target_locale', 'destination')}" for r in accepted))}
        write_json(out / "summary.json", summary)
        (out / "summary.md").write_text("# Distillation generation\n\n" +
            f"Teacher: {args.teacher} ({settings.manifest['model']['base_repo']}).\n\n" +
            f"Sources: {len(selected)}; accepted: {len(accepted)}; rejected: {len(selected) - len(accepted)}.\n\n" +
            f"Teacher-authored source share: {source_mix(accepted)['teacher_share']:.1%}; " +
            f"unique source fingerprints: {source_mix(accepted)['unique_sources']}; groups: {source_mix(accepted)['groups']}.\n\n" +
            "AI 검토(참고) 대기: sample.review.csv. 학습 정답은 원본 선생 모델 출력이며 수정 번역은 가져오지 않습니다.\n",
            encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        await client.aclose()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="stage", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--teacher", choices=TEACHER_PROFILES, default="14b-q4")
    gen.add_argument("--pool", type=Path, default=ROOT / "train/sources/pool.jsonl")
    gen.add_argument("--out", type=Path, required=True)
    gen.add_argument("--url", default=os.environ.get("AI_URL", "http://127.0.0.1:8000"))
    gen.add_argument("--model-url")
    gen.add_argument("--limit", type=int, default=0, help="smoke subset only")
    gen.add_argument("--source-count", type=int, default=80, help="unique teacher utterances per locale/scenario, in batches of at most 10")
    gen.add_argument("--seed", type=int, default=42)
    review = sub.add_parser("review")
    for name in ("draft", "review", "key", "out"):
        review.add_argument(f"--{name}", type=Path, required=True)
    review.add_argument("--reviewer", required=True, help="AI reviewer model/name; not a human approval")
    args = parser.parse_args()
    if args.stage == "generate":
        if args.source_count <= 0 or args.limit < 0:
            parser.error("source-count must be positive and limit must be nonnegative")
        asyncio.run(generate(args))
    else:
        record_review(args.draft, args.review, args.key, args.out, args.reviewer)


if __name__ == "__main__":
    main()
