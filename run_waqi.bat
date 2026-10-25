@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist raw mkdir raw
.venv\Scripts\python.exe -m aqi_hanoi.pipeline.scheduled_pipeline >> raw\log.txt 2>&1
if errorlevel 1 exit /b 1
endlocal