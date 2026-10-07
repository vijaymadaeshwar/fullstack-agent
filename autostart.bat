@echo off
rem Runs at Windows logon: starts the Seyon stack and the supervisor,
rem then opens the face. The supervisor keeps everything alive after this.
rem
rem The supervisor takes a lock file, so it is safe to run this AND
rem "Start Seyon.bat" at the same time -- the second one exits and says so
rem in supervisor.log rather than fighting over port 4599.
start "" cmd /c "ping -n 12 127.0.0.1 >nul"
cd /d "%USERPROFILE%\my-agent\fullstack-agent"
rem stderr goes to a file, not to this window: a traceback printed to a
rem console nobody is watching is the same as no traceback, and that is
rem how a dead supervisor spent half a day unexplained. stdout stays on
rem the window so there is still something to see while it is healthy.
start "" cmd /c "py -3 supervisor.py 2>>%USERPROFILE%\my-agent\supervisor.stderr.log"
timeout /t 8 >nul
start "" "http://127.0.0.1:8790/faces/board/"
