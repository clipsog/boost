#!/usr/bin/env python3
"""Upload local boost history to a deployed booster instance."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Push boosted_videos.json to production.")
    parser.add_argument(
        "--url",
        required=True,
        help="App base URL, e.g. https://boost-xxxx.onrender.com",
    )
    parser.add_argument(
        "--file",
        default="boosted_videos.json",
        help="Local history JSON file (default: boosted_videos.json)",
    )
    parser.add_argument(
        "--secret",
        default=os.getenv("BOOST_ADMIN_SECRET", ""),
        help="Admin secret (or set BOOST_ADMIN_SECRET)",
    )
    args = parser.parse_args()

    if not args.secret:
        print("Set BOOST_ADMIN_SECRET or pass --secret", file=sys.stderr)
        return 1

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, dict):
        print("History file must be a JSON object keyed by video ID", file=sys.stderr)
        return 1

    base = args.url.rstrip("/")
    payload = json.dumps({"entries": entries}).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/api/history/import",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Admin-Secret": args.secret,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP {exc.code}: {detail}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Request failed: {exc.reason}", file=sys.stderr)
        return 1

    if not body.get("ok"):
        print(body.get("error") or body, file=sys.stderr)
        return 1

    print(
        f"Imported {body.get('added', 0)} entries "
        f"({body.get('skipped', 0)} skipped, {body.get('total', '?')} total on server)"
    )
    history = body.get("history") or {}
    if history:
        print(f"Server history: {history.get('full_boosted', '?')} full boosts tracked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
