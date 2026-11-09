"""실행 PC 확인: GPU·VRAM·RAM·필수 파일. 에뮬레이터를 켜기 전/후로 한 번씩 실행해 기록한다."""
import ctypes
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config import load_manifest, model_profile  # noqa: E402


def ram_gib():
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    if platform.system() == "Linux":
        info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
        kib = lambda k: int(info[k].split()[0])  # noqa: E731
        return round(kib("MemTotal") / 2**20, 1), round(kib("MemAvailable") / 2**20, 1)
    if platform.system() != "Windows":
        return None
    s = MS()
    s.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return round(s.ullTotalPhys / 2**30, 1), round(s.ullAvailPhys / 2**30, 1)


def gpu():
    if not shutil.which("nvidia-smi"):
        return "nvidia-smi not found (NVIDIA driver?)"
    q = "name,memory.total,memory.used,memory.free,driver_version"
    out = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader"], capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()


def main():
    m = load_manifest()
    ram = ram_gib()
    rows = {
        "python": sys.version.split()[0],
        "model profile": model_profile(),
        "model alias": m["model"]["alias"],
        "gpu (name, total, used, free, driver)": gpu(),
        "ram total/avail GiB": f"{ram[0]} / {ram[1]}" if ram else "n/a",
        "llama-server": any((ROOT / "tools" / "llama").rglob("llama-server*")),
        "llama VERSION.json": (ROOT / "tools" / "llama" / "VERSION.json").read_text(encoding="utf-8-sig").strip()
        if (ROOT / "tools" / "llama" / "VERSION.json").exists() else "missing",
        "model file": (ROOT / "models" / m["model"]["gguf_file"]).exists(),
        "adapter": m["adapter"]["file"] if m["adapter"] else "none",
    }
    for k, v in rows.items():
        print(f"{k:40} {v}")
    # 파라미터 튜닝: 모델 단독 VRAM 5GiB 이하, 전체 GPU 여유 1GiB 이상을 측정 목표로 기록


if __name__ == "__main__":
    main()
