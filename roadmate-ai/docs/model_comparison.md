# 모델 프로필과 토큰 예산

`MODEL_PROFILE`은 다운로드·실행·서버 준비 확인·평가·선택 manifest 해시에 함께 적용한다. 환경 변수 값이 `.env`보다 우선하며 기본값은 `8b-q4`다. 실행 중 프로필을 바꾸면 AI와 모델 서버를 모두 종료한 뒤 재시작한다. 다른 프로필을 띄운 서버에 평가 요청을 보내면 시작 단계에서 차단한다. 결과의 `run.json`과 `summary.md`, 준비 응답의 `model_profile`에서 실제 선택을 확인한다. 기본 manifest를 복사하거나 덮어쓸 필요가 없다.

| 프로필 | 고정 모델 | 파일 바이트 | 비교 의미 |
|---|---|---:|---|
| `4b-q4` | Qwen3-4B-Instruct-2507 Q4_K_M | 2,497,281,120 | 4B 대안 |
| `4b-q8` | 같은 4B의 Q8_0 | 4,280,405,600 | 양자화 차이 |
| `8b-q4` | Qwen3-8B Q4_K_M | 5,027,783,488 | 선택된 기본 모델 |

8B와 4B Instruct-2507은 학습 버전·thinking 지원도 달라 순수한 파라미터 수 실험으로 해석하지 않는다. 현재 DLPC는 VRAM 24GB이며 GPU_LAYERS를 지정하지 않고 manifest 기본값 99로 전체 GPU 적재를 요청한다. 가중치 외에 KV 캐시·계산 버퍼가 추가되므로 실제 GPU 적재·메모리·지연은 실행 로그와 측정값으로 확인한다. 세 프로필은 context 4096, slot 1, batch 256, ubatch 128과 같은 코드·자료·샘플링·요청 예산을 쓴다. 실패한 실험 옵션은 제거했다.

Q8은 [고정 Unsloth 파일](https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/blob/a06e946bb6b655725eafa393f4a9745d460374c9/Qwen3-4B-Instruct-2507-Q8_0.gguf)의 SHA256 `391c1e410fd9f4cf2de2b510273b56a84c19ce18f4fa3bfb3774031dac4ef068`, 8B는 [고정 공식 Qwen 파일](https://huggingface.co/Qwen/Qwen3-8B-GGUF/blob/7c41481f57cb95916b40956ab2f0b139b296d974/Qwen3-8B-Q4_K_M.gguf)의 SHA256 `d98cdcbd03e17ce47681435b5150e34c1417f50b5c0019dd560e4882c5745785`를 manifest에 기록했다. 다운로드 뒤 실제 파일 해시를 대조한다.

8B 요청에는 `chat_template_kwargs={"enable_thinking":false}`를 넣는다. `smoke.py`는 실제 llama-server에 짧은 JSON 번역 요청을 보내며, 응답에 think 태그나 비어 있지 않은 reasoning 필드가 있으면 실패한다. 이를 제거해서 성공으로 만들지 않는다. 이 옵션은 [고정 llama.cpp b12076 API](https://github.com/ggml-org/llama.cpp/blob/b12076/tools/server/README.md#post-v1chatcompletions-openai-compatible-chat-completions)에 따른다. 로컬 HTTP 모의 시험 통과는 실제 모델 응답 확인과 다르다.

## 토큰 예산 재현

HF tokenizer의 기본 chat template과 GGUF 내장 template은 다를 수 있다. 고정 tokenizer 파일과 실제 GGUF template을 함께 사용한다. 이번 로컬 검사에서는 세 GGUF의 첫 8MiB만 HTTP 206 Range로 읽어 template metadata를 추출했다. 전체 모델 가중치를 다운로드하거나 GPU 추론한 것은 아니다.

- 4B Q4·Q8 template SHA256: `c979e0e71a3e21b8f208e6ab120d5cb29327885f29d2a8b18fda67a723798e18`
- 8B Q4 template SHA256: `57f1fd00f0013a2be96aa79b857391f27e23df5b5f847072b524c897e24d0361`

DLPC에서 선택 모델이 실행 중이면 `/props`의 실제 template과 manifest의 고정 tokenizer를 다음처럼 저장한다. `.venv-train`의 Jinja/tokenizers 환경 또는 같은 의존성이 있는 별도 검사 환경을 사용한다.

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

`eval/compare.py` CSV는 달라진 출력만이 아니라 같은 ID의 번역 전체를 담는다. 한국어 출력 전부와 실패·확인 질문도 검토한다. `review.csv`의 의미·자연스러움·치명 오류를 사람이 판정하며, 스페인어는 원어민 검토가 필요하다. 자동 통과율과 번역투 표지 수를 의미 정확도 합격으로 사용하지 않는다.

번역투 집계는 HTTP 200·목표 한국어·status ok인 최종 출력에 한정한다. 7그룹마다 출력당 한 번 세고, 하나 이상의 표지가 있는 출력 비율을 별도로 표시한다. 대명사, 명사화, 문어체 연결 표현, 반말 의문, 로마자, 같은 문장 안 종결형 혼용은 검토 신호다. 이름·인용·정당한 대명사도 신호가 될 수 있다.

대명사 후처리는 영어·스페인어→한국어에서만 적용한다. 인용문과 동사·합성어로 볼 수 있는 형태는 보존한다. `나는/우리는`은 절 시작만, 소유 `내`는 명확한 동작 문맥을 제외하고, 소유 `우리`는 띄어 쓴 형태만 바꾼다. 따라서 모든 대명사를 정규화한다는 보장은 없다. 바뀐 출력도 숫자·부정·언어 등 기존 검사를 다시 통과해야 하며, `당신`은 삭제하지 않는다.

LoRA는 Qwen 선생 출력에 자동 필터와 AI 표본 검토를 적용한 뒤 학습한다([실행 안내](guide.md#8-8b-lora-증류-준비)). 개발 평가 문장을 프롬프트·예시·후처리·학습 자료로 복사하지 않고, 잠긴 최종셋은 이 비교에 사용하지 않는다. 실제 DLPC 품질·지연·VRAM과 전체 출력 사람 검수는 별도로 남아 있다.
