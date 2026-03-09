# Accessibility Video Creator – Android
### How the Code Works

This app takes text (typed, pasted, or dictated) and produces a narrated video: the text scrolls smoothly on screen, line by line, with the currently spoken line highlighted — like a teleprompter that also has audio. It is built with **Flutter** for the UI and a native **Kotlin** engine for video creation.

---

## High-Level Architecture

```
┌─────────────────────────────────────┐
│           Flutter UI (Dart)         │
│  - Text input                       │
│  - TTS service settings             │
│  - "Create Video" button            │
└──────────────┬──────────────────────┘
               │  MethodChannel ("video_creator")
               ▼
┌─────────────────────────────────────┐
│     Native Android (Kotlin)         │
│  - MediaCodec  → H.264 video        │
│  - MediaCodec  → AAC audio          │
│  - MediaMuxer  → MP4 container      │
│  - MediaStore  → Movies/TTS Videos/ │
└─────────────────────────────────────┘
```

The app has two separate layers that communicate over a Flutter **MethodChannel**:

1. **Dart/Flutter** — handles the UI, calls the TTS cloud API, and passes the audio file path + text to the native layer.
2. **Kotlin (Android)** — receives those paths, renders every line of text as a video frame, transcodes the audio to AAC, muxes everything into an MP4, and saves it to a public folder.

---

## Step-by-Step: What Happens When You Tap "Create Video"

### Step 1 — Text-to-Speech (Dart, `main.dart`)

Depending on which TTS service is selected in settings, one of two cloud APIs is called:

**ElevenLabs** (`elevenlabs_service.dart`):
```dart
POST https://api.elevenlabs.io/v1/text-to-speech/{voiceId}
Headers: { xi-api-key: ..., Accept: audio/mpeg }
Body:    { text, model_id: "eleven_multilingual_v2", voice_settings: {...} }
```
The response body is raw MP3 bytes, written directly to `tts_audio.mp3`.

**Google Cloud TTS** (`google_tts_service.dart`):
```dart
POST https://texttospeech.googleapis.com/v1/text:synthesize?key=...
Body: { input: {text}, voice: {languageCode, name}, audioConfig: {audioEncoding: "MP3"} }
```
The response contains a `audioContent` field which is a Base64-encoded MP3. The app decodes it and writes the bytes to `tts_audio.mp3`.

Both services produce the same output: an MP3 file on the device's external storage.

---

### Step 2 — Video Creation (Kotlin, `MainActivity.kt`)

The Dart code calls across the MethodChannel:
```dart
VideoCreatorChannel.createVideo(
  audioPath: '/path/to/tts_audio.mp3',
  videoPath: '/path/to/tts_video.mp4',
  text: 'The full text...',
)
```

The Kotlin `VideoCreator.create()` function then does four things in sequence.

#### 2a — Sentence Splitting & Layout

The text is split into sentences using a regex that breaks on `.`, `!`, or `?` followed by whitespace:
```kotlin
text.split(Regex("(?<=[.!?])\\s+"))
```

Each sentence is then word-wrapped to fit within 82% of the 1080px frame width. This produces a **full virtual canvas** — a list of every line of text with its Y position, measured as if the entire text were printed on an infinitely tall page.

#### 2b — Scroll Target Calculation

For smooth line-by-line scrolling, the app calculates a **scroll target** for every line — the Y offset needed to vertically center that line on the 1920px screen:
```kotlin
val lineScrollTargets = allLines.map {
    (it.lineY - H / 2f + LINE_HEIGHT / 2f).coerceAtLeast(0f)
}
```
The `coerceAtLeast(0f)` prevents blank space at the top for the first lines.

The total video duration (in frames) is derived from the audio file's `KEY_DURATION` metadata, ensuring the video and audio are exactly the same length.

#### 2c — H.264 Video Encoding

The video is encoded at **1080×1920 (portrait), 24 fps, 2 Mbps**. For each frame, the app:

1. Determines which line is currently "active" based on `frameIndex / framesPerLine`
2. Interpolates the current scroll position using a **cosine ease-in/out** curve during the transition between lines (0.3 seconds):
   ```kotlin
   val t = frameInLine.toFloat() / transFrames
   scrollY = prevScrollY + (target - prevScrollY) * ((1f - cos(t * PI)) / 2f)
   ```
3. Renders a `Bitmap` using Android's `Canvas` API:
   - Black background
   - All visible lines drawn in white text (38sp, centered)
   - A dark grey rounded rectangle behind the currently active line only
4. Converts the bitmap from ARGB to **NV12 (YUV 4:2:0)** format, which is what MediaCodec expects
5. Feeds the NV12 bytes into a `MediaCodec` H.264 encoder

#### 2d — Audio Transcoding

The input MP3 goes through a two-phase transcode pipeline:

**Phase 1 — Decode to PCM:**
- `MediaExtractor` opens the MP3 and detects the format
- A `MediaCodec` decoder converts compressed MP3 → raw 16-bit PCM bytes
- (For WAV/raw PCM inputs, the PCM bytes are read directly — no decoder needed)

**Phase 2 — Encode to AAC:**
- The accumulated PCM bytes are fed into a `MediaCodec` AAC-LC encoder
- Output is a list of AAC samples with presentation timestamps

#### 2e — Muxing & Saving

`MediaMuxer` combines the H.264 video samples and AAC audio samples into a single `.mp4` file.

The finished file is then copied into Android's public **Movies/TTS Videos/** folder via the `MediaStore` API (required for Android 10+):
```kotlin
val cv = ContentValues().apply {
    put(MediaStore.Video.Media.DISPLAY_NAME, "tts_video_$timestamp.mp4")
    put(MediaStore.Video.Media.RELATIVE_PATH, "Movies/TTS Videos")
    put(MediaStore.Video.Media.TITLE, "TTS Video")
    // ...
    put(MediaStore.Video.Media.IS_PENDING, 1)
}
val uri = resolver.insert(MediaStore.Video.Media.EXTERNAL_CONTENT_URI, cv)
resolver.openOutputStream(uri).use { out -> File(tempPath).inputStream().copyTo(out) }
```
The temp file is deleted after the copy. The absolute path of the new file (e.g. `/storage/emulated/0/Movies/TTS Videos/tts_video_123.mp4`) is returned to Dart so the video can be opened immediately in VLC.

---

## File Structure

```
example/
├── lib/
│   ├── main.dart                  # UI, state, TTS orchestration
│   ├── elevenlabs_service.dart    # ElevenLabs REST API client
│   ├── google_tts_service.dart    # Google Cloud TTS REST API client
│   └── video_creator_channel.dart # Flutter ↔ Kotlin MethodChannel bridge
│
└── android/app/src/main/kotlin/
    └── MainActivity.kt            # H.264/AAC encoder, MediaMuxer, MediaStore
```

---

## Key Design Decisions

**Why native Kotlin for video?**
Flutter has no built-in video encoding API. Android's `MediaCodec` and `MediaMuxer` are the lowest-level (and most performant) tools available. Doing this in Dart/FFI would be far more complex.

**Why cloud TTS instead of device TTS?**
Android's `TextToSpeech.synthesizeToFile()` runs in a separate process and is unreliable across manufacturers — particularly on Samsung devices, where it frequently times out or writes to inaccessible paths. Cloud APIs (ElevenLabs, Google TTS) are consistent and return high-quality audio every time.

**Why MediaStore instead of writing directly to `/sdcard/Movies/`?**
Android 10+ (scoped storage) blocks direct writes to public directories. `MediaStore` is the official API for saving to shared storage and ensures the file appears in gallery apps and file managers without extra permissions.

**Why NV12 for video frames?**
Android's `MediaCodec` H.264 encoder expects YUV color space, not ARGB. NV12 (a packed YUV 4:2:0 format) is the most widely supported color format across Android devices. The conversion is done in software per-frame using the standard BT.601 matrix.

---

## Settings

Settings are persisted in `SharedPreferences`:

| Key | Description |
|---|---|
| `tts_service` | `"elevenlabs"` or `"google"` |
| `el_api_key` | ElevenLabs API key |
| `el_voice_id` | ElevenLabs voice ID (default: Rachel) |
| `google_api_key` | Google Cloud TTS API key |
| `google_voice_name` | Google voice name (default: `en-US-Neural2-F`) |

---

## Getting Started

### Prerequisites
- Flutter SDK
- Android Studio (for the Android SDK / build tools)
- An ElevenLabs API key (elevenlabs.io) **or** a Google Cloud TTS API key (console.cloud.google.com)

### Build & Run
```bash
cd example
flutter build apk --release
adb install -r build/app/outputs/flutter-apk/app-release.apk
```

### Configure TTS
Tap the **gear icon** in the top-right corner, select your TTS service (ElevenLabs or Google TTS), enter your API key, and tap Save.

### Create a Video
1. Type, paste, or dictate your text into the input field
2. Tap **Create Video**
3. The video is saved to **Movies/TTS Videos/** on your device and opens automatically in your default video player
