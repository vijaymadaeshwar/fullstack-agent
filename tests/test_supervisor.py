"""Does the supervisor actually keep the stack alive, and not kill it?

Every bug this file guards against was found by hand, on a live machine, after
it had already caused trouble: --stop that did not stop, a lock that was
advisory rather than exclusive, a 404 read as proof of life, a face repair that
took the hands board down with it. None of them had a test, so none of them had
anything stopping them from coming back.

The module is written to be testable without a live stack: every external
effect it has -- powershell, netstat, taskkill, spawning servers, sleeping,
talking to ports -- is reached through a module-level name, so a test replaces
that name for the duration of one case. That is also why the seams are
worth keeping: without them these tests would need a running opencode, a
loaded Whisper model and a camera, and would be skipped for exactly the
reasons the bugs went unnoticed.

Run: py -3 tests/test_supervisor.py
"""
from pathlib import Path
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from contextlib import contextmanager
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import supervisor as sup                                    # noqa: E402


NETSTAT = """\
  TCP    127.0.0.1:8790         0.0.0.0:0              LISTENING       16188
  TCP    127.0.0.1:8790         0.0.0.0:0              LISTENING       17528
  TCP    127.0.0.1:8790         127.0.0.1:50186        TIME_WAIT       0
  TCP    127.0.0.1:8794         0.0.0.0:0              LISTENING        496
  TCP    127.0.0.1:4599         0.0.0.0:0              LISTENING        840
  TCP    127.0.0.1:87904        0.0.0.0:0              LISTENING       1234
  UDP    127.0.0.1:8790         *:*                                    9999
"""


def http_error(code):
    """The exception urlopen raises for a status that is not 2xx."""
    return urllib.error.HTTPError("http://x", code, "m", {}, io.BytesIO())


@contextmanager
def quiet():
    """Swallow the supervisor's talking while a test runs."""
    with mock.patch.object(sup, "say", lambda *a, **k: None):
        yield


@contextmanager
def no_sleep():
    """Every wait loop in here is for a service that is never coming up in a
    test. Without this, the retry loops run in real time."""
    with mock.patch.object(sup.time, "sleep", lambda *a, **k: None):
        yield


class TestHttpOk(unittest.TestCase):
    """A listening socket is not proof of life, and not every answer is good
    news. A squatter that 404s everything used to pass as a healthy brain."""

    def call(self, responder):
        with mock.patch.object(sup.urllib.request, "urlopen", responder):
            return sup.http_ok(8790, "/state")

    def test_200_is_alive(self):
        self.assertTrue(self.call(lambda *a, **k: mock.MagicMock()))

    def test_401_is_alive(self):
        """A server asking for a credential is still running."""
        def boom(*a, **k):
            raise http_error(401)
        self.assertTrue(self.call(boom))

    def test_403_is_alive(self):
        def boom(*a, **k):
            raise http_error(403)
        self.assertTrue(self.call(boom))

    def test_404_is_not_alive(self):
        """The wrong server is on this port, which is the case worth acting
        on: "no such endpoint here" is precisely a stranger."""
        def boom(*a, **k):
            raise http_error(404)
        self.assertFalse(self.call(boom))

    def test_connection_refused_is_not_alive(self):
        def boom(*a, **k):
            raise ConnectionRefusedError()
        self.assertFalse(self.call(boom))


class TestOwnersOf(unittest.TestCase):
    """Windows let two live processes share one port, so the supervisor has to
    be able to ask for every holder rather than the first. Get-NetTCPConnection
    answered with a different PID on different runs of the same question;
    netstat answers the same both times."""

    def test_finds_every_listener(self):
        with mock.patch.object(sup.subprocess, "run",
                               lambda *a, **k: mock.Mock(stdout=NETSTAT)):
            self.assertEqual(sup.owners_of(8790), {16188, 17528})

    def test_single_listener(self):
        with mock.patch.object(sup.subprocess, "run",
                               lambda *a, **k: mock.Mock(stdout=NETSTAT)):
            self.assertEqual(sup.owners_of(8794), {496})

    def test_ignores_time_wait_and_udp(self):
        with mock.patch.object(sup.subprocess, "run",
                               lambda *a, **k: mock.Mock(stdout=NETSTAT)):
            # TIME_WAIT rows and the UDP row must not count as holders.
            self.assertNotIn(0, sup.owners_of(8790))
            self.assertNotIn(9999, sup.owners_of(8790))

    def test_port_match_is_exact(self):
        """87904 must not be mistaken for 8790. A prefix match would let an
        unrelated service on 87904 be killed as a duplicate face."""
        with mock.patch.object(sup.subprocess, "run",
                               lambda *a, **k: mock.Mock(stdout=NETSTAT)):
            self.assertEqual(sup.owners_of(87904), {1234})

    def test_nothing_listening(self):
        with mock.patch.object(sup.subprocess, "run",
                               lambda *a, **k: mock.Mock(stdout="")):
            self.assertEqual(sup.owners_of(8790), set())

    def test_owner_of_keeps_the_oldest_holder(self):
        with mock.patch.object(sup.subprocess, "run",
                               lambda *a, **k: mock.Mock(stdout=NETSTAT)):
            self.assertEqual(sup.owner_of(8790), 16188)

    def test_owner_of_empty_port_is_zero(self):
        with mock.patch.object(sup.subprocess, "run",
                               lambda *a, **k: mock.Mock(stdout="")):
            self.assertEqual(sup.owner_of(8790), 0)


class TestDuplicates(unittest.TestCase):
    """The duplicate cleanup has to be surgical. An earlier version swept up
    every server.py, which meant a face repair killed the hands board."""

    def dupes(self, port, holders):
        with mock.patch.object(sup, "owners_of", lambda p: set(holders)), \
             mock.patch.object(sup, "_is_python", lambda pid: True), \
             quiet(), no_sleep():
            return sup._duplicate_port_pids(port)

    def test_second_holder_is_the_duplicate(self):
        self.assertEqual(self.dupes(8790, [16188, 17528]), [17528])

    def test_the_oldest_holder_is_the_one_kept(self):
        """The copy that was there first is the one clients were talking to,
        so it is the one that stays."""
        self.assertEqual(self.dupes(8790, [17528, 16188]), [17528])

    def test_three_copies_leave_one(self):
        self.assertEqual(self.dupes(8790, [100, 200, 300]), [200, 300])

    def test_lone_server_has_no_duplicate(self):
        self.assertEqual(self.dupes(8790, [16188]), [])

    def test_non_python_holders_are_left_alone(self):
        """The port may be held by a cmd.exe wrapper; killing that leaves the
        real server running behind it."""
        with mock.patch.object(sup, "owners_of", lambda p: {16188}), \
             mock.patch.object(sup, "_is_python", lambda pid: False), \
             quiet(), no_sleep(), \
             mock.patch.object(sup, "kill") as killed:
            sup._duplicate_port_pids(8790)
            killed.assert_not_called()

    def test_face_repair_never_touches_the_hands_port(self):
        """The regression this file exists for. A face repair once killed the
        board, because both repos run a file called server.py."""
        with mock.patch.object(sup, "face_alive", lambda: False), \
             mock.patch.object(sup, "port_open", lambda p: False), \
             mock.patch.object(sup, "owners_of",
                               lambda p: {16188} if p == 8790 else {496}), \
             mock.patch.object(sup, "_is_python", lambda pid: True), \
             mock.patch.object(sup, "spawn") as spawned, \
             mock.patch.object(sup, "kill") as killed, \
             quiet(), no_sleep():
            sup.ensure_face()
        killed.assert_called_once_with([16188])
        self.assertEqual(spawned.call_count, 1)

    def test_face_repair_starts_exactly_one_face(self):
        with mock.patch.object(sup, "face_alive", lambda: False), \
             mock.patch.object(sup, "port_open", lambda p: False), \
             mock.patch.object(sup, "owners_of", lambda p: set()), \
             mock.patch.object(sup, "spawn") as spawned, \
             quiet(), no_sleep():
            sup.ensure_face()
        self.assertEqual(spawned.call_count, 1)


class TestSingletonLock(unittest.TestCase):
    """Two supervisors fight: each kills what the other just started. The lock
    has to be held for the life of the process, not just for the call."""

    @contextmanager
    def claimed(self, lock):
        """Hold the singleton lock for the duration of one test.

        The handle is module-level on purpose -- that is what makes the lock
        survive the call that takes it -- so it has to be closed before the
        temporary directory goes away, or Windows refuses to delete the file
        that is still open.
        """
        with mock.patch.object(sup, "LOCK", lock), quiet():
            try:
                yield
            finally:
                if sup._LOCK_HANDLE is not None:
                    sup._LOCK_HANDLE.close()
                    sup._LOCK_HANDLE = None

    def test_second_claim_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            lock = os.path.join(d, "supervisor.lock")
            with self.claimed(lock):
                self.assertTrue(sup.claim_singleton())
                self.assertFalse(sup.claim_singleton())

    def test_lock_is_released_when_the_process_dies(self):
        """Windows drops the lock when the handle closes, including on a hard
        kill, so a crashed supervisor leaves nothing to clean up by hand."""
        with tempfile.TemporaryDirectory() as d:
            lock = os.path.join(d, "supervisor.lock")
            try:
                with mock.patch.object(sup, "LOCK", lock), quiet():
                    self.assertTrue(sup.claim_singleton())
                    # Stand in for the process dying: the handle goes, the OS
                    # releases the range, and the file stays on disk.
                    sup._LOCK_HANDLE.close()
                    sup._LOCK_HANDLE = None
                    self.assertTrue(sup.claim_singleton())
            finally:
                if sup._LOCK_HANDLE is not None:
                    sup._LOCK_HANDLE.close()
                    sup._LOCK_HANDLE = None

    def test_the_handle_is_kept_at_module_scope(self):
        """The bug the module-level handle exists to prevent.

        If claim_singleton() stored its handle as a local, the file would
        close the moment the function returned, Windows would release the
        lock, and the very first extra supervisor would walk straight in --
        which is exactly what happened before this was fixed.
        """
        with tempfile.TemporaryDirectory() as d:
            lock = os.path.join(d, "supervisor.lock")
            with self.claimed(lock):
                self.assertTrue(sup.claim_singleton())
                self.assertIsNotNone(sup._LOCK_HANDLE,
                                     "handle was dropped: the lock protects "
                                     "nothing once the call returns")
                self.assertFalse(sup._LOCK_HANDLE.closed)

    def test_a_stale_lock_file_does_not_block_startup(self):
        """The lock is released by the OS when the process dies, so a crashed
        supervisor must not need manual cleanup. A lock file left on disk with
        nobody holding it is claimed normally."""
        with tempfile.TemporaryDirectory() as d:
            lock = os.path.join(d, "supervisor.lock")
            Path(lock).write_text("99999\n")   # a dead supervisor's leftover
            with self.claimed(lock):
                self.assertTrue(sup.claim_singleton())


class TestHandsOptIn(unittest.TestCase):
    """The board is a tool you open when you want it, not a service. The
    supervisor repairs it once asked for and otherwise leaves it alone."""

    def hands_dir(self, tmp, installed=True, enabled=False):
        root = os.path.join(tmp, "barehands")
        if installed:
            os.makedirs(os.path.join(root, "state"), exist_ok=True)
            if enabled:
                Path(root, "state", ".enabled").write_text("enabled")
        return root

    def test_not_installed_means_disabled(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(sup, "HANDS", self.hands_dir(tmp, installed=False)):
            self.assertFalse(sup.hands_enabled())

    def test_installed_but_no_marker_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(sup, "HANDS", self.hands_dir(tmp)):
            self.assertFalse(sup.hands_enabled())

    def test_marker_turns_it_on(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(sup, "HANDS", self.hands_dir(tmp, enabled=True)):
            self.assertTrue(sup.hands_enabled())

    def test_disabled_board_is_never_started(self):
        """The user closed it on purpose. Starting it here would put a window
        on screen nobody asked for."""
        with mock.patch.object(sup, "hands_enabled", lambda: False), \
             mock.patch.object(sup, "spawn") as spawned, \
             mock.patch.object(sup, "kill") as killed, \
             quiet(), no_sleep():
            sup.ensure_hands()
        spawned.assert_not_called()
        killed.assert_not_called()

    def test_enabled_but_down_is_restarted(self):
        with mock.patch.object(sup, "hands_enabled", lambda: True), \
             mock.patch.object(sup, "hands_alive", lambda: False), \
             mock.patch.object(sup, "port_open", lambda p: False), \
             mock.patch.object(sup, "owner_of", lambda p: 0), \
             mock.patch.object(sup, "spawn") as spawned, \
             quiet(), no_sleep():
            sup.ensure_hands()
        spawned.assert_called_once()

    def test_enabled_and_healthy_is_left_alone(self):
        with mock.patch.object(sup, "hands_enabled", lambda: True), \
             mock.patch.object(sup, "hands_alive", lambda: True), \
             mock.patch.object(sup, "owners_of", lambda p: {496}), \
             mock.patch.object(sup, "_is_python", lambda pid: True), \
             mock.patch.object(sup, "spawn") as spawned, \
             quiet(), no_sleep():
            sup.ensure_hands()
        spawned.assert_not_called()


class TestStop(unittest.TestCase):
    """Stop Seyon.bat used to stop nothing: the supervisor was still running
    and started everything again on its next poll. The watchdog goes first."""

    def test_supervisor_is_killed_before_the_children(self):
        order = []
        with mock.patch.object(sup, "procs",
                               lambda p: [111] if "supervisor" in p else []), \
             mock.patch.object(sup, "kill",
                               lambda p: order.append(tuple(p))), \
             mock.patch.object(sup, "owner_of", lambda p: 0), \
             mock.patch.object(sup, "port_open", lambda p: False), \
             quiet(), no_sleep():
            sup.stop()
        self.assertEqual(order[0], (111,), "the watchdog must die first")

    def test_face_and_voice_are_stopped(self):
        killed = []
        with mock.patch.object(sup, "procs",
                               lambda p: [222] if "backtalk" in p else []), \
             mock.patch.object(sup, "kill", lambda p: killed.extend(p)), \
             mock.patch.object(sup, "owner_of", lambda p: 333), \
             mock.patch.object(sup, "port_open", lambda p: True), \
             quiet(), no_sleep():
            sup.stop()
        self.assertIn(222, killed, "the voice was not stopped")
        self.assertIn(333, killed, "the face was not stopped")

    def test_marker_is_cleared(self):
        """Left in place, --stop only half works: the board comes back on the
        next poll."""
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "barehands")
            os.makedirs(os.path.join(root, "state"))
            marker = os.path.join(root, "state", ".enabled")
            Path(marker).write_text("enabled")
            with mock.patch.object(sup, "HANDS", root), \
                 mock.patch.object(sup, "procs", lambda p: []), \
                 mock.patch.object(sup, "kill", lambda p: None), \
                 mock.patch.object(sup, "owner_of", lambda p: 0), \
                 mock.patch.object(sup, "port_open", lambda p: False), \
                 quiet(), no_sleep():
                sup.stop()
            self.assertFalse(os.path.exists(marker))

    def test_a_stop_that_does_not_stop_says_so(self):
        """"Stopped" printed over a stack that is still running is worse than
        an error: the user watches it come back and trusts nothing after."""
        said = []
        with mock.patch.object(sup, "procs",
                               lambda p: [1] if "backtalk" in p else []), \
             mock.patch.object(sup, "kill", lambda p: None), \
             mock.patch.object(sup, "owner_of", lambda p: 1), \
             mock.patch.object(sup, "port_open", lambda p: True), \
             mock.patch.object(sup, "say", said.append), \
             no_sleep():
            sup.stop()
        self.assertTrue(any("WARNING" in s for s in said),
                        f"expected a warning, got: {said}")


class TestModelRepair(unittest.TestCase):
    """A brain that is merely restarting looks exactly like a model that has
    vanished. Rewriting the config on that guess turns a 20-second outage into
    a permanent change nobody asked for."""

    def test_nothing_is_rewritten_when_the_brain_cannot_be_asked(self):
        with mock.patch.object(sup, "known_models", set), \
             mock.patch.object(sup, "BACKTALK_JSON", "unused.json"), \
             mock.patch.object(sup, "say", lambda *a: None), \
             mock.patch("builtins.open", mock.mock_open()) as opened:
            sup.repair_model()
        opened.assert_not_called()

    def test_a_still_valid_model_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "backtalk.json")
            Path(cfg).write_text(json.dumps({"model": "good/model"}))
            with mock.patch.object(sup, "known_models",
                                   lambda: {"good/model", "other/model"}), \
                 mock.patch.object(sup, "BACKTALK_JSON", cfg), \
                 mock.patch.object(sup, "say", lambda *a: None):
                sup.repair_model()
            self.assertEqual(json.loads(Path(cfg).read_text())["model"],
                             "good/model")

    def test_a_vanished_model_is_repaired(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = os.path.join(tmp, "backtalk.json")
            Path(cfg).write_text(json.dumps({"model": "gone/model"}))
            with mock.patch.object(sup, "known_models", lambda: {"live/model"}), \
                 mock.patch.object(sup, "BACKTALK_JSON", cfg), \
                 mock.patch.object(sup, "procs", lambda p: []), \
                 mock.patch.object(sup, "say", lambda *a: None):
                sup.repair_model()
            self.assertEqual(json.loads(Path(cfg).read_text())["model"],
                             sup.FALLBACK_MODEL)


class TestStuckTurn(unittest.TestCase):
    """A turn wedged in thinking/speaking is the one failure the user cannot
    work around by talking."""

    def test_recent_turn_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = os.path.join(tmp, ".voice_state")
            Path(bus).write_text("thinking")
            with mock.patch.object(sup, "BUS", bus), \
                 mock.patch.object(sup, "kill") as killed, \
                 mock.patch.object(sup, "ensure_voice") as started, \
                 quiet(), no_sleep():
                sup.recover_stuck()
            killed.assert_not_called()
            started.assert_not_called()

    def test_wedged_turn_restarts_the_voice(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = os.path.join(tmp, ".voice_state")
            Path(bus).write_text("speaking")
            old = sup.time.time() - (sup.STUCK_AFTER + 60)
            os.utime(bus, (old, old))
            with mock.patch.object(sup, "BUS", bus), \
                 mock.patch.object(sup, "procs", lambda p: [9]), \
                 mock.patch.object(sup, "kill") as killed, \
                 mock.patch.object(sup, "ensure_voice") as started, \
                 quiet(), no_sleep():
                sup.recover_stuck()
            killed.assert_called_once_with([9])
            started.assert_called_once()

    def test_an_idle_bus_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            bus = os.path.join(tmp, ".voice_state")
            Path(bus).write_text("idle")
            with mock.patch.object(sup, "BUS", bus), \
                 mock.patch.object(sup, "kill") as killed, \
                 quiet(), no_sleep():
                sup.recover_stuck()
            killed.assert_not_called()


class TestRotate(unittest.TestCase):
    """Both logs grow forever otherwise, and one of them is written by a
    process this supervisor does not own."""

    def test_small_file_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "s.log")
            Path(log).write_text("hello")
            sup.rotate(log, max_bytes=1024, keep=3)
            self.assertEqual(Path(log).read_text(), "hello")

    def test_large_file_is_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "s.log")
            Path(log).write_text("x" * 5000)
            sup.rotate(log, max_bytes=1000, keep=3)
            self.assertTrue(os.path.exists(log + ".1"))

    def test_archives_are_capped(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "s.log")
            for i in range(6):
                Path(log).write_text("x" * 5000)
                sup.rotate(log, max_bytes=1000, keep=3)
            self.assertTrue(os.path.exists(log + ".3"))
            self.assertFalse(os.path.exists(log + ".4"))

    def test_a_locked_log_still_rotates(self):
        """The normal case on Windows: the voice holds backtalk.log open for
        the whole session and Windows refuses to rename it. Swallowing that
        would mean the log never rotates at all, silently, forever -- which
        is the whole thing this was written to prevent."""
        with tempfile.TemporaryDirectory() as tmp:
            log = os.path.join(tmp, "s.log")
            Path(log).write_text("x" * 5000)
            real_replace = os.replace

            def locked(src, dst, *a, **k):
                # Only the live log is held open. The archive copy must still
                # be allowed to move into place, which is the whole fallback.
                if os.path.abspath(src) == os.path.abspath(log):
                    raise PermissionError(32, "in use", src)
                return real_replace(src, dst, *a, **k)

            with mock.patch.object(sup.os, "replace", locked):
                sup.rotate(log, max_bytes=1000, keep=3)

            self.assertTrue(os.path.exists(log + ".1"), "no archive was made")
            self.assertEqual(Path(log + ".1").read_text(), "x" * 5000,
                             "the old contents must be saved")
            self.assertEqual(os.path.getsize(log), 0,
                             "the live log must be emptied in place")


class TestWatchdogSurvivesItsOwnFailures(unittest.TestCase):
    """The watchdog's job is to outlive everything it supervises.

    Found by reading supervisor.log rather than by running anything: four
    restarts in a single day with not one error line between them. The
    first check ran outside the handler, and stderr went to a console
    `start ""` never redirected, so a dead provider during startup killed
    the whole thing silently -- hours with nothing on watch and nothing
    recorded to explain it.
    """

    @staticmethod
    def messages(say_mock):
        return " ".join(c.args[0] for c in say_mock.call_args_list)

    def test_a_failing_check_does_not_escape(self):
        with mock.patch.object(sup, "once",
                               side_effect=RuntimeError("provider down")), \
             mock.patch.object(sup, "rotate"), \
             mock.patch.object(sup, "say") as say:
            sup._cycle(sup.time.time())          # must not raise
        msg = self.messages(say)
        self.assertIn("check failed", msg)
        self.assertIn("provider down", msg)

    def test_a_failing_rotate_does_not_escape(self):
        with mock.patch.object(sup, "once"), \
             mock.patch.object(sup, "rotate",
                               side_effect=OSError("log is locked")), \
             mock.patch.object(sup, "say") as say:
            sup._cycle(sup.time.time())
        self.assertIn("rotate failed", self.messages(say))

    def test_a_broken_check_still_lets_the_heartbeat_through(self):
        """A check that fails forever must not silence the heartbeat:
        silence is how a dead watcher and a working one look alike."""
        old = sup.time.time() - 700
        with mock.patch.object(sup, "once", side_effect=RuntimeError("x")), \
             mock.patch.object(sup, "rotate"), \
             mock.patch.object(sup, "say") as say:
            beat = sup._cycle(old)
        self.assertIn("still watching", self.messages(say))
        self.assertGreater(beat, old, "the heartbeat clock must move")

    def test_a_healthy_cycle_is_quiet(self):
        with mock.patch.object(sup, "once") as once, \
             mock.patch.object(sup, "rotate") as rotate, \
             mock.patch.object(sup, "say") as say:
            beat = sup._cycle(sup.time.time())
        once.assert_called_once()
        rotate.assert_called_once()
        self.assertEqual(say.call_count, 0, "no news on a healthy cycle")
        self.assertIsInstance(beat, float)

    def test_a_crash_is_written_to_the_log_before_it_exits(self):
        """The outer handler, for anything the guards above cannot catch.
        Before it, a bug here meant a supervisor that vanished with an
        empty log and no way to ask it what happened."""
        with mock.patch.object(sup, "claim_singleton", lambda: True), \
             mock.patch.object(sup, "_cycle", lambda b: b), \
             mock.patch.object(sup.time, "sleep",
                               mock.Mock(side_effect=RuntimeError("exploded"))), \
             mock.patch.object(sup, "say") as say, \
             mock.patch.object(sys, "argv", ["supervisor.py"]):
            with self.assertRaises(RuntimeError):
                sup.main()
        msg = self.messages(say)
        self.assertIn("supervisor died", msg)
        self.assertIn("exploded", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)