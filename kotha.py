"""
Kotha (কথা) - fast voice typing for Windows and macOS.
Bangla, English, mixed Banglish, Hindi and 80+ more languages.

  Hold the shortcut (Windows: Ctrl + Win, Mac: Right Option) -> talk, release -> text is typed at your cursor
  Double-tap the shortcut                 -> hands-free, press once more to finish
  Esc while recording                     -> cancel

Meeting notes (Windows): records your mic + the call audio, writes the transcript and a summary.
  Win + Alt + M or the floating button on the left edge          -> start / stop meeting notes

Open source. Engines: Gemini Transcribe (default, free API key), Gemini Flash,
Groq, OpenAI, and an optional fully offline Whisper engine (Kotha Offline build).
"""

import os
import sys
import io
import json
import math
import time
import wave
import array
import base64
import ctypes
import queue
import threading
import webbrowser
import importlib.util
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime

import sounddevice as sd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import socket
import websocket   # websocket-client
try:   # some Windows setups reject TCP_KEEPIDLE/KEEPCNT (WinError 10042); keep only universal options
    from websocket import _socket as _wss
    _wss.DEFAULT_SOCKET_OPTION[:] = [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),
                                     (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
except Exception:
    pass

try:   # use the Windows certificate store (fixes SSL errors behind antivirus / proxies)
    import truststore
    truststore.inject_into_ssl()
except Exception:
    pass
import pyperclip
import tkinter as tk
from tkinter import ttk, messagebox
import customtkinter as ctk
import ui_theme as ui
import pystray
from PIL import Image

if sys.platform == "win32":
    import plat_win as plat
elif sys.platform == "darwin":
    import plat_mac as plat
else:
    raise SystemExit("Kotha supports Windows and macOS only.")

APP = "Kotha"
APP_BN = "কথা"
VERSION = "2.3.0"
CFG_VERSION = 3
DATA_DIR = os.path.join(plat.USER_DATA_BASE, APP)
MODELS_DIR = os.path.join(DATA_DIR, "models")
os.makedirs(MODELS_DIR, exist_ok=True)
CFG_PATH = os.path.join(DATA_DIR, "config.json")
HIST_PATH = os.path.join(DATA_DIR, "history.json")
LOG_PATH = os.path.join(DATA_DIR, "kotha.log")
FONT = plat.FONT   # Windows: Nirmala UI (renders Bangla and Devanagari well); Mac: system font with Bangla fallback
DEV_NAME = "Manik Hossain"
DEV_URL = "https://www.manikhossain.in/"
DEV_SITE = "manikhossain.in"
AGENCY_NAME = "NextGen Agency"
AGENCY_URL = "https://www.nextgenagency.co/"
WHATSAPP_TEXT = "+880 1720-520782"
WHATSAPP_URL = "https://wa.me/8801720520782"
SUPPORT_NUMBER = "01650043801"                  # bKash / Rocket / Nagad (personal)
SUPPORT_NUMBER_TEXT = "+880 1650-043801"
SUPPORT_WALLETS = (("bKash", "#e2136e"), ("Rocket", "#8c3494"), ("Nagad", "#f6921e"))

LOCAL_AVAILABLE = importlib.util.find_spec("faster_whisper") is not None
PASTE_KEYS = "Cmd+V" if plat.IS_MAC else "Ctrl+V"
GEMINI_KEY_URL = "https://aistudio.google.com/apikey"
GEMINI_BASE = "https://generativelanguage.googleapis.com"


def resource(rel):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel)


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


# --------------------------------------------------------------------------
# Languages
# key, label, Gemini Transcribe codes, Whisper code, name used in prompts
# --------------------------------------------------------------------------
LANGS = [
    ("auto", "Auto (যেকোনো ভাষা, মিক্সও চলবে)", [], None, None),
    ("bn_en", "বাংলা + English মিক্স", ["bn-BD", "en-US"], "bn", "Bangla and English, code-mixed"),
    ("bn", "বাংলা", ["bn-BD"], "bn", "Bangla"),
    ("en", "English", ["en-US"], "en", "English"),
    ("hi", "हिन्दी Hindi", ["hi-IN"], "hi", "Hindi"),
    ("hi_en", "Hindi + English (Hinglish)", ["hi-IN", "en-IN"], "hi", "Hindi and English, code-mixed"),
    ("ur", "اردو Urdu", [], "ur", "Urdu"),
    ("ne", "नेपाली Nepali", ["ne-NP"], "ne", "Nepali"),
    ("ar", "العربية Arabic", ["ar-EG"], "ar", "Arabic"),
    ("id", "Bahasa Indonesia", ["id-ID"], "id", "Indonesian"),
    ("ms", "Bahasa Melayu", ["ms-MY"], "ms", "Malay"),
    ("es", "Español", ["es-419"], "es", "Spanish"),
    ("fr", "Français", ["fr-FR"], "fr", "French"),
    ("de", "Deutsch", ["de-DE"], "de", "German"),
    ("pt", "Português", ["pt-BR"], "pt", "Portuguese"),
    ("tr", "Türkçe", ["tr-TR"], "tr", "Turkish"),
]
LANG = {l[0]: l for l in LANGS}


def lang_info(cfg):
    return LANG.get(cfg.get("language", "auto"), LANGS[0])


# --------------------------------------------------------------------------
# Hotkeys
# --------------------------------------------------------------------------
TOKEN_VKS, MOD_TOKENS, SOLO_OK, TOKEN_LABEL, ORDER = plat.TOKEN_VKS, plat.MOD_TOKENS, plat.SOLO_OK, plat.TOKEN_LABEL, plat.ORDER
HOTKEY_PRESETS, DEFAULT_HOTKEY = plat.HOTKEY_PRESETS, plat.DEFAULT_HOTKEY
DEFAULT_NOTE_HOTKEY, NOTE_HOTKEY_PRESETS = plat.DEFAULT_NOTE_HOTKEY, plat.NOTE_HOTKEY_PRESETS
_VK_SIDE, _VK_GENERIC = plat.VK_SIDE, plat.VK_GENERIC


def hotkey_label(tokens):
    return " + ".join(TOKEN_LABEL.get(t, t.upper()) for t in tokens)


def sort_tokens(tokens):
    return sorted(set(tokens), key=lambda t: (ORDER.index(t) if t in ORDER else 99, t))


def vks_to_tokens(vks):
    vks = set(vks)
    if len(vks) == 1:
        vk = next(iter(vks))
        if vk in _VK_SIDE:
            return [_VK_SIDE[vk]]
    tokens = set()
    for vk in vks:
        if vk in _VK_GENERIC:
            tokens.add(_VK_GENERIC[vk])
            continue
        for t, s in TOKEN_VKS.items():
            if t not in MOD_TOKENS and vk in s:
                tokens.add(t)
                break
        else:
            return None
    return sort_tokens(tokens)


def hotkey_valid(tokens):
    if not tokens:
        return False
    if len(tokens) == 1:
        return tokens[0] in SOLO_OK
    return any(t in MOD_TOKENS for t in tokens) or all(t.startswith("f") and t[1:].isdigit() for t in tokens)


# --------------------------------------------------------------------------
# Config & history
# --------------------------------------------------------------------------
DEFAULTS = {
    "cfg_version": CFG_VERSION,
    "provider": "gemini_live",          # gemini_live | gemini_tx | gemini | groq | openai | local
    "gemini_live_model": "gemini-3.5-transcribe-live",
    "gemini_api_key": "",
    "gemini_tx_model": "gemini-3.5-transcribe",
    "gemini_model": "gemini-flash-latest",
    "groq_api_key": "",
    "groq_model": "whisper-large-v3-turbo",
    "openai_api_key": "",
    "openai_model": "gpt-4o-transcribe",
    "local_model": "large-v3-turbo",
    "local_model_path": "",
    "local_device": "auto",
    "language": "auto",
    "script_style": "mixed",            # mixed | native
    "clean_fillers": True,              # SMART mode
    "custom_words": "",
    "hotkey": list(DEFAULT_HOTKEY),
    "mic": "",
    "smart_paste": True,
    "restore_clipboard": True,
    "sounds": True,
    "start_with_windows": plat.DEFAULT_START_AT_LOGIN,     # key name kept for old config files
    "pipeline": True,
    "hold_threshold_ms": 300,
    "double_tap_ms": 400,
    "max_seconds": 590,
    # meeting notes
    "notes_enabled": plat.SUPPORTS_MEETINGS,
    "float_button": True,
    "meeting_detect": "ask",            # ask | auto | off
    "meeting_autostop": True,
    "note_hotkey": list(plat.DEFAULT_NOTE_HOTKEY),
    "notes_language": "auto",           # auto | bn | en
    "keep_meeting_audio": False,
    "float_y": 0.42,
}


class Config(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        saved = {}
        try:
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
        except Exception:
            pass
        if saved and saved.get("cfg_version", 1) < CFG_VERSION:
            # v1.x: switch to the new fast engine, keep keys and personal settings
            if saved.get("cfg_version", 1) < 2 and saved.get("provider") in ("gemini", "local", None):
                saved["provider"] = "gemini_tx"
            if saved.get("language") not in LANG:
                saved["language"] = "auto"
            if saved.get("script_style") == "bangla":
                saved["script_style"] = "native"
            if saved.get("cfg_version", 1) < 2:
                saved.pop("groq_model", None)
            if saved.get("provider") == "gemini_tx":
                saved["provider"] = "gemini_live"     # v3: real-time engine
            saved["cfg_version"] = CFG_VERSION
        self.update(saved)
        if self["provider"] == "local" and not LOCAL_AVAILABLE:
            self["provider"] = "gemini_live"
        if not hotkey_valid(self.get("note_hotkey") or []):
            self["note_hotkey"] = list(DEFAULT_NOTE_HOTKEY)
        if not hotkey_valid(self["hotkey"]):
            self["hotkey"] = list(DEFAULT_HOTKEY)

    def save(self):
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(dict(self), f, ensure_ascii=False, indent=2)

    def ready(self):
        p = self["provider"]
        if p == "local":
            return LOCAL_AVAILABLE
        key = "gemini_api_key" if p.startswith("gemini") else f"{p}_api_key"
        return bool(self.get(key, "").strip())


class History:
    def __init__(self):
        self.items = []
        try:
            with open(HIST_PATH, "r", encoding="utf-8") as f:
                self.items = json.load(f)
        except Exception:
            pass

    def add(self, text):
        self.items.insert(0, {"t": datetime.now().strftime("%Y-%m-%d %H:%M"), "text": text})
        self.items = self.items[:300]
        try:
            with open(HIST_PATH, "w", encoding="utf-8") as f:
                json.dump(self.items, f, ensure_ascii=False, indent=1)
        except Exception:
            pass


# --------------------------------------------------------------------------
# OS layer (see plat_win.py / plat_mac.py)
# --------------------------------------------------------------------------
plat.set_logger(log)
key_is_down, send_mask_key, wait_keys_released = plat.key_is_down, plat.send_mask_key, plat.wait_keys_released
press_paste, make_no_activate, work_area = plat.press_paste, plat.make_no_activate, plat.work_area
focused_is_editable, HotkeyHook = plat.focused_is_editable, plat.HotkeyHook


# --------------------------------------------------------------------------
# Audio (no numpy needed)
# --------------------------------------------------------------------------
SR = 16000
BLOCK = 800            # 50 ms


def block_rms(b):
    a = array.array("h")
    a.frombytes(b)
    sub = a[::4]
    if not sub:
        return 0.0
    return math.sqrt(sum(x * x for x in sub) / len(sub)) / 32768.0


class Recorder:
    def __init__(self):
        self.stream = None
        self.blocks = []
        self.rms = []
        self.level = 0.0

    @staticmethod
    def _device_index(name):
        if not name:
            return None
        try:
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and d["name"] == name:
                    return i
        except Exception:
            pass
        return None

    def start(self, mic_name=""):
        self.blocks, self.rms, self.level = [], [], 0.0
        self.stream = sd.RawInputStream(samplerate=SR, channels=1, dtype="int16", blocksize=BLOCK,
                                        device=self._device_index(mic_name), callback=self._cb,
                                        latency="low")
        self.stream.start()

    def _cb(self, indata, frames, t, status):
        b = bytes(indata)
        r = block_rms(b)
        self.blocks.append(b)
        self.rms.append(r)
        self.level = min(1.0, r * 9)

    def stop(self):
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self.level = 0.0


def list_mics():
    names = []
    try:
        api = sd.default.hostapi
        for d in sd.query_devices():
            if d["max_input_channels"] > 0 and d["hostapi"] == api and d["name"] not in names:
                names.append(d["name"])
    except Exception:
        pass
    return names


def speech_threshold(rms):
    if not rms:
        return 0.01
    srt = sorted(rms)
    floor = srt[len(srt) // 20]
    return min(max(0.006, floor * 2.5), 0.02)


def to_wav(raw):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(raw)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Transcription engines
# --------------------------------------------------------------------------
SESSION = requests.Session()
# Retry broken/stale keep-alive connections automatically (the cause of random "HTTPSConnectionPool" errors)
_retry = Retry(total=3, connect=3, read=1, status=0, other=2, backoff_factor=0.15, allowed_methods=None,
               raise_on_status=False)
SESSION.mount("https://", HTTPAdapter(pool_connections=4, pool_maxsize=8, max_retries=_retry))


def net_error_text(e):
    t = repr(e)
    low = t.lower()
    if "ssl" in low or "certificate" in low:
        return "SSL সমস্যা: antivirus বা VPN এর HTTPS scanning বন্ধ করে দেখো"
    if "proxy" in low:
        return f"Proxy সমস্যা: {plat.PROXY_HINT}"
    if "getaddrinfo" in low or "nameresolution" in low or "name resolution" in low:
        return "DNS সমস্যা: ইন্টারনেট চালু আছে কিনা দেখো, বা DNS 8.8.8.8 দিয়ে দেখো"
    if "timed out" in low or "timeout" in low:
        return "Google এ কানেক্ট হতে বেশি সময় লাগছে, ইন্টারনেট ধীর"
    if "refused" in low or "reset" in low or "aborted" in low or "remotedisconnected" in low:
        return "কানেকশন কেটে গেছে, আবার চেষ্টা করো"
    return "ইন্টারনেট সমস্যা: " + t[-120:]

PROVIDER_HOST = {"gemini_live": GEMINI_BASE, "gemini_tx": GEMINI_BASE, "gemini": GEMINI_BASE,
                 "groq": "https://api.groq.com", "openai": "https://api.openai.com"}


def prewarm(provider):
    """Open the TLS connection while the user is still talking."""
    host = PROVIDER_HOST.get(provider)
    if not host:
        return

    def run():
        try:
            SESSION.head(host, timeout=4)
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()


class EngineError(Exception):
    pass


class EmptyResult(Exception):
    pass


def api_error(r):
    try:
        j = r.json()
        e = j.get("error")
        msg = e.get("message") if isinstance(e, dict) else e
        return f"{r.status_code}: {msg or r.text[:160]}"
    except Exception:
        return f"{r.status_code}: {r.text[:160]}"


def vocab_list(cfg):
    return [w.strip() for w in cfg.get("custom_words", "").replace("،", ",").split(",") if w.strip()][:100]


def build_instructions(cfg):
    _, _, _, _, lname = lang_info(cfg)
    lines = [
        "You are a precise speech-to-text engine.",
        "Transcribe exactly what is spoken. Never translate, summarize, answer questions, or add anything.",
    ]
    if lname:
        lines.append(f"The speech is in {lname}.")
    else:
        lines.append("The speech can be in any language, often Bangla, English, or a natural mix of both.")
    if cfg["script_style"] == "native":
        lines.append("Write the whole transcript in the native script of the main language; "
                     "transliterate borrowed English words into that script (e.g. Bangla: মিটিং, ক্লায়েন্ট).")
    else:
        lines.append("When languages are mixed, write each word in its own script: Bangla words in Bangla script, "
                     "Hindi words in Devanagari, English words in English letters. Example: "
                     "'আমি আজকে meeting এ একটু late হবো, তুমি client কে inform করে দিও।'")
    if cfg["clean_fillers"]:
        lines.append("Drop filler sounds (um, uh, আ, আহ, এহ) and accidental stutters, apply spoken self-corrections, "
                     "but keep every word the speaker meant.")
    lines.append("Use natural punctuation for the language ('।' ends Bangla and Hindi sentences).")
    v = vocab_list(cfg)
    if v:
        lines.append("These names and terms may appear; spell them exactly like this: " + ", ".join(v))
    lines.append("Output ONLY the transcript. If there is no speech, output nothing.")
    return "\n".join(lines)


def whisper_hint(cfg):
    key = cfg.get("language", "auto")
    hint = ""
    if key in ("bn", "bn_en", "auto"):
        hint = ("আমি আজকে অফিসে যাবো, তারপর মিটিংটা অ্যাটেন্ড করবো।" if cfg["script_style"] == "native"
                else "আমি আজকে office এ যাবো, তারপর meeting টা attend করবো।")
    elif key in ("hi", "hi_en"):
        hint = "मैं आज office जाऊँगा, फिर meeting attend करूँगा।"
    v = vocab_list(cfg)
    return (hint + " " + ", ".join(v)).strip()


def _gemini_text(r):
    cands = r.json().get("candidates") or [{}]
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


class BusyError(Exception):
    """Google's servers are temporarily overloaded (503). Not a key problem."""


class QuotaError(Exception):
    def __init__(self, msg, wait):
        super().__init__(msg)
        self.wait = wait


# Each model has its own free quota, so trying several multiplies the free limit (gemini-2.5-* are retired).
GEMINI_FALLBACKS = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.5-flash",
                    "gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
_model_blocked = {}           # model -> monotonic time until which we skip it


def _gemini_check(r, model):
    """Turn a Gemini HTTP error into the right exception."""
    if r.status_code == 200:
        return
    msg = api_error(r)
    low = msg.lower()
    log(f"{model} HTTP {r.status_code}: {msg[:300]}")
    # Only a real prepay/credit problem is a billing error. Google's normal free-tier quota message also
    # says "check your plan and billing details", so that alone must NOT be treated as billing.
    if "prepayment" in low or "credits are depleted" in low or "billing account" in low and "disabled" in low:
        raise EngineError("key এর প্রজেক্টে billing চালু, তাই ফ্রি না। Settings এ 'key কাজ করছে না?' দেখো")
    if r.status_code in (401, 403) or "api key not valid" in low or "api_key_invalid" in low:
        raise EngineError("API key ঠিক নেই, আবার কপি করে বসাও")
    if r.status_code == 429:
        wait = 3600
        try:
            for d in r.json().get("error", {}).get("details", []):
                if d.get("retryDelay"):
                    wait = float(str(d["retryDelay"]).rstrip("s"))
            if "limit: 0" in low or "per_day" in low or "perday" in low:
                wait = max(wait, 3600)
        except Exception:
            pass
        raise QuotaError(msg, wait)
    if r.status_code == 404 or "not found" in low or "not supported" in low:
        raise QuotaError(msg, 6 * 3600)
    if r.status_code in (500, 502, 503, 504) or "high demand" in low or "overloaded" in low:
        raise BusyError(msg)
    raise EmptyResult(msg)


def _gemini_tx_call(wav_b64, cfg, model):
    atc = {"mode": "SMART" if cfg["clean_fillers"] else "VERBATIM"}
    codes = lang_info(cfg)[2]
    if codes:
        atc["languageCodes"] = codes
    v = vocab_list(cfg)
    if v:
        atc["customVocabulary"] = v
    url = f"{GEMINI_BASE}/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": cfg["gemini_api_key"].strip()}

    def call(a):
        body = {"contents": [{"role": "user", "parts": [{"inlineData": {"mimeType": "audio/wav", "data": wav_b64}}]}],
                "generationConfig": {"audioTranscriptionConfig": a}}
        return SESSION.post(url, json=body, headers=headers, timeout=60)

    r = call(atc)
    if r.status_code == 400 and len(atc) > 1:
        r = call({"mode": atc["mode"]})
    _gemini_check(r, model)
    text = _gemini_text(r)
    if not text:
        raise EmptyResult("empty")
    return text


def _gemini_prompt_call(wav_b64, cfg, model):
    url = f"{GEMINI_BASE}/v1beta/models/{model}:generateContent"
    headers = {"x-goog-api-key": cfg["gemini_api_key"].strip()}
    body = {
        "system_instruction": {"parts": [{"text": build_instructions(cfg)}]},
        "contents": [{"role": "user", "parts": [
            {"inline_data": {"mime_type": "audio/wav", "data": wav_b64}},
            {"text": "Transcribe this audio."}]}],
    }
    if model.startswith("gemini-2.5-flash"):
        body["generationConfig"] = {"thinkingConfig": {"thinkingBudget": 0}}
    elif "flash" in model:
        body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": "low"}}
    r = SESSION.post(url, json=body, headers=headers, timeout=90)
    if r.status_code == 400 and "generationConfig" in body:
        body.pop("generationConfig")
        r = SESSION.post(url, json=body, headers=headers, timeout=90)
    _gemini_check(r, model)
    return _gemini_text(r)


def transcribe_gemini_chain(wav, cfg, start_with_tx=True):
    """Try several Gemini models. Each model has its own free quota, so this multiplies the free limit."""
    b64 = base64.b64encode(wav).decode()
    chain = []
    if start_with_tx:
        chain.append(("tx", cfg["gemini_tx_model"].strip() or "gemini-3.5-transcribe"))
    for m in [cfg["gemini_model"].strip() or "gemini-flash-latest"] + GEMINI_FALLBACKS:
        if ("prompt", m) not in chain:
            chain.append(("prompt", m))
    now = time.monotonic()
    last_quota = None
    busy = False
    for kind, model in chain:
        if _model_blocked.get(model, 0) > now:
            continue
        try:
            if kind == "tx":
                return _gemini_tx_call(b64, cfg, model)
            return _gemini_prompt_call(b64, cfg, model)
        except QuotaError as e:
            log(f"{model}: quota/unavailable, trying next ({str(e)[:160]})")
            _model_blocked[model] = now + min(e.wait, 6 * 3600)
            last_quota = e
        except BusyError as e:
            log(f"{model}: busy, trying next ({str(e)[:120]})")
            _model_blocked[model] = now + 60
            busy = True
        except requests.exceptions.Timeout:
            log(f"{model}: timeout, trying next")
            _model_blocked[model] = now + 60
            busy = True
        except EmptyResult as e:
            log(f"{model}: no text ({str(e)[:160]}), trying next")
    if busy and last_quota is None:
        raise EngineError("Google এর সার্ভার এখন ব্যস্ত, কয়েক সেকেন্ড পরে আবার বলো")
    if last_quota is not None:
        raise QuotaError("all gemini models exhausted", 60)
    raise EngineError("Gemini কোনো লেখা দেয়নি, আবার চেষ্টা করো")


def transcribe_openai_style(wav, cfg, url, key, model):
    data = {"model": model, "response_format": "json", "temperature": "0", "prompt": whisper_hint(cfg)}
    wcode = lang_info(cfg)[3]
    if wcode:
        data["language"] = wcode
    r = SESSION.post(url, data=data, timeout=90, headers={"Authorization": f"Bearer {key}"},
                     files={"file": ("audio.wav", wav, "audio/wav")})
    if r.status_code in (401, 403):
        raise EngineError("API key ঠিক নেই: " + api_error(r))
    if r.status_code == 429:
        raise EngineError("লিমিট শেষ, একটু পরে চেষ্টা করো")
    if r.status_code != 200:
        raise EngineError(api_error(r))
    return r.json().get("text", "")


class LocalEngine:
    """Offline Whisper via faster-whisper (Kotha Offline build only)."""

    def __init__(self):
        self.model, self.key, self.device = None, None, "cpu"
        self.lock = threading.Lock()

    @staticmethod
    def _target(cfg):
        return (cfg.get("local_model_path", "").strip() or cfg.get("local_model") or "large-v3-turbo",
                cfg.get("local_device", "auto"))

    def ready(self, cfg):
        return self.model is not None and self.key == self._target(cfg)

    def _make(self, name, device):
        from faster_whisper import WhisperModel
        return WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8",
                            cpu_threads=max(1, os.cpu_count() or 4), download_root=MODELS_DIR)

    def load(self, cfg, force_cpu=False):
        with self.lock:
            key = self._target(cfg)
            if self.model is not None and self.key == key and not force_cpu:
                return self.model
            device = "cpu" if force_cpu else key[1]
            if device == "auto":
                try:
                    import ctranslate2
                    device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
                except Exception:
                    device = "cpu"
            self.model = None
            try:
                self.model = self._make(key[0], device)
            except Exception as e:
                if device != "cuda":
                    raise
                log(f"CUDA load failed, using CPU: {e}")
                device = "cpu"
                self.model = self._make(key[0], device)
            self.device, self.key = device, key
            return self.model

    def transcribe(self, raw, cfg):
        import numpy as np
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        language = lang_info(cfg)[3]
        for attempt in range(2):
            model = self.load(cfg, force_cpu=(attempt == 1))
            try:
                segs, _ = model.transcribe(audio, language=language, task="transcribe",
                                           beam_size=5 if self.device == "cuda" else 1,
                                           vad_filter=True, without_timestamps=True,
                                           condition_on_previous_text=False,
                                           initial_prompt=whisper_hint(cfg) or None)
                return " ".join(s.text.strip() for s in segs)
            except Exception as e:
                if attempt == 0 and self.device == "cuda":
                    log(f"CUDA inference failed, retrying on CPU: {e}")
                    continue
                raise


LOCAL = LocalEngine()


def clean_text(text):
    text = (text or "").strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'`":
        text = text[1:-1].strip()
    return text


def _groq(wav, cfg):
    return transcribe_openai_style(wav, cfg, "https://api.groq.com/openai/v1/audio/transcriptions",
                                   cfg["groq_api_key"].strip(), cfg["groq_model"].strip() or "whisper-large-v3-turbo")


def transcribe_raw(raw, cfg):
    p = cfg["provider"]
    if p == "local":
        return clean_text(LOCAL.transcribe(raw, cfg))
    wav = to_wav(raw)
    if p in ("gemini_live", "gemini_tx", "gemini"):
        try:
            return clean_text(transcribe_gemini_chain(wav, cfg, start_with_tx=(p != "gemini")))
        except (QuotaError, EngineError) as e:
            if isinstance(e, EngineError) and "ব্যস্ত" not in str(e):
                raise
            if cfg.get("groq_api_key", "").strip():
                log("Gemini unavailable (quota or busy), using Groq backup")
                return clean_text(_groq(wav, cfg))
            if isinstance(e, EngineError):
                raise
            raise EngineError("আজকের Gemini ফ্রি লিমিট শেষ। Settings এ ফ্রি Groq key দিলে ব্যাকআপ হিসেবে চলবে")
    if p == "groq":
        return clean_text(_groq(wav, cfg))
    return clean_text(transcribe_openai_style(wav, cfg, "https://api.openai.com/v1/audio/transcriptions",
                                              cfg["openai_api_key"].strip(),
                                              cfg["openai_model"].strip() or "gpt-4o-transcribe"))


def check_gemini_key(key):
    """Real test: one tiny generateContent call (models.list succeeds even when billing blocks generation)."""
    cfg = {"gemini_api_key": key}
    busy = False
    for model in ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.8-flash"]:
        try:
            r = SESSION.post(f"{GEMINI_BASE}/v1beta/models/{model}:generateContent",
                             headers={"x-goog-api-key": key.strip()}, timeout=15,
                             json={"contents": [{"role": "user", "parts": [{"text": "Reply with OK"}]}]})
            _gemini_check(r, model)
            return True, "OK"
        except QuotaError:
            continue
        except BusyError:
            busy = True
            continue
        except EngineError as e:
            return False, str(e)
        except EmptyResult as e:
            return False, str(e)[:120]
        except requests.exceptions.RequestException as e:
            log(f"key check network error: {e!r}")
            return False, net_error_text(e)
        except Exception as e:
            return False, str(e)[:100]
    if busy:
        return True, "key ঠিক আছে, তবে Google এর সার্ভার এখন ব্যস্ত"
    return False, "আজকের ফ্রি লিমিট শেষ, কাল আবার চলবে"



# --------------------------------------------------------------------------
# Real-time engine: Gemini 3.5 Transcribe Live over WebSocket
# Text appears while you talk; when you release, only the last words need finalizing.
# --------------------------------------------------------------------------
LIVE_URL = ("wss://generativelanguage.googleapis.com/ws/"
            "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={}")


class LiveSession:
    def __init__(self, cfg, recorder, on_text):
        self.cfg, self.rec, self.on_text = cfg, recorder, on_text
        self.finals, self.interim = [], ""
        self.ready = threading.Event()
        self.dead = threading.Event()
        self.failed = None
        self.closing = False
        self.sent = 0
        self.ws = None
        self.last_msg = time.monotonic()
        self.send_lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def text(self):
        parts = list(self.finals)
        if self.interim.strip():
            parts.append(self.interim.strip())
        return " ".join(p for p in parts if p).strip()

    def _setup_msg(self):
        itc = {"mode": "SMART" if self.cfg["clean_fillers"] else "VERBATIM"}
        codes = lang_info(self.cfg)[2]
        if codes:
            itc["languageCodes"] = codes
        v = vocab_list(self.cfg)
        if v:
            itc["customVocabulary"] = v
        model = self.cfg.get("gemini_live_model", "").strip() or "gemini-3.5-transcribe-live"
        return {"setup": {"model": f"models/{model}",
                          "generationConfig": {"responseModalities": ["TEXT"]},
                          "inputAudioTranscription": itc}}

    def _run(self):
        try:
            self.ws = websocket.create_connection(LIVE_URL.format(self.cfg["gemini_api_key"].strip()),
                                                  timeout=8, enable_multithread=True)
            self.ws.send(json.dumps(self._setup_msg()))
            self.ws.settimeout(0.5)
            while True:
                try:
                    msg = self.ws.recv()
                except websocket.WebSocketTimeoutException:
                    if self.closing and not self.ready.is_set():
                        break
                    continue
                if msg in ("", b"", None):
                    if not self.closing:
                        self.failed = "closed: " + str(getattr(self.ws, "status", ""))
                    break
                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8", "ignore")
                self.last_msg = time.monotonic()
                data = json.loads(msg)
                if "setupComplete" in data:
                    self.ready.set()
                    continue
                if data.get("error"):
                    self.failed = str(data["error"])[:200]
                    break
                sc = data.get("serverContent") or {}
                it = sc.get("interimInputTranscription")
                if it is not None and it.get("text") is not None:
                    self.interim = it["text"]
                    self.on_text(self.text())
                ft = sc.get("inputTranscription")
                if ft is not None and (ft.get("text") or "").strip():
                    self.finals.append(ft["text"].strip())
                    self.interim = ""
                    self.on_text(self.text())
        except websocket.WebSocketConnectionClosedException as e:
            if not self.closing:
                self.failed = f"closed: {e}"
        except Exception as e:
            if not self.closing:
                self.failed = repr(e)[:200]
        finally:
            if self.failed:
                log(f"live session failed: {self.failed}")
            self.dead.set()
            self.ready.set()

    def _send(self, obj):
        if self.ws is None or self.dead.is_set():
            return
        try:
            with self.send_lock:
                self.ws.send(json.dumps(obj))
        except Exception as e:
            if not self.closing:
                self.failed = f"send: {e!r}"[:200]

    def feed(self):
        """Stream any new microphone audio (called every 100 ms)."""
        if not self.ready.is_set() or self.dead.is_set():
            return
        blocks = self.rec.blocks
        n = len(blocks)
        while self.sent < n:
            end = min(n, self.sent + 10)      # up to 500 ms per message for the backlog
            raw = b"".join(blocks[self.sent:end])
            self._send({"realtimeInput": {"audio": {"data": base64.b64encode(raw).decode(),
                                                    "mimeType": "audio/pcm;rate=16000"}}})
            self.sent = end

    def finish(self, wait=3.0):
        """Flush, ask the server to finalize, and return the transcript (None if the session failed)."""
        if not self.ready.wait(timeout=2.5) or (self.failed and not self.finals and not self.interim):
            self.close()
            return None
        self.feed()
        n_final = len(self.finals)
        sent_at = time.monotonic()
        self._send({"realtimeInput": {"audioStreamEnd": True}})
        hard_end = sent_at + wait
        while time.monotonic() < hard_end and not self.dead.is_set():
            quiet = time.monotonic() - max(self.last_msg, sent_at)
            if len(self.finals) > n_final and not self.interim.strip() and quiet > 0.15:
                break
            if not self.interim.strip() and quiet > 0.8:
                break
            time.sleep(0.02)
        text = self.text()
        self.close()
        return text or None

    def close(self):
        self.closing = True
        try:
            if self.ws:
                self.ws.close(timeout=0.3)
        except Exception:
            pass

# --------------------------------------------------------------------------
# Controller: recording state machine + pipelined transcription
# --------------------------------------------------------------------------
IDLE, PTT, TAP_WAIT, HANDSFREE, BUSY = range(5)
MIN_SEG_BLOCKS = 400      # 20 s before we try to cut a chunk at a pause
MAX_SEG_BLOCKS = 900      # 45 s hard cut
PAUSE_BLOCKS = 12         # 600 ms of silence


def beep(kind, enabled):
    if enabled:
        threading.Thread(target=plat.play_beep, args=(kind,), daemon=True).start()


class Job:
    _ids = 0

    def __init__(self, cfg):
        Job._ids += 1
        self.id = Job._ids
        self.cfg = cfg
        self.seg_start = 0
        self.futures = []
        self.probe = None
        self.live = None


class Controller(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.cfg = app.cfg
        self.q = queue.Queue()
        self.rec = app.recorder
        self.state = IDLE
        self.press_t = self.rec_start = self.tap_deadline = 0.0
        self.ignore_next_up = False
        self.job = None
        self.cloud_pool = ThreadPoolExecutor(max_workers=3)
        self.local_pool = ThreadPoolExecutor(max_workers=1)

    def ui(self, *msg):
        self.app.ui_q.put(msg)

    def run(self):
        while True:
            try:
                ev = self.q.get(timeout=0.1)
            except queue.Empty:
                ev = None
            try:
                if ev:
                    self.handle(ev)
                self.tick()
            except Exception as e:
                log(f"controller error: {e!r}")
                self.rec.stop()
                self.state = IDLE
                self.ui("show", "error", "কিছু একটা ভুল হয়েছে")

    def tick(self):
        if self.state not in (PTT, HANDSFREE, TAP_WAIT):
            return
        now = time.monotonic()
        if self.state == TAP_WAIT and now >= self.tap_deadline:
            self.cancel(silent=True)
            return
        if now - self.rec_start > self.cfg["max_seconds"]:
            self.finish()
            return
        if self.job and self.job.live:
            self.job.live.feed()
        else:
            self.maybe_cut()

    # ---- recording ----
    def start_recording(self):
        if not self.cfg.ready():
            self.ui("show", "error", "আগে Settings এ API key দাও")
            self.ui("settings")
            return False
        try:
            self.rec.start(self.cfg.get("mic", ""))
        except Exception as e:
            log(f"mic error: {e!r}")
            self.ui("show", "error", "মাইক্রোফোন চালু করা যায়নি")
            return False
        self.job = Job(dict(self.cfg))
        self.rec_start = time.monotonic()
        if self.job.cfg["provider"] == "gemini_live":
            self.job.live = LiveSession(self.job.cfg, self.rec, lambda t: self.ui("live", t))
        prewarm(self.job.cfg["provider"])
        beep("start", self.cfg["sounds"])
        return True

    def handle(self, ev):
        kind = ev[0]
        if kind == "down":
            if ev[2]:
                send_mask_key()
            if self.state == IDLE:
                if self.start_recording():
                    self.state = PTT
                    self.press_t = ev[1]
                    self.ui("show", "rec")
            elif self.state == TAP_WAIT:
                self.state = HANDSFREE
                self.ignore_next_up = True
                self.ui("show", "hands")
            elif self.state == HANDSFREE:
                self.finish()
        elif kind == "up":
            if self.state == PTT:
                if (ev[1] - self.press_t) * 1000 >= self.cfg["hold_threshold_ms"]:
                    self.finish()
                else:
                    self.state = TAP_WAIT
                    self.tap_deadline = ev[1] + self.cfg["double_tap_ms"] / 1000
            elif self.state == HANDSFREE and self.ignore_next_up:
                self.ignore_next_up = False
        elif kind in ("other", "esc"):
            if self.state in (PTT, TAP_WAIT, HANDSFREE):
                self.cancel(silent=(kind == "other"))
        elif kind == "done":
            if self.job and ev[1] == self.job.id:
                self.on_done(ev[2], ev[3])
        elif kind == "handsfree":
            if self.state == IDLE:
                if self.start_recording():
                    self.state = HANDSFREE
                    self.ignore_next_up = False
                    self.ui("show", "hands")
            elif self.state in (HANDSFREE, PTT, TAP_WAIT):
                self.finish()
        elif kind == "paste_last":
            if self.app.history.items and self.state == IDLE:
                self.paste(self.app.history.items[0]["text"], force=True)

    def cancel(self, silent=False):
        self.rec.stop()
        if self.job and self.job.live:
            self.job.live.close()
        self.job = None
        self.state = IDLE
        self.ui("hide")
        if not silent:
            beep("cancel", self.cfg["sounds"])

    # ---- pipelined chunks ----
    def _pool(self):
        return self.local_pool if self.job.cfg["provider"] == "local" else self.cloud_pool

    def _submit(self, a, b, trim_start, trim_end):
        rms = self.rec.rms
        thr = speech_threshold(rms)
        seg = rms[a:b]
        if not seg or max(seg) < thr * 1.3:
            return
        if trim_start:
            while a < b and rms[a] < thr:
                a += 1
            a = max(0, a - 6)
        if trim_end:
            while b > a and rms[b - 1] < thr:
                b -= 1
            b = min(len(rms), b + 6)
        raw = b"".join(self.rec.blocks[a:b])
        if len(raw) < SR * 2 * 0.3:
            return
        self.job.futures.append(self._pool().submit(transcribe_raw, raw, self.job.cfg))

    def maybe_cut(self):
        if not self.job or not self.job.cfg.get("pipeline", True):
            return
        rms = self.rec.rms
        n = len(rms)
        s = self.job.seg_start
        if n - s < MIN_SEG_BLOCKS:
            return
        thr = speech_threshold(rms)
        k = 0
        for i in range(n - 1, s, -1):
            if rms[i] < thr:
                k += 1
            else:
                break
        if k >= PAUSE_BLOCKS:
            cut = n - k // 2
        elif n - s >= MAX_SEG_BLOCKS:
            window = rms[n - 40:n]
            cut = n - 40 + window.index(min(window)) + 1
        else:
            return
        self._submit(s, cut, trim_start=(s == 0), trim_end=False)
        self.job.seg_start = cut

    def finish(self):
        self.rec.stop()
        job = self.job
        if not job:
            self.state = IDLE
            return
        rms = self.rec.rms
        thr = speech_threshold(rms)
        if job.live and len(rms) >= 7 and max(rms or [0]) >= thr * 1.3:
            return self.finish_live(job)
        if job.live:
            job.live.close()
        if len(rms) < 7 or (not job.futures and (not rms or max(rms[job.seg_start:] or [0]) < thr * 1.3)):
            self.state = IDLE
            self.job = None
            self.ui("show", "error", "কিছু শোনা যায়নি")
            return
        self._submit(job.seg_start, len(rms), trim_start=(job.seg_start == 0), trim_end=True)
        if not job.futures:
            self.state = IDLE
            self.job = None
            self.ui("show", "error", "কিছু শোনা যায়নি")
            return
        beep("stop", self.cfg["sounds"])
        self.state = BUSY
        if job.cfg["provider"] == "local" and not LOCAL.ready(job.cfg):
            self.ui("show", "info", "অফলাইন মডেল লোড হচ্ছে…")
        else:
            self.ui("show", "busy")
        self.start_probe(job)

        def collect():
            texts, err = [], None
            for f in job.futures:
                try:
                    t = f.result()
                    if t:
                        texts.append(t)
                except EngineError as e:
                    err = str(e)
                except QuotaError:
                    err = "ফ্রি লিমিট শেষ, একটু পরে চেষ্টা করো"
                except requests.exceptions.RequestException as e:
                    log(f"network error: {e!r}")
                    err = net_error_text(e)
                except Exception as e:
                    log(f"transcribe error: {e!r}")
                    err = f"Error: {str(e)[:90]}"
            self.q.put(("done", job.id, " ".join(texts).strip(), err if not texts else None))
        threading.Thread(target=collect, daemon=True).start()

    def start_probe(self, job):
        if job.cfg["smart_paste"]:
            job.probe = Future()

            def probe(fut=job.probe):
                try:
                    fut.set_result(focused_is_editable())
                except Exception:
                    fut.set_result(None)
            threading.Thread(target=probe, daemon=True).start()

    def finish_live(self, job):
        beep("stop", self.cfg["sounds"])
        self.state = BUSY
        self.ui("show", "busy")
        self.start_probe(job)
        raw_all = b"".join(self.rec.blocks)

        def work():
            text = job.live.finish()
            if text:
                self.q.put(("done", job.id, text, None))
                return
            log("live engine unavailable, falling back to file transcription")
            try:
                t, err = transcribe_raw(raw_all, job.cfg), None
            except EngineError as e:
                t, err = "", str(e)
            except QuotaError:
                t, err = "", "ফ্রি লিমিট শেষ, একটু পরে চেষ্টা করো"
            except requests.exceptions.RequestException as e:
                log(f"network error: {e!r}")
                t, err = "", net_error_text(e)
            except Exception as e:
                log(f"transcribe error: {e!r}")
                t, err = "", f"Error: {str(e)[:90]}"
            self.q.put(("done", job.id, t, err if not t else None))
        threading.Thread(target=work, daemon=True).start()

    def on_done(self, text, err):
        job = self.job
        self.job = None
        self.state = IDLE
        if err:
            self.ui("show", "error", err)
            return
        if not text:
            self.ui("show", "error", "কিছু শোনা যায়নি")
            return
        self.app.history.add(text)
        self.ui("hide")
        editable = None
        if job and job.probe:
            try:
                editable = job.probe.result(timeout=1.0)
            except Exception:
                editable = None
        self.paste(text, editable=editable)

    def paste(self, text, editable=None, force=False):
        wait_keys_released(self.app.hook.combo_vks | plat.MODIFIER_VKS)
        if not force and self.cfg["smart_paste"] and editable is False:
            pyperclip.copy(text)
            self.ui("popup", text)
            return
        old = None
        if self.cfg["restore_clipboard"]:
            try:
                old = pyperclip.paste()
            except Exception:
                old = None
        pyperclip.copy(text)
        time.sleep(0.03)
        press_paste()
        if old:
            def restore():
                time.sleep(0.6)
                try:
                    if pyperclip.paste() == text:
                        pyperclip.copy(old)
                except Exception:
                    pass
            threading.Thread(target=restore, daemon=True).start()


# --------------------------------------------------------------------------
# Meeting notes: record a whole meeting (your mic + everyone else through the speakers),
# transcribe it in 2-minute pieces while it runs, then write a summary with key points.
# --------------------------------------------------------------------------
MEET_DIR = os.path.join(DATA_DIR, "meetings")
MEET_CHUNK_S = 60               # transcribe every minute of audio (live transcript, short wait at the end)
MEET_MAX_HOURS = 6
MEETING_SUPPORTED = plat.SUPPORTS_MEETINGS


def _fmt_clock(seconds):
    seconds = int(seconds)
    h, m, s = seconds // 3600, (seconds % 3600) // 60, seconds % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class MeetingRecorder:
    """Two tracks on disk, kept in sync with the wall clock: mic.pcm (you) and sys.pcm (what the
    computer plays, i.e. the other people in the call). 16 kHz mono int16."""

    def __init__(self, folder, mic_name=""):
        self.folder, self.mic_name = folder, mic_name
        self.paths = {k: os.path.join(folder, f"{k}.pcm") for k in ("mic", "sys")}
        self.written = {"mic": 0, "sys": 0}
        self.level = {"mic": 0.0, "sys": 0.0}
        self.alive = {"mic": False, "sys": False}
        self.errors = {}
        self.running = False
        self.threads = []
        self.lock = threading.Lock()

    def start(self):
        if plat.IS_WIN:
            import soundcard  # noqa: F401  (fail early, in the caller's thread, if it is missing)
        self.t0 = time.monotonic()
        self.running = True
        for kind in ("mic", "sys"):
            t = threading.Thread(target=self._loop_mac if plat.IS_MAC else self._loop, args=(kind,), daemon=True)
            t.start()
            self.threads.append(t)

    def _device(self, kind):
        import soundcard as sc
        if kind == "sys":
            spk = sc.default_speaker()
            return sc.get_microphone(id=str(spk.id), include_loopback=True), spk.id
        if self.mic_name:
            for m in sc.all_microphones():
                if m.name == self.mic_name or m.name.startswith(self.mic_name) or self.mic_name in m.name:
                    return m, None
        return sc.default_microphone(), None

    def _loop(self, kind):
        import numpy as np
        import soundcard as sc
        f = open(self.paths[kind], "ab")
        while self.running:
            try:
                dev, spk_id = self._device(kind)
                with dev.recorder(samplerate=SR, channels=1, blocksize=SR // 10) as r:
                    self.alive[kind] = True
                    self.errors.pop(kind, None)
                    last_check = time.monotonic()
                    while self.running:
                        data = r.record(numframes=SR // 10)          # 100 ms
                        a = data[:, 0] if data.ndim == 2 else data
                        a = np.nan_to_num(a)
                        self.level[kind] = min(1.0, float(np.sqrt(np.mean(a * a))) * 9) if len(a) else 0.0
                        self._append(kind, f, np.clip(a * 32767, -32768, 32767).astype("<i2").tobytes(), len(a))
                        if kind == "sys" and time.monotonic() - last_check > 5:
                            last_check = time.monotonic()
                            try:
                                if sc.default_speaker().id != spk_id:      # headphones plugged in, etc.
                                    log("meeting: output device changed, reopening loopback")
                                    break
                            except Exception:
                                pass
            except Exception as e:
                self.alive[kind] = False
                if self.errors.get(kind) != str(e):
                    log(f"meeting {kind} capture error: {e!r}")
                self.errors[kind] = str(e)
                time.sleep(2)
        self.alive[kind] = False
        f.close()

    def _loop_mac(self, kind):
        """macOS: microphone through PortAudio, system audio through ScreenCaptureKit."""
        import numpy as np
        f = open(self.paths[kind], "ab")
        chunks = queue.Queue()
        while self.running:
            src = None
            try:
                if kind == "mic":
                    src = sd.RawInputStream(samplerate=SR, channels=1, dtype="int16", blocksize=SR // 10,
                                            device=Recorder._device_index(self.mic_name),
                                            callback=lambda indata, frames, t, status: chunks.put(bytes(indata)))
                    src.start()
                else:
                    src = plat.SystemAudioCapture(chunks.put)
                    src.start()
                self.alive[kind] = True
                self.errors.pop(kind, None)
                while self.running:
                    try:
                        pcm = chunks.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    a = np.frombuffer(pcm, dtype="<i2")
                    if len(a):
                        self.level[kind] = min(1.0, float(np.sqrt(np.mean((a / 32768.0) ** 2))) * 9)
                        self._append(kind, f, pcm, len(a))
            except Exception as e:
                self.alive[kind] = False
                if self.errors.get(kind) != str(e):
                    log(f"meeting {kind} capture error: {e!r}")
                self.errors[kind] = str(e)
                for _ in range(10):
                    if not self.running:
                        break
                    time.sleep(0.5)
            finally:
                if src is not None:
                    try:
                        src.stop()
                        if kind == "mic":
                            src.close()
                    except Exception:
                        pass
        self.alive[kind] = False
        f.close()

    def _append(self, kind, f, pcm, n):
        with self.lock:
            start = int((time.monotonic() - self.t0) * SR) - n
            gap = start - self.written[kind]
            if gap > SR // 4:                      # device delivered nothing for a while: keep the timeline
                f.write(b"\x00\x00" * gap)
                self.written[kind] += gap
            f.write(pcm)
            self.written[kind] += n
            f.flush()

    def elapsed(self):
        return time.monotonic() - self.t0 if self.running else max(self.written.values()) / SR

    def available(self):
        """Samples that both tracks have (a dead track does not hold the other back)."""
        live = [self.written[k] for k in ("mic", "sys") if self.alive[k] or self.written[k]]
        if not live:
            return 0
        return min(live) if self.running else max(live)

    def stop(self):
        self.running = False
        for t in self.threads:
            t.join(timeout=3)
        n = max(self.written.values())
        for kind in ("mic", "sys"):           # pad the shorter track so both line up
            if self.written[kind] < n:
                with open(self.paths[kind], "ab") as f:
                    f.write(b"\x00\x00" * (n - self.written[kind]))
                self.written[kind] = n


def _read_track(path, a, b):
    try:
        with open(path, "rb") as f:
            f.seek(a * 2)
            data = f.read((b - a) * 2)
    except OSError:
        data = b""
    return data + b"\x00\x00" * (b - a - len(data) // 2)


def mix_tracks(folder, a, b):
    """Mix mic + system audio. When the other side is talking loudly, the mic (which also hears the
    speakers) is turned down so voices are not doubled."""
    import numpy as np
    mic = np.frombuffer(_read_track(os.path.join(folder, "mic.pcm"), a, b), dtype="<i2").astype(np.float32)
    sysa = np.frombuffer(_read_track(os.path.join(folder, "sys.pcm"), a, b), dtype="<i2").astype(np.float32)
    n = len(mic) // BLOCK * BLOCK
    if n:
        mb = mic[:n].reshape(-1, BLOCK)
        sb = sysa[:n].reshape(-1, BLOCK)
        m_rms = np.sqrt((mb * mb).mean(axis=1)) + 1
        s_rms = np.sqrt((sb * sb).mean(axis=1)) + 1
        gain = np.where(s_rms > 2 * m_rms, 0.35, 1.0).astype(np.float32)
        mic[:n] = (mb * gain[:, None]).reshape(-1)
    out = np.clip(mic + sysa, -32768, 32767).astype("<i2")
    return out.tobytes()


def split_tracks(folder, a, b):
    """(my voice, everyone else). Blocks where the mic only hears the speakers (echo) are silenced in
    the mic track, so the other people's words are not attributed to you."""
    import numpy as np
    mic = np.frombuffer(_read_track(os.path.join(folder, "mic.pcm"), a, b), dtype="<i2").astype(np.float32)
    sysa = np.frombuffer(_read_track(os.path.join(folder, "sys.pcm"), a, b), dtype="<i2").astype(np.float32)
    n = len(mic) // BLOCK * BLOCK
    if n:
        mb = mic[:n].reshape(-1, BLOCK)
        sb = sysa[:n].reshape(-1, BLOCK)
        m_rms = np.sqrt((mb * mb).mean(axis=1)) + 1
        s_rms = np.sqrt((sb * sb).mean(axis=1)) + 1
        echo = s_rms > 2 * m_rms
        mic[:n] = (mb * np.where(echo, 0.0, 1.0)[:, None]).reshape(-1)
    return mic.astype("<i2").tobytes(), sysa.astype("<i2").tobytes()


def _block_levels(raw):
    import numpy as np
    a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    n = len(a) // BLOCK * BLOCK
    if not n:
        return []
    return list(np.sqrt((a[:n].reshape(-1, BLOCK) ** 2).mean(axis=1)))


ME_LABEL = "আমি"


def _meeting_track_prompt(cfg, seconds, who, context):
    lines = [
        "You transcribe meeting recordings.",
        f"This audio is a {int(seconds)}-second part of a longer online meeting.",
        ("It is the local user's own microphone, so normally only the user is speaking." if who == "me" else
         "It is the call audio: the other participants (one or more people), not the local user."),
        "Transcribe everything that is said, faithfully and completely. Never summarize, translate, answer "
        "questions, or skip parts.",
        "People often mix Bangla and English: write Bangla words in Bangla script and English words in English "
        "letters, exactly as spoken (e.g. 'আমরা next week এ launch করবো'). Other languages: their own script.",
        "Drop filler sounds (um, uh, আ, আহ) but keep every meaningful word. Use natural punctuation ('।' for Bangla).",
        "Return a JSON list of segments in time order. Each segment has: start = approximate seconds from the "
        "beginning of this audio, speaker = " + ('"me"' if who == "me" else
                                    '"Speaker 1", "Speaker 2", ... (the same number for the same voice; use the '
                                    "person's real name instead if it is clearly said)") +
        ", text = what was said. Start a new segment at every change of speaker, or about every 20 seconds.",
        "If there is no speech at all, return [].",
    ]
    v = vocab_list(cfg)
    if v:
        lines.append("These names and terms may appear; spell them exactly like this: " + ", ".join(v))
    if context:
        lines.append("For continuity, the transcript so far ended with:\n" + context[-600:])
    return "\n".join(lines)


SEGMENT_SCHEMA = {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
    "start": {"type": "NUMBER"}, "speaker": {"type": "STRING"}, "text": {"type": "STRING"}},
    "required": ["start", "text"]}}


# Meeting pieces: the Flash-Lite models answer in a few seconds and have the biggest free quota.
# The final summary uses the stronger Flash model first.
MEETING_TX_MODELS = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]


def _gemini_generate(cfg, parts, system_text, timeout, schema=None, prefer=None):
    """One generateContent call across the Gemini model chain (each model has its own free quota)."""
    models = []
    for m in (prefer or []) + [cfg.get("gemini_model", "").strip() or "gemini-flash-latest"] + GEMINI_FALLBACKS:
        if m not in models:
            models.append(m)
    headers = {"x-goog-api-key": cfg["gemini_api_key"].strip()}
    now = time.monotonic()
    last = None
    for model in models:
        if _model_blocked.get(model, 0) > now:
            continue
        body = {"system_instruction": {"parts": [{"text": system_text}]},
                "contents": [{"role": "user", "parts": parts}]}
        gen = {}
        if model.startswith("gemini-2.5-flash"):
            gen["thinkingConfig"] = {"thinkingBudget": 0}
        elif "flash" in model:
            gen["thinkingConfig"] = {"thinkingLevel": "low"}
        if schema:
            gen.update({"responseMimeType": "application/json", "responseSchema": schema})
        if gen:
            body["generationConfig"] = gen
        url = f"{GEMINI_BASE}/v1beta/models/{model}:generateContent"
        try:
            r = SESSION.post(url, json=body, headers=headers, timeout=timeout)
            if r.status_code == 400 and gen.pop("thinkingConfig", None) is not None:
                if not gen:
                    body.pop("generationConfig")
                r = SESSION.post(url, json=body, headers=headers, timeout=timeout)
            _gemini_check(r, model)
            return _gemini_text(r)
        except QuotaError as e:
            _model_blocked[model] = now + min(e.wait, 6 * 3600)
            last = e
        except (BusyError, requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            _model_blocked[model] = now + 60
            last = e
        except EmptyResult as e:
            last = e
    if isinstance(last, QuotaError):
        raise QuotaError("all gemini models exhausted", 60)
    raise EngineError(f"Gemini: {str(last)[:120] if last else 'no model available'}")


def _parse_segments(text, who):
    label = ME_LABEL if who == "me" else "Speaker 1"
    try:
        data = json.loads(text)
        out = []
        for seg in data if isinstance(data, list) else []:
            t = clean_text(str(seg.get("text", "")))
            if not t:
                continue
            sp = str(seg.get("speaker") or label).strip()
            if who == "me" or sp.lower() in ("me", "user", "local user"):
                sp = ME_LABEL
            out.append([float(seg.get("start") or 0), sp, t])
        return out
    except Exception:
        t = clean_text(text)
        return [[0.0, label, t]] if t else []


def transcribe_meeting_track(raw, cfg, who, context=""):
    """Timed segments [[seconds, speaker, text], ...] for one track of a meeting piece."""
    seconds = len(raw) / 2 / SR
    label = ME_LABEL if who == "me" else "Speaker 1"
    if cfg.get("provider") == "local" and LOCAL_AVAILABLE:
        t = clean_text(LOCAL.transcribe(raw, cfg))
        return [[0.0, label, t]] if t else []
    wav = to_wav(raw)
    if cfg.get("gemini_api_key", "").strip():
        try:
            parts = [{"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(wav).decode()}},
                     {"text": "Transcribe this meeting audio."}]
            text = _gemini_generate(cfg, parts, _meeting_track_prompt(cfg, seconds, who, context), 120, SEGMENT_SCHEMA,
                                    prefer=MEETING_TX_MODELS)
            return _parse_segments(text, who)
        except (QuotaError, EngineError) as e:
            log(f"meeting {who}: gemini failed ({str(e)[:120]}), trying backup")
            if not cfg.get("groq_api_key", "").strip() and not cfg.get("openai_api_key", "").strip():
                raise
    if cfg.get("groq_api_key", "").strip():
        t = clean_text(_groq(wav, cfg))
    elif cfg.get("openai_api_key", "").strip():
        t = clean_text(transcribe_openai_style(wav, cfg, "https://api.openai.com/v1/audio/transcriptions",
                                               cfg["openai_api_key"].strip(),
                                               cfg.get("openai_model", "").strip() or "gpt-4o-transcribe"))
    else:
        raise EngineError("মিটিং নোটের জন্য Settings এ Gemini (ফ্রি) বা Groq key দাও")
    return [[0.0, label, t]] if t else []


NOTES_HEADINGS = {
    "bn": ["সারসংক্ষেপ", "মূল পয়েন্ট", "সিদ্ধান্ত", "করণীয়", "প্রশ্ন ও পরবর্তী ধাপ"],
    "en": ["Summary", "Key points", "Decisions", "Action items", "Open questions & next steps"],
}


def _summary_prompt(lang):
    if lang == "en":
        rule = "Write the notes in English."
        h = NOTES_HEADINGS["en"]
    elif lang == "bn":
        rule = "Write the notes in Bangla (keep English technical terms, product names and numbers as they are)."
        h = NOTES_HEADINGS["bn"]
    else:
        rule = ("Write the notes in the main language of the meeting: if it is mostly Bangla or Bangla-English mix, "
                "write in Bangla (keep English terms in English); otherwise in that language. "
                "Translate the headings into that language too.")
        h = NOTES_HEADINGS["bn"]
    return "\n".join([
        "You are an expert meeting assistant. Turn the meeting transcript into clear, well-organised notes in Markdown.",
        "Be faithful: never invent facts, names, numbers, dates or decisions. If something is unclear, say so briefly.",
        f"'{ME_LABEL}' is the person who recorded the meeting (the user). 'Speaker 1', 'Speaker 2', ... are the other "
        "participants, labelled by voice; use real names only when the conversation reveals them.",
        rule,
        "Use exactly this structure:",
        "# <a short, specific title for the meeting>",
        f"## {h[0]}",
        "2 to 4 sentences: what the meeting was about and the outcome.",
        f"## {h[1]}",
        "Bullet points grouped by topic (use ### for topic names when there are several). One clear point per bullet; "
        "keep important numbers, dates, prices and names.",
        f"## {h[2]}",
        "Bullets of what was decided. Write '—' if nothing was decided.",
        f"## {h[3]}",
        "Checklist lines: '- [ ] task — owner — due date' (leave out owner or date when unknown). '—' if none.",
        f"## {h[4]}",
        "Unresolved questions, risks and follow-ups. '—' if none.",
    ])


def summarize_meeting(transcript, cfg, lang="auto"):
    system = _summary_prompt(lang)
    user = "Meeting transcript:\n\n" + transcript
    if cfg.get("gemini_api_key", "").strip():
        try:
            return _gemini_generate(cfg, [{"text": user}], system, 240,
                                    prefer=["gemini-3.8-flash", "gemini-flash-latest", "gemini-3.7-flash", "gemini-3.5-flash"]).strip()
        except (QuotaError, EngineError) as e:
            log(f"summary: gemini failed ({str(e)[:120]})")
            if not cfg.get("groq_api_key", "").strip() and not cfg.get("openai_api_key", "").strip():
                raise
    for key_name, url, model in (
            ("groq_api_key", "https://api.groq.com/openai/v1/chat/completions",
             cfg.get("groq_chat_model") or "openai/gpt-oss-120b"),
            ("groq_api_key", "https://api.groq.com/openai/v1/chat/completions", "qwen/qwen3.8-27b"),
            ("openai_api_key", "https://api.openai.com/v1/chat/completions",
             cfg.get("openai_chat_model") or "gpt-4o-mini")):
        key = cfg.get(key_name, "").strip()
        if not key:
            continue
        r = SESSION.post(url, timeout=240, headers={"Authorization": f"Bearer {key}"},
                         json={"model": model, "temperature": 0.2,
                               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user[-400000:]}]})
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"].strip()
        log(f"summary via {model} failed: {api_error(r)}")
    raise EngineError("সারাংশ বানানো যায়নি, পরে 'আবার সারাংশ বানাও' চাপো")


def place_segments(segments, levels):
    """Give each transcribed segment a time inside its piece. Gemini's own timestamps are unreliable, so
    the text is spread over the moments where this track actually has speech (by text length)."""
    if not segments:
        return []
    thr = 0.006
    speech = [i for i, lv in enumerate(levels) if lv >= thr] or list(range(len(levels))) or [0]
    total = sum(max(1, len(t)) for _, _, t in segments)
    out, done = [], 0
    for _, sp, text in segments:
        k = min(len(speech) - 1, int(done / total * len(speech)))
        out.append([speech[k] * BLOCK / SR, sp, text])
        done += max(1, len(text))
    return out


def wait_for_gemini(max_wait=90, stop=None):
    """If every Gemini model is cooling down after a rate limit, wait (up to max_wait s) for the first one."""
    if not _model_blocked:
        return
    soonest = min(_model_blocked.values()) - time.monotonic()
    if soonest > 0:
        delay = min(soonest + 1, max_wait)
        if stop is not None:
            stop.wait(delay)
        else:
            time.sleep(delay)


class MeetingStore:
    @staticmethod
    def list():
        out = []
        try:
            for name in os.listdir(MEET_DIR):
                meta = MeetingStore.load(name)
                if meta:
                    out.append(meta)
        except OSError:
            pass
        return sorted(out, key=lambda m: m.get("started", ""), reverse=True)

    @staticmethod
    def folder(mid):
        return os.path.join(MEET_DIR, mid)

    @staticmethod
    def load(mid):
        try:
            with open(os.path.join(MEET_DIR, mid, "meta.json"), "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    @staticmethod
    def save(meta):
        p = os.path.join(MEET_DIR, meta["id"], "meta.json")
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)

    @staticmethod
    def read_text(mid, name):
        try:
            with open(os.path.join(MEET_DIR, mid, name), "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    @staticmethod
    def write_text(mid, name, text):
        with open(os.path.join(MEET_DIR, mid, name), "w", encoding="utf-8") as f:
            f.write(text)

    @staticmethod
    def delete(mid):
        import shutil
        p = os.path.abspath(os.path.join(MEET_DIR, mid))
        if os.path.dirname(p) == os.path.abspath(MEET_DIR):
            shutil.rmtree(p, ignore_errors=True)


def build_transcript(meta):
    out = []
    for c in meta.get("chunks", []):
        if c.get("segments"):
            for t, sp, text in c["segments"]:
                out.append(f"[{_fmt_clock(t)}] {sp}: {text}")
        elif c.get("text"):
            out.append(f"[{_fmt_clock(c['a'] / SR)}] {c['text']}")
        elif c.get("status") == "failed":
            out.append(f"[{_fmt_clock(c['a'] / SR)}] (এই অংশ এখনো লেখা হয়নি: {c.get('error', '')[:80]})")
    return "\n".join(out)


class MeetingSession:
    """Runs one meeting: recording + background transcription + final summary.
    With `mid` it re-processes an existing (interrupted or partly failed) meeting from its saved audio."""

    def __init__(self, app, label="", auto_key=None, mid=None):
        self.app, self.cfg = app, dict(app.cfg)
        self.auto_key = auto_key
        self.stop_evt = threading.Event()
        self.recorder = None
        if mid:
            self.meta = MeetingStore.load(mid)
            self.folder = MeetingStore.folder(mid)
            self.meta["status"] = "processing"
        else:
            started = datetime.now()
            mid = started.strftime("%Y-%m-%d_%H%M%S")
            self.folder = MeetingStore.folder(mid)
            os.makedirs(self.folder, exist_ok=True)
            self.meta = {"id": mid, "title": label and f"{label} মিটিং" or "মিটিং নোট",
                         "app": label, "started": started.strftime("%Y-%m-%d %H:%M"), "duration": 0,
                         "status": "recording", "processed": 0, "chunks": [], "lang": self.cfg.get("notes_language", "auto")}
            MeetingStore.save(self.meta)
            self.recorder = MeetingRecorder(self.folder, self.cfg.get("mic", ""))
            self.recorder.start()
        self.id = self.meta["id"]
        threading.Thread(target=self._work, daemon=True).start()

    @property
    def recording(self):
        return self.recorder is not None and self.recorder.running

    def elapsed(self):
        return self.recorder.elapsed() if self.recorder else self.meta.get("duration", 0)

    def stop(self):
        if self.recorder and self.recorder.running:
            self.recorder.stop()
            self.meta["duration"] = int(self.recorder.elapsed())
        self.stop_evt.set()

    def abort(self):
        """App is quitting: stop recording, leave the audio so the meeting can be processed next time."""
        if self.recorder and self.recorder.running:
            self.recorder.stop()
            self.meta["duration"] = int(self.recorder.elapsed())
        self.meta["status"] = "interrupted"
        try:
            MeetingStore.save(self.meta)
        except Exception:
            pass

    def _event(self, *msg):
        self.app.ui_q.put(msg)

    def _total_samples(self):
        if self.recorder:
            return self.recorder.available()
        sizes = []
        for k in ("mic", "sys"):
            try:
                sizes.append(os.path.getsize(os.path.join(self.folder, f"{k}.pcm")) // 2)
            except OSError:
                pass
        return max(sizes) if sizes else 0

    def _cut_point(self, a, b):
        """End the piece in the quietest 100 ms of its last 8 seconds, so words are not cut in half."""
        win = min(8 * SR, b - a)
        levels = _block_levels(mix_tracks(self.folder, b - win, b))
        if not levels:
            return b
        i = min(range(len(levels)), key=lambda k: levels[k])
        return b - win + (i + 1) * BLOCK

    def _process(self, a, b):
        mine, others = split_tracks(self.folder, a, b)
        chunk = {"a": a, "b": b, "segments": []}
        context = build_transcript(self.meta)[-600:]
        errors = []
        jobs = []
        for who, raw in (("others", others), ("me", mine)):
            levels = _block_levels(raw)
            if levels and max(levels) >= 0.006:
                jobs.append((who, raw, levels))

        def one(job):                      # both voices are transcribed at the same time
            who, raw, levels = job
            try:
                return place_segments(transcribe_meeting_track(raw, self.cfg, who, context), levels), None
            except Exception as e:
                log(f"meeting piece {a // SR}s ({who}) failed: {e!r}")
                return [], str(e)[:200]
        with ThreadPoolExecutor(max_workers=2) as ex:
            for segs, err in ex.map(one, jobs):
                if err:
                    errors.append(err)
                for t, sp, text in segs:
                    chunk["segments"].append([a / SR + t, sp, text])
        chunk["segments"].sort(key=lambda x: x[0])
        if errors and not chunk["segments"]:
            chunk["status"], chunk["error"] = "failed", errors[0]
        elif errors:
            chunk["status"], chunk["error"] = "failed", errors[0]   # keep what we got, retry the rest later
        else:
            chunk["status"] = "done" if chunk["segments"] else "silent"
        return chunk

    def _save_progress(self):
        MeetingStore.write_text(self.id, "transcript.md", build_transcript(self.meta))
        MeetingStore.save(self.meta)
        self._event("meeting_update", self.id)

    def _work(self):
        try:
            step = MEET_CHUNK_S * SR
            pos = self.meta.get("processed", 0)
            # 1. while recording: transcribe every finished 2-minute piece
            while self.recording:
                if self.recorder.elapsed() > MEET_MAX_HOURS * 3600:
                    self._event("meeting_stop_request",)
                if self._total_samples() - pos >= step + 8 * SR:
                    cut = self._cut_point(pos, pos + step)
                    self.meta["chunks"].append(self._process(pos, cut))
                    pos = self.meta["processed"] = cut
                    self.meta["duration"] = int(self.recorder.elapsed())
                    self._save_progress()
                else:
                    self.stop_evt.wait(2)
            # 2. after the meeting: the remaining pieces, all at once, then retry failed pieces
            self.meta["status"], self.meta["stage"] = "processing", "transcribing"
            self._save_progress()
            total = self._total_samples()
            pieces = []
            while total - pos > SR:
                end = total if total - pos <= step + 20 * SR else self._cut_point(pos, pos + step)
                pieces.append((pos, end))
                pos = end
            with ThreadPoolExecutor(max_workers=2) as ex:
                for chunk in ex.map(lambda ab: self._process(*ab), pieces):
                    self.meta["chunks"].append(chunk)
            self.meta["processed"] = pos
            self._save_progress()
            for attempt in range(3):                       # free-tier limits are per minute: wait, then retry
                failed = [i for i, c in enumerate(self.meta["chunks"]) if c.get("status") == "failed"]
                if not failed:
                    break
                self.meta["stage"] = "retrying"
                self._save_progress()
                wait_for_gemini(90)
                for i in failed:
                    c = self.meta["chunks"][i]
                    self.meta["chunks"][i] = self._process(c["a"], c["b"])
                    self._save_progress()
            self.meta["duration"] = max(self.meta.get("duration", 0), int(total / SR))
            # 3. summary
            self.meta["stage"] = "summarizing"
            self._save_progress()
            transcript = build_transcript(self.meta)
            spoken = [c for c in self.meta["chunks"] if c.get("segments") or c.get("text")]
            if not spoken:
                self.meta["status"] = "empty" if not any(c.get("status") == "failed" for c in self.meta["chunks"]) else "failed"
                MeetingStore.write_text(self.id, "notes.md", "")
            else:
                try:
                    notes = None
                    for attempt in range(4):         # Google's "high demand" errors usually pass in seconds
                        try:
                            notes = summarize_meeting(transcript, self.cfg, self.meta.get("lang", "auto"))
                            break
                        except (QuotaError, EngineError):
                            if attempt == 3:
                                raise
                            wait_for_gemini(45)
                            time.sleep(5 * (attempt + 1))
                    MeetingStore.write_text(self.id, "notes.md", notes)
                    first = notes.strip().splitlines()[0] if notes.strip() else ""
                    if first.startswith("#"):
                        self.meta["title"] = first.lstrip("#").strip()[:90] or self.meta["title"]
                    failed = any(c.get("status") == "failed" for c in self.meta["chunks"])
                    self.meta["status"] = "partial" if failed else "done"
                except Exception as e:
                    log(f"meeting summary failed: {e!r}")
                    self.meta["status"], self.meta["error"] = "summary_failed", str(e)[:200]
            self.meta.pop("stage", None)
            # 4. tidy up the raw audio
            if self.meta["status"] in ("done", "empty"):
                self._cleanup_audio(total)
            self._save_progress()
            self._event("meeting_done", self.id)
        except Exception as e:
            log(f"meeting worker crashed: {e!r}")
            self.meta["status"], self.meta["error"] = "failed", str(e)[:200]
            try:
                self._save_progress()
            except Exception:
                pass
            self._event("meeting_done", self.id)

    def _cleanup_audio(self, total):
        if self.cfg.get("keep_meeting_audio"):
            try:
                with wave.open(os.path.join(self.folder, "meeting.wav"), "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(SR)
                    step = 60 * SR
                    for a in range(0, total, step):
                        w.writeframes(mix_tracks(self.folder, a, min(total, a + step)))
            except Exception as e:
                log(f"save meeting.wav failed: {e!r}")
                return
        for k in ("mic", "sys"):
            try:
                os.remove(os.path.join(self.folder, f"{k}.pcm"))
            except OSError:
                pass


def resummarize(mid, cfg):
    meta = MeetingStore.load(mid)
    notes = summarize_meeting(build_transcript(meta), cfg, meta.get("lang", "auto"))
    MeetingStore.write_text(mid, "notes.md", notes)
    first = notes.strip().splitlines()[0] if notes.strip() else ""
    if first.startswith("#"):
        meta["title"] = first.lstrip("#").strip()[:90] or meta["title"]
    if meta.get("status") == "summary_failed":
        meta["status"] = "done"
    MeetingStore.save(meta)
    return notes


class MeetingDetector(threading.Thread):
    """Notices when a call app (Zoom, Teams, a browser with Google Meet, ...) keeps the microphone open."""
    POLL, MIN_ON, MIN_OFF = 3.0, 8.0, 20.0

    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.current = {}          # key -> label, apps holding the mic right now
        self.first_seen, self.gone_since = {}, {}
        self.announced = set()
        self.self_exe = os.path.abspath(sys.executable).lower()

    def run(self):
        while True:
            time.sleep(self.POLL)
            cfg = self.app.cfg
            if not cfg.get("notes_enabled", True):
                continue
            try:
                app = self.app
                busy = bool(app.meeting or app.recorder.stream is not None or app.controller.state != IDLE)
                found = dict(plat.active_meetings(self.self_exe, busy))
            except Exception as e:
                log(f"meeting detection: {e!r}")
                continue
            now = time.monotonic()
            self.current = found
            for key, label in found.items():
                self.gone_since.pop(key, None)
                self.first_seen.setdefault(key, now)
                if key not in self.announced and now - self.first_seen[key] >= self.MIN_ON:
                    self.announced.add(key)
                    self.app.ui_q.put(("meeting_detected", label, key))
            for key in list(self.first_seen):
                if key in found:
                    continue
                self.gone_since.setdefault(key, now)
                if now - self.gone_since[key] >= self.MIN_OFF:
                    self.first_seen.pop(key, None)
                    self.gone_since.pop(key, None)
                    if key in self.announced:
                        self.announced.discard(key)
                        self.app.ui_q.put(("meeting_ended", key))


# --------------------------------------------------------------------------
# Overlay pill
# --------------------------------------------------------------------------
class Overlay:
    W, H = 330, 50
    KEY = plat.OVERLAY_KEY

    def __init__(self, root, recorder, scale, hotkey_text):
        self.root, self.rec, self.s = root, recorder, scale
        self.hotkey_text = hotkey_text
        self.w, self.h = int(self.W * scale), int(self.H * scale)
        self.win = tk.Toplevel(root)
        plat.init_overlay(self.win, self.KEY)
        self.cv = tk.Canvas(self.win, width=self.w, height=self.h, bg=self.KEY, highlightthickness=0)
        self.cv.pack()
        self.mode, self.msg = None, ""
        self.live_text = ""
        self.base_w, self.base_h = self.w, self.h
        self.levels = [0.0] * 9
        self.tick_n = 0
        self.hide_job = None
        self.offscreen()
        make_no_activate(self.win)
        self._animate()

    def offscreen(self):
        plat.hide_overlay(self.win, self.w, self.h)

    def _size(self):
        if self.live_text and self.mode in ("rec", "hands", "busy"):
            l, t, r, b = work_area(self.root)
            return min(int(620 * self.s), r - l - 40), int(104 * self.s)
        return self.base_w, self.base_h

    def place(self):
        self.w, self.h = self._size()
        self.cv.config(width=self.w, height=self.h)
        l, t, r, b = work_area(self.root)
        self.win.geometry(f"{self.w}x{self.h}+{l + (r - l - self.w) // 2}+{b - self.h - int(28 * self.s)}")
        plat.show_overlay(self.win)
        self.win.attributes("-topmost", True)

    def set_live(self, text):
        grow = bool(text) and not self.live_text
        self.live_text = text
        if self.mode in ("rec", "hands", "busy"):
            if grow:
                self.place()
            self.draw()

    def show(self, mode, msg=""):
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None
        if mode in ("rec", "hands") and self.mode not in ("rec", "hands"):
            self.live_text = ""
        if mode not in ("rec", "hands", "busy"):
            self.live_text = ""
        self.mode, self.msg = mode, msg
        self.place()
        self.draw()
        if mode in ("error", "note"):
            self.hide_job = self.root.after(3200, self.hide)

    def hide(self):
        self.mode = None
        self.live_text = ""
        self.offscreen()

    def _pill(self):
        x1, y1, x2, y2 = 1, 1, self.w - 1, self.h - 1
        r = (self.h - 2) // 2 if self.h <= self.base_h else int(18 * self.s)
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        self.cv.create_polygon(pts, smooth=True, fill="#14161c", outline="#3a3f4d")

    def draw(self):
        cv, s = self.cv, self.s
        cv.delete("all")
        if not self.mode:
            return
        self._pill()
        cy = self.base_h // 2 if self.live_text else self.h // 2
        if self.live_text and self.mode in ("rec", "hands", "busy"):
            t = self.live_text
            maxc = 150
            if len(t) > maxc:
                t = "…" + t[-maxc:]
            cv.create_text(int(20 * s), int(46 * s), text=t, fill="#e8edf2", font=(FONT, 11),
                           anchor="nw", width=self.w - int(40 * s))
        if self.mode in ("rec", "hands"):
            on = (self.tick_n // 8) % 2 == 0
            r, cx = int(6 * s), int(20 * s)
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#F42A41" if on else "#a01c2b", outline="")
            label = "শুনছি…" if self.mode == "rec" else "হ্যান্ডস-ফ্রি"
            hint = "ছাড়লে লেখা হবে" if self.mode == "rec" else f"{self.hotkey_text()} চাপলে শেষ"
            cv.create_text(int(34 * s), cy - int(8 * s), text=label, fill="#ffffff", font=(FONT, 10, "bold"), anchor="w")
            cv.create_text(int(34 * s), cy + int(9 * s), text=hint, fill="#8b97a3", font=(FONT, 8), anchor="w")
            bx = self.w - int(22 * s)
            for i, lv in enumerate(reversed(self.levels)):
                bh = max(3 * s, lv * (self.h - 18 * s))
                x = bx - i * int(9 * s)
                cv.create_rectangle(x - int(3 * s), cy - bh / 2, x + int(3 * s), cy + bh / 2, fill="#2bd18f", outline="")
        elif self.mode == "busy":
            dots = "." * (1 + (self.tick_n // 4) % 3)
            cv.create_text(self.w // 2, cy, text=f"লিখছি{dots}", fill="#ffffff", font=(FONT, 10, "bold"))
            if self.live_text:
                cv.delete("all")
                self._pill()
                t = self.live_text if len(self.live_text) <= 150 else "…" + self.live_text[-150:]
                cv.create_text(int(20 * s), cy, text=f"লিখছি{dots}", fill="#2bd18f", font=(FONT, 10, "bold"), anchor="w")
                cv.create_text(int(20 * s), int(46 * s), text=t, fill="#e8edf2", font=(FONT, 11),
                               anchor="nw", width=self.w - int(40 * s))
        else:
            color = "#ffcf5c" if self.mode == "error" else "#ffffff"
            cv.create_text(self.w // 2, cy, text=self.msg, fill=color, font=(FONT, 9, "bold"),
                           width=self.w - int(28 * s), justify="center")

    def _animate(self):
        self.tick_n += 1
        if self.mode in ("rec", "hands"):
            self.levels = self.levels[1:] + [self.rec.level]
            self.draw()
        elif self.mode == "busy" and self.tick_n % 4 == 0:
            self.draw()
        self.root.after(50, self._animate)


# --------------------------------------------------------------------------
# Floating side button, small pop-up cards, Markdown view (meeting notes UI)
# --------------------------------------------------------------------------
def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def _load_logo(size):
    try:
        img = tk.PhotoImage(file=resource("assets/kotha.png"))
        f = max(1, round(max(img.width(), img.height()) / size))
        return img.subsample(f, f) if f > 1 else img
    except Exception:
        return None


class FloatBar:
    """Wispr-style edge handle. Collapsed it is just a thin line on the left edge of the screen. Hover and it opens
    into round buttons: microphone (hands-free dictation), meeting notes, all notes, settings. Drag it up/down."""
    KEY = plat.OVERLAY_KEY

    def __init__(self, app):
        self.app, self.root = app, app.root
        s = self.s = app.scale
        self.cw, self.ch = int(20 * s), int(64 * s)                  # collapsed window
        self.btn, self.gap, self.pad = int(40 * s), int(8 * s), int(7 * s)
        self.rail_w = self.btn + 2 * self.pad
        self.nbtn = 4
        self.eh = self.nbtn * self.btn + (self.nbtn - 1) * self.gap + 2 * self.pad
        self.ew = int(340 * s)                                       # rail + room for the label
        self.win = tk.Toplevel(self.root)
        plat.init_float(self.win, self.KEY)
        self.cv = tk.Canvas(self.win, bg=self.KEY, highlightthickness=0, width=self.cw, height=self.ch)
        self.cv.pack(fill="both", expand=True)
        self.expanded = False
        self.hover = None
        self.enter_t = None
        self.last_inside = 0.0
        self.drag_from, self.dragged = None, False
        self.tick_n = 0
        self.rects = []
        self.cv.bind("<ButtonPress-1>", self._press)
        self.cv.bind("<B1-Motion>", self._motion)
        self.cv.bind("<ButtonRelease-1>", self._release)
        self.place()
        make_no_activate(self.win, click_through=False)
        self.draw()
        self._tick()

    # ---- geometry ----
    def _y_center(self):
        l, t, r, b = work_area(self.root)
        frac = min(0.9, max(0.08, float(self.app.cfg.get("float_y", 0.42))))
        return int(t + frac * (b - t)), t, b

    def place(self):
        yc, t, b = self._y_center()
        x = int(2 * self.s)
        l = work_area(self.root)[0]
        if self.expanded:
            y = max(t + 4, min(yc - self.eh // 2, b - self.eh - 4))
            self.win.geometry(f"{self.ew}x{self.eh}+{l + x}+{y}")
            self.cv.config(width=self.ew, height=self.eh)
        else:
            self.win.geometry(f"{self.cw}x{self.ch}+{l + x}+{yc - self.ch // 2}")
            self.cv.config(width=self.cw, height=self.ch)
        try:
            self.win.attributes("-alpha", 0.98 if self.expanded else 0.82)
        except Exception:
            pass
        self.win.attributes("-topmost", True)

    def anchor(self):
        """Screen point right next to the handle, for pop-up cards."""
        yc, t, b = self._y_center()
        return work_area(self.root)[0] + self.rail_w + int(14 * self.s), yc - self.eh // 2

    def destroy(self):
        try:
            self.win.destroy()
        except Exception:
            pass

    # ---- state ----
    def _state(self):
        m = self.app.meeting
        if m and m.recording:
            return "rec"
        if m:
            return "busy"
        if self.app.detector and self.app.detector.current:
            return "detected"
        return "idle"

    def _dictating(self):
        return self.app.controller.state in (PTT, HANDSFREE, TAP_WAIT)

    # ---- mouse ----
    def _press(self, e):
        self.drag_from = (e.y_root, self.win.winfo_y())
        self.dragged = False

    def _motion(self, e):
        if not self.drag_from:
            return
        dy = e.y_root - self.drag_from[0]
        if abs(dy) > 5:
            self.dragged = True
        if self.dragged:
            l, t, r, b = work_area(self.root)
            h = self.eh if self.expanded else self.ch
            yc = self.drag_from[1] + dy + h / 2
            self.app.cfg["float_y"] = min(0.9, max(0.08, (yc - t) / max(1, b - t)))
            self.place()

    def _release(self, e):
        was_drag = self.dragged
        self.dragged = False
        self.drag_from = None
        if was_drag:
            try:
                self.app.cfg.save()
            except Exception:
                pass
            return
        if not self.expanded:
            self.expanded = True
            self.last_inside = time.monotonic()
            self.place()
            self.draw()

    def _act(self, key):
        def go(_e=None):
            if self.dragged:
                return
            if key == "mic":
                self.app.controller.q.put(("handsfree",))
            elif key == "rec":
                self.app.toggle_meeting()
            elif key == "notes":
                self.app.open_notes()
            elif key == "settings":
                self.app.open_settings()
            self.root.after(150, self.draw)
        return go

    def _set_hover(self, key):
        def go(_e=None):
            self.hover = key
            self.draw()
        return go

    # ---- drawing ----
    def _icon(self, kind, cx, cy, col):
        cv, k = self.cv, self.s
        if kind == "mic":
            _round_rect(cv, cx - 3.6 * k, cy - 9.5 * k, cx + 3.6 * k, cy + 2.5 * k, 3.6 * k, fill=col, outline="")
            cv.create_arc(cx - 7.2 * k, cy - 6.5 * k, cx + 7.2 * k, cy + 7.5 * k, start=200, extent=140, style="arc",
                          outline=col, width=max(1.5, 1.6 * k))
            cv.create_line(cx, cy + 7.5 * k, cx, cy + 10.5 * k, fill=col, width=max(1.5, 1.6 * k), capstyle="round")
            cv.create_line(cx - 4 * k, cy + 10.5 * k, cx + 4 * k, cy + 10.5 * k, fill=col, width=max(1.5, 1.6 * k),
                           capstyle="round")
        elif kind == "rec":
            cv.create_oval(cx - 6.5 * k, cy - 6.5 * k, cx + 6.5 * k, cy + 6.5 * k, fill=col, outline="")
        elif kind == "stop":
            _round_rect(cv, cx - 6 * k, cy - 6 * k, cx + 6 * k, cy + 6 * k, 2 * k, fill=col, outline="")
        elif kind == "notes":
            w = max(1.6, 1.8 * k)
            for dy, half in ((-5.5, 8), (0, 8), (5.5, 5)):
                cv.create_line(cx - 8 * k, cy + dy * k, cx - 8 * k + 2 * half * k, cy + dy * k, fill=col, width=w,
                               capstyle="round")
        elif kind == "gear":
            cv.create_text(cx, cy - 1, text="⚙", fill=col, font=(plat.SYMBOL_FONT, int(14 * (k if k < 1.6 else 1.6))))

    def _label(self, y_center, title, sub=None, keys=None, tone="#ffffff"):
        cv, s = self.cv, self.s
        x1 = self.rail_w + int(10 * s)
        pad = int(12 * s)
        t = cv.create_text(x1 + pad, y_center, text=title, anchor="w", fill=tone, font=(FONT, 10, "bold"))
        bb = list(cv.bbox(t))
        extra = []
        if sub:
            cv.coords(t, x1 + pad, y_center - int(9 * s))
            u = cv.create_text(x1 + pad, y_center + int(9 * s), text=sub, anchor="w", fill="#9aa3b2", font=(FONT, 9))
            bb = [min(bb[0], cv.bbox(u)[0]), cv.bbox(t)[1], max(cv.bbox(t)[2], cv.bbox(u)[2]), cv.bbox(u)[3]]
            extra.append(u)
        right = bb[2]
        if keys:
            k = cv.create_text(right + int(10 * s), cv.bbox(t)[1] + (cv.bbox(t)[3] - cv.bbox(t)[1]) // 2,
                               text=keys, anchor="w", fill="#e8ecf4", font=(FONT, 9, "bold"))
            kb = cv.bbox(k)
            _round_rect(cv, kb[0] - int(7 * s), kb[1] - int(3 * s), kb[2] + int(7 * s), kb[3] + int(3 * s), int(8 * s),
                        fill="#2b3040", outline="")
            cv.tag_raise(k)
            right = kb[2] + int(7 * s)
        top, bot = bb[1] - int(9 * s), bb[3] + int(9 * s)
        bg = _round_rect(cv, x1, top, right + pad, bot, int(14 * s), fill="#171a21", outline="#343a49")
        cv.tag_lower(bg)
        for item in cv.find_all():
            pass
        self.rects.append((x1, top, right + pad, bot))

    def draw(self):
        cv, s = self.cv, self.s
        cv.delete("all")
        self.rects = []
        state = self._state()
        pulse = (self.tick_n // 4) % 2 == 0
        if not self.expanded:
            cx, cy = self.cw // 2, self.ch // 2
            hw, hh = int(3.5 * s), int(22 * s)
            col = {"rec": "#ef4444" if pulse else "#b91c1c", "busy": "#8b5cf6",
                   "detected": "#f59e0b" if pulse else "#b8791a"}.get(state, "#a7adb8")
            if self._dictating():
                col = "#22c55e"
            _round_rect(cv, cx - hw, cy - hh, cx + hw, cy + hh, hw, fill=col, outline="")
            self.rects = [(0, 0, self.cw, self.ch)]
            return
        pad, btn, gap = self.pad, self.btn, self.gap
        _round_rect(cv, 1, 1, self.rail_w, self.eh - 1, self.rail_w // 2, fill="#14161c", outline="#343a49")
        keys_dict = hotkey_label(self.app.cfg["hotkey"])
        note_keys = hotkey_label(self.app.cfg.get("note_hotkey", DEFAULT_NOTE_HOTKEY))
        items = [("mic", "mic"), ("rec", "stop" if state == "rec" else "rec"), ("notes", "notes"), ("settings", "gear")]
        self.rects.append((0, 0, self.rail_w + 6, self.eh))
        for i, (key, icon) in enumerate(items):
            cx = pad + btn // 2 + 1
            cy = pad + i * (btn + gap) + btn // 2
            hov = self.hover == key
            fill = "#262a35"
            ic = "#e8ecf4"
            ring = ""
            if key == "mic":
                if self._dictating():
                    fill, ic = "#16a34a", "#ffffff"
            if key == "rec":
                if state == "rec":
                    fill, ic = ("#ef4444" if pulse else "#dc2626"), "#ffffff"
                elif state == "busy":
                    fill, ic = "#3a3560", "#c4b5fd"
                elif state == "detected":
                    ring = "#f59e0b" if pulse else "#7c5412"
                    ic = "#f87171"
                else:
                    ic = "#f87171"
            if hov and fill == "#262a35":
                fill = "#343a49"
            r = btn // 2
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill, outline=ring, width=int(2 * s) if ring else 0,
                           tags=("b_" + key,))
            self._icon(icon, cx, cy, ic)
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill="", outline="", tags=("b_" + key,))
            cv.tag_bind("b_" + key, "<ButtonRelease-1>", self._act(key))
            cv.tag_bind("b_" + key, "<Enter>", self._set_hover(key))
            cv.tag_bind("b_" + key, "<Leave>", self._set_hover(None))
            if hov:
                cv.config(cursor="hand2")
                if key == "mic":
                    self._label(cy, "কথা বলে লিখুন", "চেপে ধরুন · ক্লিকে হ্যান্ডস-ফ্রি", keys_dict)
                elif key == "rec":
                    if state == "rec":
                        self._label(cy, f"নোট শেষ করুন   {_fmt_clock(self.app.meeting.elapsed())}", "ক্লিক করলে সারাংশ তৈরি হবে",
                                    tone="#fecaca")
                    elif state == "busy":
                        self._label(cy, "নোট তৈরি হচ্ছে…", "ক্লিক করে দেখুন")
                    elif state == "detected":
                        names = ", ".join(self.app.detector.current)
                        self._label(cy, f"{names} এ মিটিং চলছে", "ক্লিক করে নোট নেওয়া শুরু করুন", note_keys, "#fde68a")
                    else:
                        self._label(cy, "মিটিং নোট শুরু করুন", "এখন কোনো মিটিং চলছে না", note_keys)
                elif key == "notes":
                    self._label(cy, "সব মিটিং নোট", "নোট, সারাংশ, ট্রান্সক্রিপ্ট")
                else:
                    self._label(cy, "Settings", f"Kotha {VERSION}")
        if self.hover is None:
            cv.config(cursor="")

    def _tick(self):
        try:
            self.tick_n += 1
            px, py = self.root.winfo_pointerxy()
            x, y = self.win.winfo_x(), self.win.winfo_y()
            inside = any(x + a <= px < x + c and y + b <= py < y + d for a, b, c, d in self.rects) or \
                (self.drag_from is not None)
            now = time.monotonic()
            if inside:
                self.last_inside = now
                if self.enter_t is None:
                    self.enter_t = now
                if not self.expanded and not self.drag_from and now - self.enter_t >= 0.12:
                    self.expanded = True
                    self.place()
                    self.draw()
            else:
                self.enter_t = None
                if self.expanded and now - self.last_inside > 0.45:
                    self.expanded = False
                    self.hover = None
                    self.place()
                    self.draw()
            if self._state() != "idle" or self._dictating() or (self.expanded and self.hover == "rec"):
                if self.tick_n % 3 == 0:
                    self.draw()
            if self.tick_n % 40 == 0:
                self.win.attributes("-topmost", True)
        except Exception as e:
            log(f"float bar: {e!r}")
        self.root.after(120, self._tick)


class Card:
    """Small clickable pop-up that never steals focus (meeting suggestion, 'notes ready')."""
    KEY = plat.OVERLAY_KEY

    def __init__(self, app, title, body, buttons, timeout=30):
        self.app, s = app, app.scale
        self.win = tk.Toplevel(app.root)
        plat.init_float(self.win, self.KEY)
        w = int(360 * s)
        cv = tk.Canvas(self.win, bg=self.KEY, highlightthickness=0, width=w, height=int(200 * s))
        cv.pack()
        pad = int(16 * s)
        t1 = cv.create_text(pad, pad, text=title, anchor="nw", fill="#ffffff", font=(FONT, 11, "bold"), width=w - 2 * pad)
        y = cv.bbox(t1)[3] + int(6 * s)
        t2 = cv.create_text(pad, y, text=body, anchor="nw", fill="#c9d1d9", font=(FONT, 9), width=w - 2 * pad)
        y = cv.bbox(t2)[3] + int(14 * s)
        bh = int(32 * s)
        x = pad
        items = []
        for i, (label, fn) in enumerate(buttons):
            tag = f"b{i}"
            t = cv.create_text(0, 0, text=label, font=(FONT, 9, "bold"), fill="#ffffff", tags=tag)
            tw = cv.bbox(t)[2] - cv.bbox(t)[0]
            bw = tw + int(28 * s)
            r = _round_rect(cv, x, y, x + bw, y + bh, int(10 * s), fill="#8b5cf6" if i == 0 else "#2a2e3a",
                            outline="", tags=tag)
            cv.coords(t, x + bw / 2, y + bh / 2)
            cv.tag_raise(t)
            cv.tag_bind(tag, "<ButtonRelease-1>", self._click(fn))
            cv.tag_bind(tag, "<Enter>", lambda e: cv.config(cursor="hand2"))
            cv.tag_bind(tag, "<Leave>", lambda e: cv.config(cursor=""))
            x += bw + int(8 * s)
            items.append(r)
        h = y + bh + pad
        bg = _round_rect(cv, 1, 1, w - 1, h - 1, int(16 * s), fill="#14161c", outline="#2f3442")
        cv.tag_lower(bg)
        cv.config(height=h)
        l, t, r, b = work_area(app.root)
        if app.float_bar:
            ax, ay = app.float_bar.anchor()
            ay = max(t + 4, min(ay, b - h - 4))
        else:
            ax, ay = r - w - int(20 * s), b - h - int(20 * s)
        self.win.geometry(f"{w}x{h}+{ax}+{ay}")
        make_no_activate(self.win, click_through=False)
        self.job = app.root.after(int(timeout * 1000), self.close) if timeout else None

    def _click(self, fn):
        def go(_e=None):
            self.close()
            if fn:
                fn()
        return go

    def close(self):
        try:
            if self.job:
                self.app.root.after_cancel(self.job)
            self.win.destroy()
        except Exception:
            pass

    def alive(self):
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False


def render_markdown(txt, md):
    """Very small Markdown view: headings, bullets and checkboxes."""
    txt.config(state="normal")
    txt.delete("1.0", "end")
    prev_blank, prev_head = True, False
    for line in (md or "").splitlines():
        s = line.rstrip()
        if not s.strip():
            if prev_blank or prev_head:
                continue                     # headings already have their own spacing
            prev_blank = True
            txt.insert("end", "\n")
            continue
        prev_blank, prev_head = False, s.startswith("#")
        tag = None
        if s.startswith("### "):
            s, tag = s[4:], "h3"
        elif s.startswith("## "):
            s, tag = s[3:], "h2"
        elif s.startswith("# "):
            s, tag = s[2:], "h1"
        else:
            st = s.lstrip()
            ind = len(s) - len(st)
            if st.startswith("- [ ] ") or st.startswith("* [ ] "):
                s = " " * ind + "☐ " + st[6:]
            elif st.startswith("- [x] ") or st.startswith("- [X] "):
                s = " " * ind + "☑ " + st[6:]
            elif st.startswith("- ") or st.startswith("* "):
                s = " " * ind + "•  " + st[2:]
        s = s.replace("**", "")
        if tag:
            txt.insert("end", s + "\n", tag)
        else:
            txt.insert("end", s + "\n")
    txt.config(state="disabled")


STATUS_BN = {"recording": "● রেকর্ড হচ্ছে", "processing": "লেখা হচ্ছে…", "done": "তৈরি",
             "partial": "কিছু অংশ বাকি", "failed": "সমস্যা হয়েছে", "summary_failed": "সারাংশ বাকি",
             "interrupted": "অসমাপ্ত", "empty": "কোনো কথা পাওয়া যায়নি"}


# --------------------------------------------------------------------------
# App + windows
# --------------------------------------------------------------------------
PROVIDERS = [
    ("gemini_live", "Gemini Live: রিয়েল-টাইম, কথা বলার সাথে সাথে লেখা (ফ্রি key)"),
    ("gemini_tx", "Gemini Transcribe: কথা শেষে একবারে লেখা (ফ্রি key)"),
    ("gemini", "Gemini Flash: লেখার ধরন নিজে ঠিক করা যায় (ফ্রি key)"),
    ("groq", "Groq Whisper (ফ্রি key)"),
    ("openai", "OpenAI (পেইড)"),
    ("local", "Offline Whisper: ইন্টারনেট লাগে না, তবে ধীর"
              + ("" if LOCAL_AVAILABLE else " (Kotha Offline লাগবে)")),
]


class App:
    def __init__(self):
        self.cfg = Config()
        self.history = History()
        self.recorder = Recorder()
        self.ui_q = queue.Queue()
        global FONT
        if ui.load_fonts(resource):
            FONT = ui.FAMILY
        else:
            ui.fallback_family(FONT)
        ctk.set_appearance_mode("dark")
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title(APP)
        plat.set_window_icon(self.root, resource)
        self.scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        style = ttk.Style(self.root)
        plat.style_theme(style)
        style.configure(".", font=(FONT, 10))
        style.configure("Title.TLabel", font=(FONT, 12, "bold"))
        style.configure("Hint.TLabel", foreground="#666666", font=(FONT, 9))
        style.configure("Big.TLabel", font=(FONT, 13, "bold"), foreground="#006A4E")
        style.configure("Credit.TLabel", foreground="#666666", font=(FONT, 9))
        style.configure("Link.TLabel", foreground="#007a4d", font=(FONT, 9, "bold"))
        style.configure("LinkHover.TLabel", foreground="#005c3a", font=(FONT, 9, "bold", "underline"))
        style.configure("BigLink.TLabel", foreground="#007a4d", font=(FONT, 12, "bold"))
        style.configure("BigLinkHover.TLabel", foreground="#005c3a", font=(FONT, 12, "bold", "underline"))
        self.overlay = Overlay(self.root, self.recorder, self.scale, lambda: hotkey_label(self.cfg["hotkey"]))
        self.controller = Controller(self)
        self.hook = HotkeyHook(self.controller.q, self.ui_q, self.cfg["hotkey"])
        self.settings_win = self.history_win = self.popup_win = None
        self.capture_cb = None
        self.meeting = None            # running MeetingSession
        self.detector = None
        self.float_bar = None
        self.card = None
        self.notes_win = None

    # ---- tray ----
    def start_tray(self):
        img = Image.open(resource("assets/kotha.png"))
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: f"{APP_BN} চালু আছে  ({hotkey_label(self.cfg['hotkey'])})", None, enabled=False),
            pystray.MenuItem("Settings", lambda: self.ui_q.put(("settings",)), default=True),
            pystray.MenuItem("History", lambda: self.ui_q.put(("history",))),
            pystray.MenuItem("শেষ টেক্সট আবার পেস্ট করো", lambda: self.controller.q.put(("paste_last",))),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: "মিটিং নোট শেষ করো" if self.meeting and self.meeting.recording
                             else "মিটিং নোট শুরু করো", lambda: self.ui_q.put(("note_toggle", False)),
                             visible=lambda _: MEETING_SUPPORTED and self.cfg.get("notes_enabled", True)),
            pystray.MenuItem("মিটিং নোটগুলো", lambda: self.ui_q.put(("notes",)),
                             visible=lambda _: MEETING_SUPPORTED),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("বন্ধ করো (Quit)", lambda: self.ui_q.put(("quit",))),
        )
        self.tray = pystray.Icon(APP, img, f"{APP} ({APP_BN})", menu)
        self.tray.run_detached()

    def pump(self):
        try:
            while True:
                msg = self.ui_q.get_nowait()
                kind = msg[0]
                if kind == "show":
                    self.overlay.show(msg[1], msg[2] if len(msg) > 2 else "")
                elif kind == "live":
                    self.overlay.set_live(msg[1])
                elif kind == "hide":
                    self.overlay.hide()
                elif kind == "popup":
                    self.open_popup(msg[1])
                elif kind == "settings":
                    self.open_settings()
                elif kind == "history":
                    self.open_history()
                elif kind == "captured":
                    if self.capture_cb:
                        self.capture_cb(msg[1])
                elif kind == "note_toggle":
                    if msg[1]:
                        send_mask_key()
                    self.toggle_meeting()
                elif kind == "notes":
                    self.open_notes()
                elif kind == "meeting_detected":
                    self.on_meeting_detected(msg[1], msg[2])
                elif kind == "meeting_ended":
                    self.on_meeting_ended(msg[1])
                elif kind == "meeting_update":
                    self.refresh_meeting_ui(msg[1])
                elif kind == "meeting_done":
                    self.on_meeting_done(msg[1])
                elif kind == "meeting_stop_request":
                    self.stop_meeting()
                elif kind == "quit":
                    self.quit()
        except queue.Empty:
            pass
        self.root.after(25, self.pump)

    def quit(self):
        if self.meeting and self.meeting.recording:
            if not messagebox.askyesno(APP, "মিটিং নোট নেওয়া চলছে। এখন বন্ধ করলে রেকর্ডিং থেমে যাবে; "
                                            "পরে 'মিটিং নোটগুলো' থেকে প্রসেস করা যাবে। বন্ধ করবো?"):
                return
            self.meeting.abort()
        try:
            self.tray.stop()
        except Exception:
            pass
        self.root.destroy()
        os._exit(0)

    # ---- meeting notes ----
    def notes_keys_ready(self):
        c = self.cfg
        return bool(c.get("gemini_api_key", "").strip() or c.get("groq_api_key", "").strip()
                    or c.get("openai_api_key", "").strip() or (c["provider"] == "local" and LOCAL_AVAILABLE))

    def toggle_meeting(self):
        if self.meeting and self.meeting.recording:
            self.stop_meeting()
        else:
            self.start_meeting()

    def start_meeting(self, label="", auto_key=None):
        if not MEETING_SUPPORTED or not self.cfg.get("notes_enabled", True):
            return
        if self.meeting and self.meeting.recording:
            return
        if self.meeting:
            self.overlay.show("note", "আগের মিটিংয়ের নোট এখনো তৈরি হচ্ছে, একটু পরে আবার চেষ্টা করো")
            return
        if not self.notes_keys_ready():
            self.overlay.show("error", "আগে Settings এ ফ্রি Gemini key দাও")
            self.open_settings()
            return
        if not label and self.detector and self.detector.current:
            label = auto_key = next(iter(self.detector.current))
        try:
            self.meeting = MeetingSession(self, label, auto_key)
        except Exception as e:
            log(f"meeting start failed: {e!r}")
            self.meeting = None
            self.overlay.show("error", "মিটিং রেকর্ড শুরু করা যায়নি")
            return
        if self.card and self.card.alive():
            self.card.close()
        self.overlay.show("note", "মিটিং নোট নেওয়া শুরু হয়েছে")
        self.refresh_meeting_ui()
        self.root.after(6000, self._check_capture)

    def _check_capture(self):
        m = self.meeting
        if not m or not m.recording:
            return
        errs = m.recorder.errors
        if errs.get("sys") and plat.IS_MAC:
            self.overlay.show("error", "অন্যদের কণ্ঠ শোনা যাচ্ছে না। System Settings > Privacy > Screen Recording এ "
                                       "Kotha চালু করে Kotha আবার খুলুন")
            plat.open_privacy_pane("screen")
        elif errs.get("sys"):
            log(f"meeting: no system audio: {errs['sys']}")
        if errs.get("mic"):
            self.overlay.show("error", "মাইক্রোফোন চালু করা যাচ্ছে না, মাইকের অনুমতি দেখুন")

    def stop_meeting(self):
        if self.meeting and self.meeting.recording:
            self.meeting.stop()
            self.overlay.show("note", "মিটিং শেষ। নোট আর সারাংশ তৈরি হচ্ছে…")
            self.refresh_meeting_ui()

    def refresh_meeting_ui(self, mid=None):
        if self.float_bar:
            self.float_bar.draw()
        if self.notes_win and self.notes_win.winfo_exists():
            self._notes_reload(keep=True, live_mid=mid)

    def on_meeting_detected(self, label, key):
        mode = self.cfg.get("meeting_detect", "ask")
        if not self.cfg.get("notes_enabled", True) or mode == "off" or (self.meeting and self.meeting.recording):
            return
        if mode == "auto":
            self.start_meeting(label, key)
            return
        if self.card and self.card.alive():
            self.card.close()
        self.card = Card(self, f"{label} এ মিটিং চলছে মনে হচ্ছে",
                         "নোট নেবো? আপনার আর অন্যদের সবার কথা লিখে রাখবে, শেষে মূল পয়েন্টসহ সারাংশ বানিয়ে দেবে। "
                         "রেকর্ড করার আগে সবাইকে জানিয়ে রাখুন।",
                         [("হ্যাঁ, নোট নাও", lambda: self.start_meeting(label, key)), ("এখন না", None)], timeout=45)
        self.card_key = key

    def on_meeting_ended(self, key):
        if self.card and self.card.alive() and getattr(self, "card_key", None) == key:
            self.card.close()
        m = self.meeting
        if m and m.recording and m.auto_key == key and self.cfg.get("meeting_autostop", True):
            self.stop_meeting()

    def on_meeting_done(self, mid):
        if self.meeting and self.meeting.id == mid and not self.meeting.recording:
            self.meeting = None
        meta = MeetingStore.load(mid) or {}
        self.refresh_meeting_ui()
        st = meta.get("status")
        if st == "empty":
            self.overlay.show("note", "মিটিংয়ে কোনো কথা পাওয়া যায়নি")
            return
        title = "মিটিং নোট তৈরি হয়েছে ✓" if st in ("done", "partial") else "মিটিং নোটে সমস্যা হয়েছে"
        body = meta.get("title", "") if st in ("done", "partial") else (meta.get("error") or "বিস্তারিত দেখতে খুলুন")
        if self.card and self.card.alive():
            self.card.close()
        self.card = Card(self, title, body, [("খুলুন", lambda: self.open_notes(mid)), ("বন্ধ", None)], timeout=60)

    def open_notes(self, select=None):
        if self.notes_win and self.notes_win.winfo_exists():
            self._show_window(self.notes_win)
            self._notes_reload(keep=select is None, select=select)
            return
        C = ui.C
        w = self._new_window(f"{APP} · মিটিং নোট", 1180, 760)
        w.minsize(860, 520)
        self.notes_win = w
        self._center(w)
        self.n_tab = "notes"
        self.n_ids, self.n_cards, self.n_sel = [], {}, None
        w.grid_columnconfigure(1, weight=1)
        w.grid_rowconfigure(0, weight=1)

        # ---------- left: meetings ----------
        left = ctk.CTkFrame(w, fg_color="transparent", width=330)
        left.grid(row=0, column=0, sticky="ns", padx=(14, 6), pady=14)
        left.grid_propagate(False)
        left.pack_propagate(False)
        top = ctk.CTkFrame(left, fg_color="transparent")
        top.pack(fill="x", padx=6, pady=(4, 12))
        try:
            self._logo30 = ui.image(resource("assets/kotha.png"), (32, 32))
            ctk.CTkLabel(top, image=self._logo30, text="").pack(side="left")
        except Exception:
            pass
        tt = ctk.CTkFrame(top, fg_color="transparent")
        tt.pack(side="left", padx=10)
        ui.label(tt, "মিটিং নোট", 20, "semibold").pack(anchor="w")
        ui.label(tt, "Kotha · AI note taker", 11, color=C["faint"]).pack(anchor="w")
        self.n_btn = ui.button(left, "", self.toggle_meeting, height=46)
        self.n_btn.pack(fill="x", padx=6)
        ui.label(left, f"শর্টকাট  {hotkey_label(self.cfg.get('note_hotkey', DEFAULT_NOTE_HOTKEY))}", 11,
                 color=C["faint"]).pack(anchor="w", padx=10, pady=(6, 10))
        self.n_search = ctk.CTkEntry(left, height=38, corner_radius=10, fg_color=ui.C["surface2"],
                                     border_color=ui.C["border"], border_width=1, text_color=ui.C["text"],
                                     font=ui.font(13), placeholder_text="🔍   নোট খুঁজুন…",
                                     placeholder_text_color=ui.C["faint"])
        self.n_search.pack(fill="x", padx=6, pady=(0, 8))
        self.n_search.bind("<KeyRelease>", lambda e: self._notes_reload(keep=True))
        self.n_listf = ctk.CTkScrollableFrame(left, fg_color="transparent", scrollbar_button_color=C["surface3"],
                                              scrollbar_button_hover_color=C["border"])
        self.n_listf.pack(fill="both", expand=True)

        # ---------- right: one meeting ----------
        right = ctk.CTkFrame(w, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 16), pady=14)
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=1)
        self.n_banner = ctk.CTkFrame(right, fg_color="transparent", height=1)
        self.n_banner.grid(row=0, column=0, sticky="ew")
        self.n_banner.grid_remove()
        head = ctk.CTkFrame(right, fg_color="transparent")
        head.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.n_title = ui.label(head, "", 25, "semibold", wrap=720)
        self.n_title.pack(anchor="w")
        self.n_chips = ctk.CTkFrame(head, fg_color="transparent")
        self.n_chips.pack(anchor="w", pady=(6, 10))
        bar = ctk.CTkFrame(right, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.n_seg = ctk.CTkSegmentedButton(bar, values=["নোট ও সারাংশ", "পুরো ট্রান্সক্রিপ্ট"],
                                            command=lambda v: self._notes_tab("notes" if v.startswith("নোট") else "trans"),
                                            font=ui.font(13, "medium"), height=36, corner_radius=10,
                                            fg_color=C["surface2"], selected_color=C["accent"],
                                            selected_hover_color=C["accent_hover"], unselected_color=C["surface2"],
                                            unselected_hover_color=C["surface3"], text_color=C["text"])
        self.n_seg.set("নোট ও সারাংশ")
        self.n_seg.pack(side="left")
        self.n_tools = ctk.CTkFrame(bar, fg_color="transparent")
        self.n_tools.pack(side="right")
        ui.button(self.n_tools, "⧉  কপি", self._notes_copy_current, kind="ghost", height=34).pack(side="left", padx=3)
        ui.button(self.n_tools, "⤓  সেভ", self._notes_export, kind="ghost", height=34).pack(side="left", padx=3)
        ui.button(self.n_tools, "↻  নতুন সারাংশ", self._notes_resummarize, kind="ghost", height=34).pack(side="left", padx=3)
        ui.button(self.n_tools, "📁", self._notes_folder, kind="ghost", width=40, height=34).pack(side="left", padx=3)
        ui.button(self.n_tools, "🗑", self._notes_delete, kind="danger", width=40, height=34).pack(side="left", padx=3)
        body = ui.card(right)
        body.grid(row=3, column=0, sticky="nsew")
        self.n_box = ctk.CTkTextbox(body, font=ui.font(15), fg_color="transparent", text_color=C["text"], wrap="word",
                                    scrollbar_button_color=C["surface3"], border_spacing=14)
        self.n_box.pack(fill="both", expand=True, padx=4, pady=4)
        self._notes_tags()
        self._notes_reload(select=select)
        self._notes_tick()
        self._show_window(w)

    def _notes_tags(self):
        t = self.n_box._textbox
        try:
            sc = ctk.ScalingTracker.get_widget_scaling(self.n_box)
        except Exception:
            sc = 1.0

        def f(size, weight="regular"):
            return (ui.family(weight), -int(size * sc), "bold" if weight == "bold" else "normal")
        C = ui.C
        t.tag_configure("h1", font=f(23, "semibold"), foreground=C["text"], spacing3=8)
        t.tag_configure("h2", font=f(17, "semibold"), foreground=C["accent_text"], spacing1=16, spacing3=6)
        t.tag_configure("h3", font=f(15, "semibold"), foreground=C["text"], spacing1=8, spacing3=2)
        t.tag_configure("time", font=f(12), foreground=C["faint"])
        t.tag_configure("me", font=f(15, "semibold"), foreground=C["accent_text"])
        for i, col in enumerate(["#86efac", "#fcd34d", "#93c5fd", "#f9a8d4", "#5eead4"]):
            t.tag_configure(f"sp{i}", font=f(15, "semibold"), foreground=col)
        t.tag_configure("muted", foreground=C["muted"])
        t.configure(spacing1=3, spacing3=3)

    def _notes_render(self, mid, meta, live):
        t = self.n_box._textbox
        t.configure(state="normal")
        y = t.yview()[1]
        if self.n_tab == "notes":
            notes = MeetingStore.read_text(mid, "notes.md")
            if not notes:
                notes = ("# নোট তৈরি হচ্ছে…\n\nমিটিং চলাকালীন প্রতি মিনিটে কথাগুলো লেখা হচ্ছে ('পুরো ট্রান্সক্রিপ্ট' দেখুন)। "
                         "মিটিং শেষ করলে এখানে সারাংশ, মূল পয়েন্ট, সিদ্ধান্ত আর করণীয় আসবে।") if live else \
                        "# এখনো সারাংশ নেই\n\n'↻ নতুন সারাংশ' চাপলে ট্রান্সক্রিপ্ট থেকে নোট বানানো হবে।"
            lines = notes.strip().splitlines()
            if lines and lines[0].startswith("# ") and not live:
                lines = lines[1:]
            render_markdown(t, "\n".join(lines).strip())
        else:
            t.delete("1.0", "end")
            colors = {}
            text = MeetingStore.read_text(mid, "transcript.md")
            if not text:
                t.insert("end", "প্রথম মিনিটের পর থেকে এখানে কথা দেখা যাবে…" if live else "কোনো কথা লেখা হয়নি।", "muted")
            for line in text.splitlines():
                m = line.split("] ", 1)
                if line.startswith("[") and len(m) == 2:
                    t.insert("end", m[0] + "]  ", "time")
                    sp, _, said = m[1].partition(": ")
                    if said:
                        if sp == ME_LABEL:
                            tag = "me"
                        else:
                            tag = colors.setdefault(sp, f"sp{len(colors) % 5}")
                        t.insert("end", sp, tag)
                        t.insert("end", "  " + said + "\n")
                    else:
                        t.insert("end", m[1] + "\n", "muted")
                else:
                    t.insert("end", line + "\n")
            if live and y > 0.97:
                t.see("end")
        t.configure(state="disabled")

    def _notes_tab(self, which):
        self.n_tab = which
        self._notes_show()

    def _notes_sel(self):
        return self.n_sel

    def _notes_reload(self, keep=True, select=None, live_mid=None):
        if not (self.notes_win and self.notes_win.winfo_exists()):
            return
        C = ui.C
        cur = select or (self.n_sel if keep else None)
        q = self.n_search.get().strip().lower() if getattr(self, "n_search", None) else ""
        metas = [m for m in MeetingStore.list()
                 if not q or q in (m.get("title", "") + m.get("app", "") + m.get("started", "")).lower()]
        for wd in self.n_listf.winfo_children():
            wd.destroy()
        self.n_ids = [m["id"] for m in metas]
        self.n_cards = {}
        for m in metas:
            live = self.meeting and self.meeting.id == m["id"]
            st = "recording" if live and self.meeting.recording else ("processing" if live else m.get("status"))
            c = ctk.CTkFrame(self.n_listf, fg_color="transparent", corner_radius=12, border_width=1,
                             border_color=C["glass"] if ui.GLASS_OK else C["bg"])
            c.pack(fill="x", pady=3, padx=2)
            a = ui.label(c, m.get("title", "মিটিং")[:70], 14, "medium", wrap=270)
            a.pack(anchor="w", padx=12, pady=(10, 2))
            row = ctk.CTkFrame(c, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=(0, 10))
            b = ui.label(row, f"{m.get('started', '')[5:]}  ·  {_fmt_clock(m.get('duration', 0))}", 11, color=C["faint"])
            b.pack(side="left")
            tone = {"recording": "red", "processing": "accent", "done": "green", "partial": "amber",
                    "failed": "red", "summary_failed": "amber", "interrupted": "amber"}.get(st, "neutral")
            ch = ui.chip(row, STATUS_BN.get(st, st or ""), tone)
            ch.pack(side="right")
            for wd in (c, a, b, row, ch):
                wd.bind("<Button-1>", lambda e, mid=m["id"]: self._notes_pick(mid))
            self.n_cards[m["id"]] = c
        if not metas:
            self.n_sel = None
            self._notes_empty(bool(q))
            return
        if cur not in self.n_ids:
            cur = self.n_ids[0]
        self._notes_pick(cur, render=(live_mid is None or live_mid == cur))

    def _notes_pick(self, mid, render=True):
        C = ui.C
        self.n_sel = mid
        for k, c in self.n_cards.items():
            sel = k == mid
            c.configure(fg_color=C["surface2"] if sel else "transparent",
                        border_color=C["accent"] if sel else (C["glass"] if ui.GLASS_OK else C["bg"]))
        if render:
            self._notes_show()

    def _notes_empty(self, searching):
        for wd in self.n_banner.winfo_children():
            wd.destroy()
        self.n_banner.grid_remove()
        for wd in self.n_chips.winfo_children():
            wd.destroy()
        self.n_title.configure(text="কোনো নোট পাওয়া যায়নি" if searching else "এখনো কোনো মিটিং নোট নেই")
        t = self.n_box._textbox
        t.configure(state="normal")
        render_markdown(t, "" if searching else
                        "## কীভাবে শুরু করবেন\n"
                        "- Google Meet, Zoom, Teams বা যেকোনো কলে ঢুকুন। Kotha জিজ্ঞেস করবে \"নোট নেবো?\"\n"
                        f"- অথবা বাঁ পাশের বাটন বা {hotkey_label(self.cfg.get('note_hotkey', DEFAULT_NOTE_HOTKEY))} চাপুন।\n"
                        "- মিটিং শেষে আবার চাপুন। সারাংশ, মূল পয়েন্ট, সিদ্ধান্ত আর করণীয় এখানে চলে আসবে।\n\n"
                        "## যা শোনে\n- আপনার মাইক (আমি)\n- কম্পিউটারের সাউন্ড, মানে মিটিংয়ের অন্যরা (Speaker 1, 2…)\n")
        t.configure(state="disabled")

    def _notes_show(self):
        mid = self.n_sel
        if not mid:
            return
        C = ui.C
        meta = MeetingStore.load(mid) or {}
        live = bool(self.meeting and self.meeting.id == mid)
        rec = live and self.meeting.recording
        st = "recording" if rec else ("processing" if live else meta.get("status"))
        self.n_title.configure(text=meta.get("title", "মিটিং"))
        for wd in self.n_chips.winfo_children():
            wd.destroy()
        self.n_dur_chip = ui.chip(self.n_chips, "⏱  " + _fmt_clock(self.meeting.elapsed() if live else meta.get("duration", 0)))
        for text, tone in ((f"📅  {meta.get('started', '')}", "neutral"), (None, None),
                           (meta.get("app") or "মিটিং", "accent"),
                           (STATUS_BN.get(st, st or ""), {"recording": "red", "processing": "accent", "done": "green"}.get(st, "amber"))):
            if text is None:
                self.n_dur_chip.pack(side="left", padx=(0, 6))
                continue
            ui.chip(self.n_chips, text, tone).pack(side="left", padx=(0, 6))
        for wd in self.n_banner.winfo_children():
            wd.destroy()
        show_banner = rec or live or meta.get("status") in ("interrupted", "partial", "failed", "summary_failed")
        (self.n_banner.grid if show_banner else self.n_banner.grid_remove)()
        if rec:
            bn = ui.card(self.n_banner, fg_color=C["red_soft"], border_color=ui.adj("#5b1d22"))
            bn.pack(fill="x", pady=(0, 10))
            self.n_rec_lbl = ui.label(bn, "", 14, "semibold", color="#fecaca")
            self.n_rec_lbl.pack(side="left", padx=16, pady=12)
            ui.label(bn, "প্রতি মিনিটে লেখা হচ্ছে · শেষে সারাংশ", 12, color="#fca5a5").pack(side="left")
            ui.button(bn, "■  নোট শেষ করো", self.stop_meeting, kind="rec", height=34).pack(side="right", padx=12)
        elif live:
            bn = ui.card(self.n_banner, fg_color=C["accent_soft"], border_color=ui.adj("#3b2f63"))
            bn.pack(fill="x", pady=(0, 10))
            stage = {"transcribing": "শেষ অংশগুলো লেখা হচ্ছে…", "summarizing": "সারাংশ আর মূল পয়েন্ট তৈরি হচ্ছে…",
                     "retrying": "কিছু অংশ আবার চেষ্টা করা হচ্ছে…"}.get(meta.get("stage"), "নোট তৈরি হচ্ছে…")
            ui.label(bn, "✦  " + stage, 14, "semibold", color=C["accent_text"]).pack(side="left", padx=16, pady=12)
            pb = ctk.CTkProgressBar(bn, mode="indeterminate", progress_color=C["accent"], fg_color=C["surface3"], width=220)
            pb.pack(side="right", padx=16)
            pb.start()
        elif meta.get("status") in ("interrupted", "partial", "failed", "summary_failed"):
            bn = ui.card(self.n_banner, fg_color=C["amber_soft"], border_color=ui.adj("#5a4110"))
            bn.pack(fill="x", pady=(0, 10))
            msg = {"interrupted": "Kotha বন্ধ হয়ে যাওয়ায় এই মিটিংয়ের নোট শেষ হয়নি।",
                   "partial": "কিছু অংশ এখনো লেখা হয়নি (ফ্রি লিমিট বা ইন্টারনেট)।",
                   "failed": "নোট বানাতে সমস্যা হয়েছে।",
                   "summary_failed": "ট্রান্সক্রিপ্ট আছে, কিন্তু সারাংশ বানানো যায়নি।"}[meta["status"]]
            ui.label(bn, "⚠  " + msg, 13, "medium", color="#fde68a").pack(side="left", padx=16, pady=12)
            if not self.meeting:
                ui.button(bn, "আবার প্রসেস করো", self._notes_retry, kind="soft", height=34).pack(side="right", padx=12)
        self._notes_render(mid, meta, live)

    def _notes_tick(self):
        if not (self.notes_win and self.notes_win.winfo_exists()):
            return
        m = self.meeting
        if m and m.recording:
            self.n_btn.configure(text=f"■  নোট শেষ করো   {_fmt_clock(m.elapsed())}", fg_color=ui.C["red"],
                                 hover_color=ui.C["red_hover"], state="normal")
            if self.n_sel == m.id:
                lbl = getattr(self, "n_rec_lbl", None)
                if lbl is not None and lbl.winfo_exists():
                    dot = "●" if int(time.monotonic() * 2) % 2 == 0 else "○"
                    lbl.configure(text=f"{dot}  শুনছি…  {_fmt_clock(m.elapsed())}")
                chip = getattr(self, "n_dur_chip", None)
                if chip is not None and chip.winfo_exists():
                    chip.configure(text=f"  ⏱  {_fmt_clock(m.elapsed())}  ")
        elif m:
            self.n_btn.configure(text="✦  নোট তৈরি হচ্ছে…", fg_color=ui.C["surface3"], hover_color=ui.C["surface3"],
                                 state="disabled")
        else:
            self.n_btn.configure(text="●  নতুন মিটিং নোট শুরু করুন", fg_color=ui.C["accent"],
                                 hover_color=ui.C["accent_hover"], state="normal")
        self.root.after(500, self._notes_tick)

    def _notes_copy_current(self):
        self._notes_copy("notes.md" if self.n_tab == "notes" else "transcript.md")

    def _notes_copy(self, name):
        mid = self._notes_sel()
        if mid:
            pyperclip.copy(MeetingStore.read_text(mid, name))
            self.overlay.show("note", "কপি হয়েছে")

    def _notes_export(self):
        from tkinter import filedialog
        mid = self._notes_sel()
        if not mid:
            return
        meta = MeetingStore.load(mid) or {}
        name = "".join(ch for ch in meta.get("title", "meeting") if ch not in '\\/:*?"<>|')[:60] or "meeting"
        path = filedialog.asksaveasfilename(parent=self.notes_win, defaultextension=".md", initialfile=f"{name}.md",
                                            filetypes=[("Markdown", "*.md"), ("Text", "*.txt")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(MeetingStore.read_text(mid, "notes.md") + "\n\n---\n\n## Transcript\n\n" +
                        MeetingStore.read_text(mid, "transcript.md"))
            self.overlay.show("note", "সেভ হয়েছে")

    def _notes_folder(self):
        mid = self._notes_sel()
        if mid:
            plat.open_folder(MeetingStore.folder(mid))

    def _notes_resummarize(self):
        mid = self._notes_sel()
        if not mid or (self.meeting and self.meeting.id == mid):
            return
        self.overlay.show("busy")

        def work():
            try:
                resummarize(mid, dict(self.cfg))
                self.ui_q.put(("meeting_update", mid))
                self.ui_q.put(("show", "note", "নতুন সারাংশ তৈরি হয়েছে"))
            except Exception as e:
                log(f"resummarize failed: {e!r}")
                self.ui_q.put(("show", "error", "সারাংশ বানানো যায়নি, একটু পরে আবার চেষ্টা করো"))
        threading.Thread(target=work, daemon=True).start()

    def _notes_retry(self):
        mid = self._notes_sel()
        if not mid or self.meeting:
            return
        meta = MeetingStore.load(mid) or {}
        if meta.get("status") == "summary_failed":
            return self._notes_resummarize()
        if not any(os.path.exists(os.path.join(MeetingStore.folder(mid), f"{k}.pcm")) for k in ("mic", "sys")):
            messagebox.showinfo(APP, "এই মিটিংয়ের অডিও আর নেই, তাই আবার প্রসেস করা যাবে না।", parent=self.notes_win)
            return
        self.meeting = MeetingSession(self, mid=mid)
        self.refresh_meeting_ui()

    def _notes_delete(self):
        mid = self._notes_sel()
        if not mid or (self.meeting and self.meeting.id == mid):
            return
        meta = MeetingStore.load(mid) or {}
        if messagebox.askyesno(APP, f"\"{meta.get('title', '')}\" মুছে ফেলবো? নোট, ট্রান্সক্রিপ্ট ও অডিও সব মুছে যাবে, "
                                    "আর ফেরত আনা যাবে না।", parent=self.notes_win):
            MeetingStore.delete(mid)
            self.n_sel = None
            self._notes_reload(keep=False)

    def setup_meetings(self):
        """Called once at start-up (Windows)."""
        for m in MeetingStore.list():                 # the app was closed while these were running
            if m.get("status") in ("recording", "processing"):
                m["status"] = "interrupted"
                try:
                    MeetingStore.save(m)
                except Exception:
                    pass
        self.apply_meeting_settings()
        self.detector = MeetingDetector(self)
        self.detector.start()

    def apply_meeting_settings(self):
        on = MEETING_SUPPORTED and self.cfg.get("notes_enabled", True)
        self.hook.set_toggle_hotkey(self.cfg.get("note_hotkey", DEFAULT_NOTE_HOTKEY) if on else [])
        want_bar = on and self.cfg.get("float_button", True)
        if want_bar and not self.float_bar:
            self.float_bar = FloatBar(self)
        elif not want_bar and self.float_bar:
            self.float_bar.destroy()
            self.float_bar = None
        elif self.float_bar:
            self.float_bar.place()
            self.float_bar.draw()

    # ---- popup when nowhere to paste ----
    def _new_window(self, title, w, h, resizable=True):
        win = ctk.CTkToplevel(self.root)
        win.title(title)
        try:
            sc = ctk.ScalingTracker.get_window_scaling(win)
            l, t, r, b = work_area(self.root)
            w, h = min(w, int((r - l) * 0.94 / sc)), min(h, int((b - t) * 0.9 / sc))
        except Exception:
            pass
        win.geometry(f"{w}x{h}")
        win._kotha_size = (w, h)
        if not resizable:
            win.resizable(False, False)
        win.configure(fg_color=ui.style_window(win))
        ui.set_icon(win, resource("assets/kotha.ico"))
        return win

    @staticmethod
    def _place(win, x, y):
        """Move a customtkinter window to screen pixel (x, y). CTk scales positions too, so undo that."""
        try:
            sc = ctk.ScalingTracker.get_window_scaling(win)
        except Exception:
            sc = 1.0
        win.geometry(f"+{max(0, int(x / sc))}+{max(0, int(y / sc))}")

    def _center(self, win):
        try:
            sc = ctk.ScalingTracker.get_window_scaling(win)
        except Exception:
            sc = 1.0
        size = getattr(win, "_kotha_size", None)
        if size:
            ww, wh = int(size[0] * sc), int(size[1] * sc)
        else:
            win.update_idletasks()
            ww, wh = win.winfo_reqwidth(), win.winfo_reqheight()
        l, t, r, b = work_area(self.root)
        win.geometry(f"+{max(l, l + (r - l - ww) // 2)}+{max(t, t + (b - t - wh) // 2 - 10)}")

    def _show_window(self, win):
        win.deiconify()
        win.lift()
        win.focus_force()
        win.attributes("-topmost", True)
        win.after(300, lambda: win.attributes("-topmost", False))
        plat.bring_to_front()

    def open_popup(self, text):
        if self.popup_win and self.popup_win.winfo_exists():
            self.popup_win.destroy()
        w = self._new_window(f"{APP_BN} - টেক্সট", 480, 300, resizable=False)
        self.popup_win = w
        w.attributes("-topmost", True)
        l, t, r, b = work_area(self.root)
        w.update_idletasks()
        self._place(w, r - w.winfo_width() - 24, b - w.winfo_height() - 24)
        box = ui.card(w)
        box.pack(fill="both", expand=True, padx=14, pady=14)
        ui.label(box, "টেক্সট ক্লিপবোর্ডে কপি করা আছে", 15, "semibold").pack(anchor="w", padx=16, pady=(14, 0))
        ui.label(box, f"বসানোর মতো টেক্সট বক্স পাইনি। যেকোনো জায়গায় {PASTE_KEYS} দাও।", 12,
                 color=ui.C["muted"]).pack(anchor="w", padx=16)
        txt = ctk.CTkTextbox(box, height=110, font=ui.font(14), fg_color=ui.C["surface2"], corner_radius=10,
                             border_width=1, border_color=ui.C["border"], wrap="word")
        txt.insert("1.0", text)
        txt.pack(fill="both", expand=True, padx=16, pady=10)
        bar = ctk.CTkFrame(box, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 14))

        def copy_close():
            pyperclip.copy(txt.get("1.0", "end").strip())
            w.destroy()
        ui.button(bar, "কপি করে বন্ধ করো", copy_close).pack(side="right")
        ui.button(bar, "বন্ধ", w.destroy, kind="ghost").pack(side="right", padx=8)
        w.bind("<Escape>", lambda e: w.destroy())
        self._show_window(w)

    # ---- history ----
    def open_history(self):
        if self.history_win and self.history_win.winfo_exists():
            self._show_window(self.history_win)
            return
        w = self._new_window(f"{APP} - History", 880, 560)
        self.history_win = w
        self._center(w)
        w.grid_columnconfigure(1, weight=1)
        w.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(w, fg_color="transparent")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=20, pady=(16, 8))
        ui.label(head, "History", 22, "semibold").pack(side="left")
        ui.label(head, "   শেষ ৩০০টা লেখা, শুধু আপনার কম্পিউটারে", 12, color=ui.C["muted"]).pack(side="left", pady=(6, 0))
        lst = ctk.CTkScrollableFrame(w, width=300, fg_color="transparent", scrollbar_button_color=ui.C["surface3"])
        lst.grid(row=1, column=0, sticky="nsw", padx=(14, 6), pady=(0, 16))
        right = ui.card(w)
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 16), pady=(0, 16))
        txt = ctk.CTkTextbox(right, font=ui.font(15), fg_color="transparent", wrap="word")
        txt.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        bar = ctk.CTkFrame(right, fg_color="transparent")
        bar.pack(fill="x", padx=14, pady=(0, 14))
        meta = ui.label(bar, "", 12, color=ui.C["muted"])
        meta.pack(side="left")
        ui.button(bar, "কপি করো", lambda: (pyperclip.copy(txt.get("1.0", "end").strip()),
                                         self.overlay.show("note", "কপি হয়েছে"))).pack(side="right")
        items = self.history.items
        rows = []

        def pick(i):
            for k, r in enumerate(rows):
                r.configure(fg_color=ui.C["surface2"] if k == i else "transparent",
                            border_color=ui.C["accent"] if k == i else ui.C["surface"])
            txt.delete("1.0", "end")
            txt.insert("1.0", items[i]["text"])
            meta.configure(text=f"{items[i]['t']}  ·  {len(items[i]['text'])} অক্ষর")
        for i, it in enumerate(items[:300]):
            r = ctk.CTkFrame(lst, fg_color="transparent", corner_radius=10, border_width=1, border_color=ui.C["surface"])
            r.pack(fill="x", pady=2)
            a = ui.label(r, it["text"].replace("\n", " ")[:52], 13)
            a.pack(anchor="w", padx=10, pady=(6, 0))
            b = ui.label(r, it["t"], 11, color=ui.C["faint"])
            b.pack(anchor="w", padx=10, pady=(0, 6))
            for wd in (r, a, b):
                wd.bind("<Button-1>", lambda e, i=i: pick(i))
            rows.append(r)
        if items:
            pick(0)
        else:
            txt.insert("1.0", "এখনো কিছু লেখা হয়নি। শর্টকাট চেপে ধরে কথা বলুন।")
        self._show_window(w)

    # ---- shortcut capture ----
    def capture_hotkey(self, parent, on_done):
        d = ctk.CTkToplevel(parent)
        d.title("নতুন শর্টকাট")
        d.geometry("460x250")
        d.resizable(False, False)
        d.configure(fg_color=ui.style_window(d))
        d.transient(parent)
        box = ui.card(d)
        box.pack(fill="both", expand=True, padx=14, pady=14)
        ui.label(box, "এখন নতুন শর্টকাট চেপে ছেড়ে দাও", 18, "semibold").pack(pady=(22, 2))
        ui.label(box, "যেমন: Ctrl + Alt, Right Alt, F9, Ctrl + Shift + Space", 12, color=ui.C["muted"]).pack()
        status = ui.label(box, "অপেক্ষা করছি…", 16, "semibold", color=ui.C["accent_text"])
        status.pack(pady=14)

        def done(vks):
            tokens = vks_to_tokens(vks)
            if not tokens or not hotkey_valid(tokens):
                status.configure(text="এটা চলবে না, আবার চাপো", text_color="#fca5a5")
                self.hook.start_capture()
                return
            self.capture_cb = None
            d.destroy()
            on_done(tokens)

        def cancel():
            self.hook.stop_capture()
            self.capture_cb = None
            d.destroy()
        ui.button(box, "বাতিল", cancel, kind="ghost").pack()
        d.protocol("WM_DELETE_WINDOW", cancel)
        self.capture_cb = done
        self.hook.start_capture()
        self._center(d)
        d.after(200, d.grab_set)

    # ---- settings ----
    def open_settings(self, page=None):
        if self.settings_win and self.settings_win.winfo_exists():
            self._show_window(self.settings_win)
            if page and getattr(self, "_settings_go", None):
                self._settings_go(page)
            return
        cfg = self.cfg
        C = ui.C
        w = self._new_window(f"{APP} Settings", 1000, 680)
        w.minsize(780, 520)
        self.settings_win = w

        prov_labels = [p[1] for p in PROVIDERS]
        prov_by_label = {p[1]: p[0] for p in PROVIDERS}
        label_by_prov = {p[0]: p[1] for p in PROVIDERS}
        lang_labels = [l[1] for l in LANGS]
        lang_by_label = {l[1]: l[0] for l in LANGS}
        style_opts = ["প্রতিটা শব্দ নিজের ভাষার হরফে (আমি meeting এ যাবো)", "সব এক হরফে (আমি মিটিংয়ে যাবো)"]

        v_prov = tk.StringVar(value=label_by_prov.get(cfg["provider"], prov_labels[0]))
        v_gk = tk.StringVar(value=cfg["gemini_api_key"])
        v_lang = tk.StringVar(value=LANG.get(cfg["language"], LANGS[0])[1])
        v_style = tk.StringVar(value=style_opts[0] if cfg["script_style"] == "mixed" else style_opts[1])
        v_words = tk.StringVar(value=cfg["custom_words"])
        v_fill = tk.BooleanVar(value=cfg["clean_fillers"])
        hotkey = list(cfg["hotkey"])
        v_hk = tk.StringVar(value=hotkey_label(hotkey))
        mics = ["System default"] + list_mics()
        v_mic = tk.StringVar(value=cfg["mic"] or "System default")
        v_smart = tk.BooleanVar(value=cfg["smart_paste"])
        v_restore = tk.BooleanVar(value=cfg["restore_clipboard"])
        v_sound = tk.BooleanVar(value=cfg["sounds"])
        v_start = tk.BooleanVar(value=cfg["start_with_windows"])
        v_pipe = tk.BooleanVar(value=cfg["pipeline"])
        v_notes = tk.BooleanVar(value=cfg.get("notes_enabled", True))
        v_float = tk.BooleanVar(value=cfg.get("float_button", True))
        v_autostop = tk.BooleanVar(value=cfg.get("meeting_autostop", True))
        v_keep = tk.BooleanVar(value=cfg.get("keep_meeting_audio", False))
        detect_opts = {"ask": "জিজ্ঞেস করো, নোট নেবো কিনা", "auto": "নিজে থেকেই নোট নেওয়া শুরু করো",
                       "off": "কিছু করো না"}
        v_detect = tk.StringVar(value=detect_opts.get(cfg.get("meeting_detect", "ask"), detect_opts["ask"]))
        nlang_opts = {"auto": "মিটিংয়ের ভাষা অনুযায়ী", "bn": "বাংলা", "en": "English"}
        v_nlang = tk.StringVar(value=nlang_opts.get(cfg.get("notes_language", "auto"), nlang_opts["auto"]))
        note_hotkey = list(cfg.get("note_hotkey", DEFAULT_NOTE_HOTKEY))
        v_nhk = tk.StringVar(value=hotkey_label(note_hotkey))
        adv = {k: tk.StringVar(value=cfg[k]) for k in
               ("gemini_tx_model", "gemini_model", "groq_api_key", "groq_model",
                "openai_api_key", "openai_model", "local_model", "local_model_path", "local_device")}

        w.grid_columnconfigure(1, weight=1)
        w.grid_rowconfigure(0, weight=1)

        # ---------- sidebar ----------
        side = ctk.CTkFrame(w, fg_color="transparent", width=236)
        side.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(14, 4), pady=14)
        side.grid_propagate(False)
        side.pack_propagate(False)
        brand = ctk.CTkFrame(side, fg_color="transparent")
        brand.pack(fill="x", padx=8, pady=(6, 18))
        try:
            self._logo40 = ui.image(resource("assets/kotha.png"), (42, 42))
            ctk.CTkLabel(brand, image=self._logo40, text="").pack(side="left")
        except Exception:
            pass
        bt = ctk.CTkFrame(brand, fg_color="transparent")
        bt.pack(side="left", padx=10)
        ui.label(bt, "Kotha", 21, "semibold").pack(anchor="w")
        ui.label(bt, f"কথা  ·  v{VERSION}", 12, color=C["muted"]).pack(anchor="w")

        pages_def = [("start", "⚡", "শুরু করুন"), ("lang", "🌐", "ভাষা ও লেখা"), ("keys", "⌨", "শর্টকাট"),
                     ("general", "⚙", "সাধারণ")]
        if MEETING_SUPPORTED:
            pages_def.append(("meeting", "📝", "মিটিং নোট"))
        pages_def += [("advanced", "🧩", "অ্যাডভান্সড"), ("support", "☕", "সাপোর্ট"), ("about", "✦", "পরিচিতি")]
        titles = {"start": ("শুরু করুন", "কথা বলুন, লেখা হয়ে যাবে। আগে একটা ফ্রি key দিন।"),
                  "lang": ("ভাষা ও লেখা", "কোন ভাষায় বলবেন, আর লেখা কেমন হবে।"),
                  "keys": ("শর্টকাট", "চেপে ধরে কথা বলুন, ছাড়লে লেখা বসে যাবে।"),
                  "general": ("সাধারণ", "মাইক্রোফোন, পেস্ট আর চালু হওয়ার নিয়ম।"),
                  "meeting": ("মিটিং নোট", "পুরো মিটিং শুনে নোট, সারাংশ আর করণীয় বানিয়ে দেয়।"),
                  "advanced": ("অ্যাডভান্সড", "মডেল আর অন্য engine এর key।"),
                  "support": ("পাশে থাকুন", "Kotha ফ্রি, ওপেন সোর্স, আর সবসময় ফ্রি থাকবে।"),
                  "about": ("পরিচিতি", "")}
        nav_btns = {}
        nav = ctk.CTkFrame(side, fg_color="transparent")
        nav.pack(fill="x")
        for key, icon, text in pages_def:
            b = ctk.CTkButton(nav, text=f"  {icon}   {text}", anchor="w", height=42, corner_radius=14,
                              fg_color="transparent", hover_color=C["surface"], text_color=C["muted"],
                              font=ui.font(14, "medium"), command=lambda k=key: go(k))
            b.pack(fill="x", pady=2)
            nav_btns[key] = b

        cred = ui.card(side, fg_color=C["surface"])
        cred.pack(side="bottom", fill="x", pady=(8, 0))
        ui.label(cred, "Developed by", 11, color=C["faint"]).pack(anchor="w", padx=14, pady=(10, 0))
        n = ui.label(cred, DEV_NAME, 14, "semibold", color=C["accent_text"])
        n.pack(anchor="w", padx=14)
        ui.bind_link(n, lambda: webbrowser.open(DEV_URL))
        ag = ui.label(cred, f"Part of {AGENCY_NAME}", 12, color=C["muted"])
        ag.pack(anchor="w", padx=14, pady=(0, 10))
        ui.bind_link(ag, lambda: webbrowser.open(AGENCY_URL))

        # ---------- content ----------
        content = ctk.CTkFrame(w, fg_color="transparent")
        content.grid(row=0, column=1, sticky="nsew", padx=(10, 18), pady=(18, 0))
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(content, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=4)
        h_title = ui.label(head, "", 26, "semibold")
        h_title.pack(anchor="w")
        h_sub = ui.label(head, "", 13, color=C["muted"])
        h_sub.pack(anchor="w", pady=(0, 6))
        pages = {}
        for key, _, _ in pages_def:
            pg = ctk.CTkScrollableFrame(content, fg_color="transparent", scrollbar_button_color=C["surface3"],
                                        scrollbar_button_hover_color=C["border"])
            pages[key] = pg

        def go(key):
            for k, b in nav_btns.items():
                sel = k == key
                b.configure(fg_color=C["accent_soft"] if sel else "transparent",
                            text_color=C["accent_text"] if sel else C["muted"])
            for k, pg in pages.items():
                if k == key:
                    pg.grid(row=1, column=0, sticky="nsew")
                else:
                    pg.grid_forget()
            t, sub = titles[key]
            h_title.configure(text=t)
            h_sub.configure(text=sub)
        self._settings_go = go

        # ---------- page: start ----------
        p = pages["start"]
        hero = ui.card(p, fg_color=C["accent_soft"], border_color=ui.adj("#3b2f63"))
        hero.pack(fill="x", pady=(4, 6))
        hl = ctk.CTkFrame(hero, fg_color="transparent")
        hl.pack(fill="x", padx=20, pady=16)
        ui.label(hl, "কথা বলুন, লেখা হয়ে যাবে", 19, "semibold").pack(anchor="w")
        ui.label(hl, "যেকোনো অ্যাপে ক্লিক করুন, শর্টকাট চেপে ধরে কথা বলুন, ছেড়ে দিন। দুইবার চাপলে হ্যান্ডস-ফ্রি, "
                     "Esc চাপলে বাতিল।", 13, color=C["accent_text"], wrap=560).pack(anchor="w", pady=(2, 8))
        kc = ctk.CTkFrame(hl, fg_color="transparent")
        kc.pack(anchor="w")
        for i, part in enumerate(hotkey_label(cfg["hotkey"]).split(" + ")):
            if i:
                ui.label(kc, "+", 14, "semibold", color=C["accent_text"]).pack(side="left", padx=6)
            ui.keycap(kc, part).pack(side="left")

        s1 = ui.section(p, "Engine")
        ui.setting_row(s1, "Engine", "কোন সার্ভিস দিয়ে লেখা হবে। Gemini Live সবচেয়ে দ্রুত, লেখা সাথে সাথে আসে।",
                       lambda f: ui.select(f, v_prov, prov_labels, width=360), first=True)
        ctk.CTkFrame(s1, height=10, width=1, fg_color="transparent").pack()

        s2 = ui.section(p, "Gemini API key (ফ্রি)")
        kr = ctk.CTkFrame(s2, fg_color="transparent")
        kr.pack(fill="x", padx=18, pady=(16, 6))
        ui.entry(kr, v_gk, show="•", width=420, placeholder="AIza… key এখানে পেস্ট করুন").pack(side="left", fill="x", expand=True)
        kb = ctk.CTkFrame(s2, fg_color="transparent")
        kb.pack(fill="x", padx=18, pady=(4, 4))
        key_status = ui.label(kb, "", 12, color=C["muted"])

        def test_key():
            key_status.configure(text="চেক করছি…", text_color=C["muted"])

            def run():
                ok, msg = check_gemini_key(v_gk.get())
                self.root.after(0, lambda: key_status.configure(
                    text=("✓  key ঠিক আছে" if msg == "OK" else f"✓  {msg}") if ok else f"✗  {msg[:90]}",
                    text_color="#86efac" if ok else "#fca5a5"))
            threading.Thread(target=run, daemon=True).start()
        ui.button(kb, "ফ্রি key নাও  ↗", lambda: webbrowser.open(GEMINI_KEY_URL), kind="soft").pack(side="left")
        ui.button(kb, "Key চেক করো", test_key, kind="ghost").pack(side="left", padx=8)
        key_status.pack(side="left", padx=6)
        ui.label(s2, "Google অ্যাকাউন্ট দিয়ে aistudio.google.com এ ঢুকে 'Create API key' চাপুন, key কপি করে এখানে বসান। "
                     "পুরোপুরি ফ্রি, কার্ড লাগে না।", 12, color=C["muted"], wrap=600).pack(anchor="w", padx=18, pady=(6, 2))
        ui.button(s2, "key কাজ করছে না? এখানে দেখুন", lambda: self.key_help(w), kind="link", height=28).pack(
            anchor="w", padx=10, pady=(0, 12))

        s3 = ui.section(p, "ব্যাকআপ")
        ui.setting_row(s3, "Groq key (ঐচ্ছিক)", "Gemini এর দিনের ফ্রি লিমিট শেষ হলে Kotha নিজে থেকে Groq দিয়ে চালাবে। এটাও ফ্রি।",
                       lambda f: ui.entry(f, adv["groq_api_key"], show="•", width=260), first=True)
        ui.button(s3, "ফ্রি Groq key নাও  ↗", lambda: webbrowser.open("https://console.groq.com/keys"),
                  kind="link", height=28).pack(anchor="w", padx=10, pady=(0, 12))

        # ---------- page: language ----------
        p = pages["lang"]
        s = ui.section(p, "ভাষা")
        ui.setting_row(s, "যে ভাষায় বলবেন", "Auto রাখলে বাংলা, English, মিক্স, Hindi সহ ৮৫টার বেশি ভাষা নিজে চিনে নেবে।",
                       lambda f: ui.select(f, v_lang, lang_labels, width=300), first=True)
        ui.setting_row(s, "মিক্স লেখার ধরন", "Gemini Flash, Groq, OpenAI ও Offline engine এ কাজ করে।",
                       lambda f: ui.select(f, v_style, style_opts, width=300))
        ctk.CTkFrame(s, height=8, width=1, fg_color="transparent").pack()
        s = ui.section(p, "লেখার মান")
        ui.setting_row(s, "Smart লেখা", "আ, আহ, um বাদ দেয়, ভুল শুধরে নেয়, সুন্দর করে সাজায়।",
                       lambda f: ui.switch(f, v_fill), first=True)
        ui.setting_row(s, "বিশেষ শব্দ ও নাম", "কমা দিয়ে লিখুন। যেমন: Storeganise, Dhanmondi, তাহমিদ",
                       lambda f: ui.entry(f, v_words, width=280))
        ctk.CTkFrame(s, height=8, width=1, fg_color="transparent").pack()

        # ---------- page: shortcut ----------
        p = pages["keys"]
        s = ui.section(p, "কথা বলার শর্টকাট")

        def hk_control(f):
            box = ctk.CTkFrame(f, fg_color="transparent")

            def on_preset(choice):
                for pr in HOTKEY_PRESETS:
                    if hotkey_label(pr) == choice:
                        hotkey[:] = pr
            ui.select(box, v_hk, [hotkey_label(pr) for pr in HOTKEY_PRESETS], width=220, command=on_preset).pack(side="left")

            def set_custom(tokens):
                hotkey[:] = tokens
                v_hk.set(hotkey_label(tokens))
            ui.button(box, "নিজের শর্টকাট…", lambda: self.capture_hotkey(w, set_custom), kind="ghost").pack(side="left", padx=(8, 0))
            return box
        ui.setting_row(s, "রেকর্ডিং শর্টকাট", "চেপে ধরে কথা বলুন, ছেড়ে দিলে লেখা হবে। দুইবার চাপলে হ্যান্ডস-ফ্রি, আবার চাপলে শেষ।",
                       hk_control, first=True)
        ctk.CTkFrame(s, height=8, width=1, fg_color="transparent").pack()
        tip = ui.card(p, fg_color=C["surface"])
        tip.pack(fill="x", pady=8)
        ui.label(tip, "💡  Wispr Flow বা অন্য অ্যাপের সাথে একই শর্টকাট রাখবেন না।", 13, color=C["muted"]).pack(
            anchor="w", padx=18, pady=14)

        # ---------- page: general ----------
        p = pages["general"]
        s = ui.section(p, "মাইক্রোফোন")
        ui.setting_row(s, "Microphone", "কোন মাইক দিয়ে শুনবে।", lambda f: ui.select(f, v_mic, mics, width=300), first=True)
        ctk.CTkFrame(s, height=8, width=1, fg_color="transparent").pack()
        s = ui.section(p, "লেখা বসানো")
        rows = [("টেক্সট বক্স না পেলে পপআপে দেখাও", "লেখা ক্লিপবোর্ডে থাকবে, পরে পেস্ট করতে পারবেন।", v_smart),
                ("পেস্টের পর আগের ক্লিপবোর্ড ফিরিয়ে দাও", None, v_restore),
                ("লম্বা কথা টুকরো করে আগেই পাঠাও", "অনেক দ্রুত হয়।", v_pipe),
                ("শুরু/শেষে ছোট সাউন্ড", None, v_sound),
                (plat.START_AT_LOGIN_LABEL, None, v_start)]
        for i, (t, d, var) in enumerate(rows):
            ui.setting_row(s, t, d, lambda f, var=var: ui.switch(f, var), first=(i == 0))
        ctk.CTkFrame(s, height=8, width=1, fg_color="transparent").pack()
        if plat.IS_MAC:
            s = ui.section(p, "Mac অনুমতি", "শর্টকাট, পেস্ট ও মাইক্রোফোনের জন্য প্রথম তিনটা। মিটিংয়ে অন্যদের কণ্ঠ "
                                           "শোনার জন্য Screen Recording ও চালু করুন (macOS 13+), তারপর Kotha আবার খুলুন।")
            pb = ctk.CTkFrame(s, fg_color="transparent")
            pb.pack(anchor="w", padx=18, pady=12)
            for text, which in (("Input Monitoring", "input"), ("Accessibility", "accessibility"),
                                ("Microphone", "mic"), ("Screen Recording", "screen")):
                ui.button(pb, text, lambda k=which: plat.open_privacy_pane(k), kind="ghost").pack(side="left", padx=(0, 6))

        # ---------- page: meeting notes ----------
        if MEETING_SUPPORTED:
            p = pages["meeting"]
            hero = ui.card(p, fg_color=C["accent_soft"], border_color=ui.adj("#3b2f63"))
            hero.pack(fill="x", pady=(4, 6))
            hl = ctk.CTkFrame(hero, fg_color="transparent")
            hl.pack(fill="x", padx=20, pady=16)
            ui.label(hl, "মিটিংয়ের নোট ও সারাংশ, নিজে থেকেই", 18, "semibold").pack(anchor="w")
            ui.label(hl, "Google Meet, Zoom, Teams, Slack, Discord, WhatsApp সব মিটিংয়ে। আপনার মাইক আর অন্যদের কণ্ঠ "
                         "দুটোই শোনে, প্রতি মিনিটে লিখে রাখে, শেষে সারাংশ, মূল পয়েন্ট, সিদ্ধান্ত আর করণীয় দেয়।",
                     13, color=C["accent_text"], wrap=580).pack(anchor="w", pady=(2, 10))
            ui.button(hl, "মিটিং নোটগুলো দেখুন  →", self.open_notes, kind="primary").pack(anchor="w")
            s = ui.section(p, "চালু / বন্ধ")
            ui.setting_row(s, "মিটিং নোট চালু রাখো", None, lambda f: ui.switch(f, v_notes), first=True)
            ui.setting_row(s, "বাম পাশে ভাসমান Kotha বাটন", "মাউস নিলে শর্টকাট আর নোট শুরুর বাটন দেখায়।",
                           lambda f: ui.switch(f, v_float))
            ctk.CTkFrame(s, height=8, width=1, fg_color="transparent").pack()
            s = ui.section(p, "মিটিং শনাক্ত করা")
            ui.setting_row(s, "মিটিং শুরু হলে", "কোনো কল অ্যাপ মাইক চালু করলে Kotha বুঝতে পারে।",
                           lambda f: ui.select(f, v_detect, list(detect_opts.values()), width=290), first=True)
            ui.setting_row(s, "মিটিং শেষ হলে নোটও নিজে থেকে বন্ধ", None, lambda f: ui.switch(f, v_autostop))
            ctk.CTkFrame(s, height=8, width=1, fg_color="transparent").pack()
            s = ui.section(p, "নোট")

            def nhk_control(f):
                box = ctk.CTkFrame(f, fg_color="transparent")

                def on_preset(choice):
                    for pr in NOTE_HOTKEY_PRESETS:
                        if hotkey_label(pr) == choice:
                            note_hotkey[:] = pr
                ui.select(box, v_nhk, [hotkey_label(pr) for pr in NOTE_HOTKEY_PRESETS], width=190,
                          command=on_preset).pack(side="left")

                def set_custom(tokens):
                    note_hotkey[:] = tokens
                    v_nhk.set(hotkey_label(tokens))
                ui.button(box, "নিজের…", lambda: self.capture_hotkey(w, set_custom), kind="ghost").pack(side="left", padx=(8, 0))
                return box
            ui.setting_row(s, "নোট শুরু / শেষের শর্টকাট", None, nhk_control, first=True)
            ui.setting_row(s, "নোটের ভাষা", None, lambda f: ui.select(f, v_nlang, list(nlang_opts.values()), width=230))
            ui.setting_row(s, "মিটিংয়ের অডিও ফাইলও রেখে দাও", "meeting.wav হিসেবে নোটের ফোল্ডারে থাকবে।",
                           lambda f: ui.switch(f, v_keep))
            ctk.CTkFrame(s, height=8, width=1, fg_color="transparent").pack()
            note = ui.card(p, fg_color=C["amber_soft"], border_color=ui.adj("#5a4110"))
            note.pack(fill="x", pady=8)
            ui.label(note, "🔒  রেকর্ড করার আগে মিটিংয়ের সবাইকে জানিয়ে রাখুন। নোট আর অডিও শুধু আপনার কম্পিউটারে থাকে; "
                           "লেখার জন্য অডিও আপনার নিজের Gemini/Groq key দিয়ে পাঠানো হয়।", 12,
                     color="#fde68a", wrap=600).pack(anchor="w", padx=18, pady=14)

        # ---------- page: advanced ----------
        p = pages["advanced"]
        s = ui.section(p, "মডেল")
        for i, (title, k, show) in enumerate([("Gemini Transcribe model", "gemini_tx_model", ""),
                                               ("Gemini Flash model", "gemini_model", ""),
                                               ("Groq model", "groq_model", "")]):
            ui.setting_row(s, title, None, lambda f, k=k, show=show: ui.entry(f, adv[k], show=show, width=300), first=(i == 0))
        ctk.CTkFrame(s, height=8, width=1, fg_color="transparent").pack()
        s = ui.section(p, "OpenAI (পেইড)")
        ui.setting_row(s, "OpenAI API key", None, lambda f: ui.entry(f, adv["openai_api_key"], show="•", width=300), first=True)
        ui.setting_row(s, "OpenAI model", None, lambda f: ui.entry(f, adv["openai_model"], width=300))
        ctk.CTkFrame(s, height=8, width=1, fg_color="transparent").pack()
        s = ui.section(p, "Offline")
        if LOCAL_AVAILABLE:
            ui.setting_row(s, "Offline model", None, lambda f: ui.select(
                f, adv["local_model"], ["large-v3-turbo", "large-v3", "medium", "small"], width=220), first=True)
            ui.setting_row(s, "Offline model ফোল্ডার", None, lambda f: ui.entry(f, adv["local_model_path"], width=300))
            ui.setting_row(s, "Offline device", None, lambda f: ui.select(f, adv["local_device"], ["auto", "cpu", "cuda"], width=160))
        else:
            ui.label(s, plat.NO_OFFLINE_HINT, 12, color=C["muted"], wrap=600).pack(anchor="w", padx=18, pady=14)
        ctk.CTkFrame(s, height=8, width=1, fg_color="transparent").pack()

        # ---------- page: support ----------
        p = pages["support"]
        warm = ui.card(p, fg_color=C["accent_soft"])
        warm.pack(fill="x", pady=(4, 8))
        wl = ctk.CTkFrame(warm, fg_color="transparent")
        wl.pack(fill="x", padx=22, pady=18)
        ui.label(wl, "☕  Kotha আপনার কাজে লাগলে", 19, "semibold").pack(anchor="w")
        ui.label(wl, "Kotha বানানো আর ঠিক রাখতে সময় আর খরচ লাগে, তবুও এটা সবার জন্য ফ্রি। আপনি যদি এটা ব্যবহার করে "
                     "উপকার পেয়ে থাকেন আর চান, একটা কফির দাম পাঠিয়ে আমাদের পাশে থাকতে পারেন। এতে নতুন ফিচার আর "
                     "Mac, মিটিং নোটের মতো কাজগুলো এগিয়ে নিতে সুবিধা হয়।", 13, color=C["accent_text"], wrap=620
                 ).pack(anchor="w", pady=(6, 8))
        ui.label(wl, "না পাঠালেও কোনো সমস্যা নেই, Kotha আপনার জন্যই। আপনার ধন্যবাদটুকুই অনেক। ❤", 13, "medium",
                 color=C["text"], wrap=620).pack(anchor="w")
        pay = ui.card(p)
        pay.pack(fill="x", pady=4)
        ui.label(pay, "Send Money · Personal নাম্বার", 12, color=C["muted"]).pack(anchor="w", padx=22, pady=(16, 0))
        nr = ctk.CTkFrame(pay, fg_color="transparent")
        nr.pack(fill="x", padx=22, pady=(2, 8))
        ui.label(nr, SUPPORT_NUMBER_TEXT, 26, "semibold").pack(side="left")
        copy_btn = ui.button(nr, "⧉  কপি", None, kind="soft", height=36)
        copy_btn.pack(side="left", padx=14)

        def copy_number():
            pyperclip.copy(SUPPORT_NUMBER)
            copy_btn.configure(text="✓  কপি হয়েছে")
            w.after(1800, lambda: copy_btn.configure(text="⧉  কপি"))
        copy_btn.configure(command=copy_number)
        wr = ctk.CTkFrame(pay, fg_color="transparent")
        wr.pack(fill="x", padx=22, pady=(0, 6))
        for name, col in SUPPORT_WALLETS:
            ctk.CTkLabel(wr, text=f"  {name}  ·  Personal  ", font=ui.font(13, "semibold"), text_color="#ffffff",
                         fg_color=col, corner_radius=14, height=34).pack(side="left", padx=(0, 8))
        ui.label(pay, "bKash, Rocket আর Nagad, তিনটাতেই এই একই নাম্বার। যেকোনো একটা দিয়ে Send Money করতে পারেন। "
                      "পরিমাণ পুরোপুরি আপনার ইচ্ছা।", 12, color=C["muted"], wrap=620).pack(anchor="w", padx=22, pady=(4, 18))
        other = ui.card(p, fg_color=C["surface"])
        other.pack(fill="x", pady=8)
        ui.label(other, "টাকা ছাড়াও সাহায্য করা যায়", 14, "semibold").pack(anchor="w", padx=22, pady=(16, 4))
        ui.label(other, "• GitHub এ একটা ⭐ দিন\n• বন্ধুদের বা সহকর্মীদের Kotha এর কথা বলুন\n"
                        "• কোনো সমস্যা বা নতুন আইডিয়া থাকলে জানান", 13, color=C["muted"], wrap=620).pack(anchor="w", padx=22)
        ob = ctk.CTkFrame(other, fg_color="transparent")
        ob.pack(anchor="w", padx=22, pady=(10, 18))
        ui.button(ob, "GitHub এ ⭐  ↗", lambda: webbrowser.open("https://github.com/procoder-manik/Kotha"),
                  kind="ghost").pack(side="left")
        ui.button(ob, "WhatsApp এ জানান", lambda: webbrowser.open(WHATSAPP_URL), kind="ghost").pack(side="left", padx=8)

        # ---------- page: about ----------
        p = pages["about"]
        ab = ui.card(p)
        ab.pack(fill="x", pady=(4, 8))
        try:
            self._logo96 = ui.image(resource("assets/kotha.png"), (96, 96))
            ctk.CTkLabel(ab, image=self._logo96, text="").pack(pady=(26, 6))
        except Exception:
            pass
        ui.label(ab, f"Kotha  ({APP_BN})", 28, "semibold").pack()
        ui.label(ab, f"Version {VERSION}  ·  Open source (MIT)", 12, color=C["muted"]).pack()
        ui.label(ab, "বাংলা, English আর মিক্স ভাষায় দ্রুত, ফ্রি ভয়েস টাইপিং ও মিটিং নোট", 14,
                 color=C["accent_text"]).pack(pady=(8, 20))
        dev = ui.card(p, fg_color=C["surface"])
        dev.pack(fill="x", pady=4)
        dl = ctk.CTkFrame(dev, fg_color="transparent")
        dl.pack(fill="x", padx=20, pady=18)
        ui.label(dl, "Developed by", 12, "medium", color=C["faint"]).pack(anchor="w")
        nm = ui.label(dl, DEV_NAME, 22, "semibold")
        nm.pack(anchor="w")
        ui.bind_link(nm, lambda: webbrowser.open(DEV_URL))
        ag2 = ui.label(dl, f"Part of {AGENCY_NAME}", 14, color=C["accent_text"])
        ag2.pack(anchor="w", pady=(0, 12))
        ui.bind_link(ag2, lambda: webbrowser.open(AGENCY_URL))
        br = ctk.CTkFrame(dl, fg_color="transparent")
        br.pack(anchor="w")
        ui.button(br, f"🌐  {DEV_SITE}", lambda: webbrowser.open(DEV_URL)).pack(side="left")
        ui.button(br, f"WhatsApp  {WHATSAPP_TEXT}", lambda: webbrowser.open(WHATSAPP_URL), kind="ghost").pack(side="left", padx=8)
        ui.button(br, "GitHub  ↗", lambda: webbrowser.open("https://github.com/procoder-manik/Kotha"),
                  kind="ghost").pack(side="left")
        ui.button(br, "☕  Buy me a coffee", lambda: go("support"), kind="soft").pack(side="left", padx=8)

        # ---------- footer ----------
        foot = ctk.CTkFrame(w, fg_color="transparent")
        foot.grid(row=1, column=1, sticky="ew", padx=(10, 18), pady=(10, 16))
        status = ui.label(foot, "", 12, color=C["muted"])
        status.pack(side="left")

        def save():
            prov = prov_by_label[v_prov.get()]
            if prov == "local" and not LOCAL_AVAILABLE:
                messagebox.showwarning(APP, plat.NO_OFFLINE_HINT, parent=w)
                return
            if not hotkey_valid(hotkey):
                messagebox.showwarning(APP, "শর্টকাটটা ঠিক নেই।", parent=w)
                return
            if MEETING_SUPPORTED and sort_tokens(note_hotkey) == sort_tokens(hotkey):
                messagebox.showwarning(APP, "কথা বলার শর্টকাট আর মিটিং নোটের শর্টকাট আলাদা হতে হবে।", parent=w)
                return
            cfg.update({k: v.get().strip() for k, v in adv.items()})
            cfg.update({
                "provider": prov, "gemini_api_key": v_gk.get().strip(),
                "language": lang_by_label[v_lang.get()],
                "script_style": "mixed" if v_style.get() == style_opts[0] else "native",
                "custom_words": v_words.get().strip(), "clean_fillers": v_fill.get(),
                "hotkey": list(hotkey), "mic": "" if v_mic.get() == "System default" else v_mic.get(),
                "smart_paste": v_smart.get(), "restore_clipboard": v_restore.get(), "pipeline": v_pipe.get(),
                "sounds": v_sound.get(), "start_with_windows": v_start.get(),
            })
            if MEETING_SUPPORTED:
                cfg.update({
                    "notes_enabled": v_notes.get(), "float_button": v_float.get(),
                    "meeting_detect": next(k for k, v in detect_opts.items() if v == v_detect.get()),
                    "meeting_autostop": v_autostop.get(), "note_hotkey": list(note_hotkey),
                    "notes_language": next(k for k, v in nlang_opts.items() if v == v_nlang.get()),
                    "keep_meeting_audio": v_keep.get(),
                })
            cfg.save()
            self.hook.set_hotkey(cfg["hotkey"])
            if MEETING_SUPPORTED:
                self.apply_meeting_settings()
            set_startup(cfg["start_with_windows"])
            if not cfg.ready():
                go("start")
                status.configure(text="বেছে নেওয়া engine এর API key দিন", text_color="#fca5a5")
                return
            w.destroy()
            self.overlay.show("note", f"সেভ হয়েছে। {hotkey_label(cfg['hotkey'])} চেপে কথা বলো")
            self.preload()

        ui.button(foot, "Save", save, width=120).pack(side="right")
        ui.button(foot, "Cancel", w.destroy, kind="ghost", width=100).pack(side="right", padx=8)
        if plat.IS_MAC:
            ui.button(foot, "Quit Kotha", self.quit, kind="danger").pack(side="right", padx=(0, 8))
        go(page or ("start" if not cfg.ready() else "start"))
        self._center(w)
        self._show_window(w)

    def key_help(self, parent):
        d = ctk.CTkToplevel(parent)
        d.title("Gemini key ঠিক করো")
        d.geometry("620x470")
        d.resizable(False, False)
        d.configure(fg_color=ui.style_window(d))
        d.transient(parent)
        box = ui.card(d)
        box.pack(fill="both", expand=True, padx=14, pady=14)
        ui.label(box, "Gemini key কাজ করছে না?", 19, "semibold").pack(anchor="w", padx=20, pady=(18, 4))
        ui.label(box, "\"credits depleted\" বা \"billing\" error এলে key টা এমন প্রজেক্টের, যেখানে billing চালু। "
                      "সেই প্রজেক্টে ফ্রি লিমিট থাকে না।", 13, color=ui.C["muted"], wrap=540).pack(anchor="w", padx=20)
        steps = ["নিচের বাটনে চাপুন, aistudio.google.com/apikey খুলবে।",
                 "\"Create API key\" চাপুন, তারপর \"Create API key in new project\" বেছে নিন।",
                 "নতুন প্রজেক্টে কোনো billing বা কার্ড যোগ করবেন না, তাহলেই ফ্রি থাকবে।",
                 "নতুন key কপি করে Kotha তে বসান, তারপর \"Key চেক করো\" চাপুন।"]
        for i, t in enumerate(steps, 1):
            r = ctk.CTkFrame(box, fg_color="transparent")
            r.pack(fill="x", padx=20, pady=4)
            ctk.CTkLabel(r, text=str(i), width=26, height=26, corner_radius=13, fg_color=ui.C["accent_soft"],
                         text_color=ui.C["accent_text"], font=ui.font(12, "semibold")).pack(side="left", anchor="n")
            ui.label(r, t, 13, wrap=480).pack(side="left", padx=10)
        ui.label(box, "\"ফ্রি লিমিট শেষ\" দেখালে দিনের ফ্রি কোটা শেষ। Kotha কয়েকটা Gemini মডেল পালা করে ব্যবহার করে; "
                      "তবুও শেষ হলে ফ্রি Groq key দিয়ে রাখুন, সেটা ব্যাকআপ হিসেবে চলবে।", 12, color=ui.C["muted"],
                 wrap=540).pack(anchor="w", padx=20, pady=(10, 8))
        bar = ctk.CTkFrame(box, fg_color="transparent")
        bar.pack(fill="x", padx=20, pady=(4, 18))
        ui.button(bar, "নতুন key বানাও  ↗", lambda: webbrowser.open(GEMINI_KEY_URL)).pack(side="left")
        ui.button(bar, "বন্ধ", d.destroy, kind="ghost").pack(side="right")
        self._center(d)

    def preload(self):
        cfg = dict(self.cfg)
        if cfg["provider"] != "local" or not LOCAL_AVAILABLE or LOCAL.ready(cfg):
            return
        first = not any(os.scandir(MODELS_DIR)) and not cfg["local_model_path"]
        self.ui_q.put(("show", "info", "প্রথমবার অফলাইন মডেল ডাউনলোড হচ্ছে…" if first else "অফলাইন মডেল লোড হচ্ছে…"))

        def work():
            try:
                LOCAL.load(cfg)
                self.ui_q.put(("show", "note", f"{APP_BN} রেডি। {hotkey_label(self.cfg['hotkey'])} চেপে কথা বলো"))
            except Exception as e:
                log(f"model load failed: {e!r}")
                self.ui_q.put(("show", "error", "মডেল লোড হয়নি। ইন্টারনেট বা ফোল্ডার চেক করো"))
        threading.Thread(target=work, daemon=True).start()

    def run(self):
        plat.request_permissions()
        try:
            self.start_tray()
        except Exception as e:
            log(f"tray icon failed: {e!r}")
            self.ui_q.put(("settings",))
        self.hook.start()
        self.controller.start()
        if MEETING_SUPPORTED:
            try:
                self.setup_meetings()
            except Exception as e:
                log(f"meeting notes setup failed: {e!r}")
        set_startup(self.cfg["start_with_windows"])
        self.cfg.save()
        if not self.cfg.ready():
            self.ui_q.put(("settings",))
        elif self.cfg["provider"] == "local":
            self.preload()
        else:
            self.overlay.show("note", f"{APP_BN} চালু হয়েছে। {hotkey_label(self.cfg['hotkey'])} চেপে কথা বলো")
            prewarm(self.cfg["provider"])
        self.root.after(25, self.pump)
        self.root.mainloop()


def set_startup(enabled):
    plat.set_startup(enabled, APP)


def selftest(ui_check=False):
    """Kotha --selftest [--ui]: checks the bundled audio, meeting and (with --ui) window code. Used to verify builds."""
    lines = [f"Kotha {VERSION} selftest on {plat.OS_NAME}"]
    ok = True

    def check(name, fn):
        nonlocal ok
        try:
            lines.append(f"ok    {name}: {fn()}")
        except Exception as e:
            ok = False
            lines.append(f"FAIL  {name}: {e!r}")

    check("numpy", lambda: __import__("numpy").__version__)
    if plat.IS_WIN:
        def audio():
            import soundcard as sc
            spk = sc.default_speaker()
            loop = sc.get_microphone(id=str(spk.id), include_loopback=True)
            with loop.recorder(samplerate=SR, channels=1) as r:
                return f"{len(sc.all_speakers())} speakers, {len(sc.all_microphones())} mics, loopback {len(r.record(numframes=SR // 10))} frames"
        check("audio", audio)
    else:
        check("microphones", lambda: f"{len(list_mics())} found: {list_mics()[:3]}")

        def sck():
            import ScreenCaptureKit as SCK
            cfg = SCK.SCStreamConfiguration.alloc().init()
            return (f"ScreenCaptureKit loaded, audio capture supported={bool(cfg.respondsToSelector_(b'setCapturesAudio:'))}, "
                    f"screen permission={plat.screen_capture_allowed()}")
        check("system audio api", sck)
        check("pcm conversion", lambda: bool(plat._cm()) and "CoreMedia functions bound")
        check("mic in use (CoreAudio)", lambda: plat.mic_in_use_somewhere())
    check("meeting detection", lambda: plat.active_meetings("", False))
    check("fonts", lambda: f"loaded={ui.load_fonts(resource)} family={ui.FAMILY}")
    if ui_check:
        def windows():
            app = App()
            app.cfg["gemini_api_key"] = "AIzaSelfTest"
            app.apply_meeting_settings()
            r = app.root
            out = []

            def pump(sec=0.4):
                end = time.time() + sec
                while time.time() < end:
                    r.update()
                    time.sleep(0.02)
            for page in ("start", "lang", "keys", "general", "meeting", "advanced", "support", "about"):
                if page == "meeting" and not MEETING_SUPPORTED:
                    continue
                app.open_settings(page)
                pump(0.25)
            out.append(f"settings {app.settings_win.winfo_width()}x{app.settings_win.winfo_height()}")
            app.settings_win.destroy()
            app.open_history()
            pump()
            app.history_win.destroy()
            app.open_notes()
            pump(0.5)
            out.append(f"notes {app.notes_win.winfo_width()}x{app.notes_win.winfo_height()}")
            app.notes_win.destroy()
            fb = app.float_bar
            if fb is not None:
                for hov in (None, "mic", "rec"):
                    fb.expanded, fb.hover = True, hov
                    fb.place()
                    fb.draw()
                    pump(0.1)
                fb.expanded = False
                fb.place()
                fb.draw()
                out.append("float bar drawn")
            app.on_meeting_detected("Zoom", "Zoom")
            pump(0.3)
            app.card.close()
            app.overlay.show("rec")
            pump(0.2)
            app.overlay.hide()
            app.open_popup("test")
            pump(0.2)
            app.popup_win.destroy()
            return ", ".join(out)
        check("windows", windows)
    lines.append("RESULT: " + ("OK" if ok else "FAILED"))
    for line in lines:
        log("selftest: " + line)
    try:
        with open(os.path.join(DATA_DIR, "selftest.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass
    print("\n".join(lines))
    sys.stdout.flush()
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        os._exit(selftest("--ui" in sys.argv))
    if not plat.acquire_single_instance(APP):
        plat.already_running_notice(APP)
        return
    plat.setup_process()
    App().run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"fatal: {e!r}")
        raise
