@echo off
rem Starts the AI companion with a double click: the companion server, the tunnel and, if companion.ini asks for it,
rem the XTTS server (see companion_start.py). It takes the Python of MantellaEnv if that folder is there.
cd /d "%~dp0"
if exist "MantellaEnv\Scripts\python.exe" (
    "MantellaEnv\Scripts\python.exe" companion_start.py %*
) else (
    python companion_start.py %*
)
pause
