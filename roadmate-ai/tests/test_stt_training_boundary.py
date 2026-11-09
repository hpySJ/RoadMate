# 테스트
import json
from pathlib import Path

import pytest

from eval.stt_format import normalize_source
from train import data_boundary as B


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "eval").mkdir()
    (tmp_path / "prompts").mkdir()
    monkeypatch.setattr(B.P, "concept_spans", lambda *args: [])
    monkeypatch.setattr(B.P, "places", lambda: ())
    B.fingerprint.cache_clear()
    yield tmp_path
    B.fingerprint.cache_clear()


def save_eval(root, name, rows):
    (root / "eval" / name).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


@pytest.mark.parametrize("source,locale,reference,target", [
    ("The archive storage fee is 46,700 won.", "en-US", "기록 보관료는 4만 6700원이에요.", "ko-KR"),
    ("문서 복사 비용은 2만 8600원이에요.", "ko-KR", "The document copy fee is 28,600 won.", "en-US"),
    ("¿Puede abrir el archivador metálico?", "es-US", "금속 서류함을 열어 주실 수 있어요?", "ko-KR"),
])
def test_protects_original_and_stt_input_and_reference(root, source, locale, reference, target):
    save_eval(root, "general.jsonl", [{"text": source, "source_locale": locale,
                                     "reference": reference, "target_locale": target}])
    blocked = B.protected_templates(root, include_locked=False, include_stt=True)
    for text, language in ((source, locale), (reference, target)):
        assert B.fingerprint(text, language) in blocked
        normalized = normalize_source(text, language)
        assert B.fingerprint(normalized, language) in blocked
        assert B.overlaps(normalized, language, blocked)


def test_stt_amount_fingerprint_gap_is_closed_without_changing_default_policy(root):
    text = "The archive storage fee is 46,700 won."
    save_eval(root, "general.jsonl", [{"text": text, "source_locale": "en-US"}])
    stt = normalize_source(text, "en-US")
    old_keys = B.protected_templates(root)
    assert old_keys == {B.fingerprint(text, "en-US")}
    assert not B.overlaps(stt, "en-US", old_keys)
    assert B.overlaps(stt, "en-US", B.protected_templates(root, include_locked=False, include_stt=True))


def test_few_shot_inputs_and_outputs_are_protected_in_both_formats(root):
    inputs = [
        {"utterance": "The copper cabinet deposit is 53,800 won.", "source_locale": "en-US", "target_locale": "ko-KR"},
        {"source": "보관함 청소비는 1만 9200원이에요.", "source_locale": "ko-KR", "target_locale": "en-US"},
    ]
    outputs = [{"translated_text": "구리색 보관함 보증금은 5만 3800원이에요."},
               {"text": "The locker cleaning fee is 19,200 won."}]
    (root / "prompts/translate.examples.json").write_text(json.dumps(
        [{"input": i, "output": o} for i, o in zip(inputs, outputs)]), encoding="utf-8")
    blocked = B.protected_templates(root, include_locked=False, include_stt=True)
    for i, o in zip(inputs, outputs):
        for text, locale in ((i.get("utterance") or i["source"], i["source_locale"]),
                             (o.get("translated_text") or o["text"], i["target_locale"])):
            assert B.fingerprint(text, locale) in blocked
            assert B.fingerprint(normalize_source(text, locale), locale) in blocked


def test_nonlocked_boundary_skips_locked_names_before_any_read(root, monkeypatch):
    text = "The woven basket clasp is loose."
    save_eval(root, "general.jsonl", [{"text": text, "source_locale": "en-US"}])
    for name in ("test.locked.jsonl", "test.locked.v2.jsonl", "test.locked.future.jsonl"):
        (root / "eval" / name).write_bytes(b"must never be read")
    (root / "prompts/test.locked.examples.json").write_bytes(b"must never be read")
    read = Path.read_text
    def guarded(path, *args, **kwargs):
        assert not path.name.casefold().startswith("test.locked")
        return read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", guarded)
    assert B.protected_templates(root, include_locked=False, include_stt=True) == {B.fingerprint(text, "en-US")}


def test_existing_default_still_protects_synthetic_locked_records(root):
    text = "The triangular binder label is missing."
    save_eval(root, "test.locked.jsonl", [{"text": text, "source_locale": "en-US"}])
    assert B.protected_templates(root) == {B.fingerprint(text, "en-US")}
    assert B.protected_templates(root, include_locked=False, include_stt=True) == set()


@pytest.mark.parametrize("relative", ["eval/general.jsonl", "prompts/translate.examples.json"])
@pytest.mark.parametrize("destination", ["eval/test.locked.jsonl", "outside/corpus.jsonl"])
def test_nonlocked_mode_rejects_redirected_paths_before_read(root, monkeypatch, relative, destination):
    path = root / relative
    path.write_bytes(b"must never be read")
    resolve, read = Path.resolve, Path.read_text
    def redirect(candidate, *args, **kwargs):
        return root / destination if candidate == path else resolve(candidate, *args, **kwargs)
    def guarded(candidate, *args, **kwargs):
        assert candidate != path
        return read(candidate, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", redirect)
    monkeypatch.setattr(Path, "read_text", guarded)
    with pytest.raises(ValueError, match="path was redirected"):
        B.protected_templates(root, include_locked=False, include_stt=True)
