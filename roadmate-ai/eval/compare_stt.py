# 평가
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.evaluate import judge  # noqa: E402
from eval.export_review import load_run, output  # noqa: E402
from eval.stt_format import DATASETS, STT_FORMAT_VERSION, normalize_source, variant_bytes  # noqa: E402


NOTICE = ("질문·부탁 표지는 표현을 찾는 참고 신호이며 의미 정확도 판정이 아닙니다. "
          "물음표 변화만으로 뜻이 바뀌었다고 판정하지 않습니다. "
          "502·확인 질문 등 양쪽 모두 성공한 번역이 아닌 행은 표지 비교에서 제외합니다.")
NUMERIC_DEFINITION = ("원문에 Unicode 십진 숫자(\\d, Nd)가 하나 이상 있는 행입니다. "
                      "한국어의 5만 원·4시, 전각·아라비아 숫자도 포함하며 three·삼 같은 숫자 단어만 있는 행은 제외합니다.")
CUES = {
    "en": {"question": r"^\s*(?:can|could|would|will|do|does|did|is|are|was|were|have|has|may|what|where|when|why|how|which)\b",
           "request": r"\bplease\b|\b(?:could|would|can|will)\s+you\b"},
    "ko": {"question": r"(?:나요|까요|인가요|신가요|습니까|주시겠어요)(?=\s|[?.!？。！]|$)",
           "request": r"(?:주세요|주십시오|주시겠어요|부탁드려요)(?=\s|[?.!？。！]|$)"},
    "es": {"question": r"^\s*¿?\s*(?:puedo|puede|pueden|podría|dónde|cuánto|cuánta|cuántos|cuántas|qué|cómo|cuál|hay)\b",
           "request": r"\bpor\s+favor\b|\b(?:deme|dígame|ayúdeme|indíqueme)\b"},
}
FIELDS = ["dataset", "id", "task", "direction", "original_source", "stt_source", "reference", "numeric_source",
          "original_output", "stt_output", "output_changed", "original_http", "stt_http",
          "original_status", "stt_status", "original_FAIL", "stt_FAIL", "original_flags", "stt_flags",
          "signals_eligible", "original_signal", "stt_signal", "original_question_punctuation",
          "stt_question_punctuation", "signal_changed", "speech_act_changed", "notes"]
STABLE = ("instance_id", "backend", "model_id", "model_profile", "engine_bundle", "versions",
          "runtime_config", "artifact_hashes", "model_runtime")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def is_hash(value):
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def speech_act_cues(text, locale):
    patterns = CUES.get(locale.split("-")[0], {})
    found = {name: bool(re.search(pattern, text, re.I)) for name, pattern in patterns.items()}
    label = "both" if all(found.values()) and found else next((k for k, v in found.items() if v), "unmarked")
    return {"label": label, "question_punctuation": bool(re.search(r"[?？¿]", text))}


def strict_run(folder, expected, dataset_hash, allow_mock):
    meta = json.loads((Path(folder) / "run.json").read_text(encoding="utf-8"))
    if meta.get("dataset") not in DATASETS:
        raise ValueError("Only canonical general/general_es/naturalness.dev dataset basenames are allowed")
    if not is_hash(meta.get("results_sha256")) or meta.get("dataset_sha256") != dataset_hash:
        raise ValueError("Missing results hash or changed dataset hash")
    records, provenance = load_run(folder)
    if provenance["metadata"] != meta:
        raise ValueError("Run metadata changed while loading")
    if set(records) != set(expected) or any(records[cid]["case"] != case for cid, case in expected.items()):
        raise ValueError("Missing IDs or changed case source/expectations")
    ready, end = meta.get("ready", {}), meta.get("ready_end", {})
    profile, model = ready.get("model_profile"), ready.get("model_id")
    runtime, config, artifacts = ready.get("model_runtime"), ready.get("runtime_config", {}), ready.get("artifact_hashes")
    if (ready.get("ready") is not True or end.get("ready") is not True
            or not isinstance(ready.get("instance_id"), str) or not ready["instance_id"]
            or any(ready.get(k) != end.get(k) for k in STABLE)
            or not profile or not model or meta.get("model_profile") != profile
            or meta.get("model_profile_consistent") is not True or config.get("model_profile") != profile
            or type(config.get("model_timeout_s")) not in (int, float) or config["model_timeout_s"] <= 0
            or type(config.get("max_text_chars")) is not int or config["max_text_chars"] <= 0
            or not ready.get("engine_bundle") or not isinstance(ready.get("versions"), dict) or not ready["versions"]
            or not isinstance(artifacts, dict) or not artifacts
            or any(not is_hash(v) for v in artifacts.values())):
        raise ValueError("Missing or changed ready/ready_end model/profile/artifact evidence")
    mock = ready.get("backend") == "mock" or model == "mock"
    if mock:
        if not allow_mock or ready.get("backend") != "mock" or model != "mock":
            raise ValueError("Mock runs require --allow-mock; they are not quality evidence")
    elif (ready.get("backend") != "llama" or config.get("model_id") != model
          or not isinstance(runtime, dict) or not is_hash(runtime.get("model_sha256"))
          or runtime.get("adapter_sha256") is not None and not is_hash(runtime["adapter_sha256"])
          or not is_hash(runtime.get("chat_template_sha256")) or not isinstance(runtime.get("chat_template_kwargs"), dict)
          or not runtime.get("process") or not runtime.get("build") or not runtime.get("default_parameters")
          or type(runtime.get("context")) is not int or runtime["context"] <= 0
          or type(runtime.get("slots")) is not int or runtime["slots"] <= 0):
        raise ValueError("Verified real model runtime required")
    for cid, row in records.items():
        passed, flags = judge(row["case"], row["http"], row["body"])
        if type(row.get("pass")) is not bool or row["pass"] != passed or row.get("flags") != flags:
            raise ValueError(f"Stored judgment mismatch: {cid}")
        if row["http"] == 200 and row["body"].get("model_id") != model:
            raise ValueError(f"Output model mismatch: {cid}")
    signature = {k: ready.get(k) for k in STABLE if k not in ("instance_id", "model_runtime")}
    signature["model_runtime"] = {k: v for k, v in (runtime or {}).items() if k != "process"}
    return records, provenance, signature, mock


def compare_pair(original_folder, stt_folder, allow_mock=False):
    meta = json.loads((Path(original_folder) / "run.json").read_text(encoding="utf-8"))
    dataset = meta.get("dataset")
    if dataset not in DATASETS:
        raise ValueError("Only canonical general/general_es/naturalness.dev dataset basenames are allowed")
    canonical = ROOT.resolve() / "eval" / dataset
    if canonical.resolve(strict=True) != canonical:
        raise ValueError("Canonical dataset must not redirect to another file")
    raw = canonical.read_bytes()
    cases = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
    expected = {case["id"]: case for case in cases}
    if not expected or len(expected) != len(cases):
        raise ValueError("Canonical dataset has missing or duplicate IDs")
    variant = {cid: {**case, "text": normalize_source(case["text"], case["source_locale"])} for cid, case in expected.items()}
    a, pa, identity, mock = strict_run(original_folder, expected, sha(raw), allow_mock)
    b, pb, other_identity, other_mock = strict_run(stt_folder, variant, sha(variant_bytes(cases)), allow_mock)
    if pb["metadata"]["dataset"] != dataset or identity != other_identity or mock != other_mock:
        raise ValueError("Paired dataset/model/profile/artifacts/runtime mismatch")
    rows = []
    for cid, original in a.items():
        transformed, case = b[cid], original["case"]
        left, ls = output(original)
        right, rs = output(transformed)
        if case["task"] != "translate":
            left, right = (json.dumps({k: row["body"].get(k) for k in
                ("status", "intent", "query_ko_candidate", "candidates", "error_code")}, ensure_ascii=False)
                for row in (original, transformed))
        eligible = case["task"] == "translate" and ls["status"] == rs["status"] == "ok"
        cues = [speech_act_cues(text, case["target_locale"]) for text in (left, right)] if eligible else [None, None]
        states = [r["body"].get("status", "missing") if r["http"] == 200 else f"http_{r['http']}"
                  for r in (original, transformed)]
        rows.append(dict(zip(FIELDS, [dataset, cid, case["task"], f"{case['source_locale']}->{case.get('target_locale', '')}",
            case["text"], transformed["case"]["text"], case.get("reference", ""), bool(re.search(r"\d", case["text"])),
            left, right, left != right,
            original["http"], transformed["http"], *states, not original["pass"], not transformed["pass"],
            " | ".join(original["flags"]), " | ".join(transformed["flags"]), eligible,
            cues[0]["label"] if eligible else "unavailable", cues[1]["label"] if eligible else "unavailable",
            cues[0]["question_punctuation"] if eligible else "", cues[1]["question_punctuation"] if eligible else "",
            cues[0]["label"] != cues[1]["label"] if eligible else "", "", ""])))
    return rows, {"dataset": dataset, "original": pa, "stt": pb, "signature": identity, "mock": mock}


def measures(rows):
    eligible = [r for r in rows if r["signals_eligible"]]
    fail, blocked = {}, {}
    for side in ("original", "stt"):
        fail[side] = sum(r[side + "_FAIL"] for r in rows)
        blocked[side] = sum(r[side + "_http"] == 502 for r in rows)
    fail["delta"] = fail["stt"] - fail["original"]
    blocked["delta"] = blocked["stt"] - blocked["original"]
    key = lambda r: f"{r['dataset']}:{r['id']}"
    return {"rows": len(rows), "FAIL": fail, "502": blocked,
        "new_failures": [key(r) for r in rows if not r["original_FAIL"] and r["stt_FAIL"]],
        "recovered": [key(r) for r in rows if r["original_FAIL"] and not r["stt_FAIL"]],
        "502_transitions": dict(Counter(f"{r['original_http'] == 502}->{r['stt_http'] == 502}" for r in rows)),
        "status_transitions": dict(Counter(f"{r['original_status']}->{r['stt_status']}" for r in rows)),
        "signals": {"eligible_pairs": len(eligible), "label_changed": sum(r["signal_changed"] for r in eligible),
            "question_punctuation_changed": sum(r["original_question_punctuation"] != r["stt_question_punctuation"] for r in eligible),
            "question_loss": sum(r["original_question_punctuation"] and not r["stt_question_punctuation"] for r in eligible),
            "question_addition": sum(not r["original_question_punctuation"] and r["stt_question_punctuation"] for r in eligible),
            "question_loss_rows": [key(r) for r in eligible if r["original_question_punctuation"] and not r["stt_question_punctuation"]],
            "question_addition_rows": [key(r) for r in eligible if not r["original_question_punctuation"] and r["stt_question_punctuation"]],
            "label_transitions": dict(Counter(f"{r['original_signal']}->{r['stt_signal']}" for r in eligible)),
            "changed_outputs": sum(r["output_changed"] for r in eligible),
            "advisory_rows": [key(r) for r in eligible if r["signal_changed"] or
                              r["original_question_punctuation"] != r["stt_question_punctuation"]]}}


def cohort_measures(rows):
    return {**measures(rows), "numeric": measures([row for row in rows if row["numeric_source"]]),
            "directions": {direction: measures([row for row in rows if row["direction"] == direction])
                           for direction in sorted({row["direction"] for row in rows})}}


def markdown(report):
    lines = ["# STT 형식 비교", "", NOTICE, "", f"- 정규화 버전: {report['transform_version']}",
             f"- 실행 종류: {'MOCK 계약 시험 — 품질 근거 아님' if report['mock'] else '실제 모델 기록 비교'}",
             "- changes.csv에는 표지가 같아도 달라진 번역을 놓치지 않도록 모든 행을 담았습니다.",
             "- speech_act_changed·notes는 사람이 원문·출력·reference를 보고 채우는 빈 검토 칸입니다.", "",
             "| dataset | n | FAIL 원본→STT | Δ | 502 원본→STT | Δ |", "|---|---:|---:|---:|---:|---:|"]
    for name, stats in [*report["datasets"].items(), ("TOTAL", report["aggregate"])]:
        lines.append(f"| {name} | {stats['rows']} | {stats['FAIL']['original']}→{stats['FAIL']['stt']} | {stats['FAIL']['delta']:+d} | "
                     f"{stats['502']['original']}→{stats['502']['stt']} | {stats['502']['delta']:+d} |")
    for name, stats in [*report["datasets"].items(), ("TOTAL", report["aggregate"])]:
        lines += [f"\n- {name} 새 FAIL: {', '.join(stats['new_failures']) or '없음'}",
                  f"- {name} 회복: {', '.join(stats['recovered']) or '없음'}",
                  f"- {name} 502 전이(False=502 아님): {json.dumps(stats['502_transitions'], ensure_ascii=False)}"]
    lines += ["", "## 숫자가 포함된 원문만 비교", "", report["numeric_definition"], "",
              "| dataset | 숫자 행 n | FAIL 원본→STT | Δ | 502 원본→STT | Δ |", "|---|---:|---:|---:|---:|---:|"]
    for name, stats in [*report["datasets"].items(), ("TOTAL", report["aggregate"])]:
        nums = stats["numeric"]
        lines.append(f"| {name} | {nums['rows']} | {nums['FAIL']['original']}→{nums['FAIL']['stt']} | {nums['FAIL']['delta']:+d} | "
                     f"{nums['502']['original']}→{nums['502']['stt']} | {nums['502']['delta']:+d} |")
    for name, stats in [*report["datasets"].items(), ("TOTAL", report["aggregate"])]:
        nums, sig = stats["numeric"], stats["numeric"]["signals"]
        lines += [f"\n- {name} 숫자 행 새 FAIL: {', '.join(nums['new_failures']) or '없음'}",
                  f"- {name} 숫자 행 회복: {', '.join(nums['recovered']) or '없음'}",
                  f"- {name} 숫자 행 502 전이(False=502 아님): {json.dumps(nums['502_transitions'], ensure_ascii=False)}",
                  f"- {name} 숫자 행 표현 표지: 성공 번역 쌍 {sig['eligible_pairs']}, 출력 변경 {sig['changed_outputs']}, "
                  f"표지 변경 {sig['label_changed']}, 물음표 변화 {sig['question_punctuation_changed']}",
                  f"- 표현 전이: {json.dumps(sig['label_transitions'], ensure_ascii=False)}",
                  f"- 참고 행: {', '.join(sig['advisory_rows']) or '없음'}"]
    lines += ["", "## 질문·부탁 표현 표지 (참고)", "",
              "question/request/both/unmarked는 아래 표현 검색 결과입니다. could you처럼 두 표지가 함께 나타날 수 있습니다.",
              "물음표는 별도 집계이며 question 표지를 만드는 조건이 아닙니다. 이 집계에는 의미 변화 건수가 없습니다."]
    lines += ["", "원본 출력의 물음표 유무를 기준으로 질문 소실(있음→없음)·추가(없음→있음)를 셉니다.",
              "502·확인 질문은 성공 번역 쌍에서 제외되며, 제외된 행을 개선으로 계산하지 않습니다.", "",
              "| 방향 | 성공 번역 쌍 | 질문 소실 | 질문 추가 |", "|---|---:|---:|---:|"]
    for direction, stats in [*report["aggregate"]["directions"].items(), ("TOTAL", report["aggregate"])]:
        sig = stats["signals"]
        lines.append(f"| {direction} | {sig['eligible_pairs']} | {sig['question_loss']} | {sig['question_addition']} |")
    for name, stats in [*report["datasets"].items(), ("TOTAL", report["aggregate"])]:
        sig = stats["signals"]
        lines += [f"\n- {name}: 성공 번역 쌍 {sig['eligible_pairs']}, 출력 변경 {sig['changed_outputs']}, "
                  f"표지 변경 {sig['label_changed']}, 물음표 변화 {sig['question_punctuation_changed']}",
                  f"- 표현 전이: {json.dumps(sig['label_transitions'], ensure_ascii=False)}",
                  f"- 참고 행: {', '.join(sig['advisory_rows']) or '없음'}"]
    lines += ["", "사용한 언어별 표지 정규식:", "", "```json", json.dumps(CUES, ensure_ascii=False, indent=2), "```"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description='원본과 STT 형식 평가 결과를 비교합니다.')
    parser.add_argument("--pair", nargs=2, action="append", required=True, metavar=("ORIGINAL_RUN", "STT_RUN"))
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--allow-mock", action="store_true", help="Contract checks only; never quality evidence")
    args = parser.parse_args(argv)
    try:
        if args.out_dir.exists():
            raise ValueError("Output directory already exists; previous reports were preserved")
        all_rows, pairs, datasets = [], [], {}
        for original, stt in args.pair:
            rows, pair = compare_pair(original, stt, args.allow_mock)
            if pair["dataset"] in datasets or pairs and pair["signature"] != pairs[0]["signature"]:
                raise ValueError("Duplicate dataset or mixed configurations across pairs")
            datasets[pair["dataset"]] = cohort_measures(rows)
            all_rows.extend(rows)
            pairs.append(pair)
        report = {"transform_version": STT_FORMAT_VERSION, "notice": NOTICE, "mock": any(p["mock"] for p in pairs),
                  "cue_definitions": CUES, "numeric_definition": NUMERIC_DEFINITION,
                  "datasets": datasets, "aggregate": cohort_measures(all_rows), "pairs": pairs}
        args.out_dir.mkdir(parents=True, exist_ok=False)
        with (args.out_dir / "changes.csv").open("x", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(all_rows)
        (args.out_dir / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        text = markdown(report)
        (args.out_dir / "summary.md").write_text(text, encoding="utf-8")
        print(text)
        print(f"saved: {args.out_dir.resolve()}")
        return 0
    except (ValueError, KeyError, TypeError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
