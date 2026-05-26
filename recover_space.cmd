@echo off
setlocal
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\windows_recover_disk_space.ps1" %*
endlocal
