<div align="center">

<img src="assets/kotha.png" width="96" alt="Kotha voice typing app logo">

# Kotha (কথা)

**Free, open-source voice typing for Windows. Speak Bangla, English, or a natural mix of both, and the text appears wherever your cursor is.**

কথা বলুন, লেখা হয়ে যাবে।

[![Download](https://img.shields.io/badge/Download-Kotha--Setup.exe-006A4E?style=for-the-badge&logo=windows)](../../releases/latest/download/Kotha-Setup.exe)
[![Offline](https://img.shields.io/badge/Offline%20edition-73%20MB-444?style=for-the-badge)](../../releases/latest/download/Kotha-Setup-Offline.exe)
[![License](https://img.shields.io/badge/License-MIT-F42A41?style=for-the-badge)](LICENSE)

<img src="docs/kotha-free-bangla-voice-typing-banner.png" alt="Kotha: free open-source Bangla and English voice typing app for Windows, by Manik Hossain" width="760">

</div>

## Why Kotha

- **Bangla first.** Built for people who think in Bangla and write in a mix of Bangla and English. 85+ languages are supported.
- **Works everywhere.** Notepad, Word, WhatsApp Web, Gmail, VS Code, any text box. Kotha types the text for you.
- **Fast.** Long speech is sent in pieces while you are still talking, the connection is opened the moment you press the shortcut, and silence is trimmed before upload.
- **Free.** Uses a free Gemini API key. No card, no subscription.
- **Open source.** MIT licensed. Read the code, change it, build it yourself.
- **Private by choice.** Your key stays on your PC. Need fully offline dictation? Use the Offline edition.

## Download

Get the latest installers from the **[Releases page](../../releases/latest)**.

| File | Size | Use it when |
|---|---|---|
| [`Kotha-Setup.exe`](../../releases/latest/download/Kotha-Setup.exe) | ~15 MB | Recommended. Uses Gemini with a free API key. |
| [`Kotha-Setup-Offline.exe`](../../releases/latest/download/Kotha-Setup-Offline.exe) | ~74 MB | You also want the offline Whisper engine (no internet needed, slower, weaker for Bangla). |

Windows 10 or 11. No admin rights needed.

> Windows SmartScreen may warn about an unknown publisher because the installer is not code-signed. Click **More info** and then **Run anyway**.

## Setup guide: get a free Gemini API key (2 minutes)

Kotha needs a Gemini API key to turn your voice into text. It is free and does not need a card.

**Step 1. Open Google AI Studio**
Go to **https://aistudio.google.com/apikey** and sign in with your Google account.

**Step 2. Create the key in a new project**
1. Click **Create API key**.
2. Choose **Create API key in new project**.
3. Copy the key that appears.

> **Important:** do not add billing or a card to this project. A key from a project with billing enabled is not free, and Kotha will show a "credits depleted" or "billing" error.

**Step 3. Paste it into Kotha**
1. Install and open Kotha. It lives in the system tray (bottom-right of the taskbar).
2. Right-click the tray icon and open **Settings** (or double-click the icon).
3. In the **শুরু** tab, paste the key into **Gemini API key**.
4. Click **Key চেক করো**. You should see a green **✓ key ঠিক আছে**.
5. Click **Save**.

<div align="center">
<img src="docs/kotha-settings-gemini-api-key-setup.png" alt="Kotha settings window: paste your free Gemini API key, click Key check, then Save" width="520">
</div>

**Step 4. Start talking**
1. Click into any text box.
2. Hold **Ctrl + Win**, speak, and release. The text is typed for you.
3. Double-tap the shortcut for hands-free mode, press it again to finish.
4. **Esc** cancels.

You can change the shortcut in the **শর্টকাট** tab (presets, or record your own).

### Optional: a free Groq key as backup
Gemini's free tier has a daily limit. Add a free key from **https://console.groq.com/keys** in the **Groq key (ব্যাকআপ)** field. When the Gemini limit runs out, Kotha switches to Groq automatically.

### Troubleshooting

| Problem | Fix |
|---|---|
| "credits depleted" or "billing" error | Your key belongs to a project with billing. Create a new key with **Create API key in new project** and do not add a card. |
| "ফ্রি লিমিট শেষ" (free limit reached) | Today's free quota is used up. Add a free Groq key as backup, or try again tomorrow. |
| Nothing is typed | Click inside a text box first. If none is found, Kotha shows a popup and copies the text to your clipboard, so you can press Ctrl+V. |
| Shortcut does nothing | Another app (for example Wispr Flow) may use the same shortcut. Pick a different one in the **শর্টকাট** tab. |
| Wrong microphone | Choose the microphone in the **সাধারণ** tab. |
| Proxy or network error | Check your Windows proxy settings and internet connection. |

## বাংলায় সংক্ষেপে

1. [Releases](../../releases/latest) থেকে `Kotha-Setup.exe` নামিয়ে ইনস্টল করুন।
2. https://aistudio.google.com/apikey এ Google অ্যাকাউন্ট দিয়ে ঢুকুন।
3. **Create API key** চাপুন, তারপর **Create API key in new project** বেছে নিন। কোনো billing বা কার্ড যোগ করবেন না, তাহলেই ফ্রি থাকবে।
4. key কপি করে Kotha এর Settings এ **Gemini API key** ঘরে বসান, **Key চেক করো** চাপুন, তারপর **Save**।
5. যেকোনো টেক্সট বক্সে ক্লিক করুন, **Ctrl + Win** চেপে ধরে কথা বলুন, ছেড়ে দিলেই লেখা বসে যাবে।

## Engines

| Engine | Key | Notes |
|---|---|---|
| Gemini Live (default) | Free Gemini key | Real-time text while you speak |
| Gemini Transcribe | Free Gemini key | Text appears after you finish, high accuracy for Bangla and mixed speech |
| Gemini Flash | Free Gemini key | Lets you choose how mixed speech is written |
| Groq Whisper | Free Groq key | Very fast, weaker on Bangla |
| OpenAI | Paid key | |
| Offline Whisper | None | Offline edition only |

Free-tier audio may be used by Google to improve its products. Use the Offline edition for fully private dictation.

## Build from source

Needs Windows, Python 3.12, and [NSIS](https://nsis.sourceforge.io).

```bat
build.bat            :: lite installer  -> output\Kotha-Setup.exe
build.bat offline    :: offline installer -> output\Kotha-Setup-Offline.exe
```

Run from source:

```bat
pip install -r requirements.txt
python kotha.py
```

A GitHub Actions workflow in `.github/workflows/build.yml` builds both installers. Push a tag like `v2.2.1` or run it manually.

Settings, history and logs live in `%APPDATA%\Kotha`.

## Contributing

Issues and pull requests are welcome. If you find a bug, open an issue with the steps to reproduce and the log from `%APPDATA%\Kotha\kotha.log`. Never share your API key.

## Share Kotha

Ready-made banners for Facebook, Instagram, X and LinkedIn are in [`docs/banners`](docs/banners). Feel free to use them when you share the project.

<div align="center">
<img src="docs/banners/kotha-free-bangla-voice-typing-linkedin-1200x627.png" alt="Kotha voice typing app banner: speak Bangla or English and text appears in any Windows app" width="620">
</div>

## Credits

<img src="docs/kotha-about-developer-manik-hossain.png" alt="Kotha about page: developed by Manik Hossain, part of NextGen Agency" width="420">

Developed by **[Manik Hossain](https://www.manikhossain.in/)**, part of **[NextGen Agency](https://www.nextgenagency.co/)**.

## License

[MIT](LICENSE)
