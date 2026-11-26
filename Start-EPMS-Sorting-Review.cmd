@echo off
setlocal
cd /d "%~dp0"
set "EPMS_SORTING_ENABLED=1"
if exist "EPMS.exe" (
  call "%~dp0Start-EPMS-Portable.cmd" --db "%~dp0data\sorting-review.db" %*
  exit /b
)
if exist "Start-EPMS-OCR.cmd" (
  call "%~dp0Start-EPMS-OCR.cmd" --db "%~dp0data\sorting-review.db" %*
  exit /b
)
echo Run this launcher from the extracted validation package folder.
pause
