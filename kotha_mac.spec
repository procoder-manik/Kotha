# PyInstaller spec for the macOS app bundle:  pyinstaller kotha_mac.spec
# Produces dist/Kotha.app (a menu-bar app: no Dock icon, asks for microphone access).
import re

with open("kotha.py", encoding="utf-8") as f:
    VERSION = re.search(r'^VERSION = "([^"]+)"', f.read(), re.M).group(1)

hidden = [
    "plat_mac", "pystray._darwin",
    "objc", "Foundation", "AppKit", "Quartz", "CoreFoundation", "ApplicationServices",
    "PyObjCTools", "ui_theme",
]

from PyInstaller.utils.hooks import collect_data_files

a = Analysis(
    ["kotha.py"],
    pathex=["."],
    datas=[("assets", "assets")] + collect_data_files("customtkinter"),
    hiddenimports=hidden,
    excludes=["unittest", "pydoc", "lib2to3", "setuptools", "numpy", "faster_whisper", "ctranslate2",
              "onnxruntime", "av", "tokenizers", "huggingface_hub", "plat_win", "uiautomation", "comtypes",
              "soundcard"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Kotha", console=False, argv_emulation=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Kotha")
app = BUNDLE(
    coll,
    name="Kotha.app",
    icon="assets/kotha.icns",
    bundle_identifier="in.manikhossain.kotha",
    version=VERSION,
    info_plist={
        "CFBundleName": "Kotha",
        "CFBundleDisplayName": "Kotha (কথা)",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "11.0",
        "LSUIElement": True,                      # menu-bar app, no Dock icon
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "Kotha listens to your voice only while you hold the shortcut, to turn it into text.",
        "NSAppleEventsUsageDescription": "Kotha uses this to paste the text into the app you are typing in.",
        "NSHumanReadableCopyright": "Developed by Manik Hossain (https://www.manikhossain.in/). MIT License.",
    },
)
