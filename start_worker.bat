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
echo   AIRaid Worker - 환경 점검
echo   경로: %~dp0
echo ============================================
echo.

set "PYTHON_OK=X"
set "PIP_OK=X"
set "PSUTIL_OK=X"

if exist "%PY%" set "PYTHON_OK=O"
if exist "%PIP%" set "PIP_OK=O"

if "%PYTHON_OK%"=="O" if "%PIP_OK%"=="O" (
    "%PY%" -c "import psutil" 2>nul && set "PSUTIL_OK=O"
)

echo  [환경 점검 결과]
echo.
echo   [%PYTHON_OK%] Python 3.12 ......... 설치하려면 1 입력
echo   [%PIP_OK%] pip .................. 설치하려면 2 입력
echo   [%PSUTIL_OK%] psutil ............... 설치하려면 3 입력
echo.
echo   O = 설치됨 / X = 미설치
echo.
echo ============================================

if "%PYTHON_OK%"=="X" goto :MENU
if "%PIP_OK%"=="X" goto :MENU
if "%PSUTIL_OK%"=="X" goto :MENU

echo.
echo  모든 환경이 준비되었습니다!
echo.
echo  [Enter] 워커 시작
echo  [R] 다시 검사
echo  [0] 종료
echo.
set /p "READY=선택: "
if /i "%READY%"=="R" goto :RECHECK
if "%READY%"=="0" exit /b 0
goto :START_WORKER

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

:DO_ALL
if "%PYTHON_OK%"=="X" call :INSTALL_PYTHON
if "%PIP_OK%"=="X" call :INSTALL_PIP
if "%PSUTIL_OK%"=="X" call :INSTALL_PKG psutil
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
::  워커 시작
:: ==========================================
:START_WORKER
cls
echo.
echo ============================================
echo   AIRaid Worker Agent 시작
echo ============================================
echo.

if defined MASTER_ADDR goto :SKIP_INPUT
echo  마스터 서버 주소를 입력하세요.
echo  (예: 192.168.0.104)
echo.
set /p "MASTER_IP=마스터 IP: "
if "%MASTER_IP%"=="" (
    echo [오류] IP를 입력해주세요.
    pause
    goto :START_WORKER
)
set "MASTER_ADDR=http://%MASTER_IP%:5555"

:SKIP_INPUT
echo.
echo  마스터 주소: %MASTER_ADDR%
echo.

set "INTERVAL=3"
echo  전송 간격: %INTERVAL%초 (기본값)
echo  (변경하려면 숫자 입력, Enter=기본값)
echo.
set /p "NEW_INTERVAL=전송 간격(초): "
if not "%NEW_INTERVAL%"=="" set "INTERVAL=%NEW_INTERVAL%"

echo.
echo ============================================
echo  워커를 시작합니다... (종료: Ctrl+C)
echo  마스터: %MASTER_ADDR%
echo  간격  : %INTERVAL%초
echo ============================================
echo.
"%PY%" "%~dp0worker.py" --master %MASTER_ADDR% --interval %INTERVAL%

echo.
echo  워커가 종료되었습니다.
echo  아무 키나 누르면 창을 닫습니다...
pause >nul
