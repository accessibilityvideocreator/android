#!/usr/bin/env python3
"""
tts_web.py  --  TTS Generator Web Interface
--------------------------------------------
Starts a local HTTP server and opens the TTS Generator in your browser.
All processing happens locally on your machine -- no data leaves your PC.

Usage:
    python tts_web.py
    python tts_web.py --port 8765

Requirements (same as before):
    pip install pyttsx3        # for offline System TTS
    pip install pytesseract Pillow  # for OCR
    ffmpeg on PATH             # for video generation
"""

import argparse
import base64
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR    = pathlib.Path(__file__).parent
CONFIG_FILE   = SCRIPT_DIR / "tts_generator_config.json"
NARRATION_TXT = SCRIPT_DIR / "example" / "HOW_IT_WORKS_narration.txt"
SCREENSHOT_PNG = SCRIPT_DIR / "HOW_IT_WORKS_screenshot.png"
MAKE_VIDEO_PY  = SCRIPT_DIR / "make_doc_video.py"
TESSERACT_EXE  = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

DEFAULT_CFG = {
    "service":        "system",
    "el_api_key":     "",
    "el_voice_id":    "21m00Tcm4TlvDq8ikWAM",
    "google_api_key": "",
    "google_voice":   "en-US-Standard-B",
    "output_dir":     str(pathlib.Path.home() / "Downloads"),
}

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def load_cfg():
    try:
        if CONFIG_FILE.exists():
            with open(CONFIG_FILE, encoding="utf-8") as f:
                return {**DEFAULT_CFG, **json.load(f)}
    except Exception:
        pass
    return dict(DEFAULT_CFG)

def save_cfg(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# TTS helpers (ported from tts_generator.py)
# ---------------------------------------------------------------------------
def _chunk_text(text: str, max_chars: int = 4999):
    chunks, current = [], ""
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if len(current) + len(sentence) + 1 <= max_chars:
            current = (current + " " + sentence).strip()
        else:
            if current:
                chunks.append(current)
            current = sentence[:max_chars]
    if current:
        chunks.append(current)
    return chunks or [text[:max_chars]]

def synthesize_system_tts(text, out_path, log_fn=None):
    import pyttsx3
    engine = pyttsx3.init()
    engine.setProperty("rate", 165)
    tmp = out_path.replace(".mp3", "_sys.wav")
    engine.save_to_file(text, tmp)
    engine.runAndWait()
    if not pathlib.Path(tmp).exists() or pathlib.Path(tmp).stat().st_size < 100:
        raise RuntimeError("pyttsx3 produced no output -- check Windows speech settings.")
    os.replace(tmp, out_path.replace(".mp3", ".wav"))
    return out_path.replace(".mp3", ".wav")

def synthesize_elevenlabs(text, api_key, voice_id, out_path, log_fn=None):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    payload = json.dumps({
        "text": text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST",
        headers={"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"})
    with urllib.request.urlopen(req) as resp:
        audio = resp.read()
    with open(out_path, "wb") as f:
        f.write(audio)
    return out_path

def _google_chunk(chunk, api_key, lang_code, voice_name):
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={api_key}"
    payload = json.dumps({
        "input": {"text": chunk},
        "voice": {"languageCode": lang_code, "name": voice_name},
        "audioConfig": {"audioEncoding": "MP3"}
    }).encode()
    req = urllib.request.Request(url, data=payload, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return base64.b64decode(data["audioContent"])

def synthesize_google(text, api_key, voice_name, out_path, log_fn=None):
    lang_code = "-".join(voice_name.split("-")[:2])
    chunks = _chunk_text(text)
    if log_fn:
        log_fn(f"Google TTS: {len(text):,} chars split into {len(chunks)} chunk(s)")
    audio_bytes = b""
    for i, chunk in enumerate(chunks, 1):
        if log_fn:
            log_fn(f"  Chunk {i}/{len(chunks)}  ({len(chunk):,} chars)...")
        audio_bytes += _google_chunk(chunk, api_key, lang_code, voice_name)
    with open(out_path, "wb") as f:
        f.write(audio_bytes)
    return out_path

def ocr_image(image_path):
    import pytesseract
    from PIL import Image as PILImage
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_EXE
    img = PILImage.open(image_path)
    text = pytesseract.image_to_string(img, lang="eng")
    return re.sub(r"\n{3,}", "\n\n", text).strip()

# ---------------------------------------------------------------------------
# Server-sent events log queue (shared between worker threads and HTTP handler)
# ---------------------------------------------------------------------------
_log_queue = []
_log_lock  = threading.Lock()

def _push_log(msg, level="info"):
    with _log_lock:
        _log_queue.append({"msg": msg, "level": level, "t": time.time()})
        if len(_log_queue) > 500:
            _log_queue.pop(0)

def _pop_logs(since: float):
    with _log_lock:
        return [e for e in _log_queue if e["t"] > since]

# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TTS Generator &mdash; Accessibility Video Creator</title>
<style>
  :root {
    --bg:      #0d1117;
    --bg2:     #161b22;
    --bg3:     #21262d;
    --border:  #30363d;
    --fg:      #c9d1d9;
    --fg2:     #8b949e;
    --bright:  #e6edf3;
    --blue:    #1f6feb;
    --blue-h:  #388bfd;
    --green:   #238636;
    --green-h: #2ea043;
    --orange:  #d29922;
    --red:     #da3633;
    --accent:  #3fb950;
    --radius:  8px;
    --font:    -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    --mono:    "Cascadia Code", "Consolas", monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    background: var(--bg);
    color: var(--fg);
    font-family: var(--font);
    font-size: 15px;
    line-height: 1.5;
    min-height: 100vh;
  }
  /* ── Layout ── */
  .header {
    background: var(--bg2);
    border-bottom: 1px solid var(--border);
    padding: 14px 24px;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .header h1 { font-size: 18px; color: var(--bright); font-weight: 600; }
  .header .sub { color: var(--fg2); font-size: 13px; }
  .main { max-width: 900px; margin: 0 auto; padding: 24px; display: flex; flex-direction: column; gap: 20px; }

  /* ── Cards ── */
  .card {
    background: var(--bg2);
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
  }
  .card-header {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
    font-weight: 600;
    font-size: 13px;
    color: var(--fg2);
    text-transform: uppercase;
    letter-spacing: .05em;
    background: var(--bg);
  }
  .card-body { padding: 16px; display: flex; flex-direction: column; gap: 12px; }

  /* ── Form elements ── */
  label { font-size: 13px; color: var(--fg2); display: block; margin-bottom: 4px; }
  input[type=text], input[type=password], textarea, select {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--fg);
    font-family: var(--font);
    font-size: 14px;
    padding: 8px 12px;
    outline: none;
    transition: border-color .15s;
  }
  input[type=text]:focus, input[type=password]:focus,
  textarea:focus, select:focus {
    border-color: var(--blue);
  }
  textarea { font-family: var(--mono); font-size: 13px; resize: vertical; min-height: 180px; line-height: 1.6; }
  select { cursor: pointer; }
  .row { display: flex; gap: 8px; align-items: flex-end; }
  .row > * { flex: 1; }
  .row > button { flex: 0 0 auto; }

  /* ── Radio cards for service selection ── */
  .service-grid { display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; }
  .svc-card {
    border: 2px solid var(--border);
    border-radius: var(--radius);
    padding: 12px 14px;
    cursor: pointer;
    transition: border-color .15s, background .15s;
    user-select: none;
  }
  .svc-card:hover { border-color: var(--blue-h); background: var(--bg3); }
  .svc-card.active { border-color: var(--blue); background: #1c2a3a; }
  .svc-card input { display: none; }
  .svc-icon { font-size: 20px; margin-bottom: 6px; }
  .svc-name { font-weight: 600; font-size: 14px; color: var(--bright); }
  .svc-desc { font-size: 12px; color: var(--fg2); margin-top: 2px; }

  /* ── Buttons ── */
  button {
    border: none; border-radius: 6px; cursor: pointer;
    font-family: var(--font); font-size: 14px; font-weight: 500;
    padding: 8px 16px; transition: background .15s, opacity .15s;
    white-space: nowrap;
  }
  button:disabled { opacity: .45; cursor: not-allowed; }
  .btn-primary   { background: var(--blue);   color: #fff; }
  .btn-primary:hover:not(:disabled)   { background: var(--blue-h); }
  .btn-success   { background: var(--green);  color: #fff; }
  .btn-success:hover:not(:disabled)   { background: var(--green-h); }
  .btn-secondary { background: var(--bg3);    color: var(--fg); border: 1px solid var(--border); }
  .btn-secondary:hover:not(:disabled) { background: var(--border); }
  .btn-orange    { background: #6e4c00; color: var(--orange); border: 1px solid var(--orange); }
  .btn-orange:hover:not(:disabled)    { background: #8a6000; }
  .btn-danger    { background: transparent; color: var(--red); border: 1px solid var(--red); }
  .btn-danger:hover:not(:disabled)    { background: #3d0a09; }
  .btn-sm { padding: 5px 10px; font-size: 12px; }

  /* ── Toolbar above textarea ── */
  .toolbar { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .char-count { margin-left: auto; font-size: 12px; color: var(--fg2); }

  /* ── Action row ── */
  .action-row {
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
  }
  .action-row .spacer { flex: 1; }
  #status-msg { font-size: 13px; color: var(--accent); }

  /* ── Log panel ── */
  #log {
    background: #010409;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    font-family: var(--mono);
    font-size: 12px;
    line-height: 1.6;
    padding: 12px 14px;
    height: 180px;
    overflow-y: auto;
    color: var(--fg2);
  }
  #log .log-info   { color: var(--fg2); }
  #log .log-ok     { color: var(--accent); }
  #log .log-error  { color: var(--red); }
  #log .log-warn   { color: var(--orange); }

  /* ── Settings panel ── */
  #settings-panel { display: none; }
  #settings-panel.visible { display: flex; flex-direction: column; gap: 12px; }

  /* ── Progress bar ── */
  .progress-wrap {
    height: 4px; background: var(--bg3); border-radius: 2px;
    overflow: hidden; display: none;
  }
  .progress-wrap.visible { display: block; }
  .progress-bar {
    height: 100%; background: var(--blue);
    width: 0%; transition: width .3s;
    border-radius: 2px;
  }
  .progress-bar.indeterminate {
    width: 40%;
    animation: slide 1.2s infinite ease-in-out;
  }
  @keyframes slide {
    0%   { margin-left: -40%; }
    100% { margin-left: 100%; }
  }

  /* ── Toasts ── */
  #toast-container {
    position: fixed; bottom: 24px; right: 24px;
    display: flex; flex-direction: column; gap: 8px; z-index: 999;
  }
  .toast {
    background: var(--bg2); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 12px 16px;
    font-size: 14px; color: var(--bright);
    animation: fadein .2s ease;
    max-width: 360px;
  }
  .toast.ok    { border-left: 3px solid var(--accent); }
  .toast.error { border-left: 3px solid var(--red); }
  @keyframes fadein { from { opacity:0; transform:translateY(8px); } to { opacity:1; transform:none; } }

  @media (max-width: 600px) {
    .service-grid { grid-template-columns: 1fr; }
    .action-row { flex-direction: column; align-items: stretch; }
  }
</style>
</head>
<body>

<div class="header">
  <span style="font-size:22px">🎙</span>
  <div>
    <h1>TTS Generator</h1>
    <div class="sub">Accessibility Video Creator &mdash; runs 100% locally</div>
  </div>
</div>

<div class="main">

  <!-- Service selector -->
  <div class="card">
    <div class="card-header">TTS Service</div>
    <div class="card-body">
      <div class="service-grid">
        <label class="svc-card" id="svc-system" onclick="setSvc('system')">
          <div class="svc-icon">🖥</div>
          <div class="svc-name">System TTS</div>
          <div class="svc-desc">Offline &bull; Free &bull; No API key</div>
        </label>
        <label class="svc-card" id="svc-elevenlabs" onclick="setSvc('elevenlabs')">
          <div class="svc-icon">🎙</div>
          <div class="svc-name">ElevenLabs</div>
          <div class="svc-desc">Cloud &bull; Premium quality</div>
        </label>
        <label class="svc-card" id="svc-google" onclick="setSvc('google')">
          <div class="svc-icon">☁</div>
          <div class="svc-name">Google Cloud TTS</div>
          <div class="svc-desc">Cloud &bull; Neural2 voices</div>
        </label>
      </div>

      <!-- Settings per service -->
      <div id="settings-panel">
        <!-- system: just a note -->
        <div id="s-system" style="display:none">
          <p style="font-size:13px;color:var(--fg2)">
            Uses your Windows built-in voices (SAPI5). No internet, no API key, no character limit.<br>
            Requires: <code style="color:var(--orange)">pip install pyttsx3</code>
          </p>
        </div>
        <!-- ElevenLabs -->
        <div id="s-elevenlabs" style="display:none">
          <label>API Key</label>
          <input type="password" id="el-key" placeholder="sk-..." oninput="saveSetting('el_api_key',this.value)">
          <label style="margin-top:8px">Voice ID</label>
          <input type="text" id="el-voice" placeholder="21m00Tcm4TlvDq8ikWAM" oninput="saveSetting('el_voice_id',this.value)">
        </div>
        <!-- Google -->
        <div id="s-google" style="display:none">
          <label>API Key</label>
          <input type="password" id="g-key" placeholder="AIza..." oninput="saveSetting('google_api_key',this.value)">
          <label style="margin-top:8px">Voice Name</label>
          <input type="text" id="g-voice" placeholder="en-US-Standard-B" oninput="saveSetting('google_voice',this.value)">
        </div>
      </div>
    </div>
  </div>

  <!-- Text input -->
  <div class="card">
    <div class="card-header">Text to Speak</div>
    <div class="card-body">
      <div class="toolbar">
        <button class="btn-secondary btn-sm" onclick="loadNarration()">📄 Load narration.txt</button>
        <button class="btn-orange btn-sm" onclick="ocrFromImage()">📷 OCR from image</button>
        <button class="btn-danger btn-sm" onclick="clearText()">🗑 Clear</button>
        <span class="char-count" id="char-count">0 chars</span>
      </div>
      <textarea id="text-input" placeholder="Paste or type your narration text here, or use the buttons above..."
        oninput="updateCharCount()"></textarea>
    </div>
  </div>

  <!-- Output -->
  <div class="card">
    <div class="card-header">Output</div>
    <div class="card-body">
      <div>
        <label>Save folder</label>
        <div class="row">
          <input type="text" id="out-dir" oninput="saveSetting('output_dir',this.value)">
        </div>
      </div>
      <div>
        <label>Screenshot PNG (for video generation)</label>
        <div class="row" style="align-items:center;gap:12px">
          <button class="btn-secondary" onclick="document.getElementById('png-file-input').click()" style="flex:0 0 auto">
            📂&nbsp; Upload Screenshot PNG
          </button>
          <span id="png-filename" style="font-size:13px;color:var(--fg2)">No file selected</span>
        </div>
        <input type="file" id="png-file-input" accept="image/png,image/*" style="display:none" onchange="uploadScreenshot(this)">
      </div>
    </div>
  </div>

  <!-- Actions -->
  <div class="action-row">
    <button class="btn-primary" id="btn-audio" onclick="generate(false)">
      🎙&nbsp; Generate Audio
    </button>
    <button class="btn-success" id="btn-video" onclick="generate(true)">
      🎬&nbsp; Generate Video
    </button>
    <button class="btn-secondary" id="btn-open" onclick="openAudio()" disabled>
      ▶&nbsp; Open Audio
    </button>
    <span class="spacer"></span>
    <span id="status-msg"></span>
  </div>

  <!-- Progress bar -->
  <div class="progress-wrap" id="progress-wrap">
    <div class="progress-bar indeterminate" id="progress-bar"></div>
  </div>

  <!-- Log -->
  <div class="card">
    <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
      <span>Log</span>
      <button class="btn-secondary btn-sm" onclick="clearLog()">Clear</button>
    </div>
    <div id="log"></div>
  </div>

</div><!-- /main -->

<div id="toast-container"></div>

<!-- Hidden file input for OCR -->
<input type="file" id="ocr-file-input" accept="image/*" style="display:none" onchange="uploadOcr(this)">

<script>
// ── State ──────────────────────────────────────────────────────────────────
let cfg = {};
let lastAudioPath = null;
let logPollHandle = null;
let lastLogTime = 0;
let busy = false;

// ── Init ───────────────────────────────────────────────────────────────────
async function init() {
  const r = await fetch("/api/config");
  cfg = await r.json();
  // Populate fields
  document.getElementById("el-key").value   = cfg.el_api_key   || "";
  document.getElementById("el-voice").value = cfg.el_voice_id  || "";
  document.getElementById("g-key").value    = cfg.google_api_key || "";
  document.getElementById("g-voice").value  = cfg.google_voice  || "";
  document.getElementById("out-dir").value  = cfg.output_dir   || "";
  if (cfg.png_path) {
    window._pngServerPath = cfg.png_path;
    document.getElementById("png-filename").textContent = cfg.png_path.split(/[\\/]/).pop();
    document.getElementById("png-filename").style.color = "var(--accent)";
  }
  setSvc(cfg.service || "system", false);
  // Load narration if available
  const nr = await fetch("/api/narration");
  if (nr.ok) {
    const t = await nr.text();
    if (t) {
      document.getElementById("text-input").value = t;
      updateCharCount();
    }
  }
  startLogPoll();
}

// ── Service selector ───────────────────────────────────────────────────────
function setSvc(svc, save=true) {
  cfg.service = svc;
  ["system","elevenlabs","google"].forEach(s => {
    document.getElementById("svc-"+s).classList.toggle("active", s===svc);
    document.getElementById("s-"+s).style.display = s===svc ? "block" : "none";
  });
  document.getElementById("settings-panel").classList.toggle("visible", true);
  if (save) saveSetting("service", svc);
}

// ── Settings persistence ───────────────────────────────────────────────────
async function saveSetting(key, val) {
  cfg[key] = val;
  await fetch("/api/config", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify({key, val})
  });
}

// ── Text helpers ───────────────────────────────────────────────────────────
function updateCharCount() {
  const n = document.getElementById("text-input").value.length;
  document.getElementById("char-count").textContent = n.toLocaleString() + " chars";
}

async function loadNarration() {
  const r = await fetch("/api/narration");
  if (r.ok) {
    const t = await r.text();
    document.getElementById("text-input").value = t;
    updateCharCount();
    appendLog("Loaded narration.txt", "ok");
  } else {
    toast("narration.txt not found", "error");
  }
}

function clearText() {
  document.getElementById("text-input").value = "";
  updateCharCount();
}

// ── OCR ───────────────────────────────────────────────────────────────────
function ocrFromImage() {
  document.getElementById("ocr-file-input").click();
}

async function uploadOcr(input) {
  const file = input.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("image", file);
  setBusy(true);
  appendLog("Running OCR on: " + file.name + "...", "info");
  const r = await fetch("/api/ocr", {method:"POST", body: fd});
  const d = await r.json();
  setBusy(false);
  if (d.ok) {
    document.getElementById("text-input").value = d.text;
    updateCharCount();
    appendLog("OCR complete -- " + d.text.length.toLocaleString() + " chars extracted", "ok");
  } else {
    appendLog("OCR failed: " + d.error, "error");
    toast("OCR failed: " + d.error, "error");
  }
  input.value = "";
}

// ── Screenshot upload ──────────────────────────────────────────────────────
async function uploadScreenshot(input) {
  const file = input.files[0];
  if (!file) return;
  const label = document.getElementById("png-filename");
  label.textContent = "Uploading...";
  label.style.color = "var(--fg2)";
  const fd = new FormData();
  fd.append("screenshot", file);
  const r = await fetch("/api/upload-screenshot", {method:"POST", body: fd});
  const d = await r.json();
  if (d.ok) {
    window._pngServerPath = d.path;
    label.textContent = file.name + "  ✓";
    label.style.color = "var(--accent)";
    saveSetting("png_path", d.path);
    appendLog("Screenshot uploaded: " + d.path, "ok");
  } else {
    label.textContent = "Upload failed";
    label.style.color = "var(--red)";
    toast("Upload failed: " + d.error, "error");
  }
  input.value = "";
}

// ── Generate ──────────────────────────────────────────────────────────────
async function generate(makeVideo) {
  const text = document.getElementById("text-input").value.trim();
  if (!text) { toast("Please enter some text first.", "error"); return; }
  const pngPath = window._pngServerPath || "";
  if (makeVideo && !pngPath) { toast("Please enter the screenshot PNG path.", "error"); return; }
  setBusy(true);
  document.getElementById("status-msg").textContent = makeVideo ? "Generating audio + video..." : "Generating audio...";
  const body = {
    text,
    service:         cfg.service,
    el_api_key:      cfg.el_api_key,
    el_voice_id:     cfg.el_voice_id,
    google_api_key:  cfg.google_api_key,
    google_voice:    cfg.google_voice,
    output_dir:      document.getElementById("out-dir").value.trim(),
    png_path:        pngPath,
    make_video:      makeVideo,
  };
  const r = await fetch("/api/generate", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(body)
  });
  const d = await r.json();
  setBusy(false);
  if (d.ok) {
    lastAudioPath = d.audio_path;
    document.getElementById("btn-open").disabled = false;
    document.getElementById("status-msg").textContent = "Done: " + (d.video_path ? "audio + video saved" : "audio saved");
    toast(makeVideo ? "Video saved to Downloads!" : "Audio saved to Downloads!", "ok");
  } else {
    document.getElementById("status-msg").textContent = "Error";
    toast("Error: " + d.error, "error");
  }
}

async function openAudio() {
  if (!lastAudioPath) return;
  await fetch("/api/open", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path:lastAudioPath})});
}

// ── Log polling ────────────────────────────────────────────────────────────
function startLogPoll() {
  logPollHandle = setInterval(async () => {
    const r = await fetch("/api/logs?since=" + lastLogTime);
    const entries = await r.json();
    entries.forEach(e => {
      appendLog(e.msg, e.level);
      lastLogTime = Math.max(lastLogTime, e.t);
    });
  }, 600);
}

function appendLog(msg, level="info") {
  const el = document.getElementById("log");
  const line = document.createElement("div");
  const ts = new Date().toLocaleTimeString();
  line.className = "log-" + (level==="ok"?"ok":level==="error"?"error":level==="warn"?"warn":"info");
  line.textContent = "[" + ts + "] " + msg;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

function clearLog() {
  document.getElementById("log").innerHTML = "";
}

// ── Busy state ────────────────────────────────────────────────────────────
function setBusy(b) {
  busy = b;
  document.getElementById("btn-audio").disabled = b;
  document.getElementById("btn-video").disabled = b;
  const pw = document.getElementById("progress-wrap");
  pw.classList.toggle("visible", b);
}

// ── Toast ──────────────────────────────────────────────────────────────────
function toast(msg, type="ok") {
  const c = document.getElementById("toast-container");
  const t = document.createElement("div");
  t.className = "toast " + type;
  t.textContent = msg;
  c.appendChild(t);
  setTimeout(() => t.remove(), 4000);
}

// ── Boot ───────────────────────────────────────────────────────────────────
init();
</script>
</body>
</html>
"""

# ---------------------------------------------------------------------------
# HTTP request handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    cfg = load_cfg()
    last_audio_path = None

    def log_message(self, fmt, *args):
        pass  # suppress default access log spam

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, status=200):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length)

    # ── GET ──────────────────────────────────────────────────────────────
    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path == "/" or path == "/index.html":
            body = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/config":
            self._send_json(Handler.cfg)

        elif path == "/api/narration":
            if NARRATION_TXT.exists():
                self._send_text(NARRATION_TXT.read_text(encoding="utf-8"))
            else:
                self.send_response(404)
                self.end_headers()

        elif path == "/api/logs":
            qs    = parse_qs(parsed.query)
            since = float(qs.get("since", ["0"])[0])
            self._send_json(_pop_logs(since))

        else:
            self.send_response(404)
            self.end_headers()

    # ── POST ─────────────────────────────────────────────────────────────
    def do_POST(self):
        path = urlparse(self.path).path

        if path == "/api/config":
            data = json.loads(self._read_body())
            Handler.cfg[data["key"]] = data["val"]
            save_cfg(Handler.cfg)
            self._send_json({"ok": True})

        elif path == "/api/ocr":
            self._handle_ocr()

        elif path == "/api/upload-screenshot":
            self._handle_screenshot_upload()

        elif path == "/api/generate":
            data = json.loads(self._read_body())
            # Run in thread so we don't block the server
            result = {}
            ev = threading.Event()
            threading.Thread(target=self._generate_worker,
                             args=(data, result, ev), daemon=True).start()
            ev.wait(timeout=600)
            if result.get("ok"):
                self._send_json(result)
            else:
                self._send_json(result, 500)

        elif path == "/api/open":
            data = json.loads(self._read_body())
            p = data.get("path", "")
            if p and pathlib.Path(p).exists():
                os.startfile(p)
            self._send_json({"ok": True})

        else:
            self.send_response(404)
            self.end_headers()

    def _handle_screenshot_upload(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._send_json({"ok": False, "error": "Expected multipart"}, 400)
            return
        boundary = ctype.split("boundary=")[-1].strip().encode()
        body = self._read_body()
        img_bytes = None
        for part in body.split(b"--" + boundary):
            if b"Content-Disposition" in part and b'name="screenshot"' in part:
                header_end = part.find(b"\r\n\r\n")
                if header_end != -1:
                    img_bytes = part[header_end+4:].rstrip(b"\r\n--")
        if img_bytes is None:
            self._send_json({"ok": False, "error": "No file data found"}, 400)
            return
        save_path = str(SCRIPT_DIR / "uploaded_screenshot.png")
        try:
            with open(save_path, "wb") as f:
                f.write(img_bytes)
            size_kb = len(img_bytes) // 1024
            _push_log(f"Screenshot saved: {save_path}  ({size_kb} KB)", "ok")
            self._send_json({"ok": True, "path": save_path})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)

    def _handle_ocr(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._send_json({"ok": False, "error": "Expected multipart"}, 400)
            return
        # Parse multipart manually (minimal)
        boundary = ctype.split("boundary=")[-1].strip().encode()
        body = self._read_body()
        # Extract file bytes between boundaries
        parts = body.split(b"--" + boundary)
        img_bytes = None
        img_name  = "upload.png"
        for part in parts:
            if b"Content-Disposition" in part and b'name="image"' in part:
                header_end = part.find(b"\r\n\r\n")
                if header_end != -1:
                    img_bytes = part[header_end+4:].rstrip(b"\r\n--")
                    # Try to grab filename
                    disp = part[:header_end].decode("utf-8", errors="replace")
                    m = re.search(r'filename="([^"]+)"', disp)
                    if m:
                        img_name = m.group(1)
        if img_bytes is None:
            self._send_json({"ok": False, "error": "No image data found"}, 400)
            return
        # Write to temp file
        import tempfile
        suffix = pathlib.Path(img_name).suffix or ".png"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tf:
            tf.write(img_bytes)
            tmp_path = tf.name
        try:
            if not pathlib.Path(TESSERACT_EXE).exists():
                raise RuntimeError(
                    "Tesseract not found at: " + TESSERACT_EXE +
                    "\nInstall with: winget install UB-Mannheim.TesseractOCR"
                )
            text = ocr_image(tmp_path)
            self._send_json({"ok": True, "text": text})
        except Exception as e:
            self._send_json({"ok": False, "error": str(e)}, 500)
        finally:
            try: os.unlink(tmp_path)
            except: pass

    def _generate_worker(self, data, result, ev):
        try:
            text       = data["text"]
            svc        = data.get("service", "system")
            out_dir    = pathlib.Path(data.get("output_dir") or str(pathlib.Path.home() / "Downloads"))
            out_dir.mkdir(parents=True, exist_ok=True)
            make_video = data.get("make_video", False)
            ts         = time.strftime("%Y%m%d_%H%M%S")
            audio_ext  = ".wav" if svc == "system" else ".mp3"
            audio_out  = str(out_dir / f"tts_audio_{ts}{audio_ext}")

            _push_log(f"Generating audio via {svc}...")

            if svc == "system":
                actual = synthesize_system_tts(text, audio_out, log_fn=_push_log)
            elif svc == "elevenlabs":
                actual = synthesize_elevenlabs(
                    text, data["el_api_key"], data["el_voice_id"], audio_out, log_fn=_push_log)
            elif svc == "google":
                actual = synthesize_google(
                    text, data["google_api_key"], data["google_voice"], audio_out, log_fn=_push_log)
            else:
                raise ValueError(f"Unknown service: {svc}")

            size_kb = os.path.getsize(actual) // 1024
            _push_log(f"Audio saved: {actual}  ({size_kb} KB)", "ok")
            Handler.last_audio_path = actual
            result["ok"] = True
            result["audio_path"] = actual

            if make_video:
                png_path = data.get("png_path", "")
                if not pathlib.Path(png_path).exists():
                    raise FileNotFoundError(f"Screenshot PNG not found: {png_path}")
                if not shutil.which("ffmpeg"):
                    raise RuntimeError(
                        "ffmpeg not found on PATH.\n"
                        "Install with: winget install Gyan.FFmpeg"
                    )
                video_out = str(out_dir / f"doc_video_{ts}.mp4")
                _push_log("Starting video generation...")
                cmd = [sys.executable, "-u", str(MAKE_VIDEO_PY),
                       "--audio",      actual,
                       "--screenshot", png_path,
                       "--output",     video_out]
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        bufsize=1, encoding="utf-8", errors="replace")
                for line in proc.stdout:
                    _push_log(line.rstrip())
                proc.wait()
                if proc.returncode != 0:
                    raise RuntimeError("make_doc_video.py exited with an error")
                _push_log(f"Video saved: {video_out}", "ok")
                result["video_path"] = video_out
                _play_done_chime()

        except Exception as e:
            _push_log(f"Error: {e}", "error")
            result["ok"] = False
            result["error"] = str(e)
        finally:
            ev.set()


def _play_done_chime():
    try:
        import winsound
        for freq, dur in [(523, 120), (659, 120), (784, 120), (1047, 300)]:
            winsound.Beep(freq, dur)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="TTS Generator web interface")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    url    = f"http://127.0.0.1:{args.port}"
    print(f"TTS Generator running at  {url}")
    print("Press Ctrl+C to stop.\n")

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
