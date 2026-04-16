"""
AIRaid Worker Agent - Distributed llama.cpp Worker
워커 PC에서 실행: 시스템 모니터링 + RPC 서버 관리
사용법: python worker.py --master http://192.168.0.104:5555
"""

import argparse
import json
import os
import platform
import sys
import socket
import subprocess
import threading
import time
import uuid
import urllib.error
import urllib.request

import psutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WORKER_ID_FILE = os.path.join(BASE_DIR, ".airaid_worker_id")
LLAMA_DIR = os.path.join(BASE_DIR, "llama")

# 워커는 worker.py 가 import 하는 pip 만 (flask/requests 는 마스터 전용)
WORKER_PIP_PACKAGES = ("psutil",)
PROJECT_LLAMA_BINARIES = ("llama-server", "rpc-server")


def _find_llama_binary(llama_dir, name):
    if not llama_dir or not os.path.isdir(llama_dir):
        return None
    candidates = {name.lower(), (name + ".exe").lower()}
    for root, _dirs, files in os.walk(llama_dir):
        for fn in files:
            if fn.lower() in candidates:
                return os.path.join(root, fn)
    return None


def _llama_binary_version_line(exe_path):
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


def _pip_pkg_version(name):
    try:
        from importlib import metadata

        return metadata.version(name)
    except Exception:
        return None


def collect_project_env(llama_dir):
    out = {
        "env_role": "worker",
        "python": platform.python_version(),
        "packages": {},
        "llama_binaries": {},
        "collected_at": time.time(),
    }
    for pkg in WORKER_PIP_PACKAGES:
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


_software_cache = None
_software_cache_ts = 0.0
_SOFTWARE_CACHE_TTL = 45.0


def get_software_snapshot():
    global _software_cache, _software_cache_ts
    now = time.time()
    if _software_cache is None or now - _software_cache_ts >= _SOFTWARE_CACHE_TTL:
        try:
            _software_cache = collect_project_env(LLAMA_DIR)
        except Exception as e:
            _software_cache = {"error": str(e)}
        _software_cache_ts = now
    return _software_cache


# ── RPC server process ─────────────────────────
rpc_proc = None
rpc_proc_lock = threading.Lock()
rpc_port = 50052


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def get_worker_id():
    """마스터에서 워커를 구분하는 고유 ID (IP만으로는 중복·충돌 가능)."""
    try:
        if os.path.exists(WORKER_ID_FILE):
            with open(WORKER_ID_FILE, "r", encoding="utf-8") as f:
                s = f.read().strip()
                if s:
                    return s
    except Exception:
        pass
    wid = str(uuid.uuid4())
    try:
        with open(WORKER_ID_FILE, "w", encoding="utf-8") as f:
            f.write(wid)
    except Exception:
        pass
    return wid


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


# ── RPC server management ──────────────────────

def _rpc_stdout_drain(stream):
    """PIPE 를 비우지 않으면 rpc-server 가 출력에 블록되어 RPC 가 멈출 수 있음."""
    try:
        for line in iter(stream.readline, b""):
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if text:
                print(f"[rpc-server] {text}", flush=True)
    except Exception:
        pass


def find_rpc_binary():
    names = {"rpc-server", "rpc-server.exe", "llama-rpc-server", "llama-rpc-server.exe"}
    for root, _dirs, files in os.walk(LLAMA_DIR):
        for fn in files:
            if fn.lower() in {n.lower() for n in names}:
                return os.path.join(root, fn)
    return None


def start_rpc(port):
    global rpc_proc, rpc_port
    with rpc_proc_lock:
        if rpc_proc and rpc_proc.poll() is None:
            print(f"[RPC] 이미 실행 중 (PID {rpc_proc.pid})")
            return

        exe = find_rpc_binary()
        if not exe:
            print("[RPC 오류] rpc-server를 찾을 수 없습니다 (llama/ 폴더 확인)")
            return

        rpc_port = port
        try:
            rpc_env = os.environ.copy()
            # setdefault 가 아니라 강제 — 사용자 환경에 그래프 켜짐이 남아 있으면 RPC 불안정
            rpc_env["GGML_CUDA_DISABLE_GRAPHS"] = "1"
            # 로그의 GTX1650(Turing, 텐서코어 없음) 권장 경로 — 커널/그래프 이슈 완화
            rpc_env["GGML_CUDA_FORCE_MMQ"] = "1"
            rpc_proc = subprocess.Popen(
                [exe, "--host", "0.0.0.0", "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=rpc_env,
            )
            if rpc_proc.stdout:
                threading.Thread(
                    target=_rpc_stdout_drain, args=(rpc_proc.stdout,), daemon=True
                ).start()
            print(f"[RPC] rpc-server 시작됨 (port {port}, PID {rpc_proc.pid})")
        except Exception as e:
            print(f"[RPC 오류] {e}")


def stop_rpc():
    global rpc_proc
    with rpc_proc_lock:
        if rpc_proc and rpc_proc.poll() is None:
            rpc_proc.terminate()
            try:
                rpc_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                rpc_proc.kill()
            print("[RPC] rpc-server 중지됨")
        rpc_proc = None


def get_rpc_status():
    with rpc_proc_lock:
        running = rpc_proc is not None and rpc_proc.poll() is None
    return {"running": running, "port": rpc_port}


def process_commands(commands):
    for cmd in commands:
        if cmd.get("type") == "rpc":
            action = cmd.get("action")
            port = cmd.get("port", 50052)
            if action == "start":
                start_rpc(port)
            elif action == "stop":
                stop_rpc()
            else:
                print(f"[명령] 알 수 없는 RPC 액션: {action}")


# ── Stats collection ───────────────────────────

def collect_stats():
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()

    try:
        disk = psutil.disk_usage("C:\\" if platform.system() == "Windows" else "/")
    except Exception:
        disk = None

    return {
        "hostname": socket.gethostname(),
        "ip": get_local_ip(),
        "worker_id": get_worker_id(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": {
            "percent": cpu_percent,
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
        "rpc": get_rpc_status(),
        "software": get_software_snapshot(),
        "timestamp": time.time(),
    }


# ── Report loop ────────────────────────────────

def report_loop(master_url, interval):
    psutil.cpu_percent()
    time.sleep(0.5)

    while True:
        try:
            stats = collect_stats()
            data = json.dumps(stats).encode("utf-8")
            req = urllib.request.Request(
                f"{master_url}/api/report",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=5)
            resp_data = json.loads(resp.read().decode())

            commands = resp_data.get("commands", [])
            if commands:
                threading.Thread(target=process_commands, args=(commands,), daemon=True).start()

            rpc = stats["rpc"]
            rpc_str = f"RPC {'ON' if rpc['running'] else 'OFF'}:{rpc['port']}"
            print(
                f"[OK] 리포트 전송 | "
                f"CPU {stats['cpu']['percent']}% | "
                f"RAM {stats['ram']['percent']}% | "
                f"GPU {len(stats['gpus'])}개 | "
                f"{rpc_str}"
            )
        except urllib.error.URLError as e:
            print(f"[연결 실패] 마스터({master_url})에 연결할 수 없습니다: {e.reason}")
        except Exception as e:
            print(f"[오류] {e}")

        time.sleep(interval)


# ── Main ───────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AIRaid Worker Agent")
    parser.add_argument("--master", required=True, help="마스터 서버 주소 (예: http://192.168.0.104:5555)")
    parser.add_argument("--interval", type=int, default=3, help="리포트 전송 간격 - 초 (기본: 3)")
    parser.add_argument("--rpc-port", type=int, default=50052, help="RPC 서버 포트 (기본: 50052)")
    args = parser.parse_args()

    global rpc_port
    rpc_port = args.rpc_port

    master_url = args.master.rstrip("/")
    local_ip = get_local_ip()

    print("=" * 58)
    print("  AIRaid Worker - Distributed llama.cpp Worker")
    print("=" * 58)
    print(f"  내 IP      : {local_ip}")
    print(f"  워커 ID    : {get_worker_id()}")
    print(f"  마스터     : {master_url}")
    print(f"  전송 간격  : {args.interval}초")
    print(f"  RPC 포트   : {rpc_port}")
    print(f"  llama 폴더 : {LLAMA_DIR}")
    print("=" * 58)
    print()

    report_loop(master_url, args.interval)


if __name__ == "__main__":
    main()
