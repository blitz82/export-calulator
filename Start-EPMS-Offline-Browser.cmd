@echo off
setlocal
cd /d "%~dp0"
set "EPMS_SORTING_ENABLED=1"
if exist "%~dp0EPMS.exe" (
  call "%~dp0Start-EPMS-Portable.cmd" --browser %*
  exit /b
)
if exist "%~dp0Start-EPMS-OCR.cmd" (
  call "%~dp0Start-EPMS-OCR.cmd" --browser %*
  exit /b
)
echo Extract the entire ZIP before running this launcher.
pause
