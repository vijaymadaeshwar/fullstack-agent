@echo off
rem fullstack-agent: give your AI a full stack — memory, voice, face, hands.
rem Copyright (C) 2026 Jared Rhodenizer
rem
rem This program is free software: you can redistribute it and/or modify
rem it under the terms of the GNU Affero General Public License as published
rem by the Free Software Foundation, either version 3 of the License, or
rem (at your option) any later version.
rem
rem This program is distributed in the hope that it will be useful,
rem but WITHOUT ANY WARRANTY; without even the implied warranty of
rem MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
rem GNU Affero General Public License for more details.
rem
rem You should have received a copy of the GNU Affero General Public License
rem along with this program. If not, see <https://www.gnu.org/licenses/>.
rem
rem SPDX-License-Identifier: AGPL-3.0-or-later

rem Starts the agent's pieces. Each server gets its own window;
rem close the windows (or this one for the voice) to stop.
rem   start.bat          everything installed
rem   start.bat voice    the voice and the face (no hands)
rem   start.bat hands    the voice and the hands board (no face)

cd /d "%~dp0.."

rem Hands is opt-in for the supervisor, so ask it to watch: this marker is
rem the whole contract. The launcher writes it when the user opens the
rem board, and --stop clears it, so closing the board means what it says.
rem Writing it before the launch below also means a crash during startup
rem still gets repaired instead of left dead.
if exist "barehands\" (
  if not "%1"=="voice" (
    if not exist "barehands\state\" mkdir "barehands\state" >nul 2>&1
    >"barehands\state\.enabled" echo enabled
  )
)

rem Start a server only if it is not already answering. The launchers have
rem no such check themselves, so without this every click of a Desktop
rem shortcut started a second copy: the loser cannot bind the port, and on
rem Windows that can leave it alive, serving nothing and stealing requests
rem from the real one.
if exist "ai-visualizer\" if not "%1"=="hands" (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://127.0.0.1:8790/config; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
  if errorlevel 1 (
    echo   face:  starting
    start "agent face" cmd /c "cd ai-visualizer && run.bat"
  ) else (
    echo   face:  already running
  )
)

rem Both servers are started through their own run.bat, which finds a
rem working interpreter and holds its window if anything goes wrong. This
rem file deliberately does NOT hunt for Python itself: a clean Windows 11
rem answers to the name `python` with a Microsoft Store decoy that passes
rem `where` and then exits 9009, so the check has to run an interpreter
rem rather than locate one -- and that belongs in one place per repo, not
rem duplicated here where a standalone user would never see the fix.
if exist "barehands\" if not "%1"=="voice" (
  powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 http://127.0.0.1:8794/config; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1"
  if errorlevel 1 (
    echo   hands: starting
    start "agent hands" cmd /c "cd barehands && run.bat"
  ) else (
    echo   hands: already running
  )
)

if exist "backtalk\" (
  echo   voice: starting in this window. Close it to hang up.
  cd backtalk
  rem Self-repair: reconcile the voice line's packages before launch
  rem (fast when current; heals a half-installed environment).
  rem
  rem Its output is deliberately NOT hidden. This used to run quiet with
  rem everything sent to nul, so a first run downloaded a few hundred
  rem megabytes behind a completely blank screen. There is no way to tell
  rem that apart from frozen, and people reasonably assumed the worst.
  echo   voice: checking packages. The FIRST run downloads a few hundred MB
  echo          and can take several minutes. It is not stuck.
  uv sync --inexact
  rem Stop HERE if the packages could not be installed. This used to fall
  rem through to the launch and then blame backtalk's log for a failure
  rem that happened before backtalk ever ran, sending people to a healthy
  rem log file with nothing in it to find.
  if errorlevel 1 (
    echo.
    echo   The voice line's packages could not be installed, so it never
    echo   started. The reason is in the output above.
    echo.
    echo   This happened during setup, BEFORE the voice ran, so there is
    echo   nothing about it in backtalk\logs\backtalk.log.
    echo.
    pause
    exit /b 1
  )
  uv run python -m backtalk.main
  rem A clean goodbye exits 0 and the window may close. An error exits
  rem nonzero, and the window HOLDS so the message can be read.
  if errorlevel 1 (
    echo.
    echo   The voice line stopped with an error. The message is above.
    echo   The log lives in backtalk\logs\backtalk.log
    pause
  )
  rem Closing this window means the stack is done, so tell the supervisor to
  rem stop resurrecting it. Without this, "close the window" restarted the
  rem voice on the watchdog's next poll -- and hands with it.
  if exist "barehands\state\.enabled" del /q "barehands\state\.enabled" >nul 2>&1
)
