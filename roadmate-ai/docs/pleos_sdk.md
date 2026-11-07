# Pleos SDK와 AI 파트

우리 앱은 Pleos Connect SDK 다섯 가지(STT, TTS, 차량 정보, NaviHelper, FusedLocation)를 씁니다. 공모전이 다섯 가지를 모두 쓰라고 정한 것은 아니고, 핵심 기능에 맞게 쓰는지를 봅니다. 이 문서는 AI 파트가 각 SDK에서 무엇을 받고 무엇을 돌려주는지 정리한 것입니다. SDK 호출 자체는 앱 담당이 Kotlin으로 하고, AI 파트는 값의 의미와 형식을 맞춥니다.

이름과 동작은 Pleos 공식 문서(2026년 11월 4일 확인) 기준입니다. 에뮬레이터에서 실제로 동작하는지는 아직 확인하지 않았습니다.

## 한눈에 보기

| SDK | 앱에서 하는 일 | AI 파트와 맞닿는 곳 |
|---|---|---|
| SpeechToText | 정차 중 통역 버튼을 누르면 말을 받아 적음 | 최종 인식 문장 → `/internal/v1/translate`의 `text`. 인식에 쓴 locale → `source_locale` |
| TextToSpeech | 번역문, 확인 질문, 주행 안내를 읽음 | `translated_text`는 `target_locale`로, `clarification_question`은 `source_locale`로 읽음 |
| 차량 정보 | 기어(`GEAR_SELECTION`), 속도(`PERF_VEHICLE_SPEED`), 방향지시등 | `StopLock.update()`(정차 잠금), 스냅샷의 `gear`·`speed`·`turn_signal` |
| NaviHelper | 회전 안내(TBT), 현재 도로 제한속도, 주의 지점, 목적지 설정 | 스냅샷의 `tbt`·`current_speed_limit`·`school_zone`, 목적지 후보 좌표로 경로 설정 |
| FusedLocation | `addProximityAlert`로 스쿨존 진입·이탈 | 스냅샷의 `school_zone`(NaviHelper 주의 지점이 안 올 때) |

## 1. 음성(STT·TTS)

- STT와 TTS 모두 **ON_DEVICE**로 초기화합니다. STT는 모드를 비우면 HYBRID로 동작해서 앱 등록과 서버 연결이 필요해집니다.
- 두 SDK가 지원하는 locale은 `ko-KR`, `en-US`, `fr-CA`, `es-US`, `es-MX`입니다. 일본어·중국어는 없습니다.
- 언어마다 쓰는 범위가 다릅니다.

| locale | 통역 API | 주행 안내 문구 |
|---|---|---|
| `ko-KR` | 지원(한쪽은 항상 한국어) | 개발용 |
| `en-US` | 지원 | 지원 |
| `es-US`, `es-MX` | 지원 | 지원(같은 문구) |
| `fr-CA` | **지원 안 함**(보내면 422 `invalid_input`) | 지원 |

- 프랑스어 사용자는 주행 안내만 프랑스어로 듣습니다. 앱은 프랑스어 사용자에게 통역 버튼을 숨기거나, 영어로 통역할지 고르게 합니다.

| 통역 방향 | STT locale | 요청 `source_locale` → `target_locale` | TTS locale |
|---|---|---|---|
| 영어 → 한국어 | `en-US` | `en-US` → `ko-KR` | `ko-KR` |
| 한국어 → 영어 | `ko-KR` | `ko-KR` → `en-US` | `en-US` |
| 스페인어 → 한국어 | `es-US` 또는 `es-MX` | `es-US`/`es-MX` → `ko-KR` | `ko-KR` |
| 한국어 → 스페인어 | `ko-KR` | `ko-KR` → `es-US`/`es-MX` | 같은 `es-US`/`es-MX` |

- `es-US`와 `es-MX`는 같은 스페인어로 처리합니다. 온보딩에서 고른 값을 그대로 보내면 됩니다. 번역문은 중남미식 존댓말(usted)이고 원화는 `30,000 wones`, 시각은 `3 p. m.`처럼 씁니다.
- TTS는 `speak()` 전에 `setLocale()`을 부르지 않으면 이전 언어로 읽습니다. 번역문을 읽을 때와 확인 질문을 읽을 때 언어가 다르니 매번 설정합니다.
- `status`가 `clarification_required`이면 번역문이 없습니다. 확인 질문만 말한 사람 언어로 읽습니다. 고정 문구(다른 언어로 말했을 때, 알아듣지 못했을 때)도 한국어·영어·스페인어로 준비돼 있습니다.
- STT 결과는 최종 결과(final)만 보냅니다. 중간 결과를 보내면 같은 turn에서 요청이 여러 번 나갑니다.

## 2. 정차 잠금(차량 정보)

통역은 정차 중에만 씁니다. `guidance.py`의 `StopLock`이 판단하고, 앱은 기어나 속도 값이 들어올 때마다 `update()`를 부릅니다.

- 기어 `P`(status valid)이고, 속도 절댓값이 0.1m/s 이하로 1초 이상 이어질 때만 통역 버튼을 켭니다.
- 속도는 m/s로 넘깁니다(Android 차량 속성 기준). 값마다 `value`, `status`, `ts_ms`를 같이 넣습니다.
- STT 시작, 요청 전송, 번역 도착, TTS 직전에 다시 확인합니다. 그사이 P가 풀리면 백엔드가 `/internal/v1/cancel`을 호출하고 도착한 번역은 읽지 않습니다.
- 에뮬레이터에서 속도가 안 읽히면 NaviHelper `onDrivingInfo`를 대신 검토합니다. 여기에 기어 값까지 들어오는지는 문서로 확인되지 않았습니다. 기어를 모르면 통역 버튼은 켜지 않습니다. 단위와 유효성 표시는 같은 기준으로 맞춥니다.
- 차량 좌표, 속도, 기어 값은 통역 API로 보내지 않습니다.

## 3. 주행 안내(NaviHelper·FusedLocation·방향지시등)

주행 안내는 모델을 쓰지 않습니다. 검수한 규칙과 문구(`packs/rules.json`)로 앱 안에서 바로 판단합니다. 안내 문구는 한국어·영어·프랑스어·스페인어가 있습니다.

- 스쿨존: NaviHelper `onCautionPointInfo`에 스쿨존이 오면 그 값을 쓰고, 안 오면 공공데이터 좌표로 만든 구역을 FusedLocation `addProximityAlert`로 받습니다.
- 제한속도: `onCurrentRoadSpeedLimit`는 지금 달리는 도로의 값입니다. 앞에 있는 스쿨존의 제한값으로 쓰지 않습니다. 앞 구역 값을 모르면 "표지판을 확인하세요"라고 안내합니다.
- 우회전: 우측 방향지시등과 TBT 우회전(10~150m)이 같이 있을 때만 안내합니다. 비상등은 제외합니다. 신호 색은 단정하지 않습니다.
- 오래된 값(속도 1.5초, 내비 5초)과 `status`가 `valid`가 아닌 값은 쓰지 않습니다.
- 온보딩 정보(언어, 평소 운전 국가, 한국 운전 경험)를 `DriverProfile`로 넘기면 출발 안내와 설명의 양이 바뀝니다. 안전 경고는 누구에게나 나갑니다.

규칙을 Kotlin으로 옮긴 뒤에는 `packs/fixtures/guidance_scenarios.json`의 시나리오로 결과가 같은지 확인합니다.

## 4. 목적지(NaviHelper)

1. STT로 받은 문장을 `/internal/v1/parse-destination`에 보냅니다(`source_locale`은 STT locale).
2. 응답의 `candidates`는 데모 장소 목록(`packs/poi.demo.json`)에서만 고릅니다. 모델이 좌표를 만들지 않습니다.
3. 화면에는 사용자 언어 이름을 보여 줍니다(`name_en`, `name_es`, `name_ko`).
4. `needs_confirmation`이 `true`면 사용자에게 확인을 받은 뒤, 고른 후보의 `lat`·`lon`으로 NaviHelper 목적지를 설정합니다.
5. `clarification_required`면 `clarification_question`을 사용자 언어로 읽고 다시 말하게 합니다.

데모 좌표는 아직 근사값(`verified: false`)이라 지금은 항상 확인을 받습니다.

## 5. 에뮬레이터에서 확인할 것

- [ ] STT·TTS ON_DEVICE가 `ko-KR`, `en-US`, `es-US`(또는 `es-MX`)에서 실제로 동작하는지
- [ ] 기어·속도·방향지시등 값이 읽히는지, 안 되면 `onDrivingInfo`로 속도(와 기어)를 대신할 수 있는지
- [ ] `onCautionPointInfo`에 스쿨존이 오는지, 안 오면 `addProximityAlert` 경로. 근접 알림은 반경 기준이라 지나가는 길에 있는 구역인지는 TBT·진행 방향으로 따로 확인
- [ ] TBT 거리·방향 값의 형식
- [ ] 후보 좌표로 NaviHelper 목적지 설정
- [ ] 모델 서버와 에뮬레이터를 같이 30분 돌렸을 때 메모리와 지연

참고: [STT](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/SpeechToText/intro) · [STT 언어](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/SpeechToText/set-locale) · [TTS](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/TextToSpeech/intro) · [TTS 언어](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/TextToSpeech/set-locale) · [NaviHelper 이벤트](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/NaviHelper/event-listeners) · [Android 차량 속성](https://developer.android.com/reference/android/car/VehiclePropertyIds)
