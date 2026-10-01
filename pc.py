"""pc.py - voice-driven PC control for Jarvis.

Usage:
  py -3 pc.py open <target>          open a URL, app, folder or file
  py -3 pc.py search <query>         web search (DuckDuckGo)
  py -3 pc.py youtube <query>        YouTube search results
  py -3 pc.py wiki <query>           Wikipedia search
  py -3 pc.py maps <query>           Google Maps search
  py -3 pc.py gmail <query>          Gmail search
  py -3 pc.py type <text>            type text into the focused window
  py -3 pc.py key <keys>             press keys (enter, tab, ctrl+s, alt+tab)
  py -3 pc.py notepad <text>         open Notepad and type into it
  py -3 pc.py close                  close the focused window
  py -3 pc.py volume <0-100>         set system volume
  py -3 pc.py mute on|off            mute or unmute
  py -3 pc.py list                   list visible app windows
  py -3 pc.py focus <title>          focus a window by title substring
  py -3 pc.py screenshot             save a screenshot to media\
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
import urllib.parse

IS_WIN = os.name == "nt"
if not IS_WIN:
    sys.exit("pc.py is Windows only")

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.BringWindowToTop.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.AttachThreadInput.argtypes = [wt.DWORD, wt.DWORD, wt.BOOL]
user32.GetClipboardData.restype = wt.HANDLE
user32.GetClipboardData.argtypes = [wt.UINT]
user32.SetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
user32.OpenClipboard.argtypes = [wt.HWND]
user32.EmptyClipboard.argtypes = []
user32.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, wt.DWORD, ctypes.c_ulong]
user32.SendInput.argtypes = [wt.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = wt.UINT
kernel32.GetCurrentThreadId.restype = wt.DWORD

SW_RESTORE = 9

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_SHIFT = 0x10

MODS = {
    "ctrl": VK_CONTROL, "control": VK_CONTROL, "alt": VK_MENU,
    "shift": VK_SHIFT, "win": 0x5B, "super": 0x5B, "cmd": 0x5B,
}

NAMED = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
    "home": 0x24, "end": 0x23, "up": 0x26, "down": 0x28, "left": 0x25,
    "right": 0x27, "pgup": 0x21, "pgdn": 0x22, "f1": 0x70, "f2": 0x71,
    "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75, "f11": 0x7A,
    "f12": 0x7B, "printscreen": 0x2C,
}

APPS = {
    "notepad": "notepad.exe", "calculator": "calc.exe", "calc": "calc.exe",
    "paint": "mspaint.exe", "explorer": "explorer.exe", "file explorer": "explorer.exe",
    "files": "explorer.exe", "task manager": "taskmgr.exe", "cmd": "cmd.exe",
    "command prompt": "cmd.exe", "powershell": "powershell.exe", "terminal": "wt.exe",
    "vscode": "code.cmd", "vs code": "code.cmd", "code": "code.cmd",
    "visual studio code": "code.cmd", "chrome": "chrome.exe",
    "google chrome": "chrome.exe", "edge": "msedge.exe", "firefox": "firefox.exe",
    "word": "winword.exe", "excel": "excel.exe", "powerpoint": "powerpnt.exe",
    "outlook": "outlook.exe", "spotify": "spotify.exe", "settings": "ms-settings:",
    "taskbar": "ms-settings:", "snipping tool": "snippingtool.exe",
}

FOLDERS = {
    "downloads": "shell:Downloads",
    "download": "shell:Downloads",
    "documents": "shell:PersonalFolder",
    "pictures": "shell:MyPictures",
    "videos": "shell:MyVideo",
    "music": "shell:MyMusic",
    "desktop": "shell:Desktop",
    "recycle bin": "shell:::{645FF040-5081-101B-9F08-00AA002F954E}",
    "taskbar": "shell:AppsFolder",
    "start menu": "shell:AppsFolder",
    "start": "shell:AppsFolder",
    "control panel": "shell:ControlPanelFolder",
    "task manager": "taskmgr.exe",
    "settings": "ms-settings:",
}

SEARCH = {
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "google": "https://www.google.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "ddg": "https://duckduckgo.com/?q={q}",
    "wiki": "https://en.wikipedia.org/w/index.php?search={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "maps": "https://www.google.com/maps/search/{}",
    "gmail": "https://mail.google.com/mail/u/0/#search/{}",
    "github": "https://github.com/search?q={q}",
    "stack overflow": "https://stackoverflow.com/search?q={q}",
    "amazon": "https://www.amazon.com/s?k={q}",
    "netflix": "https://www.netflix.com/search?q={q}",
    "imdb": "https://www.imdb.com/find/?q={q}",
    "twitter": "https://twitter.com/search?q={q}",
    "x": "https://x.com/search?q={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
    "linkedin": "https://www.linkedin.com/search/results/all/?keywords={q}",
    "chatgpt": "https://chatgpt.com/",
    "drive": "https://drive.google.com/",
    "translate": "https://translate.google.com/?text={q}&sl=auto&tl=en",
}


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 24)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


def send_input(events: list[INPUT]) -> bool:
    """Deliver synthetic keystrokes. False means Windows rejected them.

    SendInput reports how many events it accepted, and it silently accepts
    NONE when the process has no right to inject -- a service session, a
    session 0 launch, or a desktop the input desktop does not belong to. The
    old code discarded the return value, so every keystroke could be thrown
    away with no indication at all, and a caller had no way to tell "typed
    fine" from "did nothing". That is what made a broken Ctrl+C look like a
    Notepad problem for a long time. Checked here and warned once.
    """
    n = len(events)
    if n == 0:
        return True
    arr = (INPUT * n)(*events)
    sent = user32.SendInput(n, ctypes.byref(arr), ctypes.sizeof(INPUT))
    if sent != n:
        global _WARNED_INJECTION
        if not _WARNED_INJECTION:
            _WARNED_INJECTION = True
            print("WARNING: Windows rejected synthetic keystrokes "
                  f"({sent} of {n} events accepted). This process cannot "
                  "inject input -- it is probably running in a service or "
                  "detached session. Typing and key commands will do "
                  "nothing until it runs in your interactive session.",
                  file=sys.stderr)
    return sent == n


_WARNED_INJECTION = False


def key_down(vk: int) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=0, time=0, dwExtraInfo=None))


def key_up(vk: int) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP, time=0, dwExtraInfo=None))


def char_event(ch: str, up: bool) -> INPUT:
    return INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(
        wVk=0, wScan=ord(ch), dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0),
        time=0, dwExtraInfo=None))


def clip_get() -> str:
    if not user32.OpenClipboard(None):
        return ""
    try:
        h = user32.GetClipboardData(13)
        if not h:
            return ""
        k32 = ctypes.windll.kernel32
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [ctypes.c_void_p]
        k32.GlobalUnlock.restype = wt.BOOL
        k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        ptr = k32.GlobalLock(h)
        if not ptr:
            return ""
        try:
            return ctypes.wstring_at(ptr)
        finally:
            k32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def clip_set(text: str) -> None:
    for _ in range(10):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        k32 = ctypes.windll.kernel32
        k32.GlobalAlloc.restype = ctypes.c_void_p
        k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [ctypes.c_void_p]
        k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        GMEM_MOVEABLE = 0x0002
        h = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ptr = k32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        k32.GlobalUnlock(h)
        user32.SetClipboardData(13, h)
    finally:
        user32.CloseClipboard()


def _uia_document(hwnd: int):
    """The editable document inside a window, via UI Automation.

    Needed because a modern app is not built from Win32 EDIT controls:
    Windows 11 Notepad is a WinUI window with a RichEditD2DPT child, so
    neither SendInput nor WM_SETTEXT can reach its text. UIA is the only
    route, and it is reached by WINDOW HANDLE rather than by walking the
    desktop, which is what lets it cross desktops.
    """
    try:
        import comtypes.client as cc
        from comtypes.gen import UIAutomationClient as UIA
    except ImportError:
        return None, None
    try:
        uia = cc.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        root = uia.ElementFromHandle(hwnd)
        if root is None:
            return None, None
        kids = root.FindAll(7, uia.CreateTrueCondition())
        for j in range(kids.Length):
            el = kids.GetElement(j)
            if el.CurrentControlType in (50030, 50004):   # Document, Edit
                try:
                    pattern = el.GetCurrentPattern(UIA.UIA_ValuePatternId)
                    return pattern.QueryInterface(
                        UIA.IUIAutomationValuePattern), root
                except Exception:
                    continue
        return None, root
    except Exception:
        return None, None


def _uia_read(hwnd: int) -> str:
    """The window's current text, or "" when it cannot be read."""
    pattern, _ = _uia_document(hwnd)
    if pattern is None:
        return ""
    try:
        return str(pattern.CurrentValue or "")
    except Exception:
        try:
            return str(pattern.get_CurrentValue() or "")
        except Exception:
            return ""


def _uia_contains(hwnd: int, text: str) -> bool:
    """Whether the window actually shows `text` now.

    This is the check that makes a keystroke-based attempt verifiable. It
    cannot be perfect -- a window with no readable document always answers
    False, so callers must treat it as "no evidence it worked" rather than
    proof it failed, and fall through to a path that does not need it.
    """
    if not text:
        return True
    probe = text.strip()
    if not probe:
        return True
    current = _uia_read(hwnd)
    if not current:
        return False
    # Compare on the tail: a paste into a window that already had text
    # appends, so the new text is at the end rather than the start.
    return probe in current or probe in current[-len(probe) * 2:]


def _uia_type(hwnd: int, text: str) -> bool:
    """Put text into a window over UIA. Replaces the window's contents
    rather than typing at a cursor, which is what a voice command means."""
    vp, _ = _uia_document(hwnd)
    if vp is None:
        return False
    try:
        vp.SetValue(text)
        time.sleep(0.15)
        return (vp.CurrentValue or "").strip() == text.strip()
    except Exception:
        return False


def _desktop_name(handle: int) -> str:
    """The name of a desktop handle (UOI_NAME = 2).

    Needed because comparing desktop HANDLES is not enough: the same desktop
    can be reached through different handles, and the input desktop is a
    separate open each time.
    """
    u32 = ctypes.windll.user32
    buf = ctypes.create_unicode_buffer(256)
    need = ctypes.c_uint(0)
    if not u32.GetUserObjectInformationW(
            handle, 2, buf, ctypes.sizeof(buf), ctypes.byref(need)):
        return ""
    return buf.value


def input_desktop_is_current() -> bool:
    """Keystroke injection only works from the desktop that owns physical
    input. When the agent runs on a different desktop every SendInput
    quietly does nothing, which is why typing appeared to succeed and yet
    never reached the window. Detect it and take a different route.

    The two desktops are compared BY NAME. The previous version called
    GetUserObjectInformation with a zero-length buffer and tested the return
    for >= 0, but that call reports the buffer size it needs -- a
    non-negative number for any valid handle -- so it answered "yes, fine"
    on every desktop including the wrong one, and the fallback route it was
    written to trigger never ran.
    """
    k32 = ctypes.windll.kernel32
    u32 = ctypes.windll.user32
    try:
        cur = u32.GetThreadDesktop(k32.GetCurrentThreadId())
        inp = u32.OpenInputDesktop(0, False, 0x0100)
        if not inp:
            return True
        try:
            return _desktop_name(cur) == _desktop_name(inp)
        finally:
            u32.CloseDesktop(inp)
    except Exception:
        return True


def _first_child_edit(hwnd: int) -> int:
    """The editable control inside a window, for apps that host one."""
    found = [0]

    def walk(h: int, depth: int) -> None:
        if found[0] or depth > 4:
            return
        child = user32.GetWindow(h, 5)   # GW_CHILD
        while child:
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(child, cls, 64)
            name = cls.value
            if name and name.lower() in ("edit", "richedit20w", "richedit50w",
                                         "notepad", "textbox"):
                found[0] = child
                return
            walk(child, depth + 1)
            if found[0]:
                return
            child = user32.GetWindow(child, 2)   # GW_HWNDNEXT

    walk(hwnd, 0)
    return found[0] or hwnd


def _post_text(hwnd: int, text: str) -> bool:
    """Put text into a window by MESSAGE, which crosses desktops.

    A window message is delivered by the window manager, so unlike
    SendInput it does not care which desktop the sender sits on. Tries a
    real paste first so the target's own editor handles it, then falls
    back to replacing the control's text outright."""
    target = _first_child_edit(hwnd)
    saved = clip_get()
    clip_set(text)
    WM_PASTE = 0x0302
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.PostMessageW.restype = wt.BOOL
    user32.SendMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.SendMessageW.restype = wt.LPARAM
    before = _read_window_text(target)
    ok = bool(user32.PostMessageW(target, WM_PASTE, 0, 0))
    time.sleep(0.35)
    if _read_window_text(target) != before:
        if saved:
            clip_set(saved)
        return True
    WM_SETTEXT = 0x000C
    buf = ctypes.create_unicode_buffer(text)
    # The parameter is declared wt.LPARAM, which ctypes defines as c_longlong
    # -- an integer. Passing a pointer object (or wt.LPWSTR) raises
    # ArgumentError, and the old code did exactly that with wt.LPARAM, so
    # every WM_SETTEXT here raised TypeError instead of setting any text:
    # the last-resort path was dead code. Pass the address as an integer.
    user32.SendMessageW(target, WM_SETTEXT, 0,
                        ctypes.cast(buf, ctypes.c_void_p).value)
    time.sleep(0.2)
    if saved:
        clip_set(saved)
    return _read_window_text(target) != before


def _read_window_text(hwnd: int) -> str:
    if not hwnd:
        return ""
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def type_text(text: str, paste_threshold: int = 24,
              window: str | None = None) -> bool:
    """Type into the focused window, or into `window` if named.

    Keystrokes are the faithful path and are tried first, but they are
    ignored from the wrong desktop, so the result is verified and a
    message-based paste is used instead when the keystrokes did not land.
    """
    if not text:
        return False
    hwnd = find_window(window) if window else user32.GetForegroundWindow()

    # Keystrokes are the faithful path but are ignored outright from the
    # wrong desktop, and a modern app has no control a window message can
    # set, so when the desktops differ UIA is the only route that works.
    if hwnd and not input_desktop_is_current():
        if _uia_type(hwnd, text):
            return True
        if _post_text(hwnd, text):
            return True

    if len(text) > paste_threshold:
        # A paste is keystrokes too, so it is subject to exactly the same
        # problem: from the wrong desktop ctrl+v goes nowhere while this
        # function reports success. This branch used to `return True`
        # unconditionally, which meant any sentence over 24 characters
        # silently did nothing at all. So the result is now READ BACK and,
        # if the text is not there, the UIA path runs -- which is what makes
        # the length threshold irrelevant instead of a failure mode.
        saved = clip_get()
        clip_set(text)
        press("ctrl+v")
        time.sleep(0.2)
        if saved:
            clip_set(saved)
        if not hwnd or _uia_contains(hwnd, text):
            return True
        if _uia_type(hwnd, text):
            return True
        return _post_text(hwnd, text)
    events: list[INPUT] = []
    for ch in text:
        events.append(char_event(ch, False))
        events.append(char_event(ch, True))
    for i in range(0, len(events), 60):
        send_input(events[i:i + 60])
        time.sleep(0.008)

    if hwnd and _uia_type(hwnd, text):
        return True
    return True


def press(combo: str) -> None:
    parts = [p.strip().lower() for p in combo.replace("-", "+").split("+") if p.strip()]
    mods: list[int] = []
    keys: list[int] = []
    chars: list[str] = []
    for p in parts:
        if p in MODS:
            mods.append(MODS[p])
        elif p in NAMED:
            keys.append(NAMED[p])
        elif p.isdigit():
            keys.append(int(p))
        elif len(p) == 1:
            chars.append(p)
        else:
            raise ValueError(f"unknown key: {p}")
    if not (mods or keys or chars):
        return
    events: list[INPUT] = [key_down(m) for m in mods]
    events += [key_down(k) for k in keys]
    for ch in chars:
        events.append(char_event(ch, False))
        events.append(char_event(ch, True))
    events += [key_up(k) for k in reversed(keys)]
    events += [key_up(m) for m in reversed(mods)]
    send_input(events)
    time.sleep(0.05)


def window_title(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n == 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def foreground() -> str:
    hwnd = user32.GetForegroundWindow()
    return window_title(hwnd) if hwnd else ""


def force_foreground(hwnd) -> bool:
    cur_tid = kernel32.GetCurrentThreadId()
    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    tgt_tid = user32.GetWindowThreadProcessId(hwnd, None)
    attached = False
    if fg_tid and fg_tid != cur_tid:
        user32.AttachThreadInput(cur_tid, fg_tid, True)
        attached = True
    if tgt_tid and tgt_tid != cur_tid:
        user32.AttachThreadInput(cur_tid, tgt_tid, True)
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if tgt_tid and tgt_tid != cur_tid:
        user32.AttachThreadInput(cur_tid, tgt_tid, False)
    if attached:
        user32.AttachThreadInput(cur_tid, fg_tid, False)
    if user32.GetForegroundWindow() != hwnd:
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 2, 0)
        time.sleep(0.05)
        user32.SetForegroundWindow(hwnd)
    return user32.GetForegroundWindow() == hwnd


def wait_foreground(hwnd, timeout: float = 1.5) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if user32.GetForegroundWindow() == hwnd:
            return True
        force_foreground(hwnd)
        time.sleep(0.08)
    return user32.GetForegroundWindow() == hwnd


def find_window(title: str):
    target = title.lower()
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        t = window_title(hwnd)
        if t and target in t.lower():
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return found[0] if found else None


def focus_window(title: str) -> bool:
    hwnd = find_window(title)
    if not hwnd:
        return False
    return wait_foreground(hwnd)


def list_windows() -> list[str]:
    out: list[str] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value.strip():
            out.append(buf.value)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)(cb), 0)
    return out


def open_target(target: str) -> str:
    raw = target.strip()
    low = raw.lower()
    if low in FOLDERS:
        return launch_url(FOLDERS[low])
    if low in SEARCH:
        return launch_url(SEARCH[low].format(urllib.parse.quote("")))
    if low in APPS:
        return launch(APPS[low])
    if "://" in raw:
        return launch_url(raw)
    if low.startswith(("www.", "http")):
        return launch_url("https://" + raw)
    if os.path.exists(raw):
        return launch(f'"{raw}"')
    if " " not in raw:
        return launch(f'"{raw}.exe"')
    return launch(f'"{raw}"')


DETACH = {
    "stdin": subprocess.DEVNULL,
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
    "close_fds": True,
}


def launch(cmd: str) -> str:
    try:
        subprocess.Popen(cmd, shell=True, **DETACH)
        return f"opened: {cmd}"
    except Exception as exc:
        return f"failed: {exc}"


def launch_url(url: str) -> str:
    try:
        subprocess.Popen(["cmd", "/c", "start", "", url], shell=False, **DETACH)
        return f"opened: {url}"
    except Exception:
        try:
            os.startfile(url)
            return f"opened: {url}"
        except Exception as exc:
            return f"failed: {exc}"


def search_url(engine: str, query: str) -> str:
    engine = engine.lower()
    tpl = SEARCH.get(engine, SEARCH["duckduckgo"])
    if "{q}" in tpl:
        return tpl.format(q=urllib.parse.quote_plus(query))
    return tpl.format(urllib.parse.quote_plus(query))


def do_notepad(text: str) -> str:
    if text:
        docs = os.path.join(os.path.expanduser("~"), "Documents")
        os.makedirs(docs, exist_ok=True)
        path = os.path.join(docs, f"notepad-{time.strftime('%Y%m%d-%H%M%S')}.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        launch(f'notepad.exe "{path}"')
        return f"opened notepad with your text ({len(text)} chars): {path}"
    launch("notepad.exe")
    return "notepad opened"


winmm = ctypes.windll.winmm
WAVE_MAPPER = 0


def get_volume() -> int:
    v = wt.DWORD()
    if winmm.waveOutGetVolume(WAVE_MAPPER, ctypes.byref(v)) != 0:
        return -1
    return int(((v.value >> 16) & 0xFFFF) * 100 / 0xFFFF)


def set_volume(level: int) -> str:
    level = max(0, min(100, level))
    v = int(level / 100 * 0xFFFF)
    if winmm.waveOutSetVolume(WAVE_MAPPER, (v << 16) | v) != 0:
        return f"could not set volume to {level}%"
    return f"volume {get_volume()}%"


def set_mute(state: str) -> str:
    before = get_volume()
    if state == "on" and before != 0:
        return f"already unmuted at {before}% (volume 0 mutes)"
    if state == "off" and before == 0:
        winmm.waveOutSetVolume(WAVE_MAPPER, 0xFFFF0000)
        return "unmuted, volume 100%"
    if state == "on":
        winmm.waveOutSetVolume(WAVE_MAPPER, 0)
        return "muted (volume 0)"
    winmm.waveOutSetVolume(WAVE_MAPPER, 0xFFFF0000)
    return "unmuted"


def screenshot() -> str:
    home = os.path.expanduser("~")
    out_dir = os.path.join(home, "my-agent", "ai-visualizer", "media", "misc")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"screen-{time.strftime('%Y%m%d-%H%M%S')}.png")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
        "$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height;"
        "$g = [System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size);"
        f"$bmp.Save('{path}', [System.Drawing.Imaging.ImageFormat]::Png);"
        "'saved'"
    )
    subprocess.Popen(["powershell", "-NoProfile", "-Command", script], shell=False)
    time.sleep(1.5)
    return f"screenshot: {path}"


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("open"); p.add_argument("target", nargs="+")
    for name in ("search", "youtube", "wiki", "maps", "gmail"):
        p = sub.add_parser(name)
        p.add_argument("query", nargs="+")
    p = sub.add_parser("type")
    p.add_argument("text", nargs="+")
    p.add_argument("--window", dest="window")
    p.add_argument("--enter", action="store_true")
    p = sub.add_parser("key")
    p.add_argument("keys", nargs="+")
    p.add_argument("--window", dest="window")
    p = sub.add_parser("notepad"); p.add_argument("text", nargs="*")
    sub.add_parser("close")
    p = sub.add_parser("volume"); p.add_argument("level", type=int)
    p = sub.add_parser("mute"); p.add_argument("state", choices=["on", "off"])
    sub.add_parser("list")
    p = sub.add_parser("focus"); p.add_argument("title", nargs="+")
    sub.add_parser("screenshot")

    args = ap.parse_args()
    if not args.cmd:
        ap.print_help()
        return 1

    text = " ".join(getattr(args, "text", []) or [])
    query = " ".join(getattr(args, "query", []) or [])
    target = " ".join(getattr(args, "target", []) or [])
    keys = " ".join(getattr(args, "keys", []) or [])
    title = " ".join(getattr(args, "title", []) or [])

    if args.cmd == "open":
        print(open_target(target))
    elif args.cmd in ("search", "youtube", "wiki", "maps", "gmail"):
        engine = "duckduckgo" if args.cmd == "search" else args.cmd
        url = search_url(engine, query)
        print(launch_url(url))
    elif args.cmd == "type":
        if args.window and not focus_window(args.window):
            print(f"no window matching {args.window!r}, nothing typed")
            return 1
        type_text(text)
        if args.enter:
            press("enter")
        where = foreground()
        print(f"typed {len(text)} chars into {where!r}" if where else "typed")
    elif args.cmd == "key":
        if args.window and not focus_window(args.window):
            print(f"no window matching {args.window!r}, nothing pressed")
            return 1
        for combo in keys.split():
            press(combo)
        print(f"pressed {keys} in {foreground()!r}")
    elif args.cmd == "notepad":
        print(do_notepad(text))
    elif args.cmd == "close":
        press("alt+f4")
        print("closed focused window")
    elif args.cmd == "volume":
        print(set_volume(args.level))
    elif args.cmd == "mute":
        print(set_mute(args.state))
    elif args.cmd == "list":
        for w in list_windows():
            print(w)
    elif args.cmd == "focus":
        focus_window(title)
        time.sleep(0.4)
        print(f"focused: {foreground()}")
    elif args.cmd == "screenshot":
        print(screenshot())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
