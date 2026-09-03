"""Render word-highlighted caption videos from Kokoro timings."""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

from kokoro_engine import SAMPLE_RATE, SynthesisResult, WordTiming

SENTENCE_END = re.compile(r"[.!?…]$")

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
]


@dataclass
class CaptionStyle:
    width: int = 1280
    height: int = 720
    fps: int = 12
    words_per_phrase: int = 4
    font_size: int = 58
    idle_color: tuple[int, int, int] = (110, 110, 110)
    spoken_color: tuple[int, int, int] = (255, 255, 255)
    active_color: tuple[int, int, int] = (255, 204, 0)
    text_y_ratio: float = 0.62
    fast: bool = True


def build_phrases(words: list[WordTiming], max_words: int = 4) -> list[list[WordTiming]]:
    groups: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    for word in words:
        current.append(word)
        if len(current) >= max_words or SENTENCE_END.search(word.text):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def visual_segments(words: list[WordTiming]) -> list[tuple[float, float, float]]:
    """Return (start, end, render_time) slices where caption appearance is constant."""
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


def _find_phrase_index(phrases: list[list[WordTiming]], time: float) -> int:
    for index, phrase in enumerate(phrases):
        start = phrase[0].start
        end = phrase[-1].end
        if start <= time <= end + 0.05:
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


def _word_color(word: WordTiming, time: float, style: CaptionStyle) -> tuple[int, int, int]:
    if time >= word.end:
        return style.spoken_color
    if time >= word.start:
        return style.active_color
    return style.idle_color


def _render_gradient_background(width: int, height: int) -> Image.Image:
    arr = np.zeros((height, width, 3), dtype=np.uint8)
    top = np.array([9, 11, 16], dtype=np.float32)
    mid = np.array([17, 20, 28], dtype=np.float32)
    bottom = np.array([8, 9, 13], dtype=np.float32)
    ys = np.linspace(0, 1, height, dtype=np.float32)
    for y, ratio in enumerate(ys):
        color = top + (mid - top) * (ratio / 0.55) if ratio < 0.55 else mid + (bottom - mid) * ((ratio - 0.55) / 0.45)
        arr[y, :] = np.clip(color, 0, 255).astype(np.uint8)
    return Image.fromarray(arr, mode="RGB").convert("RGBA")


def _render_caption_phrase(
    phrase: list[WordTiming],
    time: float,
    style: CaptionStyle,
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
        if not style.fast and color == style.active_color:
            glow = Image.new("RGBA", (width + 40, max(heights) + 40), (0, 0, 0, 0))
            gdraw = ImageDraw.Draw(glow)
            gdraw.text((20, 20), text, font=font, fill=(255, 204, 0, 90))
            overlay.alpha_composite(glow, (x - 20, y - 20))
        draw.text((x, y), text, font=font, fill=(*color, 255))
        x += width + gap
    return overlay


class CaptionRenderer:
    def __init__(self, words: list[WordTiming], style: CaptionStyle | None = None, transparent: bool = False):
        self.style = style or CaptionStyle()
        self.phrases = build_phrases(words, max_words=self.style.words_per_phrase)
        self.font = _load_font(self.style.font_size)
        if transparent:
            self._background = Image.new("RGBA", (self.style.width, self.style.height), (0, 0, 0, 0))
        else:
            self._background = _render_gradient_background(self.style.width, self.style.height)
        self._frame_cache: dict[tuple[int, tuple[tuple[int, int, int], ...]], Image.Image] = {}

    def render_frame_rgba(self, time: float) -> Image.Image:
        phrase_idx = _find_phrase_index(self.phrases, time)
        phrase = self.phrases[phrase_idx]
        colors = tuple(_word_color(word, time, self.style) for word in phrase)
        cache_key = (phrase_idx, colors)
        if cache_key not in self._frame_cache:
            overlay = _render_caption_phrase(phrase, time, self.style, self.font)
            frame = self._background.copy()
            self._frame_cache[cache_key] = Image.alpha_composite(frame, overlay)
        return self._frame_cache[cache_key]

    def render_frame(self, time: float) -> np.ndarray:
        return np.asarray(self.render_frame_rgba(time).convert("RGB"))


def _write_concat_video(
    result: SynthesisResult,
    output_path: Path,
    *,
    renderer: CaptionRenderer,
    segments: list[tuple[float, float, float]],
    fmt: str,
) -> None:
    is_webm = fmt == "webm"
    is_mov = fmt == "mov"
    ext = ".jpg" if not (is_webm or is_mov) else ".png"

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
                frame.convert("RGB").save(img_path, quality=88, optimize=False)
            else:
                frame.save(img_path, compress_level=1)
            concat_lines.append(f"file '{img_path.as_posix()}'")
            concat_lines.append(f"duration {duration:.6f}")
            last_path = img_path

        if last_path is not None:
            concat_lines.append(f"file '{last_path.as_posix()}'")

        concat_file = tmp / "concat.txt"
        concat_file.write_text("\n".join(concat_lines), encoding="utf-8")
        wav_path = tmp / "audio.wav"
        sf.write(wav_path, result.audio, SAMPLE_RATE)

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
        ]

        if is_webm:
            cmd.extend(
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
                ]
            )
        elif is_mov:
            cmd.extend(["-c:v", "qtrle", "-pix_fmt", "argb", "-c:a", "pcm_s16le"])
        else:
            cmd.extend(
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
                ]
            )

        cmd.extend(["-shortest", str(output_path)])
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffmpeg failed to encode video")


def render_caption_video(
    result: SynthesisResult,
    output_path: str | Path,
    *,
    background_video: str | Path | None = None,
    style: CaptionStyle | None = None,
) -> Path:
    """Burn Kokoro-synced word highlights into a video file."""
    style = style or CaptionStyle()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fmt = output_path.suffix.lower().lstrip(".") or "mp4"
    is_transparent = fmt in {"webm", "mov"}

    if background_video and Path(background_video).exists():
        from moviepy import AudioFileClip, VideoClip, VideoFileClip, concatenate_videoclips

        renderer = CaptionRenderer(result.words, style=style, transparent=False)
        duration = max(result.duration, len(result.audio) / SAMPLE_RATE)
        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "narration.wav"
            sf.write(wav_path, result.audio, SAMPLE_RATE)
            audio_clip = AudioFileClip(str(wav_path))
            bg = VideoFileClip(str(background_video))
            if bg.duration < duration:
                loops = int(duration // bg.duration) + 1
                bg = concatenate_videoclips([bg] * loops).subclipped(0, duration)
            else:
                bg = bg.subclipped(0, duration)

            def make_frame(t: float):
                base = Image.fromarray(bg.get_frame(t)).convert("RGBA")
                phrase = renderer.phrases[_find_phrase_index(renderer.phrases, t)]
                overlay = _render_caption_phrase(phrase, t, style, renderer.font)
                return np.asarray(Image.alpha_composite(base, overlay).convert("RGB"))

            video_clip = VideoClip(make_frame, duration=duration).with_fps(style.fps)
            final = video_clip.with_audio(audio_clip)
            final.write_videofile(
                str(output_path),
                codec="libx264",
                audio_codec="aac",
                fps=style.fps,
                preset="ultrafast",
                logger=None,
            )
            video_clip.close()
            audio_clip.close()
            final.close()
            bg.close()
        return output_path

    renderer = CaptionRenderer(result.words, style=style, transparent=is_transparent)
    segments = visual_segments(result.words)
    _write_concat_video(result, output_path, renderer=renderer, segments=segments, fmt=fmt)
    return output_path


def render_caption_video_bytes(
    result: SynthesisResult,
    *,
    background_video: str | Path | None = None,
    style: CaptionStyle | None = None,
    fmt: str = "mp4",
) -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        suffix = ".mp4" if fmt == "mp4" else (".mov" if fmt == "mov" else ".webm")
        out = Path(tmpdir) / f"caption_video{suffix}"
        render_caption_video(result, out, background_video=background_video, style=style)
        return out.read_bytes()
