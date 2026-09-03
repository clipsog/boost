"""Persist rich-text documents for the Kokoro editor."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

DOCUMENTS_DIR = Path(__file__).resolve().parent / "saved_documents"
DEFAULT_HTML = "<p><br></p>"
DEFAULT_TITLE = "Untitled script"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_title(title: str) -> str:
    cleaned = re.sub(r"\s+", " ", (title or "").strip())
    return cleaned[:120] or DEFAULT_TITLE


def _doc_path(doc_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9-]{36}", doc_id):
        raise ValueError("Invalid document id.")
    return DOCUMENTS_DIR / f"{doc_id}.json"


def ensure_documents_dir() -> None:
    DOCUMENTS_DIR.mkdir(parents=True, exist_ok=True)


def list_documents() -> list[dict]:
    ensure_documents_dir()
    docs: list[dict] = []
    for path in DOCUMENTS_DIR.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        docs.append(
            {
                "id": payload.get("id", path.stem),
                "title": payload.get("title", DEFAULT_TITLE),
                "updated_at": payload.get("updated_at"),
            }
        )
    docs.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return docs


def load_document(doc_id: str) -> dict:
    path = _doc_path(doc_id)
    if not path.exists():
        raise FileNotFoundError("Document not found.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "id": payload["id"],
        "title": payload.get("title", DEFAULT_TITLE),
        "html": payload.get("html", DEFAULT_HTML),
        "updated_at": payload.get("updated_at"),
    }


def save_document(title: str, html: str, doc_id: str | None = None) -> dict:
    ensure_documents_dir()
    doc_id = doc_id or str(uuid.uuid4())
    path = _doc_path(doc_id)
    payload = {
        "id": doc_id,
        "title": _safe_title(title),
        "html": html or DEFAULT_HTML,
        "updated_at": _now_iso(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def delete_document(doc_id: str) -> None:
    path = _doc_path(doc_id)
    if path.exists():
        path.unlink()
