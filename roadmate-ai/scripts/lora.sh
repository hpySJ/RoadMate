#!/usr/bin/env bash
# 학습 단계
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -x .venv/bin/python ] || { echo 'Run bash scripts/run.sh setup first.' >&2; exit 1; }
exec .venv/bin/python scripts/lora_pipeline.py "$@"
