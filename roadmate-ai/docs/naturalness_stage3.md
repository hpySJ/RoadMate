# 수정 및 자연스러움 검토 체계 — 2026-11-09

POLISH_KO=0을 유지한다. TWO_STEP_KO, NATURAL_STYLE_KO, FIELD_GLOSSARY는 모두 기본값 0이며 서로 독립적이다. 이번 결과는 로컬 코드 검증 결과이며 실제 모델 번역 품질 개선을 의미하지 않는다.

## A 재현 전후

| 사례 | 수정 전 | 수정 후 및 확인 방법 |
|---|---|---|
| gasoline, not diesel → ‘경유가 아닌 휘발유로 5만 원어치 넣어 주세요.’ | fuel_polarity_mismatch:fuel_diesel | 통과. 동일 원문·출력의 검사기 회귀 시험 |
| ‘경유 아닌 휘발유…’ | 동일 오탐 | 통과 |
| 위 원문 → ‘휘발유가 아닌 경유로…’ | 연료 의미 불일치 | 계속 거절 |
| 위 원문 → ‘경유가 아닌지 확인…’ | 확정된 연료 선택을 보존하지 않음 | 계속 거절. 원문도 ‘경유가 아닌지 확인’하는 뜻이면 통과 |
| ‘근처 편의점 찾아 줘’ | 제공된 4B 실행에서 other/not_destination, 확인 false | 일반 근처+종류+검색 요청 규칙으로 set_destination, query=편의점, 확인 true, 후보 없음. 모델을 호출하지 않는 경로 회귀 시험 |
| 연료 종류 불명 예시·기존 유사 예시 | 평가 문장 또는 숫자·장소 치환 틀 중복 존재 | 독립 문장으로 교체. prompts·packs 문장 누출 자동 검사 통과 |

종류 검색은 한국어·영어·스페인어의 명확한 긍정 검색 요청만 처리한다. 부정·과거 서술·고유 장소가 붙은 요청은 기존 경로를 유지한다. 실제 업체 검색·좌표 조회 기능을 추가한 것은 아니며, 카테고리를 확정 목적지로 제시하지 않는다.

누출 감사는 dev/general/general_es/naturalness 및 잠근 파일을 기계적으로 비교한다. 잠근 파일의 내용은 출력하지 않고 ID·파일 경로만 진단한다. 잠근 정답을 검사기에 넣는 기존 회귀 시험은 실행 대상에서 제외했다. 잠근 파일 자체와 해시는 바꾸지 않았다. 과거 노출 사실은 eval_boundaries.json에 보존하며, 노출을 제거했다고 기존 평가를 새 비공개 시험으로 취급하지 않는다. 의미가 비슷한 모든 의역을 자동으로 탐지한다는 보장은 없다.

## 제공한 기능

- `export_review.py`: 한 실행 또는 두 실행의 모든 ko/en/es 번역, 502·확인 질문·누락을 함께 내보낸다. 행마다 A/B를 섞고 설정은 별도 키에만 저장한다. 검토자 사본에는 자기 칸만 있고 키가 없다.
- `score_review.py`: 설정별 판정·문제 유형·일치율, 같은 행의 양쪽 판정 결과, 뜻 오류·FAIL·502 변화 및 p50/p95를 출력한다. 미검토를 좋음으로 세지 않는다. AI 결과는 ‘AI 검토(참고)’이며 사용자 final이나 출시 승인을 자동으로 채우지 않는다.
- 두 단계 번역은 한국어 목표에서만 literal을 먼저 만든다. 앱 응답에서는 제거하고 LOG_REJECTED 로그에서만 보존한다. 최종 번역만 정규화·안전 검증하며 재시도도 같은 형식이다. 서버와 평가기의 옵션 불일치 또는 실행 중 변경은 실패한다.
- 독립 문체 예시 6개 및 선택 현장 어휘 9개. 평가 출력의 대안 문장을 재사용하지 않는다. 기존 안전 예시의 역할과 안전 검사는 유지한다. 새 예시는 추론 전용이며 학습 빌더에 포함하지 않는다.
- `naturalness_ab.sh`: 기존 DLPC 작업 종료 후 실행하는 비교 파일. 4B 기준·4B 두 단계·8B 기준의 3개 데이터셋과 6개 블라인드 리뷰 파일을 준비한다.

## 검증

세 프로필 모두 실제 GGUF chat template과 고정 tokenizer를 사용해 8개 옵션 조합을 검사했다. 각 프로필 7,504개 검사를 통과했다. 입력 최대 길이의 용어 밀집 사례, 재시도 지시, 두 단계 출력 512토큰 예약을 포함한다.

| 프로필 | 최대 합계 | 문맥 상한 |
|---|---:|---:|
| 4B Q4 | 4,000 | 4,096 |
| 4B Q8 | 4,000 | 4,096 |
| 8B Q4 | 4,004 | 4,096 |

최대 사례 여유가 92토큰이므로 프롬프트·예시·힌트·재시도 지시를 추가하면 다시 검사해야 한다. 이 검사는 허용 가능한 모든 문장의 수학적 상한을 증명하지 않는다.

최종 전체 테스트: **1392 passed in 64.80s (0:01:04)**. 주행 시나리오 PASS. 검증 기록: `run/verify-63b2be3b1a/report.json`. Linux 비교 스크립트의 격리 실행 시험 5개도 포함하며, 실제 모델·네트워크 없이 기존/동시 프로세스 보호와 실패 정리를 확인했다. `git diff --check`와 Bash 구문 검사도 통과했다.

## 아직 확인하지 못한 것

실제 4B/8B의 새 번역 출력·자연스러움·p50/p95·8B GPU 적재·실제 non-thinking 응답은 확인하지 않았다. DLPC 모델 비교를 실행하거나 실행 중인 서버를 조작하지 않았다. 새 리뷰 도구는 합성 결과로 검증했고 실제 리뷰 판정은 아직 채우지 않았다. 원어민 검수도 하지 않았다. LoRA는 계획 문서만 작성했으며 자료 생성·학습은 실행하지 않았다.

사용법은 [guide.md](guide.md#74-자연스러움-블라인드-비교), 판단 기준은 [naturalness_rubric.md](naturalness_rubric.md), 개선·LoRA 계획은 [naturalness_plan.md](naturalness_plan.md)를 참고한다.

## 변경 파일

```text
.env.example
app.py
config.py
destination_intents.py
docs/guide.md
docs/naturalness_plan.md
docs/naturalness_rubric.md
docs/naturalness_stage3.md
engine.py
eval/evaluate.py
eval/export_review.py
eval/release_gate.py
eval/score_review.py
models/manifest.json
models/manifest.qwen3-4b-q8.json
models/manifest.qwen3-8b.json
packs/field_glossary.json
prompting.py
prompts/destination.examples.json
prompts/destination.txt
prompts/translate.examples.json
prompts/translate.natural.examples.json
prompts/translate.txt
scripts/check_prompt_budget.py
scripts/naturalness_ab.sh
tests/test_contract.py
tests/test_destination_intents.py
tests/test_eval_data.py
tests/test_evaluation.py
tests/test_inference_boundary.py
tests/test_naturalness_script.py
tests/test_output_validation.py
tests/test_prompting.py
tests/test_review_tools.py
tests/test_two_step.py
train/data_boundary.py
train/eval_boundaries.json
validation.py
```
