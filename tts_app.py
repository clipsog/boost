#!/usr/bin/env python3
"""Web UI for Kokoro-82M text-to-speech."""

from __future__ import annotations

import base64
import hashlib
import re
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

from caption_video import render_caption_video_bytes
from document_store import delete_document, list_documents, load_document, save_document
from kokoro_engine import VOICES, mp3_bytes, synthesize_with_timings, to_srt, warmup

app = Flask(__name__)
CACHE_DIR = Path(__file__).resolve().parent / "caption_cache"
_warmup_started = False


@app.get("/")
def index():
    return render_template("tts.html", voices=VOICES)


@app.get("/api/voices")
def voices():
    return jsonify({"voices": VOICES})


@app.get("/api/documents")
def documents_index():
    return jsonify({"ok": True, "documents": list_documents()})


@app.get("/api/documents/<doc_id>")
def documents_get(doc_id: str):
    try:
        return jsonify({"ok": True, **load_document(doc_id)})
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Document not found."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/documents")
def documents_save():
    body = request.get_json(silent=True) or {}
    title = (body.get("title") or "").strip()
    html = body.get("html") or ""
    doc_id = (body.get("id") or "").strip() or None
    try:
        saved = save_document(title=title, html=html, doc_id=doc_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, **saved})


@app.delete("/api/documents/<doc_id>")
def documents_delete(doc_id: str):
    try:
        delete_document(doc_id)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True})


def _start_warmup() -> None:
    global _warmup_started
    if _warmup_started:
        return
    _warmup_started = True

    def _run() -> None:
        try:
            warmup()
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


@app.before_request
def _ensure_warmup() -> None:
    _start_warmup()


def get_cached_video(text: str, voice: str, speed: float, fmt: str) -> bytes | None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key_src = f"{text}|{voice}|{speed}|{fmt}"
    h = hashlib.md5(key_src.encode("utf-8")).hexdigest()
    suffix = ".mp4" if fmt == "mp4" else (".mov" if fmt == "mov" else ".webm")
    cached_path = CACHE_DIR / f"{h}{suffix}"
    if cached_path.exists():
        try:
            return cached_path.read_bytes()
        except OSError:
            return None
    return None

def save_cached_video(text: str, voice: str, speed: float, fmt: str, data: bytes) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key_src = f"{text}|{voice}|{speed}|{fmt}"
    h = hashlib.md5(key_src.encode("utf-8")).hexdigest()
    suffix = ".mp4" if fmt == "mp4" else (".mov" if fmt == "mov" else ".webm")
    cached_path = CACHE_DIR / f"{h}{suffix}"
    try:
        cached_path.write_bytes(data)
    except OSError:
        pass


@app.post("/api/generate")
def generate():
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    voice = (body.get("voice") or "af_heart").strip()
    try:
        speed = float(body.get("speed", 1.0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Speed must be a number."}), 400

    if not text:
        return jsonify({"ok": False, "error": "Please enter some text."}), 400
    if speed < 0.5 or speed > 2.0:
        return jsonify({"ok": False, "error": "Speed must be between 0.5 and 2.0."}), 400

    try:
        result = synthesize_with_timings(text, voice=voice, speed=speed)
        mp3 = mp3_bytes(result.audio)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Generation failed: {exc}"}), 500

    return jsonify(
        {
            "ok": True,
            "voice": voice,
            "audio_base64": base64.b64encode(mp3).decode("ascii"),
            "audio_mime": "audio/mpeg",
            "filename": f"kokoro_{voice}.mp3",
            "srt": to_srt(result.words),
            "srt_filename": f"kokoro_{voice}.srt",
            **result.to_dict(),
        }
    )


@app.post("/api/generate/mp3")
def generate_mp3():
    """Binary MP3 download endpoint (no timings)."""
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    voice = (body.get("voice") or "af_heart").strip()
    try:
        speed = float(body.get("speed", 1.0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Speed must be a number."}), 400

    if not text:
        return jsonify({"ok": False, "error": "Please enter some text."}), 400

    try:
        result = synthesize_with_timings(text, voice=voice, speed=speed)
        data = mp3_bytes(result.audio)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Generation failed: {exc}"}), 500

    filename = f"kokoro_{voice}.mp3"
    return Response(
        data,
        mimetype="audio/mpeg",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/generate/video")
def generate_video():
    if request.is_json:
        body = request.get_json(silent=True) or {}
    else:
        body = request.form or {}
    text = (body.get("text") or "").strip()
    voice = (body.get("voice") or "af_heart").strip()
    fmt = (body.get("format") or "mp4").strip().lower()
    try:
        speed = float(body.get("speed", 1.0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Speed must be a number."}), 400

    if not text:
        return jsonify({"ok": False, "error": "Please enter some text."}), 400
    if speed < 0.5 or speed > 2.0:
        return jsonify({"ok": False, "error": "Speed must be between 0.5 and 2.0."}), 400

    data = get_cached_video(text, voice, speed, fmt)
    if data is None:
        try:
            result = synthesize_with_timings(text, voice=voice, speed=speed)
            data = render_caption_video_bytes(result, fmt=fmt)
            save_cached_video(text, voice, speed, fmt, data)
        except ValueError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Video export failed: {exc}"}), 500

    title = (body.get("title") or "").strip()
    if title:
        safe_title = re.sub(r'[\\/*?:"<>|]', "_", title)
    else:
        safe_title = f"kokoro_{voice}"

    if fmt == "mp4":
        filename = f"{safe_title}.mp4"
        mimetype = "video/mp4"
    elif fmt == "mov":
        filename = f"{safe_title}.mov"
        mimetype = "video/quicktime"
    else:
        filename = f"{safe_title}.webm"
        mimetype = "video/webm"

    return Response(
        data,
        mimetype=mimetype,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5051, debug=True)
