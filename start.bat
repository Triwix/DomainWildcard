@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 scripts\quickstart.py %*
) else (
  python scripts\quickstart.py %*
)
endlocal
