# roadmate-ai 사용 안내

설치, 서버 실행, 백엔드·앱 연동 규칙, 평가, 튜닝, 학습, 문제 해결을 모아 둔 문서입니다. Pleos SDK와 맞닿는 곳은 [`pleos_sdk.md`](pleos_sdk.md)에 따로 정리했습니다. 아래 1~11장은 Windows 기준이고 명령은 `roadmate-ai` 폴더에서 PowerShell로 실행합니다. Linux(DLPC 등)는 바로 아래 장을 먼저 봅니다.

## Linux(DLPC)에서 실행

기본 프로필은 **8b-q4(Qwen3-8B Q4_K_M, 생각 모드 끔)**입니다. 4B 대안은 `MODEL_PROFILE=4b-q4`로 선택합니다.

Ubuntu에서는 PowerShell 스크립트 대신 `scripts/run.sh` 하나로 설치하고 실행합니다. llama.cpp는 공식 Ubuntu CUDA 12.8 빌드를 받아 쓰므로 직접 빌드하지 않습니다. 명령은 JupyterLab 터미널의 `roadmate-ai` 폴더에서 실행합니다.

```bash
git clone -b codex/roadmate-ai-mvp https://github.com/hpySJ/RoadMate.git
cd RoadMate/roadmate-ai
nvidia-smi                                   # GPU 이름과 VRAM 확인
bash scripts/run.sh setup                    # .venv, llama.cpp, 모델 다운로드, 환경 확인
bash scripts/run.sh start                    # 모델 서버 → AI 서버 → smoke 시험(SMOKE OK가 나오면 성공)
.venv/bin/python eval/evaluate.py --file eval/dev.jsonl --label q4-base
bash scripts/run.sh stop
```

선생 비교·증류는 아래 8장의 scripts/lora.sh를 사용합니다. 아래 DLPC 명령은 로컬 검증 중 실행하지 않았습니다.

Windows 명령과의 대응은 이렇습니다. 나머지 Python 명령은 `.venv\Scripts\python.exe`를 `.venv/bin/python`으로, `\`를 `/`로 바꿔 그대로 씁니다.

| Windows | Linux |
|---|---|
| `setup_ai_env.ps1`, `setup_llama.ps1`, `download_model.py`, `check_env.py` | `bash scripts/run.sh setup` |
| `start_all.ps1 [-Mock]` | `bash scripts/run.sh start [--mock]` |
| `stop_all.ps1` | `bash scripts/run.sh stop` |
| `start_model.ps1` / `start_ai.ps1` (포그라운드) | `bash scripts/run.sh model` / `bash scripts/run.sh ai` |
| `start_model.ps1 -GpuLayers 30` | `GPU_LAYERS=30 bash scripts/run.sh start` |
| `setup_train_env.ps1`, `setup_convert_env.ps1` | `bash scripts/run.sh setup-train`, `bash scripts/run.sh setup-convert` |

DLPC에서 알아 둘 점:

- 기본으로 `python3.12`를 씁니다. 다른 Python을 쓰려면 `PYTHON=/경로/python bash scripts/run.sh setup`처럼 지정합니다.
- 노트북 작업을 정지하면 띄워 둔 서버도 같이 꺼집니다. 홈 폴더의 모델과 가상환경은 남으니 다시 `start`만 하면 됩니다.
- 서버는 그 작업 안(127.0.0.1)에서만 접속됩니다. 다른 PC의 백엔드는 이 서버를 호출할 수 없으니, DLPC는 평가·튜닝·학습에 쓰고 백엔드 연동 시연은 백엔드와 같은 PC에서 합니다.
- 학습까지 하면 디스크를 20GB 넘게 씁니다. `df -h ~`로 남은 공간을 먼저 확인합니다.

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
.\.venv\Scripts\python.exe scripts\download_model.py   # GGUF 2.5GB 다운로드 + SHA256 확인
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

### 모델 없이 계약만 시험(백엔드 담당 개발용)
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

## 4. 백엔드 연동

전체 계약: [`docs/openapi.json`](openapi.json) (서버 실행 중이면 `http://127.0.0.1:8000/docs`).
계약을 바꾸면 `app.py`의 `API_VERSION`을 올리고 `.\.venv\Scripts\python.exe scripts\export_openapi.py`로 재생성한다(테스트가 불일치를 잡는다).

| 방향 | API | 용도 |
|---|---|---|
| 백엔드 → AI | `POST /internal/v1/translate` | 최종 STT 문장 1개 번역 |
| 백엔드 → AI | `POST /internal/v1/parse-destination` | 목적지 검색어 추출 + 데모 장소 후보 |
| 백엔드 → AI | `POST /internal/v1/cancel` | `session_epoch` 이하 요청·진행 중 추론 취소 |
| 백엔드 → AI | `GET /health/ready` | 준비 확인 |

### 번역 요청/응답
```json
POST /internal/v1/translate
{"request_id":"req-001","session_id":"sess-001","session_epoch":3,"turn_id":1,
 "source_locale":"en-US","target_locale":"ko-KR","scenario":"gas_station",
 "text":"Please fill it up with gasoline.","engine_bundle":"roadmate-ko-en-q4-v1"}
```
```json
{"request_id":"req-001","session_id":"sess-001","session_epoch":3,"turn_id":1,
 "status":"ok","translated_text":"휘발유로 가득 넣어 주세요.","clarification_question":null,
 "engine_bundle":"roadmate-ko-en-q4-8b-v1","model_id":"roadmate-qwen8b-q4",
 "prompt_version":"translate-v9","glossary_version":"rental-ko-en-es-v11","latency_ms":850}
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
- timeout 권장: AI 모델 8초 < 백엔드 10초 < 앱 12초. timeout됐다고 자동으로 두 번째 호출을 보내지 않는다.

### 내부 토큰(선택)
`.env`(예시 `.env.example`)에 `AI_INTERNAL_TOKEN=...`을 넣으면 `X-Internal-Token` 헤더가 필요하다. 서버·`smoke.py`·`evaluate.py`가 모두 `config.py`로 같은 `.env`를 읽으므로 새 터미널에서도 따로 설정하지 않는다(이미 설정된 환경 변수가 우선). AI 서버는 `127.0.0.1`에만 바인딩한다. FastAPI·모델 포트를 인터넷에 공개하지 않는다.

### 에뮬레이터 주소
APK의 `127.0.0.1`은 에뮬레이터 자신이다. 같은 PC에서는 `10.0.2.2`가 호스트의 loopback으로 연결되므로 AI 서버를 `127.0.0.1`에 바인딩한 것 자체가 접속 불가 이유는 아니다. 백엔드 구성이면 APK → `http://10.0.2.2:8080`(백엔드), 백엔드 → `http://127.0.0.1:8000`(AI)다. 로컬 시연에서 앱의 AI 직접 연결을 선택하면 주소는 `http://10.0.2.2:8000`이다. 다른 PC나 DLPC에는 이 별칭이 적용되지 않는다. [Android 네트워크 주소](https://developer.android.com/studio/run/emulator-networking-address)

Android `895bb08`에는 `INTERNET` 권한·HTTP 클라이언트·cleartext 예외가 없으므로 주소만 바꿔서는 통역되지 않는다. HTTP 예외는 debug 소스 세트의 `network_security_config`에서 `10.0.2.2` domain-config에만 두고 release에서는 허용하지 않는다. XML의 `debug-overrides`는 인증서 설정용이므로 cleartext 예외와 혼동하지 않는다. [Android 보안 설정](https://developer.android.com/privacy-and-security/security-config)

팀 백엔드의 존재·배치와 시연용 GPU PC를 먼저 확정한다. AI 직접 연결을 선택하면 앱이 요청 ID·epoch·취소·오류 처리를 맡고, 내부 토큰을 APK의 배포용 비밀값으로 두지 않는다. 정차 신호의 재관측·유효성까지 포함한 작업 순서는 [`pleos_sdk.md`](pleos_sdk.md) 6장을 따른다.

## 5. 앱 연동(주행 안내 규칙·정차 잠금)

주행 안내는 LLM이 아니라 **고정 규칙과 문구(현재 draft, 출시 전 검수 필요)**로 APK 안에서 실행한다. `guidance.py`가 기준 구현이며, 팀 Android `895bb08`의 기존 Kotlin 규칙과 같은 fixture로 동등성을 시험한다. 현재 차이와 기어 재관측·원본 속도·TTS 완료/중단 분리 조건은 [`pleos_sdk.md`](pleos_sdk.md) 2~3장에 기록했다.

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
- 평가 사례는 모두 `review_status: draft`다. 사람이 검수해 `approved`로 바꾼다. `tests/test_eval_data.py`가 정답이 검증기를 통과하는지, dev/test/few-shot 사이 누수가 없는지 확인한다.

### STT 형식 입력 비교

소문자·문장부호 없는 입력에 대한 견고성을 비교한다. `general`, `general_es`, `naturalness.dev` 세 파일의 `text`만 바꾼 사본을 만들고 원본·정답·기대값·ID·행 순서는 유지한다. locked 파일은 읽거나 바꾸지 않는다. 아래 도구는 프롬프트·검사기·서버를 수정하거나 시작하지 않는다.

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

## 7. 튜닝

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

## 8. 8B LoRA 증류 준비

학습 정답은 고정 Qwen 선생 모델의 **수정하지 않은 출력**만 사용한다. 사람·Claude·Codex 번역, 기존 seed·사람이 고친 review_data 산출물은 본학습에 넣지 않는다. AI 검토는 참고 판정이며 사람 검수로 표시하지 않는다.

### 8.1 설치·용량

    bash scripts/run.sh setup
    bash scripts/run.sh setup-train
    bash scripts/run.sh setup-convert

| 파일 | 디스크 크기 | 용도 |
|---|---:|---|
| Qwen3-8B HF 원본 | 약 16GB | NF4 학습 |
| 8B Q4_K_M GGUF | 5.03GB | 기본·어댑터 비교 |
| 14B Q4_K_M GGUF | 9.00GB | 선생 비교·생성 |
| 32B Q4_K_M GGUF | 19.76GB | `--teacher 32b-q4`로 선택하는 DLPC 선생 전용 |
| 환경·캐시·체크포인트 | 추가 수~수십 GB | 설치·체크포인트 수에 따라 변동 |

여유 디스크 50~70GB 이상을 계획한다. 파일 크기는 VRAM 요구량과 다르다. 현재 DLPC는 **VRAM 24GB**이므로 아래 명령은 GPU_LAYERS를 지정하지 않고 manifest의 n_gpu_layers=99로 전체 GPU 적재를 요청한다. 시작 로그의 실제 offload 레이어 수와 실행 중 VRAM 사용량으로 확인한다. 이 PC에서 24GB DLPC의 적재·학습·속도를 실측한 것은 아니다. MODEL_TIMEOUT_S=60은 실행 제한 시간이며 예상 지연이 아니다.

[공식 14B](https://huggingface.co/Qwen/Qwen3-14B-GGUF/tree/530227a7d994db8eca5ab5ced2fb692b614357fd), [공식 32B](https://huggingface.co/Qwen/Qwen3-32B-GGUF/tree/938a7432affaec9157f883a87164e2646ae17555)의 revision·SHA256·크기를 manifest에 고정했다. 생각 모드를 끄며 실제 응답에 생각 내용이 나오면 거절한다.

**32B는 24GB GPU에서 여유가 빠듯하다.** 19.76GB 모델 파일 외에도 KV 캐시와 실행 버퍼가 필요하므로 파일 크기만으로 적재 성공을 판단하지 않는다. 32B 선생은 n_gpu_layers=99·ctx=4096으로 실행하고 CPU로 넘기거나 레이어 수를 줄여 재시도하지 않는다. 사용자 제공 `teacher-02` 실측에서는 RTX A5000에 전체 적재하여 로드 약 2분, 1,700토큰 prompt 처리 2.25초, 생성 28.5 tok/s였다. 이는 전체 teacher-check 완료나 품질 판정 결과가 아니며 이 PC에서 독립 재현하지 않았다. 시연 기본 모델은 계속 8b-q4이며 `/translate`·`/internal/v1/*` 계약도 바뀌지 않는다.

### 8.2 단계별 명령

각 --out은 `teacher-03`처럼 **아직 없는 새 폴더**여야 한다. 기존 `teacher-01`·`teacher-02` 결과를 덮어쓰지 않는다. 24GB DLPC의 전체 단계별 소요 시간은 **실측 전**이며, 기존 8GB CPU offload 가정의 시간 추정은 사용하지 않는다. 다운로드·검토 시간은 별도다.

| 단계 | 소요 시간 |
|---|---|
| teacher-check | 24GB DLPC 실측 필요 |
| generate | 생성 발화 수·실제 토큰 수에 따라 실측 필요 |
| train-eval | 학습 토큰 수·처리량에 따라 실측 필요 |

`--teacher`는 `14b-q4` 또는 `32b-q4`이며 기본값은 `14b-q4`다. 기본 선생의 단계별 명령:

    MODEL_TIMEOUT_S=60 bash scripts/lora.sh teacher-check --out train/runs/teacher-01
    MODEL_TIMEOUT_S=60 bash scripts/lora.sh generate --out train/runs/distill-01
    bash scripts/lora.sh train-eval --data train/runs/distill-01 --out train/runs/lora-01

32B 선생 비교를 명시적으로 선택하는 명령:

    MODEL_TIMEOUT_S=60 bash scripts/lora.sh teacher-check --teacher 32b-q4 --out train/runs/teacher-03

비교·검토에서 32B를 선생으로 선택한 뒤에만 생성으로 진행한다. 자료 생성과 후속 학습에도 같은 선택을 전달한다(생성 후 AI 표본 검토 절차는 8.3절).

    MODEL_TIMEOUT_S=60 bash scripts/lora.sh generate --teacher 32b-q4 --out train/runs/distill-02
    bash scripts/lora.sh train-eval --teacher 32b-q4 --data train/runs/distill-02 --out train/runs/lora-02

선택한 선생은 단계 기록·생성 기록·준비한 자료·학습 설정에 남는다. `prepare_data.py`와 `train_qlora.py`는 선택한 선생 manifest의 모델·revision·해시와 자료 출처를 대조한다. 14B와 32B 자료를 섞거나 다른 선생을 지정하면 중단한다. 학습 대상 본체는 두 경우 모두 8B다.

이전에 GPU_LAYERS를 export했다면 실행 전에 unset GPU_LAYERS로 해제한다. 실제 토큰 수와 필터 통과율에 따라 시간·자료 수가 달라진다. 각 단계는 설정·로그·산출물·stage_summary.md를 한 폴더에 남기고 요약과 경과 시간을 출력한다. 홈의 roadmate-review-*.tgz에는 리뷰 CSV/MD만 포함하고 key·설정·평가 원본은 넣지 않는다. 비교 리뷰는 Claude용·Codex용을 분리한다. 기존 PID·사용 중인 포트를 발견하면 시작하지 않고 자신이 시작한 프로세스만 종료한다.

teacher-check는 **8B 기준 모델과 선택한 선생**으로 general·general_es·naturalness.dev를 평가한다. FAIL·502·첫 시도 오언어·재시도·번역투·p50/p95와 블라인드 리뷰를 남긴다. 선생이 더 나은지 검토한 뒤 generate로 진행한다. 두 모델에는 같은 제한 시간을 적용한다. 14B 실패 시 32B로 자동 전환하지 않는다.

32B의 teacher-check·generate는 시작 전에 디스크를 검사한다. 아직 없는 GGUF(32B 19.76GB, 비교 시 필요한 8B 포함), 보존할 기존 로그의 복사본과 단계 산출물 여유 2GiB를 예약한다. 같은 파일시스템의 필요량은 합산하며 이미 있는 정상 크기 모델은 다시 예약하지 않고 기존 다운로드 단계에서 SHA256을 검증한다. 기존 산출물은 삭제하지 않는다. 부족하면 다운로드·서버 시작 전에 중단한다.

32B는 GPU_LAYERS가 99가 아닌 설정을 거절하며 자동 메모리 맞춤을 끄고 CUDA 장치를 요구한다. 현재 실행의 `offloaded N/N layers to GPU`와 CUDA 모델 적재 로그가 있으면 기존 방식으로 확인한다. 이 로그가 없는 빌드에서는 시작한 llama-server의 PID·프로세스 시작 시각을 확인한 뒤 `nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader,nounits`로 해당 PID의 GPU 메모리를 확인한다. 단일 GPU에서 GGUF 크기의 90% 이상이어야 통과하며, 다른 프로세스나 여러 GPU의 메모리를 합쳐 통과시키지 않는다. 명시적인 부분 적재·로딩 실패 로그는 메모리 수치로 덮어쓰지 않는다.

확인 방식·실제 레이어 수(로그에 있을 때만)·PID·GPU 메모리·기준값·원시 조회 결과·로그 경로·디스크 검사 결과를 `stage_summary.md`에 남긴다. 메모리 조회 실패, N/A, 프로세스 변경, 컨테이너/호스트 PID 불일치, 기준 미달은 중단 사유이며 CPU 비교 결과를 만들지 않는다. 실패한 시작 로그도 보존한다. [NVIDIA의 프로세스 메모리 지표](https://docs.nvidia.com/deploy/nvidia-smi/index.html)는 GPU context의 메모리이므로 이 대체 검사는 메모리 기반 확인이다. 관측하지 않은 레이어 수를 N/N으로 만들어 기록하지 않는다.

모델·어댑터 SHA256은 요청 제한 시간 밖의 초기화 단계에서 준비한다. `run/runtime-hashes/` 디스크 캐시는 해석된 절대 경로·size·mtime_ns·ctime_ns·inode가 모두 같은 파일만 재사용하며, 하나라도 바뀌면 다시 검증한다. cache hit/miss와 해시 계산 시간을 로그에 남긴다. 요청 중 캐시가 없거나 무효화되면 큰 파일을 다시 읽는 대신 준비되지 않은 상태로 거절하므로 서버를 재시작해 초기화를 다시 수행한다. Linux·Windows 시작 스크립트는 프로세스 시작·준비 대기 전에 해시를 준비하며, smoke와 증류 도구도 호출 제한 시간을 시작하기 전에 준비한다. 다운로드 단계의 전체 SHA256 검증은 유지한다. NFS에서 최초 해시는 오래 걸릴 수 있으나 smoke·번역의 모델 응답 제한 시간을 소비하지 않는다.

### 8.3 원문·정답·AI 표본 검토

정적 원문 풀은 **115건의 보조 자료**(번역 107·목적지 8)만 남겼다. 숫자·낱말을 바꿔 늘렸던 2,400건은 삭제했고, 보조 자료도 그룹·locale 방향별 최대 2건이다. 정답은 저장하지 않는다.

주된 원문은 선생 모델이 새로 쓰는 구어 발화다. 기본 목표는 4개 원문 locale × 5개 상황 × 80개로 **중복 제거 후 원문 1,600개**이며, 한국어 원문의 세 목표 locale을 포함하면 **번역 요청 2,400행**이다. 같은 원문을 다른 목표 언어로 옮긴 행은 원문 다양성에 중복 계산하지 않는다. 보조 115건까지 포함하면 필터 전 최대 2,515행이다. 실제 번역 정답은 이 원문을 서버와 같은 프롬프트로 선생 모델에 다시 요청해 얻는다.

한 번에 최대 10개씩 요청하며 배치마다 상황별 주제 하나를 순환 지정한다. 짧은 부탁·질문·상태 설명·확인·정정·가벼운 머뭇거림의 형식, 초점과 seed도 순환한다. 같은 locale·상황에서 이미 채택한 원문 전체를 `already_used`로 전달해 같은 요청을 말만 바꿔 반복하지 않도록 지시한다. 거절된 원문이나 다른 locale·상황의 원문은 이력에 넣지 않는다. 생성문끼리도 숫자 슬롯·지역 locale·근사 문자열 중복을 제거한다. 두 배의 고정 제안 예산 안에서 목표량을 채우지 못하면 기록을 남기고 중단하며 템플릿으로 메우지 않는다. 이 지시는 의미 중복 제거의 보장이 아니므로 의미가 비슷하지만 표현이 크게 다른 중복과 실제 구어성은 표본 검토가 필요하다.

group_id는 상황 전체가 아니라 독립 생성 배치별로 부여한다. 같은 원문의 여러 목표 언어는 같은 그룹을 유지한다. source_generation.jsonl에 생성 프롬프트·seed·원본 응답·모델 해시·채택/거절 이유를 기록하고 source_summary.json에 고유 원문·그룹 수 및 `accepted_by_topic[locale][scenario][topic]`를 남긴다. 주제별 수는 목표 언어로 확장하기 전 채택한 원문 수다. 생성 원문의 원본 응답도 준비 단계에서 대조하므로 출처 이름만 바꿔 선생 발화로 셀 수 없다.

선생 요청은 고정 llama.cpp의 [입력 토큰 계산 API](https://github.com/ggml-org/llama.cpp/blob/b12076/tools/server/README.md#post-v1chatcompletionsinput_tokens-token-counting)로 실제 템플릿·토크나이저를 적용한 수를 확인한다. 원문 생성의 출력 예약 1,024토큰까지 합쳐 ctx 4,096을 넘으면 추론 전에 중단하고 이력을 자르지 않는다. 토큰 계산 API 실패도 중단 사유다. 짧은 합성 원문 79개가 들어가는지는 아래 테스트로 재현하며, 긴 원문 79개까지 들어간다는 보장은 아니다. 테스트 문장은 학습 원문에 사용하지 않는다.

    SOURCE_TOKENIZER_DIR=/고정_14B_tokenizer_경로 python -m pytest tests/test_source_token_budget.py -q

위 폴더에는 manifest의 base_revision에 해당하는 tokenizer.json과 실제 GGUF에서 추출한 gguf-chat-template.jinja가 필요하며 `tokenizers`·`Jinja2`를 설치한 Python으로 실행한다. 실제 모델 생성은 하지 않는다.

원문과 출력 모두 평가셋·few-shot 정확/근사 중복을 제외한다. locked는 봉인된 중복 배제에만 쓰고 추론·점수·튜닝에 쓰지 않는다. 문자·슬롯 비교가 모든 의미상 중복을 보장하지는 않는다. 서버와 같은 프롬프트의 선생 원본 출력에 안전 검사·번역투 필터를 적용한다. 통과분을 단계 폴더의 distill.draft.jsonl과 최신 사본 train/data/distill.draft.jsonl에 저장한다. 이전 사본·원본 응답·해시·필터 결과도 보존한다.

안전 필터를 거친 뒤에도 선생 생성 원문 비율은 **80% 이상**이어야 한다. 보조 자료가 과해지면 보조 행을 제한하며 그 기록도 보존한다. prepare_data와 본학습에서 전체 및 train/validation/test 각 비어 있지 않은 분할의 비율을 다시 검사한다. summary.json·dataset_manifest.json에 필터 전후 비율, 출처별 행 수, 고유 원문 구조·그룹 수가 남는다. 이 비율은 자료 구성 규칙이며 자연스러움 향상의 측정 결과가 아니다.

무작위 최대 150행의 sample.review.csv를 AI에게 전달한다. key는 전달하지 않는다. ai_verdict·ai_issue·notes만 채우고 원문·출력을 고치지 않는다. 실제 검토한 AI 모델을 기록한다.

    .venv/bin/python train/distill.py review --draft train/runs/distill-01/distill.draft.jsonl --review train/runs/distill-01/sample.review.csv --key train/runs/distill-01/sample.key.json --out train/runs/distill-01/sample.evidence.json --reviewer '<실제로 검토한 AI 모델>'

표본 누락·나쁨·해시 불일치는 학습을 막는다. 나쁜 정답을 사람이 고쳐 넣지 말고 선생·생성 단계부터 검토한다. 상태는 auto_filtered_ai_sample_reviewed, human_reviewed=false다. 본학습은 이 증거와 Qwen 원본 출력 해시를 재확인한다. 아직 실제 선생 정답·표본 검토를 만들지 않았다.

### 8.4 학습·채택

`prepare_data.py`는 C단계에서 번역 입력의 STT 형식 사본을 기본 50% 비율로 선택해 추가한다. `eval/stt_format.py`의 관찰 규칙을 사용하며 기본 seed는 기존 분할과 같은 42다. split·source_origin별 번역 행을 seed 해시 순서로 정렬하고 각 묶음에서 `floor(행 수 × 비율)`개를 선택하므로 입력 파일 순서가 표본을 바꾸지 않는다. 소규모 묶음의 내림과 제외 조건 때문에 실제 추가 비율은 50%보다 낮을 수 있다. 목적지 행은 복사하지 않는다.

원본의 선생 출력·AI 표본 검수 증거를 먼저 검증한 뒤 사본의 `text`만 바꾸어 서버와 같은 빌더로 prompt를 만든다. completion은 원본과 같고 group_id·source_origin·source_generation도 유지한다. `meta.stt_copy`에 부모 ID·원문·변환 버전을 기록하여 새 선생 응답과 구분한다. 사본은 원본과 같은 train/validation/test 분할에만 들어간다.

C단계의 원본·사본 경계 검사에서는 **test.locked*를 열지 않는다**. 나머지 평가셋·few-shot의 원문/출력과 그 STT 변형을 정확·근사 중복에서 제외한다. 변환 결과가 동일하거나 안전 검사·경계 검사에 실패한 사본, 다른 group의 틀과 충돌하는 사본은 추가하지 않는다. 원본 자료의 전체·분할별 선생 비율 80% 검사를 먼저 통과해야 하며, 사본 추가 후에도 같은 검사를 수행한다. 보조 자료 사본이 이 기준을 깨면 보조 사본 수를 제한한다. 원본 정답이나 검사기를 변경하지 않는다.

`dataset_manifest.json`의 `stt_copies`에 요청 비율·seed·변환 버전/파일 SHA256·원본 번역 행 수·선택/추가 건수·분할별 추가 건수·제외 사유를 남긴다. `realized_ratio`는 추가 사본/원본 번역 행, `copy_share`는 최종 전체 자료 중 사본 비율이다. 모든 번역 행이 변환되어 50% 사본을 추가하면 최종 번역 자료에서 사본은 약 33.3%다. 사본도 토큰 길이 검사에 포함한다.

준비만 실행하거나 전체 C단계에 비율을 전달할 수 있다. 아래 경로는 실제 검수 완료 자료와 아직 없는 출력 폴더로 바꾼다. `--stt-copies-ratio 0`이면 사본을 추가하지 않는다.

```bash
.venv-train/bin/python train/prepare_data.py --teacher 32b-q4 --inputs train/runs/distill-02/distill.draft.jsonl --ai-review train/runs/distill-02/sample.evidence.json --out train/out-stt-01 --stt-copies-ratio 0.5
bash scripts/lora.sh train-eval --teacher 32b-q4 --data train/runs/distill-02 --out train/runs/lora-stt-01 --stt-copies-ratio 0.5
```

고정 HF 8B에 NF4 double quantization, r16/alpha32, q/k/v/o/gate/up/down, 2 epoch를 적용한다. group_id 단위 80/10/10 분할과 max_length 4096을 사용하고 넘는 정답은 자르지 않고 중단한다. enable_thinking=False로 prompt/completion을 먼저 렌더링하며 정답 JSON에만 loss를 준다. 첫 배치 마스킹도 확인한다. [TRL 형식 문서](https://huggingface.co/docs/trl/sft_trainer#train-on-completion-only)를 따른다.

GGUF 어댑터와 .gguf.json 출처 파일을 만들며 기본 manifest를 수정하지 않는다. train-eval은 8B 기본·8B+LoRA를 같은 세 개발셋으로 비교한다. 독립 검토 뒤 다음을 general·general_es·naturalness.dev 각각에 적용한다.

    .venv/bin/python eval/export_review.py --merge train/runs/lora-01/reviews/claude/review_pair01-general.csv train/runs/lora-01/reviews/codex/review_pair01-general.csv --key train/runs/lora-01/private/review_pair01-general.key.json --out-dir train/runs/lora-01/merged --label pair01-general

불일치는 병합 CSV의 final에 사용자가 A/B별 판정을 적는다. 양쪽 AI 판정이 필요하며 누락을 좋음으로 세지 않는다. 세 파일 검토 후 GPU 없이 판정만 마무리한다.

    bash scripts/lora.sh train-eval --finish train/runs/lora-01 --reviews train/runs/lora-01/merged

조건은 같은 행에서 **나쁨 비증가·어색함 감소**, 각 평가셋 FAIL·502·목적지 실패 비증가, HTTP p95 증가 300ms 이하다. decision.json/.md에 pending/passed/rejected를 기록한다. 실패한 어댑터는 rejected로 표시해 로드를 막고 증거만 보존한다. 통과도 자동 배포하지 않는다.

    MODEL_PROFILE=8b-q4 bash scripts/run.sh start --lora /절대경로/adapter.gguf

후속 평가에도 같은 LORA_ADAPTER 절대 경로를 전달한다. 기본 복귀는 종료 후 LORA_ADAPTER= bash scripts/run.sh start로 실행한다. 실제 14B·32B 품질·VRAM, 학습·변환·적재·LoRA 효과는 DLPC 확인 항목이다.

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

## 10. GitHub 병합(PM)

- `roadmate-ai/` 폴더 하나로 독립. 팀 저장소 루트에 그대로 두면 된다.
- 커밋 대상: 코드, `prompts/`, `packs/`, `eval/*.jsonl`, `models/manifest.json`, `train/*.py`·`training_config.json`·`data/`, `docs/openapi.json`, `requirements*.txt`, lock 파일.
- 커밋 제외(`.gitignore`): `.venv*`, `models/*.gguf`, `models/adapters/`, `tools/llama*/`, `logs/`, `run/`, `eval/results/`, `train/out|runs|base-config/`, `.env`. 큰 파일은 스크립트로 재설치한다.
- CI: 저장소 루트 `.github/workflows/roadmate-ai.yml`이 `roadmate-ai/**` 변경 시 Python 3.11에서 `pytest`와 `guidance.py`를 실행한다(모델 불필요). 폴더 위치를 바꾸면 workflow의 `paths`, `working-directory`도 바꾼다.
- `.gitattributes`: `.ps1`은 CRLF, 나머지는 LF.
- 백엔드·앱과 맞닿는 부분은 코드에 `# 백엔드 연동`, `# 프론트 연동` 주석으로 표시되어 있다.

## 11. 검증 상태

현재 버전은 14B 토크나이저 테스트를 포함한 `python scripts/verify_mvp.py --local`로 API·번역 검증·학습 경계·STT 형식 비교/학습 사본·런타임 해시·GPU 적재 확인·Windows 시작 경로·개인화 회귀 1,797건과 주행 시나리오를 통과했다. 결과는 `run/verify-a6c01a83b9/`에 기록한다. STT 학습 사본·해시·GPU 테스트는 합성 자료·응답으로, Windows 시작 테스트는 합성 실행 명령을 연결한 실제 PowerShell 스크립트로 확인했다. 14B 검사에는 8.3절의 `SOURCE_TOKENIZER_DIR` 설정을 사용했으며, 설정이 없으면 해당 4건은 건너뛴다. 이 명령은 실제 모델 번역이나 SDK 기기 시험이 아니다.

실제 모델 품질의 완료 기준과 실행 순서는 [mvp_acceptance.md](mvp_acceptance.md)에 있다. 평가기는 mock을 기본 거절하고, 실제 서버의 코드·팩 해시와 실행 설정을 기록한다. 자연스러움과 의미 보존은 review.csv에 별도로 검토해야 한다. 기존 버전의 모델 평가 수치를 이번 버전의 성적으로 쓰지 않는다.

현재 기본 모델은 Qwen3-8B Q4_K_M이다. 4B Q4·Q8 대안을 포함해 고정 tokenizer와 실제 GGUF template으로 개발 문장·400자 입력·교정 재시도의 토큰 예산을 검사한다. 이는 측정 사례의 결과이며 모든 입력에 대한 상한 증명은 아니다.

토큰 예산 재검사에는 모델 가중치 대신 해당 base_revision의 tokenizer 파일만 필요하다. `tokenizers`, `Jinja2`가 설치된 Python에서 다음 명령을 쓴다.

```bash
MODEL_PROFILE=8b-q4 python scripts/check_prompt_budget.py \
  --tokenizer-dir <고정_revision의_tokenizer_디렉터리> \
  --chat-template <실제_GGUF_chat_template.jinja>
```

실행 PC에서 남은 확인:

1. 실제 Qwen Q4의 한↔영·한↔스 의미 보존·자연스러움·지연. 개발 평가로 설정 확정 후 잠근 시험은 `--final`로 실행한다.
2. 8장의 선생 비교 → 생성·AI 표본 검토 → 학습 비교 순서로 진행한다. 실측 전에는 어댑터를 채택하지 않는다.
3. Pleos STT/TTS·기어·속도·방향지시등·NaviHelper·FusedLocation 입력을 실제 SDK에서 연결하고 fixture와 동등성 확인.
4. 에뮬레이터와 모델 서버를 30분 함께 실행해 메모리·VRAM·취소·언어 전환을 확인.
