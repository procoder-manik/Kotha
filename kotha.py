"""
Kotha (কথা) - fast voice typing for Windows and macOS.
Bangla, English, mixed Banglish, Hindi and 80+ more languages.

  Hold the shortcut (Windows: Ctrl + Win, Mac: Right Option) -> talk, release -> text is typed at your cursor
  Double-tap the shortcut                 -> hands-free, press once more to finish
  Esc while recording                     -> cancel

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
VERSION = "2.2.2"
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


GEMINI_FALLBACKS = ["gemini-flash-latest", "gemini-3.5-flash-lite", "gemini-2.5-flash", "gemini-2.5-flash-lite"]
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
    for model in ["gemini-flash-latest", "gemini-2.5-flash-lite", "gemini-3.5-flash-lite", "gemini-2.5-flash"]:
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
        self.cv.create_polygon(pts, smooth=True, fill="#14181c", outline="#2a3138")

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

    # ---- tray ----
    def start_tray(self):
        img = Image.open(resource("assets/kotha.png"))
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: f"{APP_BN} চালু আছে  ({hotkey_label(self.cfg['hotkey'])})", None, enabled=False),
            pystray.MenuItem("Settings", lambda: self.ui_q.put(("settings",)), default=True),
            pystray.MenuItem("History", lambda: self.ui_q.put(("history",))),
            pystray.MenuItem("শেষ টেক্সট আবার পেস্ট করো", lambda: self.controller.q.put(("paste_last",))),
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
                elif kind == "quit":
                    self.quit()
                    return
        except queue.Empty:
            pass
        self.root.after(25, self.pump)

    def quit(self):
        try:
            self.tray.stop()
        except Exception:
            pass
        self.root.destroy()
        os._exit(0)

    # ---- popup when nowhere to paste ----
    def open_popup(self, text):
        if self.popup_win and self.popup_win.winfo_exists():
            self.popup_win.destroy()
        s = self.scale
        w = tk.Toplevel(self.root)
        self.popup_win = w
        w.title(f"{APP_BN} - টেক্সট")
        w.attributes("-topmost", True)
        w.configure(bg="#ffffff")
        ww, wh = int(440 * s), int(240 * s)
        l, t, r, b = work_area(self.root)
        w.geometry(f"{ww}x{wh}+{r - ww - int(20 * s)}+{b - wh - int(20 * s)}")
        tk.Label(w, text=f"বসানোর মতো টেক্সট বক্স পাইনি। টেক্সট ক্লিপবোর্ডে কপি করা আছে, যেকোনো জায়গায় {PASTE_KEYS} দাও।",
                 bg="#ffffff", fg="#555", font=(FONT, 9), wraplength=ww - 30, justify="left"
                 ).pack(anchor="w", padx=14, pady=(12, 6))
        txt = tk.Text(w, wrap="word", font=(FONT, 11), height=5, relief="solid", bd=1)
        txt.insert("1.0", text)
        txt.pack(fill="both", expand=True, padx=14)
        bar = tk.Frame(w, bg="#ffffff")
        bar.pack(fill="x", padx=14, pady=10)

        def copy_close():
            pyperclip.copy(txt.get("1.0", "end").strip())
            w.destroy()
        ttk.Button(bar, text="কপি করো", command=copy_close).pack(side="right")
        ttk.Button(bar, text="বন্ধ", command=w.destroy).pack(side="right", padx=8)
        w.bind("<Escape>", lambda e: w.destroy())
        w.lift()
        w.focus_force()
        plat.bring_to_front()

    # ---- history ----
    def open_history(self):
        if self.history_win and self.history_win.winfo_exists():
            self.history_win.lift()
            plat.bring_to_front()
            return
        s = self.scale
        w = tk.Toplevel(self.root)
        self.history_win = w
        plat.bring_to_front()
        w.title(f"{APP} - History")
        w.geometry(f"{int(660 * s)}x{int(450 * s)}")
        pan = ttk.PanedWindow(w, orient="horizontal")
        pan.pack(fill="both", expand=True, padx=10, pady=10)
        lb = tk.Listbox(pan, font=(FONT, 10), activestyle="none")
        txt = tk.Text(pan, wrap="word", font=(FONT, 11))
        pan.add(lb, weight=1)
        pan.add(txt, weight=2)
        items = self.history.items
        for it in items:
            lb.insert("end", f"{it['t'][5:]}  {it['text'].replace(chr(10), ' ')[:40]}")

        def on_sel(_=None):
            sel = lb.curselection()
            if sel:
                txt.delete("1.0", "end")
                txt.insert("1.0", items[sel[0]]["text"])
        lb.bind("<<ListboxSelect>>", on_sel)
        ttk.Button(w, text="কপি করো", command=lambda: pyperclip.copy(txt.get("1.0", "end").strip())
                   ).pack(side="right", padx=10, pady=(0, 10))
        if items:
            lb.selection_set(0)
            on_sel()

    # ---- shortcut capture ----
    def capture_hotkey(self, parent, on_done):
        d = tk.Toplevel(parent)
        d.title("নতুন শর্টকাট")
        d.transient(parent)
        d.resizable(False, False)
        f = ttk.Frame(d, padding=20)
        f.pack()
        ttk.Label(f, text="এখন নতুন শর্টকাট চেপে ছেড়ে দাও", style="Title.TLabel").pack()
        ttk.Label(f, text="যেমন: Ctrl + Alt, Right Alt, F9, Ctrl + Shift + Space", style="Hint.TLabel").pack(pady=(4, 0))
        status = ttk.Label(f, text="অপেক্ষা করছি…", style="Big.TLabel")
        status.pack(pady=12)

        def done(vks):
            tokens = vks_to_tokens(vks)
            if not tokens or not hotkey_valid(tokens):
                status.config(text="এটা চলবে না, আবার চাপো", foreground="#b00020")
                self.hook.start_capture()
                return
            self.capture_cb = None
            d.destroy()
            on_done(tokens)

        def cancel():
            self.hook.stop_capture()
            self.capture_cb = None
            d.destroy()
        ttk.Button(f, text="বাতিল", command=cancel).pack()
        d.protocol("WM_DELETE_WINDOW", cancel)
        self.capture_cb = done
        self.hook.start_capture()
        d.update_idletasks()
        d.geometry(f"+{parent.winfo_rootx() + 60}+{parent.winfo_rooty() + 120}")
        d.grab_set()

    # ---- settings ----
    def open_settings(self):
        if self.settings_win and self.settings_win.winfo_exists():
            self.settings_win.lift()
            self.settings_win.focus_force()
            plat.bring_to_front()
            return
        cfg = self.cfg
        w = tk.Toplevel(self.root)
        self.settings_win = w
        w.title(f"{APP} ({APP_BN}) {VERSION} - Settings")
        w.resizable(False, False)
        outer = ttk.Frame(w, padding=14)
        outer.pack(fill="both", expand=True)
        nb = ttk.Notebook(outer)
        nb.pack(fill="both", expand=True)

        def tab(title):
            f = ttk.Frame(nb, padding=14)
            f.columnconfigure(1, weight=1)
            nb.add(f, text=title)
            return f

        def combo(parent, row, label, var, values, width=46):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            cb = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width)
            cb.grid(row=row, column=1, sticky="we", pady=4)
            return cb

        def entry(parent, row, label, var, show="", width=48):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=4)
            e = ttk.Entry(parent, textvariable=var, show=show, width=width)
            e.grid(row=row, column=1, sticky="we", pady=4)
            return e

        def hint(parent, row, text):
            ttk.Label(parent, text=text, style="Hint.TLabel", wraplength=int(430 * self.scale), justify="left"
                      ).grid(row=row, column=1, sticky="w")

        def link(parent, text, url, big=False):
            base = "BigLink.TLabel" if big else "Link.TLabel"
            hover = "BigLinkHover.TLabel" if big else "LinkHover.TLabel"
            lb = ttk.Label(parent, text=text, style=base, cursor="hand2")
            lb.bind("<Button-1>", lambda e: webbrowser.open(url))
            lb.bind("<Enter>", lambda e: lb.configure(style=hover))
            lb.bind("<Leave>", lambda e: lb.configure(style=base))
            return lb

        prov_labels = [p[1] for p in PROVIDERS]
        prov_by_label = {p[1]: p[0] for p in PROVIDERS}
        label_by_prov = {p[0]: p[1] for p in PROVIDERS}
        lang_labels = [l[1] for l in LANGS]
        lang_by_label = {l[1]: l[0] for l in LANGS}

        v_prov = tk.StringVar(value=label_by_prov.get(cfg["provider"], prov_labels[0]))
        v_gk = tk.StringVar(value=cfg["gemini_api_key"])
        v_lang = tk.StringVar(value=LANG.get(cfg["language"], LANGS[0])[1])
        v_style = tk.StringVar(value="প্রতিটা শব্দ নিজের ভাষার হরফে (আমি meeting এ যাবো)"
                               if cfg["script_style"] == "mixed" else "সব এক হরফে (আমি মিটিংয়ে যাবো)")
        style_opts = ["প্রতিটা শব্দ নিজের ভাষার হরফে (আমি meeting এ যাবো)", "সব এক হরফে (আমি মিটিংয়ে যাবো)"]
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
        adv = {k: tk.StringVar(value=cfg[k]) for k in
               ("gemini_tx_model", "gemini_model", "groq_api_key", "groq_model",
                "openai_api_key", "openai_model", "local_model", "local_model_path", "local_device")}

        # --- tab 1: start ---
        t1 = tab("শুরু")
        ttk.Label(t1, text="কথা বলো, লেখা হয়ে যাবে", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(t1, text=f"শর্টকাট চেপে ধরে কথা বলো, ছাড়লে লেখা বসে যাবে। দুইবার চাপলে হ্যান্ডস-ফ্রি। Esc = বাতিল।",
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 10))
        combo(t1, 2, "Engine", v_prov, prov_labels)
        entry(t1, 3, "Gemini API key", v_gk, show="•")
        keybar = ttk.Frame(t1)
        keybar.grid(row=4, column=1, sticky="w", pady=(2, 4))
        key_status = ttk.Label(keybar, text="", style="Hint.TLabel")
        ttk.Button(keybar, text="ফ্রি key নাও", command=lambda: webbrowser.open(GEMINI_KEY_URL)).pack(side="left")

        def test_key():
            key_status.config(text="চেক করছি…", foreground="#666")

            def run():
                ok, msg = check_gemini_key(v_gk.get())
                self.root.after(0, lambda: key_status.config(
                    text=("✓ key ঠিক আছে" if msg == "OK" else f"✓ {msg}") if ok else f"✗ {msg[:90]}", foreground="#007a4d" if ok else "#b00020"))
            threading.Thread(target=run, daemon=True).start()
        ttk.Button(keybar, text="Key চেক করো", command=test_key).pack(side="left", padx=6)
        key_status.pack(side="left", padx=6)
        hint(t1, 5, "Google অ্যাকাউন্ট দিয়ে aistudio.google.com এ ঢুকে 'Create API key' চাপো, "
                    "key কপি করে এখানে বসাও। পুরোপুরি ফ্রি, কার্ড লাগে না।")
        ttk.Button(t1, text="key কাজ করছে না? এখানে দেখো", command=lambda: self.key_help(w)
                   ).grid(row=6, column=1, sticky="w", pady=(4, 10))
        entry(t1, 7, "Groq key (ব্যাকআপ)", adv["groq_api_key"], show="•")
        gbar = ttk.Frame(t1)
        gbar.grid(row=8, column=1, sticky="w")
        ttk.Button(gbar, text="ফ্রি Groq key নাও", command=lambda: webbrowser.open("https://console.groq.com/keys")
                   ).pack(side="left")
        hint(t1, 9, "ঐচ্ছিক। Gemini এর দিনের ফ্রি লিমিট শেষ হলে Kotha নিজে থেকে Groq দিয়ে চালাবে। এটাও ফ্রি।")

        # --- tab 2: language ---
        t2 = tab("ভাষা")
        combo(t2, 0, "ভাষা", v_lang, lang_labels)
        hint(t2, 1, "Auto রাখলে বাংলা, English, মিক্স, Hindi সহ ৮৫টার বেশি ভাষা নিজে চিনে নেবে।")
        combo(t2, 2, "মিক্স লেখার ধরন", v_style, style_opts)
        hint(t2, 3, "এটা Gemini Flash, Groq, OpenAI ও Offline engine এ কাজ করে।")
        ttk.Checkbutton(t2, text="আ, আহ, um বাদ দাও, ভুল শুধরে নাও, সুন্দর করে সাজাও (Smart)",
                        variable=v_fill).grid(row=4, column=1, sticky="w", pady=(8, 2))
        entry(t2, 5, "বিশেষ শব্দ/নাম", v_words)
        hint(t2, 6, "কমা দিয়ে লেখো, যেমন: Storeganise, Dhanmondi, তাহমিদ। নাম আর ব্র্যান্ড ঠিক বানানে আসবে।")

        # --- tab 3: shortcut ---
        t3 = tab("শর্টকাট")
        ttk.Label(t3, text="রেকর্ডিং শর্টকাট").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=4)
        hk_box = ttk.Combobox(t3, textvariable=v_hk, state="readonly", width=30,
                              values=[hotkey_label(p) for p in HOTKEY_PRESETS])
        hk_box.grid(row=0, column=1, sticky="w", pady=4)

        def on_preset(_=None):
            for p in HOTKEY_PRESETS:
                if hotkey_label(p) == v_hk.get():
                    hotkey[:] = p
        hk_box.bind("<<ComboboxSelected>>", on_preset)

        def set_custom(tokens):
            hotkey[:] = tokens
            v_hk.set(hotkey_label(tokens))
        ttk.Button(t3, text="নিজের শর্টকাট সেট করো…", command=lambda: self.capture_hotkey(w, set_custom)
                   ).grid(row=1, column=1, sticky="w", pady=4)
        hint(t3, 2, "চেপে ধরে কথা বলো, ছেড়ে দিলে লেখা হবে। দুইবার চাপলে হ্যান্ডস-ফ্রি, আবার চাপলে শেষ। "
                    "Wispr Flow বা অন্য অ্যাপের সাথে একই শর্টকাট রেখো না।")

        # --- tab 4: general ---
        t4 = tab("সাধারণ")
        combo(t4, 0, "Microphone", v_mic, mics)
        r = 1
        for text, var in [("টেক্সট বক্স না পেলে পপআপে দেখাও", v_smart),
                          ("পেস্টের পর আগের ক্লিপবোর্ড ফিরিয়ে দাও", v_restore),
                          ("লম্বা কথা টুকরো করে আগেই পাঠাও (অনেক দ্রুত)", v_pipe),
                          ("শুরু/শেষে ছোট সাউন্ড", v_sound),
                          (plat.START_AT_LOGIN_LABEL, v_start)]:
            ttk.Checkbutton(t4, text=text, variable=var).grid(row=r, column=1, sticky="w", pady=3)
            r += 1
        if plat.IS_MAC:
            ttk.Label(t4, text="Mac অনুমতি: শর্টকাট, পেস্ট ও মাইক্রোফোনের জন্য এই তিনটায় Kotha কে চালু করো",
                      style="Hint.TLabel", wraplength=int(430 * self.scale), justify="left"
                      ).grid(row=r, column=1, sticky="w", pady=(14, 2))
            pbar = ttk.Frame(t4)
            pbar.grid(row=r + 1, column=1, sticky="w")
            for text, which in (("Input Monitoring", "input"), ("Accessibility", "accessibility"), ("Microphone", "mic")):
                ttk.Button(pbar, text=text, command=lambda k=which: plat.open_privacy_pane(k)).pack(side="left", padx=(0, 6))

        # --- tab 5: advanced ---
        t5 = tab("অ্যাডভান্সড")
        rows = [("Gemini Transcribe model", "gemini_tx_model", ""), ("Gemini Flash model", "gemini_model", ""),
                ("Groq model", "groq_model", ""),
                ("OpenAI API key", "openai_api_key", "•"), ("OpenAI model", "openai_model", "")]
        for i, (label, k, show) in enumerate(rows):
            entry(t5, i, label, adv[k], show=show)
        i = len(rows)
        if LOCAL_AVAILABLE:
            combo(t5, i, "Offline model", adv["local_model"], ["large-v3-turbo", "large-v3", "medium", "small"], width=30)
            entry(t5, i + 1, "Offline model ফোল্ডার", adv["local_model_path"])
            combo(t5, i + 2, "Offline device", adv["local_device"], ["auto", "cpu", "cuda"], width=30)
        else:
            hint(t5, i, plat.NO_OFFLINE_HINT)

        # --- tab 6: about ---
        t6 = tab("পরিচিতি")
        t6.columnconfigure(0, weight=1)
        about = ttk.Frame(t6)
        about.grid(row=0, column=0, columnspan=2, pady=(18, 0))
        try:
            logo = tk.PhotoImage(file=resource("assets/kotha.png"))
            f = max(1, round(max(logo.width(), logo.height()) / (64 * self.scale)))
            if f > 1:
                logo = logo.subsample(f, f)
            w._about_logo = logo   # keep a reference so Tk does not drop the image
            ttk.Label(about, image=logo).pack()
        except Exception:
            pass
        ttk.Label(about, text=f"{APP} ({APP_BN})", font=(FONT, 16, "bold")).pack(pady=(8, 0))
        ttk.Label(about, text=f"Version {VERSION}", style="Hint.TLabel").pack()
        ttk.Label(about, text="বাংলা, English আর মিক্স ভাষায় দ্রুত, ফ্রি ভয়েস টাইপিং",
                  style="Hint.TLabel").pack(pady=(6, 18))
        ttk.Separator(about).pack(fill="x", pady=(0, 14))
        ttk.Label(about, text="Developed by", style="Hint.TLabel").pack()
        link(about, DEV_NAME, DEV_URL, big=True).pack(pady=(2, 2))
        link(about, f"🌐 {DEV_SITE}", DEV_URL).pack()
        part = ttk.Frame(about)
        part.pack(pady=(10, 0))
        ttk.Label(part, text="Part of", style="Hint.TLabel").pack(side="left")
        link(part, AGENCY_NAME, AGENCY_URL).pack(side="left", padx=(4, 0))
        btns = ttk.Frame(about)
        btns.pack(pady=(14, 0))
        ttk.Button(btns, text="ওয়েবসাইট দেখো", command=lambda: webbrowser.open(DEV_URL)).pack(side="left")
        ttk.Button(btns, text=f"WhatsApp: {WHATSAPP_TEXT}", command=lambda: webbrowser.open(WHATSAPP_URL)
                   ).pack(side="left", padx=(8, 0))

        def save():
            prov = prov_by_label[v_prov.get()]
            if prov == "local" and not LOCAL_AVAILABLE:
                messagebox.showwarning(APP, plat.NO_OFFLINE_HINT, parent=w)
                return
            if not hotkey_valid(hotkey):
                messagebox.showwarning(APP, "শর্টকাটটা ঠিক নেই।", parent=w)
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
            cfg.save()
            self.hook.set_hotkey(cfg["hotkey"])
            set_startup(cfg["start_with_windows"])
            if not cfg.ready():
                messagebox.showwarning(APP, "বেছে নেওয়া engine এর API key দাও।", parent=w)
                return
            w.destroy()
            self.overlay.show("note", f"সেভ হয়েছে। {hotkey_label(cfg['hotkey'])} চেপে কথা বলো")
            self.preload()

        bar = ttk.Frame(outer)
        bar.pack(fill="x", pady=(12, 0))
        ttk.Label(bar, text=f"Kotha {VERSION} · open source", style="Hint.TLabel").pack(side="left")
        ttk.Button(bar, text="Cancel", command=w.destroy).pack(side="right")
        ttk.Button(bar, text="Save", command=save).pack(side="right", padx=8)
        if plat.IS_MAC:     # the menu-bar icon also has Quit; this is a fallback if the icon cannot be shown
            ttk.Button(bar, text="Quit Kotha", command=self.quit).pack(side="right", padx=(0, 8))

        # credit strip, always visible at the bottom of Settings
        ttk.Separator(outer).pack(fill="x", pady=(12, 8))
        credit = ttk.Frame(outer)
        credit.pack(fill="x")
        ttk.Label(credit, text="Developed by", style="Credit.TLabel").pack(side="left")
        link(credit, DEV_NAME, DEV_URL).pack(side="left", padx=(4, 0))
        link(credit, AGENCY_NAME, AGENCY_URL).pack(side="right")
        ttk.Label(credit, text="Part of", style="Credit.TLabel").pack(side="right", padx=(0, 4))
        w.update_idletasks()
        sw, sh = w.winfo_screenwidth(), w.winfo_screenheight()
        w.geometry(f"+{(sw - w.winfo_width()) // 2}+{max(0, (sh - w.winfo_height()) // 2 - 30)}")
        w.lift()
        w.focus_force()
        plat.bring_to_front()

    def key_help(self, parent):
        d = tk.Toplevel(parent)
        d.title("Gemini key ঠিক করো")
        d.transient(parent)
        d.resizable(False, False)
        f = ttk.Frame(d, padding=18)
        f.pack()
        steps = (
            "\"credits depleted\" বা \"billing\" error এলে বুঝবে key টা এমন প্রজেক্টের, যেখানে billing চালু। "
            "সেই প্রজেক্টে ফ্রি লিমিট থাকে না। ঠিক করার উপায়:\n\n"
            "1. নিচের বাটনে চাপো, aistudio.google.com/apikey খুলবে।\n"
            "2. \"Create API key\" চাপো, তারপর \"Create API key in new project\" বেছে নাও।\n"
            "3. নতুন প্রজেক্টে কোনো billing বা কার্ড যোগ করবে না, তাহলেই ফ্রি থাকবে।\n"
            "4. নতুন key কপি করে Kotha তে বসাও, তারপর \"Key চেক করো\" চাপো।\n\n"
            "\"ফ্রি লিমিট শেষ\" দেখালে দিনের ফ্রি কোটা শেষ হয়েছে। Kotha নিজে থেকে কয়েকটা Gemini মডেল "
            "পালা করে ব্যবহার করে, তবুও শেষ হলে ফ্রি Groq key দিয়ে রাখো, সেটা ব্যাকআপ হিসেবে চলবে।"
        )
        ttk.Label(f, text=steps, wraplength=int(440 * self.scale), justify="left").pack(anchor="w")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(14, 0))
        ttk.Button(bar, text="নতুন key বানাও", command=lambda: webbrowser.open(GEMINI_KEY_URL)).pack(side="left")
        ttk.Button(bar, text="বন্ধ", command=d.destroy).pack(side="right")
        d.update_idletasks()
        d.geometry(f"+{parent.winfo_rootx() + 40}+{parent.winfo_rooty() + 60}")

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


def main():
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
