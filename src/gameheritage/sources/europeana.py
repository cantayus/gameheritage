"""Europeana Search API: historical photos, prints and paintings with rights statements.

Needs a free API key (https://pro.europeana.eu/page/get-api). Only items whose
rights statement allows reuse are kept.
"""

from __future__ import annotations

from .http import HttpClient

API = "https://api.europeana.eu/record/v2/search.json"
SOURCE_NAME = "Europeana"

OPEN_RIGHTS = (
    "creativecommons.org/publicdomain/mark/",
    "creativecommons.org/publicdomain/zero/",
    "creativecommons.org/licenses/by/",
    "creativecommons.org/licenses/by-sa/",
)


def is_open_rights(url: str) -> bool:
    return any(marker in (url or "") for marker in OPEN_RIGHTS)


def rights_label(url: str) -> str:
    u = (url or "").lower()
    if "publicdomain/mark" in u:
        return "Public domain"
    if "publicdomain/zero" in u:
        return "CC0 1.0"
    for code, label in (("by-sa", "CC BY-SA"), ("by", "CC BY")):
        marker = f"/licenses/{code}/"
        if marker in u:
            version = u.split(marker, 1)[1].strip("/").split("/")[0]
            return f"{label} {version}".strip()
    return url


def _first(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    if isinstance(value, dict):
        for v in value.values():
            return _first(v)
        return ""
    return str(value or "")


def search(client: HttpClient, query: str, api_key: str, *, rows: int = 20) -> list[dict]:
    """Openly reusable still images matching ``query``."""
    if not api_key:
        raise ValueError("Europeana needs an API key: set EUROPEANA_API_KEY")
    data = client.get_json(
        API,
        {
            "query": query,
            "wskey": api_key,
            "rows": str(rows),
            "reusability": "open",
            "media": "true",
            "qf": "TYPE:IMAGE",
            "profile": "standard",
        },
    )
    out = []
    for item in data.get("items", []):
        rights = _first(item.get("rights"))
        out.append(
            {
                "europeana_id": item.get("id", ""),
                "title": _first(item.get("title")),
                "creator": _first(item.get("dcCreator")),
                "year": _first(item.get("year")),
                "provider": _first(item.get("dataProvider")),
                "file_url": _first(item.get("edmIsShownBy")),
                "thumb_url": _first(item.get("edmPreview")),
                "page_url": item.get("guid", ""),
                "rights_url": rights,
                "license": rights_label(rights),
                "is_free": is_open_rights(rights),
            }
        )
    return out
