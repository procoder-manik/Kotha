"""macOS platform layer for Kotha (the Windows twin is plat_win.py).

Global shortcut  : Quartz event tap  (needs Input Monitoring + Accessibility permission)
Paste            : Cmd+V posted with Quartz events (needs Accessibility permission)
Text-box check   : Accessibility API (AXUIElement)
Overlay window   : Tk "help" window style with noActivates, so it never steals focus
Start at login   : LaunchAgent plist in ~/Library/LaunchAgents
Single instance  : localhost socket lock
"""

import os
import plistlib
import socket
import subprocess
import sys
import threading
import time

try:
    import Quartz
except Exception:                      # not on a Mac, or pyobjc missing
    Quartz = None

IS_WIN, IS_MAC = False, True
OS_NAME = "macOS"
FONT = "Helvetica Neue"      # Tk falls back to the system Bangla font (Kohinoor Bangla / Bangla Sangam MN)
OVERLAY_KEY = "systemTransparent"
DEFAULT_START_AT_LOGIN = False
START_AT_LOGIN_LABEL = "Mac চালু/লগইন হলে Kotha চালু হবে"
PROXY_HINT = "Mac এর Network proxy settings চেক করো"
NO_OFFLINE_HINT = "Offline engine এখনো Mac ভার্শনে নেই। Gemini বা Groq এর ফ্রি key ব্যবহার করো।"
USER_DATA_BASE = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
LAUNCH_AGENT_ID = "in.manikhossain.kotha"

_log = lambda msg: None


def set_logger(fn):
    global _log
    _log = fn


# --------------------------------------------------------------------------
# Hotkey tables (key codes are macOS virtual key codes, kVK_*)
# --------------------------------------------------------------------------
_LETTERS = {"a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
            "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
            "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40,
            "n": 45, "m": 46}
_FKEYS = {1: 122, 2: 120, 3: 99, 4: 118, 5: 96, 6: 97, 7: 98, 8: 100, 9: 101, 10: 109, 11: 103, 12: 111,
          13: 105, 14: 107, 15: 113, 16: 106, 17: 64, 18: 79, 19: 80, 20: 90}

TOKEN_VKS = {
    "ctrl": {59, 62}, "lctrl": {59}, "rctrl": {62},
    "alt": {58, 61}, "lalt": {58}, "ralt": {61},
    "shift": {56, 60}, "lshift": {56}, "rshift": {60},
    "win": {55, 54}, "space": {49}, "capslock": {57}, "tab": {48}, "backtick": {50},
}
for _n, _code in _FKEYS.items():
    TOKEN_VKS[f"f{_n}"] = {_code}
for _ch, _code in _LETTERS.items():
    TOKEN_VKS[_ch] = {_code}

MOD_TOKENS = {"ctrl", "lctrl", "rctrl", "alt", "lalt", "ralt", "shift", "lshift", "rshift", "win"}
SOLO_OK = {"ralt", "rctrl", "lalt", "rshift"} | {f"f{i}" for i in range(1, 21)}
TOKEN_LABEL = {"ctrl": "Control", "lctrl": "Left Control", "rctrl": "Right Control", "alt": "Option",
               "lalt": "Left Option", "ralt": "Right Option", "shift": "Shift", "lshift": "Left Shift",
               "rshift": "Right Shift", "win": "Command", "space": "Space", "capslock": "Caps Lock",
               "tab": "Tab", "backtick": "`"}
ORDER = ["ctrl", "lctrl", "rctrl", "alt", "lalt", "ralt", "shift", "lshift", "rshift", "win"]
DEFAULT_HOTKEY = ["ralt"]    # hold Right Option, talk, release
HOTKEY_PRESETS = [
    ["ralt"], ["ctrl", "alt"], ["ctrl", "win"], ["rctrl"], ["alt", "win"],
    ["ctrl", "shift", "space"], ["f9"],
]
VK_SIDE = {59: "lctrl", 62: "rctrl", 58: "lalt", 61: "ralt", 56: "lshift", 60: "rshift", 55: "win", 54: "win"}
VK_GENERIC = {59: "ctrl", 62: "ctrl", 58: "alt", 61: "alt", 56: "shift", 60: "shift", 55: "win", 54: "win"}
MODIFIER_VKS = {59, 62, 58, 61, 56, 60, 55, 54}
ESC_VK = 53
V_KEYCODE = 9

# which flag bit a modifier key controls (CGEventFlags)
_FLAG_OF = {56: 0x20000, 60: 0x20000, 59: 0x40000, 62: 0x40000, 58: 0x80000, 61: 0x80000,
            55: 0x100000, 54: 0x100000, 57: 0x10000, 63: 0x800000}


def key_is_down(vk):
    try:
        return bool(Quartz.CGEventSourceKeyState(Quartz.kCGEventSourceStateCombinedSessionState, vk))
    except Exception:
        return False


def send_mask_key():
    pass                     # macOS has no Start-menu style side effect to suppress


def wait_keys_released(vks, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end and any(key_is_down(v) for v in vks):
        time.sleep(0.01)


def press_paste():
    """Cmd+V into the focused app. The event carries only the Command flag, so a still-held shortcut key
    does not turn it into something else."""
    src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for down in (True, False):
        ev = Quartz.CGEventCreateKeyboardEvent(src, V_KEYCODE, down)
        Quartz.CGEventSetFlags(ev, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.012)


# --------------------------------------------------------------------------
# Permissions
# --------------------------------------------------------------------------
_PANES = {
    "accessibility": "Privacy_Accessibility",
    "input": "Privacy_ListenEvent",
    "mic": "Privacy_Microphone",
}


def open_privacy_pane(which):
    try:
        subprocess.Popen(["open", f"x-apple.systempreferences:com.apple.preference.security?{_PANES[which]}"])
    except Exception as e:
        _log(f"open privacy pane: {e!r}")


def request_permissions(log_fn=None):
    """Ask macOS to show its permission prompts (Accessibility + Input Monitoring)."""
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
    except Exception as e:
        _log(f"accessibility prompt: {e!r}")
    try:
        if Quartz is not None and hasattr(Quartz, "CGRequestListenEventAccess"):
            if not Quartz.CGPreflightListenEventAccess():
                Quartz.CGRequestListenEventAccess()
    except Exception as e:
        _log(f"input monitoring prompt: {e!r}")


# --------------------------------------------------------------------------
# Windows, theme, screen
# --------------------------------------------------------------------------
def init_overlay(win, key):
    """Borderless floating pill that never takes focus from the app you are typing in."""
    try:
        win.tk.call("::tk::unsupported::MacWindowStyle", "style", win._w, "help", "noActivates")
    except Exception as e:
        _log(f"MacWindowStyle failed, falling back to overrideredirect: {e!r}")
        win.overrideredirect(True)
    win.attributes("-topmost", True)
    try:
        win.attributes("-transparent", True)
    except Exception:
        pass
    win.configure(bg="systemTransparent")
    win.attributes("-alpha", 0.96)


def make_no_activate(widget):
    pass                     # already done in init_overlay


def hide_overlay(win, w, h):
    win.withdraw()


def show_overlay(win):
    try:
        if win.state() == "withdrawn":
            win.deiconify()
    except Exception:
        pass


def work_area(widget=None):
    """(left, top, right, bottom) of the usable screen, excluding the menu bar and the Dock."""
    try:
        from AppKit import NSScreen
        scr = NSScreen.mainScreen()
        f, v = scr.frame(), scr.visibleFrame()
        top = int(f.size.height - (v.origin.y + v.size.height))
        bottom = int(f.size.height - v.origin.y)
        return int(v.origin.x), top, int(v.origin.x + v.size.width), bottom
    except Exception:
        if widget is not None:
            return 0, 25, widget.winfo_screenwidth(), widget.winfo_screenheight() - 80
        return 0, 25, 1440, 820


def style_theme(style):
    try:
        style.theme_use("aqua")
    except Exception:
        pass


def set_window_icon(root, resource):
    pass                     # the Dock/app icon comes from the .app bundle


def bring_to_front():
    """Kotha is a menu-bar app, so its windows must ask macOS to come to the front."""
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


# --------------------------------------------------------------------------
# Is there a text box to type into?
# --------------------------------------------------------------------------
_TEXT_ROLES = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"}
_NOT_TEXT_ROLES = {"AXButton", "AXImage", "AXList", "AXTable", "AXOutline", "AXRow", "AXToolbar",
                   "AXMenuItem", "AXMenuBar", "AXMenuBarItem", "AXCheckBox", "AXRadioButton", "AXSlider"}


def focused_is_editable():
    """True = text box, False = definitely not, None = unknown (paste anyway)."""
    try:
        import ApplicationServices as AS
        system = AS.AXUIElementCreateSystemWide()
        err, el = AS.AXUIElementCopyAttributeValue(system, AS.kAXFocusedUIElementAttribute, None)
        if err != 0 or el is None:
            return None
        err, role = AS.AXUIElementCopyAttributeValue(el, AS.kAXRoleAttribute, None)
        if err != 0 or not role:
            return None
        if str(role) in _TEXT_ROLES:
            return True
        if str(role) in _NOT_TEXT_ROLES:
            return False
        return None
    except Exception as e:
        _log(f"AX probe failed: {e!r}")
        return None


# --------------------------------------------------------------------------
# Global keyboard hook with configurable shortcut (Quartz event tap)
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
        self.can_swallow = True
        self._tap = None
        self._pid = os.getpid()
        self.set_hotkey(tokens)

    def set_hotkey(self, tokens):
        groups = [TOKEN_VKS[t] for t in tokens if t in TOKEN_VKS]
        all_vks = set().union(*groups) if groups else set()
        swallow = set()
        for t in tokens:
            if t not in MOD_TOKENS:
                swallow |= TOKEN_VKS.get(t, set())
        self.groups, self.combo_vks, self.swallow_vks = groups, all_vks, swallow
        self.needs_mask = False
        self.combo = False

    def start_capture(self):
        self.cap_down, self.cap_all = set(), set()
        self.capturing = True

    def stop_capture(self):
        self.capturing = False

    def _active(self):
        return bool(self.groups) and all(self.down & g for g in self.groups)

    def _out(self, event, swallow=False):
        return None if (swallow and self.can_swallow) else event

    def _cb(self, proxy, etype, event, refcon):
        try:
            if etype in (Quartz.kCGEventTapDisabledByTimeout, Quartz.kCGEventTapDisabledByUserInput):
                if self._tap is not None:
                    Quartz.CGEventTapEnable(self._tap, True)
                return event
            if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGEventSourceUnixProcessID) == self._pid:
                return event                              # our own synthetic Cmd+V
            vk = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
            if etype == Quartz.kCGEventFlagsChanged:
                mask = _FLAG_OF.get(vk)
                if mask is None:
                    return event
                held = self.cap_down if self.capturing else self.down
                down = bool(Quartz.CGEventGetFlags(event) & mask) and vk not in held
            elif etype == Quartz.kCGEventKeyDown:
                if Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat):
                    return self._out(event, vk in self.swallow_vks and self.combo)
                down = True
            elif etype == Quartz.kCGEventKeyUp:
                down = False
            else:
                return event

            if self.capturing:
                if down:
                    self.cap_down.add(vk)
                    self.cap_all.add(vk)
                else:
                    self.cap_down.discard(vk)
                    if not self.cap_down and self.cap_all:
                        self.capturing = False
                        self.ui_q.put(("captured", set(self.cap_all)))
                return self._out(event, True)
            # forget keys whose key-up we missed (screen lock, fast user switching, ...)
            for k in list(self.down):
                if k != vk and not key_is_down(k):
                    self.down.discard(k)
            if down:
                if self.combo and vk not in self.combo_vks:
                    self.q.put(("other",))
                if vk == ESC_VK:
                    self.q.put(("esc",))
                self.down.add(vk)
            else:
                self.down.discard(vk)
            now = self._active()
            if now and not self.combo:
                self.combo = True
                self.q.put(("down", time.monotonic(), False))
            elif not now and self.combo:
                self.combo = False
                self.q.put(("up", time.monotonic()))
            if vk in self.swallow_vks:
                if down and self.combo:
                    self.swallowed.add(vk)
                    return self._out(event, True)
                if not down and vk in self.swallowed:
                    self.swallowed.discard(vk)
                    return self._out(event, True)
        except Exception as e:
            _log(f"event tap callback: {e!r}")
        return event

    def _create_tap(self):
        mask = (Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) | Quartz.CGEventMaskBit(Quartz.kCGEventKeyUp)
                | Quartz.CGEventMaskBit(Quartz.kCGEventFlagsChanged))
        for options, swallow in ((Quartz.kCGEventTapOptionDefault, True), (Quartz.kCGEventTapOptionListenOnly, False)):
            tap = Quartz.CGEventTapCreate(Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
                                          options, mask, self._cb, None)
            if tap:
                self.can_swallow = swallow
                return tap
        return None

    def run(self):
        if Quartz is None:
            _log("pyobjc Quartz is not available, global shortcut disabled")
            return
        warned = False
        while True:
            self._tap = self._create_tap()
            if self._tap:
                break
            if not warned:
                warned = True
                _log("event tap refused: Input Monitoring / Accessibility permission missing")
                self.ui_q.put(("show", "error", "System Settings > Privacy এ Kotha কে Input Monitoring ও Accessibility তে চালু করো"))
                self.ui_q.put(("settings",))
            time.sleep(2.0)
        if warned:
            self.ui_q.put(("show", "note", "অনুমতি পাওয়া গেছে, শর্টকাট চালু"))
        src = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), src, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        Quartz.CFRunLoopRun()


# --------------------------------------------------------------------------
# Sounds, start at login, single instance
# --------------------------------------------------------------------------
_SOUNDS = {"start": "Tink", "stop": "Pop", "cancel": "Basso"}


def play_beep(kind):
    try:
        path = f"/System/Library/Sounds/{_SOUNDS.get(kind, 'Pop')}.aiff"
        subprocess.Popen(["afplay", "-v", "0.5", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def set_startup(enabled, app_name):
    path = os.path.join(os.path.expanduser("~"), "Library", "LaunchAgents", f"{LAUNCH_AGENT_ID}.plist")
    try:
        if not enabled:
            if os.path.exists(path):
                os.remove(path)
            return
        if getattr(sys, "frozen", False):
            args = [sys.executable]
        else:
            args = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kotha.py")]
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            plistlib.dump({"Label": LAUNCH_AGENT_ID, "ProgramArguments": args, "RunAtLoad": True,
                           "ProcessType": "Interactive"}, f)
    except Exception as e:
        _log(f"launch agent: {e!r}")


_lock_sock = None


def acquire_single_instance(app_name):
    global _lock_sock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 48611))
        s.listen(1)
    except OSError:
        return False
    _lock_sock = s
    return True


def already_running_notice(app_name):
    try:
        subprocess.run(["osascript", "-e",
                        f'display dialog "Kotha আগে থেকেই চালু আছে (menu bar দেখো)।" with title "{app_name}" '
                        'buttons {"OK"} default button 1 with icon note'], timeout=30)
    except Exception:
        pass


def setup_process():
    pass
