@echo off
chcp 65001 >nul
title Launch OnePost Real Chrome (Port 9333)
set "PROFILE_DIR=%~dp0data\studio_chrome_profile"
if not exist "%PROFILE_DIR%" mkdir "%PROFILE_DIR%"

set "BROWSER_EXE="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" set "BROWSER_EXE=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_EXE if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" set "BROWSER_EXE=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_EXE if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" set "BROWSER_EXE=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
if not defined BROWSER_EXE if exist "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" set "BROWSER_EXE=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not defined BROWSER_EXE if exist "C:\Program Files\Microsoft\Edge\Application\msedge.exe" set "BROWSER_EXE=C:\Program Files\Microsoft\Edge\Application\msedge.exe"

if not defined BROWSER_EXE (
  echo [OnePost] Could not find Google Chrome or Microsoft Edge.
  echo Please configure your browser executable path in OnePost Settings.
  pause
  exit /b 1
)

start "" "%BROWSER_EXE%" ^
  --remote-debugging-port=9333 ^
  --user-data-dir="%PROFILE_DIR%" ^
  --no-first-run ^
  --no-default-browser-check ^
  "https://creator.douyin.com/" ^
  "https://channels.weixin.qq.com/" ^
  "https://weibo.com/" ^
  "https://x.com/home"
