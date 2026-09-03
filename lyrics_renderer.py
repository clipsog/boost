"""Render transparent lyric caption videos for TikTok overlays."""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

from lyrics_engine import LyricWord, LyricsResult, load_audio_for_mux

SENTENCE_END = re.compile(r"[.!?…]$")

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


@dataclass
class LyricsStyle:
    width: int = 1080
    height: int = 1920
    fps: int = 30
    words_per_phrase: int = 4
    font_size: int = 72
    idle_color: tuple[int, int, int] = (160, 160, 160)
    spoken_color: tuple[int, int, int] = (255, 255, 255)
    active_color: tuple[int, int, int] = (255, 204, 0)
    stroke_color: tuple[int, int, int] = (0, 0, 0)
    stroke_width: int = 4
    text_y_ratio: float = 0.72


def build_phrases(words: list[LyricWord], max_words: int = 4) -> list[list[LyricWord]]:
    groups: list[list[LyricWord]] = []
    current: list[LyricWord] = []
    for word in words:
        current.append(word)
        if len(current) >= max_words or SENTENCE_END.search(word.text):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def visual_segments(words: list[LyricWord]) -> list[tuple[float, float, float]]:
    if not words:
        return [(0.0, 0.05, 0.0)]
    boundaries = sorted({0.0, words[-1].end + 0.02} | {w.start for w in words} | {w.end for w in words})
    segments: list[tuple[float, float, float]] = []
    for index in range(len(boundaries) - 1):
        start, end = boundaries[index], boundaries[index + 1]
        if end - start < 0.01:
            continue
        segments.append((start, end, start + (end - start) * 0.5))
    return segments or [(0.0, 0.05, 0.0)]


def _find_phrase_index(phrases: list[list[LyricWord]], time: float) -> int:
    for index, phrase in enumerate(phrases):
        if phrase[0].start <= time <= phrase[-1].end + 0.05:
            return index
    for index in range(len(phrases) - 1, -1, -1):
        if time >= phrases[index][0].start:
            return index
    return 0


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _word_color(word: LyricWord, time: float, style: LyricsStyle) -> tuple[int, int, int]:
    if time >= word.end:
        return style.spoken_color
    if time >= word.start:
        return style.active_color
    return style.idle_color


def _draw_stroked_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: tuple[int, int, int, int],
    stroke: tuple[int, int, int],
    stroke_width: int,
) -> None:
    x, y = xy
    for dx in range(-stroke_width, stroke_width + 1):
        for dy in range(-stroke_width, stroke_width + 1):
            if dx * dx + dy * dy > stroke_width * stroke_width:
                continue
            draw.text((x + dx, y + dy), text, font=font, fill=(*stroke, 255))
    draw.text((x, y), text, font=font, fill=fill)


def _render_caption_phrase(
    phrase: list[LyricWord],
    time: float,
    style: LyricsStyle,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> Image.Image:
    overlay = Image.new("RGBA", (style.width, style.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    gap = int(style.font_size * 0.28)
    words = [(word.text.upper(), _word_color(word, time, style)) for word in phrase]
    sizes = [draw.textbbox((0, 0), text, font=font) for text, _ in words]
    widths = [box[2] - box[0] for box in sizes]
    heights = [box[3] - box[1] for box in sizes]
    total_width = sum(widths) + gap * max(len(words) - 1, 0)
    x = (style.width - total_width) // 2
    y = int(style.height * style.text_y_ratio) - max(heights) // 2

    for (text, color), width in zip(words, widths):
        _draw_stroked_text(
            draw,
            (x, y),
            text,
            font,
            (*color, 255),
            style.stroke_color,
            style.stroke_width,
        )
        x += width + gap
    return overlay


class LyricsRenderer:
    def __init__(
        self,
        words: list[LyricWord],
        style: LyricsStyle | None = None,
        *,
        background: tuple[int, int, int, int] = (0, 0, 0, 0),
    ):
        self.style = style or LyricsStyle()
        self.phrases = build_phrases(words, max_words=self.style.words_per_phrase)
        self.font = _load_font(self.style.font_size)
        self._background = Image.new("RGBA", (self.style.width, self.style.height), background)
        self._frame_cache: dict[tuple[int, tuple[tuple[int, int, int], ...]], Image.Image] = {}

    def render_frame_rgba(self, time: float) -> Image.Image:
        if not self.phrases:
            return self._background.copy()
        phrase_idx = _find_phrase_index(self.phrases, time)
        phrase = self.phrases[phrase_idx]
        colors = tuple(_word_color(word, time, self.style) for word in phrase)
        cache_key = (phrase_idx, colors)
        if cache_key not in self._frame_cache:
            overlay = _render_caption_phrase(phrase, time, self.style, self.font)
            frame = self._background.copy()
            self._frame_cache[cache_key] = Image.alpha_composite(frame, overlay)
        return self._frame_cache[cache_key]


def _encode_args(fmt: str) -> tuple[str, list[str], str]:
    """Return container suffix, ffmpeg video/audio args, and frame extension."""
    if fmt in {"hevc", "prores"}:
        return (
            ".mov",
            [
                "-c:v",
                "prores_ks",
                "-profile:v",
                "4444",
                "-pix_fmt",
                "yuva444p10le",
                "-c:a",
                "aac",
            ],
            ".png",
        )
    if fmt in {"tiktok", "greenscreen"}:
        return (
            ".mp4",
            [
                "-c:v",
                "h264_videotoolbox",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
            ],
            ".png",
        )
    if fmt == "webm":
        return (
            ".webm",
            [
                "-c:v",
                "libvpx-vp9",
                "-pix_fmt",
                "yuva420p",
                "-deadline",
                "realtime",
                "-cpu-used",
                "8",
                "-c:a",
                "libopus",
            ],
            ".png",
        )
    if fmt == "mov":
        return (".mov", ["-c:v", "qtrle", "-pix_fmt", "argb", "-c:a", "pcm_s16le"], ".png")
    return (
        ".mp4",
        [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
        ],
        ".jpg",
    )


def _write_video(
    lyrics: LyricsResult,
    audio_path: Path,
    output_path: Path,
    *,
    renderer: LyricsRenderer,
    segments: list[tuple[float, float, float]],
    fmt: str,
) -> None:
    _, _, ext = _encode_args(fmt)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        concat_lines: list[str] = []
        last_path: Path | None = None

        for index, (start, end, render_time) in enumerate(segments):
            duration = end - start
            if duration <= 0:
                continue
            frame = renderer.render_frame_rgba(render_time)
            img_path = tmp / f"frame_{index:04d}{ext}"
            if ext == ".jpg":
                frame.convert("RGB").save(img_path, quality=88)
            else:
                frame.save(img_path, compress_level=1)
            concat_lines.append(f"file '{img_path.as_posix()}'")
            concat_lines.append(f"duration {duration:.6f}")
            last_path = img_path

        if last_path is not None:
            concat_lines.append(f"file '{last_path.as_posix()}'")

        concat_file = tmp / "concat.txt"
        concat_file.write_text("\n".join(concat_lines), encoding="utf-8")

        audio, sample_rate = load_audio_for_mux(audio_path, sample_rate=44100)
        wav_path = tmp / "audio.wav"
        sf.write(wav_path, audio, sample_rate)

        cmd = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            "-i",
            str(wav_path),
            *_encode_args(fmt)[1],
            "-shortest",
            str(output_path),
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffmpeg failed to encode video")


def render_lyrics_video(
    lyrics: LyricsResult,
    audio_path: str | Path,
    output_path: str | Path,
    *,
    style: LyricsStyle | None = None,
    fmt: str | None = None,
) -> Path:
    """Render a lyric caption video.

    Formats:
    - hevc / prores: transparent MOV for iPhone gallery / CapCut (ProRes 4444)
    - tiktok: black-background MP4 with white text for direct TikTok upload
    - webm: transparent WebM for desktop editors
    """
    style = style or LyricsStyle()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = fmt or output_path.suffix.lower().lstrip(".") or "hevc"
    if fmt == "greenscreen":
        fmt = "tiktok"

    if fmt == "tiktok":
        background = (0, 0, 0, 255)
        style = LyricsStyle(
            width=style.width,
            height=style.height,
            fps=style.fps,
            words_per_phrase=style.words_per_phrase,
            font_size=style.font_size,
            idle_color=(255, 255, 255),
            spoken_color=(255, 255, 255),
            active_color=(255, 255, 255),
            stroke_color=(0, 0, 0),
            stroke_width=0,
            text_y_ratio=style.text_y_ratio,
        )
    else:
        background = (0, 0, 0, 0)

    renderer = LyricsRenderer(lyrics.words, style=style, background=background)
    segments = visual_segments(lyrics.words)
    _write_video(lyrics, Path(audio_path), output_path, renderer=renderer, segments=segments, fmt=fmt)
    return output_path


def render_lyrics_video_bytes(
    lyrics: LyricsResult,
    audio_path: str | Path,
    *,
    style: LyricsStyle | None = None,
    fmt: str = "hevc",
) -> bytes:
    suffix = _encode_args(fmt)[0]
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / f"lyrics{suffix}"
        render_lyrics_video(lyrics, audio_path, out, style=style, fmt=fmt)
        return out.read_bytes()
