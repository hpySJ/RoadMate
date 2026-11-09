# 평가
import argparse
import csv
import hashlib
import io
import json
import re
import secrets
from pathlib import Path


BASE_FIELDS = ["id", "direction", "source", "A", "B"]
REVIEWERS = ("claude", "codex")
REVIEW_FIELDS = [f"{name}_{kind}" for name in REVIEWERS for kind in ("verdict", "issue", "rewrite")]
FIELDS = BASE_FIELDS + REVIEW_FIELDS + ["final", "notes"]
VERDICTS = ("좋음", "어색함", "나쁨")
ISSUES = ("뜻 오류", "직역 표현", "낱말·용어 오역", "대명사·말투", "문법", "외국어 섞임", "현장에서 안 쓰는 표현")


def packed(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def immutable_hash(row):
    return hashlib.sha256(packed({name: row[name] for name in BASE_FIELDS}).encode("utf-8")).hexdigest()


def key_hash(key):
    return hashlib.sha256(packed({k: v for k, v in key.items() if k != "integrity_sha256"}).encode("utf-8")).hexdigest()


def load_run(folder):
    folder = Path(folder)
    metadata = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError("run.json must contain an object")
    if Path(str(metadata.get("dataset", ""))).name.startswith("test.locked"):
        raise ValueError("Locked evaluation results are not accepted by this workflow")
    raw = (folder / "results.jsonl").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if metadata.get("results_sha256") and metadata["results_sha256"] != digest:
        raise ValueError(f"Results hash mismatch: {folder}")
    records = {}
    for line in raw.decode("utf-8-sig").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        case = record["case"]
        cid = case["id"]
        if not isinstance(cid, str) or not cid or cid in records:
            raise ValueError(f"Missing or duplicate case id: {cid!r}")
        records[cid] = record
    return records, {"path": str(folder.resolve()), "metadata": metadata, "results_sha256": digest}


def is_translation(record):
    case = record["case"]
    return case.get("task") == "translate" and str(case.get("target_locale", "")).split("-")[0] in {"ko", "en", "es"}


def output(record):
    if record is None:
        return "[출력 없음]", {"status": "missing", "http": None}
    body = record.get("body") or {}
    http = record.get("http")
    state = {"http": http, "auto_pass": record.get("pass"), "flags": record.get("flags", []),
             "latency_ms": body.get("latency_ms"), "wall_ms": record.get("wall_ms")}
    if http == 502:
        return "[502 · 안전 검사 거절]", {**state, "status": "blocked_502"}
    if http == 200 and body.get("status") == "clarification_required":
        return "[확인 질문] " + (body.get("clarification_question") or ""), {**state, "status": "clarification"}
    if http == 200 and body.get("status") == "ok":
        text = body.get("translated_text")
        if isinstance(text, str) and text.strip():
            return text, {**state, "status": "ok"}
        return "[출력 없음]", {**state, "status": "missing"}
    return f"[오류 · HTTP {http}]", {**state, "status": "error"}


def export_runs(folders):
    if len(folders) not in (1, 2):
        raise ValueError("Supply one or two result folders")
    loaded = [load_run(folder) for folder in folders]
    runs = {f"run{i + 1}": metadata for i, (_, metadata) in enumerate(loaded)}
    records = [item[0] for item in loaded]
    ids = sorted({cid for run in records for cid, record in run.items() if is_translation(record)})
    rows, keys = [], {}
    for cid in ids:
        found = [run.get(cid) for run in records]
        cases = [record["case"] for record in found if record is not None]
        case = cases[0]
        identity = ("task", "source_locale", "target_locale", "text")
        if any(tuple(c.get(k) for k in identity) != tuple(case.get(k) for k in identity) for c in cases):
            raise ValueError(f"Source or direction mismatch for {cid}")
        if not all(isinstance(case.get(k), str) and case[k] for k in identity):
            raise ValueError(f"Invalid case identity for {cid}")
        order = list(range(len(records)))
        secrets.SystemRandom().shuffle(order)
        row = dict.fromkeys(FIELDS, "")
        row.update(id=cid, direction=f"{case['source_locale']}>{case['target_locale']}", source=case["text"])
        mapping = {}
        for side, index in zip(("A", "B"), order):
            row[side], state = output(found[index])
            mapping[side] = {"run": f"run{index + 1}", **state}
        rows.append(row)
        keys[cid] = {"immutable_sha256": immutable_hash(row), "sides": mapping}
    if not rows:
        raise ValueError("No ko/en/es translation results found")
    key = {"schema": 1, "review_id": secrets.token_hex(16), "runs": runs, "rows": keys}
    return rows, key


def read_key(path):
    key = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(key, dict) or key.get("integrity_sha256") != key_hash(key):
        raise ValueError("Review key integrity mismatch")
    if (key.get("schema") != 1 or not isinstance(key.get("runs"), dict) or len(key["runs"]) not in (1, 2)
            or not isinstance(key.get("rows"), dict) or not key["rows"]
            or any(not isinstance(run, dict) or not isinstance(run.get("metadata"), dict) for run in key["runs"].values())):
        raise ValueError("Invalid review key")
    for entry in key["rows"].values():
        if not isinstance(entry, dict) or not isinstance(entry.get("sides"), dict):
            raise ValueError("Invalid key row")
        sides = entry.get("sides", {})
        if set(sides) != set(("A",) if len(key["runs"]) == 1 else ("A", "B")):
            raise ValueError("Invalid key sides")
        if any(not isinstance(side, dict) for side in sides.values()) or {side.get("run") for side in sides.values()} != set(key["runs"]):
            raise ValueError("Invalid key run mapping")
        if any(side.get("status") not in {"ok", "blocked_502", "clarification", "missing", "error"} for side in sides.values()):
            raise ValueError("Invalid key output status")
    return key


def issue_types(value):
    return value.get("types", []) if isinstance(value, dict) else value


def cell(row, field, key):
    raw = row.get(field, "")
    if not raw.strip():
        return {}
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{row['id']} {field}: use a JSON object keyed by A/B") from exc
    if not isinstance(values, dict):
        raise ValueError(f"{row['id']} {field}: expected a JSON object")
    sides = key["rows"][row["id"]]["sides"]
    for side, value in values.items():
        if side not in sides or sides[side]["status"] != "ok":
            raise ValueError(f"{row['id']} {field}: {side} is not an eligible translation")
        if field.endswith("verdict") or field == "final":
            valid = value in VERDICTS if isinstance(value, str) else False
        elif field.endswith("issue"):
            types = issue_types(value)
            valid = isinstance(types, list) and all(isinstance(x, str) and x in ISSUES for x in types)
            if isinstance(value, dict):
                valid = valid and "types" in value and set(value) <= {"types", "note"} and isinstance(value.get("note", ""), str)
        else:
            valid = isinstance(value, str)
        if not valid:
            raise ValueError(f"{row['id']} {field}: invalid value for {side}")
    return values


def read_review(path, key):
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if len(set(fields)) != len(fields) or not set(BASE_FIELDS) <= set(fields) or not set(fields) <= set(FIELDS):
            raise ValueError("Invalid review columns")
        rows = list(reader)
    seen = set()
    for row in rows:
        cid = row["id"]
        if None in row or any(value is None for value in row.values()) or cid in seen or cid not in key["rows"]:
            raise ValueError(f"Malformed, duplicate or unknown review row: {cid}")
        seen.add(cid)
        if immutable_hash(row) != key["rows"][cid]["immutable_sha256"]:
            raise ValueError(f"Source, direction or output was edited, or wrong key: {cid}")
        for field in REVIEW_FIELDS + ["final"]:
            cell(row, field, key)
        for reviewer in REVIEWERS:
            verdicts = cell(row, f"{reviewer}_verdict", key)
            for side, issue in cell(row, f"{reviewer}_issue", key).items():
                if "뜻 오류" in issue_types(issue) and side in verdicts and verdicts[side] != "나쁨":
                    raise ValueError(f"{cid} {reviewer}: meaning errors must be rated 나쁨")
    return rows, fields


def merge_reviews(paths, key, base=None):
    reviews = []
    for name, path in zip(REVIEWERS, paths):
        rows, fields = read_review(path, key)
        allowed = BASE_FIELDS + [f"{name}_{kind}" for kind in ("verdict", "issue", "rewrite")]
        if set(fields) != set(allowed):
            raise ValueError(f"{name} input must be an independent --reviewer {name} workbook")
        reviews.append({row["id"]: row for row in rows})
    if set(reviews[0]) != set(reviews[1]):
        raise ValueError("Reviewer workbooks must cover the same rows")
    if base is None:
        merged = [{**dict.fromkeys(FIELDS, ""), **{k: row[k] for k in BASE_FIELDS}} for row in reviews[0].values()]
    else:
        merged = [{**dict.fromkeys(FIELDS, ""), **row} for row in base]
        if not set(reviews[0]) <= {row["id"] for row in merged}:
            raise ValueError("Reviewer workbooks contain rows absent from the base review")
    for row in merged:
        for name, review in zip(REVIEWERS, reviews):
            addition = review.get(row["id"], {})
            for kind in ("verdict", "issue", "rewrite"):
                field = f"{name}_{kind}"
                before = cell(row, field, key)
                after = cell(addition, field, key) if addition else {}
                if any(side in before and before[side] != value for side, value in after.items()):
                    raise ValueError(f"Refusing to replace an existing opinion: {row['id']} {field}")
                combined = {**before, **after}
                if combined:
                    row[field] = packed(combined)
    return merged


def select_rows(rows, key, only):
    if not only:
        return rows
    chosen = []
    for row in rows:
        claude, codex = (cell(row, f"{name}_verdict", key) for name in REVIEWERS)
        if only == "disagreements":
            keep = any(claude[side] != codex[side] for side in claude.keys() & codex.keys())
        else:
            keep = "나쁨" in (*claude.values(), *codex.values(), *cell(row, "final", key).values())
        if keep:
            chosen.append(row)
    return chosen


def markdown(rows, fields):
    def escaped(value):
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\r", "").replace("\n", "<br>")

    lines = ["# AI 검토(참고)", "", "A/B는 행마다 독립적으로 배치됩니다. 뜻이 달라지면 문체와 관계없이 ‘나쁨’입니다.",
             "판정·문제·대안 셀은 A/B를 키로 하는 JSON입니다. 대안은 검토 근거이며 학습·프롬프트 자료가 아닙니다.",
             "502·확인 질문·출력 없음·오류는 번역 품질 판정 대상에서 제외합니다.", "",
             "| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    lines.extend("| " + " | ".join(escaped(row.get(field, "")) for field in fields) + " |" for row in rows)
    return "\n".join(lines) + "\n"


def write_files(contents):
    paths = list(contents)
    if any(path.exists() for path in paths):
        raise FileExistsError("Refusing to overwrite existing review files")
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
    reserved = []
    try:
        for path in paths:
            with path.open("x", encoding="utf-8"):
                pass
            reserved.append(path)
        for path, content in contents.items():
            path.write_text(content, encoding="utf-8", newline="")
    except BaseException:
        for path in reserved:
            path.unlink(missing_ok=True)
        raise


def write_review(rows, key, out_dir, label, fields=None, include_key=True):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", label):
        raise ValueError("Use a short neutral filename label containing only letters, digits, _, . or -")
    fields = fields or FIELDS
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    stem = Path(out_dir) / f"review_{label}"
    suffixes = [("csv", ".csv"), ("md", ".md")] + ([("key", ".key.json")] if include_key else [])
    paths = {kind: Path(str(stem) + suffix) for kind, suffix in suffixes}
    contents = {paths["csv"]: "\ufeff" + stream.getvalue(), paths["md"]: markdown(rows, fields)}
    if include_key:
        sealed_key = {**key, "integrity_sha256": key_hash(key)}
        contents[paths["key"]] = json.dumps(sealed_key, ensure_ascii=False, indent=2) + "\n"
    write_files(contents)
    return paths


def main(argv=None):
    parser = argparse.ArgumentParser(description='AI 검토용 블라인드 비교 파일을 만듭니다.')
    parser.add_argument("runs", type=Path, nargs="*")
    parser.add_argument("--review", type=Path, help="Existing review; preserves its judgments and notes")
    parser.add_argument("--key", type=Path, help="Private key from the original export")
    parser.add_argument("--reviewer", choices=REVIEWERS, help="Export only this reviewer's columns, excluding final/notes")
    parser.add_argument("--merge", type=Path, nargs=2, metavar=("CLAUDE_CSV", "CODEX_CSV"))
    parser.add_argument("--only", choices=("disagreements", "bad"))
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--label", required=True, help="Neutral output filename label, e.g. pair01")
    args = parser.parse_args(argv)
    try:
        if args.runs:
            if args.review or args.key or args.merge or args.only or args.reviewer:
                raise ValueError("Result folders cannot be combined with existing-review options")
            rows, key = export_runs(args.runs)
            fields = FIELDS
        else:
            if not args.key or not (args.review or args.merge):
                raise ValueError("Use result folders, or --key with --review/--merge")
            key = read_key(args.key)
            rows, fields = read_review(args.review, key) if args.review else (None, FIELDS)
            if args.merge:
                rows = merge_reviews(args.merge, key, rows)
                fields = FIELDS
            rows = select_rows(rows, key, args.only)
        if args.reviewer:
            fields = BASE_FIELDS + [f"{args.reviewer}_{kind}" for kind in ("verdict", "issue", "rewrite")]
        paths = write_review(rows, key, args.out_dir, args.label, fields, include_key=not args.reviewer)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"review export failed: {exc}\n")
    print(f"AI 검토(참고): {len(rows)} rows")
    for kind, path in paths.items():
        print(f"{kind}: {path}")
    print("Keep the key private; give each reviewer only their own CSV/MD. No human approval or training data is produced.")


if __name__ == "__main__":
    main()
