# AIRaid - Distributed llama.cpp

llama.cpp의 RPC 분산 추론을 활용한 **마스터-워커 구조**의 분산 LLM 시스템입니다.
여러 대의 PC에 분산된 GPU/CPU를 하나로 묶어 대형 모델을 실행할 수 있습니다.

## 아키텍처

```
마스터 PC                              워커 PC (N대)
┌─────────────────────────┐           ┌──────────────────┐
│ master.py (Flask)       │           │ worker.py        │
│  ├ 웹 대시보드          │◄──report──│  ├ 시스템 모니터  │
│  ├ llama-server 관리    │──cmds───►│  └ rpc-server 관리│
│  ├ 모델 관리            │           │                  │
│  └ 채팅 프록시          │           │ rpc-server       │
│                         │           │  (GPU/CPU 공유)  │
│ llama-server            │◄──RPC────│                  │
│  (--rpc w1:50052,...)   │           └──────────────────┘
│  (모델 로드 + 추론)     │                  ...
└─────────────────────────┘
         ▲
         │
    브라우저 (대시보드)
     ├ 모니터링 탭  - 전체 시스템 실시간 모니터링
     ├ LLM 서버 탭 - 모델/서버/워커 RPC 제어
     └ 채팅 탭     - 스트리밍 채팅 인터페이스
```

## 핵심 기능

| 기능 | 설명 |
|------|------|
| 분산 추론 | llama.cpp RPC로 여러 PC의 GPU를 묶어 추론 |
| 웹 제어판 | 모델 선택, 서버 시작/중지, 파라미터 설정 |
| 워커 RPC 원격 제어 | 대시보드에서 각 워커의 RPC 서버 시작/중지 |
| 실시간 모니터링 | CPU, RAM, GPU, 디스크 사용량 실시간 확인 |
| 스트리밍 채팅 | OpenAI 호환 API를 통한 실시간 채팅 |
| 서버 로그 | llama-server 출력 실시간 확인 |
| 자동 설치 | bat 파일로 Python, llama.cpp 등 자동 설치 |

## 빠른 시작 (Windows)

### 1. 마스터 PC

1. `start_master.bat` 더블클릭
2. `[A]` 입력하여 미설치 항목 모두 설치 (Python, pip, Flask, llama.cpp 등)
3. llama.cpp 설치 시 GPU 백엔드 선택 (CUDA/Vulkan/CPU)
4. 모든 항목 설치 후 Enter로 서버 시작
5. `models/` 폴더에 `.gguf` 모델 파일 배치

### 2. 워커 PC (각각)

1. `start_worker.bat` 더블클릭
2. `[A]` 입력하여 미설치 항목 모두 설치
3. 마스터 IP 입력 (예: `192.168.0.104`)
4. Enter로 워커 시작

### 3. 대시보드에서 제어

1. 브라우저에서 `http://마스터IP:5555` 접속
2. **LLM 서버** 탭 이동
3. 워커 RPC 시작 (워커 옆 "시작" 버튼)
4. 모델 선택 + 파라미터 설정
5. "서버 시작" 클릭 → 모델 로딩 (RPC 워커 자동 연결)
6. **채팅** 탭에서 대화

## 폴더 구조

```
AIRaid_llama/
├── models/          ← .gguf 모델 파일을 여기에 배치
├── llama/           ← llama.cpp 바이너리 (자동 설치)
│   ├── llama-server.exe
│   ├── rpc-server.exe
│   └── ...
├── master.py        ← 마스터 서버
├── worker.py        ← 워커 에이전트
├── index.html       ← 웹 대시보드
├── start_master.bat ← 마스터 시작 스크립트
├── start_worker.bat ← 워커 시작 스크립트
└── requirements.txt
```

## 수동 실행

Python이 이미 설치된 환경에서는 직접 실행할 수 있습니다.

```bash
pip install -r requirements.txt

# 마스터
python master.py --port 5555

# 워커
python worker.py --master http://192.168.0.104:5555 --rpc-port 50052
```

## 분산 추론 원리

1. 각 워커가 `rpc-server`를 실행하면 해당 PC의 GPU/CPU 연산 자원을 네트워크에 공유합니다.
2. 마스터의 `llama-server`가 `--rpc worker1:50052,worker2:50052,...` 옵션으로 시작되면, 모델의 레이어를 각 워커에 자동 분배합니다.
3. 예: 70B 모델을 4대의 24GB GPU에 분산하여 실행 가능

## API 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|-----------|--------|------|
| `/` | GET | 웹 대시보드 |
| `/api/stats` | GET | 전체 시스템 통계 + llama 상태 |
| `/api/models` | GET | 사용 가능한 모델 목록 |
| `/api/llama/status` | GET | llama-server 상태 + 로그 |
| `/api/llama/start` | POST | llama-server 시작 |
| `/api/llama/stop` | POST | llama-server 중지 |
| `/api/llama/logs` | GET | llama-server 로그 |
| `/api/llama/chat` | POST | 채팅 (OpenAI 호환 프록시) |
| `/api/workers/<ip>/rpc` | POST | 워커 RPC 원격 제어 |
| `/api/report` | POST | 워커 상태 리포트 수신 |

## 모니터링 항목

| 항목 | 세부 내용 |
|------|----------|
| CPU | 사용률(%), 코어 수, 스레드 수, 클럭 속도 |
| RAM | 사용량/전체(GB), 사용률(%) |
| Disk | 사용량/전체(GB), 사용률(%) |
| GPU | 이름, VRAM 사용량/전체(MB), 사용률(%), 온도(°C) |
| RPC | 워커별 RPC 서버 상태 (ON/OFF), 포트 |

## 방화벽 설정

마스터와 워커 간 통신에 필요한 포트:

| 포트 | 용도 |
|------|------|
| 5555 | 마스터 웹 서버 + API |
| 50052 | 워커 RPC 서버 (워커마다) |

```powershell
# Windows
netsh advfirewall firewall add rule name="AIRaid Master" dir=in action=allow protocol=TCP localport=5555
netsh advfirewall firewall add rule name="AIRaid RPC" dir=in action=allow protocol=TCP localport=50052
```

```bash
# Linux
sudo ufw allow 5555/tcp
sudo ufw allow 50052/tcp
```

## 종료 방법

1. 대시보드에서 "서버 중지" 클릭
2. ngrok 터미널에서 `Ctrl + C`
3. 워커 터미널에서 `Ctrl + C`
