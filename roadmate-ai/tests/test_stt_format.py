import copy
import hashlib
import json
from pathlib import Path

import pytest

from eval import stt_format as F


@pytest.mark.parametrize("source, expected", [
    ("¿CÓMO está AQUÍ? ¡BIEN!", "cómo está aquí bien"),
    ("Don't, DON'T! D’Ávila; l‘eau", "don't don't d’ávila l‘eau"),
    ("5'8 / 5’8 / 5＇8", "5'8 5’8 5＇8"),
    ("“GPS” «USB» 'B2' ＇EV＇", "gps usb 'b2' ＇ev＇"),
    ("A-B / 4:45; 2.5%.", "a b 4:45 2.5%"),
    ("지금 GPS를 켤까요？  USB는요!", "지금 gps를 켤까요 usb는요"),
    ("CAFÉ\tÁRBOL\nNIÑO  €30 + 5℃ 🚙", "café árbol niño €30 + 5℃ 🚙"),
    ("I\u0301'M / CAFÉ'S", "i\u0301'm café's"),
    ("  a—b…c、d_e  ", "a b c d e"),
])
def test_normalization_preserves_apostrophes_accents_and_numeric_punctuation(source, expected):
    assert F.normalize_source(source) == expected
    assert F.normalize_source(expected) == expected


@pytest.mark.parametrize("source, expected", [
    ("At 09:25 P.M.!", "at 925 p m"),
    ("By 2:05a.m.", "by 205 a m"),
    ("12:00 AM, then 12:01 pm.", "1200 a m then 1201 p m"),
    ("00:20am / 13:20pm / 4:75am / 04:5pm", "00:20am 13:20pm 4:75am 04:5pm"),
    ("Code X9:25pm, 1.2:30pm; at 23:59.", "code x9:25pm 1.2:30pm at 23:59"),
])
def test_only_valid_english_clocks_with_meridiem_are_compacted(source, expected):
    assert F.normalize_source(source, "en-US") == expected
    assert F.normalize_source(expected, "en-US") == expected


@pytest.mark.parametrize("source, expected", [
    ("Pay 64,700won.", "pay 64 thousand 700 won"),
    ("18,000 KRW; 1,002,005 won", "18 thousand krw 1002 thousand 5 won"),
    ("Refund -42,600 won.", "refund -42 thousand 600 won"),
    ("USD 21,600; $6,005; 8,450 dollars", "usd 21 thousand 600 $ 6 thousand 5 8 thousand 450 dollars"),
    ("1,234 won't fit; code 64,700; ID64,700won", "1,234 won't fit code 64,700 id64,700won"),
    ("USD 2,345.67; 2,345.67 won; 64,70won", "usd 2,345.67 2,345.67 won 64,70won"),
    ("The address is 172.16.0.8; change -0.5%, -.5%, .25.", "the address is 172.16.0.8 change -0.5% -.5% .25"),
])
def test_english_money_conversion_preserves_value_and_currency_without_injecting_errors(source, expected):
    assert F.normalize_source(source, "en-US") == expected
    assert F.normalize_source(expected, "en-US") == expected


@pytest.mark.parametrize("locale, source, expected", [
    ("ko-KR", "오전 9시 25분에 6만 4천원인가요?", "오전 9시 25분에 6만 4천원인가요"),
    ("ko-KR", "09:25에 64,700원, 4.5%예요.", "09:25에 64,700원 4.5%예요"),
    ("es-US", "A las 09:25 P.M., son 64,700 won.", "a las 09:25 p m son 64,700 won"),
    ("es-MX", "Pague 64.700,50 euros, por favor.", "pague 64.700,50 euros por favor"),
])
def test_korean_and_spanish_numeric_formats_do_not_use_english_conversions(locale, source, expected):
    assert F.normalize_source(source, locale) == expected


def test_variant_bytes_uses_each_cases_source_locale():
    cases = [{"id": locale, "source_locale": locale, "text": "09:25 P.M., 64,700 won."}
             for locale in ("en-US", "ko-KR", "es-US")]
    rows = [json.loads(line) for line in F.variant_bytes(cases).decode().splitlines()]
    assert rows[0]["text"] == "925 p m 64 thousand 700 won"
    assert [row["text"] for row in rows[1:]] == ["09:25 p m 64,700 won"] * 2


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
            {"id": f"synthetic-{index}-b", "task": "destination", "source_locale": "en-US", "text": "already normalized",
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
    assert manifest["observations"]["source"] == "user_reported"
    assert manifest["observations"]["independently_reproduced"] is False
    assert manifest["observations"]["environment"]["recognizer"] == "ON_DEVICE"
    for entry, name in zip(manifest["datasets"], F.DATASETS):
        payload = (out / name).read_bytes()
        transformed = [json.loads(line) for line in payload.decode().splitlines()]
        expected = copy.deepcopy(cases[name])
        for row in expected:
            row["text"] = F.normalize_source(row["text"], row["source_locale"])
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
