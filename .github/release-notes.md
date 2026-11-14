## Kotha 2.4.1: stability fix (please update)

Free, open-source voice typing and AI meeting notes for Bangla, English and the mix of both. Windows and macOS.

### Download

| System | File |
|---|---|
| **Windows 10/11** | **Kotha-Windows-Setup.exe** (recommended) |
| Windows, with offline Whisper | Kotha-Windows-Offline-Setup.exe |
| **Mac, Apple Silicon** (M1/M2/M3/M4) | **Kotha-Mac-AppleSilicon.dmg** |
| **Mac, Intel** | Kotha-Mac-Intel.dmg |

You need a free Gemini API key. Step-by-step guide: [README](https://github.com/procoder-manik/Kotha#get-your-free-gemini-api-key).

### Fixed
- **The app froze when the mouse was over the floating handle** (busy cursor, nothing worked, voice typing could not paste). The handle redrew itself in an endless loop on hover. It now redraws only when you move to another button. This affected Windows and Mac in 2.4.0.
- **Settings and Meeting Notes open instantly**: they are prepared quietly in the background (they used to take 2 to 4 seconds and froze the app meanwhile).
- **History opens fast** even with hundreds of entries.
- A self-test now moves the mouse over the handle on every build, on real Windows and Mac machines, so this cannot come back unnoticed.

### Improved
- Floating handle in the Kotha logo colours: green line, green microphone button, red record button.

Everything from 2.4.0 is included: meeting notes on Windows and Mac, the floating handle, the new design and the সাপোর্ট page.

Developed by [Manik Hossain](https://www.manikhossain.in/), part of [NextGen Agency](https://www.nextgenagency.co/).
