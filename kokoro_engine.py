"""Kokoro-82M text-to-speech engine."""

from __future__ import annotations

import io
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

SAMPLE_RATE = 24000
PUNCT_ONLY = re.compile(r"^[\.,!\?;:\"'\)\(\-—…]+$")

# All Kokoro v1.0 voices (hexgrad/Kokoro-82M).
VOICES: list[dict[str, str]] = [
    # American English — female
    {"id": "af_alloy", "label": "Alloy", "group": "American English (female)"},
    {"id": "af_aoede", "label": "Aoede", "group": "American English (female)"},
    {"id": "af_bella", "label": "Bella", "group": "American English (female)"},
    {"id": "af_heart", "label": "Heart", "group": "American English (female)"},
    {"id": "af_jessica", "label": "Jessica", "group": "American English (female)"},
    {"id": "af_kore", "label": "Kore", "group": "American English (female)"},
    {"id": "af_nicole", "label": "Nicole", "group": "American English (female)"},
    {"id": "af_nova", "label": "Nova", "group": "American English (female)"},
    {"id": "af_river", "label": "River", "group": "American English (female)"},
    {"id": "af_sarah", "label": "Sarah", "group": "American English (female)"},
    {"id": "af_sky", "label": "Sky", "group": "American English (female)"},
    # American English — male
    {"id": "am_adam", "label": "Adam", "group": "American English (male)"},
    {"id": "am_echo", "label": "Echo", "group": "American English (male)"},
    {"id": "am_eric", "label": "Eric", "group": "American English (male)"},
    {"id": "am_fenrir", "label": "Fenrir", "group": "American English (male)"},
    {"id": "am_liam", "label": "Liam", "group": "American English (male)"},
    {"id": "am_michael", "label": "Michael", "group": "American English (male)"},
    {"id": "am_onyx", "label": "Onyx", "group": "American English (male)"},
    {"id": "am_puck", "label": "Puck", "group": "American English (male)"},
    {"id": "am_santa", "label": "Santa", "group": "American English (male)"},
    # British English — female
    {"id": "bf_alice", "label": "Alice", "group": "British English (female)"},
    {"id": "bf_emma", "label": "Emma", "group": "British English (female)"},
    {"id": "bf_isabella", "label": "Isabella", "group": "British English (female)"},
    {"id": "bf_lily", "label": "Lily", "group": "British English (female)"},
    # British English — male
    {"id": "bm_daniel", "label": "Daniel", "group": "British English (male)"},
    {"id": "bm_fable", "label": "Fable", "group": "British English (male)"},
    {"id": "bm_george", "label": "George", "group": "British English (male)"},
    {"id": "bm_lewis", "label": "Lewis", "group": "British English (male)"},
    # Spanish
    {"id": "ef_dora", "label": "Dora", "group": "Spanish (female)"},
    {"id": "em_alex", "label": "Alex", "group": "Spanish (male)"},
    {"id": "em_santa", "label": "Santa", "group": "Spanish (male)"},
    # French
    {"id": "ff_siwis", "label": "Siwis", "group": "French (female)"},
    # Hindi
    {"id": "hf_alpha", "label": "Alpha", "group": "Hindi (female)"},
    {"id": "hf_beta", "label": "Beta", "group": "Hindi (female)"},
    {"id": "hm_omega", "label": "Omega", "group": "Hindi (male)"},
    {"id": "hm_psi", "label": "Psi", "group": "Hindi (male)"},
    # Italian
    {"id": "if_sara", "label": "Sara", "group": "Italian (female)"},
    {"id": "im_nicola", "label": "Nicola", "group": "Italian (male)"},
    # Japanese (requires: pip install misaki[ja])
    {"id": "jf_alpha", "label": "Alpha", "group": "Japanese (female)"},
    {"id": "jf_gongitsune", "label": "Gongitsune", "group": "Japanese (female)"},
    {"id": "jf_nezumi", "label": "Nezumi", "group": "Japanese (female)"},
    {"id": "jf_tebukuro", "label": "Tebukuro", "group": "Japanese (female)"},
    {"id": "jm_kumo", "label": "Kumo", "group": "Japanese (male)"},
    # Portuguese
    {"id": "pf_dora", "label": "Dora", "group": "Portuguese (female)"},
    {"id": "pm_alex", "label": "Alex", "group": "Portuguese (male)"},
    {"id": "pm_santa", "label": "Santa", "group": "Portuguese (male)"},
    # Mandarin (requires: pip install misaki[zh])
    {"id": "zf_xiaobei", "label": "Xiaobei", "group": "Mandarin (female)"},
    {"id": "zf_xiaoni", "label": "Xiaoni", "group": "Mandarin (female)"},
    {"id": "zf_xiaoxiao", "label": "Xiaoxiao", "group": "Mandarin (female)"},
    {"id": "zf_xiaoyi", "label": "Xiaoyi", "group": "Mandarin (female)"},
    {"id": "zm_yunjian", "label": "Yunjian", "group": "Mandarin (male)"},
    {"id": "zm_yunxi", "label": "Yunxi", "group": "Mandarin (male)"},
    {"id": "zm_yunxia", "label": "Yunxia", "group": "Mandarin (male)"},
    {"id": "zm_yunyang", "label": "Yunyang", "group": "Mandarin (male)"},
]

VOICE_IDS = {voice["id"] for voice in VOICES}

_pipelines: dict[str, object] = {}
_synthesis_cache: dict[str, "SynthesisResult"] = {}
_MAX_SYNTHESIS_CACHE = 32


def _best_device() -> str | None:
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return None


def _cache_key(text: str, voice: str, speed: float) -> str:
    return f"{voice}\0{speed:.4f}\0{text}"


def clear_synthesis_cache() -> None:
    _synthesis_cache.clear()


@dataclass
class WordTiming:
    text: str
    start: float
    end: float

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "start": self.start, "end": self.end}


@dataclass
class SynthesisResult:
    audio: np.ndarray
    words: list[WordTiming]
    duration: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration": round(self.duration, 3),
            "words": [word.to_dict() for word in self.words],
        }


def lang_code_for_voice(voice_id: str) -> str:
    return voice_id[0]


def get_pipeline(lang_code: str):
    if lang_code not in _pipelines:
        from kokoro import KPipeline

        device = _best_device()
        _pipelines[lang_code] = KPipeline(
            lang_code=lang_code,
            repo_id="hexgrad/Kokoro-82M",
            device=device,
        )
    return _pipelines[lang_code]


def _merge_punctuation(words: list[WordTiming]) -> list[WordTiming]:
    merged: list[WordTiming] = []
    for word in words:
        if merged and PUNCT_ONLY.match(word.text):
            merged[-1].text += word.text
            merged[-1].end = word.end
        else:
            merged.append(word)
    return merged


def _extract_word_timings(results, offset: float = 0.0) -> tuple[list[WordTiming], float]:
    words: list[WordTiming] = []
    for result in results:
        if result.tokens:
            for token in result.tokens:
                text = token.text.strip()
                if not text or token.start_ts is None or token.end_ts is None:
                    continue
                words.append(
                    WordTiming(
                        text=text,
                        start=round(offset + token.start_ts, 3),
                        end=round(offset + token.end_ts, 3),
                    )
                )
        audio = result.audio if hasattr(result, "audio") else result[2]
        if audio is not None:
            if hasattr(audio, "numpy"):
                audio = audio.numpy()
            offset += len(np.asarray(audio)) / SAMPLE_RATE
    return _merge_punctuation(words), offset


def synthesize_with_timings(text: str, voice: str, speed: float = 1.0, *, use_cache: bool = True) -> SynthesisResult:
    if voice not in VOICE_IDS:
        raise ValueError(f"Unknown voice: {voice}")

    text = text.strip()
    if not text:
        raise ValueError("Text is empty.")

    key = _cache_key(text, voice, speed)
    if use_cache and key in _synthesis_cache:
        return _synthesis_cache[key]

    lang_code = lang_code_for_voice(voice)
    pipeline = get_pipeline(lang_code)
    results = list(pipeline(text, voice=voice, speed=speed, split_pattern=r"\n+"))

    chunks: list[np.ndarray] = []
    for result in results:
        audio = result.audio if hasattr(result, "audio") else result[2]
        if audio is None:
            continue
        if hasattr(audio, "numpy"):
            audio = audio.numpy()
        chunks.append(np.asarray(audio, dtype=np.float32))

    if not chunks:
        raise RuntimeError("No audio was generated.")

    audio = np.concatenate(chunks)
    words, duration = _extract_word_timings(results)
    if not words:
        duration = len(audio) / SAMPLE_RATE

    result = SynthesisResult(audio=audio, words=words, duration=duration or len(audio) / SAMPLE_RATE)
    if use_cache:
        if len(_synthesis_cache) >= _MAX_SYNTHESIS_CACHE:
            _synthesis_cache.clear()
        _synthesis_cache[key] = result
    return result


def synthesize(text: str, voice: str, speed: float = 1.0) -> np.ndarray:
    return synthesize_with_timings(text, voice, speed).audio


def warmup() -> None:
    """Load Kokoro model and warm up GPU so the first request is fast."""
    pipeline = get_pipeline("a")
    list(pipeline("Ready.", voice="af_heart", speed=1.0, split_pattern=r"\n+"))


def format_srt_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def to_srt(words: list[WordTiming], uppercase: bool = True) -> str:
    blocks: list[str] = []
    for index, word in enumerate(words, start=1):
        text = word.text.upper() if uppercase else word.text
        blocks.extend(
            [
                str(index),
                f"{format_srt_timestamp(word.start)} --> {format_srt_timestamp(word.end)}",
                text,
                "",
            ]
        )
    return "\n".join(blocks).strip() + "\n"


def wav_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, audio, sample_rate, format="WAV")
    return buffer.getvalue()


def mp3_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    wav_buffer = io.BytesIO()
    sf.write(wav_buffer, audio, sample_rate, format="WAV")
    proc = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-f",
            "mp3",
            "-codec:a",
            "libmp3lame",
            "-qscale:a",
            "4",
            "pipe:1",
        ],
        input=wav_buffer.getvalue(),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode().strip() or "ffmpeg failed to encode MP3")
    return proc.stdout
