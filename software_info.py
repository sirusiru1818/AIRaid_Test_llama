"""
AIRaid 마스터/워커 공통: Python·pip 패키지·llama.cpp 바이너리 버전 수집.
"""

from __future__ import annotations

import os
import platform
import re
import subprocess
import sys
import time
from typing import Any, Dict, Optional

# requirements.txt 기준
PIP_PACKAGES = ("flask", "psutil", "requests")
LLAMA_BINARIES = ("llama-server", "rpc-server")


def _find_binary(llama_dir: str, name: str) -> Optional[str]:
    if not llama_dir or not os.path.isdir(llama_dir):
        return None
    candidates = {name.lower(), (name + ".exe").lower()}
    for root, _dirs, files in os.walk(llama_dir):
        for fn in files:
            if fn.lower() in candidates:
                return os.path.join(root, fn)
    return None


def _binary_version_line(exe_path: str) -> Optional[str]:
    if not exe_path or not os.path.isfile(exe_path):
        return None
    for ver_args in (["--version"], ["-v"]):
        try:
            run_kw = dict(
                args=[exe_path] + ver_args,
                capture_output=True,
                text=True,
                timeout=12,
            )
            if sys.platform == "win32":
                run_kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            r = subprocess.run(**run_kw)
            out = (r.stdout or "") + (r.stderr or "")
            lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
            if lines:
                return lines[0][:800]
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    try:
        st = os.stat(exe_path)
        return f"(바이너리만 확인, --version 없음 · mtime {int(st.st_mtime)})"
    except OSError:
        return None


def _pip_version(name: str) -> Optional[str]:
    try:
        from importlib import metadata

        return metadata.version(name)
    except Exception:
        return None


def collect_airaid_software(llama_dir: str) -> Dict[str, Any]:
    """마스터/워커에서 동일하게 호출."""
    out: Dict[str, Any] = {
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "packages": {},
        "llama_binaries": {},
        "collected_at": time.time(),
    }
    for pkg in PIP_PACKAGES:
        v = _pip_version(pkg)
        if v:
            out["packages"][pkg] = v
    for name in LLAMA_BINARIES:
        path = _find_binary(llama_dir, name)
        if path:
            out["llama_binaries"][name] = {
                "path": path,
                "version": _binary_version_line(path),
            }
        else:
            out["llama_binaries"][name] = {"path": None, "version": None}
    return out


def extract_llama_build_token(version_line: Optional[str]) -> Optional[str]:
    """버전 문자열에서 build 번호 등 비교에 쓸 토큰 추출."""
    if not version_line:
        return None
    s = version_line.strip()
    m = re.search(r"build[_\s:]*([0-9]+)", s, re.I)
    if m:
        return f"build:{m.group(1)}"
    m = re.search(r"\b(b[0-9]+)[-\s]", s, re.I)
    if m:
        return m.group(1).lower()
    m = re.search(r"version:\s*([^\s]+)", s, re.I)
    if m:
        return m.group(1)[:64]
    return s[:120] if s else None
