# AIRaid Monitor

마스터-워커 구조의 실시간 시스템 모니터링 대시보드입니다.
같은 네트워크(LAN) 내의 여러 컴퓨터의 CPU, RAM, GPU, 디스크 사용량을 실시간으로 확인하고,
ngrok을 통해 외부에서도 접속할 수 있습니다.

## 구조

```
마스터 (1대)                    워커 (N대)
┌──────────────┐        ┌──────────────┐
│ master.py    │◄───────│ worker.py    │  ← 3초마다 상태 전송
│  - 대시보드   │        └──────────────┘
│  - API 서버   │        ┌──────────────┐
│  - ngrok 터널 │◄───────│ worker.py    │
└──────────────┘        └──────────────┘
    ▲                        ...
    │
 브라우저 접속
 LAN: http://마스터IP:5555
 외부: https://xxxx-xxxx.ngrok-free.app
```

## 사전 준비 (Windows)

- git이 없다면: `winget install --id Git.Git -e --source winget`
- 윈도우 설정 > 앱 > 검색(앱 별칭) > python3, python 관련된 거 다 끄기

## 사용법

### 1. 마스터 PC에서

`start_master.bat` 더블클릭

- Python, pip, psutil, flask, ngrok이 없으면 자동 설치 (포터블)
- `[A]` 입력하면 미설치 항목 모두 자동 설치
- 모두 설치되면 Enter로 서버 + ngrok 터널 시작

### 2. 각 워커 PC에서

`start_worker.bat` 더블클릭

- Python, pip, psutil이 없으면 자동 설치 (포터블)
- 마스터 IP 입력 (예: `192.168.0.104`)
- Enter로 워커 시작

### 3. 대시보드 확인

- **LAN 접속:** `http://마스터IP:5555`
- **외부 접속:** ngrok 실행 후 표시되는 `https://xxxx-xxxx.ngrok-free.app` URL

## 사용법 (수동)

Python이 이미 설치된 환경이라면 직접 실행할 수도 있습니다.

```bash
pip install -r requirements.txt

# 마스터
python master.py                                          # 기본 포트 5555
python master.py --port 8888                              # 포트 변경

# 워커
python worker.py --master http://192.168.0.104:5555       # 기본 3초 간격
python worker.py --master http://192.168.0.104:5555 --interval 5  # 간격 변경
```

## 모니터링 항목

| 항목 | 세부 내용 |
|------|----------|
| CPU | 사용률(%), 코어 수, 스레드 수, 클럭 속도 |
| RAM | 사용량/전체(GB), 사용률(%) |
| Disk | 사용량/전체(GB), 사용률(%) |
| GPU | 이름, VRAM 사용량/전체(MB), 사용률(%), 온도(°C) |

## GPU 모니터링

NVIDIA GPU가 있는 경우 `nvidia-smi`를 통해 자동으로 감지합니다.
GPU가 없거나 `nvidia-smi`가 설치되어 있지 않으면 GPU 항목은 "감지 불가"로 표시됩니다.

## 방화벽 설정

마스터 PC에서 포트 5555(또는 지정 포트)이 열려 있어야 합니다.

**Windows:**
```powershell
netsh advfirewall firewall add rule name="AIRaid Monitor" dir=in action=allow protocol=TCP localport=5555
```

**Linux:**
```bash
sudo ufw allow 5555/tcp
```

## 종료 방법

1. ngrok 터미널에서 `Ctrl + C`
2. 마스터 서버는 자동 종료 (또는 작업 관리자에서 python 프로세스 종료)
3. 워커 터미널에서 `Ctrl + C`
