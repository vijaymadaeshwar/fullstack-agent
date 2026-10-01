"""supervisor.py - keeps the Jarvis stack alive so it just works.

Checks, on a loop:
  1. the opencode brain   (port 4599)
  2. the ai-visualizer face (port 8790)
  3. the backtalk voice
  4. a turn that is stuck in "thinking" far too long

Anything missing or wedged is restarted, and stale duplicates or
port squatters are cleared first so two things never fight over the
same port or the same signal bus.

  py -3 supervisor.py            run the loop in the foreground
  py -3 supervisor.py --once     check and repair a single time, then exit
  py -3 supervisor.py --stop     stop the voice and the face
"""
from __future__ import annotations

import argparse
import json
import msvcrt
import os
import socket
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
AGENT = os.path.join(HOME, "my-agent")
BACKTALK = os.path.join(AGENT, "backtalk")
FACE = os.path.join(AGENT, "ai-visualizer")
BUS = os.path.join(BACKTALK, ".voice_state")
LOG = os.path.join(AGENT, "supervisor.log")
BACKTALK_JSON = os.path.join(BACKTALK, "backtalk.json")
BT_LOG = os.path.join(BACKTALK, "logs", "backtalk.log")
LOCK = os.path.join(AGENT, "supervisor.lock")

# The model proven to answer reliably here. Used only to repair a config
# whose model has disappeared from the provider.
FALLBACK_MODEL = "nvidia/nvidia/nemotron-3-super-120b-a12b"

BRAIN_PORT = 4599
FACE_PORT = 8790
UV = os.path.join(HOME, ".local", "bin", "uv.exe")

STUCK_AFTER = 240
POLL = 10.0

DETACH = {
    "stdin": subprocess.DEVNULL,
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
    "close_fds": True,
}

CREATE_NO_WINDOW = 0x08000000

# Log rotation. The voice line appends every turn and this file appends a
# heartbeat, so on a machine that starts at login neither ever stops: both
# are unbounded otherwise. Rotation is by SIZE, not age, because the thing
# that grows is exactly the thing that matters -- a burst of errors should
# not be able to push out months of quiet history, and a quiet month should
# not cost disk.
#
# KEEP=3 means the live file plus three archives, so the previous failure
# before a crash is still on disk to read. Rotating keeps the ARCHIVE with
# the lower number the NEWER one: .1 is the most recent archive. Renaming
# downwards rather than copying avoids a window where the log does not
# exist at all, which is what a delete-then-rename would leave behind.
LOG_MAX_BYTES = 2 * 1024 * 1024      # 2 MB before rotation
LOG_KEEP = 3


def rotate(path: str, max_bytes: int | None = None, keep: int | None = None) -> None:
    """Keep `path` under max_bytes, shifting archives .1..keep.

    The limits are read at CALL time rather than bound as default arguments,
    which snapshot their values when this function is defined: changing
    LOG_MAX_BYTES afterwards would silently do nothing, and a caller testing
    a small threshold would instead be testing the production one.
    """
    if max_bytes is None:
        max_bytes = LOG_MAX_BYTES
    if keep is None:
        keep = LOG_KEEP
    try:
        if not os.path.exists(path) or os.path.getsize(path) <= max_bytes:
            return
        _shift_archives(path, keep)
        try:
            os.replace(path, f"{path}.1")
            return
        except PermissionError:
            # WINDOWS SPECIFIC, and it is the normal case here, not an edge
            # case: the voice process holds backtalk.log open for the whole
            # session, and Windows refuses to rename a file another process
            # has open. Swallowing this would mean the log never rotates at
            # all -- silently, forever, which is exactly what this was
            # written to prevent.
            #
            # So fall back to archiving the CONTENT and truncating in place.
            # Truncating keeps the same file object the writer holds, so the
            # voice keeps appending to the same handle with no interruption,
            # and the previous contents are already saved as the archive.
            _archive_in_place(path, keep)
    except OSError:
        # Rotation failing must never stop the watchdog: the log growing is
        # a far smaller problem than the face not coming back.
        pass


def _shift_archives(path: str, keep: int) -> None:
    """Roll .1 -> .2 -> ... -> .keep, dropping the oldest."""
    oldest = f"{path}.{keep}"
    if os.path.exists(oldest):
        os.remove(oldest)
    for i in range(keep - 1, 0, -1):
        src = f"{path}.{i}"
        if os.path.exists(src):
            os.replace(src, f"{path}.{i + 1}")


def _archive_in_place(path: str, keep: int) -> None:
    """Save the current contents as .1, then empty the original file.

    Used when the file cannot be renamed because a writer holds it open.
    """
    import shutil
    tmp = f"{path}.1.tmp"
    try:
        shutil.copyfile(path, tmp)
        os.replace(tmp, f"{path}.1")
        with open(path, "w", encoding="utf-8"):
            pass
    except OSError:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def say(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        rotate(LOG)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def claim_singleton() -> bool:
    """Take exclusive ownership of the lock file, or report that someone
    else already has it.

    Two supervisors fight: each sees the other's children as healthy, or
    each kills what the other just started, and you get two brains fighting
    over port 4599 plus a service that flickers. The lock file is held open
    for the life of the process and locked non-blocking, which Windows
    releases automatically if this process dies -- including a hard kill, so
    there is no stale lock to clean up by hand after a crash.
    """
    f = open(LOCK, "a+")
    # seek BEFORE locking, and this is not cosmetic. msvcrt.locking locks the
    # byte range starting at the CURRENT position, and "a+" opens with the
    # pointer at end of file. The holder writes its pid, so the next process
    # would lock the byte AFTER that pid while the holder holds byte 0 --
    # different ranges, no conflict, both walk in. Always lock byte 0.
    f.seek(0)
    try:
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        f.close()
        return False
    f.truncate()
    f.write(f"{os.getpid()}\n")
    f.flush()
    # Module level on purpose. As a local this handle is dropped when the
    # function returns, the file closes, and Windows releases the lock --
    # so the very first extra supervisor walked straight in. The handle has
    # to outlive this call or it protects nothing.
    global _LOCK_HANDLE
    _LOCK_HANDLE = f
    return True


_LOCK_HANDLE = None


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", port)) == 0


def procs(pattern: str) -> list[int]:
    """PIDs whose command line matches a regex, via one WMIC-free call."""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -match '{pattern}' "
         f"-and $_.Name -notmatch 'powershell' }}).ProcessId"],
        capture_output=True, text=True, timeout=45)
    pids = []
    for tok in out.stdout.split():
        if tok.isdigit():
            pids.append(int(tok))
    return pids


def kill(pids: list[int]) -> None:
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=30)
        except Exception:
            pass
    if pids:
        time.sleep(1.5)


def spawn(cmd: str, cwd: str) -> None:
    subprocess.Popen(cmd, cwd=cwd, shell=True, creationflags=CREATE_NO_WINDOW, **DETACH)


def owner_of(port: int) -> int:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
         f"| Select-Object -First 1).OwningProcess"],
        capture_output=True, text=True, timeout=45)
    for tok in out.stdout.split():
        if tok.isdigit():
            return int(tok)
    return 0


def ensure_brain() -> None:
    if port_open(BRAIN_PORT):
        return
    say(f"brain was down, restarting on :{BRAIN_PORT}")
    exe = os.path.join(HOME, "AppData", "Roaming", "npm", "node_modules",
                       "opencode-ai", "bin", "opencode.exe")
    if os.path.exists(exe):
        spawn(f'"{exe}" serve --port {BRAIN_PORT} --hostname 127.0.0.1', AGENT)
    for _ in range(30):
        time.sleep(1)
        if port_open(BRAIN_PORT):
            say("brain is up")
            return
    say("brain did not come back up, will retry next cycle")


def ensure_face() -> None:
    if port_open(FACE_PORT):
        return
    say(f"face was down, restarting on :{FACE_PORT}")
    for stray in procs(r"ai-visualizer.*server\.py|server\.py"):
        kill([stray])
    spawn("py -3 server.py", FACE)
    for _ in range(20):
        time.sleep(0.5)
        if port_open(FACE_PORT):
            say("face is up")
            return
    say("face did not come back up, will retry next cycle")


def ensure_voice() -> None:
    running = procs(r"backtalk\.main")
    if running:
        return
    say("voice was not running, starting it")
    if os.path.exists(UV):
        spawn(f'"{UV}" run python -m backtalk.main', BACKTALK)
    else:
        spawn("uv run python -m backtalk.main", BACKTALK)


def known_models() -> set:
    """Full 'provider/model_id' refs the brain can actually serve right now."""
    import urllib.request
    with urllib.request.urlopen(
            f"http://127.0.0.1:{BRAIN_PORT}/config/providers", timeout=20) as r:
        data = json.load(r)
    out = set()
    for p in data.get("providers", data if isinstance(data, list) else []):
        models = p.get("models") or {}
        ids = list(models) if isinstance(models, dict) else [
            m.get("id") or m.get("name") for m in models]
        for mid in ids:
            if mid:
                out.add(f"{p.get('id')}/{mid}")
    return out


def repair_model() -> None:
    """A model that has vanished from the provider is the one failure the
    user cannot work around by talking. Rewrite the config to the known-good
    model and let the voice pick it up on its next start."""
    try:
        with open(BACKTALK_JSON, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return
    model = cfg.get("model")
    if not model:
        return
    if model in known_models():
        return
    say(f"model {model!r} is no longer offered, switching to {FALLBACK_MODEL}")
    cfg["model"] = FALLBACK_MODEL
    if cfg.get("deep_model") and cfg["deep_model"] not in known_models():
        cfg["deep_model"] = FALLBACK_MODEL
    try:
        tmp = BACKTALK_JSON + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, BACKTALK_JSON)
    except OSError as e:
        say(f"could not write the repaired config: {e}")
        return
    if procs(r"backtalk\.main"):
        kill(procs(r"backtalk\.main"))


def bus_state() -> tuple[str, float]:
    try:
        with open(BUS, encoding="utf-8") as f:
            return f.read().strip(), os.path.getmtime(BUS)
    except OSError:
        return "", 0.0


def recover_stuck() -> None:
    state, mtime = bus_state()
    if not mtime:
        return
    age = time.time() - mtime
    if state in ("thinking", "speaking") and age > STUCK_AFTER:
        say(f"turn looked stuck in {state!r} for {age:.0f}s, restarting the voice")
        kill(procs(r"backtalk\.main"))
        time.sleep(2)
        ensure_voice()


def once() -> None:
    ensure_brain()
    repair_model()
    ensure_face()
    ensure_voice()
    recover_stuck()


def stop() -> None:
    say("stopping voice and face")
    kill(procs(r"backtalk\.main"))
    face = owner_of(FACE_PORT)
    if face:
        kill([face])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--stop", action="store_true")
    args = ap.parse_args()

    if args.stop:
        stop()
        return 0

    if args.once:
        # A one-shot check must never take the lock: it would fight the
        # running supervisor over the children it is only inspecting.
        once()
        say("single check done")
        return 0

    if not claim_singleton():
        say("another supervisor already holds the lock, exiting quietly")
        return 0

    say("supervisor up, watching brain, face and voice")
    once()
    beat = time.time()
    try:
        while True:
            time.sleep(POLL)
            try:
                once()
            except Exception as e:
                say(f"check failed, continuing: {type(e).__name__}: {e}")
            # A heartbeat, because silence here is indistinguishable from a
            # dead process: silence used to mean "nothing needed fixing" and
            # also "the watcher had been killed".
            #
            # The voice line's log is the one that grows fast, since it
            # records every turn and this process does not own it. Rotating
            # it from here keeps it honest even though backtalk is the
            # writer, and it avoids adding rotation code to backtalk itself,
            # which is a fork that should stay close to upstream.
            rotate(BT_LOG)
            if time.time() - beat >= 600:
                say("still watching, all quiet")
                beat = time.time()
    except KeyboardInterrupt:
        say("supervisor stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
