@echo off
setlocal
if not exist "%~dp0EPMS.exe" (
    echo EPMS.exe was not found. Extract the entire ZIP and try again.
    pause
    exit /b 1
)
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; $env:PSModulePath=Join-Path $PSHOME 'Modules'; if ((Get-ExecutionPolicy -List).Where({$_.ExecutionPolicy -ne 'Undefined'}).Count) { exit 1 }"
if not errorlevel 1 set "PSExecutionPolicyPreference=RemoteSigned"
start "" "%~dp0EPMS.exe" %*
