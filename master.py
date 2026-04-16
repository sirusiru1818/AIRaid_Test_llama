"""
AIRaid Master Server - Distributed llama.cpp Control Center
마스터 PC에서 실행: 워커 관리, llama-server 제어, 웹 대시보드 제공
사용법: python master.py [--port 5555]
"""

import argparse
import glob as globmod
import json
import os
import platform
import re
import socket
import subprocess
import sys
import threading
import time

import psutil
import requests as http_client
from flask import Flask, Response, jsonify, request, send_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, "index.html")
MODELS_DIR = os.path.join(BASE_DIR, "models")
LLAMA_DIR = os.path.join(BASE_DIR, "llama")

app = Flask(__name__)

# ── Worker management ──────────────────────────
workers = {}
workers_lock = threading.Lock()
worker_commands = {}
worker_commands_lock = threading.Lock()
WORKER_TIMEOUT = 15

# ── llama-server process ───────────────────────
llama_proc = None
llama_proc_lock = threading.Lock()
llama_log_lines = []
llama_log_lock = threading.Lock()
MAX_LOG_LINES = 500

llama_state = {
    "running": False,
    "ready": False,
    "pid": None,
    "started_at": None,
    "model": None,
    "port": 8080,
    "loading_phase": "idle",
    "loading_progress": 0,
    "total_tensors": 0,
    "loaded_tensors": 0,
    "phase_started_at": None,
    "last_output_at": None,
    "error_message": None,
}

_PHASE_ORDER = ("idle", "starting", "fitting", "loading", "warmup", "ready", "error")


def _phase_idx(p):
    try:
        return _PHASE_ORDER.index(p)
    except ValueError:
        return -1


def _extra_specifies_flash_attn(extra_tokens):
    """추가 인자에 -fa / --flash-attn 이 이미 있으면 True."""
    i = 0
    while i < len(extra_tokens):
        t = extra_tokens[i]
        if t in ("-fa", "--flash-attn"):
            return True
        if t.startswith("-fa=") or t.startswith("--flash-attn="):
            return True
        i += 1
    return False


def _extra_specifies_parallel(extra_tokens):
    """추가 인자에 -np / --parallel 이 이미 있으면 True."""
    for t in extra_tokens:
        if t in ("-np", "--parallel"):
            return True
        if t.startswith("--parallel="):
            return True
    return False


def _extra_specifies_batch(extra_tokens):
    """추가 인자에 -b / -ub 가 이미 있으면 True."""
    for t in extra_tokens:
        if t in ("-b", "--batch-size", "-ub", "--ubatch-size"):
            return True
        if t.startswith(("--batch-size=", "--ubatch-size=")):
            return True
    return False


def _extra_specifies_kv_cache(extra_tokens):
    for t in extra_tokens:
        if t in ("-ctk", "--cache-type-k", "-ctv", "--cache-type-v"):
            return True
        if t.startswith(("--cache-type-k=", "--cache-type-v=")):
            return True
    return False


# ── System info ────────────────────────────────

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_gpu_info():
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.used,memory.free,"
                "utilization.gpu,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return []
        gpus = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            p = [x.strip() for x in line.split(",")]
            gpus.append({
                "index": int(p[0]), "name": p[1],
                "memory_total_mb": int(p[2]), "memory_used_mb": int(p[3]),
                "memory_free_mb": int(p[4]), "gpu_util_percent": int(p[5]),
                "temperature_c": int(p[6]),
            })
        return gpus
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return []


# ── 프로젝트 환경: 마스터는 웹 대시보드용 pip 전부, 워커는 worker.py가 쓰는 것만 ──
MASTER_PIP_PACKAGES = ("flask", "psutil", "requests")
WORKER_PIP_PACKAGES = ("psutil",)
PROJECT_LLAMA_BINARIES = ("llama-server", "rpc-server")


def _find_llama_binary(llama_dir: str, name: str):
    if not llama_dir or not os.path.isdir(llama_dir):
        return None
    candidates = {name.lower(), (name + ".exe").lower()}
    for root, _dirs, files in os.walk(llama_dir):
        for fn in files:
            if fn.lower() in candidates:
                return os.path.join(root, fn)
    return None


def _llama_binary_version_line(exe_path: str):
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


def _pip_pkg_version(name: str):
    try:
        from importlib import metadata

        return metadata.version(name)
    except Exception:
        return None


def collect_project_env(llama_dir: str):
    """마스터: requirements.txt + llama 바이너리. (워커는 worker.py 의 collect_project_env 참고)"""
    out = {
        "env_role": "master",
        "python": platform.python_version(),
        "packages": {},
        "llama_binaries": {},
        "collected_at": time.time(),
    }
    for pkg in MASTER_PIP_PACKAGES:
        v = _pip_pkg_version(pkg)
        out["packages"][pkg] = v if v else "(미설치)"
    for name in PROJECT_LLAMA_BINARIES:
        path = _find_llama_binary(llama_dir, name)
        if path:
            out["llama_binaries"][name] = {
                "path": path,
                "version": _llama_binary_version_line(path),
            }
        else:
            out["llama_binaries"][name] = {"path": None, "version": None}
    return out


def extract_llama_build_token(version_line):
    if not version_line:
        return None
    try:
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
    except Exception:
        return None


master_stats = {}
master_stats_lock = threading.Lock()
_master_software_ts = 0.0
_MASTER_SOFTWARE_TTL = 45.0


def collect_master_loop():
    global _master_software_ts
    psutil.cpu_percent()
    time.sleep(1)
    while True:
        try:
            cpu_pct = psutil.cpu_percent(interval=1)
            cpu_freq = psutil.cpu_freq()
            mem = psutil.virtual_memory()
            try:
                disk = psutil.disk_usage("C:\\" if platform.system() == "Windows" else "/")
            except Exception:
                disk = None

            now = time.time()
            sw = None
            with master_stats_lock:
                prev_sw = master_stats.get("software")
            if (
                prev_sw is None
                or now - _master_software_ts >= _MASTER_SOFTWARE_TTL
            ):
                try:
                    sw = collect_project_env(LLAMA_DIR)
                except Exception as e:
                    sw = {"error": str(e)}
                _master_software_ts = now
            else:
                sw = prev_sw or {}

            stats = {
                "hostname": socket.gethostname(),
                "ip": get_local_ip(),
                "os": f"{platform.system()} {platform.release()}",
                "role": "master",
                "cpu": {
                    "percent": cpu_pct,
                    "count_logical": psutil.cpu_count(logical=True),
                    "count_physical": psutil.cpu_count(logical=False) or psutil.cpu_count(logical=True),
                    "freq_mhz": round(cpu_freq.current, 0) if cpu_freq else 0,
                },
                "ram": {
                    "total_gb": round(mem.total / (1024 ** 3), 2),
                    "used_gb": round(mem.used / (1024 ** 3), 2),
                    "percent": mem.percent,
                },
                "disk": {
                    "total_gb": round(disk.total / (1024 ** 3), 1) if disk else 0,
                    "used_gb": round(disk.used / (1024 ** 3), 1) if disk else 0,
                    "percent": round(disk.percent, 1) if disk else 0,
                },
                "gpus": get_gpu_info(),
                "software": sw,
                "timestamp": time.time(),
            }
            with master_stats_lock:
                master_stats.update(stats)
        except Exception as e:
            print(f"[마스터 수집 오류] {e}")
        time.sleep(2)


# ── Model helpers ──────────────────────────────

def scan_models():
    os.makedirs(MODELS_DIR, exist_ok=True)
    models = []
    for f in globmod.glob(os.path.join(MODELS_DIR, "**", "*.gguf"), recursive=True):
        st = os.stat(f)
        models.append({
            "name": os.path.basename(f),
            "path": f,
            "size_gb": round(st.st_size / (1024 ** 3), 2),
        })
    models.sort(key=lambda x: x["name"])
    return models


# ── Model presets & download ───────────────────

MODEL_PRESETS = [
    {
        "id": "llama-3.1-8b-q4km",
        "name": "Llama 3.1 8B Instruct",
        "quant": "Q4_K_M",
        "size_gb": 4.9,
        "vram_gb": 6,
        "description": "가벼운 모델 · 단일 GPU로 실행 가능",
        "recommended": {"ctx_size": 8192, "n_gpu_layers": -1},
        "files": [
            {"url": "https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/main/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
             "name": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"},
        ],
    },
    {
        "id": "llama-3.1-8b-q8",
        "name": "Llama 3.1 8B Instruct",
        "quant": "Q8_0",
        "size_gb": 8.5,
        "vram_gb": 10,
        "description": "8B 고품질 양자화 · 단일 GPU",
        "recommended": {"ctx_size": 8192, "n_gpu_layers": -1},
        "files": [
            {"url": "https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/main/Meta-Llama-3.1-8B-Instruct-Q8_0.gguf",
             "name": "Meta-Llama-3.1-8B-Instruct-Q8_0.gguf"},
        ],
    },
    {
        "id": "llama-3.1-70b-q4km",
        "name": "Llama 3.1 70B Instruct",
        "quant": "Q4_K_M",
        "size_gb": 40.8,
        "vram_gb": 44,
        "description": "고성능 모델 · 분산 추론 권장 (2~3 GPU)",
        "recommended": {"ctx_size": 4096, "n_gpu_layers": -1},
        "files": [
            {"url": "https://huggingface.co/bartowski/Meta-Llama-3.1-70B-Instruct-GGUF/resolve/main/Meta-Llama-3.1-70B-Instruct-Q4_K_M.gguf",
             "name": "Meta-Llama-3.1-70B-Instruct-Q4_K_M.gguf"},
        ],
    },
    {
        "id": "llama-3.1-405b-q4km",
        "name": "Llama 3.1 405B Instruct",
        "quant": "Q4_K_M",
        "size_gb": 235,
        "vram_gb": 250,
        "description": "최대 모델 · 다중 GPU 분산 필수 (8+ GPU)",
        "recommended": {"ctx_size": 2048, "n_gpu_layers": -1},
        "files": [
            {"url": f"https://huggingface.co/bartowski/Meta-Llama-3.1-405B-Instruct-GGUF/resolve/main/Meta-Llama-3.1-405B-Instruct-Q4_K_M/Meta-Llama-3.1-405B-Instruct-Q4_K_M-{i:05d}-of-00009.gguf",
             "name": f"Meta-Llama-3.1-405B-Instruct-Q4_K_M-{i:05d}-of-00009.gguf"}
            for i in range(1, 10)
        ],
    },
]

download_state = {
    "active": False,
    "preset_id": None,
    "preset_name": "",
    "current_file": "",
    "file_index": 0,
    "total_files": 0,
    "downloaded_bytes": 0,
    "total_bytes": 0,
    "speed_bps": 0,
    "error": None,
}
_download_lock = threading.Lock()
_download_cancel = threading.Event()


def _download_worker(preset):
    files = preset["files"]
    with _download_lock:
        download_state.update({
            "active": True, "preset_id": preset["id"],
            "preset_name": preset["name"], "file_index": 0,
            "total_files": len(files), "error": None,
            "downloaded_bytes": 0, "total_bytes": 0, "speed_bps": 0,
        })
    _download_cancel.clear()

    for idx, finfo in enumerate(files):
        if _download_cancel.is_set():
            break

        url, fname = finfo["url"], finfo["name"]
        dest = os.path.join(MODELS_DIR, fname)

        if os.path.exists(dest):
            fsize = os.path.getsize(dest)
            with _download_lock:
                download_state.update({
                    "current_file": fname, "file_index": idx + 1,
                    "downloaded_bytes": fsize, "total_bytes": fsize,
                })
            continue

        with _download_lock:
            download_state.update({
                "current_file": fname, "file_index": idx + 1,
                "downloaded_bytes": 0, "total_bytes": 0, "speed_bps": 0,
            })

        part_path = dest + ".part"
        try:
            resp = http_client.get(url, stream=True, timeout=30,
                                   headers={"User-Agent": "AIRaid/1.0"})
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            with _download_lock:
                download_state["total_bytes"] = total

            downloaded = 0
            t0 = time.time()
            last_t, last_b = t0, 0

            with open(part_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if _download_cancel.is_set():
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    dt = now - last_t
                    if dt >= 0.5:
                        speed = (downloaded - last_b) / dt
                        last_t, last_b = now, downloaded
                        with _download_lock:
                            download_state["speed_bps"] = speed
                    with _download_lock:
                        download_state["downloaded_bytes"] = downloaded

            if _download_cancel.is_set():
                if os.path.exists(part_path):
                    os.remove(part_path)
                break

            os.rename(part_path, dest)
            print(f"[다운로드] 완료: {fname}")

        except Exception as e:
            if os.path.exists(part_path):
                os.remove(part_path)
            with _download_lock:
                download_state.update({"error": str(e), "active": False})
            print(f"[다운로드 오류] {fname}: {e}")
            return

    with _download_lock:
        download_state["active"] = False
    if not _download_cancel.is_set():
        print(f"[다운로드] 프리셋 완료: {preset['name']}")


def find_binary(name):
    """LLAMA_DIR 안에서 바이너리를 재귀 검색 (.exe 포함)."""
    candidates = {name.lower(), (name + ".exe").lower()}
    for root, _dirs, files in os.walk(LLAMA_DIR):
        for fn in files:
            if fn.lower() in candidates:
                return os.path.join(root, fn)
    return None


# ── llama-server management ────────────────────

_ERROR_PATTERNS = (
    "crashed", "recv failed", "rpc server crashed",
    "cuda error", "out of memory", "fatal error",
    "failed to allocate", "ggml_cuda_error",
    "couldn't bind", "could not bind", "bind http server",
)


def _parse_log_phase(text):
    """로그 한 줄을 분석해서 llama_state의 로딩 단계/진행률을 갱신.

    최신 llama.cpp 버전의 다양한 로그 포맷을 폭넓게 매칭한다.
    단계는 항상 앞으로만 전진하며, 이전 단계로 되돌아가지 않는다.
    """
    lower = text.lower()
    now = time.time()

    m = re.search(r"(\d+)\s+tensors", text)
    if m and "meta" in lower:
        llama_state["total_tensors"] = int(m.group(1))

    prev = llama_state.get("loading_phase", "starting")

    if prev == "ready":
        return

    if any(p in lower for p in _ERROR_PATTERNS):
        llama_state["loading_phase"] = "error"
        llama_state["error_message"] = text.strip()
        llama_state["phase_started_at"] = now
        return

    if "fitting" in lower and ("device" in lower or "memory" in lower or "param" in lower):
        if _phase_idx(prev) < _phase_idx("fitting"):
            llama_state["loading_phase"] = "fitting"
            llama_state["loading_progress"] = 5
            llama_state["phase_started_at"] = now
    elif "load_tensors" in lower:
        if _phase_idx(prev) < _phase_idx("loading"):
            llama_state["loading_phase"] = "loading"
            llama_state["loaded_tensors"] = 0
            llama_state["loading_progress"] = 10
            llama_state["phase_started_at"] = now
    elif "warming up" in lower or "warm up" in lower:
        if _phase_idx(prev) < _phase_idx("warmup"):
            llama_state["loading_phase"] = "warmup"
            llama_state["loading_progress"] = 95
            llama_state["phase_started_at"] = now
    elif "all slots are idle" in lower or ("server" in lower and "listening" in lower):
        llama_state["loading_phase"] = "ready"
        llama_state["loading_progress"] = 100
        llama_state["phase_started_at"] = now


def _log_reader(stream):
    """llama-server stdout를 문자 단위로 읽어 로그 저장 + 로딩 진행률 추적."""
    buf = bytearray()
    try:
        while True:
            ch = stream.read(1)
            if not ch:
                break
            if ch in (b"\n", b"\r"):
                if buf:
                    text = buf.decode("utf-8", errors="replace").rstrip()
                    buf.clear()
                    llama_state["last_output_at"] = time.time()
                    _parse_log_phase(text)
                    with llama_log_lock:
                        llama_log_lines.append(text)
                        if len(llama_log_lines) > MAX_LOG_LINES:
                            del llama_log_lines[: len(llama_log_lines) - MAX_LOG_LINES]
            else:
                buf += ch
                if ch == b"." and llama_state.get("loading_phase") == "loading":
                    llama_state["loaded_tensors"] = llama_state.get("loaded_tensors", 0) + 1
                    llama_state["last_output_at"] = time.time()
                    total = llama_state.get("total_tensors", 0)
                    if total > 0:
                        pct = min(10 + round(llama_state["loaded_tensors"] / total * 80), 90)
                        llama_state["loading_progress"] = pct
    except Exception:
        pass
    finally:
        check_llama_alive()


def _health_checker():
    """llama-server /health 엔드포인트를 주기적으로 확인."""
    while True:
        time.sleep(3)
        if not llama_state.get("running"):
            llama_state["ready"] = False
            continue
        try:
            r = http_client.get(
                f"http://127.0.0.1:{llama_state['port']}/health", timeout=2
            )
            ready = r.status_code == 200
            llama_state["ready"] = ready
            if ready and llama_state.get("loading_phase") != "ready":
                llama_state["loading_phase"] = "ready"
                llama_state["loading_progress"] = 100
        except Exception:
            llama_state["ready"] = False


def start_llama(config):
    global llama_proc
    with llama_proc_lock:
        if llama_proc and llama_proc.poll() is None:
            return False, "llama-server가 이미 실행 중입니다"

        exe = find_binary("llama-server")
        if not exe:
            return False, "llama-server 바이너리를 찾을 수 없습니다 (llama/ 폴더 확인)"

        model_path = config.get("model")
        if not model_path or not os.path.exists(model_path):
            return False, f"모델 파일을 찾을 수 없습니다: {model_path}"

        port = int(config.get("port", 8080))
        ctx = int(config.get("ctx_size", 4096))
        ngl = int(config.get("n_gpu_layers", -1))

        extra = config.get("extra_args", "").strip()
        extra_tokens = extra.split() if extra else []

        rpc = config.get("rpc_workers", [])
        # 원격 4GB 등 VRAM 한계 시 8192 ctx + 큰 배치가 rpc-server OOM/슬롯 초기화 크래시 유발
        if rpc and config.get("rpc_cap_context", True):
            cap = int(config.get("rpc_context_cap", 4096))
            if ctx > cap:
                ctx = cap

        cmd = [
            exe, "-m", model_path,
            "--host", "0.0.0.0",
            "--port", str(port),
            "-c", str(ctx),
            "-ngl", str(ngl),
        ]

        threads = int(config.get("threads", 0))
        if threads > 0:
            cmd.extend(["-t", str(threads)])

        if rpc:
            cmd.extend(["--rpc", ",".join(rpc)])

        if config.get("fit_disabled", True):
            cmd.extend(["-fit", "off"])
        # RPC 분산 시 빈 워밍업 런이 원격 rpc-server를 크래시시키는 경우가 많음
        # (ggml-rpc: recv failed / Remote RPC server crashed). 기본으로 건너뜀.
        if rpc and config.get("rpc_no_warmup", True) and "--no-warmup" not in extra_tokens:
            cmd.append("--no-warmup")
        # RPC + Flash Attention 조합에서 원격 ggml_cuda_flash_attn_ext 크래시 보고됨
        # (슬롯 초기화/그래프 실행 단계). 기본으로 끔 — https://github.com/ggml-org/llama.cpp/issues/20748
        if rpc and config.get("rpc_flash_attn_off", True) and not _extra_specifies_flash_attn(
            extra_tokens
        ):
            cmd.extend(["-fa", "off"])
        # RPC 시 기본 n_parallel=4면 KV/그래프 부담이 커져 원격 rpc-server OOM·크래시 유발 (#20315 등)
        if rpc and config.get("rpc_parallel_one", True) and not _extra_specifies_parallel(
            extra_tokens
        ):
            cmd.extend(["-np", "1"])
        if rpc and config.get("rpc_reduce_batch", True) and not _extra_specifies_batch(
            extra_tokens
        ):
            cmd.extend(["-b", "1024", "-ub", "256"])
        # KV를 q8로 줄이면 원격 VRAM 여유가 생김(품질 영향 가능). 문제 시 API에서 rpc_kv_cache_q8: false
        if rpc and config.get("rpc_kv_cache_q8", True) and not _extra_specifies_kv_cache(
            extra_tokens
        ):
            cmd.extend(["-ctk", "q8_0", "-ctv", "q8_0"])

        if extra_tokens:
            cmd.extend(extra_tokens)

        with llama_log_lock:
            llama_log_lines.clear()

        popen_env = None
        if rpc and config.get("rpc_disable_cuda_graphs", True):
            popen_env = os.environ.copy()
            # 마스터 CUDA + 원격 RPC 그래프 이슈 완화 (원격은 worker.py rpc-server 쪽도 필요)
            popen_env.setdefault("GGML_CUDA_DISABLE_GRAPHS", "1")

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                env=popen_env,
            )
            llama_proc = proc
            llama_state.update({
                "running": True, "ready": False, "pid": proc.pid,
                "started_at": time.time(),
                "model": os.path.basename(model_path),
                "port": port,
                "loading_phase": "starting",
                "loading_progress": 0,
                "total_tensors": 0,
                "loaded_tensors": 0,
                "phase_started_at": time.time(),
                "last_output_at": None,
                "error_message": None,
            })
            threading.Thread(target=_log_reader, args=(proc.stdout,), daemon=True).start()
            return True, f"llama-server 시작됨 (PID {proc.pid})"
        except Exception as e:
            return False, f"실행 오류: {e}"


def stop_llama():
    global llama_proc
    with llama_proc_lock:
        if llama_proc and llama_proc.poll() is None:
            llama_proc.terminate()
            try:
                llama_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                llama_proc.kill()
            llama_proc = None
            llama_state.update({
                "running": False, "ready": False, "pid": None, "started_at": None,
                "loading_phase": "idle", "loading_progress": 0,
                "phase_started_at": None, "last_output_at": None,
                "error_message": None,
            })
            return True, "llama-server 중지됨"
        llama_proc = None
        llama_state.update({
            "running": False, "ready": False,
            "loading_phase": "idle", "loading_progress": 0,
            "phase_started_at": None, "last_output_at": None,
            "error_message": None,
        })
        return False, "실행 중인 서버가 없습니다"


def check_llama_alive():
    global llama_proc
    with llama_proc_lock:
        if llama_proc and llama_proc.poll() is not None:
            llama_proc = None
            llama_state.update({
                "running": False, "ready": False, "pid": None,
                "loading_phase": "idle", "loading_progress": 0,
                "phase_started_at": None, "last_output_at": None,
                "error_message": None,
            })


def build_version_audit_response():
    """마스터 소프트웨어 스냅샷을 기준으로 워커와 비교."""
    now = time.time()
    with master_stats_lock:
        master = dict(master_stats)
    with workers_lock:
        active = {}
        for wid, s in workers.items():
            if now - s.get("timestamp", 0) < WORKER_TIMEOUT:
                active[wid] = s

    ref = master.get("software") or {}
    machines = [
        {
            "id": "master",
            "role": "master",
            "hostname": master.get("hostname"),
            "ip": master.get("ip"),
            "software": ref,
        }
    ]
    for wid in sorted(active.keys()):
        w = active[wid]
        machines.append({
            "id": wid,
            "role": "worker",
            "hostname": w.get("hostname"),
            "ip": w.get("ip"),
            "software": w.get("software") or {},
        })

    issues = []

    def add_issue(severity, component, machine_label, expected, actual, hint=None):
        row = {
            "severity": severity,
            "component": component,
            "machine": machine_label,
            "expected": expected,
            "actual": actual,
        }
        if hint:
            row["hint"] = hint
        issues.append(row)

    for m in machines:
        if m["id"] == "master":
            continue
        label = m.get("hostname") or str(m.get("ip") or "") or m["id"][:16]
        sw = m.get("software") or {}
        err = sw.get("error")
        if err:
            add_issue("warning", "software_collect", label, "정상", err)

        _miss = "(미설치)"
        for pkg in WORKER_PIP_PACKAGES:
            a = (ref.get("packages") or {}).get(pkg)
            b = (sw.get("packages") or {}).get(pkg)
            if a == b:
                continue
            if a in (None, _miss) and b in (None, _miss):
                continue
            add_issue("warning", f"pip:{pkg}", label, str(a), str(b))

        py_a, py_b = ref.get("python"), sw.get("python")
        if py_a and py_b and py_a != py_b:
            add_issue("warning", "python", label, py_a, py_b)

        for bin_name in PROJECT_LLAMA_BINARIES:
            va = (ref.get("llama_binaries") or {}).get(bin_name, {}).get("version")
            vb = (sw.get("llama_binaries") or {}).get(bin_name, {}).get("version")
            ta, tb = extract_llama_build_token(va), extract_llama_build_token(vb)
            if not ta and not tb:
                continue
            if ta and tb:
                if ta != tb:
                    add_issue("error", bin_name, label, va or "", vb or "", f"토큰 {ta} ≠ {tb}")
            elif ta and not tb:
                add_issue("error", bin_name, label, va or "", vb or "(없음)")
            elif not ta and tb:
                add_issue("warning", bin_name, label, "(마스터 미감지)", vb or "")

    ok = not any(i.get("severity") == "error" for i in issues)
    return {
        "reference_hostname": master.get("hostname"),
        "machines": machines,
        "issues": issues,
        "ok": ok,
    }


# ── Flask routes ───────────────────────────────

@app.route("/")
def dashboard():
    return send_file(HTML_PATH)


@app.route("/api/report", methods=["POST"])
def receive_report():
    data = request.get_json(force=True)
    ip = data.get("ip") or request.remote_addr
    data["ip"] = ip
    wid = (data.get("worker_id") or "").strip() or ip
    data["worker_id"] = wid
    data["role"] = "worker"
    with workers_lock:
        workers[wid] = data

    with worker_commands_lock:
        cmds = worker_commands.pop(wid, [])

    return jsonify({"status": "ok", "commands": cmds})


@app.route("/api/stats")
def get_all_stats():
    now = time.time()
    check_llama_alive()

    with master_stats_lock:
        master = dict(master_stats)

    with workers_lock:
        active = {}
        stale = []
        for ip, s in workers.items():
            if now - s.get("timestamp", 0) < WORKER_TIMEOUT:
                active[ip] = s
            else:
                stale.append(ip)
        for k in stale:
            del workers[k]

    return jsonify({
        "master": master,
        "workers": active,
        "llama": dict(llama_state),
    })


@app.route("/api/version-audit")
def api_version_audit():
    return jsonify(build_version_audit_response())


@app.route("/api/models")
def list_models():
    return jsonify({"models": scan_models()})


@app.route("/api/presets")
def get_presets():
    downloaded = {m["name"] for m in scan_models()}
    result = []
    for p in MODEL_PRESETS:
        info = {k: v for k, v in p.items() if k != "files"}
        info["file_count"] = len(p["files"])
        info["downloaded"] = all(f["name"] in downloaded for f in p["files"])
        if info["downloaded"]:
            info["model_path"] = os.path.join(MODELS_DIR, p["files"][0]["name"])
        result.append(info)
    return jsonify({"presets": result})


@app.route("/api/download/start", methods=["POST"])
def api_download_start():
    data = request.get_json(force=True)
    pid = data.get("preset_id")
    preset = next((p for p in MODEL_PRESETS if p["id"] == pid), None)
    if not preset:
        return jsonify({"success": False, "message": "프리셋을 찾을 수 없습니다"})
    if download_state["active"]:
        return jsonify({"success": False, "message": "이미 다운로드가 진행 중입니다"})
    threading.Thread(target=_download_worker, args=(preset,), daemon=True).start()
    return jsonify({"success": True, "message": f"{preset['name']} 다운로드 시작"})


@app.route("/api/download/status")
def api_download_status():
    with _download_lock:
        return jsonify(dict(download_state))


@app.route("/api/download/cancel", methods=["POST"])
def api_download_cancel():
    _download_cancel.set()
    return jsonify({"success": True, "message": "다운로드 취소 요청됨"})


@app.route("/api/llama/status")
def api_llama_status():
    check_llama_alive()
    with llama_log_lock:
        logs = list(llama_log_lines[-100:])
    return jsonify({**llama_state, "logs": logs})


@app.route("/api/llama/start", methods=["POST"])
def api_llama_start():
    cfg = request.get_json(force=True)
    ok, msg = start_llama(cfg)
    return jsonify({"success": ok, "message": msg})


@app.route("/api/llama/stop", methods=["POST"])
def api_llama_stop():
    ok, msg = stop_llama()
    return jsonify({"success": ok, "message": msg})


@app.route("/api/llama/logs")
def api_llama_logs():
    with llama_log_lock:
        return jsonify({"logs": list(llama_log_lines[-200:])})


@app.route("/api/llama/chat", methods=["POST"])
def proxy_chat():
    check_llama_alive()
    if not llama_state.get("running"):
        return jsonify({"error": "llama-server가 실행 중이 아닙니다"}), 503

    port = llama_state.get("port", 8080)
    data = request.get_json(force=True)
    stream = data.get("stream", False)
    target = f"http://127.0.0.1:{port}/v1/chat/completions"

    try:
        if stream:
            resp = http_client.post(target, json=data, stream=True, timeout=300)
            return Response(
                resp.iter_content(chunk_size=None),
                content_type=resp.headers.get("Content-Type", "text/event-stream"),
            )
        else:
            resp = http_client.post(target, json=data, timeout=120)
            return jsonify(resp.json())
    except http_client.ConnectionError:
        return jsonify({"error": "llama-server에 연결할 수 없습니다 (모델 로딩 중일 수 있음)"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/workers/<path:worker_id>/rpc", methods=["POST"])
def worker_rpc_control(worker_id):
    data = request.get_json(force=True)
    with worker_commands_lock:
        worker_commands.setdefault(worker_id, []).append({
            "type": "rpc",
            "action": data.get("action", "start"),
            "port": int(data.get("port", 50052)),
        })
    return jsonify({"status": "queued"})


# ── Main ───────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AIRaid Master Server")
    parser.add_argument("--port", type=int, default=5555, help="서버 포트 (기본: 5555)")
    args = parser.parse_args()

    os.makedirs(MODELS_DIR, exist_ok=True)
    os.makedirs(LLAMA_DIR, exist_ok=True)

    local_ip = get_local_ip()

    if not os.path.exists(HTML_PATH):
        print(f"[오류] index.html을 찾을 수 없습니다: {HTML_PATH}")
        sys.exit(1)

    threading.Thread(target=collect_master_loop, daemon=True).start()
    threading.Thread(target=_health_checker, daemon=True).start()

    print("=" * 58)
    print("  AIRaid Master - Distributed llama.cpp Control Center")
    print("=" * 58)
    print(f"  대시보드  : http://{local_ip}:{args.port}")
    print(f"  모델 폴더 : {MODELS_DIR}")
    print(f"  llama 폴더: {LLAMA_DIR}")
    print(f"  워커 연결 : python worker.py --master http://{local_ip}:{args.port}")
    print("=" * 58)
    print()

    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
