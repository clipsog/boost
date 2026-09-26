"""Boostero SMM API v2 client (https://boostero.com/api/v2)."""

from __future__ import annotations

import os
from typing import Any

import requests

API_URL = "https://boostero.com/api/v2"


class BoosteroAPIError(Exception):
    pass


class BoosteroClient:
    def __init__(self, api_key: str | None = None, api_url: str = API_URL) -> None:
        self.api_key = api_key or os.environ.get("BOOSTERO_API_KEY", "")
        if not self.api_key:
            raise ValueError(
                "API key required. Set BOOSTERO_API_KEY in .env or pass api_key=..."
            )
        self.api_url = api_url
        self._session = requests.Session()

    def _post(self, **params: Any) -> Any:
        payload = {"key": self.api_key, **params}
        response = self._session.post(self.api_url, data=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and "error" in data:
            raise BoosteroAPIError(data["error"])
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

    def cancel(self, order_ids: list[int | str]) -> list[dict[str, Any]]:
        return self._post(
            action="cancel", orders=",".join(str(i) for i in order_ids)
        )
