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
# Default 0 = re-fetch on each routing/balance check (ZFM_MAINT.ids is often stale).
_CACHE_TTL = float(os.getenv("ZEFAME_MAINTENANCE_CACHE_SECONDS", "0"))
_CMS_SERVICES_URL = os.getenv(
    "ZEFAME_CMS_SERVICES_URL", "https://zefame.store/services-api.php"
)
_cms_ids: frozenset[int] | None = None
_cms_loaded_at: float = 0.0
_CMS_CACHE_TTL = float(os.getenv("ZEFAME_CMS_CACHE_SECONDS", "0"))


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


def fetch_cms_service_ids(
    *,
    cms_url: str | None = None,
    session: requests.Session | None = None,
) -> frozenset[int]:
    url = (cms_url or _CMS_SERVICES_URL).strip()
    http = session or requests.Session()
    response = http.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()
    ids: set[int] = set()
    if isinstance(data, list):
        for category in data:
            if not isinstance(category, dict):
                continue
            for row in category.get("s") or []:
                if isinstance(row, dict) and row.get("id") is not None:
                    ids.add(int(row["id"]))
    return frozenset(ids)


def get_cms_service_ids(*, force_refresh: bool = False) -> frozenset[int]:
    global _cms_ids, _cms_loaded_at
    now = time.time()
    if (
        not force_refresh
        and _cms_ids is not None
        and now - _cms_loaded_at < _CMS_CACHE_TTL
    ):
        return _cms_ids
    try:
        _cms_ids = fetch_cms_service_ids()
    except Exception:
        if _cms_ids is None:
            _cms_ids = frozenset()
    _cms_loaded_at = now
    return _cms_ids


def effective_site_maintenance_ids(
    *,
    smm_catalog_ids: frozenset[int] | set[int] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """ZFM_MAINT.ids is manual and often stale; cross-check live CMS + SMM API."""
    zfm_ids = get_zefame_maintenance_ids(force_refresh=force_refresh)
    cms_ids = get_cms_service_ids(force_refresh=force_refresh)
    smm = smm_catalog_ids or frozenset()
    zfm_meta = _cache_meta or {}

    overridden: list[int] = []
    effective: set[int] = set()
    for sid in zfm_ids:
        if sid in cms_ids and sid in smm:
            overridden.append(int(sid))
            continue
        effective.add(int(sid))

    return {
        "zfm_maintenance_service_ids": sorted(zfm_ids),
        "cms_service_ids_count": len(cms_ids),
        "effective_maintenance_service_ids": sorted(effective),
        "zfm_overridden_by_live_catalog": sorted(overridden),
        "source": zfm_meta.get("source"),
        "fetch_ok": bool(zfm_meta.get("ok")),
        "fetch_error": zfm_meta.get("error"),
        "stale": bool(zfm_meta.get("stale")),
        "checked_at": time.time(),
    }


def service_in_site_maintenance(
    service_id: int,
    *,
    smm_catalog_ids: frozenset[int] | set[int] | None = None,
    force_refresh: bool = False,
) -> tuple[bool, dict[str, Any]]:
    ctx = effective_site_maintenance_ids(
        smm_catalog_ids=smm_catalog_ids,
        force_refresh=force_refresh,
    )
    sid = int(service_id)
    in_maint = sid in set(ctx["effective_maintenance_service_ids"])
    ctx["service_id"] = sid
    ctx["site_maintenance"] = in_maint
    ctx["zfm_lists_service"] = sid in set(ctx["zfm_maintenance_service_ids"])
    ctx["cleared_by_live_catalog"] = sid in set(ctx["zfm_overridden_by_live_catalog"])
    return in_maint, ctx


def maintenance_status(
    service_id: int,
    *,
    smm_catalog_ids: frozenset[int] | set[int] | None = None,
    force_refresh: bool = False,
) -> dict[str, Any]:
    in_maintenance, ctx = service_in_site_maintenance(
        service_id,
        smm_catalog_ids=smm_catalog_ids,
        force_refresh=force_refresh,
    )
    return {
        "service_id": int(service_id),
        "site_maintenance": in_maintenance,
        "maintenance_service_ids": ctx["effective_maintenance_service_ids"],
        "maintenance_ids_count": len(ctx["effective_maintenance_service_ids"]),
        **ctx,
    }
