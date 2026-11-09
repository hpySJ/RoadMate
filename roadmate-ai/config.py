import json
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODEL_PROFILES = {"4b-q4": "manifest.json", "4b-q8": "manifest.qwen3-4b-q8.json", "8b-q4": "manifest.qwen3-8b.json",
                  "14b-q4": "manifest.qwen3-14b.json", "32b-q4": "manifest.qwen3-32b.json"}
TEACHER_PROFILES = ("8b-q4", "14b-q4", "32b-q4")


def artifact_hashes() -> dict[str, str]:
    """평가한 서버와 제출 코드·문구가 같은지 비교한다. .env·개인정보는 포함하지 않는다."""
    paths = [*ROOT.glob("*.py"), *sorted((ROOT / "prompts").glob("*.*")),
             *sorted((ROOT / "packs").glob("*.json")), manifest_path()]
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(paths) if p.is_file()}


def load_dotenv(path: Path = ROOT / ".env"):
    # 백엔드 연동: 서버·smoke·평가가 같은 .env(AI_INTERNAL_TOKEN 등)를 읽는다. 이미 설정된 환경 변수가 우선
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m:
            os.environ.setdefault(m.group(1), m.group(2).strip())


def model_profile() -> str:
    load_dotenv()
    name = os.environ.get("MODEL_PROFILE", "8b-q4")
    if name not in MODEL_PROFILES:
        raise ValueError(f"unknown MODEL_PROFILE {name!r}; choose {', '.join(MODEL_PROFILES)}")
    return name


def manifest_path() -> Path:
    return ROOT / "models" / MODEL_PROFILES[model_profile()]


def load_manifest(path: Path | None = None) -> dict:
    manifest = json.loads((path or manifest_path()).read_text(encoding="utf-8"))
    # Explicit paths inspect immutable base profiles; the environment selects a per-run adapter.
    adapter = os.environ.get("LORA_ADAPTER") if path is None else None
    if not adapter:
        return manifest
    from runtime_identity import digest
    file = Path(adapter).resolve(strict=True)
    sidecar = json.loads(Path(str(file) + ".json").read_text(encoding="utf-8"))
    expected = {key: manifest["model"][key] for key in ("base_repo", "base_revision", "gguf_sha256")}
    if sidecar.get("base") != expected:
        raise ValueError("LoRA base model/revision/hash does not match MODEL_PROFILE")
    if sidecar.get("adoption_status") == "rejected":
        raise ValueError("LoRA adapter rejected by paired evaluation")
    if sidecar.get("smoke") or sidecar.get("allow_draft", True):
        raise ValueError("smoke/draft LoRA cannot be served")
    if not all(re.fullmatch(r"[0-9a-f]{64}", str(sidecar.get(key, "")))
               for key in ("sha256", "dataset_sha256", "training_meta_sha256", "ai_review_sha256")):
        raise ValueError("LoRA sidecar has incomplete training/data provenance")
    if (sidecar.get("target_source") != "qwen_teacher"
            or sidecar.get("review_status") != "auto_filtered_ai_sample_reviewed"):
        raise ValueError("LoRA requires teacher targets with recorded AI sample review")
    if sidecar["sha256"] != digest(file):
        raise ValueError("LoRA adapter SHA256 mismatch")
    if sidecar.get("enable_thinking") is not False:
        raise ValueError("LoRA was not trained with enable_thinking=False")
    manifest["adapter"] = {"file": str(file), "sha256": sidecar["sha256"],
                           "base_revision": expected["base_revision"],
                           "dataset_sha256": sidecar["dataset_sha256"]}
    manifest["engine_bundle"] += "-lora-" + sidecar["sha256"][:12]
    return manifest


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
    model_profile: str = "8b-q4"

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
            model_profile=model_profile(),
            # 검증: 개발·평가 전용. 1이면 검사에 걸린 원문과 모델 출력을 로그에 남긴다(실사용 금지, 개인정보)
            log_rejected=env("LOG_REJECTED", "0") == "1",
        )
