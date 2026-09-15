"""Fetch public TikTok video stats from a URL."""

from __future__ import annotations

import json
import re
import subprocess
import time
from typing import Any

import requests

from profile_feed import ProfileFeedError, _cover_url, _impersonate_targets, _ytdlp_path

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


def _author_from_url(url: str) -> str | None:
    match = re.search(r"tiktok\.com/@([^/]+)", url, re.I)
    return match.group(1) if match else None


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


def _fetch_tikwm(url: str) -> dict[str, Any]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.tikwm.com/",
        "Origin": "https://www.tikwm.com",
    }
    try:
        resp = requests.post(
            TIKWM_API,
            data={"url": url, "hd": 1},
            timeout=30,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise TikTokStatsError(f"Could not reach TikTok stats service: {exc}") from exc


def _stats_from_tikwm_payload(
    payload: dict[str, Any],
    *,
    canonical: str,
    video_id: str,
) -> dict[str, Any]:
    data = payload.get("data") or {}
    author = data.get("author") or {}
    return {
        "url": canonical,
        "video_id": str(data.get("id") or video_id),
        "views": int(data.get("play_count") or 0),
        "likes": int(data.get("digg_count") or 0),
        "comments": int(data.get("comment_count") or 0),
        "shares": int(data.get("share_count") or 0),
        "author": author.get("unique_id") or author.get("nickname") or _author_from_url(canonical),
        "title": (data.get("title") or "").strip(),
        "cover": data.get("cover"),
    }


def _parse_ytdlp_entry(stdout: str, *, fallback_url: str, video_id: str) -> dict[str, Any]:
    line = ""
    for raw in stdout.splitlines():
        raw = raw.strip()
        if raw:
            line = raw
            break
    if not line:
        raise TikTokStatsError("yt-dlp returned no metadata.")

    try:
        entry = json.loads(line)
    except json.JSONDecodeError as exc:
        raise TikTokStatsError("yt-dlp returned invalid JSON.") from exc

    resolved_id = str(entry.get("id") or video_id)
    uploader = entry.get("uploader") or entry.get("channel") or _author_from_url(fallback_url)
    webpage = entry.get("webpage_url") or fallback_url
    if uploader and resolved_id and "/video/" not in webpage:
        webpage = f"https://www.tiktok.com/@{uploader}/video/{resolved_id}"

    return {
        "url": webpage,
        "video_id": resolved_id,
        "views": int(entry.get("view_count") or 0),
        "likes": int(entry.get("like_count") or 0),
        "comments": int(entry.get("comment_count") or 0),
        "shares": 0,
        "author": uploader,
        "title": (entry.get("title") or entry.get("description") or "").strip(),
        "cover": _cover_url(entry),
    }


def _stats_via_ytdlp(url: str, video_id: str) -> dict[str, Any]:
    try:
        ytdlp = _ytdlp_path()
    except ProfileFeedError as exc:
        raise TikTokStatsError(str(exc)) from exc

    errors: list[str] = []
    for target in _impersonate_targets():
        cmd = [
            ytdlp,
            "-j",
            "--no-download",
            "--no-update",
            "--impersonate",
            target,
            url,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TikTokStatsError("Timed out fetching video metadata.") from exc

        if proc.returncode == 0 and proc.stdout.strip():
            return _parse_ytdlp_entry(proc.stdout, fallback_url=url, video_id=video_id)

        err = (proc.stderr or proc.stdout or "").strip()
        if err:
            errors.append(f"[{target}] {err[:400]}")

    cmd = [ytdlp, "-j", "--no-download", "--no-update", url]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise TikTokStatsError("Timed out fetching video metadata.") from exc

    if proc.returncode == 0 and proc.stdout.strip():
        return _parse_ytdlp_entry(proc.stdout, fallback_url=url, video_id=video_id)

    err = (proc.stderr or proc.stdout or "").strip()
    if err:
        errors.append(f"[no-impersonate] {err[:400]}")
    summary = errors[-1] if errors else "Could not fetch video metadata."
    raise TikTokStatsError(summary)


def _stats_from_queue(video_id: str) -> dict[str, Any] | None:
    from boost_queue import queue_item_for_video, stats_from_queue_item

    item = queue_item_for_video(video_id, force_refresh=False)
    if not item:
        return None
    stats = stats_from_queue_item(item)
    if not stats.get("author"):
        stats["author"] = _author_from_url(stats["url"])
    stats.setdefault("comments", 0)
    stats.setdefault("shares", 0)
    return stats


def _try_tikwm_stats(canonical: str, video_id: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = _fetch_tikwm(canonical)
    except TikTokStatsError as exc:
        return None, str(exc)

    if payload.get("code") != 0 and "request/second" in (payload.get("msg") or "").lower():
        time.sleep(1.1)
        try:
            payload = _fetch_tikwm(canonical)
        except TikTokStatsError as exc:
            return None, str(exc)

    if payload.get("code") != 0:
        msg = payload.get("msg") or "Unknown error"
        return None, msg

    return _stats_from_tikwm_payload(payload, canonical=canonical, video_id=video_id), None


def _resolve_via_ytdlp(url: str) -> tuple[str, str]:
    stats = _stats_via_ytdlp(url, video_id="")
    video_id = str(stats["video_id"])
    if not video_id:
        raise TikTokStatsError("Could not determine video ID for this link.")
    return video_id, stats["url"]


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
        try:
            return _resolve_via_tikwm(candidate)
        except TikTokStatsError:
            return _resolve_via_ytdlp(candidate)

    if not extract_video_id(candidate):
        author_match = re.search(r"tiktok\.com/@([^/]+)", candidate)
        if author_match:
            canonical = f"https://www.tiktok.com/@{author_match.group(1)}/video/{video_id}"
        else:
            canonical = f"https://www.tiktok.com/video/{video_id}"
    else:
        canonical = candidate

    return video_id, canonical


def get_video_stats(url: str) -> dict[str, Any]:
    video_id, canonical = resolve_video(url)
    cache_key = video_id
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    tikwm_result, tikwm_error = _try_tikwm_stats(canonical, video_id)
    if tikwm_result is not None:
        _cache[cache_key] = (now, tikwm_result)
        return tikwm_result

    queue_result = _stats_from_queue(video_id)
    if queue_result is not None:
        _cache[cache_key] = (now, queue_result)
        return queue_result

    try:
        result = _stats_via_ytdlp(canonical, video_id)
    except TikTokStatsError as exc:
        detail = str(exc)
        if tikwm_error:
            raise TikTokStatsError(
                f"TikTok stats unavailable (tikwm: {tikwm_error}). yt-dlp: {detail}"
            ) from exc
        raise

    _cache[cache_key] = (now, result)
    return result
