"""
AIRaid Master Server
마스터 PC에서 실행하여 워커들의 시스템 상태를 수집하고 대시보드를 제공합니다.
사용법: python master.py [--port 5555]
"""

import argparse
import os
import platform
import socket
import subprocess
import sys
import threading
import time

import psutil
from flask import Flask, jsonify, request, send_file

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, "index.html")

app = Flask(__name__)

workers = {}
workers_lock = threading.Lock()
WORKER_TIMEOUT = 15


# ──────────────────────────────────────────────
#  시스템 정보 수집
# ──────────────────────────────────────────────

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


master_stats = {}
master_stats_lock = threading.Lock()


def collect_master_loop():
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
                    "total_gb": round(mem.total / (1024**3), 2),
                    "used_gb": round(mem.used / (1024**3), 2),
                    "percent": mem.percent,
                },
                "disk": {
                    "total_gb": round(disk.total / (1024**3), 1) if disk else 0,
                    "used_gb": round(disk.used / (1024**3), 1) if disk else 0,
                    "percent": round(disk.percent, 1) if disk else 0,
                },
                "gpus": get_gpu_info(),
                "timestamp": time.time(),
            }
            with master_stats_lock:
                master_stats.update(stats)
        except Exception as e:
            print(f"[마스터 수집 오류] {e}")
        time.sleep(2)


# ──────────────────────────────────────────────
#  API 라우트
# ──────────────────────────────────────────────

@app.route("/")
def dashboard():
    return send_file(HTML_PATH)


@app.route("/api/report", methods=["POST"])
def receive_report():
    data = request.get_json(force=True)
    ip = data.get("ip", request.remote_addr)
    data["role"] = "worker"
    with workers_lock:
        workers[ip] = data
    return jsonify({"status": "ok"})


@app.route("/api/stats")
def get_all_stats():
    now = time.time()
    with master_stats_lock:
        master = dict(master_stats)

    with workers_lock:
        active = {}
        stale_keys = []
        for ip, stats in workers.items():
            if now - stats.get("timestamp", 0) < WORKER_TIMEOUT:
                active[ip] = stats
            else:
                stale_keys.append(ip)
        for k in stale_keys:
            del workers[k]

    return jsonify({"master": master, "workers": active})


# ──────────────────────────────────────────────
#  메인
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AIRaid Master Server")
    parser.add_argument("--port", type=int, default=5555, help="서버 포트 (기본: 5555)")
    args = parser.parse_args()

    local_ip = get_local_ip()

    if not os.path.exists(HTML_PATH):
        print(f"[오류] index.html을 찾을 수 없습니다: {HTML_PATH}")
        sys.exit(1)

    threading.Thread(target=collect_master_loop, daemon=True).start()

    print("=" * 50)
    print("  AIRaid Master Server")
    print("=" * 50)
    print(f"  대시보드  : http://{local_ip}:{args.port}")
    print(f"  워커 연결 : python worker.py --master http://{local_ip}:{args.port}")
    print("=" * 50)
    print()

    app.run(host="0.0.0.0", port=args.port, debug=False)


if __name__ == "__main__":
    main()
