#!/usr/bin/env python3
"""Generate a Kokoro narration with word-highlighted captions burned into MP4."""

from __future__ import annotations

import argparse
from pathlib import Path

from caption_video import CaptionStyle, render_caption_video
from kokoro_engine import synthesize_with_timings, to_srt


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Kokoro speech + highlighted-caption MP4")
    parser.add_argument("text", nargs="?", help="Text to speak (or use --file)")
    parser.add_argument("-f", "--file", type=Path, help="Read text from a file")
    parser.add_argument("-o", "--output", type=Path, default=Path("output.mp4"), help="Output MP4 path")
    parser.add_argument("-v", "--voice", default="am_michael", help="Kokoro voice id (default: am_michael)")
    parser.add_argument("-s", "--speed", type=float, default=0.85, help="Speech speed (default: 0.85)")
    parser.add_argument("--background", type=Path, help="Optional background video to loop/trim")
    parser.add_argument("--srt", type=Path, help="Also write word-level SRT to this path")
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()

    if args.file:
        text = args.file.read_text(encoding="utf-8").strip()
    elif args.text:
        text = args.text.strip()
    else:
        parser.error("Provide text or --file")

    print("Generating speech and word timings…")
    result = synthesize_with_timings(text, voice=args.voice, speed=args.speed)
    if args.srt:
        args.srt.write_text(to_srt(result.words), encoding="utf-8")
        print(f"Wrote {args.srt}")

    style = CaptionStyle(width=args.width, height=args.height, fps=args.fps)
    print(f"Rendering caption video ({style.width}x{style.height} @ {style.fps}fps)…")
    render_caption_video(
        result,
        args.output,
        background_video=args.background,
        style=style,
    )
    print(f"Done: {args.output.resolve()}")


if __name__ == "__main__":
    main()
