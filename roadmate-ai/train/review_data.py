"""학습: 원문 묶음 → 서버 초안 → 사람 검수 CSV → 승인 자료(train/data/*.approved.jsonl).

초안: .venv/bin/python train/review_data.py draft --pool train/sources/pool.jsonl --name batch1
      (AI 서버를 POLISH_KO=1로 띄워 둔다. 서버와 같은 프롬프트·검사·다듬기를 거친 초안이 나온다)
검수: train/data/drafts/batch1.review.csv를 엑셀로 열어 fixed(고친 문장, 그대로면 비움)와 approve(y)를 채운다
가져오기: .venv/bin/python train/review_data.py import --name batch1
로컬 모델 초안을 검수 없이 정답으로 쓰지 않는다. approve가 y인 줄만, 검사기를 통과해야 가져온다.
"""
import argparse
import csv
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_dotenv, load_manifest  # noqa: E402
from validation import validate_translation  # noqa: E402

load_dotenv()
DRAFTS = ROOT / "train" / "data" / "drafts"
FIELDS = ["id", "group_id", "source_locale", "target_locale", "scenario", "text", "draft", "fixed", "approve", "notes"]


def draft(a):
    pool = [json.loads(x) for x in Path(a.pool).read_text(encoding="utf-8").splitlines() if x.strip()]
    pool = pool[: a.limit] if a.limit else pool
    bundle = load_manifest()["engine_bundle"]
    headers = {"X-Internal-Token": os.environ["AI_INTERNAL_TOKEN"]} if os.environ.get("AI_INTERNAL_TOKEN") else {}
    DRAFTS.mkdir(parents=True, exist_ok=True)
    out = DRAFTS / f"{a.name}.review.csv"
    # 검수 중인 CSV를 같은 배치 이름의 초안으로 덮어쓰지 않는다.
    with out.open("x", encoding="utf-8-sig", newline="") as f, httpx.Client(timeout=60, headers=headers) as client:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for i, c in enumerate(pool):
            sid = f"draft-{uuid.uuid4().hex[:8]}"
            req = {"request_id": f"{sid}-0", "session_id": sid, "session_epoch": 1, "turn_id": 0,
                   "source_locale": c["source_locale"], "target_locale": c["target_locale"],
                   "scenario": c["scenario"], "text": c["text"], "engine_bundle": bundle}
            for _ in range(20):  # 429 busy면 잠시 뒤 재시도
                r = client.post(f"{a.url}/internal/v1/translate", json=req)
                if r.status_code != 429:
                    break
                time.sleep(1)
            body = r.json()
            ok = r.status_code == 200 and body.get("status") == "ok"
            # 학습: 서버가 막은 문장은 초안 없이 남겨 사람이 직접 번역하게 한다
            note = "" if ok else f"{r.status_code} {body.get('status') or body.get('error_code')} {body.get('message', '')}"
            w.writerow({**{k: c[k] for k in FIELDS[:6]}, "draft": body.get("translated_text") if ok else "",
                        "fixed": "", "approve": "", "notes": note.strip()})
            print(f"[{i + 1}/{len(pool)}] {'OK  ' if ok else 'NONE'} {c['id']}")
    print(f"review this file, then run import: {out}")


def load_review(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def to_records(rows: list[dict], reviewer: str) -> tuple[list[dict], list[str]]:
    recs, errors = [], []
    for r in rows:
        if (r.get("approve") or "").strip().lower() not in ("y", "yes", "1", "o", "ㅇ"):
            continue
        final = (r.get("fixed") or "").strip() or (r.get("draft") or "").strip()
        if not final:
            errors.append(f"{r['id']}: approved but no translation")
            continue
        out = {"status": "ok", "translated_text": final, "clarification_question": None}
        issues = validate_translation(out, r["text"], r["source_locale"], r["target_locale"])
        if issues:
            errors.append(f"{r['id']}: {issues} | {final}")
            continue
        recs.append({"group_id": r["group_id"], "task": "translate", "source_locale": r["source_locale"],
                     "target_locale": r["target_locale"], "scenario": r["scenario"], "text": r["text"], "output": out,
                     "review_status": "approved", "reviewer": reviewer,
                     "source": "human_fixed" if (r.get("fixed") or "").strip() else "model_draft_reviewed"})
    return recs, errors


def import_(a):
    recs, errors = to_records(load_review(DRAFTS / f"{a.name}.review.csv"), a.reviewer)
    if errors:
        print("\n".join(errors))
        sys.exit(f"{len(errors)} approved rows failed checks. Fix them in the CSV and run import again.")
    out = ROOT / "train" / "data" / f"{a.name}.approved.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs), encoding="utf-8")
    print(f"{len(recs)} approved records -> {out}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("draft")
    d.add_argument("--pool", default=str(ROOT / "train" / "sources" / "pool.jsonl"))
    d.add_argument("--name", required=True)
    d.add_argument("--url", default=os.environ.get("AI_URL", "http://127.0.0.1:8000"))
    d.add_argument("--limit", type=int, default=0)
    i = sub.add_parser("import")
    i.add_argument("--name", required=True)
    i.add_argument("--reviewer", required=True, help="검수한 사람 이름(기록용)")
    a = ap.parse_args()
    draft(a) if a.cmd == "draft" else import_(a)


if __name__ == "__main__":
    main()
