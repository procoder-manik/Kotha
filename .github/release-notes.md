## Kotha 2.5.0: meeting notes in seconds

Free, open-source voice typing and AI meeting notes for Bangla, English and the mix of both. Windows and macOS.

### Download

| System | File |
|---|---|
| **Windows 10/11** | **Kotha-Windows-Setup.exe** (recommended) |
| Windows, with offline Whisper | Kotha-Windows-Offline-Setup.exe |
| **Mac, Apple Silicon** (M1/M2/M3/M4) | **Kotha-Mac-AppleSilicon.dmg** |
| **Mac, Intel** | Kotha-Mac-Intel.dmg |

You need a free Gemini API key. Step-by-step guide: [README](https://github.com/procoder-manik/Kotha#get-your-free-gemini-api-key).

### Meeting notes: much faster
- **Notes a few seconds after the meeting ends.** Tested with a 32-minute Bangla-English meeting played in real time: the full notes (summary, key points, decisions, action items) were ready **6 seconds** after stop. Version 2.4.1 needed about 8 minutes even for a 5-minute meeting.
- The meeting is transcribed **every 30 seconds in the background** while it runs, so almost nothing is left to do at the end.
- The summary **appears on screen while it is being written**.
- If Google's servers are busy, Kotha switches to another model without losing any part of the meeting.

### Meeting notes: captures more
- **Windows: the other people are recorded on every output device.** Before, only the default speaker was recorded. If the call played through a headset, a monitor's speaker or the "communications" device, the other side was missing.
- **Quiet microphones are no longer treated as silence**: Kotha measures each microphone's own noise level and raises quiet speech before transcribing.

### Language
- **Speak Bangla (or Bangla-English mix) and the notes are in Bangla. Speak English and they are in English.** Kotha checks the language that was actually spoken. You can still fix the language in Settings.

Tip: add names and words that are often misheard (for example bKash, SSLCommerz, your product names) in Settings > ভাষা ও লেখা > বিশেষ শব্দ ও নাম, and the transcript will spell them right.

Everything from 2.4.1 is included.

Developed by [Manik Hossain](https://www.manikhossain.in/), part of [NextGen Agency](https://www.nextgenagency.co/).
