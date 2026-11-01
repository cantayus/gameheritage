"""Wikipedia page summaries for short site descriptions (CC BY-SA 4.0)."""

from __future__ import annotations

from urllib.parse import quote, unquote

from .http import HttpClient

LICENSE = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"


def summary(client: HttpClient, title: str, language: str = "en") -> dict:
    """Lead extract of a Wikipedia article, with the attribution the license requires."""
    title = unquote(title)  # accept already-encoded titles from URLs
    url = (
        f"https://{language}.wikipedia.org/api/rest_v1/page/summary/"
        f"{quote(title.replace(' ', '_'), safe='')}"
    )
    data = client.get_json(url)
    page = data.get("content_urls", {}).get("desktop", {}).get("page", "")
    return {
        "title": data.get("title", title),
        "extract": data.get("extract", ""),
        "page_url": page,
        "license": LICENSE,
        "license_url": LICENSE_URL,
    }


def intros(
    client: HttpClient, titles: list[str], *, sentences: int = 4, language: str = "en"
) -> dict[str, dict]:
    """Plain-text lead sections of many articles, 20 per request.

    Returns the requested title -> ``{"title", "extract", "page_url", "license",
    "license_url"}``; redirects are followed and missing pages left out.
    """
    out: dict[str, dict] = {}
    api = f"https://{language}.wikipedia.org/w/api.php"
    clean = [unquote(t).replace("_", " ") for t in titles]
    for i in range(0, len(clean), 20):
        chunk = clean[i : i + 20]
        data = client.get_json(
            api,
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "extracts|info",
                "inprop": "url",
                "exintro": "1",
                "explaintext": "1",
                "exsentences": str(sentences),
                "exlimit": "20",
                "redirects": "1",
                "titles": "|".join(chunk),
            },
        )
        query = data.get("query", {})
        back = {t: t for t in chunk}
        for step in ("normalized", "redirects"):
            for m in query.get(step, []):
                for orig, cur in list(back.items()):
                    if cur == m["from"]:
                        back[orig] = m["to"]
        pages = {p["title"]: p for p in query.get("pages", []) if not p.get("missing")}
        for orig, final in back.items():
            p = pages.get(final)
            if p and p.get("extract"):
                out[orig] = {
                    "title": p["title"],
                    "extract": p["extract"].strip(),
                    "page_url": p.get("fullurl", ""),
                    "license": LICENSE,
                    "license_url": LICENSE_URL,
                }
    return out
