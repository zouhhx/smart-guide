@echo off
cd /d "D:\exhibition-hall-new\runtime\exhibition-hall-platform"
"C:\Program Files\nodejs\node.exe" .\src\index.js >> central-task.log 2>>&1
