@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY_DIR=%~dp0python"
set "PY=%PY_DIR%\python.exe"
set "PIP=%PY_DIR%\Scripts\pip.exe"
set "PTH_FILE=%PY_DIR%\python312._pth"

:: ==========================================
::  환경 점검
:: ==========================================
:RECHECK
cls
echo ============================================
echo   AIRaid Master - 환경 점검
echo   경로: %~dp0
echo ============================================
echo.

set "PYTHON_OK=X"
set "PIP_OK=X"
set "PSUTIL_OK=X"
set "FLASK_OK=X"
set "NGROK_OK=X"

if exist "%PY%" set "PYTHON_OK=O"
if exist "%PIP%" set "PIP_OK=O"
if exist "%~dp0ngrok.exe" set "NGROK_OK=O"

if "%PYTHON_OK%"=="O" if "%PIP_OK%"=="O" (
    "%PY%" -c "import psutil" 2>nul && set "PSUTIL_OK=O"
    "%PY%" -c "import flask" 2>nul && set "FLASK_OK=O"
)

echo  [환경 점검 결과]
echo.
echo   [%PYTHON_OK%] Python 3.12 ......... 설치하려면 1 입력
echo   [%PIP_OK%] pip .................. 설치하려면 2 입력
echo   [%PSUTIL_OK%] psutil ............... 설치하려면 3 입력
echo   [%FLASK_OK%] flask ................. 설치하려면 4 입력
echo   [%NGROK_OK%] ngrok ................ 설치하려면 5 입력
echo.
echo   O = 설치됨 / X = 미설치
echo.
echo ============================================

if "%PYTHON_OK%"=="X" goto :MENU
if "%PIP_OK%"=="X" goto :MENU
if "%PSUTIL_OK%"=="X" goto :MENU
if "%FLASK_OK%"=="X" goto :MENU
if "%NGROK_OK%"=="X" goto :MENU

echo.
echo  모든 환경이 준비되었습니다!
echo.
echo  [Enter] 마스터 서버 시작
echo  [R] 다시 검사
echo  [0] 종료
echo.
set /p "READY=선택: "
if /i "%READY%"=="R" goto :RECHECK
if "%READY%"=="0" exit /b 0
goto :START_SERVER

:: ==========================================
::  설치 메뉴
:: ==========================================
:MENU
echo.
echo  [A] 미설치 항목 모두 자동 설치
echo  [R] 다시 검사
echo  [0] 종료
echo.
set /p "CHOICE=선택: "

if "%CHOICE%"=="0" exit /b 0
if /i "%CHOICE%"=="R" goto :RECHECK
if /i "%CHOICE%"=="A" goto :DO_ALL
if "%CHOICE%"=="1" goto :DO_PYTHON_ONLY
if "%CHOICE%"=="2" goto :DO_PIP_ONLY
if "%CHOICE%"=="3" goto :DO_PSUTIL_ONLY
if "%CHOICE%"=="4" goto :DO_FLASK_ONLY
if "%CHOICE%"=="5" goto :DO_NGROK_ONLY
echo [오류] 잘못된 입력입니다.
pause
goto :RECHECK

:DO_PYTHON_ONLY
call :INSTALL_PYTHON
goto :AFTER_INSTALL
:DO_PIP_ONLY
call :INSTALL_PIP
goto :AFTER_INSTALL
:DO_PSUTIL_ONLY
call :INSTALL_PKG psutil
goto :AFTER_INSTALL
:DO_FLASK_ONLY
call :INSTALL_PKG flask
goto :AFTER_INSTALL
:DO_NGROK_ONLY
call :INSTALL_NGROK
goto :AFTER_INSTALL

:DO_ALL
if "%PYTHON_OK%"=="X" call :INSTALL_PYTHON
if "%PIP_OK%"=="X" call :INSTALL_PIP
if "%PSUTIL_OK%"=="X" call :INSTALL_PKG psutil
if "%FLASK_OK%"=="X" call :INSTALL_PKG flask
if "%NGROK_OK%"=="X" call :INSTALL_NGROK
goto :AFTER_INSTALL

:AFTER_INSTALL
echo.
echo ============================================
echo  설치 완료! 환경을 다시 점검합니다...
echo ============================================
timeout /t 2 /nobreak >nul
goto :RECHECK

:: ==========================================
::  Python 포터블 설치
:: ==========================================
:INSTALL_PYTHON
if "%PYTHON_OK%"=="O" goto :eof
echo.
echo [설치] Python 3.12 포터블 다운로드 중...
powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-embed-amd64.zip' -OutFile '%~dp0python.zip'"
if not exist "%~dp0python.zip" (
    echo [오류] Python 다운로드 실패
    pause
    goto :eof
)
echo [설치] 압축 해제 중...
powershell -Command "Expand-Archive -Path '%~dp0python.zip' -DestinationPath '%PY_DIR%' -Force"
del "%~dp0python.zip" 2>nul
if not exist "%PY%" (
    echo [오류] Python 압축 해제 실패
    pause
    goto :eof
)

echo [설치] pip 사용을 위해 경로 설정 중...
if exist "%PTH_FILE%" (
    powershell -Command "(Get-Content '%PTH_FILE%') -replace '#import site','import site' | Set-Content '%PTH_FILE%'"
    findstr /C:"import site" "%PTH_FILE%" >nul 2>nul
    if errorlevel 1 (
        echo import site>> "%PTH_FILE%"
    )
)
echo [설치] Python 설치 완료!
set "PYTHON_OK=O"
call :INSTALL_PIP
goto :eof

:: ==========================================
::  pip 설치
:: ==========================================
:INSTALL_PIP
if exist "%PIP%" goto :eof
echo.
echo [설치] get-pip.py 다운로드 중...
powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile '%~dp0get-pip.py'"
if not exist "%~dp0get-pip.py" (
    echo [오류] get-pip.py 다운로드 실패
    pause
    goto :eof
)
echo [설치] pip 설치 중...
"%PY%" "%~dp0get-pip.py" --no-warn-script-location
del "%~dp0get-pip.py" 2>nul
if not exist "%PIP%" (
    echo [오류] pip 설치 실패
    pause
    goto :eof
)
echo [설치] pip 설치 완료!
set "PIP_OK=O"
goto :eof

:: ==========================================
::  패키지 설치
:: ==========================================
:INSTALL_PKG
echo.
echo [설치] %~1 설치 중...
"%PY%" -m pip install %~1 --no-warn-script-location
if errorlevel 1 (
    echo [오류] %~1 설치 실패
    pause
    goto :eof
)
echo [설치] %~1 설치 완료!
goto :eof

:: ==========================================
::  ngrok 설치
:: ==========================================
:INSTALL_NGROK
if "%NGROK_OK%"=="O" goto :eof
echo.
echo [설치] ngrok 다운로드 중...
powershell -Command "Invoke-WebRequest -Uri 'https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip' -OutFile '%~dp0ngrok.zip'"
if not exist "%~dp0ngrok.zip" (
    echo [오류] ngrok 다운로드 실패
    pause
    goto :eof
)
echo [설치] 압축 해제 중...
powershell -Command "Expand-Archive -Path '%~dp0ngrok.zip' -DestinationPath '%~dp0' -Force"
del "%~dp0ngrok.zip" 2>nul
if not exist "%~dp0ngrok.exe" (
    echo [오류] ngrok 압축 해제 실패
    pause
    goto :eof
)
echo [설치] ngrok 설치 완료!
goto :eof

:: ==========================================
::  마스터 서버 시작
:: ==========================================
:START_SERVER
cls
echo.
echo ============================================
echo  서버 시작 중...
echo ============================================
echo.

set "PORT=5555"
set "NGROK=%~dp0ngrok.exe"

:: 포트 점유 프로세스 강제 종료
echo        포트 %PORT% 점유 확인 중...
powershell -Command "Get-NetTCPConnection -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { echo ('        기존 프로세스 종료 (PID: ' + $_.OwningProcess + ')'); Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }" 2>nul
timeout /t 1 /nobreak >nul

:: [1/3] ngrok 토큰 설정
echo [1/3] ngrok 토큰 설정 중...
"%NGROK%" config add-authtoken 3CKRcSgc9iy2Rwzphk44chnEItv_26va3UN7S8YJnMT3orQ5A
echo        완료!
echo.

:: [2/3] 마스터 서버 실행
echo [2/3] 마스터 서버 실행 중...
start "MasterServer" /min "%PY%" "%~dp0master.py" --port %PORT%
timeout /t 3 /nobreak >nul
echo        완료! (http://localhost:%PORT%)
echo.

:: [3/3] ngrok 실행
echo [3/3] ngrok 터널 시작 중...
echo.
echo ============================================
echo  모든 준비 완료!
echo  아래 Forwarding URL을 브라우저에서 열면
echo  대시보드에 접속됩니다.
echo.
echo  워커 연결: 각 워커 PC에서
echo  start_worker.bat 실행 후 마스터 IP 입력
echo ============================================
echo.
"%NGROK%" http %PORT%

echo.
echo ngrok이 종료되었습니다.
echo 아무 키나 누르면 창을 닫습니다...
pause >nul
