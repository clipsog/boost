#!/usr/bin/env python3
"""Lyrics Video — transparent caption overlay generator for TikTok."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from lyrics_engine import LyricWord, LyricsResult, transcribe_audio
from lyrics_renderer import LyricsStyle, render_lyrics_video_bytes

app = Flask(__name__)
UPLOAD_DIR = Path(__file__).resolve().parent / "lyrics_uploads"
CACHE_DIR = Path(__file__).resolve().parent / "lyrics_cache"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac", ".mp4", ".mov"}
_model_ready = False
_model_lock = threading.Lock()


def _warmup_model() -> None:
    global _model_ready
    with _model_lock:
        if _model_ready:
            return
        try:
            from lyrics_engine import _get_model

            _get_model()
            _model_ready = True
        except Exception:
            pass


@app.before_request
def _ensure_model() -> None:
    if not _model_ready:
        threading.Thread(target=_warmup_model, daemon=True).start()


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip() or "lyrics"


def _session_dir(session_id: str) -> Path:
    path = UPLOAD_DIR / session_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _find_audio(session_id: str) -> Path | None:
    folder = _session_dir(session_id)
    for path in sorted(folder.glob("audio.*")):
        return path
    return None


def _words_from_payload(raw_words: list[dict]) -> list[LyricWord]:
    words: list[LyricWord] = []
    for item in raw_words:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        words.append(
            LyricWord(
                text=text,
                start=float(item.get("start", 0)),
                end=float(item.get("end", 0)),
            )
        )
    return words


def _lyrics_cache_key(session_id: str, words: list[LyricWord], fmt: str, style: LyricsStyle) -> str:
    payload = {
        "session": session_id,
        "fmt": fmt,
        "style": {
            "width": style.width,
            "height": style.height,
            "fps": style.fps,
            "words_per_phrase": style.words_per_phrase,
            "font_size": style.font_size,
        },
        "words": [word.to_dict() for word in words],
    }
    return hashlib.md5(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@app.get("/")
def index():
    return render_template("lyrics.html")


@app.post("/api/upload")
def upload():
    file = request.files.get("audio")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "Please upload an audio file."}), 400

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        return jsonify({"ok": False, "error": f"Unsupported file type: {suffix}"}), 400

    session_id = hashlib.md5(f"{file.filename}-{file.content_length}".encode()).hexdigest()[:12]
    folder = _session_dir(session_id)
    for old in folder.glob("audio.*"):
        old.unlink(missing_ok=True)

    audio_path = folder / f"audio{suffix}"
    file.save(audio_path)

    try:
        lyrics = transcribe_audio(audio_path)
    except Exception as exc:
        audio_path.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": f"Transcription failed: {exc}"}), 500

    meta_path = folder / "lyrics.json"
    meta_path.write_text(json.dumps(lyrics.to_dict(), indent=2), encoding="utf-8")

    return jsonify({"ok": True, "session_id": session_id, **lyrics.to_dict()})


@app.post("/api/transcribe")
def transcribe_existing():
    body = request.get_json(silent=True) or {}
    session_id = (body.get("session_id") or "").strip()
    if not session_id:
        return jsonify({"ok": False, "error": "Missing session_id."}), 400

    audio_path = _find_audio(session_id)
    if not audio_path:
        return jsonify({"ok": False, "error": "Audio not found. Upload again."}), 404

    try:
        lyrics = transcribe_audio(audio_path)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Transcription failed: {exc}"}), 500

    meta_path = _session_dir(session_id) / "lyrics.json"
    meta_path.write_text(json.dumps(lyrics.to_dict(), indent=2), encoding="utf-8")
    return jsonify({"ok": True, "session_id": session_id, **lyrics.to_dict()})


@app.post("/api/render")
def render():
    if request.is_json:
        body = request.get_json(silent=True) or {}
    else:
        body = request.form or {}

    session_id = (body.get("session_id") or "").strip()
    fmt = (body.get("format") or "hevc").strip().lower()
    if fmt == "greenscreen":
        fmt = "tiktok"
    if fmt not in {"hevc", "prores", "tiktok", "webm", "mov", "mp4"}:
        return jsonify({"ok": False, "error": "Format must be hevc, prores, tiktok, webm, mov, or mp4."}), 400

    audio_path = _find_audio(session_id)
    if not audio_path:
        return jsonify({"ok": False, "error": "Audio not found. Upload again."}), 404

    raw_words = body.get("words")
    if raw_words:
        words = _words_from_payload(raw_words)
    else:
        meta_path = _session_dir(session_id) / "lyrics.json"
        if not meta_path.exists():
            return jsonify({"ok": False, "error": "No lyrics found. Transcribe first."}), 400
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        words = _words_from_payload(data.get("words", []))

    if not words:
        return jsonify({"ok": False, "error": "No lyrics to render."}), 400

    duration = float(body.get("duration") or words[-1].end + 0.5)
    transcript = " ".join(word.text for word in words)
    lyrics = LyricsResult(words=words, duration=duration, transcript=transcript)

    style = LyricsStyle(
        width=int(body.get("width", 1080)),
        height=int(body.get("height", 1920)),
        fps=int(body.get("fps", 30)),
        words_per_phrase=int(body.get("words_per_phrase", 4)),
        font_size=int(body.get("font_size", 72)),
    )

    cache_key = _lyrics_cache_key(session_id, words, fmt, style)
    cached = CACHE_DIR / f"{cache_key}.{fmt}"
    if cached.exists():
        data = cached.read_bytes()
    else:
        try:
            data = render_lyrics_video_bytes(lyrics, audio_path, style=style, fmt=fmt)
            cached.write_bytes(data)
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Render failed: {exc}"}), 500

    title = _safe_filename(body.get("title") or "lyrics")
    if fmt == "hevc":
        mimetype, ext = "video/quicktime", "mov"
    elif fmt == "tiktok":
        mimetype, ext = "video/mp4", "mp4"
    elif fmt == "mov":
        mimetype, ext = "video/quicktime", "mov"
    elif fmt == "mp4":
        mimetype, ext = "video/mp4", "mp4"
    else:
        mimetype, ext = "video/webm", "webm"

    return Response(
        data,
        mimetype=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{title}.{ext}"'},
    )


if __name__ == "__main__":
    _warmup_model()
    app.run(host="127.0.0.1", port=5052, debug=True)
