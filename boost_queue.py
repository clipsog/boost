"""Build human-approval boost queue from a TikTok profile."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from boost_history import get_boost, is_full_boosted
from config import QUEUE_LOOKBACK_HOURS, QUEUE_MIN_AGE_HOURS, QUEUE_PROFILE
from profile_feed import ProfileFeedError, fetch_profile_videos

_cache: dict[str, Any] = {}
_CACHE_TTL = 120.0


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _video_payload(raw: dict[str, Any], *, status: str, now: datetime) -> dict[str, Any]:
    posted_at = _parse_iso(raw["posted_at"])
    eligible_at = posted_at + timedelta(hours=QUEUE_MIN_AGE_HOURS)
    entry = get_boost(raw["video_id"]) or {}
    item = {
        **raw,
        "status": status,
        "eligible_at": eligible_at.isoformat(),
        "already_boosted": is_full_boosted(raw["video_id"]),
        "boosted_at": entry.get("boosted_at"),
    }
    if status == "waiting":
        item["seconds_until_ready"] = max(0, int((eligible_at - now).total_seconds()))
    return item


def build_queue(
    *,
    username: str | None = None,
    lookback_hours: int | None = None,
    min_age_hours: int | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    profile = (username or QUEUE_PROFILE).strip().lstrip("@")
    lookback = lookback_hours if lookback_hours is not None else QUEUE_LOOKBACK_HOURS
    min_age = min_age_hours if min_age_hours is not None else QUEUE_MIN_AGE_HOURS

    cache_key = f"{profile}:{lookback}:{min_age}"
    now = time.time()
    cached = _cache.get(cache_key)
    if not force_refresh and cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    now_dt = datetime.now(timezone.utc)
    since = now_dt - timedelta(hours=lookback)
    min_age_delta = timedelta(hours=min_age)

    try:
        videos = fetch_profile_videos(profile, since=since)
    except ProfileFeedError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "profile": profile,
            "lookback_hours": lookback,
            "min_age_hours": min_age,
        }

    ready: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    boosted: list[dict[str, Any]] = []

    for raw in videos:
        posted_at = _parse_iso(raw["posted_at"])
        if is_full_boosted(raw["video_id"]):
            boosted.append(_video_payload(raw, status="boosted", now=now_dt))
            continue
        if now_dt - posted_at < min_age_delta:
            waiting.append(_video_payload(raw, status="waiting", now=now_dt))
        else:
            ready.append(_video_payload(raw, status="ready", now=now_dt))

    payload = {
        "ok": True,
        "profile": profile,
        "profile_url": f"https://www.tiktok.com/@{profile}",
        "lookback_hours": lookback,
        "min_age_hours": min_age,
        "fetched_at": now_dt.isoformat(),
        "counts": {
            "ready": len(ready),
            "waiting": len(waiting),
            "boosted": len(boosted),
        },
        "ready": ready,
        "waiting": waiting,
        "boosted": boosted,
    }
    _cache[cache_key] = (now, payload)
    return payload


def queue_item_for_video(video_id: str, *, force_refresh: bool = False) -> dict[str, Any] | None:
    queue = build_queue(force_refresh=force_refresh)
    if not queue.get("ok"):
        return None
    vid = str(video_id)
    for bucket in ("ready", "waiting", "boosted"):
        for item in queue.get(bucket, []):
            if str(item.get("video_id")) == vid:
                return item
    return None


def invalidate_cache() -> None:
    _cache.clear()
