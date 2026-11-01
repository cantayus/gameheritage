"""Fandom wikis: URL builders and MediaWiki file information.

Most game images were scraped from Fandom. Fandom's text is CC BY-SA, but game
screenshots there are copyrighted game assets hosted under fair-use claims, so
the project records where each came from and links back instead of
redistributing them.
"""

from __future__ import annotations

import re
from urllib.parse import quote

from .commons import parse_extmetadata
from .http import HttpClient, HttpError


def base_url(wiki: str) -> str:
    """``assassinscreed`` -> https://assassinscreed.fandom.com; ``yakuza/de`` -> .../de."""
    wiki = wiki.strip()
    if wiki.startswith("http"):
        return wiki.rstrip("/")
    name, _, lang = wiki.partition("/")
    return f"https://{name}.fandom.com" + (f"/{lang}" if lang else "")


_SAFE = ":()_,'-.!"


def page_url(wiki: str, title: str) -> str:
    return f"{base_url(wiki)}/wiki/{quote(title.replace(' ', '_'), safe=_SAFE)}"


def file_page_url(wiki: str, file_name: str) -> str:
    name = file_name.replace(" ", "_")
    return f"{base_url(wiki)}/wiki/File:{quote(name, safe=_SAFE)}"


def wiki_from_cdn_path(path: str) -> str:
    """CDN folder -> wiki: ``yakuza`` -> ``yakuza``; ``dawnofman_gamepedia_en`` ->
    ``dawnofman``; ``assassinscreed_de`` -> ``assassinscreed/de`` (German wiki)."""
    if not path:
        return ""
    m = re.match(r"^(?P<name>.+?)(?:_gamepedia)?(?:_(?P<lang>[a-z]{2}(?:-[a-z]+)?))?$", path)
    if not m:
        return path
    lang = m.group("lang")
    return m.group("name") + (f"/{lang}" if lang and lang != "en" else "")


def sanitize(title: str) -> str:
    """The scraper's file-name form of a title: every non-alphanumeric -> ``_``."""
    return re.sub(r"[^A-Za-z0-9]", "_", title)


def _matches(key: str, title: str) -> bool:
    # The scraper kept some characters (e.g. "-"), so normalise both sides.
    s, k = sanitize(title).lower(), sanitize(key).lower()
    return s == k or (len(k) >= 30 and s.startswith(k))


def _search(client: HttpClient, wiki: str, text: str, namespace: int, limit: int = 20) -> list[str]:
    try:
        data = _search_raw(client, wiki, text, namespace, limit)
    except HttpError:  # search disabled or failing on this wiki: no candidates
        return []
    return [r["title"] for r in data.get("query", {}).get("search", [])]


def _search_raw(client: HttpClient, wiki: str, text: str, namespace: int, limit: int) -> dict:
    return client.get_json(
        f"{base_url(wiki)}/api.php",
        {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "list": "search",
            "srsearch": text,
            "srnamespace": str(namespace),
            "srlimit": str(limit),
        },
    )


def _existing(client: HttpClient, wiki: str, titles: list[str]) -> list[str]:
    """Which of ``titles`` exist (after redirects), as their canonical titles."""
    found: list[str] = []
    for i in range(0, len(titles), 50):
        data = client.get_json(
            f"{base_url(wiki)}/api.php",
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "redirects": "1",
                "titles": "|".join(titles[i : i + 50]),
            },
        )
        found += [
            p["title"] for p in data.get("query", {}).get("pages", []) if not p.get("missing")
        ]
    return found


def resolve_titles(
    client: HttpClient, wiki: str, keys: dict[str, str], *, files: bool, search: bool = True
) -> dict[str, str]:
    """Map scraper keys to real wiki titles.

    ``keys`` maps each sanitised key (e.g. ``Mt__Rushmore__Civ4__jpg``) to a
    readable guess (``Mt Rushmore``). Exact guesses are tried first, then a
    title search; a candidate counts only if its sanitised form equals the key.
    Returns key -> title (without the ``File:`` prefix).
    """
    prefix = "File:" if files else ""
    out: dict[str, str] = {}
    guesses = []
    for key, guess in keys.items():
        if files:
            base, _, ext = key.rpartition("_")
            if "." in guess:
                guesses.append(f"File:{guess.replace('_', ' ').strip()}")
            # "Taj_Mahal_in-game__Civ6_" -> "Taj Mahal in-game (Civ6)";
            # "Mt__Rushmore" -> "Mt. Rushmore"
            paren = re.sub(r"__([A-Za-z0-9]+)_$", r" (\1)", base)
            for variant in {paren, paren.replace("__", ". ")}:
                guesses.append(f"File:{variant.replace('_', ' ').strip()}.{ext}")
        else:
            guesses.append(guess)
            guesses.append(
                re.sub(r"_([A-Za-z0-9 ]+)_$", r" (\1)", guess.replace(" ", "_")).replace("_", " ")
            )
    guesses = list(dict.fromkeys(guesses))
    for title in _existing(client, wiki, guesses):
        bare = title.removeprefix(prefix)
        for key in keys:
            if key not in out and _matches(key, bare):
                out[key] = bare
    if not search:
        return out
    for key, guess in keys.items():
        if key in out:
            continue
        words = re.sub(r"\b(?:jpe?g|png|gif|webp)\b", " ", guess.replace("_", " "))
        words = " ".join(w for w in words.split() if len(w) > 1)[:120]
        if not words:
            continue
        for title in _search(client, wiki, words, 6 if files else 0):
            bare = title.removeprefix("File:")
            if _matches(key, bare):
                out[key] = bare
                break
    return out


def page_images(client: HttpClient, wiki: str, titles: list[str]) -> dict[str, str]:
    """Main image file name of each page (what a ``__page_image`` scrape saved)."""
    out: dict[str, str] = {}
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        data = client.get_json(
            f"{base_url(wiki)}/api.php",
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "pageimages",
                "piprop": "name",
                "redirects": "1",
                "titles": "|".join(batch),
            },
        )
        q = data.get("query", {})
        back = {r["to"]: r["from"] for r in q.get("redirects", [])}
        back.update({n["to"]: n["from"] for n in q.get("normalized", [])})
        for page in q.get("pages", []):
            title = page.get("title", "")
            original = back.get(title, title)
            original = back.get(original, original)
            out[original] = page.get("pageimage", "")
    return out


def file_info(client: HttpClient, wiki: str, file_names: list[str]) -> dict[str, dict]:
    """Uploader, upload date, original URL and stated license for files on a Fandom wiki."""
    out: dict[str, dict] = {}
    for i in range(0, len(file_names), 50):
        batch = file_names[i : i + 50]
        data = client.get_json(
            f"{base_url(wiki)}/api.php",
            {
                "action": "query",
                "format": "json",
                "formatversion": "2",
                "prop": "imageinfo",
                "iiprop": "url|user|timestamp|size|mime|sha1|extmetadata",
                "titles": "|".join(f"File:{n}" for n in batch),
            },
        )
        for page in data.get("query", {}).get("pages", []):
            info = (page.get("imageinfo") or [{}])[0]
            meta = parse_extmetadata(info.get("extmetadata", {}))
            out[page["title"].removeprefix("File:")] = {
                "missing": page.get("missing", False),
                "file_url": info.get("url", ""),
                "page_url": info.get("descriptionurl", ""),
                "uploader": info.get("user", ""),
                "uploaded_at": info.get("timestamp", ""),
                "sha1": info.get("sha1", ""),
                **meta,
            }
    return out
