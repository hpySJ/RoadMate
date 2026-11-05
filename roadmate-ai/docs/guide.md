# roadmate-ai 사용 안내

설치, 서버 실행, 백엔드·앱 연동 규칙, 평가, 튜닝, 학습, 문제 해결을 모아 둔 문서입니다. 아래 1~11장은 Windows 기준이고 명령은 `roadmate-ai` 폴더에서 PowerShell로 실행합니다. Linux(DLPC 등)는 바로 아래 장을 먼저 봅니다.

## Linux(DLPC)에서 실행

Ubuntu에서는 PowerShell 스크립트 대신 `scripts/run.sh` 하나로 설치하고 실행합니다. llama.cpp는 공식 Ubuntu CUDA 12.8 빌드를 받아 쓰므로 직접 빌드하지 않습니다. 명령은 JupyterLab 터미널의 `roadmate-ai` 폴더에서 실행합니다.

```bash
git clone -b feat/roadmate-ai-server https://github.com/hpySJ/RoadMate.git
cd RoadMate/roadmate-ai
nvidia-smi                                   # GPU 이름과 VRAM 확인
bash scripts/run.sh setup                    # .venv, llama.cpp, 모델 다운로드, 환경 확인
bash scripts/run.sh start                    # 모델 서버 → AI 서버 → smoke 시험(SMOKE OK가 나오면 성공)
.venv/bin/python eval/evaluate.py --file eval/dev.jsonl --label q4-base
bash scripts/run.sh stop
```

학습 쪽도 같습니다. 서버를 끈 뒤 실행합니다.

```bash
bash scripts/run.sh setup-train
.venv-train/bin/python train/prepare_data.py --allow-draft --tokenizer Qwen/Qwen3-4B-Instruct-2507
.venv-train/bin/python train/train_qlora.py --smoke
bash scripts/run.sh setup-convert
.venv/bin/python train/convert_adapter.py --adapter train/runs/lora-v1/smoke/adapter --apply --allow-smoke
```

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
| GPU | NVIDIA RTX 4060급 (VRAM 8GB 기준) |
| 드라이버 | CUDA 12.4 빌드 사용 → NVIDIA 드라이버 551 이상 권장 |
| RAM | 16GB 이상 |
| Python | 3.11 (python.org 설치, `py -3.11` 사용 가능해야 함) |
| 디스크 | 서버 약 5GB, 학습까지 약 25GB(원본 가중치·캐시 포함) |

추론 시 GPU 사용량 목표: 모델 프로세스 VRAM 5GiB 이하, 에뮬레이터와 동시 실행 시 GPU 여유 1GiB 이상.

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
 "engine_bundle":"roadmate-ko-en-q4-v1","model_id":"roadmate-qwen4b-q4",
 "prompt_version":"translate-v2","glossary_version":"rental-ko-en-v2","latency_ms":850}
```
- `source_locale`/`target_locale`: `ko-KR`, `en-US` / `scenario`: `gas_station`, `parking`, `rental_counter`, `accident`, `general`
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
APK의 `127.0.0.1`은 PC가 아니다. APK → `http://10.0.2.2:8080`(백엔드), 백엔드 → `http://127.0.0.1:8000`(AI). HTTP 예외는 debug 빌드 `network_security_config`의 `10.0.2.2` domain-config에만 둔다.

## 5. 앱 연동(주행 안내 규칙·정차 잠금)

주행 안내는 LLM이 아니라 **검수된 규칙과 문구**로 APK 안에서 실행한다. `guidance.py`가 기준 구현이고, 앱 담당이 Kotlin으로 포팅한 뒤 같은 fixture로 동등성을 시험한다.

- 규칙·문구: `packs/rules.ko-en.json` (스쿨존 현재 구간 과속, 스쿨존 접근, 제한값 미상 접근, 우회전 조건부 안내, 출발 브리핑)
- 시나리오 fixture: `packs/fixtures/guidance_scenarios.json` → `.\.venv\Scripts\python.exe guidance.py`로 전부 OK 확인
- 입력 스냅샷: 각 신호에 `value`(또는 `state`), `status`(`valid`/`unavailable`/`error`), `ts_ms`. 속도는 **m/s**, 제한속도는 km/h.
- 핵심 규칙
  - 현재 도로 제한속도(`onCurrentRoadSpeedLimit`)를 앞 스쿨존 제한값으로 쓰지 않는다. 앞 구역 값이 없으면 "표지판 확인" 문구.
  - 이미 제한속도 이하이면 말하지 않는다. 과속은 +3km/h가 2초 지속될 때만.
  - 우회전은 우측 깜빡이 + TBT 우회전 맥락(10~150m)일 때만. 비상등·차로 변경은 제외. 신호색을 단정하지 않는다.
  - 오래된 신호(속도 1.5초, 내비 5초)·`status≠valid`는 사용하지 않는다.
  - 같은 규칙 하루 2회, 쿨다운. **실제 재생 완료 콜백(`mark_played`)에서만 카운트**.
  - 발화 큐는 규칙별 최신 1건, 재생 직전 `revalidate`로 지난 구역 안내 폐기.
- 정차 잠금 `StopLock`: 기어 P(status valid) + 유효 속도 |v| ≤ 0.1m/s가 1초 유지일 때만 통역 버튼 활성. STT 시작·전송·번역 도착·TTS 직전에 다시 확인.
- 출발 브리핑: `GuidanceEngine.briefing(origin_country)` — 좌측통행 국가(AU, JP, TH 등)면 "오른쪽 통행" 문구가 먼저 나온다.
- 문구 팩 상태가 `draft`다. 배포 전 의미·발음 검수 후 `review.status=approved`, `reviewer`를 채운다(`allow_draft=False`면 draft 팩은 로딩 거부).

## 6. 평가

```powershell
.\scripts\start_all.ps1
.\.venv\Scripts\python.exe eval\evaluate.py --file eval\dev.jsonl --label q4-prompt-v1
```
결과: `eval\results\<label>-dev-<시각>\summary.md`, `results.jsonl`, `review.csv`(엑셀로 열림)

- `eval/dev.jsonl` 100건(통역 60 · 목적지 25 · 모호/지시 삽입 15): 프롬프트·용어집·추론 설정을 고를 때 사용.
- `eval/test.locked.jsonl` 60건: **설정을 확정한 뒤 한 번만** 실행. 결과를 보고 설정을 바꾸면 그 파일은 회귀용으로 돌리고 새 시험셋을 만든다.
- 자동 통과는 보조 지표다. `review.csv`의 `human_meaning_ok`, `critical_error`를 원문·번역을 아는 사람이 채운다. 모델 채점으로 통과시키지 않는다.
- 필수 최소쌍: 휘발유/경유, 5만/15만 원, 가득/3만 원만, 다친 사람 있다/없다, 오세요/오지 마세요, 왼쪽/오른쪽.
- 1차 목표(측정 전 목표값): 잠근 시험 의미 보존 90% 이상, 준비 후 짧은 문장 번역 p95 3초, 발화 종료→TTS 시작 p95 5초, 에뮬레이터와 30분 동시 실행, P 해제 후 이전 결과 재생 0건.
- 평가 사례는 모두 `review_status: draft`다. 사람이 검수해 `approved`로 바꾼다. `tests/test_eval_data.py`가 정답이 검증기를 통과하는지, dev/test/few-shot 사이 누수가 없는지 확인한다.

### 서버가 자동으로 하는 출력 검증(`validation.py`)
JSON 계약 → 목표 언어 문자 → 설명문 섞임 → 길이 → 숫자·금액·시간·전화번호 보존(3만 원=30,000 won, 오후 3시=3 p.m. / 원↔달러, 오전↔오후가 바뀌면 실패) → 부정 보존 → 연료 용어(gas는 휘발유로 확정 금지)와 연료별 긍정·부정(“휘발유, 경유 말고”가 뒤집히면 실패) → 좌/우. 실패하면 이유를 붙여 **한 번만** temperature 0으로 다시 생성하고, 그래도 실패하면 502로 재생을 막는다.

## 7. 튜닝

실패 원인을 먼저 나눈다: STT 오인식 / 프롬프트 / 용어 / 양자화 / 입력 모호성. 큰 모델로 바로 바꾸지 않는다.

| 순서 | 무엇을 | 어디서 |
|---|---|---|
| 1 | 번역 지시문·few-shot | `prompts/translate.txt`, `prompts/translate.examples.json` (바꾸면 첫 줄 `prompt_version` 올림) |
| 2 | 용어집 | `packs/glossary.ko-en.json` (`version` 올림). 발화에 나온 용어만 프롬프트에 들어감 |
| 3 | 샘플링 | `prompting.py`의 `TRANSLATE_SAMPLING` 등(기본 temperature 0.2, top_p 0.8, top_k 20) |
| 4 | 추론 자원 | `models/manifest.json`의 `runtime`(ctx 2048, parallel 1, n_gpu_layers 99, batch 256, ubatch 128) |
| 5 | 검증 임계값 | `validation.py`(숫자·부정·방향 규칙), `engine.py`의 POI 점수 0.62 |
| 6 | Q5 비교 | Q4에서 의미 오류가 남고 VRAM 여유가 있을 때만(아래) |

변경할 때마다 `eval\evaluate.py --file eval\dev.jsonl --label <이름>`으로 같은 dev 자료를 다시 돌려 비교한다.

느리거나 VRAM이 부족할 때: GPU 사용 확인(시작 로그 offload) → ctx·parallel·출력 상한 확인 → 불필요한 프로세스 종료 → `.\scripts\start_model.ps1 -GpuLayers 30`처럼 레이어 수를 줄이고 지연 재측정 → 그래도 부족하면 모델 PC와 에뮬레이터 PC 분리.

Q5_K_M 비교:
```powershell
.\.venv\Scripts\python.exe scripts\download_model.py --file Qwen3-4B-Instruct-2507-Q5_K_M.gguf --sha256 5bde5e9d883622acb02bf77fe7dcbc56a8b9a9ad4be78a72ca23a532658b4ecb
```
`models/manifest.json`의 `gguf_file`, `gguf_sha256`, `gguf_size`, `engine_bundle`(예: `roadmate-ko-en-q5-v1`)을 바꾸고 재시작 → dev 평가. 두 모델을 동시에 띄우지 않는다.

## 8. 학습(QLoRA)과 배포

1차 기본안은 **학습 없이 기준 Q4 + 프롬프트·용어집**으로 제출한다. QLoRA는 검수 데이터·GPU 시간이 확보될 때만 수행하고, 배포본이 기준보다 낫고 중요 정보 오류가 늘지 않을 때만 채택한다. 학습 중에는 에뮬레이터·llama-server를 끈다.

### 8.1 환경
```powershell
.\scripts\setup_train_env.ps1      # .venv-train: torch 2.14.1+cu126, transformers 5.22.1, peft 0.21.2, trl 1.17.1, bitsandbytes 0.50.2
.\scripts\setup_convert_env.ps1    # .venv-convert: llama.cpp b12076 변환기 요구사항(transformers 4.57.6 고정이라 분리)
```
마지막 줄에 `cuda True`, GPU 이름, `bf16 True`가 보여야 한다.

### 8.2 데이터 작성
`train/data/*.jsonl` 한 줄에 사례 하나:
```json
{"group_id":"fuel_10k_diesel","task":"translate","source_locale":"en-US","target_locale":"ko-KR","scenario":"gas_station",
 "text":"Just 10,000 won of diesel, please.",
 "output":{"status":"ok","translated_text":"경유로 만 원어치만 넣어 주세요.","clarification_question":null},
 "review_status":"approved","source":"authored"}
```
- 목적지: `"task":"destination"`, `output`은 `intent, query_original, query_ko_candidate, region_hint, category_hint, needs_confirmation`.
- 같은 원문·역번역·금액/장소 치환 변형은 같은 `group_id`.
- 사람이 원문·정답을 대조한 것만 `"review_status":"approved"`. 로컬 모델 초안을 그대로 정답으로 쓰지 않는다. 개인정보 제거.
- 목표: 검수 1,000~1,500건(번역 1,200 · 목적지 200 · 모호/확인 100), 한→영·영→한 균형.
- `train/data/seed.draft.jsonl`은 형식·스모크 시험용 초안 26건이다.

### 8.3 준비 → 스모크 → 본학습
```powershell
.\.venv-train\Scripts\python.exe train\prepare_data.py --tokenizer Qwen/Qwen3-4B-Instruct-2507
.\.venv-train\Scripts\python.exe train\train_qlora.py --smoke
.\.venv-train\Scripts\python.exe train\train_qlora.py
```
- `prepare_data.py`: approved만 사용, 모든 정답을 서버 검증기로 검사, eval·few-shot과 겹치면 중단, **서버와 같은 프롬프트 빌더(`prompting.py`)로 prompt 생성**, 토큰 길이 > `max_length`면 중단(정답 잘림 방지), group_id 해시로 80/10/10 분할(자료를 추가해도 기존 그룹의 분할이 바뀌지 않음), `train/out/*_manifest.json` 기록.
- `train_qlora.py`는 `dataset_manifest.json`의 해시를 검사하고, `--allow-draft`로 만든 자료면 `--smoke` 외에는 중단한다.
  스모크 시험만 할 때는 `--allow-draft`로 seed 초안을 쓴다.
- `--smoke`: optimizer step 2회, completion 마스크 검사(정답 JSON+`<|im_end|>`만 학습), 어댑터 저장, 생성 1회. `train/runs/lora-v1/smoke/run_meta.json`에서 `peak_vram_gib` 확인.
- 설정은 `train/training_config.json`(NF4 + double quant, LoRA r8/α16/dropout 0.05, q_proj·v_proj, 배치 1×누적 16, lr 1e-4 cosine, warmup 5%, epoch 1, max_length 1536, gradient checkpointing). few-shot 포함 번역 프롬프트가 약 1,200토큰(translate-v2 추정)이라 max_length를 1536으로 둔다. `prepare_data.py --tokenizer`가 실제 길이를 재고 넘으면 중단한다.
- 학습 loss 감소만으로 고르지 않는다. 숫자·부정·연료 오류가 늘면 채택하지 않는다.

### 8.4 효과 비교(같은 검증 자료)
```powershell
.\.venv-train\Scripts\python.exe train\eval_hf.py --split validation                                        # NF4 기준
.\.venv-train\Scripts\python.exe train\eval_hf.py --split validation --adapter train\runs\lora-v1\adapter   # NF4 + LoRA
```

### 8.5 GGUF 어댑터로 배포
```powershell
.\.venv\Scripts\python.exe train\convert_adapter.py --adapter train\runs\lora-v1\adapter --apply
.\scripts\stop_all.ps1; .\scripts\start_all.ps1
.\.venv\Scripts\python.exe eval\evaluate.py --file eval\dev.jsonl --label q4-lora-v1
```
- 고정 revision의 base config로 `convert_lora_to_gguf.py` 실행 → `models/adapters/roadmate-lora-v1-f16.gguf`, SHA256을 manifest에 기록, `engine_bundle`이 `roadmate-ko-en-q4-lora-v1`로 바뀐다(백엔드도 같은 bundle 이름으로 호출).
- `start_model.ps1`이 manifest의 adapter를 보고 `--lora`를 붙인다. `/health/ready`가 어댑터 로딩을 확인한다.
- 채택 판단은 **기준 Q4 vs Q4+LoRA를 dev·검증 분할로** 한다. 잠근 시험은 결정 후 한 번만.
- 되돌리기: `.\.venv\Scripts\python.exe train\convert_adapter.py --remove` 후 재시작.
- 변환 전 어댑터 옆 `run_meta.json`의 학습 원본(repo·revision)이 manifest 본체와 다르면 중단한다.
- **본학습 전에** 작은 스모크 어댑터로 이 변환·로딩을 먼저 끝까지 확인한다(아래 검증 상태 참고). 스모크·초안 어댑터 등록은 `--allow-smoke`를 붙여야 하며, 확인 후 `--remove`로 되돌린다.
  `.\.venv\Scripts\python.exe train\convert_adapter.py --adapter train\runs\lora-v1\smoke\adapter --apply --allow-smoke`

## 9. 문제 해결

| 증상 | 확인 |
|---|---|
| `/health/ready` 503, `model_server: false` | `logs\llama.err.log`, 8081 포트 충돌, 모델 파일 경로 |
| `model_alias: false` | manifest `model.alias`와 llama-server `--alias` 일치 |
| 시작 로그에 GPU offload 없음 | CUDA 빌드 설치 여부, 드라이버, `nvidia-smi` |
| CUDA out of memory | 에뮬레이터 그래픽 설정, `-GpuLayers` 축소, ctx 유지(2048) |
| 429 `busy`가 잦음 | 앱에서 같은 세션 중복 호출, 버튼 연타 방지 |
| 취소·504 뒤 계속 429 | llama-server가 아직 계산 중이면 슬롯을 열지 않는다(`logs\ai.err.log`에 `still processing`). 계속되면 `stop_all.ps1` → `start_all.ps1` |
| `already running` / `port ... in use` | 이전 서버가 남아 있음. `stop_all.ps1`, 그래도 남으면 `netstat -ano \| findstr :8000`(또는 8081)의 PID 종료 |
| 502 `output_validation_failed` | `message`에 원인(예: `number_missing:30000`, `negation_mismatch`). dev 평가로 패턴 확인 → 프롬프트·용어집 수정 |
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

작성 PC(GPU 없음)에서 확인한 것과 실행 PC에서 처음 확인해야 할 것을 구분한다.

**확인함**
- `pytest` 259건 통과: API 계약·오류 코드·중복/충돌·epoch 취소(진행 중 취소·늦은 취소·슬롯 대기 중 취소)·busy 429·timeout 504·모델 계산 중 슬롯 유지·검증 실패 502·교정 재시도·목적지 매칭·OpenAPI 일치, 검증기 최소쌍(연료 긍정·부정, 오전·오후, 통화 포함), 규칙 엔진 fixture 33단계(관측 공백·미래 시각 포함), 발화 큐 반복 제한, 평가 데이터 117개 정답이 검증기를 통과하고 누수 없음.
- `start_all.ps1 -Mock` → `smoke.py` → `evaluate.py` → `stop_all.ps1` 실제 실행. 중복 시작·포트 점유 시 시작 거부 확인.
- `train_qlora.py`의 데이터 manifest 검사는 작성 PC에 torch 환경이 없어 실행하지 못했다(`prepare_data.py`·`convert_adapter.py`의 검사는 실행 확인).
- 실제 Qwen 토크나이저로 프롬프트 길이 측정(translate-v1 약 980토큰, 목적지 약 480토큰). translate-v2는 약 1,200토큰으로 추정하며 DLPC에서 `prepare_data.py --tokenizer`로 다시 잰다.
- 학습 파이프라인(transformers 5.22.1, trl 1.17.1, peft 0.21.2 실제 버전)을 CPU·초소형 Qwen3 구조로 끝까지 실행: 데이터 준비 → completion 마스크 검사 → 학습 2 step → 평가 → 어댑터 저장 → 생성 → `eval_hf.py`.
- llama.cpp b12076 릴리스 자산 이름, `/slots`·`--lora`·`--cache-ram`·`--jinja` 옵션, `response_format` 파싱 방식을 소스·문서에서 확인.

**실행 PC(RTX 4060)에서 처음 확인할 것**
1. `setup_llama.ps1` CUDA 빌드 설치와 GPU offload, VRAM 실측(`check_env.py`)
2. 실제 Qwen Q4 번역 품질·지연: `evaluate.py` dev 실행 후 `review.csv` 사람 판정
3. NF4 4비트 로딩(bitsandbytes CUDA)과 `--smoke`의 `peak_vram_gib`(8GB 안인지)
4. LoRA → GGUF 변환(`convert_adapter.py`)과 `--lora` 로딩 — 작성 PC에서는 외부 코드 실행이 막혀 미검증
5. 에뮬레이터·Pleos STT/TTS와 동시 30분 실행
