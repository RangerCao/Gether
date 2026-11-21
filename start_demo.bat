@echo off
setlocal
cd /d "%~dp0"
set MSYS_NO_PATHCONV=1

where bash >nul 2>nul
if %errorlevel%==0 (
  bash scripts/start_demo.sh start
) else (
  if exist "C:\Program Files\Git\bin\bash.exe" (
    "C:\Program Files\Git\bin\bash.exe" scripts/start_demo.sh start
  ) else (
    echo [Gether] 未找到 bash，请安装 Git for Windows 后重试。
  )
)

if errorlevel 1 (
  echo.
  echo [Gether] 启动失败，请查看 .run\logs 下的日志。
)
pause
