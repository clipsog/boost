"""Zefame SMM API v2 client (https://zefame.com/api/v2)."""

from __future__ import annotations

import os
from typing import Any

import requests

API_URL = "https://zefame.com/api/v2"


class ZefameAPIError(Exception):
    pass


class ZefameClient:
    def __init__(self, api_key: str | None = None, api_url: str = API_URL) -> None:
        self.api_key = api_key or os.environ.get("ZEFAME_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "API key required. Set ZEFAME_API_KEY in .env or pass api_key=..."
            )
        self.api_url = api_url
        self._session = requests.Session()

    def _post(self, **params: Any) -> Any:
        payload = {"key": self.api_key, **params}
        response = self._session.post(self.api_url, data=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "error" in data:
            raise ZefameAPIError(data["error"])
        return data

    def services(self) -> list[dict[str, Any]]:
        return self._post(action="services")

    def balance(self) -> dict[str, str]:
        return self._post(action="balance")

    def add_order(
        self,
        service: int,
        link: str,
        *,
        quantity: int | None = None,
        runs: int | None = None,
        interval: int | None = None,
        comments: str | None = None,
        username: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "action": "add",
            "service": service,
            "link": link,
        }
        if quantity is not None:
            params["quantity"] = quantity
        if runs is not None:
            params["runs"] = runs
        if interval is not None:
            params["interval"] = interval
        if comments is not None:
            params["comments"] = comments
        if username is not None:
            params["username"] = username
        params.update(extra)
        return self._post(**params)

    def order_status(self, order_id: int | str) -> dict[str, Any]:
        return self._post(action="status", order=order_id)

    def orders_status(self, order_ids: list[int | str]) -> dict[str, Any]:
        return self._post(
            action="status", orders=",".join(str(i) for i in order_ids)
        )

    def refill(self, order_id: int | str) -> dict[str, Any]:
        return self._post(action="refill", order=order_id)

    def refills(self, order_ids: list[int | str]) -> list[dict[str, Any]]:
        return self._post(
            action="refill", orders=",".join(str(i) for i in order_ids)
        )

    def refill_status(self, refill_id: int | str) -> dict[str, Any]:
        return self._post(action="refill_status", refill=refill_id)

    def refills_status(self, refill_ids: list[int | str]) -> list[dict[str, Any]]:
        return self._post(
            action="refill_status",
            refills=",".join(str(i) for i in refill_ids),
        )

    def cancel(self, order_ids: list[int | str]) -> list[dict[str, Any]]:
        return self._post(
            action="cancel", orders=",".join(str(i) for i in order_ids)
        )
