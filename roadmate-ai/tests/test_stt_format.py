import copy
import hashlib
import json
from pathlib import Path

import pytest

from eval import stt_format as F


@pytest.mark.parametrize("source, expected", [
    ("¿CÓMO está AQUÍ? ¡BIEN!", "cómo está aquí bien"),
    ("Don't, DON'T! D’Ávila; l‘eau", "dont dont dávila leau"),
    ("5'8 / 5’8 / 5＇8", "5 8 5 8 5 8"),
    ("“GPS” «USB» 'B2' ＇EV＇", "gps usb b2 ev"),
    ("A-B / 4:45; 2.5%.", "a b 4 45 2 5"),
    ("지금 GPS를 켤까요？  USB는요!", "지금 gps를 켤까요 usb는요"),
    ("CAFÉ\tÁRBOL\nNIÑO  €30 + 5℃ 🚙", "café árbol niño €30 + 5℃ 🚙"),
    ("I\u0301'M / CAFÉ'S", "i\u0301m cafés"),
    ("  a—b…c、d_e  ", "a b c d e"),
])
def test_normalization_is_only_case_punctuation_and_whitespace(source, expected):
    assert F.normalize_source(source) == expected
    assert F.normalize_source(expected) == expected


@pytest.fixture
def sources(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    directory = root / "eval"
    directory.mkdir(parents=True)
    cases = {}
    for index, name in enumerate(F.DATASETS):
        cases[name] = [
            {"id": f"synthetic-{index}-a", "task": "translate", "source_locale": "es-US", "target_locale": "ko-KR",
             "text": "¿ABRE el taller?", "reference": "원래 정답", "must_include": ["정답|대안"],
             "expect_status": "ok", "nested": {"punctuation": "KEEP!", "capital": "YES"}},
            {"id": f"synthetic-{index}-b", "task": "destination", "text": "already normalized",
             "expect_confirm": True, "expect_poi_any": ["A", "B"], "reference": None},
        ]
        (directory / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in cases[name]), encoding="utf-8")
    # An unrelated file must never be discovered/read by a glob or input option.
    (directory / "test.locked.synthetic.jsonl").write_text("not JSON; must not be read", encoding="utf-8")
    monkeypatch.setattr(F, "ROOT", root)
    return root, cases


def test_variants_preserve_every_other_field_order_and_source_bytes(sources, tmp_path, monkeypatch):
    root, cases = sources
    original_cases = copy.deepcopy(cases)
    before = {name: (root / "eval" / name).read_bytes() for name in F.DATASETS}
    read_paths = []
    read_bytes = Path.read_bytes
    def track_read(path):
        read_paths.append(path)
        return read_bytes(path)
    monkeypatch.setattr(Path, "read_bytes", track_read)
    out = tmp_path / "variants"
    manifest = F.build_variants(out)
    assert read_paths == [root / "eval" / name for name in F.DATASETS]
    assert set(path.name for path in out.iterdir()) == {*F.DATASETS, "stt_manifest.json"}
    assert manifest == json.loads((out / "stt_manifest.json").read_text(encoding="utf-8"))
    assert manifest["transform_version"] == F.STT_FORMAT_VERSION and manifest["rules"]
    for entry, name in zip(manifest["datasets"], F.DATASETS):
        payload = (out / name).read_bytes()
        transformed = [json.loads(line) for line in payload.decode().splitlines()]
        expected = copy.deepcopy(cases[name])
        for row in expected:
            row["text"] = F.normalize_source(row["text"])
        assert transformed == expected
        assert payload == F.variant_bytes(cases[name])
        assert (root / "eval" / name).read_bytes() == before[name]
        assert entry == {"source": f"eval/{name}", "source_sha256": hashlib.sha256(before[name]).hexdigest(),
                         "variant": name, "variant_sha256": hashlib.sha256(payload).hexdigest(),
                         "count": 2, "changed_count": 1}
    assert cases == original_cases


@pytest.mark.parametrize("invalid", ["duplicate", "cross_set_duplicate", "empty_text", "empty_dataset", "invalid_json", "not_string"])
def test_invalid_sources_leave_no_output_directory(sources, tmp_path, invalid):
    root, cases = sources
    name = F.DATASETS[-1]
    rows = copy.deepcopy(cases[name])
    if invalid == "duplicate":
        rows[1]["id"] = rows[0]["id"]
    elif invalid == "cross_set_duplicate":
        rows[1]["id"] = cases[F.DATASETS[0]][0]["id"]
    elif invalid == "empty_text":
        rows[1]["text"] = " ?!…  "
    elif invalid == "not_string":
        rows[1]["text"] = 123
    data = "broken JSON" if invalid == "invalid_json" else "\n".join(json.dumps(row) for row in rows)
    if invalid == "empty_dataset":
        data = "\n \n"
    (root / "eval" / name).write_text(data, encoding="utf-8")
    out = tmp_path / "absent-parent" / "variants"
    with pytest.raises(ValueError):
        F.build_variants(out)
    assert not out.parent.exists()


def test_existing_output_is_refused_before_reading_sources(tmp_path, monkeypatch):
    out = tmp_path / "variants"
    out.mkdir()
    existing = out / "keep.txt"
    existing.write_text("keep this", encoding="utf-8")
    monkeypatch.setattr(F, "ROOT", tmp_path / "missing-repo")
    with pytest.raises(FileExistsError):
        F.build_variants(out)
    assert existing.read_text(encoding="utf-8") == "keep this"


@pytest.mark.parametrize("target_name", ["outside.jsonl", "test.locked.synthetic.jsonl"])
def test_redirected_canonical_path_is_rejected_before_read(sources, tmp_path, monkeypatch, target_name):
    root, _ = sources
    original = root / "eval" / F.DATASETS[0]
    target = tmp_path / target_name
    original_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda path, *args, **kwargs:
                        target if path == original else original_resolve(path, *args, **kwargs))
    def forbidden_read(path):
        pytest.fail(f"redirected source must not be read: {path}")
    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    out = tmp_path / "variants"
    with pytest.raises(ValueError, match="redirected"):
        F.build_variants(out)
    assert not out.exists()


def test_cli_requires_new_out_dir_and_has_no_input_dataset_option(sources, tmp_path, capsys):
    out = tmp_path / "variants"
    with pytest.raises(SystemExit):
        F.main(["--out-dir", str(out), "--file", "test.locked.synthetic.jsonl"])
    assert not out.exists()
    F.main(["--out-dir", str(out)])
    assert json.loads(capsys.readouterr().out)["transform_version"] == F.STT_FORMAT_VERSION
