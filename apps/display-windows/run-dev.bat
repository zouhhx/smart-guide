@echo off
chcp 65001 >nul
echo 正在启动展厅展示端...
echo.

:: 检查 .NET SDK
dotnet --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未安装 .NET SDK
    echo 请从 https://dotnet.microsoft.com/download 下载 .NET 8.0 SDK
    pause
    exit /b 1
)

:: 检查是否已编译
if not exist "bin\Debug\net8.0-windows\ExhibitionClient.exe" (
    echo 首次运行，正在编译...
    dotnet build
    echo.
)

echo 启动中...
echo.
dotnet run
pause
