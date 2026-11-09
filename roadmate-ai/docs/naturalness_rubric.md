# 번역 자연스러움: AI 검토(참고)

Claude와 Codex의 판단은 원어민 검수나 제품 출시 승인이 아니다. 원문과 번역의 뜻을 먼저 비교하고, 현장에서 실제로 쓸 수 있는 말인지 판단한다. 자동 검사 통과도 뜻 보존을 보장하지 않는다. 두 검토자는 상대의 판정·대안과 모델 이름을 보지 않고 독립적으로 검토한다. 의견 불일치는 사용자가 최종 판단한다.

| 판정 | 기준 |
|---|---|
| 좋음 | 원문의 사실·의도·말하는 사람과 듣는 사람의 역할을 보존하고, 현장에서 자연스럽게 이해할 수 있다. |
| 어색함 | 뜻은 보존하지만 직역, 과한 대명사, 문법·말투 때문에 다듬을 필요가 있다. |
| 나쁨 | 뜻이 바뀌거나 빠지거나 추가됐다. 또는 실제 의사소통을 방해할 정도로 이해하기 어렵다. |

**뜻이 바뀌면 문장이 유창해도 ‘나쁨’이다.** 숫자·금액·시간·연료 종류·부정·좌우·주고받는 방향·질문과 부탁의 차이·추측과 확정·선택 관계를 우선 확인한다. 관용 표현은 단어별 직역이 아니라 문맥상 의미로 비교한다. 애매한 원문은 가능한 해석과 불확실성을 자기 `issue` 칸의 `note`에 적고 정답을 지어내지 않는다.

문제 유형은 중복 선택할 수 있다: **뜻 오류, 직역 표현, 낱말·용어 오역, 대명사·말투, 문법, 외국어 섞임, 현장에서 안 쓰는 표현**. 맞는 출력에는 빈 목록을 쓴다. 대안 문장은 검토 이유를 설명할 때만 적는다.

| 언어 | 확인점 |
|---|---|
| 한국어 | 대화에 맞는 공손한 -요체인지, 같은 문장 안에 말투가 섞이지 않는지 확인한다. 영어식 대명사 반복, ‘~는 것이’, ‘여부’, ‘~하기 위해’가 문맥상 불필요하게 딱딱한지 살핀다. 넣다·빼다·내다·반납하다 같은 현장 동사가 자연스러운지 본다. 대명사를 무조건 삭제하지 않는다. 강조·대조·인용에는 필요할 수 있고, 조사·주어 삭제가 주고받는 방향을 바꿀 수 있다. |
| 영어 | 쉽고 공손한 구어인지, 부자연스러운 명사화나 직역이 없는지 확인한다. 정중함을 더하려다가 질문·확신·요청의 세기를 바꾸지 않는다. |
| 스페인어 | 상대를 존중하는 usted 문맥과 중남미에서 이해 가능한 표현인지 확인한다. 악센트와 ¿/¡, 성·수·동사 일치를 살핀다. 지역별로 허용되는 말은 단순 취향으로 나쁨 처리하지 않으며, 원어민 확인이 필요한 판단은 자기 issue의 note에 남긴다. |

## 파일과 판정 형식

`eval/export_review.py`는 `results.jsonl`과 `run.json`이 있는 결과 폴더 한 개 또는 두 개를 읽는다. ko/en/es 목표의 모든 번역 행을 내보낸다. 자동 평가에서 FAIL인 정상 응답도 포함한다. 502는 `[502 · 안전 검사 거절]`, 확인 질문은 `[확인 질문]`, 누락은 `[출력 없음]`, 나머지 오류는 `[오류 · HTTP …]`로 표시한다. 이들은 번역 품질 등급을 매기지 않으며 별도로 집계한다. 막힌 출력을 검토하려고 거절 로그의 후보 번역을 정상 응답 칸에 넣지 않는다.

두 실행의 같은 id는 원문·원문 언어·목표 언어·작업이 정확히 같아야 한다. 한쪽에 없는 행도 누락으로 남긴다. 행마다 A/B 순서를 독립적으로 무작위 배치한다. 설정 이름·프로필·실행 경로·매핑·원본 해시는 `.key.json`에만 보관한다. CSV와 읽기용 MD에는 설정 정보가 없다. 파일 이름은 `pair01`처럼 중립적으로 정한다. 키는 검토자에게 전달하지 않는다.

기본 CSV 칸은 다음과 같다.

`id, direction, source, A, B, claude_verdict, claude_issue, claude_rewrite, codex_verdict, codex_issue, codex_rewrite, final, notes`

판정·문제·대안·final은 **A/B를 키로 하는 JSON 객체**다. 단일 실행은 A만 쓴다. 아직 판단하지 않은 쪽은 키를 생략하거나 셀을 비워 둔다.

| 칸 | 셀 값의 형태 |
|---|---|
| `codex_verdict` | `{"A":"좋음","B":"어색함"}` |
| `codex_issue` | `{"A":[],"B":["직역 표현","대명사·말투"]}` |
| `codex_rewrite` | `{"B":"검토자가 제안한 대안"}` |
| `final` | `{"A":"좋음","B":"나쁨"}` — 사용자가 최종 판단할 때만 기록 |

Claude 칸도 같은 형식이다. 이유나 불확실성을 설명하려면 issue에 `{"B":{"types":["직역 표현"],"note":"구체적인 검토 근거"}}`를 쓸 수 있다. 문제 유형 목록만 집계하고 note는 검토 근거로 보존한다. 502·확인 질문·누락·오류 쪽의 판정이나 대안을 넣으면 입력 검증이 실패한다. 정상 번역에 대한 미완성 검토는 그대로 미검토로 남는다. `final`은 AI끼리 합의했다고 자동으로 채우지 않는다. 이 형식은 기존 `judge`/`release_gate`의 사람 검수·승인 칸과 독립적이다.

## 독립 검토와 병합

프로젝트 루트(`roadmate-ai`)에서 다음처럼 실행한다. 아래 경로는 사용 예이며 DLPC의 실행 중인 모델을 호출하지 않는다.

```bash
python eval/export_review.py run_A run_B --label pair01 --out-dir review/master
python eval/export_review.py --review review/master/review_pair01.csv --key review/master/review_pair01.key.json --reviewer codex --label pair01 --out-dir review/codex
python eval/export_review.py --review review/master/review_pair01.csv --key review/master/review_pair01.key.json --reviewer claude --label pair01 --out-dir review/claude
```

검토자에게 **자기 폴더의 CSV/MD만** 전달한다. 검토자용 파일에는 상대의 칸·판정·대안, `final`, `notes`가 없다. `--reviewer`는 검토자 폴더에 키를 복사하지 않는다. 중앙 master 키는 관리자만 보관하고 병합·집계할 때 사용한다. 각 검토자는 자기 CSV의 세 칸만 채운다. 원문·A/B·id·direction을 고치면 해시 검사가 실패한다. CSV가 편집 원본이고 MD는 읽기용 사본이다. 편집 후 MD가 필요하면 다른 출력 폴더로 다시 내보낸다.

```bash
python eval/export_review.py --merge review/claude/review_pair01.csv review/codex/review_pair01.csv --key review/master/review_pair01.key.json --label merged --out-dir review/combined
python eval/export_review.py --review review/combined/review_merged.csv --key review/combined/review_merged.key.json --only disagreements --label disagreements --out-dir review/check
python eval/export_review.py --review review/combined/review_merged.csv --key review/combined/review_merged.key.json --only bad --label bad --out-dir review/check
python eval/score_review.py review/combined/review_merged.csv --key review/combined/review_merged.key.json --out-dir review/score
```

병합의 첫 파일은 Claude, 둘째는 Codex 파일이다. 두 검토 파일의 행 집합이 같아야 한다. 기존 합본에 미완성 검토를 보충할 때는 `--review 기존합본.csv --merge …`를 함께 쓴다. 기존 판정·대안을 다른 값으로 덮어쓰려 하면 실패하며 사용자 `final`과 `notes`는 보존한다. 수정된 의견을 반영하려면 별도 새 합본을 만들고 변경 이력을 관리한다.

`--only disagreements`는 **같은 출력을 둘 다 판단했고 서로 다른 행**만, `--only bad`는 두 검토자나 final 중 하나라도 ‘나쁨’인 행만 추린다. 판정 칸은 보존하며 원본 키와 그대로 대응한다. 모든 쓰기는 새 경로에만 가능하다. 기존 파일을 자동 덮어쓰지 않는다. 원본 결과 해시, 변경 금지 셀 해시, 키 체크섬은 실수에 의한 변경을 검출하는 장치이며 작성자 신원을 인증하는 전자서명은 아니다.

## 집계와 사용 범위

`score_review.py`는 키로 설정을 복원해 프로필과 `two_step_ko`/`natural_style_ko`/`field_glossary`를 표시하고, 설정별·검토자별 좋음/어색함/나쁨 수와 비율, 문제 유형 수를 만든다. 설정이 기록되지 않았으면 꺼짐으로 추정하지 않고 null로 표시한다. 비율의 분모는 **해당 검토자가 실제 판정한 정상 출력**이다. 정상 출력 수와 미검토 수도 함께 출력한다. Claude–Codex 일치율은 같은 출력을 둘 다 판정한 경우만 분모에 넣는다. 사용자 final 기록은 별도로 집계하고 AI 의견을 대체하지 않는다.

두 설정의 채택 비교에는 **같은 원문 행의 양쪽 정상 출력을 동일 검토자가 모두 판정한 행**만 쓴 별도 표가 있다. 전체 비율의 분모 차이로 개선처럼 보이는 일을 피하기 위해, 양쪽 판정 완료 행 수·설정별 누락 판정 수·같은 행의 나쁨 비율·나쁨 개선/악화 전이를 함께 표시한다. 뜻 오류 비교는 그중 양쪽 issue가 명시된 행만 사용한다. 문제 없음은 `[]`로 명시하고, 빈 셀을 ‘뜻 오류 없음’으로 해석하지 않는다.

자동 FAIL/502는 같은 원문에서 새로 생긴 실패와 회복을 각각 보여 준다. 지연은 양쪽 HTTP 200이며 측정값이 있는 같은 행의 p50/p95와 차이를 사용한다. 모델 지연과 HTTP 전체 지연을 따로 표시하며, 측정 누락은 0ms로 바꾸지 않는다. 표본 수가 달라지면 반드시 확인한다. 채택 기준은 같은 행의 나쁨 비율 감소, FAIL·502·뜻 오류 악화 없음, p95 증가 300ms 이하이며 **도구가 채택을 자동 승인하지는 않는다**.

필터된 파일에는 원본 중 몇 행인지 표시한다. 부분집합의 나쁨 비율을 전체 모델 품질로 해석하지 않는다. 원본 전체 상태 수와 파일 내 상태 수를 나란히 출력한다. 이 도구는 합격·자동 채택·사람 검수 완료를 선언하지 않는다.

**Claude·Codex 대안은 검토 근거로만 쓴다.** 프롬프트·예시·용어집·POI·학습 자료로 내보내는 기능은 없다. 상용 AI 출력의 학습 사용은 약관 확인 전 금지한다. 평가 원문도 학습이나 프롬프트로 이동하지 않으며 `test.locked*` 결과는 이 작업에서 읽지 않는다.
