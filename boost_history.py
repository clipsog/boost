"""Track full-pack boosts by TikTok video ID."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import BOOST_HISTORY_PATH, BOOST_HISTORY_SEED_PATH

HISTORY_PATH = BOOST_HISTORY_PATH
BOOST_MODE_FULL = "full"


def _load() -> dict[str, Any]:
    if not HISTORY_PATH.exists():
        return {}
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _entry_is_full_boost(entry: dict[str, Any]) -> bool:
    mode = entry.get("boost_mode")
    return mode is None or mode == BOOST_MODE_FULL


def get_boost(video_id: str) -> dict[str, Any] | None:
    return _load().get(str(video_id))


def is_full_boosted(video_id: str) -> bool:
    entry = get_boost(str(video_id))
    return bool(entry and _entry_is_full_boost(entry))


def history_stats() -> dict[str, Any]:
    data = _load()
    full_boosted = sum(1 for entry in data.values() if _entry_is_full_boost(entry))
    return {
        "total": len(data),
        "full_boosted": full_boosted,
        "path": str(HISTORY_PATH),
        "seed_path": str(BOOST_HISTORY_SEED_PATH) if BOOST_HISTORY_SEED_PATH else None,
    }


def merge_history(incoming: dict[str, Any]) -> dict[str, int]:
    """Merge imported entries; existing full-boost records are kept."""
    data = _load()
    added = 0
    skipped = 0

    for video_id, entry in incoming.items():
        vid = str(video_id)
        if not isinstance(entry, dict):
            skipped += 1
            continue
        if vid in data and _entry_is_full_boost(data[vid]):
            skipped += 1
            continue
        data[vid] = entry
        added += 1

    if added:
        _save(data)
    return {"added": added, "skipped": skipped, "total": len(data)}


def ensure_history_loaded() -> dict[str, int] | None:
    """Load bundled seed file when runtime history is empty."""
    if _load():
        return None
    seed_path = BOOST_HISTORY_SEED_PATH
    if not seed_path or not seed_path.exists():
        return None
    try:
        incoming = json.loads(seed_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(incoming, dict) or not incoming:
        return None
    return merge_history(incoming)


def record_boost(
    video_id: str,
    *,
    url: str,
    views_order: int | str | None = None,
    likes_order: int | str | None = None,
    boost_mode: str = BOOST_MODE_FULL,
) -> dict[str, Any] | None:
    if boost_mode != BOOST_MODE_FULL:
        return None

    data = _load()
    entry = {
        "video_id": str(video_id),
        "url": url,
        "boosted_at": datetime.now(timezone.utc).isoformat(),
        "boost_mode": BOOST_MODE_FULL,
        "views_order": views_order,
        "likes_order": likes_order,
    }
    data[str(video_id)] = entry
    _save(data)
    return entry
