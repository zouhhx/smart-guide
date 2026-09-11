@echo off
cd /d "D:\exhibition-hall-new\runtime\exhibition-hall-platform"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\exhibition-hall-new\runtime\exhibition-hall-platform\scripts\start-tts-service.ps1" >> tts-service.log 2>> tts-service.err.log
