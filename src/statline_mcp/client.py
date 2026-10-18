"""Dunne asynchrone client voor de CBS OData API."""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import httpx

CATALOG_URL = "https://opendata.cbs.nl/ODataCatalog"
DEFAULT_API = "https://opendata.cbs.nl/ODataApi/odata"
USER_AGENT = "statline-mcp/0.1 (+https://opendata.cbs.nl)"


class CBSError(Exception):
    """Fout bij het aanroepen van de CBS API (bedoeld om aan de gebruiker te tonen)."""


class CBSClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            follow_redirects=True,
        )

    async def get_json(self, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        # Een nextLink bevat al een volledige query; die laten we ongemoeid.
        params = None if "?" in url else {"$format": "json", **(params or {})}
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = await self._http.get(url, params=params)
            except httpx.HTTPError as e:
                last = e
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r.json()
            if r.status_code in (429, 502, 503, 504):
                last = CBSError(f"HTTP {r.status_code}")
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            raise CBSError(_describe_error(r))
        raise CBSError(f"CBS API niet bereikbaar ({url}): {last}")

    async def get_all(
        self, url: str, params: dict[str, str] | None = None, limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Haal alle rijen op door `odata.nextLink` te volgen (optioneel tot `limit`)."""
        rows: list[dict[str, Any]] = []
        next_url: str | None = url
        next_params = params
        while next_url:
            data = await self.get_json(next_url, next_params)
            rows.extend(data.get("value", []))
            if limit is not None and len(rows) >= limit:
                return rows[:limit]
            next_url = data.get("odata.nextLink")
            next_params = None
        return rows

    async def aclose(self) -> None:
        await self._http.aclose()


def _describe_error(r: httpx.Response) -> str:
    try:
        msg = r.json()["odata.error"]["message"]["value"]
    except Exception:
        msg = r.text[:200].strip()
    if r.status_code == 404:
        return f"Niet gevonden (404): {msg or r.url}"
    return f"CBS API fout {r.status_code}: {msg}"
