@echo off
chcp 65001 >nul
title Launch OnePost Real Chrome (Port 9333)
set "PROFILE_DIR=%~dp0data\studio_chrome_profile"
if not exist "%PROFILE_DIR%" mkdir "%PROFILE_DIR%"

start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9333 ^
  --user-data-dir="%PROFILE_DIR%" ^
  --no-first-run ^
  --no-default-browser-check ^
  "https://creator.douyin.com/" ^
  "https://channels.weixin.qq.com/" ^
  "https://weibo.com/" ^
  "https://x.com/home"
