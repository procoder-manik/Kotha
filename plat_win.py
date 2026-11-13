"""Windows platform layer for Kotha: global hotkey hook, paste keystroke, overlay window flags,
text-box detection, sounds, start-with-Windows. kotha.py talks to the OS only through this interface
(the macOS twin is plat_mac.py)."""

import ctypes
import os
import sys
import threading
import time
import winreg
import winsound
from ctypes import wintypes

try:
    import uiautomation as uia
except Exception:
    uia = None

IS_WIN, IS_MAC = True, False
OS_NAME = "Windows"
FONT = "Nirmala UI"          # ships with Windows 10/11, renders Bangla and Devanagari well
OVERLAY_KEY = "#010203"      # colour that becomes see-through on the overlay window
DEFAULT_START_AT_LOGIN = True
START_AT_LOGIN_LABEL = "Windows চালু হলে Kotha চালু হবে"
PROXY_HINT = "Windows proxy settings চেক করো"
NO_OFFLINE_HINT = "Offline engine এই ভার্শনে নেই। দরকার হলে Kotha-Windows-Offline-Setup.exe ইনস্টল করো।"
USER_DATA_BASE = os.environ.get("APPDATA", os.path.expanduser("~"))

_log = lambda msg: None


def set_logger(fn):
    global _log
    _log = fn


# --------------------------------------------------------------------------
# Hotkey tables (key codes are Windows virtual-key codes)
# --------------------------------------------------------------------------
TOKEN_VKS = {
    "ctrl": {0x11, 0xA2, 0xA3}, "lctrl": {0xA2}, "rctrl": {0xA3},
    "alt": {0x12, 0xA4, 0xA5}, "lalt": {0xA4}, "ralt": {0xA5},
    "shift": {0x10, 0xA0, 0xA1}, "lshift": {0xA0}, "rshift": {0xA1},
    "win": {0x5B, 0x5C}, "space": {0x20}, "capslock": {0x14}, "tab": {0x09},
    "insert": {0x2D}, "pause": {0x13}, "scrolllock": {0x91}, "apps": {0x5D}, "backtick": {0xC0},
}
for _i in range(24):
    TOKEN_VKS[f"f{_i + 1}"] = {0x70 + _i}
for _c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
    TOKEN_VKS[_c.lower()] = {ord(_c)}

MOD_TOKENS = {"ctrl", "lctrl", "rctrl", "alt", "lalt", "ralt", "shift", "lshift", "rshift", "win"}
SOLO_OK = {"ralt", "rctrl", "lalt", "rshift", "capslock", "insert", "pause", "scrolllock", "apps"} | \
          {f"f{i}" for i in range(1, 25)}
TOKEN_LABEL = {"ctrl": "Ctrl", "lctrl": "Left Ctrl", "rctrl": "Right Ctrl", "alt": "Alt",
               "lalt": "Left Alt", "ralt": "Right Alt", "shift": "Shift", "lshift": "Left Shift",
               "rshift": "Right Shift", "win": "Win", "space": "Space", "capslock": "Caps Lock",
               "tab": "Tab", "insert": "Insert", "pause": "Pause", "scrolllock": "Scroll Lock",
               "apps": "Menu", "backtick": "`"}
ORDER = ["ctrl", "lctrl", "rctrl", "alt", "lalt", "ralt", "shift", "lshift", "rshift", "win"]
DEFAULT_HOTKEY = ["ctrl", "win"]
DEFAULT_NOTE_HOTKEY = ["win", "alt", "m"]     # start / stop meeting notes
NOTE_HOTKEY_PRESETS = [["win", "alt", "m"], ["ctrl", "alt", "m"], ["ctrl", "shift", "m"], ["f8"]]
SUPPORTS_MEETINGS = True
HOTKEY_PRESETS = [
    ["ctrl", "win"], ["ctrl", "alt"], ["ralt"], ["rctrl"], ["alt", "win"],
    ["ctrl", "shift", "space"], ["f9"], ["capslock"],
]
VK_SIDE = {0xA2: "lctrl", 0xA3: "rctrl", 0xA4: "lalt", 0xA5: "ralt", 0xA0: "lshift", 0xA1: "rshift",
           0x11: "ctrl", 0x12: "alt", 0x10: "shift", 0x5B: "win", 0x5C: "win"}
VK_GENERIC = {0xA2: "ctrl", 0xA3: "ctrl", 0xA4: "alt", 0xA5: "alt", 0xA0: "shift", 0xA1: "shift",
              0x11: "ctrl", 0x12: "alt", 0x10: "shift", 0x5B: "win", 0x5C: "win"}
MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C}

# --------------------------------------------------------------------------
# Win32
# --------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_SYSKEYDOWN = 0x100, 0x104
LLKHF_INJECTED = 0x10
VK_ESCAPE, VK_CONTROL, VK_V, VK_MASK = 0x1B, 0x11, 0x56, 0xE8
KEYEVENTF_KEYUP = 0x2
ESC_VK = VK_ESCAPE


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hwndActive", wintypes.HWND), ("hwndFocus", wintypes.HWND),
                ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND),
                ("rcCaret", wintypes.RECT)]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.SetWindowsHookExW.argtypes = (ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
user32.CallNextHookEx.restype = ctypes.c_ssize_t
user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
user32.keybd_event.argtypes = (wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t)
user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.POINTER(GUITHREADINFO))
user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_long)
user32.GetParent.argtypes = (wintypes.HWND,)
user32.GetParent.restype = wintypes.HWND
kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
kernel32.CreateMutexW.restype = wintypes.HANDLE


def key_is_down(vk):
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def send_mask_key():
    # An unassigned key between Win/Alt down and up stops the Start menu / menu bar from opening.
    user32.keybd_event(VK_MASK, 0, 0, 0)
    user32.keybd_event(VK_MASK, 0, KEYEVENTF_KEYUP, 0)


def wait_keys_released(vks, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end and any(key_is_down(v) for v in vks):
        time.sleep(0.01)


def press_paste():
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def init_overlay(win, key):
    """Borderless, always-on-top, click-through pill that never steals focus."""
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg=key)
    win.attributes("-transparentcolor", key)
    win.attributes("-alpha", 0.96)


def init_float(win, key):
    """Borderless, always-on-top, see-through window for the floating handle and pop-up cards."""
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg=key)
    win.attributes("-transparentcolor", key)


SYMBOL_FONT = "Segoe UI Symbol"


def open_folder(path):
    try:
        os.startfile(path)
    except Exception:
        pass


def make_no_activate(widget, click_through=True):
    """Never take focus from the app you are typing in. click_through=False keeps the window clickable."""
    GWL_EXSTYLE = -20
    style_add = 0x08000000 | 0x80 | 0x8                    # NOACTIVATE|TOOLWINDOW|TOPMOST
    if click_through:
        style_add |= 0x20 | 0x80000                        # TRANSPARENT|LAYERED
    widget.update_idletasks()
    hwnd = user32.GetParent(widget.winfo_id()) or widget.winfo_id()
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, user32.GetWindowLongW(hwnd, GWL_EXSTYLE) | style_add)


def hide_overlay(win, w, h):
    win.geometry(f"{w}x{h}+-4000+-4000")


def show_overlay(win):
    pass


def work_area(widget=None):
    rect = wintypes.RECT()
    ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)
    return rect.left, rect.top, rect.right, rect.bottom


def style_theme(style):
    try:
        style.theme_use("vista")
    except Exception:
        pass


def set_window_icon(root, resource):
    try:
        root.iconbitmap(default=resource("assets/kotha.ico"))
    except Exception:
        pass


def bring_to_front():
    pass


def open_privacy_pane(which):
    pass


def request_permissions(log_fn=None):
    pass


# --------------------------------------------------------------------------
# Is there a text box to type into?
# --------------------------------------------------------------------------
def _uia_editable():
    el = uia.GetFocusedControl()
    if not el:
        return False
    CT = uia.ControlType
    vp = None
    try:
        vp = el.GetValuePattern()
    except Exception:
        pass
    if el.ControlType in (CT.EditControl, CT.DocumentControl, CT.ComboBoxControl):
        return not vp.IsReadOnly if vp is not None else True
    return vp is not None and not vp.IsReadOnly


def focused_is_editable():
    """True = text box, False = definitely not, None = unknown (paste anyway)."""
    try:
        info = GUITHREADINFO()
        info.cbSize = ctypes.sizeof(GUITHREADINFO)
        if user32.GetGUIThreadInfo(0, ctypes.byref(info)) and info.hwndCaret:
            return True
    except Exception:
        pass
    if uia is None:
        return None
    try:
        with uia.UIAutomationInitializerInThread():
            return _uia_editable()
    except Exception as e:
        _log(f"UIA probe failed: {e}")
        return None


# --------------------------------------------------------------------------
# Global keyboard hook with configurable shortcut
# --------------------------------------------------------------------------
class HotkeyHook(threading.Thread):
    def __init__(self, out_q, ui_q, tokens):
        super().__init__(daemon=True)
        self.q, self.ui_q = out_q, ui_q
        self.down = set()
        self.combo = False
        self.swallowed = set()
        self.capturing = False
        self.cap_down, self.cap_all = set(), set()
        self.set_hotkey(tokens)
        self.set_toggle_hotkey([])
        self._proc = HOOKPROC(self._callback)

    def set_hotkey(self, tokens):
        groups = [TOKEN_VKS[t] for t in tokens if t in TOKEN_VKS]
        all_vks = set().union(*groups) if groups else set()
        swallow = set()
        for t in tokens:
            if t not in MOD_TOKENS:
                swallow |= TOKEN_VKS.get(t, set())
        self.groups, self.combo_vks, self.swallow_vks = groups, all_vks, swallow
        self.needs_mask = any(t in ("alt", "lalt", "ralt", "win") for t in tokens)
        self.combo = False

    def set_toggle_hotkey(self, tokens):
        """A second shortcut that fires once per press (used to start/stop meeting notes)."""
        self.tgroups = [TOKEN_VKS[t] for t in tokens if t in TOKEN_VKS]
        self.t_swallow = set()
        for t in tokens:
            if t not in MOD_TOKENS:
                self.t_swallow |= TOKEN_VKS.get(t, set())
        self.t_mask = any(t in ("alt", "lalt", "ralt", "win") for t in tokens)
        self.tcombo = False

    def start_capture(self):
        self.cap_down, self.cap_all = set(), set()
        self.capturing = True

    def stop_capture(self):
        self.capturing = False

    def _active(self):
        return bool(self.groups) and all(self.down & g for g in self.groups)

    def _callback(self, n_code, w_param, l_param):
        if n_code == 0:
            kb = ctypes.cast(l_param, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            if not (kb.flags & LLKHF_INJECTED):
                vk = kb.vkCode
                down = w_param in (WM_KEYDOWN, WM_SYSKEYDOWN)
                if self.capturing:
                    if down:
                        self.cap_down.add(vk)
                        self.cap_all.add(vk)
                    else:
                        self.cap_down.discard(vk)
                        if not self.cap_down and self.cap_all:
                            self.capturing = False
                            self.ui_q.put(("captured", set(self.cap_all)))
                    return 1
                # forget keys whose key-up we missed (lock screen, UAC, ...)
                for k in list(self.down):
                    if k != vk and not key_is_down(k):
                        self.down.discard(k)
                if down:
                    if self.combo and vk not in self.combo_vks:
                        self.q.put(("other",))
                    if vk == VK_ESCAPE:
                        self.q.put(("esc",))
                    self.down.add(vk)
                else:
                    self.down.discard(vk)
                now = self._active()
                if now and not self.combo:
                    self.combo = True
                    self.q.put(("down", time.monotonic(), self.needs_mask))
                elif not now and self.combo:
                    self.combo = False
                    self.q.put(("up", time.monotonic()))
                if self.tgroups:
                    tnow = all(self.down & g for g in self.tgroups)
                    if tnow and not self.tcombo:
                        self.tcombo = True
                        self.ui_q.put(("note_toggle", self.t_mask))
                    elif not tnow and self.tcombo:
                        self.tcombo = False
                    if vk in self.t_swallow:
                        if down and self.tcombo:
                            self.swallowed.add(vk)
                            return 1
                        if not down and vk in self.swallowed:
                            self.swallowed.discard(vk)
                            return 1
                if vk in self.swallow_vks:
                    if down and self.combo:
                        self.swallowed.add(vk)
                        return 1
                    if not down and vk in self.swallowed:
                        self.swallowed.discard(vk)
                        return 1
        return user32.CallNextHookEx(None, n_code, w_param, l_param)

    def run(self):
        self.hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not self.hook:
            _log(f"SetWindowsHookEx failed: {ctypes.get_last_error()}")
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))


# --------------------------------------------------------------------------
# Sounds, start at login, single instance, DPI
# --------------------------------------------------------------------------
def play_beep(kind):
    try:
        if kind == "start":
            winsound.Beep(700, 35); winsound.Beep(940, 45)
        elif kind == "stop":
            winsound.Beep(940, 35); winsound.Beep(700, 45)
        else:
            winsound.Beep(400, 80)
    except Exception:
        pass


def set_startup(enabled, app_name):
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}"'
    else:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kotha.py")
        cmd = f'"{sys.executable.replace("python.exe", "pythonw.exe")}" "{script}"'
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, app_name, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, app_name)
                except FileNotFoundError:
                    pass
    except Exception as e:
        _log(f"startup registry: {e}")


_mutex = None


def acquire_single_instance(app_name):
    """True if we are the only running copy."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, "Global\\KothaVoiceTyping_SingleInstance")
    return ctypes.get_last_error() != 183


def already_running_notice(app_name):
    ctypes.windll.user32.MessageBoxW(None, "Kotha আগে থেকেই চালু আছে (system tray দেখো)।", app_name, 0x40)


def setup_process():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Meeting detection: which call app is holding the microphone right now?
# Windows records this per app under CapabilityAccessManager (LastUsedTimeStop == 0 while in use).
# --------------------------------------------------------------------------
_MIC_STORE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"
MEETING_EXES = {
    "zoom.exe": "Zoom", "ms-teams.exe": "Microsoft Teams", "teams.exe": "Microsoft Teams", "slack.exe": "Slack",
    "discord.exe": "Discord", "webex.exe": "Webex", "ciscocollabhost.exe": "Webex", "webexmta.exe": "Webex",
    "atmgr.exe": "Webex", "skype.exe": "Skype", "whatsapp.exe": "WhatsApp", "telegram.exe": "Telegram",
    "lark.exe": "Lark", "feishu.exe": "Lark", "ringcentral.exe": "RingCentral", "g2mcomm.exe": "GoTo Meeting",
    "gotomeeting.exe": "GoTo Meeting", "chime.exe": "Amazon Chime", "signal.exe": "Signal", "viber.exe": "Viber",
    "messenger.exe": "Messenger", "zoho meeting.exe": "Zoho Meeting", "bluejeans.exe": "BlueJeans",
}
BROWSER_EXES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "arc.exe",
                "comet.exe", "chromium.exe", "yandex.exe", "thorium.exe", "dia.exe", "zen.exe", "floorp.exe"}
PACKAGED_APPS = {"msteams": "Microsoft Teams", "whatsappdesktop": "WhatsApp", "slack": "Slack", "zoom": "Zoom",
                 "skypeapp": "Skype", "telegram": "Telegram", "messenger": "Messenger", "discord": "Discord"}
WEB_MEETINGS = [("Google Meet", "Google Meet"), ("Meet - ", "Google Meet"), ("meet.google.com", "Google Meet"),
                ("Zoom", "Zoom"), ("Microsoft Teams", "Microsoft Teams"), ("Teams", "Microsoft Teams"),
                ("Webex", "Webex"), ("Jitsi", "Jitsi Meet"), ("Whereby", "Whereby"), ("Discord", "Discord"),
                ("Slack", "Slack"), ("Messenger", "Messenger"), ("WhatsApp", "WhatsApp"), ("StreamYard", "StreamYard"),
                ("Riverside", "Riverside")]


def _mic_users():
    """[(app id, is_packaged)] of apps whose microphone session is open right now."""
    out = []
    for path, packaged in ((_MIC_STORE, True), (_MIC_STORE + r"\NonPackaged", False)):
        try:
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            continue
        i = 0
        while True:
            try:
                name = winreg.EnumKey(k, i)
            except OSError:
                break
            i += 1
            if name == "NonPackaged":
                continue
            try:
                with winreg.OpenKey(k, name) as sk:
                    stop = winreg.QueryValueEx(sk, "LastUsedTimeStop")[0]
                    start = winreg.QueryValueEx(sk, "LastUsedTimeStart")[0]
                if start and stop == 0:
                    out.append((name.replace("#", "\\"), packaged))
            except OSError:
                pass
        winreg.CloseKey(k)
    return out


_EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = (_EnumProc, wintypes.LPARAM)
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.IsWindowVisible.argtypes = (wintypes.HWND,)


def window_titles():
    titles = []

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                titles.append(buf.value)
        return True
    user32.EnumWindows(_EnumProc(cb), 0)
    return titles


def active_meetings(self_exe="", busy=False):
    """[(key, label)] for call apps using the microphone right now. key == label, so an app that briefly
    reopens the mic (mute/unmute, device switch) still counts as the same meeting."""
    found = {}
    titles = None
    for app_id, packaged in _mic_users():
        low = app_id.lower()
        if self_exe and low == self_exe.lower():
            continue
        if packaged:
            for frag, label in PACKAGED_APPS.items():
                if frag in low:
                    found[label] = label
            continue
        exe = os.path.basename(low)
        if exe in MEETING_EXES:
            found[MEETING_EXES[exe]] = MEETING_EXES[exe]
        elif exe in BROWSER_EXES:
            if titles is None:
                titles = window_titles()
            label = None
            for t in titles:
                for frag, lab in WEB_MEETINGS:
                    if frag in t:
                        label = lab
                        break
                if label:
                    break
            label = label or "ব্রাউজারের কল"
            found[label] = label
    return list(found.items())
