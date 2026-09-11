@echo off
chcp 65001 >nul
echo ========================================
echo   展厅展示端 C# WinForms 构建脚本
echo ========================================
echo.

:: 检查 .NET SDK
dotnet --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未安装 .NET SDK
    echo 请从 https://dotnet.microsoft.com/download 下载安装
    pause
    exit /b 1
)

echo [.NET 版本]
dotnet --version
echo.

:: 还原依赖
echo [1/3] 还原 NuGet 包...
dotnet restore
if errorlevel 1 (
    echo [错误] 还原失败
    pause
    exit /b 1
)
echo.

:: 构建
echo [2/3] 编译项目...
dotnet build -c Release
if errorlevel 1 (
    echo [错误] 编译失败
    pause
    exit /b 1
)
echo.

:: 发布
echo [3/3] 发布 exe 文件...
dotnet publish -c Release -r win-x64 --self-contained true -o ./publish
if errorlevel 1 (
    echo [错误] 发布失败
    pause
    exit /b 1
)
echo.

echo ========================================
echo   构建完成！
echo   输出目录: %~dp0publish
echo ========================================
echo.
echo 按任意键打开输出目录...
pause >nul
explorer ./publish
