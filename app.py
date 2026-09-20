#!/usr/bin/env python3
"""Small web UI to boost a video with views + likes."""

from __future__ import annotations

import random
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request

from boost_history import (
    clear_assumed_boost,
    ensure_history_loaded,
    get_boost,
    has_views_boost,
    history_stats,
    is_full_boosted,
    merge_history,
    record_boost,
    record_partial_views,
)
from boost_queue import (
    build_queue,
    invalidate_cache,
    mark_video_boosted_in_cache,
    queue_item_for_url,
    queue_item_for_video,
    queue_item_from_hint,
    stats_from_queue_item,
)
from config import (
    BOOST_ADMIN_SECRET,
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
from tiktok_stats import TikTokStatsError, extract_video_id, get_video_stats
from zefame_client import ZefameAPIError, ZefameClient

load_dotenv(Path(__file__).resolve().parent / ".env")

app = Flask(__name__)
client = ZefameClient()

_seed_result = ensure_history_loaded()
if _seed_result:
    print(
        f"Loaded boost history seed: { _seed_result['added'] } added, "
        f"{ _seed_result['total'] } total",
        flush=True,
    )

BOOST_MODE_FULL = "full"
BOOST_MODE_LOWER = "lower"
BOOST_MODE_VIEWS_ONLY = "views_only"
BOOST_MODE_LIKES_ONLY = "likes_only"
BOOST_MODE_COMPLETE = "complete"

VIEWS_BOOST_DELTA = 250


def _is_link_duplicate_error(error: str | None) -> bool:
    if not error:
        return False
    lower = error.lower()
    return "link_duplicate" in lower or ("duplicate" in lower and "link" in lower)


def _place(service_id: int, link: str, quantity: int) -> dict:
    try:
        result = client.add_order(service_id, link, quantity=quantity)
        return {"ok": True, "service": service_id, "quantity": quantity, **result}
    except ZefameAPIError as exc:
        err = str(exc)
        if _is_link_duplicate_error(err):
            return {
                "ok": True,
                "service": service_id,
                "quantity": quantity,
                "duplicate_skipped": True,
                "note": "Zefame already has an order for this link on this service.",
                "error": err,
            }
        return {"ok": False, "service": service_id, "quantity": quantity, "error": err}
    except Exception as exc:
        err = str(exc)
        if "401" in err or "Unauthorized" in err:
            err = (
                "Zefame API rejected the request (check ZEFAME_API_KEY in .env). "
                f"Details: {err}"
            )
        return {"ok": False, "service": service_id, "quantity": quantity, "error": err}


_service_rates: dict[int, float] | None = None


def _load_service_rates() -> dict[int, float]:
    global _service_rates
    if _service_rates is not None:
        return _service_rates
    try:
        _service_rates = {
            int(service["service"]): float(service["rate"])
            for service in client.services()
        }
    except Exception:
        _service_rates = {}
    return _service_rates


def _validate_service_ids() -> None:
    rates = _load_service_rates()
    if not rates:
        print("Warning: could not load Zefame services list.", flush=True)
        return
    for label, service_id in (("views", VIEWS_SERVICE_ID), ("likes", LIKES_SERVICE_ID)):
        if service_id not in rates:
            print(
                f"Warning: ZEFAME_{label.upper()}_SERVICE={service_id} is not in the "
                "Zefame services list. Orders for that leg may fail until you update env.",
                flush=True,
            )
            continue
    print(
        f"Zefame services: views={VIEWS_SERVICE_ID}, likes={LIKES_SERVICE_ID}",
        flush=True,
    )


_validate_service_ids()


def _order_cost(service_id: int, quantity: int) -> float:
    rates = _load_service_rates()
    if service_id not in rates:
        raise ZefameAPIError(f"Service {service_id} is not available on Zefame.")
    rate = rates[service_id]
    return rate * quantity / 1000


def _balance_error_for_pack(views_qty: int, likes_qty: int) -> str | None:
    """Refuse dual orders unless balance covers both legs."""
    try:
        balance = float(client.balance()["balance"])
    except Exception:
        return None

    try:
        needed = _order_cost(VIEWS_SERVICE_ID, views_qty) + _order_cost(LIKES_SERVICE_ID, likes_qty)
    except ZefameAPIError as exc:
        return str(exc)

    if needed <= 0:
        return None
    if balance + 1e-9 < needed:
        currency = "EUR"
        try:
            currency = client.balance().get("currency") or currency
        except Exception:
            pass
        return (
            f"Insufficient balance for views + likes "
            f"(need ~{needed:.4f} {currency}, have {balance:.4f} {currency}). "
            "Top up before boosting so both orders are sent."
        )
    return None


def _try_cancel_order(order_id: int | str | None) -> dict | None:
    if not order_id:
        return None
    try:
        return client.cancel([order_id])
    except Exception as exc:
        return {"ok": False, "error": str(exc), "order": order_id}


def _boost_history_kwargs(
    views: dict | None,
    likes: dict | None,
    *,
    views_at_boost: int | None = None,
    likes_at_boost: int | None = None,
) -> dict[str, Any]:
    return {
        "views_order": (views or {}).get("order"),
        "likes_order": (likes or {}).get("order"),
        "views_at_boost": views_at_boost,
        "likes_at_boost": likes_at_boost,
        "zefame_duplicate": bool((views or {}).get("duplicate_skipped"))
        or bool((likes or {}).get("duplicate_skipped")),
    }


def _place_views_and_likes(
    link: str,
    views_qty: int,
    likes_qty: int,
) -> tuple[dict, dict]:
    """Place likes then views; roll back the first order if the second fails."""
    balance_error = _balance_error_for_pack(views_qty, likes_qty)
    if balance_error:
        failed = {
            "ok": False,
            "service": LIKES_SERVICE_ID,
            "quantity": likes_qty,
            "error": balance_error,
        }
        return failed, failed

    likes = _place(LIKES_SERVICE_ID, link, likes_qty)
    if not likes.get("ok"):
        return None, likes

    views = _place(VIEWS_SERVICE_ID, link, views_qty)
    if views.get("ok"):
        return views, likes

    if _is_link_duplicate_error(views.get("error")):
        views = {**views, "ok": True, "duplicate_skipped": True}
        return views, likes

    if likes.get("order") and not likes.get("duplicate_skipped"):
        cancel_result = _try_cancel_order(likes.get("order"))
        if cancel_result is not None:
            likes["cancel_attempt"] = cancel_result
        likes["rolled_back"] = bool(cancel_result)
    return views, likes


def _normalize_mode(mode: str | None) -> str:
    if mode == BOOST_MODE_VIEWS_ONLY:
        return BOOST_MODE_VIEWS_ONLY
    if mode == BOOST_MODE_LIKES_ONLY:
        return BOOST_MODE_LIKES_ONLY
    if mode == BOOST_MODE_LOWER:
        return BOOST_MODE_LOWER
    if mode == BOOST_MODE_COMPLETE:
        return BOOST_MODE_COMPLETE
    return BOOST_MODE_FULL


def _views_already_sent(
    video_id: str,
    *,
    baseline_views: int,
    fresh_item: dict[str, Any] | None,
) -> tuple[bool, str]:
    if has_views_boost(video_id):
        return True, "history"

    current_views = int((fresh_item or {}).get("views") or baseline_views or 0)
    if baseline_views and current_views >= baseline_views + VIEWS_BOOST_DELTA:
        return True, "view_count_increased"
    return False, "view_count_unchanged"


def _resolve_queue_target(
    url: str,
    queue_hint: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    item = None
    if queue_hint and str(queue_hint.get("video_id") or extract_video_id(url) or ""):
        item = queue_item_from_hint(url, queue_hint)
    if not item:
        item = queue_item_for_url(url)
    if not item:
        video_id_hint = extract_video_id(url)
        if video_id_hint:
            item = queue_item_for_video(video_id_hint)
    if not item:
        return None, None

    stats = stats_from_queue_item(item)
    fresh_item = queue_item_for_video(stats["video_id"], force_refresh=True) or item
    return item, fresh_item


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


def _full_pack_views_quantity(current_likes: int) -> int:
    if current_likes == 0:
        return FULL_VIEWS_LOW
    if current_likes <= 7:
        return FULL_VIEWS_MID
    if current_likes < 20:
        return FULL_VIEWS_HIGH
    return FULL_VIEWS_ULTRA


def _full_pack_likes_quantity(video_id: str | None = None) -> int:
    """10–14 likes; stable per video_id so queue estimates match orders."""
    if video_id:
        return random.Random(str(video_id)).randint(FULL_LIKES_MIN, FULL_LIKES_MAX)
    return random.randint(FULL_LIKES_MIN, FULL_LIKES_MAX)


def _full_pack_quantities(
    current_likes: int,
    video_id: str | None = None,
) -> tuple[int, int]:
    """Views from current like tier; likes 10–14 (fixed per video_id)."""
    return _full_pack_views_quantity(current_likes), _full_pack_likes_quantity(video_id)


def _boost_pack_plan(item: dict[str, Any]) -> dict[str, int]:
    current_likes = int(item.get("likes") or 0)
    video_id = str(item.get("video_id") or "")
    views_qty, likes_qty = _full_pack_quantities(current_likes, video_id)
    return {"views": views_qty, "likes": likes_qty}


def _estimate_boost_all_ready(ready: list[dict[str, Any]]) -> dict[str, Any]:
    """Exact full-pack cost from each video's queue stats and boost plan."""
    count = len(ready)
    if count == 0:
        try:
            balance_raw = client.balance()
            balance = float(balance_raw["balance"])
            currency = balance_raw.get("currency") or "EUR"
        except Exception:
            balance = None
            currency = "EUR"
        return {
            "ok": True,
            "count": 0,
            "balance": balance,
            "currency": currency,
            "cost": 0.0,
            "sufficient": True,
            "shortfall": 0.0,
            "views_total": 0,
            "likes_total": 0,
            "views_service": VIEWS_SERVICE_ID,
            "likes_service": LIKES_SERVICE_ID,
        }

    try:
        balance_raw = client.balance()
        balance = float(balance_raw["balance"])
        currency = balance_raw.get("currency") or "EUR"
    except Exception as exc:
        return {"ok": False, "error": f"Could not load balance: {exc}"}

    try:
        cost_total = 0.0
        views_total = 0
        likes_total = 0
        for item in ready:
            plan = item.get("boost_pack") or _boost_pack_plan(item)
            views_qty = int(plan["views"])
            likes_qty = int(plan["likes"])
            views_total += views_qty
            likes_total += likes_qty
            cost_total += _order_cost(VIEWS_SERVICE_ID, views_qty) + _order_cost(
                LIKES_SERVICE_ID, likes_qty
            )
    except ZefameAPIError as exc:
        return {"ok": False, "error": str(exc)}

    cost_total = round(cost_total, 4)
    shortfall = round(max(0.0, cost_total - balance), 4)
    sufficient = balance + 1e-9 >= cost_total

    return {
        "ok": True,
        "count": count,
        "balance": balance,
        "currency": currency,
        "cost": cost_total,
        "sufficient": sufficient,
        "shortfall": shortfall,
        "views_total": views_total,
        "likes_total": likes_total,
        "views_service": VIEWS_SERVICE_ID,
        "likes_service": LIKES_SERVICE_ID,
    }


def _already_boosted_payload(video_id: str) -> dict:
    entry = get_boost(video_id) or {}
    return {
        "already_boosted": True,
        "boosted_at": entry.get("boosted_at"),
        "previous_url": entry.get("url"),
        "boost_mode": entry.get("boost_mode"),
    }


def _order_errors(views: dict | None, likes: dict | None) -> str | None:
    parts: list[str] = []
    if views and not views.get("ok"):
        parts.append(f"Views: {views.get('error') or 'order failed'}")
    if likes and not likes.get("ok"):
        parts.append(f"Likes: {likes.get('error') or 'order failed'}")
    return "; ".join(parts) if parts else None


def _run_complete_boost(
    url: str,
    *,
    queue_hint: dict[str, Any] | None = None,
) -> tuple[dict, int]:
    item, fresh_item = _resolve_queue_target(url, queue_hint)
    if not item:
        return {
            "ok": False,
            "error": "Video is not in the approval queue.",
            "video_id": extract_video_id(url),
        }, 400

    stats = stats_from_queue_item(fresh_item or item)
    video_id = str(stats["video_id"])
    canonical_url = stats["url"]
    current_likes = stats["likes"]
    baseline_views = int(queue_hint.get("views") if queue_hint else item.get("views") or 0)

    if is_full_boosted(video_id):
        return (
            {
                "ok": False,
                "error": "This video already received the full boost pack.",
                "video_id": video_id,
                **_already_boosted_payload(video_id),
            },
            409,
        )

    views_sent, views_reason = _views_already_sent(
        video_id,
        baseline_views=baseline_views,
        fresh_item=fresh_item,
    )
    likes_qty = _full_pack_likes_quantity(video_id)
    views = None
    likes = None

    if views_sent:
        likes = _place(LIKES_SERVICE_ID, canonical_url, likes_qty)
        views = {"ok": True, "skipped": True, "reason": views_reason}
        all_ok = bool(likes.get("ok"))
        existing = get_boost(video_id) or {}
        views_order = existing.get("views_order")
    else:
        views_qty, likes_qty = _full_pack_quantities(current_likes, video_id)
        balance_error = _balance_error_for_pack(views_qty, likes_qty)
        if balance_error:
            return {"ok": False, "error": balance_error, "video_id": video_id}, 400
        views, likes = _place_views_and_likes(canonical_url, views_qty, likes_qty)
        all_ok = bool(views and views.get("ok") and likes.get("ok"))
        views_order = views.get("order") if views else None
        if views and views.get("ok") and likes and not likes.get("ok"):
            record_partial_views(
                video_id,
                url=canonical_url,
                views_order=views_order,
                views_at_queue=baseline_views,
            )

    try:
        balance = client.balance()
    except Exception:
        balance = None

    if all_ok:
        record_boost(
            video_id,
            url=canonical_url,
            **_boost_history_kwargs(
                views,
                likes,
                views_at_boost=baseline_views,
                likes_at_boost=current_likes,
            ),
        )
        mark_video_boosted_in_cache(video_id)

    order_error = _order_errors(views, likes)
    if not all_ok and likes and not likes.get("ok") and not views:
        order_error = (
            f"{order_error or likes.get('error') or 'Likes order failed'}; "
            "views were not sent."
        )
    return (
        {
            "ok": all_ok,
            "error": order_error,
            "mode": BOOST_MODE_COMPLETE,
            "views_sent_already": views_sent,
            "url": canonical_url,
            "video_id": video_id,
            "views": views,
            "likes": likes,
            "balance": balance,
        },
        200,
    )


def _run_boost(
    url: str,
    mode: str,
    *,
    quantity: int | None = None,
    likes_service: int | None = None,
    from_queue: bool = False,
    queue_hint: dict[str, Any] | None = None,
) -> tuple[dict, int]:
    if not url:
        return {"ok": False, "error": "Please enter a video URL."}, 400

    if mode == BOOST_MODE_COMPLETE:
        return _run_complete_boost(url, queue_hint=queue_hint)

    if from_queue:
        item = None
        if queue_hint and str(queue_hint.get("video_id") or extract_video_id(url) or ""):
            item = queue_item_from_hint(url, queue_hint)
        if not item:
            item = queue_item_for_url(url)
        if not item:
            video_id_hint = extract_video_id(url)
            if video_id_hint:
                item = queue_item_for_video(video_id_hint)
        if not item:
            return {
                "ok": False,
                "error": "Video is not in the approval queue.",
                "video_id": extract_video_id(url),
            }, 400

        stats = stats_from_queue_item(item)
        video_id = str(stats["video_id"])
        canonical_url = stats["url"]
        current_likes = int(stats["likes"])
        current_views = int(stats.get("views") or 0)

        if mode == BOOST_MODE_FULL:
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
    else:
        try:
            stats = get_video_stats(url)
            video_id = str(stats["video_id"])
            canonical_url = stats["url"]
            current_likes = int(stats["likes"])
            current_views = int(stats.get("views") or 0)
        except TikTokStatsError as exc:
            return {"ok": False, "error": str(exc)}, 400

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
        service_id = likes_service if likes_service is not None else LIKES_SERVICE_ID
        likes = _place(service_id, canonical_url, likes_quantity)
        all_ok = likes.get("ok")
    elif mode == BOOST_MODE_LOWER:
        balance_error = _balance_error_for_pack(LOW_VIEWS_QUANTITY, LIKES_QUANTITY)
        if balance_error:
            return {"ok": False, "error": balance_error}, 400
        views, likes = _place_views_and_likes(canonical_url, LOW_VIEWS_QUANTITY, LIKES_QUANTITY)
        all_ok = bool(views and views.get("ok") and likes.get("ok"))
    else:
        full_views_qty, full_likes_qty = _full_pack_quantities(current_likes, video_id)
        balance_error = _balance_error_for_pack(full_views_qty, full_likes_qty)
        if balance_error:
            return {"ok": False, "error": balance_error, "video_id": video_id}, 400
        views, likes = _place_views_and_likes(canonical_url, full_views_qty, full_likes_qty)
        all_ok = bool(views and views.get("ok") and likes.get("ok"))

    try:
        balance = client.balance()
    except Exception:
        balance = None

    if all_ok and mode == BOOST_MODE_FULL:
        record_boost(
            video_id,
            url=canonical_url,
            boost_mode=mode,
            **_boost_history_kwargs(
                views,
                likes,
                views_at_boost=current_views,
                likes_at_boost=current_likes,
            ),
        )
        mark_video_boosted_in_cache(video_id)

    order_error = _order_errors(views, likes)
    if not all_ok and likes and not likes.get("ok") and not views:
        order_error = (
            f"{order_error or likes.get('error') or 'Likes order failed'}; "
            "views were not sent."
        )
    elif not all_ok and views and likes:
        if not likes.get("ok"):
            order_error = (
                f"{order_error or likes.get('error') or 'Likes order failed'}; "
                "views were not sent."
            )
        elif not views.get("ok"):
            order_error = (
                f"{order_error or views.get('error') or 'Views order failed'}; "
                "likes cancel was attempted."
            )
    return (
        {
            "ok": all_ok,
            "error": order_error,
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
    if payload.get("ok"):
        payload["history"] = history_stats()
        ready = payload.get("ready") or []
        for item in ready:
            item["boost_pack"] = _boost_pack_plan(item)
        payload["ready"] = ready
        payload["boost_all_estimate"] = _estimate_boost_all_ready(ready)
    status = 200 if payload.get("ok") else 502
    return jsonify(payload), status


@app.get("/api/config")
def config_route():
    return jsonify(
        {
            "ok": True,
            "views_service": VIEWS_SERVICE_ID,
            "likes_service": LIKES_SERVICE_ID,
        }
    )


@app.get("/api/history/stats")
def history_stats_route():
    return jsonify({"ok": True, **history_stats()})


@app.post("/api/history/import")
def history_import():
    if not BOOST_ADMIN_SECRET:
        return jsonify({"ok": False, "error": "History import is not configured."}), 503

    body = request.get_json(silent=True) or {}
    provided = request.headers.get("X-Admin-Secret") or body.get("secret")
    if provided != BOOST_ADMIN_SECRET:
        return jsonify({"ok": False, "error": "Unauthorized."}), 401

    incoming = body.get("entries")
    if incoming is None and body and "secret" not in body:
        incoming = body
    if not isinstance(incoming, dict) or not incoming:
        return jsonify({"ok": False, "error": "Expected a JSON object of video entries."}), 400

    result = merge_history(incoming)
    invalidate_cache()
    return jsonify({"ok": True, **result, "history": history_stats()})


@app.post("/api/history/unmark")
def history_unmark():
    if not BOOST_ADMIN_SECRET:
        return jsonify({"ok": False, "error": "History unmark is not configured."}), 503

    body = request.get_json(silent=True) or {}
    provided = request.headers.get("X-Admin-Secret") or body.get("secret")
    if provided != BOOST_ADMIN_SECRET:
        return jsonify({"ok": False, "error": "Unauthorized."}), 401

    raw_ids = body.get("video_ids") or body.get("video_id")
    if raw_ids is None:
        return jsonify({"ok": False, "error": "Provide video_ids (array) or video_id."}), 400
    if isinstance(raw_ids, str):
        video_ids = [raw_ids]
    elif isinstance(raw_ids, list):
        video_ids = [str(v) for v in raw_ids if v]
    else:
        return jsonify({"ok": False, "error": "video_ids must be a list."}), 400

    cleared: list[str] = []
    skipped: list[str] = []
    for vid in video_ids:
        if clear_assumed_boost(vid):
            cleared.append(vid)
        else:
            skipped.append(vid)
    if cleared:
        invalidate_cache()
    return jsonify(
        {
            "ok": True,
            "cleared": cleared,
            "skipped": skipped,
            "history": history_stats(),
        }
    )


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

    likes_service = None
    raw_service = body.get("likes_service") if body.get("likes_service") is not None else body.get("service")
    if raw_service is not None:
        try:
            likes_service = int(raw_service)
        except (TypeError, ValueError):
            return jsonify({"ok": False, "error": "likes_service must be an integer."}), 400

    payload, status = _run_boost(
        url,
        mode,
        quantity=quantity,
        likes_service=likes_service,
        from_queue=from_queue,
        queue_hint=body if from_queue else None,
    )
    return jsonify(payload), status


@app.post("/api/queue/complete-boosts")
def complete_queue_boosts():
    queue = build_queue(force_refresh=True)
    if not queue.get("ok"):
        return jsonify(queue), 502

    results: list[dict[str, Any]] = []
    ok_count = 0
    fail_count = 0
    skip_count = 0

    targets: list[dict[str, Any]] = []
    for bucket in ("ready", "waiting"):
        targets.extend(queue.get(bucket) or [])

    for item in targets:
        video_id = str(item.get("video_id") or "")
        if is_full_boosted(video_id):
            skip_count += 1
            results.append({"video_id": video_id, "ok": True, "skipped": "already_boosted"})
            continue

        payload, _status = _run_complete_boost(
            item["url"],
            queue_hint=item,
        )
        results.append(payload)
        if payload.get("ok"):
            ok_count += 1
        else:
            fail_count += 1
        time.sleep(1.5)

    try:
        balance = client.balance()
    except Exception:
        balance = None

    return jsonify(
        {
            "ok": fail_count == 0,
            "processed": len(targets),
            "ok_count": ok_count,
            "fail_count": fail_count,
            "skip_count": skip_count,
            "balance": balance,
            "results": results,
        }
    )


if __name__ == "__main__":
    import os

    port = int(os.getenv("PORT", "5050"))
    debug = os.getenv("FLASK_DEBUG", "1") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
