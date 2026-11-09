# 평가
import argparse
import json
import math
from collections import Counter
from pathlib import Path

try:
    from .export_review import ISSUES, REVIEWERS, VERDICTS, cell, issue_types, read_key, read_review, write_files
except ImportError:
    from export_review import ISSUES, REVIEWERS, VERDICTS, cell, issue_types, read_key, read_review, write_files


def tally(verdicts, issues, eligible):
    counts = Counter(verdicts)
    return {"eligible_in_workbook": eligible, "reviewed": len(verdicts), "unreviewed": eligible - len(verdicts),
            "counts": {name: counts[name] for name in VERDICTS},
            "rates": {name: counts[name] / len(verdicts) if verdicts else None for name in VERDICTS},
            "issue_counts": {name: issues[name] for name in ISSUES}}


def transitions(pairs):
    return {"improved": sum(a and not b for a, b in pairs), "worsened": sum(b and not a for a, b in pairs),
            "both": sum(a and b for a, b in pairs), "neither": sum(not a and not b for a, b in pairs)}


def paired_stats(rows, key):
    run_ids = list(key["runs"])
    if len(run_ids) != 2:
        return None
    aligned = []
    for row in rows:
        by_run = {state["run"]: (side, state) for side, state in key["rows"][row["id"]]["sides"].items()}
        aligned.append((row, [by_run[rid] for rid in run_ids]))
    both_ok = [(row, sides) for row, sides in aligned if all(state["status"] == "ok" for _, state in sides)]
    result = {"run_order": run_ids, "aligned_rows": len(aligned), "both_ok_rows": len(both_ok),
              "excluded_non_ok_rows": len(aligned) - len(both_ok), "reviewers": {}}
    for reviewer in (*REVIEWERS, "final"):
        field = "final" if reviewer == "final" else f"{reviewer}_verdict"
        complete, missing = [], [0, 0]
        for row, sides in both_ok:
            grades = [cell(row, field, key).get(side) for side, _ in sides]
            for i, grade in enumerate(grades):
                missing[i] += grade is None
            if all(grades):
                complete.append((row, sides, grades))
        bad_pairs = [(grades[0] == "나쁨", grades[1] == "나쁨") for _, _, grades in complete]
        counts = {rid: sum(pair[i] for pair in bad_pairs) for i, rid in enumerate(run_ids)}
        paired = {"complete_rows": len(complete), "missing_ratings_on_both_ok": dict(zip(run_ids, missing)),
                  "bad_counts": counts, "bad_rates": {rid: count / len(complete) if complete else None for rid, count in counts.items()},
                  "bad_transitions": transitions(bad_pairs),
                  "bad_rate_delta_run2_minus_run1": (counts[run_ids[1]] - counts[run_ids[0]]) / len(complete) if complete else None}
        if reviewer != "final":
            meaning_pairs, missing_issues = [], [0, 0]
            for row, sides, _ in complete:
                issues = cell(row, f"{reviewer}_issue", key)
                for i, (side, _) in enumerate(sides):
                    missing_issues[i] += side not in issues
                if all(side in issues for side, _ in sides):
                    meaning_pairs.append(tuple("뜻 오류" in issue_types(issues[side]) for side, _ in sides))
            paired["meaning_errors"] = {"complete_rows": len(meaning_pairs),
                                        "missing_issue_ratings": dict(zip(run_ids, missing_issues)),
                                        "counts": {rid: sum(pair[i] for pair in meaning_pairs) for i, rid in enumerate(run_ids)},
                                        "transitions": transitions(meaning_pairs)}
        result["reviewers"][reviewer] = paired

    comparable = [sides for _, sides in aligned if all(type(state.get("auto_pass")) is bool for _, state in sides)]
    result["safety"] = {"auto_comparable_rows": len(comparable),
                        "auto_fail_transitions": transitions([(not sides[0][1]["auto_pass"], not sides[1][1]["auto_pass"]) for sides in comparable]),
                        "blocked_502_transitions": transitions([(sides[0][1]["http"] == 502, sides[1][1]["http"] == 502) for sides in comparable]),
                        "per_run": {}}
    for i, rid in enumerate(run_ids):
        states = [sides[i][1] for _, sides in aligned]
        result["safety"]["per_run"][rid] = {"auto_fail": sum(state.get("auto_pass") is False for state in states),
                                          "auto_unscored": sum(type(state.get("auto_pass")) is not bool for state in states),
                                          "blocked_502": sum(state["status"] == "blocked_502" for state in states)}
    result["paired_latency"] = {}
    for metric in ("latency_ms", "wall_ms"):
        pairs = [tuple(state.get(metric) for _, state in sides) for _, sides in aligned
                 if all(state["http"] == 200 and type(state.get(metric)) in (int, float)
                        and math.isfinite(state[metric]) and state[metric] >= 0 for _, state in sides)]
        measures = {}
        for i, rid in enumerate(run_ids):
            values = sorted(pair[i] for pair in pairs)
            measures[rid] = {f"p{percent}": values[round((len(values) - 1) * percent / 100)] if values else None for percent in (50, 95)}
        result["paired_latency"][metric] = {"complete_rows": len(pairs), "per_run": measures,
                                            "p95_delta_run2_minus_run1": measures[run_ids[1]]["p95"] - measures[run_ids[0]]["p95"] if pairs else None}
    return result


def score(rows, key):
    ids = {row["id"] for row in rows}
    result = {"notice": "AI 검토(참고). 학습 자료·사람의 승인·자동 채택 판정이 아닙니다.",
              "coverage": {"workbook_rows": len(rows), "original_rows": len(key["rows"]), "subset": ids != set(key["rows"])},
              "rate_denominator": "Each reviewer's graded ok outputs in this workbook; missing grades are never counted as good.",
              "configurations": {}, "agreement": {}}
    all_agreements = []
    for run_id, run in key["runs"].items():
        metadata = run["metadata"]
        ready = metadata.get("ready") or {}
        runtime = ready.get("runtime_config") or {}
        selected = [(row, side) for row in rows for side, state in key["rows"][row["id"]]["sides"].items() if state["run"] == run_id]
        status = Counter(key["rows"][row["id"]]["sides"][side]["status"] for row, side in selected)
        full_status = Counter(state["status"] for entry in key["rows"].values() for state in entry["sides"].values() if state["run"] == run_id)
        eligible = [(row, side) for row, side in selected if key["rows"][row["id"]]["sides"][side]["status"] == "ok"]
        config = {"label": metadata.get("label", run_id),
                  "model_profile": metadata.get("model_profile") or ready.get("model_profile"),
                  "runtime_config": runtime,
                  "statuses_in_workbook": dict(status), "statuses_original": dict(full_status), "reviewers": {}}
        for reviewer in (*REVIEWERS, "final"):
            verdict_field = "final" if reviewer == "final" else f"{reviewer}_verdict"
            verdicts, issues = [], Counter()
            for row, side in eligible:
                verdict = cell(row, verdict_field, key).get(side)
                if verdict:
                    verdicts.append(verdict)
                    if reviewer != "final":
                        issues.update(set(issue_types(cell(row, f"{reviewer}_issue", key).get(side, []))))
            stats = tally(verdicts, issues, len(eligible))
            if reviewer == "final":
                stats.pop("issue_counts")
                config["final_adjudication"] = stats
            else:
                config["reviewers"][reviewer] = stats
        agreements = []
        for row, side in eligible:
            verdicts = [cell(row, f"{name}_verdict", key).get(side) for name in REVIEWERS]
            if all(verdicts):
                agreements.append(verdicts[0] == verdicts[1])
        config["agreement"] = {"jointly_reviewed": len(agreements), "agreed": sum(agreements),
                               "rate": sum(agreements) / len(agreements) if agreements else None}
        all_agreements.extend(agreements)
        result["configurations"][run_id] = config
    result["agreement"] = {"jointly_reviewed": len(all_agreements), "agreed": sum(all_agreements),
                           "rate": sum(all_agreements) / len(all_agreements) if all_agreements else None}
    result["paired_comparison"] = paired_stats(rows, key)
    return result


def summary_markdown(result):
    def pct(value):
        return "N/A" if value is None else f"{value:.1%}"

    def safe(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("<", "&lt;")

    coverage = result["coverage"]
    lines = ["# AI 검토(참고) 집계", "", result["notice"], "",
             f"- 검토 파일 범위: {coverage['workbook_rows']}/{coverage['original_rows']}개 원문 행",
             "- 비율의 분모: 해당 검토자가 판정한 정상 번역 수. 미검토 출력은 좋음으로 간주하지 않습니다.",
             "- 502·확인 질문·출력 없음·기타 오류는 품질 비율에서 제외하고 별도로 셉니다.",
             "- final은 사용자가 기록한 별도 판정란입니다. 이 도구는 채우거나 사람의 검수 여부를 인증하지 않습니다."]
    if coverage["subset"]:
        lines.append("- **부분집합 집계입니다. 필터된 파일의 비율을 전체 실행 품질로 해석하지 마세요.**")
    lines += ["", "| 설정 | 프로필 | 판정자 | 검토/정상 출력 | 좋음 | 어색함 | 나쁨 |",
              "|---|---|---|---:|---:|---:|---:|"]
    for rid, config in result["configurations"].items():
        for name, stats in [*config["reviewers"].items(), ("final (별도 기록)", config["final_adjudication"])]:
            rates = [f"{stats['counts'][v]} ({pct(stats['rates'][v])})" for v in VERDICTS]
            lines.append(f"| {rid}: {safe(config['label'])} | {safe(config['model_profile'])} | {name} | "
                         f"{stats['reviewed']}/{stats['eligible_in_workbook']} | " + " | ".join(rates) + " |")
    for rid, config in result["configurations"].items():
        lines += ["", f"## {rid}", "", f"- 실행 설정: {json.dumps(config['runtime_config'], ensure_ascii=False)}",
                  f"- 파일 내 출력 상태: {json.dumps(config['statuses_in_workbook'], ensure_ascii=False)}",
                  f"- 원본 전체 출력 상태: {json.dumps(config['statuses_original'], ensure_ascii=False)}"]
        for name, stats in config["reviewers"].items():
            counts = {issue: n for issue, n in stats["issue_counts"].items() if n}
            lines.append(f"- {name} 문제 유형(출력당 각 유형 1회): {json.dumps(counts, ensure_ascii=False)}")
        agreement = config["agreement"]
        lines.append(f"- Claude–Codex 일치: {agreement['agreed']}/{agreement['jointly_reviewed']} ({pct(agreement['rate'])})")
    agreement = result["agreement"]
    lines += ["", f"전체 Claude–Codex 일치: {agreement['agreed']}/{agreement['jointly_reviewed']} ({pct(agreement['rate'])}). "
              "같은 출력을 두 검토자가 모두 판정한 경우만 분모에 포함합니다."]
    paired = result["paired_comparison"]
    if paired:
        left, right = paired["run_order"]
        lines += ["", "## 같은 행의 두 출력을 모두 판정한 비교", "",
                  f"- 방향: {left} → {right}. 양쪽 정상 출력 {paired['both_ok_rows']}/{paired['aligned_rows']}행; "
                  f"한쪽 이상 비정상 {paired['excluded_non_ok_rows']}행은 품질 비교에서 제외합니다.",
                  "- 각 판정자의 두 출력 판정이 모두 있는 같은 행만 나쁨 비율·개선/악화 비교에 씁니다.",
                  "- 채택 기준 참고: 같은 행 나쁨 감소, FAIL·502·뜻 오류 악화 없음, p95 증가 300ms 이하. 자동 채택 판정은 하지 않습니다.", "",
                  "| 판정자 | 양쪽 판정 행 | 누락 판정 (run1/run2) | run1 나쁨 | run2 나쁨 | 개선 / 악화 |",
                  "|---|---:|---:|---:|---:|---:|"]
        for name, stats in paired["reviewers"].items():
            change = stats["bad_transitions"]
            lines.append(f"| {name} | {stats['complete_rows']} | {stats['missing_ratings_on_both_ok'][left]} / "
                         f"{stats['missing_ratings_on_both_ok'][right]} | {stats['bad_counts'][left]} ({pct(stats['bad_rates'][left])}) | "
                         f"{stats['bad_counts'][right]} ({pct(stats['bad_rates'][right])}) | {change['improved']} / {change['worsened']} |")
        lines += ["", "뜻 오류는 양쪽 문제 유형을 명시한 행만 비교합니다. 빈 셀은 오류 없음으로 간주하지 않습니다."]
        for name, stats in paired["reviewers"].items():
            if "meaning_errors" not in stats:
                continue
            meaning = stats["meaning_errors"]
            lines.append(f"- {name}: {meaning['complete_rows']}행, 뜻 오류 {meaning['counts'][left]} → {meaning['counts'][right]}, "
                         f"개선/악화 {meaning['transitions']['improved']}/{meaning['transitions']['worsened']}; "
                         f"문제 유형 누락 {meaning['missing_issue_ratings'][left]}/{meaning['missing_issue_ratings'][right]}.")
        safety = paired["safety"]
        lines += ["", f"- 파일 내 자동 안전 지표: {json.dumps(safety['per_run'], ensure_ascii=False)}",
                  f"- 양쪽 자동 판정이 있는 {safety['auto_comparable_rows']}행의 FAIL 개선/악화: "
                  f"{safety['auto_fail_transitions']['improved']}/{safety['auto_fail_transitions']['worsened']}; "
                  f"502 개선/악화: {safety['blocked_502_transitions']['improved']}/{safety['blocked_502_transitions']['worsened']}.",
                  "- 지연은 양쪽 HTTP 200과 같은 지연 측정값이 있는 행끼리 비교합니다(분위수 방식은 evaluate.py와 동일)."]
        for metric, stats in paired["paired_latency"].items():
            left_ms, right_ms = stats["per_run"][left], stats["per_run"][right]
            lines.append(f"- {metric}: {stats['complete_rows']}행, p50/p95 {left_ms['p50']}/{left_ms['p95']} → "
                         f"{right_ms['p50']}/{right_ms['p95']} ms; p95 차이 {stats['p95_delta_run2_minus_run1']} ms.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description='블라인드 AI 검토 결과를 설정별로 집계합니다.')
    parser.add_argument("review", type=Path)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        key = read_key(args.key)
        rows, _ = read_review(args.review, key)
        result = score(rows, key)
        md = summary_markdown(result)
        write_files({args.out_dir / "review_summary.json": json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                     args.out_dir / "review_summary.md": md})
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(2, f"review scoring failed: {exc}\n")
    print(md)
    print(f"saved: {args.out_dir}")


if __name__ == "__main__":
    main()
