import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))

import prepare_data  # noqa: E402
import review_data  # noqa: E402


def row(**kw):
    r = {"id": "pool-0001", "group_id": "g1", "source_locale": "en-US", "target_locale": "ko-KR",
         "scenario": "general", "text": "Can you write down your phone number for me?",
         "draft": "전화번호를 제가 써 주세요.", "fixed": "", "approve": "", "notes": ""}
    r.update(kw)
    return r


def test_import_uses_fixed_text_and_only_approved_rows():
    rows = [row(id="a", approve="y", fixed="전화번호 좀 적어 주시겠어요?"),
            row(id="b", approve="", fixed="안 쓰는 줄"),
            row(id="c", approve="ㅇ")]
    recs, errors = review_data.to_records(rows, "tester")
    assert not errors and len(recs) == 2
    assert recs[0]["output"]["translated_text"] == "전화번호 좀 적어 주시겠어요?" and recs[0]["source"] == "human_fixed"
    assert recs[1]["source"] == "model_draft_reviewed" and recs[1]["review_status"] == "approved"
    assert all(prepare_data.check(r) == [] for r in recs)  # 학습 준비 단계 검사와 같은 형식


def test_import_rejects_approved_rows_that_break_checks():
    recs, errors = review_data.to_records([row(approve="y", fixed="전화번호를 적지 마세요.")], "tester")
    assert not recs and "negation_mismatch" in errors[0]


def test_pool_has_no_eval_overlap():
    norm = lambda s: "".join(s.lower().split())  # noqa: E731
    pool = [json.loads(x) for x in (ROOT / "train" / "sources" / "pool.jsonl").read_text(encoding="utf-8").splitlines()]
    evals = {norm(json.loads(x)["text"]) for n in ("dev.jsonl", "test.locked.jsonl", "general.jsonl")
             for x in (ROOT / "eval" / n).read_text(encoding="utf-8").splitlines() if x.strip()}
    assert pool and not {norm(p["text"]) for p in pool} & evals
    assert len({p["id"] for p in pool}) == len(pool)
