# 평가
import csv
import json
import sys
from pathlib import Path


def load(run: Path) -> dict:
    rows = [json.loads(x) for x in (run / "results.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    return {r["case"]["id"]: r for r in rows}


def text(r: dict) -> str:
    b = r["body"]
    return b.get("translated_text") or b.get("clarification_question") or b.get("error_code") or ""


def main():
    a_dir, b_dir = map(Path, sys.argv[1:3])
    a, b = load(a_dir), load(b_dir)
    out = b_dir / f"compare-{a_dir.name}.csv"
    changed = 0
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "direction", "text", "a", "b", "a_pass", "b_pass", "better(a/b/same)", "notes"])
        for cid, ra in a.items():
            rb = b.get(cid)
            if not rb or ra["case"]["task"] != "translate":
                continue
            changed += text(ra) != text(rb)
            c = ra["case"]
            w.writerow([cid, f"{c['source_locale']}>{c['target_locale']}", c["text"], text(ra), text(rb),
                        ra["pass"], rb["pass"], "", ""])
    print(f"{changed} translations differ -> {out}")


if __name__ == "__main__":
    main()
