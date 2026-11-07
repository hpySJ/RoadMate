"""평가: 실행 중인 AI 서버(HTTP)로 eval jsonl을 돌려 자동 지표와 사람 검토용 CSV를 만든다.

사용: .venv\\Scripts\\python.exe eval\\evaluate.py --file eval\\dev.jsonl --label q4-prompt-v1
잠근 시험(eval\\test.locked.jsonl)은 설정을 확정한 뒤 한 번만 실행한다.
"""
import argparse
import csv
import hashlib
import json
import os
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


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "eval" / "dev.jsonl"))
    ap.add_argument("--url", default=os.environ.get("AI_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--label", default="run")
    ap.add_argument("--allow-mock", action="store_true", help="계약 점검 전용. 모델 품질 평가로 사용할 수 없음")
    ap.add_argument("--final", action="store_true", help="설정 확정 후 잠근 시험을 실행함을 명시")
    a = ap.parse_args()

    if Path(a.file).name == "test.locked.jsonl" and not a.final:
        ap.error("잠근 시험은 튜닝 종료 후 --final로 한 번만 실행하세요")

    bundle = json.loads((ROOT / "models" / "manifest.json").read_text(encoding="utf-8"))["engine_bundle"]
    headers = {"X-Internal-Token": os.environ["AI_INTERNAL_TOKEN"]} if os.environ.get("AI_INTERNAL_TOKEN") else {}
    cases = [json.loads(x) for x in Path(a.file).read_text(encoding="utf-8").splitlines() if x.strip()]
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_dir = ROOT / "eval" / "results" / f"{a.label}-{Path(a.file).stem}-{stamp}"
    client = httpx.Client(timeout=60, headers=headers)
    response = client.get(f"{a.url}/health/ready")
    ready = response.json()
    if response.status_code != 200 or not ready.get("ready"):
        raise SystemExit("model is not ready; evaluation not started")
    if (ready.get("backend") == "mock" or ready.get("model_id") == "mock") and not a.allow_mock:
        raise SystemExit("mock is not a translation quality evaluation; use --allow-mock only for contract checks")
    if ready.get("artifact_hashes") != artifact_hashes():
        raise SystemExit("server artifacts differ from this checkout; restart/deploy matching code before evaluation")
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
        rows.append({"case": c, "http": r.status_code, "body": body, "wall_ms": wall, "pass": passed, "flags": flags})
        print(f"[{i + 1}/{len(cases)}] {'PASS' if passed else 'FAIL'} {c['id']} {wall}ms {' '.join(flags)}")

    ready_end_response = client.get(f"{a.url}/health/ready")
    ready_end = ready_end_response.json()

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
            "ready": ready, "ready_end": ready_end, "label": a.label, "timestamp": stamp,
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
              f"- 502 reasons: {dict(issues) or 'none'}", "",
              "자동 통과는 보조 지표다. review.csv에서 사람이 의미 보존·치명 오류를 판정한다."]
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nsaved: {out_dir}")


if __name__ == "__main__":
    main()
