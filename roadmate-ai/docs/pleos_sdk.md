# Pleos SDK와 AI 파트

우리 앱은 Pleos Connect SDK 다섯 가지(STT, TTS, 차량 정보, NaviHelper, FusedLocation)를 연결할 계획입니다. 공모전은 SDK 개수보다 핵심 기능과 사용 경험에 맞는 활용을 평가합니다. 이 문서는 앱 담당의 Kotlin 어댑터와 AI 파트 사이의 구현 계약입니다. Python 규칙과 API가 있어도 실제 SDK 연결이 완료된 것은 아닙니다. [공식 심사 기준](https://appdevchallenge.auto-hmg.io/guide/judging-awards)

이름과 동작은 Pleos 공식 문서(2026년 11월 8일 확인) 기준입니다. 10월 19일 릴리스 노트는 Vehicle 2.3.1, NaviHelper 3.0.5, STT·TTS 3.3.1, FusedLocation 1.1.2를 나열하지만, Vehicle 개요는 SDK 2.3.2·Service 2.3.21 기준입니다. 실제 설치할 SDK·서비스 버전을 기록하고 해당 API 시그니처와 권한을 확인해야 합니다. 저장소에는 Kotlin SDK 어댑터 구현이 없으며 에뮬레이터 연동도 확인하지 않았습니다. 초기화 성공·권한·지원 여부를 확인한 다음 이벤트를 전달합니다. [SDK 릴리스 노트](https://document.pleos.ai/en/docs/pleos-only/release-note/sdk-release-note) · [Vehicle 개요](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/Vehicle/intro)

## 한눈에 보기

| SDK | 앱에서 하는 일 | AI 파트와 맞닿는 곳 |
|---|---|---|
| SpeechToText | 정차 중 통역 버튼을 누르면 말을 받아 적음 | 최종 인식 문장 → `/internal/v1/translate`의 `text`. 인식에 쓴 locale → `source_locale` |
| TextToSpeech | 번역문, 확인 질문, 주행 안내를 읽음 | `translated_text`는 `target_locale`로, `clarification_question`은 `source_locale`로 읽음 |
| 차량 정보 | 방향지시등 연결 검증. 기어·속도 관측 API는 미확인 | `StopLock.update()`와 스냅샷의 `gear`·`speed`·`turn_signal`; 관측 경로 확보 전에는 의존 기능 차단 |
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
- STT는 `request()` 전에 `setLocale(source_locale)`을 설정합니다. TTS는 매 `speak()` 전에 읽을 문장의 locale을 설정합니다. TTS locale을 생략하면 이전 설정, 이전 설정도 없으면 시스템 언어를 사용합니다. 번역 후 주행 안내를 읽을 때도 온보딩 locale로 다시 설정합니다. [TTS 언어 설정](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/TextToSpeech/set-locale)
- `status`가 `clarification_required`이면 번역문이 없습니다. 확인 질문만 말한 사람 언어로 읽습니다. 고정 문구(다른 언어로 말했을 때, 알아듣지 못했을 때)도 한국어·영어·스페인어로 준비돼 있습니다.
- STT `ResultListener.onUpdated(stt, completed)`에서 `completed == true`인 비어 있지 않은 문장만 turn당 한 번 보냅니다. 중간 결과는 화면 표시용이고, `onEndedRecognition()`만으로 번역 요청을 만들지 않습니다. 인식 오류는 빈 문장 번역 대신 재시도 상태로 처리합니다. [STT 콜백](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/SpeechToText/result-listeners)
- 앱의 음성 큐가 STT 녹음과 TTS 재생을 조정합니다. 자기 음성을 다시 인식하지 않도록 번역 TTS가 끝난 뒤 다음 STT를 시작합니다. 정차 조건 이탈·언어 변경·turn 취소 때 이전 번역의 대기 음성도 버립니다. 이는 앱 구현 계약이며 SDK가 자동으로 보장하는 동작이 아닙니다.
- 주행 안내의 `GuidanceEngine.mark_played()`는 해당 발화의 TTS `onDone()`에서 한 번만 호출합니다. `speak()` 호출, `onStart()`, `onStop()`, `onError()`에서는 재생 횟수·쿨다운을 기록하지 않습니다. 콜백에는 현재 재생 중인 발화와 `clock_epoch`를 연결하고 취소된 발화나 이전 시계 구간의 늦은 콜백은 버립니다. [TTS 완료·중단 콜백](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/TextToSpeech/event-listeners)

## 2. 정차 잠금(차량 정보)

통역은 정차 중에만 씁니다. `guidance.py`의 `StopLock`이 판단하고, 앱은 기어나 속도 값이 들어올 때마다 `update()`를 부릅니다.

**기어·속도 관측 경로 확보가 구현 선행 조건입니다.** `GEAR_SELECTION`·`PERF_VEHICLE_SPEED`는 Android 속성 이름이며 Pleos 앱에 노출된다는 근거가 아닙니다. 10월 19일 릴리스 노트에는 `VehicleSpeedListener`와 `onSpeedUpdated`가 제거됐다고 적혀 있습니다. Vehicle 2.3.2의 `Brake`는 브레이크 페달·주차 브레이크 상태, `DrivingMode`는 주행 모드·크루즈 정보이므로 기어 `P`나 차속의 대체값으로 쓰지 않습니다. [제거된 속도 리스너](https://document.pleos.ai/en/docs/pleos-only/release-note/sdk-release-note) · [Vehicle 도메인 설명](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/Vehicle/intro)

| 기능 | 확인해야 할 입력 | 입력 미확인·미지원 시 |
|---|---|---|
| 정차 통역 | 실제 기어 `P`와 유효한 속도 관측 API·권한·단위 | 통역 버튼 비활성화. 주차 브레이크나 수동 체크로 우회하지 않음 |
| 스쿨존 과속 경고 | 실제 속도와 해당 도로·구역의 제한값 | 속도 기반 경고 비활성화. 검증된 구역 정보가 있을 때 고정 진입 안내만 가능 |
| 우회전 안내 | 실제 방향지시등과 경로상 TBT 방향·거리 | 필요한 값이 없으면 안내하지 않음 |
| 스쿨존·비보호 좌회전 안내 | 검증된 구역·교차로와 경로의 대응 | 해당 동적 안내 비활성화. 임의 ID나 좌회전 TBT만으로 활성화하지 않음 |

- `rules-v5` 기준으로 기어 `P`와 속도가 모두 유효하고, 속도 절댓값이 0.1m/s 이하인 **실제 관측 구간**이 1초 이상일 때만 통역 버튼을 켭니다. 같은 속도 표본을 반복 조회하거나 `now_ms`만 늘려서 정차 시간을 채우지 않습니다.
- `gear`는 5초, `speed`는 1.5초 안의 관측값이어야 하며 값마다 `value`, `status`, `ts_ms`를 넣습니다. 속도에는 `unit`도 반드시 넣으며 `m/s` 또는 `km/h`만 허용합니다. 누락·다른 단위·NaN·무한대·문자열 값은 사용하지 않습니다. 어댑터가 원본 단위를 확인한 뒤 넘겨야 하며 `km/h`는 엔진이 m/s로 환산합니다.
- `ts_ms`와 `now_ms`는 같은 단조 시계의 음이 아닌 정수 밀리초입니다. `ts_ms`는 표본의 실제 관측 시각으로 유지하고, 캐시를 읽을 때 새 시각으로 덮어쓰지 않습니다. 관측 실패·미지원은 `valid`로 꾸미지 않습니다.
- STT 시작, 요청 전송, 번역 도착, TTS 직전에 다시 확인합니다. 그사이 P가 풀리면 백엔드가 `/internal/v1/cancel`을 호출하고 도착한 번역은 읽지 않습니다.
- NaviHelper `onDrivingInfo`는 후보일 뿐입니다. 실제 전달 필드·단위·주기·유효성은 확인되지 않았으므로, 콜백 이름만으로 속도나 기어를 얻는다고 가정하지 않습니다. 필요한 값이 확인되지 않으면 위 표의 차단을 유지합니다.
- 차량 좌표, 속도, 기어 값은 통역 API로 보내지 않습니다.

## 3. 주행 안내(NaviHelper·FusedLocation·방향지시등)

주행 안내는 모델을 쓰지 않습니다. 고정 규칙과 문구(`packs/rules.json`)로 앱 안에서 바로 판단합니다. 안내 문구는 한국어·영어·프랑스어·스페인어가 있으며, 현재 draft 상태이므로 출시 전 언어·법규 검수를 완료해야 합니다.

- 스쿨존: NaviHelper `onCautionPointInfo`의 실제 데이터 형식을 확인해 스쿨존을 매핑합니다. 해당 이벤트가 없으면 공공데이터 좌표를 FusedLocation `addProximityAlert`에 연결하는 방식을 검증합니다. 반경 진입은 법정 구역 진입과 같지 않으므로 검증한 구역·도로 대응 없이 `state=inside`로 단정하지 않습니다. 앞 구역 안내는 경로·진행 방향을 확인해 `on_route=true`인 경우에만 사용합니다.
- 제한속도: `onCurrentRoadSpeedLimit`는 지금 달리는 도로의 값입니다. 앞에 있는 스쿨존의 제한값으로 쓰지 않습니다. 앞 구역 값을 모르면 "표지판을 확인하세요"라고 안내합니다.
- 우회전: 우측 방향지시등과 TBT 우회전(10~150m)이 같이 있을 때만 안내합니다. 비상등은 제외합니다. 신호 색은 단정하지 않습니다.
- 비보호 좌회전: 스냅샷 `unprotected_left`에 `value=true`, `on_route=true`, 검증된 `intersection_id`, `status`, `ts_ms`를 넣습니다. 같은 교차로 ID의 좌회전 TBT가 있어야 규칙이 동작합니다. 좌회전 TBT만 보고 비보호라고 추정하지 않습니다. 이 표지·교차로 식별 정보가 SDK에서 제공된다는 보장은 확인되지 않았으므로 검증한 지도 자료가 없으면 동적 안내를 끄고 출발 안내만 씁니다. 현재 신호 색이나 회전 가능 여부를 관측했다고 표현하지 않습니다.
- `zone_id`와 `intersection_id`는 앱 계약의 필드이며 SDK의 `TBTInfo`·`CautionPoint`에 동일 필드가 있다고 확인한 것이 아닙니다. 앱이 검증된 지도 자료의 제공자·도로·구역/교차로를 구별하는 안정적인 ID로 매핑합니다. 같은 교차로에는 같은 ID를, 서로 다른 구역에는 다른 ID를 사용하고 매핑을 검증할 수 없으면 신호를 만들지 않습니다.
- 오래된 값(속도 1.5초, 기어·내비 5초, 방향지시등 3초)과 `status`가 `valid`가 아닌 값은 쓰지 않습니다. 과속 지속시간도 실제 `speed.ts_ms` 간격으로 계산합니다.
- 구역이 있는 경고의 쿨다운은 `(rule_id, zone_id)`별로 계산해 다른 스쿨존의 경고를 막지 않습니다. `day_key`는 앱이 `Asia/Seoul` 날짜(`YYYY-MM-DD`)로 만듭니다. 단조 시계가 역행하면 `rules-v5` 엔진은 시간 상태를 초기화하고 `clock_epoch`를 올려 이전 큐 항목과 늦은 완료 콜백을 무효화합니다. 앱도 이전 표본·재생 대기 항목을 버리고 새 관측을 기다립니다.
- 언어는 `GuidanceEngine(locale=...)`로, 평소 운전 국가와 경험은 `DriverProfile(country=..., experience=...)`로 넘깁니다. 언어에서 국적·통행 방향을 추정하지 않으며 `OTHER`의 통행 방향은 미정입니다. 안전 경고는 누구에게나 나갑니다.

NaviHelper 이벤트 전달 여부는 내비 앱 구현에 따라 달라집니다. FusedLocation은 `isSupported()`와 `pleos.car.permission.FUSED_LOCATION`을 확인하고, 위치 `null`·오류·오래된 값을 누락 상태로 처리합니다. 관측값을 못 받는 기능은 비활성화하고, fixture 시연을 실제 차량 관측으로 표시하지 않습니다. [NaviHelper 이벤트](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/NaviHelper/event-listeners) · [FusedLocation 지원·권한](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/FusedLocation/intro)

규칙을 Kotlin으로 옮긴 뒤에는 `packs/fixtures/guidance_scenarios.json`의 시나리오로 결과가 같은지 확인합니다.

## 4. 목적지(NaviHelper)

1. STT 최종 문장을 `/internal/v1/parse-destination`에 보냅니다(`source_locale`은 STT locale). 지원 언어는 한국어·영어·스페인어이며 프랑스어 STT 결과는 이 API에도 보내지 않습니다.
2. 응답의 `candidates`는 데모 장소 목록(`packs/poi.demo.json`)에서만 고릅니다. 모델이 좌표를 만들지 않습니다.
3. 화면에는 사용자 언어 이름을 보여 줍니다(`name_en`, `name_es`, `name_ko`).
4. `needs_confirmation`이 `true`면 사용자에게 확인을 받은 뒤, 고른 후보의 `lat`·`lon`으로 NaviHelper 목적지를 설정합니다.
5. `clarification_required`면 `clarification_question`을 사용자 언어로 읽고 다시 말하게 합니다.

데모 좌표는 아직 근사값(`verified: false`)이라 지금은 항상 확인을 받습니다. 이름 확인만으로 좌표가 검증되지는 않습니다. 실제 경로 시연 전에 지도상 위치·진입점을 대조하고 검증한 좌표만 사용합니다.

NaviHelper 3.0.5 릴리스 노트는 `RouteInfo` 등의 생성자 좌표 순서를 `latitude, longitude`로 명시합니다. 그러나 목적지 문서의 Kotlin 예제는 `latitude=37.48544722`, `longitude=127.03636666`인 반면 Java 예제는 경도 `127.03636666`을 첫 인자로 넣어 서로 불일치합니다. Kotlin에서는 응답 `lat`·`lon`을 `RouteInfo(latitude = candidate.lat, longitude = candidate.lon, ...)`처럼 이름 붙은 인자로 전달하고 나머지 필드도 설치 SDK 시그니처에 맞춥니다. Java 위치 인자를 그대로 복사하지 않습니다. 알려진 테스트 목적지를 설정한 뒤 `onDestinationInfo` 좌표와 지도 위치를 대조하고, 경로 완료·오류 콜백까지 확인해야 연동 검증이 끝납니다. [좌표 변경](https://document.pleos.ai/en/docs/pleos-only/release-note/sdk-release-note) · [공식 목적지 예제](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/NaviHelper/destinations)

## 5. 에뮬레이터에서 확인할 것

- [ ] Pleos Connect AVD·Virtual Console·마이크 접근·cold boot 설정, 각 SDK 초기화·권한·해제 처리
- [ ] STT·TTS ON_DEVICE가 `ko-KR`, `en-US`, `es-US`(또는 `es-MX`)에서 실제로 동작하는지; 안내용 `fr-CA` TTS와 언어 전환도 확인
- [ ] 중간 STT 결과가 API로 가지 않고, 최종 결과 중복·취소·P 해제 시 늦게 온 결과가 재생되지 않는지
- [ ] 기어·속도의 실제 접근 API·권한·단위·갱신 주기를 먼저 확인. `onDrivingInfo`는 필드 확인 전까지 대안으로 확정하지 않으며 미확인 상태에서는 통역·속도 기반 경고 차단
- [ ] 같은 표본 재조회로 정차·과속 지속시간이 늘지 않는지, 5초 지난 기어·단위 누락·시계 역행을 차단하는지
- [ ] TTS `onDone()`만 재생 완료로 기록하고 `onStop()`·`onError()`·취소/이전 `clock_epoch`의 콜백은 세지 않는지
- [ ] `onCautionPointInfo`에 스쿨존이 오는지, 안 오면 `addProximityAlert` 경로. 근접 알림은 반경 기준이라 지나가는 길에 있는 구역인지는 TBT·진행 방향으로 따로 확인
- [ ] TBT 거리·방향의 실제 형식과 앱의 안정적인 `zone_id`·`intersection_id` 매핑
- [ ] 비보호 표지와 TBT의 동일 교차로 매칭을 검증할 자료가 있는지; 없으면 동적 안내가 꺼지는지
- [ ] Kotlin named arguments로 목적지 좌표 전달 후 `onDestinationInfo`·지도 위치·경로 결과 확인
- [ ] 모델 서버와 에뮬레이터를 같이 30분 돌렸을 때 메모리와 지연

1차는 기획서가 필수이고 영상·저장소·APK는 선택 가산점 자료입니다. 2차 PT 전에는 실제 동작 가능한 앱이 필요하므로 이 문서와 fixture 통과만으로 SDK 연동 완료라고 제출하지 않습니다. [공식 제출 안내](https://appdevchallenge.auto-hmg.io/guide/submission)

참고: [STT](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/SpeechToText/intro) · [STT 언어](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/SpeechToText/set-locale) · [TTS](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/TextToSpeech/intro) · [TTS 언어](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/TextToSpeech/set-locale) · [NaviHelper 이벤트](https://document.pleos.ai/en/docs/pleos-only/connect-sdk-pleos/NaviHelper/event-listeners) · [Android 차량 속성](https://developer.android.com/reference/android/car/VehiclePropertyIds)
