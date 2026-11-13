## Kotha 2.4.0: meeting notes on Mac too, a new floating handle and a cleaner design

Free, open-source voice typing and AI meeting notes for Bangla, English and the mix of both. Windows and macOS.

### Download

| System | File |
|---|---|
| **Windows 10/11** | **Kotha-Windows-Setup.exe** (recommended) |
| Windows, with offline Whisper | Kotha-Windows-Offline-Setup.exe |
| **Mac, Apple Silicon** (M1/M2/M3/M4) | **Kotha-Mac-AppleSilicon.dmg** |
| **Mac, Intel** | Kotha-Mac-Intel.dmg |

You need a free Gemini API key. Step-by-step guide: [README](https://github.com/procoder-manik/Kotha#get-your-free-gemini-api-key).

### What's new
- **AI meeting notes on Mac** (macOS 13+ for the other people's voices): same as Windows. Records your mic and the call audio, transcribes every minute, and at the end gives title, summary, key points, decisions and action items. Needs the **Screen Recording** permission, only to hear the sound your Mac plays. Kotha does not record your screen.
- **New floating handle:** a thin line on the left edge of the screen. Hover and it opens into round buttons: hands-free dictation, meeting notes, all notes, settings. Drag it up or down.
- **Cleaner, rounder design** on Windows and Mac, with Windows 11 glass, the Hind Siliguri font and a new **সাপোর্ট** page.
- **Mac:** meeting detection (call app open + microphone in use), menu-bar app, permission buttons in Settings.
- **Self-test:** every build is now opened and checked on real Windows and macOS machines before release.
- Renamed files: `Kotha-Windows-*.exe` and `Kotha-Mac-*.dmg`.

### Good to know
- **Mac first launch:** the app is not notarized by Apple. Right-click Kotha > Open. Then allow Input Monitoring, Accessibility, Microphone and Screen Recording, and reopen Kotha. Details in the [Mac guide](https://github.com/procoder-manik/Kotha#macos-guide).
- Tell everyone in the meeting before you record. Notes stay on your computer.
- The Mac meeting-notes feature was verified on a real Apple Silicon Mac in automated testing (system audio captured), but it is new: please report problems in Issues.

Developed by [Manik Hossain](https://www.manikhossain.in/), part of [NextGen Agency](https://www.nextgenagency.co/).
