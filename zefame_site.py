"""Zefame website signals not exposed on the SMM API (maintenance badges)."""

from __future__ import annotations

import os
import re
import time
from typing import Any

import requests

# Same bundle linked from zefame.com/services — contains ZFM_MAINT.ids (site maintenance).
DEFAULT_MAINTENANCE_JS_URL = (
    "https://storage.perfectcdn.com/rx634k/0603lai24ssymyjt.js"
)
_ZFM_MAINT_RE = re.compile(
    r"var\s+ZFM_MAINT\s*=\s*\{[^}]*?ids:\s*\[([^\]]*)\]",
    re.DOTALL,
)

_cache_ids: frozenset[int] | None = None
_cache_meta: dict[str, Any] | None = None
_cache_at: float = 0.0
_CACHE_TTL = float(os.getenv("ZEFAME_MAINTENANCE_CACHE_SECONDS", "300"))


def _parse_ids_from_js(text: str) -> frozenset[int]:
    match = _ZFM_MAINT_RE.search(text)
    if not match:
        raise ValueError("ZFM_MAINT.ids block not found in Zefame site JS.")
    inner = match.group(1)
    return frozenset(int(n) for n in re.findall(r"\d+", inner))


def fetch_maintenance_service_ids(
    *,
    js_url: str | None = None,
    session: requests.Session | None = None,
) -> frozenset[int]:
    url = (js_url or os.getenv("ZEFAME_MAINTENANCE_JS_URL") or DEFAULT_MAINTENANCE_JS_URL).strip()
    http = session or requests.Session()
    response = http.get(url, timeout=30)
    response.raise_for_status()
    return _parse_ids_from_js(response.text)


def get_zefame_maintenance_ids(*, force_refresh: bool = False) -> frozenset[int]:
    global _cache_ids, _cache_meta, _cache_at
    now = time.time()
    if (
        not force_refresh
        and _cache_ids is not None
        and now - _cache_at < _CACHE_TTL
    ):
        return _cache_ids

    url = (os.getenv("ZEFAME_MAINTENANCE_JS_URL") or DEFAULT_MAINTENANCE_JS_URL).strip()
    try:
        ids = fetch_maintenance_service_ids(js_url=url)
        _cache_meta = {"ok": True, "source": url, "error": None}
    except Exception as exc:
        if _cache_ids is not None:
            _cache_meta = {
                "ok": False,
                "source": url,
                "error": str(exc),
                "stale": True,
            }
            return _cache_ids
        _cache_meta = {"ok": False, "source": url, "error": str(exc)}
        _cache_ids = frozenset()
        _cache_at = now
        return _cache_ids

    _cache_ids = ids
    _cache_at = now
    if _cache_meta is None or _cache_meta.get("ok"):
        _cache_meta = {"ok": True, "source": url, "error": None}
    return _cache_ids


def maintenance_status(service_id: int) -> dict[str, Any]:
    ids = get_zefame_maintenance_ids()
    meta = _cache_meta or {}
    in_maintenance = int(service_id) in ids
    return {
        "service_id": int(service_id),
        "site_maintenance": in_maintenance,
        "source": meta.get("source"),
        "fetch_ok": bool(meta.get("ok")),
        "fetch_error": meta.get("error"),
        "stale": bool(meta.get("stale")),
        "maintenance_ids_count": len(ids),
    }
