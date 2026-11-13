"""Kotha design system: colours, fonts, glass window style and reusable components (customtkinter).

Kept free of kotha.py imports so it can be used by any window.
"""
import ctypes
import os
import sys
import tkinter as tk

import customtkinter as ctk

IS_WIN = sys.platform == "win32"

C = {
    "glass": "#000000",          # becomes the Mica backdrop on Windows 11
    "bg": "#0f1116",             # solid background where glass is not available
    "side": "#0b0d12",
    "surface": "#171a21",
    "surface2": "#1e222b",
    "surface3": "#262b36",
    "border": "#2b303c",
    "text": "#f3f5f9",
    "muted": "#a3abbb",
    "faint": "#6c7486",
    "accent": "#8b5cf6",
    "accent_hover": "#7c3aed",
    "accent_soft": "#2a2147",
    "accent_text": "#c4b5fd",
    "green": "#22c55e",
    "green_soft": "#123524",
    "red": "#ef4444",
    "red_hover": "#dc2626",
    "red_soft": "#3b1518",
    "amber": "#f59e0b",
    "amber_soft": "#3a2a0c",
    "link": "#a78bfa",
    "brand": "#006A4E",
}

FAMILY = "Segoe UI"
_FAMILIES = {}
_fonts = {}


def load_fonts(resource):
    """Register the bundled Hind Siliguri fonts (Bangla + Latin) for this process only."""
    global FAMILY
    folder = resource("assets/fonts")
    ok = False
    try:
        for name in os.listdir(folder):
            if not name.lower().endswith(".ttf"):
                continue
            path = os.path.join(folder, name)
            if IS_WIN:
                ok |= bool(ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0))   # FR_PRIVATE
            else:
                try:
                    ctk.FontManager.load_font(path)
                    ok = True
                except Exception:
                    pass
    except OSError:
        pass
    if ok:
        FAMILY = "Hind Siliguri"
        _FAMILIES.update({"regular": "Hind Siliguri", "medium": "Hind Siliguri Medium",
                          "semibold": "Hind Siliguri SemiBold", "bold": "Hind Siliguri"})
    return ok


def fallback_family(name):
    global FAMILY
    if FAMILY == "Segoe UI":
        FAMILY = name


def family(weight="regular"):
    return _FAMILIES.get(weight, FAMILY)


def font(size=13, weight="regular"):
    key = (size, weight)
    if key not in _fonts:
        fam = family(weight)
        bold = "bold" if weight == "bold" or (weight in ("semibold", "medium") and fam == FAMILY == "Segoe UI") else "normal"
        _fonts[key] = ctk.CTkFont(family=fam, size=size, weight=bold)
    return _fonts[key]


def tkfont(size=11, weight="regular"):
    """Plain-Tk font tuple (for canvas text)."""
    fam = family(weight)
    return (fam, size, "bold" if weight == "bold" else "normal")


# --------------------------------------------------------------------------
# Window style: dark title bar, rounded corners, Windows 11 Mica glass
# --------------------------------------------------------------------------
class _MARGINS(ctypes.Structure):
    _fields_ = [("l", ctypes.c_int), ("r", ctypes.c_int), ("t", ctypes.c_int), ("b", ctypes.c_int)]


def _win_build():
    try:
        return sys.getwindowsversion().build
    except Exception:
        return 0


GLASS_OK = IS_WIN and _win_build() >= 22621

# On Mica, Windows adds the glass tint (about #1c1c1c) to every pixel the app draws. Darken surface colours
# and images by the same amount so they appear as designed.
GLASS_TINT = 0x1c
_TEXT_KEYS = {"text", "muted", "faint", "accent_text", "link", "glass", "bg"}


def adj(hex_color):
    if not GLASS_OK or not hex_color.startswith("#") or len(hex_color) != 7:
        return hex_color
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % (max(0, r - GLASS_TINT), max(0, g - GLASS_TINT), max(0, b - GLASS_TINT))


if GLASS_OK:
    for _k in list(C):
        if _k not in _TEXT_KEYS:
            C[_k] = adj(C[_k])


def image(path, size):
    """CTkImage, colour-corrected for the glass background."""
    from PIL import Image, ImageChops
    img = Image.open(path).convert("RGBA")
    if GLASS_OK:
        r, g, b, a = img.split()
        rgb = ImageChops.subtract(Image.merge("RGB", (r, g, b)), Image.new("RGB", img.size, (GLASS_TINT,) * 3))
        img = Image.merge("RGBA", (*rgb.split(), a))
    return ctk.CTkImage(img, size=size)


def style_window(win, glass=True):
    """Returns the background colour widgets should sit on ('#000000' shows the glass)."""
    if not IS_WIN:
        win.configure(fg_color=C["bg"]) if hasattr(win, "_fg_color") else None
        return C["bg"]
    try:
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        dwm = ctypes.windll.dwmapi

        def attr(a, v):
            val = ctypes.c_int(v)
            return dwm.DwmSetWindowAttribute(hwnd, a, ctypes.byref(val), 4)
        attr(20, 1)                       # dark title bar
        attr(33, 2)                       # rounded corners
        attr(35, 0x00161310)              # caption colour (BGR) = #101316
        attr(36, 0x00F9F5F3)              # caption text
        attr(34, 0x003C302B)              # border colour
        if glass and GLASS_OK:
            m = _MARGINS(-1, -1, -1, -1)
            dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(m))
            if attr(38, 2) == 0:          # DWMSBT_MAINWINDOW = Mica
                return C["glass"]
    except Exception:
        pass
    return C["bg"]


def set_icon(win, path):
    try:
        if IS_WIN and path.endswith(".ico"):
            win.after(250, lambda: win.iconbitmap(path))   # CTkToplevel resets the icon right after creation
    except Exception:
        pass


# --------------------------------------------------------------------------
# Components
# --------------------------------------------------------------------------
def card(master, **kw):
    opts = dict(fg_color=C["surface"], corner_radius=16, border_width=1, border_color=C["border"])
    opts.update(kw)
    return ctk.CTkFrame(master, **opts)


def label(master, text, size=13, weight="regular", color=None, wrap=0, **kw):
    return ctk.CTkLabel(master, text=text, font=font(size, weight), text_color=color or C["text"],
                        wraplength=wrap, justify="left", anchor="w", **kw)


def button(master, text, command=None, kind="primary", width=0, height=38, **kw):
    styles = {
        "primary": (C["accent"], C["accent_hover"], "#ffffff", 0, C["accent"]),
        "ghost": (C["surface2"], C["surface3"], C["text"], 1, C["border"]),
        "soft": (C["accent_soft"], "#33285a", C["accent_text"], 0, C["accent_soft"]),
        "danger": (C["red_soft"], "#4a1a1e", "#fca5a5", 0, C["red_soft"]),
        "rec": (C["red"], C["red_hover"], "#ffffff", 0, C["red"]),
        "link": ("transparent", C["surface2"], C["link"], 0, C["surface"]),
    }
    fg, hover, txt, bw, bc = styles[kind]
    return ctk.CTkButton(master, text=text, command=command, fg_color=fg, hover_color=hover, text_color=txt,
                         border_width=bw, border_color=bc, corner_radius=11, height=height,
                         width=width or 0, font=font(13, "semibold" if kind in ("primary", "rec") else "medium"), **kw)


def switch(master, variable, command=None):
    return ctk.CTkSwitch(master, text="", variable=variable, onvalue=True, offvalue=False, command=command,
                         progress_color=C["accent"], button_color="#ffffff", button_hover_color="#e9e3ff",
                         fg_color=C["surface3"], switch_width=44, switch_height=22, width=52)


def select(master, variable, values, width=280, command=None):
    return ctk.CTkOptionMenu(master, variable=variable, values=values, width=width, height=36, corner_radius=10,
                             fg_color=C["surface2"], button_color=C["surface3"], button_hover_color=C["border"],
                             text_color=C["text"], font=font(13), dropdown_font=font(13),
                             dropdown_fg_color=C["surface2"], dropdown_hover_color=C["accent_soft"],
                             dropdown_text_color=C["text"], dynamic_resizing=False, command=command)


def entry(master, variable, show="", width=320, placeholder=""):
    return ctk.CTkEntry(master, textvariable=variable, show=show, width=width, height=38, corner_radius=10,
                        fg_color=C["surface2"], border_color=C["border"], border_width=1, text_color=C["text"],
                        font=font(13), placeholder_text=placeholder or None, placeholder_text_color=C["faint"])


def chip(master, text, tone="neutral"):
    tones = {"neutral": (C["surface3"], C["muted"]), "accent": (C["accent_soft"], C["accent_text"]),
             "green": (C["green_soft"], "#86efac"), "red": (C["red_soft"], "#fca5a5"),
             "amber": (C["amber_soft"], "#fcd34d")}
    bg, fg = tones[tone]
    return ctk.CTkLabel(master, text=f"  {text}  ", font=font(11, "medium"), text_color=fg, fg_color=bg,
                        corner_radius=8, height=24)


def keycap(master, text):
    return ctk.CTkLabel(master, text=f"  {text}  ", font=font(12, "semibold"), text_color=C["text"],
                        fg_color=C["surface3"], corner_radius=8, height=28)


def setting_row(parent, title, desc=None, control=None, first=False):
    """A titled row inside a card: text on the left, control on the right. `control(frame)` builds it."""
    row = ctk.CTkFrame(parent, fg_color="transparent")
    row.pack(fill="x", padx=18, pady=(14 if first else 6, 6))
    if not first:
        ctk.CTkFrame(parent, height=1, fg_color=C["border"]).pack(fill="x", padx=18, before=row)
    left = ctk.CTkFrame(row, fg_color="transparent")
    left.pack(side="left", fill="x", expand=True)
    label(left, title, 14, "medium").pack(anchor="w")
    if desc:
        label(left, desc, 12, color=C["muted"], wrap=420).pack(anchor="w", pady=(1, 0))
    if control:
        w = control(row)
        w.pack(side="right", padx=(16, 0))
    return row


def section(parent, title=None, subtitle=None):
    if title:
        label(parent, title, 12, "semibold", color=C["faint"]).pack(anchor="w", padx=4, pady=(14, 6))
    c = card(parent)
    c.pack(fill="x", pady=(0, 4))
    if subtitle:
        label(c, subtitle, 12, color=C["muted"], wrap=560).pack(anchor="w", padx=18, pady=(14, 0))
    return c


def bind_link(widget, url_opener):
    widget.configure(cursor="hand2")
    widget.bind("<Button-1>", lambda e: url_opener())
