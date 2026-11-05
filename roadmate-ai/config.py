import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_dotenv(path: Path = ROOT / ".env"):
    # 백엔드 연동: 서버·smoke·평가가 같은 .env(AI_INTERNAL_TOKEN 등)를 읽는다. 이미 설정된 환경 변수가 우선
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m:
            os.environ.setdefault(m.group(1), m.group(2).strip())


def load_manifest(path: Path = ROOT / "models" / "manifest.json") -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Settings:
    model_backend: str  # llama | mock
    model_base_url: str
    model_id: str
    engine_bundle: str
    model_timeout_s: float
    busy_wait_s: float
    cancel_settle_s: float
    dedupe_ttl_s: float
    max_text_chars: int
    internal_token: str
    manifest: dict
    log_rejected: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        m = load_manifest()
        env = os.environ.get
        return cls(
            model_backend=env("MODEL_BACKEND", "llama"),
            # 백엔드 연동: AI 서버 → 로컬 llama-server
            model_base_url=env("MODEL_BASE_URL", f"http://127.0.0.1:{m['runtime']['port']}/v1"),
            model_id=env("MODEL_ID", m["model"]["alias"]),
            engine_bundle=m["engine_bundle"],
            # 파라미터 튜닝: 모델 8초 < 백엔드 10초 < 앱 12초
            model_timeout_s=float(env("MODEL_TIMEOUT_S", "8")),
            busy_wait_s=float(env("BUSY_WAIT_S", "0.3")),
            cancel_settle_s=float(env("CANCEL_SETTLE_S", "3")),
            dedupe_ttl_s=float(env("DEDUPE_TTL_S", "30")),
            max_text_chars=int(env("MAX_TEXT_CHARS", "400")),
            # 백엔드 연동: 백엔드와 공유하는 내부 토큰(비어 있으면 검사 안 함)
            internal_token=env("AI_INTERNAL_TOKEN", ""),
            manifest=m,
            # 검증: 개발·평가 전용. 1이면 검사에 걸린 원문과 모델 출력을 로그에 남긴다(실사용 금지, 개인정보)
            log_rejected=env("LOG_REJECTED", "0") == "1",
        )
