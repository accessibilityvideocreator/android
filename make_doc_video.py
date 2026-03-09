#!/usr/bin/env python3
"""
make_doc_video.py
-----------------
Takes HOW_IT_WORKS_screenshot.png (the long rendered doc image) and an MP3
audio file, and produces a 1080x1920 portrait MP4 that slowly pans/scrolls
through the document image while the narration plays.

Usage:
    python make_doc_video.py --audio path/to/narration.mp3

The output is saved to Movies/TTS Videos/ via adb, or just to the current
folder as doc_video.mp4 if adb is not available.

Requirements:  pip install Pillow   (ffmpeg must be on PATH)
"""

import argparse
import math
import os
import subprocess
import sys
import tempfile
import struct

from PIL import Image

# ── Config ────────────────────────────────────────────────────────────────────
OUT_W, OUT_H = 1080, 1920   # output video dimensions (portrait)
FPS          = 24
TITLE_HOLD   = 1.5          # seconds to hold on very top before scrolling
END_HOLD     = 2.0          # seconds to hold at bottom before ending

SCREENSHOT   = os.path.join(os.path.dirname(__file__), "HOW_IT_WORKS_screenshot.png")
OUTPUT_VIDEO = os.path.join(os.path.dirname(__file__), "doc_video.mp4")


# ── Audio helpers ──────────────────────────────────────────────────────────────
def get_audio_duration_seconds(audio_path: str) -> float:
    """Use ffprobe to get duration of any audio file."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", audio_path],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise RuntimeError(f"Could not determine audio duration for: {audio_path}\n{result.stderr}")


# ── Frame generator ───────────────────────────────────────────────────────────
def ease_in_out(t: float) -> float:
    """Cosine ease-in/out: smooth S-curve from 0→1."""
    return (1.0 - math.cos(t * math.pi)) / 2.0


def crop_frame(doc_img: Image.Image, scroll_y: float) -> Image.Image:
    """Crop a 1080×1920 slice from the tall doc image at vertical offset scroll_y."""
    y0 = int(round(scroll_y))
    y1 = y0 + OUT_H
    # Handle case where doc is shorter than the output window (shouldn't happen)
    img_h = doc_img.height
    if y1 > img_h:
        y0 = max(0, img_h - OUT_H)
        y1 = img_h
    frame = doc_img.crop((0, y0, OUT_W, y1))
    if frame.height < OUT_H:
        # pad bottom with bg colour
        padded = Image.new("RGB", (OUT_W, OUT_H), (13, 17, 23))
        padded.paste(frame, (0, 0))
        return padded
    return frame


def generate_frames(doc_img: Image.Image, total_duration: float, tmp_dir: str):
    """Yield frame PNG paths by rendering each frame from the doc image."""
    total_frames  = int(math.ceil(total_duration * FPS))
    title_frames  = int(TITLE_HOLD * FPS)
    end_frames    = int(END_HOLD * FPS)
    scroll_frames = max(1, total_frames - title_frames - end_frames)

    max_scroll    = max(0, doc_img.height - OUT_H)

    print(f"  Doc image:    {doc_img.width}×{doc_img.height}px")
    print(f"  Total frames: {total_frames}  ({total_duration:.1f}s @ {FPS}fps)")
    print(f"  Max scroll:   {max_scroll}px")

    frame_idx = 0

    def save_frame(img):
        nonlocal frame_idx
        path = os.path.join(tmp_dir, f"frame_{frame_idx:06d}.png")
        img.save(path, "PNG")
        frame_idx += 1
        if frame_idx % (FPS * 5) == 0:
            pct = frame_idx / total_frames * 100
            print(f"    rendered {frame_idx}/{total_frames} frames ({pct:.0f}%)", flush=True)

    # — Title hold (scroll_y = 0) ——————————————————————————————————————
    top_frame = crop_frame(doc_img, 0)
    for _ in range(title_frames):
        save_frame(top_frame)

    # — Scroll ——————————————————————————————————————————————————————————
    for i in range(scroll_frames):
        t = i / scroll_frames               # 0 → 1
        eased = ease_in_out(t)
        scroll_y = eased * max_scroll
        save_frame(crop_frame(doc_img, scroll_y))

    # — End hold (scroll_y = max_scroll) ————————————————————————————————
    bot_frame = crop_frame(doc_img, max_scroll)
    for _ in range(end_frames):
        save_frame(bot_frame)

    print(f"\n  ✓ {frame_idx} frames rendered")
    return frame_idx


# ── Encode ────────────────────────────────────────────────────────────────────
def encode_video(tmp_dir: str, audio_path: str, output_path: str):
    """Use ffmpeg to encode PNG frames + audio into MP4."""
    frame_pattern = os.path.join(tmp_dir, "frame_%06d.png")

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(FPS),
        "-i", frame_pattern,
        "-i", audio_path,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",       # trim to whichever stream ends first
        output_path
    ]

    print(f"\n  Encoding with ffmpeg...", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print("ffmpeg stderr:", result.stderr[-2000:], flush=True)
        raise RuntimeError("ffmpeg encoding failed")
    print(f"  ✓ Video saved: {output_path}", flush=True)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Scroll a tall PNG image into a portrait MP4 with audio.")
    parser.add_argument("--audio",      default=None, help="Path to MP3/WAV audio file (narration)")
    parser.add_argument("--screenshot", default=SCREENSHOT, help="Path to the tall PNG screenshot")
    parser.add_argument("--output",     default=OUTPUT_VIDEO, help="Output MP4 path")
    parser.add_argument("--duration",   type=float, default=None,
                        help="Override total video duration in seconds (default: audio duration)")
    args = parser.parse_args()

    # Validate inputs
    if not os.path.exists(args.screenshot):
        print(f"ERROR: Screenshot not found: {args.screenshot}")
        sys.exit(1)

    if args.audio and not os.path.exists(args.audio):
        print(f"ERROR: Audio file not found: {args.audio}")
        sys.exit(1)

    # Determine duration
    if args.duration:
        duration = args.duration
    elif args.audio:
        print(f"Getting audio duration: {args.audio}")
        duration = get_audio_duration_seconds(args.audio)
        print(f"  Audio duration: {duration:.2f}s")
    else:
        duration = 60.0
        print(f"No audio provided — using default duration of {duration}s")

    # Load doc image
    print(f"\nLoading screenshot: {args.screenshot}")
    doc_img = Image.open(args.screenshot).convert("RGB")
    # Make sure it's exactly OUT_W wide
    if doc_img.width != OUT_W:
        new_h = int(doc_img.height * OUT_W / doc_img.width)
        doc_img = doc_img.resize((OUT_W, new_h), Image.LANCZOS)
        print(f"  Resized to {doc_img.width}×{doc_img.height}")

    # Render frames
    with tempfile.TemporaryDirectory() as tmp_dir:
        print(f"\nRendering frames to temp dir...")
        generate_frames(doc_img, duration, tmp_dir)

        if args.audio:
            encode_video(tmp_dir, args.audio, args.output)
        else:
            # No audio: encode video-only
            frame_pattern = os.path.join(tmp_dir, "frame_%06d.png")
            cmd = [
                "ffmpeg", "-y",
                "-framerate", str(FPS),
                "-i", frame_pattern,
                "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                args.output
            ]
            print("\nEncoding video (no audio)...")
            subprocess.run(cmd, check=True, capture_output=True)
            print(f"  ✓ Video saved: {args.output}")

    size_mb = os.path.getsize(args.output) / 1024 / 1024
    print(f"\n✅ Done!  {args.output}  ({size_mb:.1f} MB)")
    print(f"\nTo copy to phone:")
    print(f"  adb push \"{args.output}\" /sdcard/Movies/TTS\\ Videos/")


if __name__ == "__main__":
    main()
