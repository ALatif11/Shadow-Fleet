"""`make doctor`: environment checks for the WSL2 runtime (ADR-13)."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass

from shadowfleet import config
from shadowfleet.util import disk

OK, WARN, FAIL = "ok", "warn", "FAIL"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _run(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def checks(network: bool = True) -> list[Check]:
    out: list[Check] = []
    v = sys.version_info
    out.append(Check("python", OK if v >= (3, 11) else FAIL, platform.python_version()))
    rel = platform.uname().release.lower()
    out.append(Check("wsl2", OK if "microsoft" in rel else WARN,
                     platform.uname().release + ("" if "microsoft" in rel else " (not WSL; fine only if Linux)")))
    root = str(config.REPO_ROOT)
    out.append(Check("repo on Linux filesystem", FAIL if root.startswith("/mnt/") else OK, root))
    free = disk.free_gb(config.DATA_DIR)
    st = FAIL if free < config.MIN_FREE_GB else WARN if free < config.RECOMMENDED_FREE_GB else OK
    out.append(Check("free disk for data/", st,
                     f"{free:.1f} GB at {config.DATA_DIR} (reserve {config.MIN_FREE_GB:.0f}, "
                     f"recommended {config.RECOMMENDED_FREE_GB})"))
    try:
        mem_kb = int(next(ln for ln in open("/proc/meminfo") if ln.startswith("MemTotal")).split()[1])
        mem_gb = mem_kb / 1024**2
        out.append(Check("memory", OK if mem_gb >= 12 else WARN,
                         f"{mem_gb:.1f} GB (DuckDB limit {config.DUCKDB_MEMORY_LIMIT})"))
    except (OSError, StopIteration, ValueError):
        out.append(Check("memory", WARN, "could not read /proc/meminfo"))
    out.append(Check("cpus", OK, str(os.cpu_count())))
    smi = _run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"])
    cuda = _run(["nvidia-smi"])
    cuda_v = None
    if cuda and "CUDA Version" in cuda:
        cuda_v = cuda.split("CUDA Version:")[1].split()[0]
    out.append(Check("gpu", OK if smi else WARN,
                     f"{smi} CUDA {cuda_v}" if smi else "nvidia-smi not found (needed from Phase 0 task 9)"))
    out.append(Check("GFW_TOKEN", OK if config.GFW_TOKEN else WARN,
                     "set" if config.GFW_TOKEN else "missing in .env (needed for probe-gfw)"))
    llama = os.environ.get("LLAMA_SERVER_BIN") or shutil.which("llama-server")
    out.append(Check("llama-server", OK if llama else WARN, llama or "not built yet (SETUP.md step 6)"))
    try:
        import duckdb
        import pyarrow

        out.append(Check("duckdb/pyarrow", OK, f"{duckdb.__version__} / {pyarrow.__version__}"))
    except ImportError as e:
        out.append(Check("duckdb/pyarrow", FAIL, repr(e)))
    if network:
        from shadowfleet.util import net

        targets = {
            "net: DMA": config.DMA_INDEX_URLS[0],
            "net: GFW": config.GFW_BASE_URL,
            "net: OFAC": "https://ofac.treasury.gov/",
            "net: OpenSanctions": f"{config.OPENSANCTIONS_BASE}/latest/maritime/index.json",
        }
        with net.client(timeout=15) as c:
            for name, url in targets.items():
                try:
                    r = c.get(url)
                    out.append(Check(name, OK if r.status_code < 500 else WARN, f"HTTP {r.status_code}"))
                except Exception as e:  # noqa: BLE001
                    out.append(Check(name, WARN, repr(e)[:120]))
    return out
