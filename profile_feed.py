"""Fetch recent TikTok profile videos via yt-dlp."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _ytdlp_path() -> str:
    candidates = [
        PROJECT_ROOT / ".venv" / "bin" / "yt-dlp",
        shutil.which("yt-dlp"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    raise ProfileFeedError("yt-dlp is not installed. Run: pip install yt-dlp")


class ProfileFeedError(Exception):
    pass


def _cover_url(entry: dict) -> str | None:
    thumbs = entry.get("thumbnails") or []
    for thumb in thumbs:
        url = thumb.get("url")
        if url:
            return url
    return entry.get("thumbnail")


def fetch_profile_videos(
    username: str,
    *,
    since: datetime | None = None,
    max_fetch: int = 80,
) -> list[dict]:
    handle = username.strip().lstrip("@")
    if not handle:
        raise ProfileFeedError("Profile username is empty.")

    ytdlp = _ytdlp_path()
    profile_url = f"https://www.tiktok.com/@{handle}"
    try:
        proc = subprocess.run(
            [
                ytdlp,
                "--flat-playlist",
                "-j",
                "--playlist-end",
                str(max_fetch),
                profile_url,
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProfileFeedError("Timed out fetching profile videos.") from exc

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise ProfileFeedError(err or "Could not fetch profile videos.")

    videos: list[dict] = []
    since_utc = since.astimezone(timezone.utc) if since else None

    for line in proc.stdout.splitlines():
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
            {
                "video_id": video_id,
                "url": entry.get("webpage_url")
                or f"https://www.tiktok.com/@{uploader}/video/{video_id}",
                "title": (entry.get("title") or entry.get("description") or "").strip(),
                "posted_at": posted_at.isoformat(),
                "views": int(entry.get("view_count") or 0),
                "likes": int(entry.get("like_count") or 0),
                "cover": _cover_url(entry),
            }
        )

    return videos
