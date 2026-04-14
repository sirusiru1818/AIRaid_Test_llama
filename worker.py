"""
AIRaid Worker Agent
워커 PC에서 실행하여 시스템 정보를 마스터 서버로 전송합니다.
사용법: python worker.py --master http://192.168.0.104:5555
"""

import argparse
import json
import platform
import socket
import subprocess
import time
import urllib.request
import urllib.error

import psutil


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
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []

        gpus = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            parts = [p.strip() for p in line.split(",")]
            gpus.append(
                {
                    "index": int(parts[0]),
                    "name": parts[1],
                    "memory_total_mb": int(parts[2]),
                    "memory_used_mb": int(parts[3]),
                    "memory_free_mb": int(parts[4]),
                    "gpu_util_percent": int(parts[5]),
                    "temperature_c": int(parts[6]),
                }
            )
        return gpus
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return []


def collect_stats():
    cpu_percent = psutil.cpu_percent(interval=1)
    cpu_count_logical = psutil.cpu_count(logical=True)
    cpu_count_physical = psutil.cpu_count(logical=False)
    cpu_freq = psutil.cpu_freq()
    mem = psutil.virtual_memory()

    try:
        if platform.system() == "Windows":
            disk = psutil.disk_usage("C:\\")
        else:
            disk = psutil.disk_usage("/")
    except Exception:
        disk = None

    return {
        "hostname": socket.gethostname(),
        "ip": get_local_ip(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": {
            "percent": cpu_percent,
            "count_logical": cpu_count_logical,
            "count_physical": cpu_count_physical or cpu_count_logical,
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
            urllib.request.urlopen(req, timeout=5)
            print(
                f"[OK] 리포트 전송 | "
                f"CPU {stats['cpu']['percent']}% | "
                f"RAM {stats['ram']['percent']}% | "
                f"GPU {len(stats['gpus'])}개"
            )
        except urllib.error.URLError as e:
            print(f"[연결 실패] 마스터({master_url})에 연결할 수 없습니다: {e.reason}")
        except Exception as e:
            print(f"[오류] {e}")

        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="AIRaid Worker Agent")
    parser.add_argument(
        "--master",
        required=True,
        help="마스터 서버 주소 (예: http://192.168.0.104:5555)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3,
        help="리포트 전송 간격 - 초 (기본: 3)",
    )
    args = parser.parse_args()

    master_url = args.master.rstrip("/")
    local_ip = get_local_ip()

    print("=" * 50)
    print("  AIRaid Worker Agent")
    print("=" * 50)
    print(f"  내 IP     : {local_ip}")
    print(f"  마스터    : {master_url}")
    print(f"  전송 간격 : {args.interval}초")
    print("=" * 50)
    print()

    report_loop(master_url, args.interval)


if __name__ == "__main__":
    main()
