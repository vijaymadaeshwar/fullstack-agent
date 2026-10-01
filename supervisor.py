"""supervisor.py - keeps the Seyon stack alive so it just works.

Checks, on a loop:
  1. the opencode brain   (port 4599)
  2. the ai-visualizer face (port 8790)
  3. the backtalk voice
  4. the barehands board  (port 8794) -- only once you have opened it
  5. a turn that is stuck in "thinking" far too long

Anything missing or wedged is restarted, and stale duplicates or
port squatters are cleared first so two things never fight over the
same port or the same signal bus.

  py -3 supervisor.py            run the loop in the foreground
  py -3 supervisor.py --once     check and repair once, then exit (skipped
                                 if a supervisor is already watching)
  py -3 supervisor.py --stop     stop the whole stack, watchdog included
"""
from __future__ import annotations

import argparse
import json
import msvcrt
import os
import socket
import subprocess
import time
import urllib.request

HOME = os.path.expanduser("~")
AGENT = os.path.join(HOME, "my-agent")
BACKTALK = os.path.join(AGENT, "backtalk")
FACE = os.path.join(AGENT, "ai-visualizer")
HANDS = os.path.join(AGENT, "barehands")
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
HANDS_PORT = 8794
UV = os.path.join(HOME, ".local", "bin", "uv.exe")

# The hands board is the optional piece, so it is only supervised when it is
# installed AND enabled. Watching it unconditionally would restart a board
# the user deliberately closed -- hands is a "hold this open while I work"
# tool, not a service, so starting it out of nowhere would put a window on
# screen nobody asked for.
#
# Enabled by barehands/state/.enabled, which the launcher writes. A missing
# barehands/ folder also counts as not-installed, so nothing here is a
# problem for someone who never took the hands option.

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
    # Strip the server-auth variables before anything is launched. If they
    # are set in the environment, `opencode serve` turns on HTTP Basic auth
    # and every later request 401s -- including the brain's own client,
    # which talks to it unauthenticated on loopback. The brain clears these
    # for the server it starts itself (brain.py does the same); the
    # supervisor has to do it too, or it hands back a brain nobody can talk
    # to every time it restarts one.
    env = {k: v for k, v in os.environ.items()
           if k not in ("OPENCODE_SERVER_PASSWORD", "OPENCODE_SERVER_USERNAME")}
    env["OPENCODE_CLIENT"] = "supervisor"
    subprocess.Popen(cmd, cwd=cwd, env=env, shell=True,
                     creationflags=CREATE_NO_WINDOW, **DETACH)


def owners_of(port: int) -> set[int]:
    """Every PID listening on `port`. Usually one; more than one is a bug.

    There can genuinely be two, and that is the whole point of asking for
    all of them rather than the first. Windows' SO_REUSEADDR lets a second
    process bind a port that is already bound, so a duplicated server does
    not fail cleanly -- it sits there holding the same port and the kernel
    hands arriving connections to either one. netstat shows two LISTENING
    rows on 8790 for exactly that reason.

    Get-NetTCPConnection is not trustworthy for this: it returned each of
    the two PIDs on different runs for the same port, so "who owns the port"
    is not even a stable question. netstat -ano reports both, every time.
    """
    out = subprocess.run(["netstat", "-ano"], capture_output=True,
                         text=True, timeout=45, errors="replace")
    pids = set()
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        local, state, pid = parts[1], parts[3].upper(), parts[4]
        if state != "LISTENING" or not pid.isdigit():
            continue
        # Match the port exactly, so 8790 does not also match 87904.
        if local.rsplit(":", 1)[-1] == str(port):
            pids.add(int(pid))
    return pids


def owner_of(port: int) -> int:
    """The first PID listening on `port`, or 0. Use owners_of() to see all."""
    pids = owners_of(port)
    return min(pids) if pids else 0


def http_ok(port: int, path: str, timeout: float = 6.0) -> bool:
    """True only if the server actually answers HTTP on that path.

    A listening socket is not proof of life. A wedged process keeps its
    port bound and every connect() still succeeds, so a port check alone
    would report "healthy" for a face that stopped rendering or a brain
    that stopped answering -- and the supervisor would happily leave it
    that way forever. Ask for real bytes instead.

    Most HTTP statuses count as alive, including 401 and 403. A server that
    answers "Unauthorized" is very much running; it is telling us the
    caller needs a credential, which is the application's business, not a
    reason to declare it dead and restart it in a loop. Only a refused
    connection, a timeout, or garbage means dead.

    404 is the exception, and treating it as alive was a real bug: an
    unrelated program squatting on the port answers 404 to everything,
    which "any status is alive" accepted as proof of a healthy brain or
    face. The supervisor then declared the real service unnecessary and
    left the squatter holding the port forever. A 404 means "no such
    endpoint here", which is exactly the wrong-server case worth acting on.
    """
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}",
                                    timeout=timeout) as r:
            r.read(64)
            return True
    except urllib.error.HTTPError as e:
        return e.code != 404    # it spoke HTTP, and it knows this path
    except Exception:
        return False


def face_alive() -> bool:
    return http_ok(FACE_PORT, "/state")


def brain_alive() -> bool:
    # Prefer the API endpoint the brain actually serves. The root path and
    # /health can both answer 403 when the embedded web UI is disabled (it
    # is, by default, in this environment), which tells us nothing about
    # whether the brain works. /config/providers is the endpoint backtalk
    # itself depends on, so if it answers, the brain is genuinely usable.
    #
    # Only /config/providers is a usable signal here. Falling back to "/"
    # would undo the 404 fix above: the embedded UI is disabled in this
    # environment, so the brain answers 404 on "/", and treating that as
    # "alive" would let a wedged brain pass as healthy.
    return http_ok(BRAIN_PORT, "/config/providers")


def ensure_brain() -> None:
    if brain_alive():
        return
    # Down, or listening but not answering. Take the port off the table
    # first, otherwise a wedged instance blocks the replacement.
    if port_open(BRAIN_PORT):
        say("brain was listening but not answering, restarting it")
        for stray in procs(r"opencode.*serve"):
            kill([stray])
        for _ in range(10):
            time.sleep(0.5)
            if not port_open(BRAIN_PORT):
                break
    else:
        say(f"brain was down, restarting on :{BRAIN_PORT}")
    exe = os.path.join(HOME, "AppData", "Roaming", "npm", "node_modules",
                       "opencode-ai", "bin", "opencode.exe")
    if os.path.exists(exe):
        spawn(f'"{exe}" serve --port {BRAIN_PORT} --hostname 127.0.0.1', AGENT)
    for _ in range(30):
        time.sleep(1)
        if brain_alive():
            say("brain is up")
            return
    say("brain did not come back up, will retry next cycle")


def ensure_face() -> None:
    if face_alive():
        # Only the face here. Reaping every stray server would also reach
        # the hands board, which this function has no business touching --
        # and would kill the hands board as collateral of a face repair.
        _reap_duplicate_face()
        return
    if port_open(FACE_PORT):
        say("face was listening but not answering, restarting it")
    else:
        say(f"face was down, restarting on :{FACE_PORT}")
    _clear_face_strays()
    spawn("py -3 server.py", FACE)
    for _ in range(20):
        time.sleep(0.5)
        if face_alive():
            say("face is up")
            return
    say("face did not come back up, will retry next cycle")


def _is_python(pid: int) -> bool:
    """True for a real interpreter, not a cmd.exe or py.exe launcher.

    The launchers wrap the same server script, so a port can be matched to
    the interpreter that actually serves it. Without this, killing a
    launcher PID leaves the real server running behind it.
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").Name"],
            capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    return "python" in out.stdout.strip().lower()





def ensure_hands_no_duplicates() -> None:
    """Kill a second hands server, once the board is answering.

    The hands board can be bound twice the same way the face can, and this
    is kept separate from _reap_duplicate_face() so neither can reach the
    other's port.
    """
    if not hands_alive():
        return
    for pid in _duplicate_port_pids(HANDS_PORT):
        say(f"hands port {HANDS_PORT} had a duplicate copy (pid {pid}), killing it")
        kill([pid])


def _duplicate_port_pids(port: int) -> list[int]:
    """Servers bound to `port` beyond the first one.

    netstat reports both copies when Windows' SO_REUSEADDR let a duplicate
    bind a busy port. They cannot be told apart by folder, so the older
    process is kept (it is the one that was there first and is the one
    clients were talking to) and every other holder is a duplicate.

    Only called when the port is genuinely answering, so at least one holder
    is the real server.
    """
    holders = sorted(p for p in owners_of(port) if _is_python(p))
    return holders[1:]


def _clear_face_strays() -> None:
    """Clear the face port before starting a replacement face.

    Scoped to the holders of the face port. Killing every server.py instead
    would take down the hands board, which is a different service on a
    different port and is none of the face's business. This runs only when
    the face is not answering, so nothing healthy is serving 8790 and every
    holder is a squatter to clear.
    """
    squatters = [p for p in owners_of(FACE_PORT) if _is_python(p)]
    if squatters:
        kill(squatters)


def _reap_duplicate_face() -> None:
    """Kill a second face server bound to the face port.

    Clicking a Desktop shortcut while the face is already up started a
    second copy. Windows' SO_REUSEADDR lets a second process bind an
    already-bound port, so instead of failing cleanly the duplicate stayed on
    8790 as well and netstat showed two LISTENING rows. The kernel then
    picks which copy answers any given request, so the face flickered
    between two copies and both held ~35 MB.

    Scoped to the face port on purpose. An earlier version also swept up
    every server.py that held no port at all, which meant a face repair
    killed the hands board -- the one process here that is supposed to hold
    no port while it is still starting up.

    The older holder of the port is kept, so the working face is never the
    one killed. Only runs when the face is confirmed answering.
    """
    for pid in _duplicate_port_pids(FACE_PORT):
        say(f"face port {FACE_PORT} had a duplicate copy (pid {pid}), killing it")
        kill([pid])


def ensure_voice() -> None:
    running = procs(r"backtalk\.main")
    if running:
        return
    say("voice was not running, starting it")
    if os.path.exists(UV):
        spawn(f'"{UV}" run python -m backtalk.main', BACKTALK)
    else:
        spawn("uv run python -m backtalk.main", BACKTALK)


def hands_enabled() -> bool:
    """True when the hands board is installed and the user turned it on.

    The marker file is what makes this opt-in: hands is a tool you open when
    you want it, so the supervisor repairs it once it has been asked for and
    otherwise leaves it alone. Watching it unconditionally would put a
    window on screen after the user deliberately closed it.
    """
    if not os.path.isdir(HANDS):
        return False
    return os.path.exists(os.path.join(HANDS, "state", ".enabled"))


def hands_alive() -> bool:
    return http_ok(HANDS_PORT, "/config")


def ensure_hands() -> None:
    if not hands_enabled():
        return
    if hands_alive():
        ensure_hands_no_duplicates()
        return
    if port_open(HANDS_PORT):
        say("hands was listening but not answering, restarting it")
    else:
        say(f"hands was down, restarting on :{HANDS_PORT}")
    stray = owner_of(HANDS_PORT)
    if stray:
        kill([stray])
    spawn("py -3 server.py", HANDS)
    for _ in range(20):
        time.sleep(0.5)
        if hands_alive():
            say("hands is up")
            return
    say("hands did not come back up, will retry next cycle")


def known_models() -> set:
    """Full 'provider/model_id' refs the brain can actually serve right now.

    Returns an empty set if the brain cannot be asked -- down, starting, or
    refusing the request. An empty set means "unknown", never "the model is
    gone": repair_model() must not rewrite the config on a guess.
    """
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{BRAIN_PORT}/config/providers",
                timeout=20) as r:
            data = json.load(r)
    except Exception:
        return set()
    if not isinstance(data, (dict, list)):
        return set()
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
    # Ask once, and give up if the brain will not answer. Without this a
    # brain that is merely restarting (or refusing us) looks exactly like
    # "no models offered", and the config gets quietly rewritten to the
    # fallback -- turning a 20-second outage into a permanent model change.
    offered = known_models()
    if not offered:
        return
    try:
        with open(BACKTALK_JSON, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return
    model = cfg.get("model")
    if not model:
        return
    if model in offered:
        return
    say(f"model {model!r} is no longer offered, switching to {FALLBACK_MODEL}")
    cfg["model"] = FALLBACK_MODEL
    if cfg.get("deep_model") and cfg["deep_model"] not in offered:
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
    ensure_hands()
    recover_stuck()


def stop() -> None:
    """Stop the whole stack, the watchdog included.

    The supervisor used to stop only the voice and the face, which meant
    "Stop Seyon.bat" did not stop anything: the supervisor was still
    running, so its very next poll saw voice and face missing and started
    them again within ten seconds. The user got a "stopped" message and
    then watched everything come back on its own.

    So the watchdog has to go first, or nothing after it will stick. Order
    matters -- killing the children before the supervisor just invites a
    restart on the way out. The brain is deliberately left running, because
    the other agents share it.
    """
    # Every supervisor, including any that is not this process. This one is
    # a --stop run, so it must not match its own pattern; the (?!...) guard
    # is belt-and-braces for a repo path containing "supervisor".
    watchers = [p for p in procs(r"supervisor\.py(?!.*--stop)") if p != os.getpid()]
    if watchers:
        say(f"stopping the supervisor ({len(watchers)} running)")
        kill(watchers)

    say("stopping voice and face")
    kill(procs(r"backtalk\.main"))
    face = owner_of(FACE_PORT)
    if face:
        kill([face])

    # Hands too, and the marker cleared, or a restart would immediately bring
    # back a board the user just asked to close. Leaving the marker would make
    # --stop only half work: the board would come back on the next poll.
    if port_open(HANDS_PORT):
        hands = owner_of(HANDS_PORT)
        if hands:
            kill([hands])
    marker = os.path.join(HANDS, "state", ".enabled")
    if os.path.exists(marker):
        try:
            os.remove(marker)
        except OSError:
            pass

    # The face and the voice are gone now; confirm it, so a failure to stop
    # is visible instead of silently reappearing.
    time.sleep(1.0)
    still = []
    if port_open(FACE_PORT):
        still.append("face")
    if procs(r"backtalk\.main"):
        still.append("voice")
    if port_open(HANDS_PORT):
        still.append("hands")
    if still:
        say(f"WARNING: {', '.join(still)} did not stop")
    else:
        say("stack stopped (brain left running for the other agents)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--stop", action="store_true")
    args = ap.parse_args()

    if args.stop:
        stop()
        return 0

    if args.once:
        # Take the lock here too. The original reason to skip it was that a
        # one-shot check "only inspects", but it does not: once() calls the
        # same ensure_* functions the loop does, and repairs whatever it
        # finds. So an unlocked --once running alongside the real
        # supervisor could kill a face the supervisor had just started, and
        # the two would trade restarts forever.
        #
        # Taking the lock and bailing out when it is held gives the safe
        # behaviour instead: if a supervisor is already watching, there is
        # nothing for --once to do, and it exits instead of interfering.
        # claim_singleton() keeps the handle at module scope, so the lock
        # is released when this process exits.
        if not claim_singleton():
            say("another supervisor is already watching, nothing for --once to do")
            return 0
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
