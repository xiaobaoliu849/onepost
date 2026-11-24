@echo off
chcp 65001 >nul
title OnePost - Douyin / Shipinhao / Weibo / X
cd /d "%~dp0"
echo ================================================================
echo   OnePost - one click, post everywhere
echo   http://127.0.0.1:8765
echo ================================================================
start "" "http://127.0.0.1:8765"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
pause
