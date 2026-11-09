"""평가: 실행 중인 AI 서버(HTTP)로 eval jsonl을 돌려 자동 지표와 사람 검토용 CSV를 만든다.

사용: .venv\\Scripts\\python.exe eval\\evaluate.py --file eval\\dev.jsonl --label q4-prompt-v1
잠근 시험(eval\\test.locked.jsonl)은 설정을 확정한 뒤 한 번만 실행한다.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import statistics
import sys
import time
import uuid
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import artifact_hashes, load_dotenv  # noqa: E402

load_dotenv()


def has(text: str, term: str) -> bool:
    return any(t.lower() in (text or "").lower() for t in term.split("|"))


def judge(case: dict, code: int, body: dict) -> tuple[bool, list[str]]:
    flags = []
    if code != 200:
        return False, [f"http_{code}:{body.get('error_code')}"]
    if case["task"] == "translate":
        want = case.get("expect_status", "ok")
        if body["status"] != want:
            return False, [f"status:{body['status']}"]
        out = body.get("translated_text") or ""
        flags += [f"missing:{t}" for t in case.get("must_include", []) if want == "ok" and not has(out, t)]
        flags += [f"forbidden:{t}" for t in case.get("must_not_include", []) if has(out, t)]
        return not flags, flags
    top = body["candidates"][0]["poi_id"] if body.get("candidates") else None
    if case.get("expect_status") and body["status"] != case["expect_status"]:
        flags.append(f"status:{body['status']}")
    if case.get("expect_poi") and top != case["expect_poi"]:
        flags.append(f"poi:{top}")
    if case.get("expect_poi_any") and top not in case["expect_poi_any"]:
        flags.append(f"poi:{top}")
    if case.get("expect_confirm") and not (body["needs_confirmation"] or body["status"] in ("clarification_required", "not_found")):
        flags.append("no_confirmation")
    return not flags, flags


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def attach_attempts(rows: list[dict], path: Path) -> dict:
    """서버의 opt-in 로그만 읽는다. 검사 통과는 의미 정확도와 별개다."""
    by_id = {}
    stats = Counter(translate_cases=0, traced_cases=0, retried=0, recovered=0, identical=0,
                    incomplete=0, log_errors=0, first_attempts=0, first_wrong_language=0)
    first_by_direction = {}
    retry_by_first_issue = {kind: Counter(retried=0, recovered=0, identical=0)
                            for kind in ("wrong_language", "other", "mixed")}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        lines = []
        stats["log_errors"] += 1
    for line in lines:
        if "translation_attempt " not in line:
            continue
        prefix, payload = line.split("translation_attempt ", 1)
        if (prefix.strip() not in ("", "WARNING:", "WARNING:roadmate.ai:") and not re.fullmatch(
                r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} WARNING roadmate\.ai\s+", prefix)):
            continue
        try:
            record = json.loads(payload)
            if (not isinstance(record, dict) or not isinstance(record.get("request_id"), str)
                    or type(record.get("attempt")) is not int or record["attempt"] not in (1, 2)
                    or not isinstance(record.get("issues"), list)
                    or not all(isinstance(i, str) for i in record["issues"])
                    or not (record.get("output") is None or isinstance(record["output"], dict))):
                raise ValueError("invalid attempt record")
        except (ValueError, TypeError):
            stats["log_errors"] += 1
            continue
        by_id.setdefault(record["request_id"], []).append(record)
    for row in rows:
        if row["case"]["task"] != "translate":
            continue
        stats["translate_cases"] += 1
        case = row["case"]
        direction = f"{case.get('source_locale', '?')}>{case.get('target_locale', '?')}"
        first_stats = first_by_direction.setdefault(direction, Counter(cases=0, traced=0, wrong_language=0))
        first_stats["cases"] += 1
        attempts = by_id.get(row["request_id"], [])
        row["translation_attempts"] = attempts
        if not attempts:
            continue
        stats["traced_cases"] += 1
        if [a["attempt"] for a in attempts] not in ([1], [1, 2]):
            stats["incomplete"] += 1
            continue
        first_issues = {issue.split(":", 1)[0] for issue in attempts[0]["issues"]}
        wrong_language = "wrong_language" in first_issues
        stats["first_attempts"] += 1
        stats["first_wrong_language"] += wrong_language
        first_stats["traced"] += 1
        first_stats["wrong_language"] += wrong_language
        if len(attempts) == 2:
            first, second = attempts
            kind = "mixed" if wrong_language and len(first_issues) > 1 else "wrong_language" if wrong_language else "other"
            for counts in (stats, retry_by_first_issue[kind]):
                counts["retried"] += 1
                counts["identical"] += first["output"] is not None and first["output"] == second["output"]
                counts["recovered"] += (bool(first["issues"]) and not second["issues"]
                                        and (second["output"] or {}).get("status") == "ok")
    return {**stats, "first_by_direction": first_by_direction, "retry_by_first_issue": retry_by_first_issue}


def retry_summary(stats: dict) -> list[str]:
    n = stats["retried"]
    rate = f"{stats['recovered'] / n:.1%}" if n else "N/A"
    lines = [f"- retry trace coverage: {stats['traced_cases']}/{stats['translate_cases']} "
             f"(incomplete: {stats['incomplete']}, log errors: {stats['log_errors']})",
             f"- first-attempt wrong_language: {stats['first_wrong_language']}/{stats['first_attempts']} traced first attempts",
             f"- retry validator recovery: {stats['recovered']}/{n} ({rate})",
             f"- retry identical output: {stats['identical']}/{n}"]
    for direction, counts in sorted(stats["first_by_direction"].items()):
        lines.append(f"- first-attempt wrong_language {direction}: {counts['wrong_language']}/{counts['traced']} "
                     f"(first-attempt coverage: {counts['traced']}/{counts['cases']})")
    lines += ["", "| first failure | retries | validator recovery | identical output |",
              "|---|---:|---:|---:|"]
    for kind, counts in stats["retry_by_first_issue"].items():
        n = counts["retried"]
        rate = f"{counts['recovered'] / n:.1%}" if n else "N/A"
        lines.append(f"| {kind} | {n} | {counts['recovered']}/{n} ({rate}) | {counts['identical']} |")
    lines += ["", "wrong_language: 언어 오류만, other: 그 외 검사 오류, mixed: 두 종류가 함께 발생한 첫 시도."]
    return lines


def final_input_digest(path: Path) -> str:
    # 검수 메타데이터나 행 순서만 바꿔 같은 시험을 다시 고르지 못하게 입력으로 식별한다.
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    inputs = [json.dumps({k: c.get(k) for k in ("task", "source_locale", "target_locale", "scenario", "text")},
                         ensure_ascii=False, sort_keys=True) for c in cases]
    return hashlib.sha256("\n".join(sorted(inputs)).encode("utf-8")).hexdigest()


def reserve_final(path: Path, ready: dict) -> str:
    """같은 자료의 재시도를 막는 로컬 기록. 삭제·변조를 막는 전자서명은 아니다."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    directory = ROOT / "run" / "final-evaluations"
    directory.mkdir(parents=True, exist_ok=True)
    attempt = uuid.uuid4().hex
    with (directory / f"{final_input_digest(path)}.json").open("x", encoding="utf-8") as f:
        json.dump({"attempt_id": attempt, "dataset_sha256": digest, "started": time.time(), "ready": ready}, f, ensure_ascii=False)
    return attempt


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "eval" / "dev.jsonl"))
    ap.add_argument("--url", default=os.environ.get("AI_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--label", default="run")
    ap.add_argument("--allow-mock", action="store_true", help="계약 점검 전용. 모델 품질 평가로 사용할 수 없음")
    ap.add_argument("--final", action="store_true", help="설정 확정 후 잠근 시험을 실행함을 명시")
    ap.add_argument("--attempt-log", type=Path, help="LOG_REJECTED=1인 서버 로그(예: logs/ai.err.log). 재시도 회복률 집계")
    a = ap.parse_args()
    if a.attempt_log and not a.attempt_log.is_file():
        ap.error("--attempt-log 파일이 없습니다. LOG_REJECTED=1로 서버를 먼저 시작하세요")

    locked = Path(a.file).name.startswith("test.locked")
    if Path(a.file).name == "test.locked.jsonl":
        ap.error("기존 잠근 시험은 노출되어 폐기됐습니다. test.locked.v2.jsonl을 사용하세요")
    if locked and not a.final:
        ap.error("잠근 시험은 튜닝 종료 후 --final로 한 번만 실행하세요")

    bundle = json.loads((ROOT / "models" / "manifest.json").read_text(encoding="utf-8"))["engine_bundle"]
    headers = {"X-Internal-Token": os.environ["AI_INTERNAL_TOKEN"]} if os.environ.get("AI_INTERNAL_TOKEN") else {}
    cases = [json.loads(x) for x in Path(a.file).read_text(encoding="utf-8").splitlines() if x.strip()]
    if locked and any(c.get("review_status") != "approved" or not c.get("reviewer") or not c.get("reviewed_at") for c in cases):
        ap.error("잠근 시험은 정답·기준 검수 완료 후 실행하세요")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "eval" / "results" / f"{a.label}-{Path(a.file).stem}-{stamp}"
    client = httpx.Client(timeout=60, headers=headers)
    response = client.get(f"{a.url}/health/ready", timeout=httpx.Timeout(300, connect=2))
    ready = response.json()
    if response.status_code != 200 or not ready.get("ready"):
        raise SystemExit("model is not ready; evaluation not started")
    if (ready.get("backend") == "mock" or ready.get("model_id") == "mock") and not a.allow_mock:
        raise SystemExit("mock is not a translation quality evaluation; use --allow-mock only for contract checks")
    if ready.get("artifact_hashes") != artifact_hashes():
        raise SystemExit("server artifacts differ from this checkout; restart/deploy matching code before evaluation")
    try:
        final_attempt = reserve_final(Path(a.file), ready) if locked else None
    except FileExistsError:
        raise SystemExit("이 잠근 시험은 이미 시도했습니다. 재선택 대신 실패 원인과 기존 기록을 보존하세요")
    out_dir.mkdir(parents=True, exist_ok=False)

    rows = []
    for i, c in enumerate(cases):
        sid = f"eval-{uuid.uuid4().hex[:8]}"
        req = {"request_id": f"{sid}-0", "session_id": sid, "session_epoch": 1, "turn_id": 0,
               "source_locale": c["source_locale"], "text": c["text"], "engine_bundle": bundle}
        if c["task"] == "translate":
            req.update(target_locale=c["target_locale"], scenario=c["scenario"])
            path = "/internal/v1/translate"
        else:
            path = "/internal/v1/parse-destination"
        for _ in range(20):  # 429 busy면 잠시 뒤 재시도
            t0 = time.perf_counter()
            r = client.post(f"{a.url}{path}", json=req)
            wall = int((time.perf_counter() - t0) * 1000)
            if r.status_code != 429:
                break
            time.sleep(1)
        body = r.json()
        passed, flags = judge(c, r.status_code, body)
        rows.append({"case": c, "request_id": req["request_id"], "http": r.status_code, "body": body,
                     "wall_ms": wall, "pass": passed, "flags": flags})
        print(f"[{i + 1}/{len(cases)}] {'PASS' if passed else 'FAIL'} {c['id']} {wall}ms {' '.join(flags)}")

    ready_end_response = client.get(f"{a.url}/health/ready")
    ready_end = ready_end_response.json()
    retry_stats = attach_attempts(rows, a.attempt_log) if a.attempt_log else None

    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    # 검증: 의미 보존은 사람이 판정한다(human_meaning_ok, critical_error 칸)
    with (out_dir / "review.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "task", "direction", "text", "reference", "output", "auto_pass", "flags",
                    "human_meaning_ok", "human_naturalness_ok", "critical_error", "notes"])
        for row in rows:
            c, b = row["case"], row["body"]
            output = b.get("translated_text") or b.get("clarification_question") or b.get("error_code") or ""
            if c["task"] == "destination":
                output = json.dumps({k: b.get(k) for k in ("status", "query_ko_candidate", "candidates")}, ensure_ascii=False)
            w.writerow([c["id"], c["task"], f"{c['source_locale']}>{c.get('target_locale', '')}", c["text"],
                        c.get("reference", ""), output, row["pass"], " ".join(row["flags"]), "", "", "", ""])

    meta = {"dataset": Path(a.file).name, "dataset_sha256": hashlib.sha256(Path(a.file).read_bytes()).hexdigest(),
            "ready": ready, "ready_end": ready_end, "label": a.label, "timestamp": stamp, "final_attempt": final_attempt,
            "retry_stats": retry_stats,
            "results_sha256": hashlib.sha256((out_dir / "results.jsonl").read_bytes()).hexdigest()}
    (out_dir / "run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    client.close()

    lines = [f"# 평가 결과 {a.label} ({Path(a.file).name}, {stamp})", "",
             f"- engine_bundle: {bundle}", f"- ready: {json.dumps(ready, ensure_ascii=False)}", ""]
    lines += ["| task | n | auto pass | pass rate |", "|---|---:|---:|---:|"]
    for task in sorted({r["case"]["task"] for r in rows}):
        rs = [r for r in rows if r["case"]["task"] == task]
        p = sum(r["pass"] for r in rs)
        lines.append(f"| {task} | {len(rs)} | {p} | {p / len(rs):.1%} |")
    cats = sorted({r["case"]["category"] for r in rows if r["case"].get("category")})
    if cats:  # 일반 평가셋: 상황별로 약한 곳을 본다
        lines += ["", "| category | n | auto pass | pass rate |", "|---|---:|---:|---:|"]
        for cat in cats:
            rs = [r for r in rows if r["case"].get("category") == cat]
            p = sum(r["pass"] for r in rs)
            lines.append(f"| {cat} | {len(rs)} | {p} | {p / len(rs):.1%} |")
    lat = [r["body"]["latency_ms"] for r in rows if r["http"] == 200 and "latency_ms" in r["body"]]
    wall = [r["wall_ms"] for r in rows if r["http"] == 200]
    errors = Counter(f for r in rows for f in r["flags"] if f.startswith("http_"))
    # 검증: 502가 어떤 검사에 걸렸는지 종류별로 센다(number_missing:18 → number_missing)
    issues = Counter(i.split(":")[0] for r in rows if r["http"] == 502
                     for i in (r["body"].get("message") or "").split(", ") if i)
    lines += ["", f"- model latency p50/p95 ms: {pct(lat, .5)} / {pct(lat, .95)}",
              f"- HTTP wall p50/p95 ms: {pct(wall, .5)} / {pct(wall, .95)}",
              f"- mean model latency ms: {round(statistics.mean(lat)) if lat else None}",
              f"- errors: {dict(errors) or 'none'}",
              f"- 502 reasons: {dict(issues) or 'none'}"]
    if retry_stats is not None:
        lines += retry_summary(retry_stats)
    else:
        lines.append("- retry recovery: not measured (use LOG_REJECTED=1 and --attempt-log)")
    lines += ["", "자동 통과·재시도 회복은 보조 지표다. review.csv에서 사람이 의미 보존·치명 오류를 판정한다."]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nsaved: {out_dir}")


if __name__ == "__main__":
    main()
