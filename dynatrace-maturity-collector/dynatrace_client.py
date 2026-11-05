"""Shared Dynatrace Environment API client for multi-tool inventory."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

VERSION = "1.0.0"


@dataclass
class ApiClient:
    base_url: str
    token: str
    timeout: int = 30
    retries: int = 3

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self.base_url.rstrip("/") + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        request = urllib.request.Request(
            url,
            headers={"Authorization": f"Api-Token {self.token}", "Accept": "application/json", "User-Agent": f"dt-maturity/{VERSION}"},
        )
        for attempt in range(self.retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:600]
                if exc.code == 429 and attempt + 1 < self.retries:
                    time.sleep(int(exc.headers.get("Retry-After", "2")))
                    continue
                raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
            except urllib.error.URLError as exc:
                if attempt + 1 < self.retries:
                    time.sleep(2**attempt)
                    continue
                raise RuntimeError(f"Connection error: {exc.reason}") from exc
        raise RuntimeError("Request failed")

    def paged(self, path: str, params: dict[str, Any], item_key: str) -> tuple[list[Any], int]:
        items: list[Any] = []
        pages = 0
        next_key: str | None = None
        while True:
            query = {"nextPageKey": next_key} if next_key else dict(params)
            payload = self.get(path, query)
            pages += 1
            items.extend(payload.get(item_key, []))
            next_key = payload.get("nextPageKey")
            if not next_key:
                return items, pages
