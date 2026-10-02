@echo off
rem fullstack-agent: run the test suites in the right order
cd /d "%~dp0.."
echo === fullstack-agent/tests/test_supervisor.py ===
py -3 fullstack-agent\tests\test_supervisor.py
if errorlevel 1 exit /b %errorlevel%
cd backtalk
uv sync >nul 2>&1
echo.
echo === backtalk/tests/test_journal.py ===
uv run python tests\test_journal.py
if errorlevel 1 exit /b %errorlevel%
echo.
echo === backtalk/tests/test_stt_langs.py (heavy) ===
uv run python tests\test_stt_langs.py
if errorlevel 1 exit /b %errorlevel%
echo.
echo === backtalk/tests/test_espeak_fallback.py (heavy) ===
uv run python tests\test_espeak_fallback.py
if errorlevel 1 exit /b %errorlevel%
echo.
echo === backtalk/tests/test_e2e.py (last) ===
uv run python tests\test_e2e.py
if errorlevel 1 exit /b %errorlevel%
if not "%1"=="" (
  echo.
  echo === backtalk/tests/test_live_path.py %1 ===
  uv run python tests\test_live_path.py %1
  if errorlevel 1 exit /b %errorlevel%
)
echo.
echo === ALL TESTS PASSED ===
