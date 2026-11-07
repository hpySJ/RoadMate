"""로컬 회귀검사 또는 실제 모델 품질까지 검증. --local은 모델 검증 완료를 뜻하지 않는다."""
import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import artifact_hashes  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--local", action="store_true", help="GPU 없이 코드·계약·페르소나만 검증")
    ap.add_argument("--runs", nargs="+", default=[], help="실제 모델 평가와 검토가 끝난 결과 폴더 5개")
    args = ap.parse_args()
    if args.local and args.runs:
        ap.error("--local과 --runs는 함께 쓸 수 없습니다")
    out = ROOT / "run" / f"verify-{uuid.uuid4().hex[:10]}"
    out.mkdir(parents=True)
    commands = [[sys.executable, "-B", "-m", "pytest", "-p", "no:cacheprovider", "-q", "--basetemp", str(out / "tmp")],
                [sys.executable, "-B", "guidance.py"]]
    if not args.local and args.runs:
        commands.append([sys.executable, "-B", "eval/release_gate.py", *args.runs])
    checks = []
    for i, cmd in enumerate(commands):
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
        (out / f"check-{i}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        checks.append({"command": cmd, "passed": result.returncode == 0})
        print(("PASS" if result.returncode == 0 else "FAIL"), " ".join(cmd[2:]))
        if result.returncode:
            print(result.stdout + result.stderr)
            break
    passed = len(checks) == len(commands) and all(c["passed"] for c in checks)
    release = passed and not args.local and bool(args.runs)
    report = {"local_passed": len(checks) >= 2 and all(c["passed"] for c in checks[:2]), "model_quality_verified": release,
              "artifact_hashes": artifact_hashes(), "checks": checks,
              "remaining": ([] if release else ["실제 모델 평가 5종과 의미·자연스러움 검토 필요"])
                           + ["Pleos SDK·에뮬레이터 실기기 검증 필요"]}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report: {out / 'report.json'}")
    print("LOCAL CHECKS PASSED" if args.local and passed else "MODEL QUALITY GATE PASSED" if release else "MVP VERIFICATION INCOMPLETE")
    return int(not (passed and (args.local or release)))


if __name__ == "__main__":
    raise SystemExit(main())
