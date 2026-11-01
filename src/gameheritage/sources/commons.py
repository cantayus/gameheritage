"""Wikimedia Commons: file metadata with licenses, category listings, downloads."""

from __future__ import annotations

import html
import re
from dataclasses import asdict, dataclass

from ..schema import is_free_license
from .http import HttpClient

API = "https://commons.wikimedia.org/w/api.php"
SOURCE_NAME = "Wikimedia Commons"

_TAG = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    """Plain text from Commons' HTML snippets (artist, credit, description)."""
    text = _TAG.sub(" ", text or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_extmetadata(meta: dict) -> dict:
    """Pick the licensing fields out of a MediaWiki ``extmetadata`` block."""

    def v(key: str) -> str:
        return str((meta.get(key) or {}).get("value", ""))

    short = strip_html(v("LicenseShortName"))
    attr = v("AttributionRequired").lower()
    return {
        "license": short,
        "license_url": v("LicenseUrl"),
        "license_code": v("License"),
        "author": strip_html(v("Artist")),
        "credit": strip_html(v("Credit")),
        "attribution_required": {"true": True, "false": False}.get(attr),
        "usage_terms": strip_html(v("UsageTerms")),
        "description": strip_html(v("ImageDescription"))[:500],
        "date_created": strip_html(v("DateTimeOriginal"))[:40],
        "object_name": strip_html(v("ObjectName")),
        "is_free": is_free_license(short),
    }


@dataclass
class CommonsFile:
    title: str
    page_url: str
    file_url: str
    thumb_url: str
    width: int
    height: int
    mime: str
    sha1: str
    size: int
    license: str
    license_url: str
    license_code: str
    author: str
    credit: str
    attribution_required: bool | None
    usage_terms: str
    description: str
    date_created: str
    object_name: str
    is_free: bool

    def as_dict(self) -> dict:
        return asdict(self)


def canonical_thumb_url(url: str) -> str:
    """Serve thumbnails from upload.wikimedia.org and drop tracking parameters.

    The API may return ``thumb.wikimedia.org`` links; the same path works on
    ``upload.wikimedia.org``, which is the long-standing public host.
    """
    url = url.split("?", 1)[0]
    return url.replace("https://thumb.wikimedia.org/", "https://upload.wikimedia.org/", 1)


def direct_urls(file_name: str, width: int = 1280) -> tuple[str, str, str]:
    """(page URL, thumbnail URL, original URL) computed from a Commons file name.

    Wikimedia stores files under the MD5 of their name, so these URLs need no API
    call. The thumbnail does not exist when the original is narrower than ``width``;
    use the original then.
    """
    import hashlib
    from urllib.parse import quote

    name = file_name.removeprefix("File:").replace(" ", "_")
    h = hashlib.md5(name.encode("utf-8")).hexdigest()
    q = quote(name)
    base = "https://upload.wikimedia.org/wikipedia/commons"
    page = f"https://commons.wikimedia.org/wiki/File:{q}"
    return page, f"{base}/thumb/{h[0]}/{h[:2]}/{q}/{width}px-{q}", f"{base}/{h[0]}/{h[:2]}/{q}"


def normalize_title(title: str) -> str:
    t = title.strip().replace("_", " ")
    if t.lower().startswith("file:"):
        t = t[5:]
    return f"File:{t}"


def file_info(client: HttpClient, titles: list[str], thumb_width: int = 1280) -> list[CommonsFile]:
    """Metadata for Commons files (``File:...`` titles), 50 per request."""
    out: list[CommonsFile] = []
    norm = [normalize_title(t) for t in titles]
    for i in range(0, len(norm), 50):
        data = client.get_json(
            API,
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "imageinfo",
                "iiprop": "url|size|mime|sha1|extmetadata",
                "iiurlwidth": str(thumb_width),
                "iiextmetadatafilter": (
                    "LicenseShortName|LicenseUrl|License|Artist|Credit|AttributionRequired|"
                    "UsageTerms|ImageDescription|DateTimeOriginal|ObjectName"
                ),
                "titles": "|".join(norm[i : i + 50]),
                "maxlag": "5",
            },
        )
        for page in data.get("query", {}).get("pages", []):
            if page.get("missing") or not page.get("imageinfo"):
                continue
            info = page["imageinfo"][0]
            meta = parse_extmetadata(info.get("extmetadata", {}))
            out.append(
                CommonsFile(
                    title=page["title"],
                    page_url=info.get("descriptionurl", ""),
                    file_url=info.get("url", ""),
                    thumb_url=canonical_thumb_url(info.get("thumburl", "")),
                    width=int(info.get("width", 0)),
                    height=int(info.get("height", 0)),
                    mime=info.get("mime", ""),
                    sha1=info.get("sha1", ""),
                    size=int(info.get("size", 0)),
                    **meta,
                )
            )
    return out


def category_files(client: HttpClient, category: str, limit: int = 50) -> list[str]:
    """File titles directly in a Commons category (no subcategories)."""
    cat = category if category.startswith("Category:") else f"Category:{category}"
    titles: list[str] = []
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "list": "categorymembers",
        "cmtitle": cat,
        "cmtype": "file",
        "cmlimit": str(min(limit, 500)),
        "maxlag": "5",
    }
    while len(titles) < limit:
        data = client.get_json(API, params)
        titles += [m["title"] for m in data.get("query", {}).get("categorymembers", [])]
        cont = data.get("continue", {}).get("cmcontinue")
        if not cont:
            break
        params = {**params, "cmcontinue": cont}
    return titles[:limit]


def search_files(client: HttpClient, query: str, limit: int = 20) -> list[str]:
    """Full-text search of Commons file pages; returns ``File:`` titles."""
    data = client.get_json(
        API,
        {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "list": "search",
            "srsearch": query,
            "srnamespace": "6",
            "srlimit": str(limit),
            "maxlag": "5",
        },
    )
    return [r["title"] for r in data.get("query", {}).get("search", [])]
