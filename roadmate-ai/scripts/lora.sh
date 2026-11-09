#!/usr/bin/env bash
# Local teacher comparison, distillation generation, or training/evaluation. Never stops an existing server.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
[ -x .venv/bin/python ] || { echo 'Run bash scripts/run.sh setup first.' >&2; exit 1; }
exec .venv/bin/python scripts/lora_pipeline.py "$@"
