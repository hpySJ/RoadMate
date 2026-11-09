"""관리 스크립트로 띄운 로컬 모델의 파일·프로세스·문맥을 확인한다. 원격 증명/전자서명은 아니다."""
import ctypes
import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=8)
def _digest(path: str, size: int, modified: int, changed: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def digest(path: Path) -> str:
    p = path.resolve(strict=True)
    stat = p.stat()
    return _digest(str(p), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


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
        # comm can contain spaces; fields after its final ')' start at state (field 3).
        started = (base / "stat").read_text().rsplit(")", 1)[1].split()[19]
    if executable not in ("llama-server", "llama-server.exe"):
        raise ValueError("model pid is not llama-server")
    return {"pid": pid, "started": str(started)}


def verify_runtime(props: dict, adapters: list, manifest: dict, process: dict, root: Path = ROOT) -> dict:
    model = root / "models" / manifest["model"]["gguf_file"]
    if Path(props["model_path"]).resolve() != model.resolve():
        raise ValueError("loaded model path differs from manifest")
    sha = digest(model)
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
        adapter_sha = digest(path)
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
