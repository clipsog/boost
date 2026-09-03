"""Track full-pack boosts by TikTok video ID."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HISTORY_PATH = Path(__file__).resolve().parent / "boosted_videos.json"
BOOST_MODE_FULL = "full"


def _load() -> dict[str, Any]:
    if not HISTORY_PATH.exists():
        return {}
    try:
        return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict[str, Any]) -> None:
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
