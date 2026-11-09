import dataclasses
import json
import sys
from pathlib import Path

# API 문서
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from config import Settings  # noqa: E402
from inference import MockClient  # noqa: E402

spec = create_app(dataclasses.replace(Settings.from_env(), model_backend="mock"), MockClient()).openapi()
out = ROOT / "docs" / "openapi.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"wrote {out}")
