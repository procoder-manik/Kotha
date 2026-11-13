## Kotha 2.2.2: now on macOS too

Free, open-source voice typing for Bangla, English and the mix of both. Speak, and the text appears in any app.

### Download

| System | File |
|---|---|
| Windows 10/11 | **Kotha-Windows-Setup.exe** (recommended) |
| Windows 10/11, with offline Whisper | Kotha-Windows-Offline-Setup.exe |
| Mac, Apple Silicon (M1/M2/M3/M4) | **Kotha-Mac-AppleSilicon.dmg** |
| Mac, Intel | Kotha-Mac-Intel.dmg |

You need a free Gemini API key. The step-by-step guide is in the [README](https://github.com/procoder-manik/Kotha#setup-guide-get-a-free-gemini-api-key-2-minutes).

### What's new
- **macOS version**: menu-bar app, hold **Right Option**, talk, release. Setup and permissions guide in the README.
- Windows and Mac now share one codebase (`kotha.py`), with the OS-specific code in `plat_win.py` and `plat_mac.py`.
- Release files are named by system: `Kotha-Windows-*.exe` and `Kotha-Mac-*.dmg`.

### Mac notes
The Mac app is not notarized by Apple. First launch: right-click Kotha > Open. Allow Input Monitoring, Accessibility and Microphone when asked.

Developed by [Manik Hossain](https://www.manikhossain.in/), part of [NextGen Agency](https://www.nextgenagency.co/).
