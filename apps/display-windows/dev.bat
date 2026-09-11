@echo off
chcp 65001 >nul
echo [Dev] 启动开发模式 (dotnet run)...
cd /d "%~dp0"
dotnet run --project ExhibitionClient.csproj
pause
