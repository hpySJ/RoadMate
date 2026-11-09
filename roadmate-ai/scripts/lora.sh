#!/usr/bin/env bash
# Local teacher comparison, distillation generation, or training/evaluation. Never stops an existing server.
# bash scripts/lora.sh teacher-check|generate|train-eval --teacher 14b-q4|32b-q4 [stage options]
# Default teacher: 14b-q4. 32b-q4 requires full CUDA0 offload; no CPU fallback.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -x .venv/bin/python ] || { echo 'Run bash scripts/run.sh setup first.' >&2; exit 1; }
exec .venv/bin/python scripts/lora_pipeline.py "$@"
