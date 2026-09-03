"""Transcribe audio into word-level lyric timings."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 16000


@dataclass
class LyricWord:
    text: str
    start: float
    end: float

    def to_dict(self) -> dict:
        return {"text": self.text, "start": self.start, "end": self.end}


@dataclass
class LyricsResult:
    words: list[LyricWord]
    duration: float
    transcript: str
    language: str | None = None

    def to_dict(self) -> dict:
        return {
            "duration": round(self.duration, 3),
            "transcript": self.transcript,
            "language": self.language,
            "words": [word.to_dict() for word in self.words],
        }


_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        _model = WhisperModel("base", device="cpu", compute_type="int8")
    return _model


def _to_wav(audio_path: Path) -> Path:
    """Normalize any audio file to 16 kHz mono WAV for Whisper."""
    if audio_path.suffix.lower() == ".wav":
        return audio_path

    tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            str(tmp),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Failed to convert audio to WAV")
    return tmp


def get_audio_duration(audio_path: str | Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "Failed to read audio duration")
    return float(proc.stdout.strip())


def transcribe_audio(
    audio_path: str | Path,
    *,
    language: str | None = None,
) -> LyricsResult:
    """Transcribe an audio file and return word-level lyric timings."""
    audio_path = Path(audio_path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio not found: {audio_path}")

    duration = get_audio_duration(audio_path)
    wav_path = _to_wav(audio_path)
    cleanup = wav_path != audio_path

    try:
        model = _get_model()
        segments, info = model.transcribe(
            str(wav_path),
            word_timestamps=True,
            language=language,
            # Sung lyrics often get dropped by VAD; keep full clip for music.
            vad_filter=False,
        )

        words: list[LyricWord] = []
        transcript_parts: list[str] = []
        for segment in segments:
            if segment.text:
                transcript_parts.append(segment.text.strip())
            if not segment.words:
                continue
            for word in segment.words:
                text = (word.word or "").strip()
                if not text:
                    continue
                words.append(
                    LyricWord(
                        text=text,
                        start=round(word.start, 3),
                        end=round(word.end, 3),
                    )
                )

        transcript = " ".join(transcript_parts).strip()
        if not words and transcript:
            # Fallback: one phrase spanning the clip if Whisper returns no words.
            words = [LyricWord(text=transcript, start=0.0, end=duration)]

        return LyricsResult(
            words=words,
            duration=duration,
            transcript=transcript,
            language=getattr(info, "language", None),
        )
    finally:
        if cleanup and wav_path.exists():
            wav_path.unlink(missing_ok=True)


def load_audio_for_mux(audio_path: str | Path, sample_rate: int = 44100) -> tuple[np.ndarray, int]:
    """Load source audio at the sample rate used in the final video."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-ac",
            "2",
            "-ar",
            str(sample_rate),
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(proc.stderr.strip() or "Failed to load audio")

    try:
        audio, sr = sf.read(tmp_path, dtype="float32", always_2d=True)
        return audio, sr
    finally:
        tmp_path.unlink(missing_ok=True)
