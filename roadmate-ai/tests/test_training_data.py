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


def test_pool_groups_are_stable_and_parallel_templates_share_group():
    import hashlib
    norm = lambda s: "".join(s.lower().split())  # noqa: E731
    pool = [json.loads(x) for x in (ROOT / "train" / "sources" / "pool.jsonl").read_text(encoding="utf-8").splitlines()]
    for p in pool:
        if p["group_id"].startswith("p_"):  # 손으로 쓴 문장: 내용으로 정한 ID(목록 순서와 무관)
            assert p["group_id"] == "p_" + hashlib.sha1(norm(p["text"]).encode()).hexdigest()[:10]
    langs = {}
    for p in pool:
        langs.setdefault(p["group_id"], set()).add(p["source_locale"])
    assert langs["t_not_fuel"] == langs["t_amount_fuel"] == {"en-US", "ko-KR"}


def test_prepare_rejects_same_text_in_two_groups():
    recs = [{"group_id": "g1", "text": "반납은 오후 4시까지 해 주세요."}, {"group_id": "g2", "text": "반납은 오후 4시까지  해 주세요."},
            {"group_id": "g3", "text": "다른 문장이에요."}]
    errs = prepare_data.duplicate_groups(recs)
    assert len(errs) == 1 and "g1" in errs[0] and "g2" in errs[0]


def test_pool_has_no_eval_overlap():
    norm = lambda s: "".join(s.lower().split())  # noqa: E731
    pool = [json.loads(x) for x in (ROOT / "train" / "sources" / "pool.jsonl").read_text(encoding="utf-8").splitlines()]
    evals = {norm(json.loads(x)["text"]) for n in ("dev.jsonl", "test.locked.jsonl", "general.jsonl")
             for x in (ROOT / "eval" / n).read_text(encoding="utf-8").splitlines() if x.strip()}
    assert pool and not {norm(p["text"]) for p in pool} & evals
    assert len({p["id"] for p in pool}) == len(pool)
