#!/bin/bash
# fullstack-agent: give your AI a full stack — memory, voice, face, hands.
# Copyright (C) 2026 Jared Rhodenizer
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Starts the agent's pieces, in the right order:
#   ai-visualizer (the face, opens in your browser)
#   barehands     (the hands, URL printed; open it when you want the board)
#   backtalk      (the voice, runs in this terminal; Ctrl-C stops EVERYTHING)
# Pieces you didn't install are skipped automatically.
#
#   ./start.sh          everything installed
#   ./start.sh voice    the voice and the face (no hands)
#   ./start.sh hands    the voice and the hands board (no face)

HERE="$(cd "$(dirname "$0")" && pwd)"
HOME_DIR="$(dirname "$HERE")"
MODE="${1:-all}"
PIDS=()

FACE_PORT=8790
HANDS_PORT=8794
MARKER="$HOME_DIR/barehands/state/.enabled"

cleanup() {
  trap - EXIT INT TERM
  for p in "${PIDS[@]}"; do kill "$p" 2>/dev/null; done
  echo
  echo "agent stopped."
}
trap cleanup EXIT INT TERM

# Does something already answer on this port? Asking before launching is what
# stops a second copy from appearing when you click the launcher twice.
# The servers themselves refuse to share a port too, so this is not the only
# guard -- it is what turns "it is already up" into a friendly line instead of
# a failed bind.
serving() {
  local port="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -sf -o /dev/null --max-time 3 "http://127.0.0.1:$port/config"
  else
    # No curl: ask the port directly. Weaker, because a stranger on the port
    # also "answers", but better than launching blind. The connect happens in a
    # subshell so the descriptor it opens cannot leak into the caller.
    (echo >"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1
  fi
}

echo "fullstack-agent: starting from $HOME_DIR"

if [ -d "$HOME_DIR/ai-visualizer" ] && [ "$MODE" != "hands" ]; then
  if serving "$FACE_PORT"; then
    echo "  face:  already running on :$FACE_PORT"
  else
    (cd "$HOME_DIR/ai-visualizer" && exec python3 server.py) &
    PIDS+=($!)
    echo "  face:  starting (your browser opens on the visualizer)"
  fi
fi

if [ -d "$HOME_DIR/barehands" ] && [ "$MODE" != "voice" ]; then
  # The marker is what tells a supervisor that the board was asked for, so it
  # repairs it rather than leaving a deliberately closed board alone.
  mkdir -p "$HOME_DIR/barehands/state"
  : > "$MARKER"
  if serving "$HANDS_PORT"; then
    echo "  hands: already running on :$HANDS_PORT"
  else
    (cd "$HOME_DIR/barehands" && exec python3 server.py) &
    PIDS+=($!)
    echo "  hands: starting (open the printed URL in Chrome when you want the board)"
  fi
fi

if [ -d "$HOME_DIR/backtalk" ]; then
  echo "  voice: starting (hold your talk key and speak; Ctrl-C here stops everything)"
  cd "$HOME_DIR/backtalk" && ./run.sh
  # The voice owns this terminal, so it is the thing that decides when the
  # session is over. Clear the marker here: the board is not wanted once the
  # conversation is finished.
  rm -f "$MARKER"
else
  echo
  echo "No voice installed; servers are up. Ctrl-C stops everything."
  wait
fi