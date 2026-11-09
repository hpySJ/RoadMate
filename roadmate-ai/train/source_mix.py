# 원문 구성
from collections import Counter

from train.data_boundary import fingerprint

POLICY_VERSION = "teacher-majority-v1"


def source_mix(rows: list[dict]) -> dict:
    origins = Counter(row.get("source_origin") or "unspecified" for row in rows)
    total = len(rows)
    teacher = origins["teacher_generated"]
    return {"total_rows": total, "teacher_rows": teacher, "supplemental_rows": total - teacher,
            "teacher_share": teacher / total if total else None,
            "unique_sources": len({fingerprint(row["text"], row["source_locale"]) for row in rows}),
            "groups": len({row["group_id"] for row in rows}), "by_origin": dict(sorted(origins.items()))}


def check_source_mix(rows: list[dict]) -> dict:
    stats = source_mix(rows)
    if not stats["total_rows"] or stats["teacher_rows"] * 5 < stats["total_rows"] * 4:
        raise ValueError("teacher-generated sources must be at least 80% of rows "
                         f"({stats['teacher_rows']}/{stats['total_rows']})")
    return stats
