# 모델 프로필과 토큰 예산

## 최종 설정

**Qwen3-8B Q4_K_M(`8b-q4`) + `translate-v11` + 스페인어 질문 복원 규칙**으로 고정함. LoRA 어댑터는 사용하지 않음. `735a577`의 v11 복원·규칙 확장·프롬프트 기준 고정 테스트를 기준으로 함.

스페인어 복원은 문장부호 없는 입력에만 적용함. 기존 시작 표현과 `cuándo`, `quién`, `quiénes`를 포함하며, 문장부호가 있으면 원문 유지함. `hay`, `ya`로 시작하는 평서문을 질문으로 바꿀 가능성은 남음. 상황상 질문이지만 규칙에 맞지 않는 입력도 복원되지 않을 수 있음.

`MODEL_PROFILE`은 다운로드·실행·서버 준비 확인·평가·선택 manifest 해시에 함께 적용함. 환경 변수가 `.env`보다 우선하며 기본값은 `8b-q4`. 프로필 변경 시 AI와 모델 서버 모두 재시작 필요함. 다른 프로필 서버에 평가 요청을 보내면 시작 단계에서 차단함. `run.json`, `summary.md`, 준비 응답의 `model_profile`에서 실제 선택 확인 가능함. 기본 manifest 복사·덮어쓰기 불필요함.

| 프로필 | 고정 모델 | 파일 바이트 | 비교 의미 |
|---|---|---:|---|
| `4b-q4` | Qwen3-4B-Instruct-2507 Q4_K_M | 2,497,281,120 | 4B 대안 |
| `4b-q8` | 같은 4B의 Q8_0 | 4,280,405,600 | 양자화 차이 |
| `8b-q4` | Qwen3-8B Q4_K_M | 5,027,783,488 | 선택된 기본 모델 |

8B와 4B Instruct-2507은 학습 버전·thinking 지원도 달라 순수한 파라미터 수 실험으로 해석할 수 없음. DLPC는 VRAM 24GB. `GPU_LAYERS`를 지정하지 않고 manifest 기본값 99로 전체 GPU 적재를 요청함. 가중치 외 KV 캐시·계산 버퍼가 필요하므로 실제 적재·메모리·지연은 로그와 측정값 확인 필요함. 세 프로필은 context 4096, slot 1, batch 256, ubatch 128과 같은 코드·자료·샘플링·요청 예산 사용함. 두 단계 출력·문체·현장 용어·다듬기 실험 옵션은 효과 부족 또는 안전 지표 악화로 제거함.

Q8은 [고정 Unsloth 파일](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/blob/a06e946bb6b655725eafa393f4a9745d460374c9/Qwen3-4B-Instruct-2507-Q8_0.gguf)의 SHA256 `391c1e410fd9f4cf2de2b510273b56a84c19ce18f4fa3bfb3774031dac4ef068`, 8B는 [고정 공식 Qwen 파일](https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/7c41481f57cb95916b40956ab2f0b139b296d974/Qwen3-8B-Q4_K_M.gguf)의 SHA256 `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785`를 manifest에 기록함. 다운로드 뒤 실제 파일 해시 대조함. Qwen3-8B 원본 모델 라이선스는 [Apache-2.0](https://huggingface.co/Qwen/Qwen3-8B/blob/b968826d9c46dd6066d109eabc6255188de91218/LICENSE).

8B 요청에는 `chat_template_kwargs={"enable_thinking":false}`를 사용함. `smoke.py`는 실제 llama-server에 짧은 JSON 번역을 요청함. think 태그나 비어 있지 않은 reasoning 필드가 있으면 실패함. 해당 내용을 제거해 통과시키지 않음. [고정 llama.cpp b12076 API](https://github.com/ggml-org/llama.cpp/blob/b12076/tools/server/README.md#post-v1chatcompletions-openai-compatible-chat-completions) 기준임. 로컬 HTTP 모의 시험만으로 실제 모델 응답을 확인한 것은 아님.

## 기존 비교 결과 — AI 검토(참고)

아래 수치는 사용자 제공 DLPC `34bfd43` 비교와 Claude 블라인드 검토 결과임. 현재 최종 v11 설정의 잠근 시험 성적은 아님. 이번 문서 정리에서 원시 출력 재검토는 하지 않음.

| 모델 | 좋음 | 어색함 | 나쁨 | 뜻 오류 |
|---|---:|---:|---:|---:|
| 4B | 64% | 30% | 6% | 16/268 |
| 8B | 71% | 27% | 2% | 5/269 |

같은 보고의 8B 목적지 자동 통과는 20/20, general p95는 약 511ms. 표본 수가 달라 위 전체 건수만으로 같은 행의 개선량을 계산하면 안 됨. 최종 자동 통과율·지연은 [guide](guide.md)의 같은 서버 평가 후 기록 필요함.

## LoRA 실험 종료

아래는 별도 LoRA 실험의 사용자 제공 요약임. 앞선 `34bfd43`의 5/269와 표본·실험이 다르므로 합산하지 않음. 자연스러움·뜻 오류·학습 표본 판정은 **AI 검토(참고)**.

| 선생 모델 | 보고된 결과 | 결정 |
|---|---|---|
| 14B | 같은 행 뜻 오류 7→7 | 뜻 오류 개선 없음, 미채택 |
| 32B | 자연스러움 개선, 같은 행 뜻 오류 7→6. 학습 표본 나쁨 6.7%, 스페인어 9% | 학습 자료 품질 기준 미달, 미채택 |
| 8B 자기 선생 | 학습 표본 나쁨 8.0% | 학습 자료 품질 기준 미달, 미채택 |

세 실험 모두 채택 기준 미달로 LoRA 종료함. 도구 코드는 재현용으로 남기되 추가 생성·학습은 진행하지 않음. 최종 서버는 어댑터 없는 8B Q4 사용함. LoRA 학습 프롬프트 해시 보존을 위한 주석 정리 제약은 해제함. 모델 파일 무결성·런타임 확인은 유지함.

## 토큰 예산 재현

최종 v11 로컬 검사에서 프로필별 759개 요청·재시도 사례 통과함. 출력 예약 포함 최대 토큰은 4B Q4·Q8 각각 3,209/4,096, 8B Q4 3,213/4,096. 잠근 시험은 토큰 예산 자료로 사용하지 않음. 모든 가능한 입력의 상한을 증명하는 수치는 아님.

HF tokenizer의 기본 chat template과 GGUF 내장 template은 다를 수 있음. 고정 tokenizer 파일과 실제 GGUF template을 함께 사용해야 함. 기존 로컬 검사에서는 세 GGUF의 첫 8MiB만 HTTP 206 Range로 읽어 template metadata를 추출함. 전체 가중치 다운로드·GPU 추론은 하지 않음.

- 4B Q4·Q8 template SHA256: `c979e0e71a3e21b8f208e6ab120d5cb29327885f29d2a8b18fda67a723798e18`
- 8B Q4 template SHA256: `57f1fd00f0013a2be96aa79b857391f27e23df5b5f847072b524c897e24d0361`

DLPC에서 선택 모델 실행 중이면 `/props`의 실제 template과 manifest의 고정 tokenizer를 아래처럼 저장함. `.venv-train`의 Jinja/tokenizers 환경 또는 같은 의존성이 있는 별도 검사 환경 필요함.

```bash
.venv/bin/python - <<'PY'
import json
import urllib.request
from pathlib import Path
from huggingface_hub import hf_hub_download
from config import load_manifest, model_profile
m = load_manifest()
dest = Path('run/tokenizers') / model_profile()
dest.mkdir(parents=True, exist_ok=True)
for filename in ('tokenizer.json', 'tokenizer_config.json'):
    hf_hub_download(m['model']['base_repo'], filename, revision=m['model']['base_revision'], local_dir=dest)
with urllib.request.urlopen(f"http://127.0.0.1:{m['runtime']['port']}/props") as response:
    template = json.load(response)['chat_template']
(dest / 'gguf-chat-template.jinja').write_text(template, encoding='utf-8')
print(dest)
PY
.venv-train/bin/python scripts/check_prompt_budget.py \
  --tokenizer-dir "run/tokenizers/$MODEL_PROFILE" \
  --chat-template "run/tokenizers/$MODEL_PROFILE/gguf-chat-template.jinja"
cp run/prompt_budget.json "run/prompt_budget.$MODEL_PROFILE.json"
```

## 판정 범위

`eval/compare.py` CSV는 같은 ID의 번역 전체를 포함함. 한국어 출력 전부와 실패·확인 질문도 검토 대상임. AI 검토는 참고 자료이며 사람·스페인어 원어민 검수 필요함. 자동 통과율과 번역투 표지 수는 의미 정확도 보증이 아님.

번역투 집계는 HTTP 200·목표 한국어·status ok인 최종 출력에 한정함. 7그룹마다 출력당 한 번 세고, 표지가 하나 이상 있는 출력 비율을 별도로 표시함. 대명사, 명사화, 문어체 연결 표현, 반말 의문, 로마자, 같은 문장 안 종결형 혼용은 검토 신호임. 이름·인용·정당한 대명사도 집계될 수 있음.

대명사 후처리는 영어·스페인어→한국어에서만 적용함. 인용문과 동사·합성어로 볼 수 있는 형태는 보존함. `나는/우리는`은 절 시작만, 소유 `내`는 명확한 동작 문맥을 제외하고, 소유 `우리`는 띄어 쓴 형태만 변경함. 모든 대명사 정규화를 보장하지 않음. 변경된 출력도 숫자·부정·언어 등 기존 검사를 다시 통과해야 함. `당신`은 삭제하지 않음.

개발 평가 문장을 프롬프트·예시·후처리·학습 자료로 복사하지 않음. 잠근 시험은 설정 선택·튜닝에 사용하지 않으며, 원문·reference·기대값 독립 검토 후 최종 설정으로 한 번 실행해야 함. 실행 전이므로 최종 잠근 시험 성적은 미확정임. [제출 요약](submission_summary.md)에 최종 실행 후 결과 기입 필요함.
