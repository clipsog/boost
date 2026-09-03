"""Fetch public TikTok video stats from a URL."""

from __future__ import annotations

import re
import time
from typing import Any

import requests

TIKWM_API = "https://www.tikwm.com/api/"
VIDEO_ID_PATTERNS = (
    re.compile(r"/video/(\d+)"),
    re.compile(r"/photo/(\d+)"),
    re.compile(r"/v/(\d+)"),
    re.compile(r"[?&](?:item_id|share_item_id)=(\d+)"),
)
SHORT_OR_MOBILE_HINTS = (
    "vm.tiktok.com",
    "vt.tiktok.com",
    "m.tiktok.com",
    "/t/",
    "/share/",
)


class TikTokStatsError(Exception):
    pass


_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_CACHE_TTL = 60.0


def extract_video_id(url: str) -> str | None:
    for pattern in VIDEO_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def _prepare_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise TikTokStatsError("URL is empty.")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _follow_redirects(url: str) -> str:
    try:
        resp = requests.get(
            url,
            allow_redirects=True,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0 (compatible; VideoBoost/1.0)"},
        )
        return resp.url
    except requests.RequestException as exc:
        raise TikTokStatsError(f"Could not resolve link: {exc}") from exc


def _needs_redirect(url: str) -> bool:
    lower = url.lower()
    if extract_video_id(url):
        return False
    return any(hint in lower for hint in SHORT_OR_MOBILE_HINTS)


def _resolve_via_tikwm(url: str) -> tuple[str, str]:
    payload = _fetch_tikwm(url)
    if payload.get("code") != 0 and "request/second" in (payload.get("msg") or "").lower():
        time.sleep(1.1)
        payload = _fetch_tikwm(url)
    if payload.get("code") != 0:
        msg = payload.get("msg") or "Unknown error"
        raise TikTokStatsError(msg)

    data = payload.get("data") or {}
    video_id = str(data.get("id") or "")
    if not video_id:
        raise TikTokStatsError("Could not determine video ID for this link.")

    author = (data.get("author") or {}).get("unique_id")
    if author:
        canonical = f"https://www.tiktok.com/@{author}/video/{video_id}"
    else:
        canonical = f"https://www.tiktok.com/video/{video_id}"
    return video_id, canonical


def resolve_video(url: str) -> tuple[str, str]:
    """Resolve any TikTok link to (video_id, canonical_url)."""
    prepared = _prepare_url(url)
    if "tiktok.com" not in prepared.lower():
        raise TikTokStatsError("Only TikTok video URLs are supported.")

    candidate = prepared
    video_id = extract_video_id(candidate)
    if not video_id and _needs_redirect(candidate):
        candidate = _follow_redirects(candidate)
        video_id = extract_video_id(candidate)

    if not video_id:
        video_id, canonical = _resolve_via_tikwm(candidate)
        return video_id, canonical

    if not extract_video_id(candidate):
        author_match = re.search(r"tiktok\.com/@([^/]+)", candidate)
        if author_match:
            canonical = f"https://www.tiktok.com/@{author_match.group(1)}/video/{video_id}"
        else:
            canonical = f"https://www.tiktok.com/video/{video_id}"
    else:
        canonical = candidate

    return video_id, canonical


def _fetch_tikwm(url: str) -> dict[str, Any]:
    try:
        resp = requests.post(
            TIKWM_API,
            data={"url": url, "hd": 1},
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (compatible; VideoBoost/1.0)"},
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise TikTokStatsError(f"Could not reach TikTok stats service: {exc}") from exc


def get_video_stats(url: str) -> dict[str, Any]:
    video_id, canonical = resolve_video(url)
    cache_key = video_id
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    payload = _fetch_tikwm(canonical)
    if payload.get("code") != 0 and "request/second" in (payload.get("msg") or "").lower():
        time.sleep(1.1)
        payload = _fetch_tikwm(canonical)

    if payload.get("code") != 0:
        msg = payload.get("msg") or "Unknown error"
        raise TikTokStatsError(msg)

    data = payload.get("data") or {}
    author = data.get("author") or {}
    result = {
        "url": canonical,
        "video_id": str(data.get("id") or video_id),
        "views": int(data.get("play_count") or 0),
        "likes": int(data.get("digg_count") or 0),
        "comments": int(data.get("comment_count") or 0),
        "shares": int(data.get("share_count") or 0),
        "author": author.get("unique_id") or author.get("nickname"),
        "title": (data.get("title") or "").strip(),
        "cover": data.get("cover"),
    }
    _cache[cache_key] = (now, result)
    return result
