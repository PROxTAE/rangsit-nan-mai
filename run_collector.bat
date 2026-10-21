@echo off
REM Double-click to collect a snapshot every 5 minutes. Close the window or press Ctrl+C to stop.
chcp 65001 > nul
cd /d "%~dp0"
python collector.py
pause
