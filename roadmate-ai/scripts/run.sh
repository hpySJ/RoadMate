#!/usr/bin/env bash
# Linux(DLPC 등)용 설치·실행. Windows는 같은 폴더의 .ps1 스크립트를 쓴다.
#   bash scripts/run.sh setup            .venv + llama.cpp(CUDA 12.8) + 모델 다운로드 + 환경 확인
#   bash scripts/run.sh start [--mock]   llama-server(8081) → AI 서버(8000) 백그라운드 실행 → smoke 시험
#   bash scripts/run.sh stop             AI 서버 → 모델 서버 종료
#   bash scripts/run.sh model            llama-server 포그라운드 실행(디버깅)
#   bash scripts/run.sh ai [--mock]      AI 서버 포그라운드 실행(디버깅)
#   bash scripts/run.sh setup-train      학습 환경(.venv-train)
#   bash scripts/run.sh setup-convert    LoRA → GGUF 변환 환경(.venv-convert)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
BASE_PY="${PYTHON:-$(command -v python3.12 || command -v python3)}"
PY="$ROOT/.venv/bin/python"
LLAMA="$ROOT/tools/llama"

die() { echo "$*" >&2; exit 1; }

mf() {  # MODEL_PROFILE로 선택한 manifest 값 읽기: mf runtime.port
    "$BASE_PY" - "$1" <<'EOF'
import sys
from config import load_manifest
v = load_manifest()
for k in sys.argv[1].split("."):
    v = v[k] if v is not None else None
print("" if v is None else v)
EOF
}

fetch() {  # fetch URL 파일 (이미 있으면 건너뜀)
    [ -s "$2" ] && return
    echo "download $(basename "$2")"
    "$BASE_PY" -c "import sys, urllib.request; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$1" "$2.part"
    mv "$2.part" "$2"
}

new_venv() {
    [ -x "$1/bin/python" ] && return
    if ! "$BASE_PY" -m venv "$1" 2>/dev/null; then
        # Ubuntu 시스템 Python에 ensurepip가 없을 때
        rm -rf "$1"
        "$BASE_PY" -m venv --without-pip "$1"
        fetch https://bootstrap.pypa.io/get-pip.py "$1/get-pip.py"
        "$1/bin/python" "$1/get-pip.py"
    fi
    "$1/bin/python" -m pip install --upgrade pip
}

setup_llama() {
    local tag backend="${LLAMA_BACKEND:-cuda-12.8}" tmp base a
    tag="$(mf runtime.llama_cpp_tag)"
    tmp="${TMPDIR:-/tmp}/roadmate-llama-$tag"
    base="https://github.com/ggml-org/llama.cpp/releases/download/$tag"
    local assets=("llama-$tag-bin-ubuntu-$backend-x64.tar.gz")
    # QWEN 구현: CUDA 빌드는 같은 버전의 CUDA 런타임 라이브러리를 함께 둔다
    [[ $backend == cuda-* ]] && assets+=("cudart-llama-$tag-bin-ubuntu-$backend-x64.tar.gz")
    mkdir -p "$tmp"
    rm -rf "$LLAMA" && mkdir -p "$LLAMA"
    for a in "${assets[@]}"; do
        fetch "$base/$a" "$tmp/$a"
        tar -xzf "$tmp/$a" -C "$LLAMA"
    done
    fetch "https://github.com/ggml-org/llama.cpp/archive/refs/tags/$tag.tar.gz" "$tmp/src-$tag.tar.gz"
    rm -rf tools/llama-src
    tar -xzf "$tmp/src-$tag.tar.gz" -C tools
    mv "tools/llama.cpp-$tag" tools/llama-src
    (cd "$tmp" && sha256sum "${assets[@]}") > "$LLAMA/SHA256SUMS"
    echo "{\"tag\": \"$tag\", \"backend\": \"ubuntu-$backend\"}" > "$LLAMA/VERSION.json"
    llama_env
    "$SERVER" --version
}

llama_env() {
    SERVER="$(find "$LLAMA" -name llama-server -type f 2>/dev/null | head -n1)"
    [ -n "$SERVER" ] || die "tools/llama/llama-server missing: bash scripts/run.sh setup"
    LD_LIBRARY_PATH="$(find "$LLAMA" -name '*.so*' -printf '%h\n' | sort -u | paste -sd:)${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export LD_LIBRARY_PATH
}

model_args() {
    local model adapter
    model="$ROOT/models/$(mf model.gguf_file)"
    [ -f "$model" ] || die "model missing: .venv/bin/python scripts/download_model.py"
    # 파라미터 튜닝: 문맥은 manifest, 슬롯 1, 출력 상한 256, GPU 레이어(GPU_LAYERS로 덮어씀), 배치, RAM 프롬프트 캐시 끔
    ARGS=(-m "$model" --alias "$(mf model.alias)"
        -c "$(mf runtime.ctx)" -np "$(mf runtime.parallel)" -n "$(mf runtime.n_predict)"
        -ngl "${GPU_LAYERS:-$(mf runtime.n_gpu_layers)}" -b "$(mf runtime.batch)" -ub "$(mf runtime.ubatch)"
        --cache-ram "$(mf runtime.cache_ram_mib)" --jinja --host 127.0.0.1 --port "$(mf runtime.port)")
    adapter="$(mf adapter.file)"
    if [ -n "$adapter" ]; then ARGS+=(--lora "$ROOT/$adapter"); fi
}

ai_cmd() {  # $1 = --mock 또는 빈 값
    [ -x "$PY" ] || die ".venv missing: bash scripts/run.sh setup"
    if [ "${1:-}" = --mock ]; then MODEL_BACKEND=mock; else MODEL_BACKEND=llama; fi
    export MODEL_BACKEND PYTHONIOENCODING=utf-8
    # 백엔드 연동: 백엔드(8080)는 http://127.0.0.1:8000 으로 호출, reload 없이 worker 1개
    ARGS=("$PY" -m uvicorn app:app --host 127.0.0.1 --port "${AI_PORT:-8000}" --workers 1)
}

running() {  # running 이름 확인문자열 → 살아 있는 우리 프로세스면 PID 출력
    local f="run/$1.pid" pid cmd
    [ -f "$f" ] || return 1
    pid="$(cat "$f")"
    [ -r "/proc/$pid/cmdline" ] || return 1
    cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
    # 검증: PID 재사용 대비, 명령줄이 같은 프로그램일 때만 우리 프로세스로 본다
    [[ $cmd == *"$2"* ]] && echo "$pid"
}

start_bg() {  # start_bg 이름 포트 확인문자열 명령...
    local name=$1 port=$2 tag=$3 pid
    shift 3
    if pid="$(running "$name" "$tag")"; then die "$name already running (pid $pid). Run: bash scripts/run.sh stop"; fi
    if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then die "port $port is already in use by another process"; fi
    mkdir -p logs run
    nohup "$@" > "logs/$name.out.log" 2> "logs/$name.err.log" < /dev/null &
    echo $! > "run/$name.pid"
    echo "$name started (pid $!), logs: logs/$name.*.log"
}

wait_http() {  # wait_http URL 초 이름
    local end=$((SECONDS + $2))
    while ((SECONDS < end)); do
        # 검증: 새 프로세스가 죽었으면 다른 프로세스의 응답을 준비 완료로 보지 않는다
        kill -0 "$(cat "run/$3.pid")" 2>/dev/null || die "$3 exited before ready (see logs/$3.err.log)"
        if "$BASE_PY" -c "import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=3)" "$1" 2>/dev/null; then
            echo "ready: $1"
            return
        fi
        sleep 2
    done
    die "timeout waiting for $1 (see logs/)"
}

stop_all() {
    local pair name pid
    for pair in ai:uvicorn llama:llama-server; do
        name=${pair%%:*}
        [ -f "run/$name.pid" ] || continue
        if pid="$(running "$name" "${pair#*:}")"; then
            kill "$pid"
            echo "stopped $name (pid $pid)"
        else
            echo "$name not running (stale pid file removed)"
        fi
        rm -f "run/$name.pid"
    done
}

case "${1:-}" in
    setup)
        new_venv .venv
        "$PY" -m pip install -r requirements.lock.txt
        setup_llama
        "$PY" scripts/download_model.py
        "$PY" scripts/check_env.py
        ;;
    start)
        if [ "${2:-}" != --mock ]; then
            model_args
            llama_env
            start_bg llama "$(mf runtime.port)" llama-server "$SERVER" "${ARGS[@]}"
            wait_http "http://127.0.0.1:$(mf runtime.port)/health" 300 llama
        fi
        ai_cmd "${2:-}"
        start_bg ai "${AI_PORT:-8000}" uvicorn "${ARGS[@]}"
        wait_http "http://127.0.0.1:${AI_PORT:-8000}/health/live" 60 ai
        "$PY" scripts/smoke.py
        ;;
    stop) stop_all ;;
    model)
        model_args
        llama_env
        mkdir -p run
        echo "$$" > run/llama.pid
        exec "$SERVER" "${ARGS[@]}"
        ;;
    ai)
        ai_cmd "${2:-}"
        exec "${ARGS[@]}"
        ;;
    setup-train)
        new_venv .venv-train
        .venv-train/bin/python -m pip install "torch==${TORCH:-2.14.1}" --index-url "https://download.pytorch.org/whl/${CUDA_TAG:-cu126}"
        .venv-train/bin/python -m pip install -r requirements-train.txt
        .venv-train/bin/python -c "import torch, transformers, peft, trl, bitsandbytes; c = torch.cuda.is_available(); print('torch', torch.__version__, 'cuda', c, torch.cuda.get_device_name(0) if c else '-'); print('bf16', c and torch.cuda.is_bf16_supported())"
        ;;
    setup-convert)
        [ -f tools/llama-src/convert_lora_to_gguf.py ] || die "tools/llama-src missing: bash scripts/run.sh setup"
        new_venv .venv-convert
        .venv-convert/bin/python -m pip install -r tools/llama-src/requirements/requirements-convert_hf_to_gguf.txt
        .venv-convert/bin/python -m pip install -e tools/llama-src/gguf-py huggingface_hub
        .venv-convert/bin/python tools/llama-src/convert_lora_to_gguf.py --help > /dev/null && echo "converter ok"
        ;;
    *) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
