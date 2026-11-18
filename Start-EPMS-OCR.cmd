@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; $env:PSModulePath=Join-Path $PSHOME 'Modules'; if ((Get-ExecutionPolicy -List).Where({$_.ExecutionPolicy -ne 'Undefined'}).Count) { exit 1 }"
if not errorlevel 1 set "PSExecutionPolicyPreference=RemoteSigned"
for %%F in ("%~dp0*.exe") do if exist "%%~fF" start "" "%%~fF" %*
