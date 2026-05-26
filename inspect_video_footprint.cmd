@echo off
setlocal
"%~dp0venv\Scripts\python.exe" "%~dp0scripts\analyze_windows_video_footprint.py" %*
endlocal
