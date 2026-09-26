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
) -> dict[str, Any]:
    maint = zefame_site_maintenance_ids or frozenset()
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

    return {
        "preference": preference,
        "quantity_checked": quantity,
        "zefame_service": zefame_service_id,
        "boostero_service": boostero_service_id,
        "zefame_eligible": z_ok,
        "zefame_detail": z_detail,
        "boostero_eligible": b_ok,
        "boostero_detail": b_detail,
        "zefame_site_maintenance": int(zefame_service_id) in maint,
        "active_panel": active_panel,
        "active_service": active_service,
        "active_reason": active_reason,
    }
