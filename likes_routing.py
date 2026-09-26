"""Choose Zefame vs Boostero for likes orders (auto by catalog + quantity)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LikesRoute:
    panel: str
    service_id: int
    reason: str


def _int_bound(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def zefame_service_usable(
    catalog: dict[int, dict[str, Any]],
    service_id: int,
    quantity: int,
    *,
    site_maintenance_ids: frozenset[int] | set[int] | None = None,
) -> tuple[bool, str]:
    if site_maintenance_ids and int(service_id) in site_maintenance_ids:
        return False, "site_maintenance"
    return service_accepts_quantity(catalog, service_id, quantity)


def service_accepts_quantity(
    catalog: dict[int, dict[str, Any]],
    service_id: int,
    quantity: int,
) -> tuple[bool, str]:
    row = catalog.get(service_id)
    if not row:
        return False, "not_listed"
    min_q = _int_bound(row.get("min"), 1)
    max_q = _int_bound(row.get("max"), 999_999_999)
    if quantity < min_q:
        return False, f"min_{min_q}"
    if quantity > max_q:
        return False, "above_max"
    return True, "catalog_ok"


def resolve_likes_route(
    quantity: int,
    *,
    preference: str,
    zefame_service_id: int,
    boostero_service_id: int,
    zefame_catalog: dict[int, dict[str, Any]],
    boostero_catalog: dict[int, dict[str, Any]],
    boostero_configured: bool,
    zefame_site_maintenance_ids: frozenset[int] | set[int] | None = None,
) -> LikesRoute:
    pref = (preference or "auto").lower()
    maint = zefame_site_maintenance_ids or frozenset()

    def zefame() -> tuple[LikesRoute | None, str]:
        ok, detail = zefame_service_usable(
            zefame_catalog,
            zefame_service_id,
            quantity,
            site_maintenance_ids=maint,
        )
        if ok:
            return LikesRoute("zefame", zefame_service_id, detail), detail
        return None, detail

    def boostero() -> tuple[LikesRoute | None, str]:
        if not boostero_configured:
            return None, "boostero_not_configured"
        ok, detail = service_accepts_quantity(
            boostero_catalog, boostero_service_id, quantity
        )
        if ok:
            return LikesRoute("boostero", boostero_service_id, detail), detail
        return None, detail

    if pref == "zefame":
        route, detail = zefame()
        if route:
            return route
        raise ValueError(
            f"Zefame likes service {zefame_service_id} cannot take {quantity} "
            f"({detail}). Set LIKES_PANEL=auto or fix ZEFAME_LIKES_SERVICE."
        )

    if pref == "boostero":
        route, detail = boostero()
        if route:
            return route
        raise ValueError(
            f"Boostero likes service {boostero_service_id} unavailable for "
            f"{quantity} ({detail}). Check BOOSTERO_API_KEY / BOOSTERO_LIKES_SERVICE."
        )

    # auto: Zefame when site + API allow it; else Boostero (same badge as zefame.com).
    route, _z_detail = zefame()
    if route:
        return route

    route, _b_detail = boostero()
    if route:
        return LikesRoute(
            "boostero",
            boostero_service_id,
            f"zefame_unusable:{_z_detail}",
        )

    raise ValueError(
        f"No likes panel can take {quantity} likes "
        f"(Zefame {zefame_service_id}: {_z_detail}; "
        f"Boostero {boostero_service_id}: {_b_detail})."
    )


def route_for_service_id(
    service_id: int,
    quantity: int,
    *,
    zefame_service_id: int,
    boostero_service_id: int,
    zefame_catalog: dict[int, dict[str, Any]],
    boostero_catalog: dict[int, dict[str, Any]],
    zefame_site_maintenance_ids: frozenset[int] | set[int] | None = None,
) -> LikesRoute:
    if service_id in zefame_catalog:
        ok, detail = zefame_service_usable(
            zefame_catalog,
            service_id,
            quantity,
            site_maintenance_ids=zefame_site_maintenance_ids,
        )
        if not ok:
            raise ValueError(
                f"Zefame service {service_id} cannot take {quantity} ({detail})."
            )
        return LikesRoute("zefame", service_id, "explicit")
    if service_id in boostero_catalog:
        ok, detail = service_accepts_quantity(boostero_catalog, service_id, quantity)
        if not ok:
            raise ValueError(
                f"Boostero service {service_id} cannot take {quantity} ({detail})."
            )
        return LikesRoute("boostero", service_id, "explicit")
    if service_id == boostero_service_id:
        return LikesRoute("boostero", service_id, "explicit")
    if service_id == zefame_service_id:
        ok, detail = zefame_service_usable(
            zefame_catalog,
            zefame_service_id,
            quantity,
            site_maintenance_ids=zefame_site_maintenance_ids,
        )
        if not ok:
            raise ValueError(
                f"Zefame service {service_id} cannot take {quantity} ({detail})."
            )
        return LikesRoute("zefame", service_id, "explicit")
    raise ValueError(f"Service {service_id} is not in Zefame or Boostero catalogs.")


def humanize_zefame_detail(detail: str, service_id: int, quantity: int) -> str:
    if detail == "site_maintenance":
        return (
            f"#{service_id} is En maintenance on zefame.com — "
            "not used for likes even if the API still lists it."
        )
    if detail == "not_listed":
        return f"#{service_id} is not listed in the Zefame API."
    if detail.startswith("min_"):
        return (
            f"#{service_id} needs at least {detail[4:]} likes via API "
            f"(this pack sends {quantity})."
        )
    if detail == "above_max":
        return f"#{service_id} quantity {quantity} is above the Zefame API maximum."
    if detail == "catalog_ok":
        return f"#{service_id} passes site + API checks for {quantity} likes."
    return detail


def _format_maintenance_id_list(ids: list[int] | None) -> str:
    if not ids:
        return "none listed"
    return ", ".join(f"#{sid}" for sid in sorted(ids))


def build_likes_decision_flow(
    *,
    quantity: int,
    preference: str,
    zefame_service_id: int,
    boostero_service_id: int,
    views_service_id: int | None,
    z_ok: bool,
    z_detail: str,
    b_ok: bool,
    b_detail: str,
    in_site_maintenance: bool,
    site_maintenance_fetch_ok: bool | None,
    site_maintenance_ids_count: int | None,
    maintenance_service_ids: list[int] | None,
    active_panel: str | None,
    active_service: int | None,
) -> dict[str, Any]:
    pref = (preference or "auto").lower()
    z_human = humanize_zefame_detail(z_detail, zefame_service_id, quantity)
    steps: list[dict[str, Any]] = []

    maint_ids = sorted(maintenance_service_ids or [])
    maint_set = set(maint_ids)
    configured_in_maint: list[int] = []
    if zefame_service_id in maint_set:
        configured_in_maint.append(zefame_service_id)
    if views_service_id is not None and int(views_service_id) in maint_set:
        configured_in_maint.append(int(views_service_id))
    maint_list_text = _format_maintenance_id_list(maint_ids)

    if pref == "auto":
        fetch_ok = site_maintenance_fetch_ok is not False
        if site_maintenance_fetch_ok is False:
            maint_detail = (
                "Could not refresh zefame.com maintenance list "
                "(using last cached data if any)."
            )
        else:
            maint_detail = (
                f"En maintenance on zefame.com ({site_maintenance_ids_count or len(maint_ids)}): "
                f"{maint_list_text}."
            )
            if configured_in_maint:
                cfg = ", ".join(f"#{sid}" for sid in configured_in_maint)
                maint_detail += f" This app uses {cfg} — affected."
            elif in_site_maintenance:
                maint_detail += (
                    f" Likes service #{zefame_service_id} is on the list."
                )
            else:
                maint_detail += (
                    f" Likes #{zefame_service_id} not on the list"
                    + (
                        f"; views #{views_service_id} not on the list."
                        if views_service_id is not None
                        else "."
                    )
                )
        steps.append(
            {
                "order": 1,
                "title": "Read zefame.com maintenance (ZFM_MAINT.ids)",
                "ok": fetch_ok,
                "detail": maint_detail,
            }
        )
        steps.append(
            {
                "order": 2,
                "title": f"Check Zefame likes #{zefame_service_id} for {quantity} likes",
                "ok": z_ok,
                "detail": z_human,
            }
        )
        if z_ok:
            steps.append(
                {
                    "order": 3,
                    "title": "Likes destination",
                    "ok": True,
                    "detail": f"Use Zefame #{active_service or zefame_service_id} (Boostero skipped).",
                }
            )
            summary = (
                f"Likes: Zefame #{zefame_service_id} checked first and available "
                f"→ Zefame for {quantity} likes."
            )
        else:
            steps.append(
                {
                    "order": 3,
                    "title": f"Fallback Boostero #{boostero_service_id}",
                    "ok": b_ok,
                    "detail": (
                        f"Use Boostero for {quantity} likes."
                        if b_ok and active_panel == "boostero"
                        else f"Boostero not usable ({b_detail})."
                    ),
                }
            )
            summary = (
                f"Likes: Zefame #{zefame_service_id} checked first ({z_human}) "
                f"→ {'Boostero #' + str(active_service) if b_ok and active_panel == 'boostero' else 'no fallback'}."
            )
        return {
            "zefame_checked_first": True,
            "decision_summary": summary,
            "decision_steps": steps,
            "maintenance_service_ids": maint_ids,
            "configured_services_in_maintenance": configured_in_maint,
            "maintenance_services_label": maint_list_text,
        }

    if pref == "zefame":
        steps = [
            {
                "order": 1,
                "title": "LIKES_PANEL=zefame",
                "ok": z_ok,
                "detail": "Auto routing disabled; Zefame only (site list still checked on order).",
            },
            {
                "order": 2,
                "title": f"Zefame #{zefame_service_id}",
                "ok": z_ok,
                "detail": z_human,
            },
        ]
        return {
            "zefame_checked_first": False,
            "decision_summary": f"Likes: forced Zefame #{zefame_service_id} (LIKES_PANEL=zefame).",
            "decision_steps": steps,
            "maintenance_service_ids": maint_ids,
            "configured_services_in_maintenance": configured_in_maint,
            "maintenance_services_label": maint_list_text,
        }

    steps = [
        {
            "order": 1,
            "title": "LIKES_PANEL=boostero",
            "ok": b_ok,
            "detail": "Zefame not consulted for routing (Boostero forced).",
        },
        {
            "order": 2,
            "title": f"Boostero #{boostero_service_id}",
            "ok": b_ok,
            "detail": (
                f"Use Boostero for {quantity} likes."
                if b_ok
                else f"Unavailable ({b_detail})."
            ),
        },
    ]
    return {
        "zefame_checked_first": False,
        "decision_summary": f"Likes: forced Boostero #{boostero_service_id} (LIKES_PANEL=boostero).",
        "decision_steps": steps,
        "maintenance_service_ids": maint_ids,
        "configured_services_in_maintenance": configured_in_maint,
        "maintenance_services_label": maint_list_text,
    }


def likes_routing_status(
    quantity: int,
    *,
    preference: str,
    zefame_service_id: int,
    boostero_service_id: int,
    zefame_catalog: dict[int, dict[str, Any]],
    boostero_catalog: dict[int, dict[str, Any]],
    boostero_configured: bool,
    zefame_site_maintenance_ids: frozenset[int] | set[int] | None = None,
    site_maintenance_meta: dict[str, Any] | None = None,
    views_service_id: int | None = None,
) -> dict[str, Any]:
    maint = zefame_site_maintenance_ids or frozenset()
    meta = site_maintenance_meta or {}
    maint_ids_list = sorted(meta.get("maintenance_service_ids") or maint)
    z_ok, z_detail = zefame_service_usable(
        zefame_catalog,
        zefame_service_id,
        quantity,
        site_maintenance_ids=maint,
    )
    b_ok, b_detail = (
        service_accepts_quantity(boostero_catalog, boostero_service_id, quantity)
        if boostero_configured
        else (False, "boostero_not_configured")
    )
    try:
        active = resolve_likes_route(
            quantity,
            preference=preference,
            zefame_service_id=zefame_service_id,
            boostero_service_id=boostero_service_id,
            zefame_catalog=zefame_catalog,
            boostero_catalog=boostero_catalog,
            boostero_configured=boostero_configured,
            zefame_site_maintenance_ids=maint,
        )
        active_panel = active.panel
        active_service = active.service_id
        active_reason = active.reason
    except ValueError as exc:
        active_panel = None
        active_service = None
        active_reason = str(exc)

    in_site_maint = int(zefame_service_id) in maint
    decision = build_likes_decision_flow(
        quantity=quantity,
        preference=preference,
        zefame_service_id=zefame_service_id,
        boostero_service_id=boostero_service_id,
        views_service_id=views_service_id,
        z_ok=z_ok,
        z_detail=z_detail,
        b_ok=b_ok,
        b_detail=b_detail,
        in_site_maintenance=in_site_maint,
        site_maintenance_fetch_ok=meta.get("fetch_ok"),
        site_maintenance_ids_count=meta.get("maintenance_ids_count"),
        maintenance_service_ids=maint_ids_list,
        active_panel=active_panel,
        active_service=active_service,
    )

    return {
        "preference": preference,
        "quantity_checked": quantity,
        "zefame_service": zefame_service_id,
        "boostero_service": boostero_service_id,
        "zefame_eligible": z_ok,
        "zefame_detail": z_detail,
        "boostero_eligible": b_ok,
        "boostero_detail": b_detail,
        "zefame_site_maintenance": in_site_maint,
        "active_panel": active_panel,
        "active_service": active_service,
        "active_reason": active_reason,
        **decision,
    }
