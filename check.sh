#!/bin/bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
echo "=== fullstack-agent/tests/test_supervisor.py ==="
py -3 "$ROOT/fullstack-agent/tests/test_supervisor.py"
cd "$ROOT/backtalk"
uv sync >/dev/null 2>&1
echo ""
echo "=== backtalk/tests/test_journal.py ==="
uv run python tests/test_journal.py
echo ""
echo "=== backtalk/tests/test_wake.py ==="
uv run python tests/test_wake.py
echo ""
echo "=== backtalk/tests/test_lang_detect.py ==="
uv run python tests/test_lang_detect.py
echo ""
echo "=== backtalk/tests/test_turn_errors.py ==="
uv run python tests/test_turn_errors.py
echo ""
echo "=== backtalk/tests/test_bus_park.py ==="
uv run python tests/test_bus_park.py
echo ""
echo "=== backtalk/tests/test_encoding.py ==="
uv run python tests/test_encoding.py
echo ""
echo "=== backtalk/tests/test_stt_langs.py (heavy) ==="
uv run python tests/test_stt_langs.py
echo ""
echo "=== backtalk/tests/test_espeak_fallback.py (heavy) ==="
uv run python tests/test_espeak_fallback.py
echo ""
echo "=== backtalk/tests/test_e2e.py (last) ==="
uv run python tests/test_e2e.py
if [ $# -ge 1 ]; then
  echo ""
  echo "=== backtalk/tests/test_live_path.py $* ==="
  uv run python tests/test_live_path.py "$@"
fi
echo ""
echo "=== ALL TESTS PASSED ==="