import ctypes
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
log = logging.getLogger("roadmate.runtime")


# 파일 확인
def _file_key(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns, "inode": stat.st_ino}


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# 해시 캐시
def digest(path: Path, cache_dir: Path = ROOT / "run" / "runtime-hashes", *, allow_hash: bool = True) -> str:
    started = time.monotonic()
    p = path.resolve(strict=True)
    key = _file_key(p)
    cache = cache_dir / (hashlib.sha256(str(p).encode()).hexdigest() + ".json")
    try:
        record = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        record = None
    if (isinstance(record, dict) and record.get("key") == key
            and isinstance(record.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", record["sha256"])):
        if _file_key(p) != key:
            raise ValueError("file changed during cached hash verification")
        log.info("hash cache hit path=%s elapsed_s=%.3f", p, time.monotonic() - started)
        return record["sha256"]
    log.info("hash cache miss path=%s allow_hash=%s elapsed_s=%.3f", p, allow_hash, time.monotonic() - started)
    if not allow_hash:
        raise ValueError("hash cache missing or stale; prepare runtime outside the request before use")
    sha = _digest(p)
    if _file_key(p) != key:
        raise ValueError("file changed while computing SHA256")
    cache_dir.mkdir(parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_dir, suffix=".tmp", delete=False) as stream:
            temp = Path(stream.name)
            json.dump({"key": key, "sha256": sha}, stream)
        os.replace(temp, cache)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    log.info("hash computed path=%s elapsed_s=%.3f", p, time.monotonic() - started)
    return sha


# 모델 준비
def prepare_runtime_files(manifest: dict, root: Path = ROOT) -> None:
    model = root / "models" / manifest["model"]["gguf_file"]
    if digest(model, root / "run" / "runtime-hashes") != manifest["model"]["gguf_sha256"]:
        raise ValueError("model SHA256 mismatch")
    if adapter := manifest.get("adapter"):
        if digest(root / adapter["file"], root / "run" / "runtime-hashes") != adapter["sha256"]:
            raise ValueError("adapter SHA256 mismatch")


# 프로세스 확인
def process_identity(pid_file: Path) -> dict:
    pid = int(pid_file.read_text(encoding="ascii").split()[0])
    if pid <= 0:
        raise ValueError("invalid model pid")
    if os.name == "nt":
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.restype = wintypes.HANDLE
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            raise ValueError("model process unavailable")
        try:
            code = wintypes.DWORD()
            api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            if not api.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:
                raise ValueError("model process exited")
            created, ended, kernel, user = (wintypes.FILETIME() for _ in range(4))
            api.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
            if not api.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ended), ctypes.byref(kernel), ctypes.byref(user)):
                raise ValueError("cannot read model process creation")
            name, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
            api.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            if not api.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(size)):
                raise ValueError("cannot read model executable")
            executable = Path(name.value).name
            started = (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            api.CloseHandle(handle)
    else:
        base = Path("/proc") / str(pid)
        executable = (base / "exe").resolve(strict=True).name

        started = (base / "stat").read_text().rsplit(")", 1)[1].split()[19]
    if executable not in ("llama-server", "llama-server.exe"):
        raise ValueError("model pid is not llama-server")
    return {"pid": pid, "started": str(started)}


# 런타임 확인
def verify_runtime(props: dict, adapters: list, manifest: dict, process: dict, root: Path = ROOT,
                   *, allow_hash: bool = True) -> dict:
    model = root / "models" / manifest["model"]["gguf_file"]
    if Path(props["model_path"]).resolve() != model.resolve():
        raise ValueError("loaded model path differs from manifest")
    sha = digest(model, root / "run" / "runtime-hashes", allow_hash=allow_hash)
    if sha != manifest["model"]["gguf_sha256"]:
        raise ValueError("model SHA256 mismatch")
    ctx = props["default_generation_settings"]["n_ctx"]
    if ctx != manifest["runtime"]["ctx"] or props["total_slots"] != manifest["runtime"]["parallel"]:
        raise ValueError("loaded context/slots differ from manifest")
    tag = manifest["runtime"].get("llama_cpp_tag")
    if tag and not re.search(rf"\b(?:b)?{re.escape(tag.removeprefix('b'))}\b", props["build_info"]):
        raise ValueError("llama.cpp build differs from manifest")
    expected = manifest.get("adapter")
    adapter_sha = None
    if expected:
        path = root / expected["file"]
        if len(adapters) != 1 or Path(adapters[0]["path"]).resolve() != path.resolve() or adapters[0]["scale"] != 1:
            raise ValueError("loaded adapter differs from manifest")
        adapter_sha = digest(path, root / "run" / "runtime-hashes", allow_hash=allow_hash)
        if adapter_sha != expected["sha256"]:
            raise ValueError("adapter SHA256 mismatch")
    elif adapters:
        raise ValueError("unexpected loaded adapters")
    return {"model_sha256": sha, "adapter_sha256": adapter_sha, "context": ctx, "slots": props["total_slots"],
            "process": process, "build": props["build_info"],
            "chat_template_kwargs": manifest["runtime"].get("chat_template_kwargs", {}),
            "chat_template_sha256": hashlib.sha256(props["chat_template"].encode()).hexdigest()
            if isinstance(props.get("chat_template"), str) else None,
            "default_parameters": props["default_generation_settings"]["params"]}


if __name__ == "__main__":
    from config import load_manifest
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    prepare_runtime_files(load_manifest())
