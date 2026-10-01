@echo off
rem Runs at Windows logon: starts the Jarvis stack and the supervisor,
rem then opens the face. The supervisor keeps everything alive after this.
start "" cmd /c "ping -n 12 127.0.0.1 >nul"
cd /d "%USERPROFILE%\my-agent\fullstack-agent"
start "" py -3 supervisor.py
timeout /t 8 >nul
start "" "http://127.0.0.1:8790/faces/board/"
