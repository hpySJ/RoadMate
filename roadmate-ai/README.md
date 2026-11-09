# RoadMate AI

정차 중 한국어↔영어·스페인어 통역용 로컬 AI 서버. 외부 AI API 없이 llama.cpp와 FastAPI로 실행함. 목적지 후보 검색과 주행 안내 규칙 포함.

최종 설정은 **Qwen3-8B Q4_K_M + translate-v11 + 스페인어 ¿? 복원 규칙**. 복원 대상에 `cuándo`·`quién`·`quiénes` 포함. LoRA 실험은 종료했으며 어댑터 없이 실행함. 결과·한계는 [제출 요약](docs/submission_summary.md) 참고.

```text
Android → AI /translate (127.0.0.1:8000) → llama-server (127.0.0.1:8081)
```

## 설치·실행

AI 저장소 루트에서 실행. Windows는 Python 3.11과 PowerShell 필요함. 모델 없이 API 계약 확인 가능.

```powershell
.\scripts\setup_ai_env.ps1
.\scripts\start_all.ps1 -Mock
.\scripts\stop_all.ps1
```

실제 번역은 NVIDIA GPU PC에 모델 설치 후 실행.

```powershell
.\scripts\setup_llama.ps1
.\.venv\Scripts\python.exe scripts\download_model.py
.\scripts\start_all.ps1
```

Linux는 `bash scripts/run.sh setup`, `bash scripts/run.sh start`, `bash scripts/run.sh stop` 사용. 모델 로딩 후 `/health/ready` 200 확인 필요함. 설치 조건·모델 프로필·최종 평가 명령·LoRA 종료 기록은 [사용 안내](docs/guide.md) 참고.

## API

| API | 용도 |
|---|---|
| `POST /translate` | 앱 통역: `{"text","from","to","situation"}` → `{"text"}` |
| `POST /internal/v1/translate` | 요청 ID·세션·epoch를 사용하는 내부 통역 |
| `POST /internal/v1/parse-destination` | 목적지 검색어와 데모 장소 후보 |
| `POST /internal/v1/cancel` | 내부 세션 취소 |
| `GET /health/ready` | 모델·AI 준비 상태 |

통역 언어는 `ko-KR` ↔ `en-US`·`es-US`·`es-MX`. 앱 상황 값 `rental`은 `rental_counter`로 매핑함. 재질문 응답은 `text`를 생략하고 `clarification`, `clarification_locale`을 반환함. 현재 앱은 문장 카드로 대체함. 전체 계약은 [OpenAPI](docs/openapi.json) 참고.

팀 Android `9945ab18`의 요청 형식 확인함. 실제 APK의 네트워크 권한·서버 URL 설정과 Pleos STT→HTTP→TTS 검증은 앱 측 확인 필요함. 앱 전체 제한은 3초, AI 기본 제한은 8초. 연결 시연에서는 `MODEL_TIMEOUT_S=2.5` 설정 후 실제 응답 시간 측정 필요함.

## 구성

| 위치 | 내용 |
|---|---|
| `app.py`, `schemas.py`, `sessions.py` | HTTP 계약·요청 수명·취소 |
| `engine.py`, `inference.py`, `runtime_identity.py` | 모델 호출·재시도·실행 모델 확인 |
| `prompting.py`, `prompts/`, `validation.py` | 프롬프트 조립·출력 검사 |
| `guidance.py`, `packs/` | 주행 규칙·용어집·데모 장소 |
| `models/manifest*.json` | 모델 파일·revision·해시·실행 설정 |
| `scripts/`, `tests/` | 설치·실행·검증 |
| `eval/`, `train/` | 평가·검토·보존된 증류·QLoRA 도구 |
| `docs/` | 사용 안내·API·제출 요약·PR 초안 |

## 검증

```powershell
.\.venv\Scripts\python.exe scripts\verify_mvp.py --local
```

결과는 `run/verify-*/report.json`에 기록됨. 로컬 검사는 API·검사기·주행 규칙 대상. 실제 모델의 의미 정확성·앱 3초 응답은 별도 실측 필요함. 검증 상태는 [사용 안내 11장](docs/guide.md#11-검증-상태) 참고.

모델·가상환경·`.env`·로그·평가 결과·학습 결과는 Git 제외 대상. 평가셋은 학습·프롬프트 예시로 사용 금지. 잠근 시험은 튜닝에 사용하지 않고 확정 설정으로 `--final` 1회 실행 예정.
