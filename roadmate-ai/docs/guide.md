# roadmate-ai 사용 안내

설치·실행·API·평가·실험 이력 안내. 명령은 `app.py`와 `scripts/`가 있는 AI 저장소 루트에서 실행함. Windows 명령은 PowerShell 기준, Linux(DLPC)는 아래 장 참고. 기존 Pleos 연결 기준은 [`pleos_sdk.md`](pleos_sdk.md) 참고.

## Linux(DLPC)에서 실행

최종 기본값은 **8b-q4(Qwen3-8B Q4_K_M, 생각 모드 끔) + translate-v11**, LoRA 없음. 4B는 대안 프로필로만 보존함.

Ubuntu 설치·실행은 `scripts/run.sh` 사용함. llama.cpp 공식 Ubuntu CUDA 12.8 빌드를 내려받으므로 직접 빌드 불필요함. JupyterLab 터미널의 AI 저장소 루트에서 실행함.

```bash
git clone https://github.com/roadmate-korea/roadmate-AI.git
cd roadmate-AI
nvidia-smi                                   # GPU 이름과 VRAM 확인
bash scripts/run.sh setup                    # .venv, llama.cpp, 모델 다운로드, 환경 확인
bash scripts/run.sh start                    # 모델 서버 → AI 서버 → smoke 시험(SMOKE OK가 나오면 성공)
.venv/bin/python eval/evaluate.py --file eval/dev.jsonl --label q4-base
bash scripts/run.sh stop
```

위 clone 명령은 팀 저장소에 코드 반영 후 사용함. 현재 작업은 로컬 준비만 수행함. 최종 1회 평가는 6장, LoRA 종료 결과는 8장 참고. DLPC 명령은 이번 작업에서 실행하지 않음.

Windows·Linux 명령 대응은 아래 표 참고. 나머지 Python 명령은 `.venv\Scripts\python.exe`를 `.venv/bin/python`으로, `\`를 `/`로 바꿔 사용함.

| Windows | Linux |
|---|---|
| `setup_ai_env.ps1`, `setup_llama.ps1`, `download_model.py`, `check_env.py` | `bash scripts/run.sh setup` |
| `start_all.ps1 [-Mock]` | `bash scripts/run.sh start [--mock]` |
| `stop_all.ps1` | `bash scripts/run.sh stop` |
| `start_model.ps1` / `start_ai.ps1` (포그라운드) | `bash scripts/run.sh model` / `bash scripts/run.sh ai` |
| `start_model.ps1 -GpuLayers 30` | `GPU_LAYERS=30 bash scripts/run.sh start` |
| `setup_train_env.ps1`, `setup_convert_env.ps1` | `bash scripts/run.sh setup-train`, `bash scripts/run.sh setup-convert` |

DLPC에서 알아 둘 점:

- 기본 Python은 `python3.12`. 변경 시 `PYTHON=/경로/python bash scripts/run.sh setup`으로 지정함.
- 노트북 작업 정지 시 서버도 종료됨. 홈 폴더의 모델·가상환경은 보존되므로 이후 `start`로 재시작 가능함.
- 서버는 작업 내부의 127.0.0.1에서만 접속 가능함. 앱 시연은 실제 연결 경로가 있는 GPU PC 필요함.
- 모델·환경 설치 전 `df -h ~`로 여유 공간 확인 필요함. 추가 LoRA 학습은 종료함.

## 1. 실행 PC 요구사항

| 항목 | 기준 |
|---|---|
| OS | Windows 10/11 64bit |
| GPU | NVIDIA GPU, 현재 DLPC는 VRAM 24GB |
| 드라이버 | CUDA 12.4 빌드 사용 → NVIDIA 드라이버 551 이상 권장 |
| RAM | 16GB 이상 |
| Python | 3.11 (python.org 설치, `py -3.11` 사용 가능해야 함) |
| 디스크 | 기본 8B GGUF 약 5GB, 선생 모델·학습 환경까지는 8.1장의 용량 기준 |

모델 가중치 외에 KV 캐시·계산 버퍼가 VRAM을 사용한다. 시작 로그의 실제 GPU 적재 레이어 수와 실행 중 VRAM 사용량을 확인하고, 에뮬레이터를 함께 쓰면 그 메모리도 별도로 확보한다.

## 2. 처음 설치

PowerShell을 `roadmate-ai` 폴더에서 연다. 스크립트 실행이 막혀 있으면 한 번만:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

```powershell
.\scripts\setup_ai_env.ps1                         # .venv 생성 + requirements.lock.txt 설치
.\scripts\setup_llama.ps1                          # llama.cpp b12076 CUDA 12.4 → tools\llama, 소스 → tools\llama-src
.\.venv\Scripts\python.exe scripts\download_model.py   # 기본 8B GGUF 약 5GB + SHA256 확인
.\.venv\Scripts\python.exe scripts\check_env.py        # GPU·VRAM·RAM·파일 확인
```

- 모델 파일·revision·SHA256·런타임 옵션은 모두 `models/manifest.json` 한 곳에 고정되어 있다.
- 드라이버가 CUDA 12.4를 못 쓰면 `.\scripts\setup_llama.ps1 -Backend cuda-13.4`(최신 드라이버) 또는 `-Backend vulkan`.
- `check_env.py`는 에뮬레이터를 켜기 전·후에 한 번씩 실행해 VRAM·RAM 여유를 기록한다.

## 3. 서버 띄우기·종료

### 한 번에
```powershell
.\scripts\start_all.ps1      # llama-server(8081) → AI 서버(8000) → scripts\smoke.py 연기 시험
.\scripts\stop_all.ps1       # AI 서버 → 모델 서버 종료
```
로그: `logs\llama.*.log`, `logs\ai.*.log`

### 따로(디버깅할 때, 터미널 2개)
```powershell
.\scripts\start_model.ps1    # 포그라운드 llama-server. 시작 로그에서 "offloaded N/N layers to GPU"(전부 GPU) 확인
.\scripts\start_ai.ps1       # 포그라운드 FastAPI
```

### 모델 없이 API 계약 시험
```powershell
.\scripts\start_all.ps1 -Mock    # 응답 model_id가 "mock", 번역문은 [모의 번역]/[MOCK] 접두어
```

### 상태 확인
```powershell
.\.venv\Scripts\python.exe scripts\smoke.py
```
- `GET http://127.0.0.1:8081/health` : llama-server
- `GET http://127.0.0.1:8000/health/live` : AI 프로세스
- `GET http://127.0.0.1:8000/health/ready` : 모델 연결·alias·어댑터·자체 번역 1회 검사(결과 캐시)

첫 요청 전에 `/health/ready`가 200인지 확인하고 시연을 시작한다. 모델 로딩 시간과 준비 후 번역 시간은 따로 기록한다.

## 4. API 연동

전체 계약: [`docs/openapi.json`](openapi.json) (서버 실행 중이면 `http://127.0.0.1:8000/docs`).
계약을 바꾸면 `app.py`의 `API_VERSION`을 올리고 `.\.venv\Scripts\python.exe scripts\export_openapi.py`로 재생성한다(테스트가 불일치를 잡는다).

| 방향 | API | 용도 |
|---|---|---|
| 앱 → AI | `POST /translate` | `{text, from, to, situation}` → `{text}` |
| 백엔드 → AI | `POST /internal/v1/translate` | 최종 STT 문장 1개 번역 |
| 백엔드 → AI | `POST /internal/v1/parse-destination` | 목적지 검색어 추출 + 데모 장소 후보 |
| 백엔드 → AI | `POST /internal/v1/cancel` | `session_epoch` 이하 요청·진행 중 추론 취소 |
| 백엔드 → AI | `GET /health/ready` | 준비 확인 |

### 번역 요청/응답
```json
POST /internal/v1/translate
{"request_id":"req-001","session_id":"sess-001","session_epoch":3,"turn_id":1,
 "source_locale":"en-US","target_locale":"ko-KR","scenario":"gas_station",
 "text":"Please fill it up with gasoline.","engine_bundle":"roadmate-ko-en-q4-8b-v1"}
```
```json
{"request_id":"req-001","session_id":"sess-001","session_epoch":3,"turn_id":1,
 "status":"ok","translated_text":"휘발유로 가득 넣어 주세요.","clarification_question":null,
 "engine_bundle":"roadmate-ko-en-q4-8b-v1","model_id":"roadmate-qwen8b-q4",
 "prompt_version":"translate-v11","glossary_version":"rental-ko-en-es-v12","latency_ms":850,
 "spanish_question_restored":false}
```
- `source_locale`/`target_locale`: `ko-KR`, `en-US`, `es-US`, `es-MX` 중 한쪽은 반드시 `ko-KR`(한↔영, 한↔스). Pleos STT·TTS locale 값을 그대로 쓴다 / `scenario`: `gas_station`, `parking`, `rental_counter`, `accident`, `general`
- 스페인어는 `es-US`와 `es-MX`를 같은 말로 처리한다. 번역문은 중남미식 존댓말(usted), 원화는 `30,000 wones`, 시각은 `3 p. m.`.
- 목적지 후보(`candidates`)에는 `name_ko`, `name_en`, `name_es`가 있다(API 1.1.0). 화면에는 사용자 언어 이름을 쓴다.
- `status=clarification_required`이면 `clarification_question`(말한 사람의 언어)을 화면에 보여 주고 **번역 음성은 재생하지 않는다**.
- 차량 좌표·속도·기어·실명은 보내지 않는다. 텍스트는 400자 이하.

### 오류 계약 (본문 `{"error_code","request_id","message"}`, 앱은 `error_code`로 분기)

| HTTP | error_code | 처리 |
|---|---|---|
| 422 | `invalid_input`, `bundle_mismatch`, `text_too_long` | 자동 재시도 안 함 |
| 401 | `unauthorized` | 내부 토큰 확인 |
| 409 | `request_id_conflict` | 같은 request_id에 다른 내용 |
| 409 | `epoch_cancelled` | 취소된 epoch, 결과 폐기 |
| 429 | `busy` (+`Retry-After: 1`) | 모델이 다른 turn 처리 중. 대기열 없음 |
| 502 | `output_validation_failed`, `invalid_model_output`, `output_truncated` | **번역문으로 재생 금지**, 다시 말하기 안내 |
| 503 | `model_not_ready` | 모델 서버 확인 |
| 504 | `model_timeout` | 다시 시도 안내 |

### 중복·취소 규칙
- 같은 `request_id` + 같은 내용 → 모델을 다시 돌리지 않고 같은 결과(30초 TTL). 다른 내용 → 409.
- 모델 동시 실행 1건. 바쁘면 0.3초 기다린 뒤 429.
- APK가 P 해제·이동을 감지하면 백엔드가 `POST /internal/v1/cancel {"session_id","session_epoch"}` 호출 → 그 epoch **이하**만 무효화(단조 증가). 늦게 온 epoch 3 취소가 epoch 4를 취소하지 않는다.
- 취소·timeout 뒤에는 llama-server 슬롯이 실제로 빌 때까지 확인하고 다음 turn을 받는다.
- 서버의 모델 제한 기본값은 8초다. 현재 Android `/translate` 클라이언트의 전체 제한은 3초이므로 연결 시연에서는 `MODEL_TIMEOUT_S=2.5`로 시작하고 실제 왕복 시간을 측정한다. 시간 초과 후 자동으로 같은 요청을 다시 보내지 않는다.

### 내부 토큰(선택)
`.env`(예시 `.env.example`)에 `AI_INTERNAL_TOKEN=...`을 넣으면 `X-Internal-Token` 헤더가 필요하다. 서버·`smoke.py`·`evaluate.py`가 모두 `config.py`로 같은 `.env`를 읽으므로 새 터미널에서도 따로 설정하지 않는다(이미 설정된 환경 변수가 우선). AI 서버는 `127.0.0.1`에만 바인딩한다. FastAPI·모델 포트를 인터넷에 공개하지 않는다.

### 에뮬레이터 주소
APK의 `127.0.0.1`은 기기·에뮬레이터 자신이다. 같은 PC의 에뮬레이터에서는 `10.0.2.2`가 호스트의 loopback으로 연결된다. 앱의 AI 직접 연결 주소는 `http://10.0.2.2:8000`이며 다른 PC나 DLPC에는 이 별칭이 적용되지 않는다. AI의 바인딩은 `127.0.0.1`을 유지한다. [Android 네트워크 주소](https://developer.android.com/studio/run/emulator-networking-address)

Android `9945ab18`에는 STT와 HTTP 클라이언트가 구현돼 있다. 다만 `INTERNET` 권한·debug cleartext 설정이 없고 서버 URL 기본값이 비어 있다. 이 설정과 APK 빌드는 Android 담당 범위이며, AI 저장소에는 Android 코드 변경을 포함하지 않는다. [Android 보안 설정](https://developer.android.com/privacy-and-security/security-config)

현재 앱은 `/translate`를 직접 호출한다. 서버가 내부 요청 ID를 만들며 앱의 coroutine 취소로 HTTP 연결이 닫히면 서버도 작업을 취소한다. 재질문은 `text` 키가 없는 응답으로 반환되고 현재 앱은 문장 카드 안내로 대체한다. 기존 `/internal/v1/*` 계약은 별도로 유지한다.

## 5. 앱 연동(주행 안내 규칙·정차 잠금)

주행 안내는 LLM이 아니라 **고정 규칙과 문구(현재 draft, 출시 전 검수 필요)**로 APK 안에서 실행한다. `guidance.py`가 기준 구현이며, Android Kotlin 규칙과 같은 fixture로 동등성을 시험한다. 이전 코드 검토에서 확인한 정책 차이와 기어 재관측·원본 속도·TTS 완료/중단 조건은 [`pleos_sdk.md`](pleos_sdk.md) 2~3장에 기록했다. HTTP 계약 확인이 차량 신호 동등성 확인을 대신하지 않는다.

- 규칙·문구: `packs/rules.json` (스쿨존 현재 구간 과속, 스쿨존 접근, 제한값 미상 접근, 우회전 조건부 안내, 출발 브리핑)
- 시나리오 fixture: `packs/fixtures/guidance_scenarios.json` → `.\.venv\Scripts\python.exe guidance.py`로 전부 OK 확인
- 입력 스냅샷: 각 신호에 `value`(또는 `state`), `status`(`valid`/`unavailable`/`error`), `ts_ms`. 속도는 **m/s**, 제한속도는 km/h.
- 핵심 규칙
  - 현재 도로 제한속도(`onCurrentRoadSpeedLimit`)를 앞 스쿨존 제한값으로 쓰지 않는다. 앞 구역 값이 없으면 "표지판 확인" 문구.
  - 이미 제한속도 이하이면 말하지 않는다. 과속은 +3km/h가 2초 지속될 때만.
  - 우회전은 우측 깜빡이 + TBT 우회전 맥락(10~150m)일 때만. 비상등·차로 변경은 제외. 신호색을 단정하지 않는다.
  - 오래된 신호(속도 1.5초, 내비 5초)·`status≠valid`는 사용하지 않는다.
  - 하루 2회 제한은 교육성 설명(우회전·비보호 좌회전 안내)에만 적용한다. 실제 과속 경고(스쿨존 안 과속, 과속하며 접근)는 횟수 제한 없이 120초 간격·구역당 1회로만 거른다(규칙 팩의 `daily_explanation_limit: null`). **실제 재생 완료 콜백(`mark_played`)에서만 카운트**.
  - 발화 큐는 규칙별 최신 1건, 재생 직전 `revalidate`로 지난 구역 안내 폐기.
- 정차 잠금 `StopLock`: 기어 P(status valid) + 유효 속도 |v| ≤ 0.1m/s가 1초 유지일 때만 통역 버튼 활성. STT 시작·전송·번역 도착·TTS 직전에 다시 확인.
- 온보딩 프로필: 앱이 시작 화면에서 받은 값을 `GuidanceEngine(pack, 언어, profile=DriverProfile(국가, 경험))`로 넘긴다.
  - 언어: `en-US`, `fr-CA`, `es-US`(`es-MX`도 같은 문구), 개발용 `ko-KR`
  - 국가: `CN, JP, TW, US, HK, PH, VN, TH, SG, MY, OTHER`. `OTHER`면 통행 방향을 추측하지 않는다
  - 경험: `experienced`(한국 운전 경험 있음), `first_time_korea`(한국 처음), `first_time_lhd`(좌핸들 처음)
- 개인화(기획서 3.3 표):
  - 출발 브리핑 `briefing()`이 프로필로 문구를 고른다. 처음이면 환영·한국 규칙(우회전, 비보호 좌회전, 버스전용차로, 하이패스), 좌측통행 국가에서 왔으면 우측통행 적응, 좌핸들 처음이면 좌핸들 적응, 경험자는 짧은 환영과 스쿨존 안내만.
  - 주행 중에는 규칙 팩의 `kind`로 나눈다. `warning`(스쿨존 경고)은 모두에게, `explanation`(우회전 설명)은 경험자에게 생략.
  - fixture의 `persona_*_same_road`, `experienced_same_road`가 같은 길에서 달라지는 안내를 시험한다.
- 문구 팩 상태가 `draft`다. 특히 fr-CA·es-US 문구와 비보호 좌회전 안내는 원어민·법규 검수가 필요하다. 배포 전 `review.status=approved`, `reviewer`를 채운다(`allow_draft=False`면 draft 팩은 로딩 거부).

## 6. 평가

```powershell
.\scripts\start_all.ps1
.\.venv\Scripts\python.exe eval\evaluate.py --file eval\dev.jsonl --label q4-prompt-v1
```
결과: `eval\results\<label>-dev-<시각>\summary.md`, `results.jsonl`, `review.csv`(엑셀로 열림)

재시도 비교는 서버를 `LOG_REJECTED=1`로 시작하고 아래 개발 평가를 실행한다. 이 옵션은 번역 시도별 출력과 검사 결과를 서버 로그에 기록하므로 개발·평가 때만 켠다. 앱 응답에는 거절된 번역이 포함되지 않는다.

```bash
bash scripts/run.sh stop
LOG_REJECTED=1 bash scripts/run.sh start
for dataset in dev general general_es naturalness.dev; do
  .venv/bin/python eval/evaluate.py --file "eval/$dataset.jsonl" --label repair-v7 --attempt-log logs/ai.err.log
done
```

`results.jsonl`의 `translation_attempts`에 요청별 첫 출력·재시도 출력·검사 결과를 보존한다. `summary.md`는 첫 시도의 `wrong_language` 건수와 추적 범위를 언어 방향별로 표시한다. 재시도 회복은 첫 실패가 언어 오류만인 `wrong_language`, 그 외 검사 오류인 `other`, 둘 다 있는 `mixed`로 나눈다. `retry validator recovery`는 재시도가 검사를 통과한 비율이며 의미 정확도를 뜻하지 않는다. 특히 `other`에는 숫자·연료뿐 아니라 출력 구조 오류도 포함된다. 동일 출력 수와 로그 수집 범위도 함께 표시하며 로그가 누락·손상되면 회복 0%로 단정하지 않는다. 같은 모델·GPU·다듬기 설정과 개발 자료를 사용하고, 반복 횟수를 미리 정해 모든 실행을 보존한다. 고정 seed도 출력 일치를 보장한다고 가정하지 않으며 좋은 실행만 고르지 않는다. 잠근 최종 시험은 반복 비교에 사용하지 않는다.

- `eval/dev.jsonl` 100건(통역 60 · 목적지 25 · 모호/지시 삽입 15): 프롬프트·용어집·추론 설정을 고를 때 사용.
- `eval/general.jsonl` 180건(영→한 80 · 한→영 80 · 목적지 20): 정답 없이 주유·주차·렌터카·사고·도로·여행·숫자·부정·구어체·긴 문장·외래어 섞인 문장을 고르게 담은 범용 점검용. 요약에 상황(category)별 통과율과 502가 걸린 검사 종류가 나온다. 특정 문장에 맞춘 땜질이 아니라 걸린 검사 종류·상황을 보고 일반적인 규칙으로 고치고, 의미는 `review.csv`에서 사람이 본다.
- `eval/general_es.jsonl` 100건(스→한 45 · 한→스 45 · 스페인어 목적지 10, `es-US`·`es-MX` 섞음): 스페인어 범용 점검용. 쓰는 법은 general과 같다. 스페인어는 원어민 검토가 따로 필요하다.
- `eval/test.locked.v2.jsonl` 44건: **설정을 확정한 뒤 한 번만** 실행. 결과를 보고 설정을 바꾸면 그 파일은 회귀용으로 돌리고 새 시험셋을 만든다.
- 자동 통과는 보조 지표다. `review.csv`의 `human_meaning_ok`, `critical_error`를 원문·번역을 아는 사람이 채운다. 모델 채점으로 통과시키지 않는다.
- 필수 최소쌍: 휘발유/경유, 5만/15만 원, 가득/3만 원만, 다친 사람 있다/없다, 오세요/오지 마세요, 왼쪽/오른쪽.
- 1차 목표(측정 전 목표값): 잠근 시험 의미 보존 90% 이상, 준비 후 짧은 문장 번역 p95 3초, 발화 종료→TTS 시작 p95 5초, 에뮬레이터와 30분 동시 실행, P 해제 후 이전 결과 재생 0건.
- 잠근 v2의 원문·reference·기대값 44행은 Claude·Codex 독립 AI 검토 완료. `review_status=approved`, `reviewer="ai:claude+codex (human_reviewed=false)"`로 기록함. 사람 검수나 모델 출력 합격을 뜻하지 않음. 나머지 평가 자료의 검토 상태와 구분 필요함.

### 최종 설정과 잠근 시험 1회 실행

최종 설정: `MODEL_PROFILE=8b-q4`, Qwen3-8B Q4_K_M, `translate-v11`, LoRA 없음. `735a577`의 프롬프트·예시와 345개 요청 기준 해시를 유지함. 스페인어 질문 복원에는 `cuándo`·`quién`·`quiénes` 포함. 문장부호가 하나라도 있으면 원문 유지함. 이후 잠근 시험 결과로 프롬프트·검사기·설정을 다시 고르지 않음.

아래는 **DLPC 실행 준비 명령이며 이번 작업에서 실행하지 않음**. 진행 중인 작업이 끝난 뒤, 동일 코드·모델이 설치된 `roadmate-ai` 디렉터리에서 실행 필요함. 기존 `.env`의 어댑터를 빈 환경변수로 해제하고, DLPC 24GB GPU는 manifest 기본 99레이어 사용함. 서버는 한 번 시작하고 모든 평가가 끝날 때까지 코드·팩·설정 변경 금지.

```bash
(
  set -euo pipefail
  export MODEL_PROFILE=8b-q4 LORA_ADAPTER= LOG_REJECTED=1 MODEL_TIMEOUT_S=8
  export MODEL_ID=roadmate-qwen8b-q4 MODEL_BASE_URL=http://127.0.0.1:8081/v1
  export AI_URL=http://127.0.0.1:8000 AI_PORT=8000
  unset GPU_LAYERS
  FINAL_DIR=run/final-v11-01
  mkdir -p run
  mkdir "$FINAL_DIR"
  bash scripts/run.sh stop
  bash scripts/run.sh start
  .venv/bin/python eval/stt_format.py --out-dir "$FINAL_DIR/stt"
  .venv/bin/python eval/evaluate.py --file eval/test.locked.v2.jsonl --final \
    --label final-v11 --attempt-log logs/ai.err.log | tee "$FINAL_DIR/locked.log"
  pairs=()
  for dataset in general general_es naturalness.dev; do
    .venv/bin/python eval/evaluate.py --file "eval/$dataset.jsonl" \
      --label final-v11-original --attempt-log logs/ai.err.log | tee "$FINAL_DIR/$dataset.original.log"
    .venv/bin/python eval/evaluate.py --file "$FINAL_DIR/stt/$dataset.jsonl" \
      --label final-v11-stt --attempt-log logs/ai.err.log | tee "$FINAL_DIR/$dataset.stt.log"
    original=$(sed -n 's/^saved: //p' "$FINAL_DIR/$dataset.original.log")
    stt=$(sed -n 's/^saved: //p' "$FINAL_DIR/$dataset.stt.log")
    pairs+=(--pair "$original" "$stt")
  done
  .venv/bin/python eval/compare_stt.py "${pairs[@]}" --out-dir "$FINAL_DIR/stt-report"
)
```

`run.sh start`에서 준비 확인과 smoke 수행함. `--final`은 값을 받지 않는 플래그임. `mkdir`가 준비 블록 재실행을 막고, 평가기의 `run/final-evaluations/<입력 지문>.json`이 같은 잠근 입력의 두 번째 실행을 막음. 중간 실패도 기록 보존 필요함. 폴더·ledger 삭제나 이름 변경으로 잠근 시험을 재시도하지 않음.

각 `saved:` 결과 폴더의 `summary.md`에서 FAIL·502·첫 시도 오언어·재시도·번역투 표지·p50/p95 확인 가능함. STT 비교는 `run/final-v11-01/stt-report/summary.md`에 남음. `run.json`의 모델·프롬프트·코드 해시도 함께 보존함. 최종 수치는 [제출 요약](submission_summary.md)에 실행 후 기입함.

AI 원문 검토 승인과 출력 의미 검수는 별개임. 기존 `release_gate.py`는 dev 포함 5종 평가와 사람의 출력 검토를 요구함. 위 실행만으로 사람 검수·릴리스 게이트 통과를 표시하지 않음. 팀 저장소 후속 커밋은 잠근 시험 완료 뒤 준비하며, 현재 커밋·푸시 없음.

### STT 형식 입력 비교

소문자·문장부호 없는 입력에 대한 견고성을 비교한다. `general`, `general_es`, `naturalness.dev` 세 파일의 `text`만 바꾼 사본을 만들고 원본·정답·기대값·ID·행 순서는 유지한다. locked 파일은 읽거나 바꾸지 않는다. 아래 도구는 프롬프트·검사기·서버를 수정하거나 시작하지 않는다.

`translate-v11` 프롬프트·예시는 git 기록에서 복원했으며 기존 345개 요청 해시 불변 테스트로 확인한다. 사용자 제공 DLPC 재평가에서 v12의 상황상 질문 5건 개선이 없고 깨끗한 입력의 502가 2건 늘어 복원했으며, 이 PC에서 실측한 결과는 아니다. 스페인어(`es-US`·`es-MX`)에서 지정한 질문 시작 구문은 모델 입력만 `¿…?`로 감싼다. 시작어에는 `cuándo`, `quién`, `quiénes`도 포함한다. 물음표·마침표·느낌표·말줄임표·쉼표 등 문장부호가 하나라도 있으면 원문을 유지한다. 숫자 내부 문장부호도 유지하고 복원 대상에서 제외한다. 안전 검사에는 원문을 그대로 사용한다. 내부 응답의 `spanish_question_restored`와 `translation_input` 로그에 적용 여부를 남기고, `LOG_REJECTED=1`이면 시도별 로그에도 남긴다. 앱 `/translate` 응답 형식은 그대로다.

이 시작 구문 규칙은 문장부호 없는 `hay`·`ya` 평서도 질문으로 바꾸는 오탐이 있다. 합성 회귀 테스트에 이 한계를 고정했으며, 기존 문장부호 입력을 포함한 실제 의미·지연 변화는 DLPC 비교가 필요하다. Hi-Pass 조회는 `Hi-Pass`·`hi-pass`·`hipass`를 잡지만 `hi pass`처럼 띄어 쓴 표기는 현재 잡지 못한다. 이번 변경에서는 용어집·검사기·숫자 규칙을 수정하지 않았다.

    .venv/bin/python eval/stt_format.py --out-dir run/stt-input-03

`stt-format-v3`는 소문자화·문장부호 제거·공백 정리를 하되 apostrophe(`don't`)와 숫자 내부 문장부호를 보존한다. 영어의 오전·오후가 명시된 유효한 시각은 `HMM a m/p m`, 쉼표가 있는 정수 금액은 `N thousand M`으로 바꾼다(나머지가 0이면 생략). 소수점·음수·퍼센트와 한국어·스페인어의 숫자 표기·단위는 유지한다. 한국어 물음표는 제거한다.

v3는 영어 수량의 글자↔숫자 경계도 검사기와 같은 함수로 띄어 읽는다. hundred/thousand/million이 확인되는 구간과 is/just/about/pay 같은 수량 도입어만 대상으로 하며, `B2`·`A320`·임의 식별자 접두사와 scale 단서 없는 붙임은 유지한다. 이는 숫자 분석용 처리이며 앱이 받은 원문이나 번역 프롬프트를 다시 쓰지 않는다. 붙임 오류를 평가 원문에 새로 주입하는 실험도 아니다.

근거는 사용자가 관찰한 Pleos 에뮬레이터 v2.1.0, ON_DEVICE, TTS 녹음 주입 결과다. `stt_manifest.json`에 이 관찰의 원문·인식 결과, 붙임 관찰 사례, 독립 재현 여부(false), 변환 버전·규칙·원본/사본 SHA256·변경 건수를 남긴다. 이는 **형식 모사**이며 STT 오류율을 재현한 실험은 아니다. `won`이 `one`·`1`로 들리거나 `red`가 끼는 현상은 기록만 하고 사본에 주입하지 않는다. 기존 v1·v2 폴더는 덮어쓰지 말고 새 폴더를 사용한다.

진행 중인 DLPC 작업이 끝난 뒤, 같은 모델·프롬프트·서버 설정에서 원본과 사본을 차례로 평가한다. `MODEL_PROFILE`은 실행 중인 서버와 맞춘다.

```bash
for dataset in general general_es naturalness.dev; do
  .venv/bin/python eval/evaluate.py --file "eval/$dataset.jsonl" --label original-stt03
  .venv/bin/python eval/evaluate.py --file "run/stt-input-03/$dataset.jsonl" --label stt-stt03
done
```

평가기의 `saved:`에 나온 결과 폴더를 원본·STT 순서로 지정한다. `--pair` 한 쌍만 지정해도 되며 세 평가셋은 다음처럼 묶는다. 아래 꺾쇠 자리는 실제 결과 폴더로 바꾼다.

```bash
.venv/bin/python eval/compare_stt.py \
  --pair <general_원본_결과> <general_STT_결과> \
  --pair <general_es_원본_결과> <general_es_STT_결과> \
  --pair <naturalness_원본_결과> <naturalness_STT_결과> \
  --out-dir run/stt-report-03
```

`summary.md`·`summary.json`은 FAIL·502의 원본/STT 건수와 증감, 새 실패·회복 ID, 질문·부탁 표현 표지의 변화를 집계한다. **숫자가 있는 원문**은 데이터셋별·전체에서 같은 지표를 별도 집계한다. 기준은 원문에 Unicode 십진 숫자가 하나 이상 있는 경우이며 `three` 같은 숫자 단어는 포함하지 않는다. `changes.csv`의 `numeric_source`로 이 행들만 볼 수 있다. CSV에는 원문·STT 입력·두 출력·기대 번역과 검토 칸을 나란히 남긴다. 질문·부탁 표지는 **검토 후보이며 의미 변경 판정이 아니다**. 특히 물음표만으로 질문과 부탁을 구분하지 않으며, 확인 질문·502는 번역 의도 판정에서 제외한다. 표지가 같아도 뜻이 달라질 수 있으므로 바뀐 출력 전체를 확인한다. 서로 다른 모델·팩·런타임 설정, 변조된 결과, 누락된 ID나 `text` 이외 평가 기준 변경은 비교를 거절한다. mock은 `--allow-mock`으로 도구 점검에만 허용한다.

검사기는 apostrophe 없는 영어 부정 축약형도 일반 부정·연료 부정으로 읽는다. `HMM a m/p m`은 유효한 시·분 범위와 오전·오후가 있을 때만 시각으로 읽고, `N thousand M won`은 하나의 금액으로 비교한다. 잘못된 숫자·통화·오전/오후·연료 방향은 계속 거절한다. 프롬프트·예시·힌트 규칙은 이번 변경에서 유지했으며, 실제 비교 결과를 본 뒤 변경 여부를 정한다.

축약형은 단어 전체가 일치할 때만 인식한다. 다만 `cant`(기울기)·`wont`(습관)처럼 실제 일반어와 겹치는 드문 용법은 부정 오탐 가능성이 남는다. 복합 금액은 서로 다른 통화나 뒤에 이어지는 숫자 단어·배수·분수를 한 금액으로 합치지 않는다.

`one`·`1`을 `won`으로 자동 보정하지 않는다. 둘 다 실제 수량·번호일 수 있고 `one red car`의 `red`도 실제 색상이다. `just30 thousand1`은 `just 30 thousand 1`로 읽어 숫자 30,001을 보존하므로 30,000원으로 옮긴 출력은 여전히 거절한다. `is87 thousand500red one`은 `is 87 thousand 500 red one`으로 띄어 87,500을 읽되 통화는 미확정이며 red·one도 원문에서 지우지 않는다. 이 처리가 실제 발화의 금액 복구나 의미 정확성을 보장하지는 않는다. 통화 단서가 사라진 입력은 기존 숫자 검사만으로 원화를 보장할 수 없으므로 이 사본 비교와 별도로 실제 STT 원문·음성의 확인이 필요하다.

### 서버가 자동으로 하는 출력 검증(`validation.py`)
JSON 계약 → 목표 언어 문자 → 설명문 섞임 → 길이 → 숫자·금액·시간·전화번호 보존(3만 원=30,000 won, 오후 3시=3 p.m. / 원↔달러, 오전↔오후가 바뀌면 실패) → 부정 보존 → 연료 용어(gas는 휘발유로 확정 금지)와 연료별 긍정·부정(“휘발유, 경유 말고”가 뒤집히면 실패) → 좌/우. 실패하면 이유를 붙여 **한 번만** temperature 0으로 다시 생성하고, 그래도 실패하면 502로 재생을 막는다.

재시도는 오답 초안을 다시 보내지 않고 원문·검사 종류·구체적인 용어 대응을 보낸다. 외국어의 원화 금액은 한국어 `원` 표기를 힌트로 제공한다. `at 5`, `before eight`처럼 분명한 영어 시각은 오전·오후 미정으로 비교하지만, 수량·기간·돈·날짜는 이 규칙에서 제외한다. `at 5 on Monday` 같은 추가 문맥과 `1.234,50` 혼합 금액 표기는 아직 보수적으로 기존 파서를 따른다.

## 7. 튜닝 이력

설정 선택 종료. 아래 항목은 개발 이력이며 잠근 시험 이후 튜닝 절차로 사용하지 않음.

실패 원인을 나눈다: STT 오인식 / 프롬프트 / 용어 / 모델의 의미·언어 품질 / 양자화 / 입력 모호성. 반복되는 의미 오역은 프롬프트를 계속 늘리지 않고 [4B·8B 비교](model_comparison.md)로 확인한다.

| 순서 | 무엇을 | 어디서 |
|---|---|---|
| 1 | 번역 지시문·few-shot | `prompts/translate.txt`, `prompts/translate.examples.json` (바꾸면 첫 줄 `prompt_version` 올림) |
| 2 | 용어집 | `packs/glossary.json` (`version` 올림). 발화에 나온 용어만 프롬프트에 들어감 |
| 3 | 샘플링 | `prompting.py`의 `TRANSLATE_SAMPLING` 등(기본 temperature 0.2, top_p 0.8, top_k 20) |
| 4 | 추론 자원 | `models/manifest.json`의 `runtime`(ctx 4096, parallel 1, n_gpu_layers 99, batch 256, ubatch 128) |
| 5 | 검증 임계값 | `validation.py`(숫자·부정·방향 규칙), `engine.py`의 POI 점수 0.62 |
| 6 | 영·스→한 다듬기 | 폐기·코드 삭제. 비교에서 의미 변경과 general p95 337→1135ms가 보고됐다. |
| 7 | Q5 비교 | Q4에서 의미 오류가 남고 VRAM 여유가 있을 때만(아래) |

변경할 때마다 `eval\evaluate.py --file eval\dev.jsonl --label <이름>`으로 같은 dev 자료를 다시 돌려 비교한다.

`eval/compare.py`는 같은 문장을 나란히 놓는다. 달라진 문장 수만 출력하지만 CSV에는 겹치는 번역 전체가 들어간다. 원문·오류 상태를 포함해 모든 행을 보고, 사람이 `better` 칸에 a/b/same을 적는다. `review.csv`의 의미·자연스러움·치명 오류 검수도 별도로 완료해야 한다.
```bash
.venv/bin/python eval/compare.py eval/results/<4B-실행폴더> eval/results/<8B-실행폴더>
```

스페인어도 같은 방식으로 돌린다.
```bash
.venv/bin/python eval/evaluate.py --file eval/general_es.jsonl --label es-base
```

느리거나 VRAM이 부족할 때: GPU 사용 확인(시작 로그 offload) → ctx·parallel·출력 상한 확인 → 불필요한 프로세스 종료 → `.\scripts\start_model.ps1 -GpuLayers 30`처럼 레이어 수를 줄이고 지연 재측정 → 그래도 부족하면 모델 PC와 에뮬레이터 PC 분리.

### 7.3 선택된 모델과 평가

DLPC 34bfd43 실행 및 Claude 블라인드 검토(사용자 제공, **AI 검토·참고**)에 따라 기본 모델을 8b-q4로 선택했다. 좋음/어색함/나쁨은 4B 64/30/6%, 8B 71/27/2%였고, 뜻 오류는 각각 16/268과 5/269였다. 8B 목적지 20/20, general p95 약 511ms가 보고됐다. 원본 결과를 이 PC에서 재실행한 수치는 아니다.

두 단계·추가 문체·추가 현장 용어·다듬기 실험은 폐기했다. 실행 옵션과 관련 파일을 제거했으며, 모두 끈 기존 번역 프롬프트는 삭제 전 345개 요청 해시와 비교해 유지한다. 모델만 비교할 때는 같은 커밋과 자료로 각각 실행하고 [블라인드 리뷰 도구](naturalness_rubric.md)를 사용한다.

```bash
MODEL_PROFILE=8b-q4 bash scripts/run.sh setup
MODEL_PROFILE=8b-q4 bash scripts/run.sh start
MODEL_PROFILE=8b-q4 .venv/bin/python eval/evaluate.py --file eval/general.jsonl --label 8b-base
```

다른 서버가 실행 중이면 먼저 그 작업을 마친다. 4B Q4/Q8 대안은 MODEL_PROFILE만 바꾸며, 원본 결과와 로그를 보존한다. 잠근 시험은 튜닝에 사용하지 않는다.

## 8. LoRA 실험 종료

최종 선택은 **Qwen3-8B Q4_K_M + translate-v11 + 스페인어 질문 복원 규칙**, 어댑터 없음. LoRA 추가 생성·학습은 종료함. 아래 수치는 사용자 제공 DLPC 결과와 **AI 검토(참고)** 요약이며 이번 로컬 작업에서 다시 측정하지 않음.

| 선생 모델 | 확인 결과 | 종료 사유 |
|---|---|---|
| 14B Q4 | 같은 행 뜻 오류 7→7 | 의미 정확도 개선 없음 |
| 32B Q4 | 자연스러움 개선, 같은 행 뜻 오류 7→6. 학습 표본 나쁨 6.7%, 스페인어 9% | 학습 정답 품질 기준 미달 |
| 8B 자기 선생 | 학습 표본 나쁨 8.0% | 자기 선생 표본 기준 4% 초과 |

서로 다른 검토 표본의 비율을 하나의 성능 수치로 합치지 않음. 32B 뜻 오류 1건 감소만으로 검증된 개선을 주장하지 않음. 채택 어댑터 없음. 모델 비교와 한계는 [모델 비교](model_comparison.md), 제출 상태는 [제출 요약](submission_summary.md)에 기록함.

`scripts/lora.sh`, `scripts/lora_pipeline.py`, `train/`의 생성·필터·학습·변환 도구와 관련 테스트는 보존함. 14B·32B manifest도 실험 이력용으로 유지함. 이 장은 다음 학습을 지시하는 실행 절차가 아님. 향후 재개 시 새로운 승인·자료 품질 기준 필요함.

기존 실험의 원본 결과·표본 검토·해시는 당시 증거로 보존함. LoRA 종료로 현재 코드의 주석 정리를 미루거나 소스 해시를 옛 값으로 유지할 필요 없음. 과거 증거의 해시를 덮어쓰거나 새 코드에서 재현한 결과로 표시하지 않음.

보존 도구의 로컬 테스트 중 선생 토큰 검사는 `SOURCE_TOKENIZER_DIR`에 고정 Qwen3-14B tokenizer를 지정해야 함. 미지정 시 해당 4건 건너뜀. 실제 모델 실행·학습 확인과 구분함.

## 9. 문제 해결

| 증상 | 확인 |
|---|---|
| `/health/ready` 503, `model_server: false` | `logs\llama.err.log`, 8081 포트 충돌, 모델 파일 경로 |
| `model_alias: false` | manifest `model.alias`와 llama-server `--alias` 일치 |
| 시작 로그에 GPU offload 없음 | CUDA 빌드 설치 여부, 드라이버, `nvidia-smi` |
| CUDA out of memory | 에뮬레이터 그래픽 설정, `-GpuLayers` 축소, ctx 유지(4096) |
| 429 `busy`가 잦음 | 앱에서 같은 세션 중복 호출, 버튼 연타 방지 |
| 취소·504 뒤 계속 429 | llama-server가 아직 계산 중이면 슬롯을 열지 않는다(`logs\ai.err.log`에 `still processing`). 계속되면 `stop_all.ps1` → `start_all.ps1` |
| `already running` / `port ... in use` | 이전 서버가 남아 있음. `stop_all.ps1`, 그래도 남으면 `netstat -ano \| findstr :8000`(또는 8081)의 PID 종료 |
| 502 `output_validation_failed` | `message`에 원인(예: `number_missing:30000`, `negation_mismatch`). 실제 시도 출력을 보려면 `LOG_REJECTED=1`로 서버를 띄우고 `logs/ai.err.log`에서 `translation_attempt`를 찾는다(개발·평가 전용). `evaluate.py --attempt-log logs/ai.err.log`로 재시도 집계. dev 평가로 패턴 확인 → 프롬프트·용어집·검사기 수정 |
| 504 | 첫 요청 지연(모델 준비 전) 또는 GPU 미사용 |
| PowerShell에서 한글 깨짐 | 스크립트는 UTF-8 BOM으로 저장되어 있다. 편집기에서 BOM 유지 |
| `Expand-Archive`·다운로드 실패 | 사내망 프록시, `%TEMP%\roadmate-llama-<tag>` 삭제 후 재시도 |

## 10. GitHub 구성

- `app.py`, `scripts/`, `models/` 등이 팀 AI 저장소 루트에 놓이는 독립 구성이다.
- 커밋 대상: 코드, `prompts/`, `packs/`, `eval/*.jsonl`, `models/manifest*.json`, `train/`의 설정·원문 풀, `docs/`, `requirements*.txt`, lock 파일.
- 커밋 제외(`.gitignore`): `.venv*`, `models/*.gguf`, `models/adapters/`, `tools/llama*/`, `logs/`, `run/`, `eval/results/`, `train/out|runs|base-config/`, `.env`. 큰 파일은 스크립트로 재설치한다.
- CI: 독립 저장소 루트 `.github/workflows/ci.yml`이 Python 3.11에서 `scripts/verify_mvp.py --local`을 실행한다(모델 불필요).
- `.gitattributes`: 텍스트는 LF, PowerShell(`*.ps1`)은 CRLF로 고정한다. 바이너리 파일은 줄바꿈 변환에서 제외한다.
- 코드에는 `# 모델`, `# 프롬프트 호출`처럼 간결한 섹션만 표시한다. 설치·실행·제약 설명은 문서에서 관리한다.
- PR 제목·본문은 [`pull_request.md`](pull_request.md)의 기존 초안 참고. 팀 저장소 `roadmate-korea/roadmate-AI`용 후속 커밋은 잠근 시험 실행 뒤 준비함. 현재 커밋·푸시하지 않음.
- LoRA 종료로 주석 정리의 옛 해시 보존 제약 해제됨. 과거 실험 증거는 원래 해시 그대로 보존함.

## 11. 검증 상태

2026-11-10 로컬 통합 검사 **1,961 passed**, 주행 시나리오 통과함. 보고서는 `run/verify-243be347fb/report.json`. `735a577`의 프롬프트·예시·기준 파일 동일성과 345개 요청 해시 고정 테스트도 확인함. 최종 DLPC 명령은 `bash -n` 구문검사만 통과했으며 실제 실행하지 않음.

`python scripts/verify_mvp.py --local`은 API·번역 검사·학습 경계·STT 형식·질문 복원·런타임 해시·GPU 적재 확인·Windows 시작·주행 시나리오 검사임. 모델·GPU 응답은 합성 자료로 확인하고 Windows 시작 테스트는 실제 스크립트에 합성 명령을 연결함. 이번에는 `SOURCE_TOKENIZER_DIR`을 지정해 선생 토큰 검사도 수행함. 실제 모델 번역·SDK 기기 시험은 별도임.

실제 모델 품질의 완료 기준과 실행 순서는 [mvp_acceptance.md](mvp_acceptance.md)에 있다. 평가기는 mock을 기본 거절하고, 실제 서버의 코드·팩 해시와 실행 설정을 기록한다. 자연스러움과 의미 보존은 review.csv에 별도로 검토해야 한다. 기존 버전의 모델 평가 수치를 이번 버전의 성적으로 쓰지 않는다.

세 프로필 토큰 예산은 고정 tokenizer·GGUF template으로 프로필별 759건 통과함. 출력 예약 포함 최대 4B Q4·Q8 3,209/4,096, 8B Q4 3,213/4,096토큰. 개발 문장·400자 입력·교정 재시도 검사이며 잠근 시험 미사용. 모든 입력의 상한 증명은 아님.

토큰 예산 재검사에는 모델 가중치 대신 해당 base_revision의 tokenizer 파일만 필요하다. `tokenizers`, `Jinja2`가 설치된 Python에서 다음 명령을 쓴다.

```bash
MODEL_PROFILE=8b-q4 python scripts/check_prompt_budget.py \
  --tokenizer-dir <고정_revision의_tokenizer_디렉터리> \
  --chat-template <실제_GGUF_chat_template.jinja>
```

실행 PC에서 남은 확인:

1. 확정 설정의 한↔영·한↔스 의미 보존·자연스러움·지연 확인 필요함. 6장 명령으로 잠근 시험 `--final` 1회와 개발·STT 평가 실행함.
2. LoRA 종료. 어댑터 없이 최종 설정으로 평가하고 잠근 시험 결과는 제출 요약에 기입함.
3. Pleos STT/TTS·기어·속도·방향지시등·NaviHelper·FusedLocation 입력을 실제 SDK에서 연결하고 fixture와 동등성 확인.
4. 에뮬레이터와 모델 서버를 30분 함께 실행해 메모리·VRAM·취소·언어 전환을 확인.
