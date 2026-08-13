"""Thin wrapper around Prowlarr's aggregate JSON search API.

Prowlarr already aggregates Torznab/Newznab indexers — we deliberately don't
reimplement indexer scraping, we just call its `/api/v1/search` endpoint, which
searches across all (or selected) configured indexers at once.
"""

from dataclasses import dataclass

import requests


@dataclass
class Release:
    title: str
    guid: str
    download_url: str
    indexer_id: int | None
    indexer_name: str | None
    seeders: int | None
    size: int | None
    protocol: str | None

    @classmethod
    def from_json(cls, item: dict) -> "Release":
        return cls(
            title=item.get("title", ""),
            guid=item.get("guid", ""),
            download_url=item.get("downloadUrl") or item.get("magnetUrl") or item.get("link", ""),
            indexer_id=item.get("indexerId"),
            indexer_name=item.get("indexer"),
            seeders=item.get("seeders"),
            size=item.get("size"),
            protocol=item.get("protocol"),
        )


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"X-Api-Key": self.api_key}

    def search(
        self,
        query: str,
        categories: list[int] | None = None,
        indexer_ids: list[int] | None = None,
    ) -> list[Release]:
        params: dict = {"query": query, "type": "search"}
        if categories:
            params["categories"] = ",".join(str(c) for c in categories)
        if indexer_ids:
            params["indexerIds"] = ",".join(str(i) for i in indexer_ids)

        response = requests.get(
            f"{self.base_url}/api/v1/search",
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        return [Release.from_json(item) for item in response.json()]
