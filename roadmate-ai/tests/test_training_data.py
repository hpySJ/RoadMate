import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "train"))

import prepare_data  # noqa: E402
import review_data  # noqa: E402
import train_qlora  # noqa: E402
import data_boundary as B  # noqa: E402


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
    from sources import make_pool
    norm = lambda s: "".join(s.lower().split())  # noqa: E731
    pool = [json.loads(x) for x in (ROOT / "train" / "sources" / "pool.jsonl").read_text(encoding="utf-8").splitlines()]
    for p in pool:
        if p["group_id"].startswith("p_"):  # 손으로 쓴 문장: 내용으로 정한 ID(목록 순서와 무관)
            assert p["group_id"] == "p_" + hashlib.sha1(norm(p["text"]).encode()).hexdigest()[:10]
    langs = {}
    for locale, templates in (("en-US", make_pool.EN_T), ("ko-KR", make_pool.KO_T)):
        for group, _, _ in make_pool.expand(templates):
            langs.setdefault(group, set()).add(locale)
    assert langs["t_not_fuel"] == langs["t_amount_fuel"] == {"en-US", "ko-KR"}


def test_prepare_rejects_same_text_in_two_groups():
    recs = [{"group_id": "g1", "text": "반납은 오후 4시까지 해 주세요."}, {"group_id": "g2", "text": "반납은 오후 4시까지  해 주세요."},
            {"group_id": "g3", "text": "다른 문장이에요."}]
    for rec in recs:
        rec["source_locale"] = "ko-KR"
    errs = prepare_data.duplicate_groups(recs)
    assert len(errs) == 1 and "g1" in errs[0] and "g2" in errs[0]


def test_pool_has_no_eval_overlap():
    pool = [json.loads(x) for x in (ROOT / "train" / "sources" / "pool.jsonl").read_text(encoding="utf-8").splitlines()]
    assert pool and not {B.fingerprint(p["text"], p["source_locale"]) for p in pool} & B.protected_templates(ROOT)
    assert len({p["id"] for p in pool}) == len(pool)


def test_seed_variants_share_pool_template_groups():
    from sources import make_pool
    seed = prepare_data.load_jsonl(ROOT / "train" / "data" / "seed.draft.jsonl")
    expected = {
        "Just 10,000 won of diesel, please.": "t_amount_fuel",
        "It's a gasoline car. Please don't put diesel in it.": "t_not_fuel",
        "Can I park here until 8 p.m.?": "t_en_park_until",
        "내일 오전 10시까지 반납해 주세요.": "t_return_time",
    }
    pool_groups = {g for templates in (make_pool.EN_T, make_pool.KO_T) for g, _, _ in make_pool.expand(templates)}
    for text, group in expected.items():
        rec = next(r for r in seed if r["text"] == text)
        assert rec["group_id"] == group and group in pool_groups
    assert all(r["review_status"] == "draft" for r in seed)


@pytest.mark.parametrize("eval_name", ["general_es.jsonl", "naturalness.dev.jsonl", "test.locked.jsonl", "test.locked.v2.jsonl"])
def test_training_excludes_all_evaluation_files(monkeypatch, tmp_path, capsys, eval_name):
    from sources import make_pool

    for directory in ("eval", "prompts", "train/data", "train/sources"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    text = "주차비는 30분에 2천 원이에요."
    held = {"text": "주차비는 10분에 천 원이에요.", "source_locale": "ko-KR"}
    (tmp_path / "eval" / eval_name).write_text(json.dumps(held) + "\n", encoding="utf-8")
    for name in ("translate", "destination"):
        (tmp_path / "prompts" / f"{name}.examples.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(make_pool, "ROOT", tmp_path)
    monkeypatch.setattr(make_pool, "KO", [("general", text)])
    for name in ("EN", "EN_T", "KO_T"):
        monkeypatch.setattr(make_pool, name, [])
    make_pool.main()
    assert prepare_data.load_jsonl(tmp_path / "train/sources/pool.jsonl") == []
    rec = {"group_id": "eval-leak", "task": "translate", "source_locale": "ko-KR", "target_locale": "en-US",
           "scenario": "general", "text": text, "review_status": "approved",
           "output": {"status": "ok", "translated_text": "This sentence is for evaluation.", "clarification_question": None}}
    (tmp_path / "train/data/input.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    (tmp_path / "train/training_config.json").write_text('{"max_length":1536}', encoding="utf-8")
    monkeypatch.setattr(prepare_data, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["prepare_data.py"])
    monkeypatch.setattr(sys.stdout, "reconfigure", lambda **kw: None, raising=False)
    with pytest.raises(SystemExit, match="1 invalid records"):
        prepare_data.main()
    assert "leak:" in capsys.readouterr().out
    assert not (tmp_path / "train/out").exists()


@pytest.mark.parametrize("case, message", [("draft", "no records"), ("few_shot", "leak:"),
                                           ("validation_only", "train split is empty")])
def test_prepare_rejects_unusable_training_data(tmp_path, case, message):
    rec = prepare_data.load_jsonl(ROOT / "train" / "data" / "seed.draft.jsonl")[0]
    if case != "draft":
        rec["review_status"] = "approved"
    if case == "few_shot":
        ex = prepare_data.P.examples("translate")[0]
        rec.update(text=ex["input"]["utterance"], source_locale=ex["input"]["source_locale"],
                   output=ex["output"])
    elif case == "validation_only":
        rec.update(text="The orange folder belongs in the glove compartment.",
                   output={"status": "ok", "translated_text": "주황색 서류철은 글러브 박스에 넣어야 해요.",
                           "clarification_question": None})
        rec["group_id"] = "s_rental_return_10am"
    source, output = tmp_path / "input.jsonl", tmp_path / "out"
    source.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", str(ROOT / "train/prepare_data.py"),
                             "--inputs", str(source), "--out", str(output)], capture_output=True)
    assert result.returncode != 0 and message.encode() in result.stdout + result.stderr
    assert not output.exists()


def test_draft_does_not_overwrite_human_review(monkeypatch, tmp_path):
    pool = tmp_path / "pool.jsonl"
    pool.write_text("", encoding="utf-8")
    review = tmp_path / "batch1.review.csv"
    review.write_text("human review already saved", encoding="utf-8")
    monkeypatch.setattr(review_data, "DRAFTS", tmp_path)
    with pytest.raises(FileExistsError):
        review_data.draft(SimpleNamespace(pool=pool, limit=0, name="batch1", url="http://127.0.0.1:1"))
    assert review.read_text(encoding="utf-8") == "human review already saved"


@pytest.mark.parametrize("case, message", [("missing", "run train/prepare_data.py first"),
                                           ("draft", "smoke only"), ("changed", "changed after prepare_data.py"),
                                           ("prompt", "prompt/glossary versions changed"),
                                           ("glossary", "prompt/glossary versions changed"),
                                           ("hashes", "prompt input hashes changed")])
def test_training_rejects_bad_data_before_loading_model(tmp_path, case, message):
    cfg = json.loads((ROOT / "train/training_config.json").read_text(encoding="utf-8"))
    cfg.update(data_dir=str(tmp_path), base_model=str(tmp_path / "no-model"))
    config = tmp_path / "config.json"
    config.write_text(json.dumps(cfg), encoding="utf-8")
    if case != "missing":
        (tmp_path / "train.jsonl").write_text("changed", encoding="utf-8")
        manifest = {"allow_draft": case == "draft", "outputs": {"train.jsonl": "incorrect_hash"}}
        if case in ("prompt", "glossary", "hashes"):
            manifest.update(outputs={"train.jsonl": prepare_data.sha(tmp_path / "train.jsonl")},
                            prompt_versions={n: prepare_data.P.prompt(n)[0] for n in ("translate", "destination")},
                            glossary_version=prepare_data.P.glossary()["version"])
            if case == "prompt":
                manifest["prompt_versions"]["translate"] = "outdated"
            elif case == "glossary":
                manifest["glossary_version"] = "outdated"
            else:
                manifest["prompt_hashes"] = B.server_prompt_hashes(ROOT)
                manifest["prompt_hashes"]["prompts/translate.examples.json"] = "modified_without_version_bump"
        (tmp_path / "dataset_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = subprocess.run([sys.executable, "-B", str(ROOT / "train/train_qlora.py"), "--config", str(config)],
                            capture_output=True)
    assert result.returncode != 0 and message.encode() in result.stdout + result.stderr
    assert b"ModuleNotFoundError" not in result.stderr


@pytest.mark.parametrize("locale, left, right", [
    ("en-US", "Please put in 60,000 won of diesel.", "Please put in 20,000 won of gasoline."),
    ("ko-KR", "주차비는 30분에 2천 원이에요.", "주차비는 10분에 천 원이에요."),
    ("es-MX", "Ponga cuarenta mil wones de diésel.", "Ponga veinte mil wones de gasolina."),
    ("en-US", "Take me to Jeju Airport.", "Take me to Hamdeok Beach."),
    ("en-US", "Fill it up with gas, please.", "Fill it up with fuel, please."),
    ("en-US", "Can you fill it with, um, the...", "Can you fill it with, uh..."),
])
def test_slot_variants_cannot_cross_training_groups(locale, left, right):
    assert B.fingerprint(left, locale) == B.fingerprint(right, locale)
    recs = [{"group_id": f"g{i}", "text": t, "source_locale": locale} for i, t in enumerate((left, right))]
    assert len(prepare_data.duplicate_groups(recs)) == 1
    recs[1]["group_id"] = "g0"
    assert prepare_data.duplicate_groups(recs) == []


def test_template_boundary_keeps_distinct_actions_separate():
    assert B.fingerprint("Please refund 20,000 won.", "en-US") != B.fingerprint("Please charge 20,000 won.", "en-US")


@pytest.mark.parametrize("path", ["prompts/translate.txt", "prompts/translate.examples.json", "packs/glossary.json",
                                  "packs/poi.demo.json", "prompting.py"])
def test_prompt_hashes_detect_content_edits_without_version_bump(tmp_path, path):
    for name in B.server_prompt_hashes(ROOT):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / name).read_bytes())
    before = B.server_prompt_hashes(tmp_path)
    with (tmp_path / path).open("ab") as f:
        f.write(b"\n ")
    after = B.server_prompt_hashes(tmp_path)
    assert [p for p in before if before[p] != after[p]] == [path]


@pytest.mark.parametrize("case, message", [("exact", None), ("train_empty", "train split is empty"),
                                           ("validation_empty", "validation split is empty"),
                                           ("train_long", "train:1 has 7 tokens"),
                                           ("validation_long", "validation:1 has 7 tokens")])
def test_training_checks_full_chat_length_without_truncation(case, message):
    class Tokenizer:
        def apply_chat_template(self, messages, *, tokenize):
            assert tokenize is False
            return " ".join(f"<{m['role']}> {m['content']} </{m['role']}>" for m in messages)

        def __call__(self, text, *, add_special_tokens, truncation):
            assert add_special_tokens is False and truncation is False
            return {"input_ids": text.split()}

    ds = {s: [{"prompt": [{"role": "user", "content": "question"}],
               "completion": [{"role": "assistant", "content": "answer"}]}] for s in ("train", "validation")}
    if case.endswith("_empty"):
        ds[case.removesuffix("_empty")] = []
    elif case.endswith("_long"):
        ds[case.removesuffix("_long")][0]["completion"][0]["content"] = "long answer"
    if message:
        with pytest.raises(SystemExit, match=message):
            train_qlora.check_lengths(ds, Tokenizer(), 6)
    else:
        train_qlora.check_lengths(ds, Tokenizer(), 6)
