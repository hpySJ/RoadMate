# roadmate-ai

로드메이트 코리아의 AI 파트입니다. 정차 중 한국어·영어 통역, 목적지 말 알아듣기, 주행 안내 규칙을 맡습니다.

외부 AI API는 쓰지 않습니다. PC에서 Qwen3-4B-Instruct-2507(Q4_K_M)을 llama.cpp로 띄우고, 그 앞의 FastAPI 서버가 백엔드 요청을 받습니다.

```
APK → 백엔드(8080) → AI 서버(8000) → llama-server(8081)
```

주행 안내(스쿨존, 우회전, 정차 잠금)는 모델 없이 규칙으로 동작합니다. `guidance.py`를 앱에 옮겨서 앱 안에서 돌립니다.

## 폴더 구성

| 위치 | 내용 |
|---|---|
| `app.py`, `engine.py`, `inference.py`, `sessions.py`, `schemas.py` | AI 서버 |
| `prompting.py`, `prompts/` | 프롬프트. 서버와 학습이 같이 씁니다 |
| `validation.py` | 번역 결과 검사(숫자, 시간, 통화, 부정, 연료, 좌우) |
| `guidance.py`, `packs/` | 주행 안내 규칙, 용어집, 안내 문구, 데모 장소 |
| `models/manifest.json` | 모델 파일, 버전, 실행 옵션 |
| `eval/` | 평가셋(dev 100건, test 60건)과 평가 스크립트 |
| `train/` | QLoRA 학습과 어댑터 변환 |
| `scripts/` | 설치·실행·종료 스크립트 |
| `docs/` | API 계약(`openapi.json`), 사용 안내(`guide.md`) |
| `tests/` | 모델 없이 돌아가는 테스트 |

## 다른 파트와 맞닿는 곳

백엔드는 `/internal/v1/translate`, `/internal/v1/parse-destination`, `/internal/v1/cancel`, `/health/ready`를 호출합니다. 요청과 응답 형식은 [`docs/openapi.json`](docs/openapi.json)에 있습니다. 필드 이름은 `session_id`처럼 밑줄로 씁니다. 문서에 없는 필드가 오면 422로 거절하고, 오류는 `error_code`로 구분합니다.

앱은 `guidance.py`와 `packs/fixtures/guidance_scenarios.json`을 기준으로 Kotlin으로 옮깁니다. `StopLock.update()`는 기어나 속도 값이 들어올 때마다 호출해야 합니다.

코드에서 `# 백엔드 연동`, `# 프론트 연동` 주석이 붙은 곳이 다른 파트와 연결되는 부분입니다.

## 실행 환경

NVIDIA GPU(VRAM 8GB 이상), RAM 16GB, Python 3.11~3.12를 기준으로 만들었습니다. Windows는 `scripts/*.ps1`, Linux(DLPC 등)는 `scripts/run.sh`로 설치하고 실행합니다. 자세한 방법은 [`docs/guide.md`](docs/guide.md)에 있습니다.

## 지금 상태

모델 없이 할 수 있는 확인은 끝났습니다. 테스트 259건이 통과하고, 모델 대신 가짜 응답(mock)으로 서버를 띄워 시작부터 종료까지 돌려 봤습니다.

실제 Qwen은 아직 돌려 보지 않았습니다. GPU PC에서 처음 확인할 것은 다음과 같습니다.

1. CUDA 빌드 설치와 VRAM 사용량
2. dev 평가셋으로 본 번역 품질과 속도
3. 4비트 학습이 VRAM 8GB에 들어가는지
4. 학습한 어댑터의 변환과 로딩
5. 에뮬레이터와 30분 동시 실행

평가셋, 학습 시드 데이터, 용어집, 안내 문구, 데모 장소 좌표는 아직 검수 전 초안입니다. 1차 제출은 학습 없이 기본 모델과 프롬프트, 용어집으로 갑니다. QLoRA 학습은 검수한 학습 자료가 1,000건 넘게 모이면 진행합니다.

## Git

모델 파일, 가상환경, 로그, 학습 결과, `.env`는 올리지 않습니다. 필요하면 `scripts/`의 스크립트로 다시 받습니다. `roadmate-ai/`가 바뀌면 GitHub Actions가 모델 없이 테스트를 돌립니다.
