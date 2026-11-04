"""연기 시험: 모델·AI 준비 확인 → 한영 번역 2건 → 목적지 1건 → 취소. 실패 시 종료 코드 1."""
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
M = json.loads((ROOT / "models" / "manifest.json").read_text(encoding="utf-8"))
AI = os.environ.get("AI_URL", "http://127.0.0.1:8000")
HEADERS = {"X-Internal-Token": os.environ["AI_INTERNAL_TOKEN"]} if os.environ.get("AI_INTERNAL_TOKEN") else {}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    c = httpx.Client(timeout=60, headers=HEADERS)
    ready = c.get(f"{AI}/health/ready")
    print("ready:", ready.status_code, ready.json())
    if ready.status_code != 200:
        return 1
    sid, bad = f"smoke-{uuid.uuid4().hex[:8]}", 0
    base = {"session_id": sid, "session_epoch": 1, "engine_bundle": M["engine_bundle"]}
    cases = [
        ("/internal/v1/translate", {"source_locale": "en-US", "target_locale": "ko-KR", "scenario": "gas_station",
                                    "text": "Please put in 50,000 won of gasoline, not diesel."}),
        ("/internal/v1/translate", {"source_locale": "ko-KR", "target_locale": "en-US", "scenario": "accident",
                                    "text": "다친 사람은 없어요. 렌터카 회사에 연락해 주세요."}),
        ("/internal/v1/parse-destination", {"source_locale": "en-US", "text": "Take me to Seongsan Sunrise Peak, please."}),
    ]
    for i, (path, extra) in enumerate(cases):
        t0 = time.perf_counter()
        r = c.post(f"{AI}{path}", json={**base, "request_id": f"{sid}-{i}", "turn_id": i, **extra})
        ms = int((time.perf_counter() - t0) * 1000)
        print(f"{path} {r.status_code} {ms}ms", json.dumps(r.json(), ensure_ascii=False))
        bad += r.status_code != 200
    r = c.post(f"{AI}/internal/v1/cancel", json={"session_id": sid, "session_epoch": 1})
    print("cancel:", r.status_code, r.json())
    bad += r.status_code != 200
    print("SMOKE", "FAIL" if bad else "OK")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
