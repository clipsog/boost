"""Fetch recent TikTok profile videos via yt-dlp (primary) or tikwm (fallback)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent
TIKWM_USER_POSTS = "https://www.tikwm.com/api/user/posts"
DEFAULT_IMPERSONATE_TARGETS = (
    "chrome-133",
    "chrome-136",
    "chrome-131",
    "chrome-124",
    "chrome",
)


class ProfileFeedError(Exception):
    pass


def _impersonate_targets() -> list[str]:
    raw = os.getenv("YTDLP_IMPERSONATE", "")
    if raw.strip():
        return [part.strip() for part in raw.split(",") if part.strip()]
    return list(DEFAULT_IMPERSONATE_TARGETS)


def _ytdlp_path() -> str:
    candidates = [
        PROJECT_ROOT / ".venv" / "bin" / "yt-dlp",
        shutil.which("yt-dlp"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise ProfileFeedError("yt-dlp is not installed. Run: pip install \"yt-dlp[default,curl-cffi]\"")


def _cover_url(entry: dict) -> str | None:
    thumbs = entry.get("thumbnails") or []
    for thumb in thumbs:
        url = thumb.get("url")
        if url:
            return url
    return entry.get("thumbnail") or entry.get("cover")


def _normalize_video(
    *,
    video_id: str,
    url: str,
    title: str,
    posted_at: datetime,
    views: int,
    likes: int,
    cover: str | None,
) -> dict:
    return {
        "video_id": str(video_id),
        "url": url,
        "title": title.strip(),
        "posted_at": posted_at.astimezone(timezone.utc).isoformat(),
        "views": int(views or 0),
        "likes": int(likes or 0),
        "cover": cover,
    }


def _parse_ytdlp_lines(stdout: str, handle: str, since_utc: datetime | None) -> list[dict]:
    videos: list[dict] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        video_id = str(entry.get("id") or "")
        timestamp = entry.get("timestamp")
        if not video_id or not timestamp:
            continue

        posted_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        if since_utc and posted_at < since_utc:
            break

        uploader = entry.get("uploader") or handle
        videos.append(
            _normalize_video(
                video_id=video_id,
                url=entry.get("webpage_url")
                or f"https://www.tiktok.com/@{uploader}/video/{video_id}",
                title=(entry.get("title") or entry.get("description") or ""),
                posted_at=posted_at,
                views=int(entry.get("view_count") or 0),
                likes=int(entry.get("like_count") or 0),
                cover=_cover_url(entry),
            )
        )
    return videos


def _fetch_via_ytdlp(
    handle: str,
    *,
    since_utc: datetime | None,
    max_fetch: int,
) -> list[dict]:
    ytdlp = _ytdlp_path()
    profile_url = f"https://www.tiktok.com/@{handle}"
    errors: list[str] = []

    for target in _impersonate_targets():
        cmd = [
            ytdlp,
            "--flat-playlist",
            "-j",
            "--no-update",
            "--playlist-end",
            str(max_fetch),
            "--impersonate",
            target,
            profile_url,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProfileFeedError("Timed out fetching profile videos.") from exc

        if proc.returncode == 0 and proc.stdout.strip():
            videos = _parse_ytdlp_lines(proc.stdout, handle, since_utc)
            if videos:
                return videos

        err = (proc.stderr or proc.stdout or "").strip()
        if err:
            errors.append(f"[{target}] {err}")

    # Last attempt without impersonate (works on some hosts).
    cmd = [
        ytdlp,
        "--flat-playlist",
        "-j",
        "--no-update",
        "--playlist-end",
        str(max_fetch),
        profile_url,
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        videos = _parse_ytdlp_lines(proc.stdout, handle, since_utc)
        if videos:
            return videos
    err = (proc.stderr or proc.stdout or "").strip()
    if err:
        errors.append(f"[no-impersonate] {err}")

    summary = errors[-1] if errors else "Could not fetch profile videos."
    raise ProfileFeedError(summary)


def _fetch_via_tikwm(
    handle: str,
    *,
    since_utc: datetime | None,
    max_fetch: int,
) -> list[dict]:
    videos: list[dict] = []
    cursor = 0
    headers = {"User-Agent": "Mozilla/5.0 (compatible; VideoBoost/1.0)"}

    while len(videos) < max_fetch:
        try:
            response = requests.post(
                TIKWM_USER_POSTS,
                data={
                    "unique_id": handle,
                    "count": min(35, max_fetch - len(videos)),
                    "cursor": cursor,
                },
                headers=headers,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise ProfileFeedError(f"tikwm request failed: {exc}") from exc
        except ValueError as exc:
            raise ProfileFeedError("tikwm returned invalid JSON.") from exc

        if payload.get("code") != 0:
            msg = payload.get("msg") or "Unknown tikwm error"
            if "request/second" in msg.lower():
                time.sleep(1.1)
                continue
            raise ProfileFeedError(msg)

        data = payload.get("data") or {}
        batch = data.get("videos") or []
        if not batch:
            break

        stop = False
        for item in batch:
            video_id = str(item.get("video_id") or item.get("id") or "")
            create_time = item.get("create_time")
            if not video_id or not create_time:
                continue

            posted_at = datetime.fromtimestamp(int(create_time), tz=timezone.utc)
            if since_utc and posted_at < since_utc:
                stop = True
                break

            author = item.get("author") or {}
            unique_id = author.get("unique_id") if isinstance(author, dict) else handle
            unique_id = unique_id or handle
            videos.append(
                _normalize_video(
                    video_id=video_id,
                    url=f"https://www.tiktok.com/@{unique_id}/video/{video_id}",
                    title=item.get("title") or item.get("desc") or "",
                    posted_at=posted_at,
                    views=int(item.get("play_count") or 0),
                    likes=int(item.get("digg_count") or item.get("like_count") or 0),
                    cover=item.get("cover") or item.get("origin_cover"),
                )
            )
            if len(videos) >= max_fetch:
                break

        if stop or not data.get("hasMore"):
            break

        cursor = int(data.get("cursor") or 0)
        time.sleep(0.4)

    if not videos:
        raise ProfileFeedError("tikwm returned no videos for this profile.")
    return videos


def fetch_profile_videos(
    username: str,
    *,
    since: datetime | None = None,
    max_fetch: int = 80,
) -> list[dict]:
    handle = username.strip().lstrip("@")
    if not handle:
        raise ProfileFeedError("Profile username is empty.")

    since_utc = since.astimezone(timezone.utc) if since else None

    try:
        return _fetch_via_ytdlp(handle, since_utc=since_utc, max_fetch=max_fetch)
    except ProfileFeedError as ytdlp_error:
        try:
            return _fetch_via_tikwm(handle, since_utc=since_utc, max_fetch=max_fetch)
        except ProfileFeedError as tikwm_error:
            raise ProfileFeedError(
                f"yt-dlp failed: {ytdlp_error} | tikwm failed: {tikwm_error}"
            ) from tikwm_error
