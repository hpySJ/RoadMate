# AI MVP 구현·검증 계약

기준: 2026-11-08에 제공한 온보딩·페르소나 기획서. 화면·Kotlin SDK 호출은 앱 파트가 맡고, 이 폴더는 통역 서버와 앱에 이식할 판단 규칙·문구·시나리오를 제공한다.

## 기능과 구현 위치

| 기획 기능 | AI 구현 | 앱 연결 조건 |
|---|---|---|
| 언어·국가·경험 개인화 | `guidance.DriverProfile`, `packs/rules.json` | 언어와 국가를 독립적으로 저장. 국가·경험 현지화 사전 제공 |
| Nakamura / Carlos / 경험자 | `packs/fixtures/guidance_scenarios.json` | 동일 경로에서 설명은 개인화하고 필수 경고는 모두 유지 |
| 영어·프랑스어·스페인어 안내 | `GuidanceEngine.briefing/evaluate`, 4언어 문구 | STT·TTS locale 설정. 프랑스어 통역·목적지 API는 지원하지 않음 |
| 스쿨존 진입·과속 | `guidance.py` | 유효하고 신선한 구역·경로 정보. 제한값 미상일 때 숫자를 추측하지 않음 |
| 우회전·비보호 좌회전 설명 | `guidance.py` | 조건부 교육 안내. 전용 신호등·횡단 예정 보행자 포함. 실제 신호색이나 통행 가능 여부를 판단하지 않음 |
| 정차 중 한↔영·한↔스 통역 | `engine.py`, `validation.py`, `prompts/` | 최종 STT만 전송, P·속도 확인, epoch 취소, TTS 직전 재확인 |
| 자연스러운 회화 번역 | `translate-v9`, 선택적 `polish-v4` | 원문 의미·주체·정도·숫자·부정·질문 의도 유지. 보험 조건 추정 금지 |
| 목적지 후보 | `PoiCatalog`, `packs/poi.demo.json` | 목록의 좌표만 반환. 미검증 좌표는 항상 확인, 모델이 좌표 생성하지 않음 |

세부 SDK 계약은 `pleos_sdk.md`, HTTP 계약은 `openapi.json`을 따른다. `unprotected_left`는 SDK에서 자동 제공한다고 가정하지 않는다. 앱이 확인한 표지·지도 맥락이 없으면 필드를 생략한다.

## 앱 통합의 선행 결정

2026-11-09에 확인한 팀 Android `895bb08`에는 Kotlin 안내·기어·속도·TTS·음성 큐와 에뮬레이터 확인 기록이 있다. 통역 STT·HTTP 연결과 AI 기준의 정차 잠금은 아직 없다. 시연용 GPU PC·백엔드 사용 여부를 먼저 정하고, 기어 재관측과 원본 m/s·관측 시각 전달을 결정한 뒤 통역을 연결한다. 안내 정책·TTS 완료와 중단의 분리·공통 fixture 이식은 병행할 수 있다. 구현 차이와 순서는 [`pleos_sdk.md`](pleos_sdk.md) 2·3·6장을 따른다.

## 검증 순서와 합격 기준

이 기준은 이번 변경의 실제 모델 출력 확인 전에 정한다. 자동 검사 통과와 의미·자연스러움 검토를 구분하며, 유사도나 키워드만으로 자연스러움 합격을 만들지 않는다.

1. `python scripts/verify_mvp.py --local`: API 계약·취소·타임아웃, 언어·숫자·연료·좌우 검증, 전체 온보딩 조합과 페르소나, 학습 데이터 경계 검사. 결과는 `run/verify-*/report.json`에 저장한다. 이 성공은 실제 모델 품질 완료가 아니다.
2. 실제 Qwen 서버를 띄워 `dev.jsonl`, `general.jsonl`, `general_es.jsonl`, `naturalness.dev.jsonl`을 각각 평가한다. `POLISH_KO=0/1` 비교는 개발 자료에서만 한다. 프롬프트·문구·코드가 바뀌면 서버를 다시 시작하고 결과를 새로 기록한다.
3. `review.csv`의 `human_meaning_ok`, `human_naturalness_ok`(통역), `critical_error`에 독립 검토자가 `yes/no`를 입력한다. 번역체 여부, 발화 주체, 부탁·질문의 의도, 정보의 추가·삭제, 존댓말과 지역 표현을 본다. 검수자 행세를 하거나 자동 결과로 이 칸을 채우지 않는다.
4. 설정을 확정한 뒤 `test.locked.v2.jsonl`을 `--final`로 실행한다. 이 시험을 보면서 튜닝하지 않는다. 실패를 숨기거나 다른 seed로 유리한 결과를 고르지 않는다.
5. `python eval/release_gate.py <결과폴더 5개>`: 각 자료에서 자동 통과율 ≥95%, 의미 보존 ≥95%, 자연스러움 ≥90%, 치명 오류 0건, 성공 응답 HTTP p95 ≤8초. 모델·자료·출력 해시, 전건 결과·검토, 같은 다듬기 설정이 필요하다. mock·미검토·불완전·이전 코드 결과는 실패한다.
6. `python scripts/verify_mvp.py --runs <결과폴더 5개>`로 코드와 모델 품질을 함께 확인한다. Pleos STT/TTS·실제 차량 신호·30분 동시 실행은 별도의 기기 검증이며 이 명령의 성공으로 대신하지 않는다.

실제 평가 예시(Linux):

```bash
bash scripts/run.sh setup
bash scripts/run.sh start
.venv/bin/python eval/evaluate.py --file eval/dev.jsonl --label base-v7
.venv/bin/python eval/evaluate.py --file eval/general.jsonl --label base-v7
.venv/bin/python eval/evaluate.py --file eval/general_es.jsonl --label base-v7
.venv/bin/python eval/evaluate.py --file eval/naturalness.dev.jsonl --label base-v7
# 개발 평가와 설정 선택이 끝난 뒤에만:
.venv/bin/python eval/evaluate.py --file eval/test.locked.v2.jsonl --label final-v7 --final
```

평가 결과의 `run.json`에는 사용 자료·결과 SHA256과 서버가 시작할 때의 코드·문구 해시, 모델·버전·다듬기 설정을 기록한다. `/health/ready`의 `artifact_hashes`와 실행 중 checkout이 같아야 평가를 시작한다. 토큰·원문 실사용 로그는 이 메타데이터에 포함하지 않는다.

## 모델과 파인튜닝

추론 모델은 기존 manifest에 고정한 Qwen3-4B-Instruct-2507 Q4_K_M을 유지한다. 개발 평가의 의미·자연스러움 실패를 먼저 분석하고, 프롬프트·용어집·검증기 오류인지 모델 자체의 일관된 한계인지 구분한다. 다듬기는 기본 꺼짐이다. 수치·부정 검사와 초안 유사도는 의미 보존 전체를 보증하지 않으므로 실제 비교 전에는 기본값을 바꾸지 않는다.

QLoRA는 기존 `train/` 경로를 쓴다. 검토되지 않은 초안은 본학습에 넣지 않는다. 평가 자료와 translate/destination/polish 예시를 학습에서 제외하고, 같은 틀의 숫자·슬롯 변형은 같은 group으로 분할한다. 승인된 자료 1,000건 이상, 학습용 GPU, 데이터 manifest·해시가 준비된 뒤 실행한다. 어댑터는 기본 모델과 같은 개발 자료·실행 예산으로 비교한 뒤 채택한다. smoke 학습·초안 데이터·빈 학습 집합은 최종 어댑터 근거가 될 수 없다.

## 현재 장비에서 남는 외부 검증

2026-11-08 작성 시점의 PC는 Intel 내장 그래픽이며 모델 파일·llama-server·학습 환경과 승인된 학습 자료가 없었다. 당시 DLPC 작업 4037의 Chrome 접근도 도구에서 거부됐다. 이후 실제 모델 평가 보고와 팀 Android의 에뮬레이터 확인 기록은 별도 증거로 다루며, 이번 문서 대조에서 모델·Android 빌드·에뮬레이터를 다시 실행하지 않았다. 외부 접속 경로가 없는 DLPC 작업을 시연 서버로 전제하지 않고, 시연 PC를 확정한 뒤 통합 검증한다.
