#!/usr/bin/env python3
"""Small web UI to boost a video with views + likes."""

from __future__ import annotations

import random
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from boost_history import get_boost, is_full_boosted, record_boost
from boost_queue import build_queue, invalidate_cache, queue_item_for_video
from config import (
    FULL_LIKES_MAX,
    FULL_LIKES_MIN,
    FULL_VIEWS_HIGH,
    FULL_VIEWS_LOW,
    FULL_VIEWS_MID,
    FULL_VIEWS_ULTRA,
    LIKES_MIN,
    LIKES_ONLY_QUANTITY,
    LIKES_QUANTITY,
    LIKES_SERVICE_ID,
    LOW_VIEWS_QUANTITY,
    QUEUE_LOOKBACK_HOURS,
    QUEUE_MIN_AGE_HOURS,
    QUEUE_PROFILE,
    VIEWS_MIN,
    VIEWS_ONLY_QUANTITY,
    VIEWS_QUANTITY,
    VIEWS_SERVICE_ID,
)
from tiktok_stats import TikTokStatsError, get_video_stats, resolve_video
from zefame_client import ZefameAPIError, ZefameClient

load_dotenv(Path(__file__).resolve().parent / ".env")

app = Flask(__name__)
client = ZefameClient()

BOOST_MODE_FULL = "full"
BOOST_MODE_LOWER = "lower"
BOOST_MODE_VIEWS_ONLY = "views_only"
BOOST_MODE_LIKES_ONLY = "likes_only"


def _place(service_id: int, link: str, quantity: int) -> dict:
    try:
        result = client.add_order(service_id, link, quantity=quantity)
        return {"ok": True, "service": service_id, "quantity": quantity, **result}
    except ZefameAPIError as exc:
        return {"ok": False, "service": service_id, "quantity": quantity, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "service": service_id, "quantity": quantity, "error": str(exc)}


def _normalize_mode(mode: str | None) -> str:
    if mode == BOOST_MODE_VIEWS_ONLY:
        return BOOST_MODE_VIEWS_ONLY
    if mode == BOOST_MODE_LIKES_ONLY:
        return BOOST_MODE_LIKES_ONLY
    if mode == BOOST_MODE_LOWER:
        return BOOST_MODE_LOWER
    return BOOST_MODE_FULL


def _parse_views_quantity(raw: object) -> int | None:
    if raw is None or raw == "":
        return VIEWS_ONLY_QUANTITY
    try:
        quantity = int(raw)
    except (TypeError, ValueError):
        return None
    if quantity < VIEWS_MIN:
        return None
    return quantity


def _parse_likes_quantity(raw: object) -> int | None:
    if raw is None or raw == "":
        return LIKES_ONLY_QUANTITY
    try:
        quantity = int(raw)
    except (TypeError, ValueError):
        return None
    if quantity < LIKES_MIN:
        return None
    return quantity


def _full_pack_quantities(current_likes: int) -> tuple[int, int]:
    """Pick views from the video's current like tier; likes are always random 10–14."""
    likes = random.randint(FULL_LIKES_MIN, FULL_LIKES_MAX)
    if current_likes == 0:
        views = FULL_VIEWS_LOW
    elif current_likes <= 7:
        views = FULL_VIEWS_MID
    elif current_likes < 20:
        views = FULL_VIEWS_HIGH
    else:
        views = FULL_VIEWS_ULTRA
    return views, likes


def _already_boosted_payload(video_id: str) -> dict:
    entry = get_boost(video_id) or {}
    return {
        "already_boosted": True,
        "boosted_at": entry.get("boosted_at"),
        "previous_url": entry.get("url"),
        "boost_mode": entry.get("boost_mode"),
    }


def _run_boost(
    url: str,
    mode: str,
    *,
    quantity: int | None = None,
    from_queue: bool = False,
) -> tuple[dict, int]:
    if not url:
        return {"ok": False, "error": "Please enter a video URL."}, 400

    try:
        stats = get_video_stats(url)
        video_id = str(stats["video_id"])
        canonical_url = stats["url"]
        current_likes = stats["likes"]
    except TikTokStatsError as exc:
        return {"ok": False, "error": str(exc)}, 400

    if from_queue and mode == BOOST_MODE_FULL:
        item = queue_item_for_video(video_id, force_refresh=True)
        if not item:
            return {
                "ok": False,
                "error": "Video is not in the approval queue.",
                "video_id": video_id,
            }, 400
        if item.get("status") == "waiting":
            return {
                "ok": False,
                "error": (
                    f"Video must be at least {QUEUE_MIN_AGE_HOURS} hour(s) old before boosting."
                ),
                "video_id": video_id,
                "eligible_at": item.get("eligible_at"),
            }, 400
        if item.get("already_boosted"):
            return {
                "ok": False,
                "error": "This video already received the full boost pack.",
                "video_id": video_id,
                **_already_boosted_payload(video_id),
            }, 409

    if mode == BOOST_MODE_FULL and is_full_boosted(video_id):
        return (
            {
                "ok": False,
                "error": "This video already received the full boost pack.",
                "video_id": video_id,
                **_already_boosted_payload(video_id),
            },
            409,
        )

    views = None
    likes = None

    if mode == BOOST_MODE_VIEWS_ONLY:
        views_quantity = quantity if quantity is not None else _parse_views_quantity(None)
        if views_quantity is None or views_quantity < VIEWS_MIN:
            return {"ok": False, "error": f"Enter at least {VIEWS_MIN} views."}, 400
        views = _place(VIEWS_SERVICE_ID, canonical_url, views_quantity)
        all_ok = views.get("ok")
    elif mode == BOOST_MODE_LIKES_ONLY:
        likes_quantity = quantity if quantity is not None else _parse_likes_quantity(None)
        if likes_quantity is None or likes_quantity < LIKES_MIN:
            return {"ok": False, "error": f"Enter at least {LIKES_MIN} likes."}, 400
        likes = _place(LIKES_SERVICE_ID, canonical_url, likes_quantity)
        all_ok = likes.get("ok")
    elif mode == BOOST_MODE_LOWER:
        views = _place(VIEWS_SERVICE_ID, canonical_url, LOW_VIEWS_QUANTITY)
        likes = _place(LIKES_SERVICE_ID, canonical_url, LIKES_QUANTITY)
        all_ok = views.get("ok") and likes.get("ok")
    else:
        full_views_qty, full_likes_qty = _full_pack_quantities(current_likes)
        views = _place(VIEWS_SERVICE_ID, canonical_url, full_views_qty)
        likes = _place(LIKES_SERVICE_ID, canonical_url, full_likes_qty)
        all_ok = views.get("ok") and likes.get("ok")

    try:
        balance = client.balance()
    except Exception:
        balance = None

    if all_ok and mode == BOOST_MODE_FULL:
        record_boost(
            video_id,
            url=canonical_url,
            boost_mode=mode,
            views_order=views.get("order") if views else None,
            likes_order=likes.get("order") if likes else None,
        )
        invalidate_cache()

    return (
        {
            "ok": all_ok,
            "mode": mode,
            "url": canonical_url,
            "video_id": video_id,
            "views": views,
            "likes": likes,
            "balance": balance,
        },
        200 if all_ok else 200,
    )


@app.get("/")
def index():
    try:
        balance = client.balance()
    except Exception:
        balance = None
    return render_template(
        "index.html",
        views_service=VIEWS_SERVICE_ID,
        views_qty=VIEWS_QUANTITY,
        low_views_qty=LOW_VIEWS_QUANTITY,
        views_only_qty=VIEWS_ONLY_QUANTITY,
        views_min=VIEWS_MIN,
        likes_service=LIKES_SERVICE_ID,
        likes_qty=LIKES_QUANTITY,
        full_likes_min=FULL_LIKES_MIN,
        full_likes_max=FULL_LIKES_MAX,
        full_views_low=FULL_VIEWS_LOW,
        full_views_mid=FULL_VIEWS_MID,
        full_views_high=FULL_VIEWS_HIGH,
        full_views_ultra=FULL_VIEWS_ULTRA,
        likes_only_qty=LIKES_ONLY_QUANTITY,
        likes_min=LIKES_MIN,
        balance=balance,
        queue_profile=QUEUE_PROFILE,
        queue_lookback_hours=QUEUE_LOOKBACK_HOURS,
        queue_min_age_hours=QUEUE_MIN_AGE_HOURS,
    )


@app.get("/api/queue")
def queue():
    force_refresh = request.args.get("refresh") == "1"
    payload = build_queue(force_refresh=force_refresh)
    status = 200 if payload.get("ok") else 502
    return jsonify(payload), status


@app.post("/api/preview")
def preview():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    mode = _normalize_mode(body.get("mode"))
    if not url:
        return jsonify({"ok": False, "error": "Please enter a video URL."}), 400
    try:
        stats = get_video_stats(url)
        video_id = str(stats["video_id"])
        already_boosted = mode == BOOST_MODE_FULL and is_full_boosted(video_id)
        payload = {"ok": True, **stats, "already_boosted": already_boosted}
        if already_boosted:
            payload.update(_already_boosted_payload(video_id))
        return jsonify(payload)
    except TikTokStatsError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Failed to load stats: {exc}"}), 500


@app.post("/api/boost")
def boost():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or request.form.get("url") or "").strip()
    mode = _normalize_mode(body.get("mode"))
    from_queue = bool(body.get("from_queue"))

    quantity = None
    if mode == BOOST_MODE_VIEWS_ONLY:
        quantity = _parse_views_quantity(body.get("quantity"))
        if quantity is None:
            return jsonify({"ok": False, "error": f"Enter at least {VIEWS_MIN} views."}), 400
    elif mode == BOOST_MODE_LIKES_ONLY:
        quantity = _parse_likes_quantity(body.get("quantity"))
        if quantity is None:
            return jsonify({"ok": False, "error": f"Enter at least {LIKES_MIN} likes."}), 400

    payload, status = _run_boost(url, mode, quantity=quantity, from_queue=from_queue)
    return jsonify(payload), status


if __name__ == "__main__":
    import os

    port = int(os.getenv("PORT", "5050"))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
