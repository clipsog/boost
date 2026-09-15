"""Track full-pack boosts by TikTok video ID."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import BOOST_HISTORY_PATH, BOOST_HISTORY_SEED_PATH

HISTORY_PATH = BOOST_HISTORY_PATH
BOOST_MODE_FULL = "full"
BOOST_MODE_PARTIAL_VIEWS = "partial_views"


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


def has_views_boost(video_id: str) -> bool:
    entry = get_boost(str(video_id))
    if not entry:
        return False
    if entry.get("views_order") or entry.get("views_sent"):
        return True
    return entry.get("boost_mode") == BOOST_MODE_PARTIAL_VIEWS


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


def record_partial_views(
    video_id: str,
    *,
    url: str,
    views_order: int | str | None = None,
    views_at_queue: int | None = None,
) -> dict[str, Any]:
    data = _load()
    existing = data.get(str(video_id)) or {}
    entry = {
        **existing,
        "video_id": str(video_id),
        "url": url,
        "boosted_at": existing.get("boosted_at") or datetime.now(timezone.utc).isoformat(),
        "boost_mode": BOOST_MODE_PARTIAL_VIEWS,
        "views_sent": True,
        "views_order": views_order or existing.get("views_order"),
        "views_at_queue": views_at_queue if views_at_queue is not None else existing.get("views_at_queue"),
    }
    data[str(video_id)] = entry
    _save(data)
    return entry


def _entry_has_real_orders(entry: dict[str, Any]) -> bool:
    return bool(entry.get("views_order") or entry.get("likes_order"))


def mark_assumed_boosted(
    video_id: str,
    *,
    url: str,
    posted_at: str | None = None,
) -> bool:
    """Mark a video as full-boosted in history without placing orders."""
    vid = str(video_id)
    existing = get_boost(vid)
    if existing and _entry_is_full_boost(existing) and not existing.get("assumed_boosted"):
        return False
    if existing and _entry_has_real_orders(existing):
        record_boost(
            vid,
            url=url,
            views_order=existing.get("views_order"),
            likes_order=existing.get("likes_order"),
        )
        return True

    data = _load()
    data[vid] = {
        "video_id": vid,
        "url": url,
        "boosted_at": datetime.now(timezone.utc).isoformat(),
        "boost_mode": BOOST_MODE_FULL,
        "assumed_boosted": True,
        "posted_at_at_mark": posted_at,
    }
    _save(data)
    return True


def clear_assumed_boost_if_newer(
    video_id: str,
    *,
    posted_at: datetime,
    cutoff: datetime,
) -> bool:
    """Remove assumed-only boost records for posts newer than the cutoff."""
    if posted_at <= cutoff:
        return False
    entry = get_boost(str(video_id))
    if not entry or not entry.get("assumed_boosted"):
        return False
    if _entry_has_real_orders(entry):
        return False
    data = _load()
    data.pop(str(video_id), None)
    _save(data)
    return True


def sync_assumed_boosts_for_videos(
    videos: list[dict[str, Any]],
    *,
    cutoff: datetime,
) -> dict[str, int]:
    marked = 0
    cleared = 0
    for raw in videos:
        posted_raw = raw.get("posted_at")
        if not posted_raw:
            continue
        posted_at = datetime.fromisoformat(str(posted_raw))
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)
        vid = str(raw["video_id"])
        url = str(raw.get("url") or f"https://www.tiktok.com/video/{vid}")
        if posted_at <= cutoff:
            if mark_assumed_boosted(vid, url=url, posted_at=str(posted_raw)):
                marked += 1
        elif clear_assumed_boost_if_newer(vid, posted_at=posted_at, cutoff=cutoff):
            cleared += 1
    return {"marked": marked, "cleared": cleared}


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
    existing = data.get(str(video_id)) or {}
    entry = {
        "video_id": str(video_id),
        "url": url,
        "boosted_at": datetime.now(timezone.utc).isoformat(),
        "boost_mode": BOOST_MODE_FULL,
        "views_order": views_order or existing.get("views_order"),
        "likes_order": likes_order or existing.get("likes_order"),
    }
    if existing.get("assumed_boosted") and not _entry_has_real_orders(entry):
        entry["assumed_boosted"] = True
    data[str(video_id)] = entry
    _save(data)
    return entry
