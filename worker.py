"""
AIRaid Worker Agent - Distributed llama.cpp Worker
워커 PC에서 실행: 시스템 모니터링 + RPC 서버 관리
사용법: python worker.py --master http://192.168.0.104:5555
"""

import argparse
import json
import os
import platform
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

import psutil

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LLAMA_DIR = os.path.join(BASE_DIR, "llama")

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
            rpc_proc = subprocess.Popen(
                [exe, "--host", "0.0.0.0", "--port", str(port)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
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
    print(f"  마스터     : {master_url}")
    print(f"  전송 간격  : {args.interval}초")
    print(f"  RPC 포트   : {rpc_port}")
    print(f"  llama 폴더 : {LLAMA_DIR}")
    print("=" * 58)
    print()

    report_loop(master_url, args.interval)


if __name__ == "__main__":
    main()
