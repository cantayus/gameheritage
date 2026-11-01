"""Expand the collection: harvest location images from a game's Fandom wiki.

Given a wiki and a category of location pages (e.g. ``Locations in Assassin's
Creed Unity``), ``harvest`` lists the pages, picks the images shown on each,
downloads them and registers them as game images. Each image carries:

* ``in_game_place``: the wiki page it was shown on (used for linking to a real site);
* ``source_page_url`` / ``file_url``: its Fandom file page and original file;
* ``notes``: uploader and upload date; ``collection_method``: the category used.

Downloaded files go to ``<dest>/<game_id>/``. Game images are copyrighted game
assets: they are kept for research and never bundled in the package.
"""

from __future__ import annotations

import contextlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .imagefile import EXT_FOR_FORMAT, inspect_file
from .ingest import image_id_for
from .names import slugify
from .parse import guess_kind, parse_filename
from .people import shows_person
from .sources import fandom
from .sources.http import HttpClient
from .tables import Tables, upsert

_SKIP_FILE = re.compile(
    r"icon|logo|symbol|emblem|badge|button|banner|flag|coat[ _]of[ _]arms|\bcoa\b|portrait|"
    r"avatar|signature|placeholder|achievement|trophy|map[ _]?marker|\.svg$|\.gif$|"
    r"minimap|\bmap\b|\bmenu\b",
    re.I,
)


def strip_qualifier(title: str) -> str:
    """Drop a trailing game tag: ``"Taj Mahal (Civ6)"`` -> ``"Taj Mahal"``."""
    return re.sub(r"\s*\([^)]*\)\s*$", "", title).strip() or title


def category_pages(
    client: HttpClient, wiki: str, category: str, *, depth: int = 1, limit: int = 2000
) -> list[str]:
    """Article titles in a category and (to ``depth``) its subcategories."""
    seen_cats: set[str] = set()
    pages: list[str] = []
    todo = [(category if category.startswith("Category:") else f"Category:{category}", 0)]
    while todo and len(pages) < limit:
        cat, d = todo.pop(0)
        if cat in seen_cats:
            continue
        seen_cats.add(cat)
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "list": "categorymembers",
            "cmtitle": cat,
            "cmtype": "page|subcat",
            "cmlimit": "500",
        }
        while True:
            data = client.get_json(f"{fandom.base_url(wiki)}/api.php", params)
            for m in data.get("query", {}).get("categorymembers", []):
                if m.get("ns") == 14 and d < depth:
                    todo.append((m["title"], d + 1))
                elif m.get("ns") == 0:
                    pages.append(m["title"])
            cont = data.get("continue", {}).get("cmcontinue")
            if not cont:
                break
            params = {**params, "cmcontinue": cont}
    return list(dict.fromkeys(pages))[:limit]


def page_files(client: HttpClient, wiki: str, titles: list[str]) -> dict[str, list[str]]:
    """Image files used on each page (``File:`` prefix removed), icons filtered out."""
    out: dict[str, list[str]] = {t: [] for t in titles}
    for i in range(0, len(titles), 50):
        params = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "images",
            "imlimit": "500",
            "titles": "|".join(titles[i : i + 50]),
        }
        while True:
            data = client.get_json(f"{fandom.base_url(wiki)}/api.php", params)
            for page in data.get("query", {}).get("pages", []):
                for im in page.get("images", []):
                    name = im["title"].removeprefix("File:")
                    if is_wanted_file(name):
                        out.setdefault(page["title"], []).append(name)
            cont = data.get("continue", {}).get("imcontinue")
            if not cont:
                break
            params = {**params, "imcontinue": cont}
    return out


def prefix_files(client: HttpClient, wiki: str, prefix: str, limit: int = 5000) -> dict[str, dict]:
    """All files on a wiki whose names start with ``prefix`` (e.g. ``ACUDB``), with
    their URLs and uploader, from ``list=allimages``."""
    out: dict[str, dict] = {}
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "list": "allimages",
        "aiprefix": prefix.replace(" ", "_"),
        "aiprop": "url|user|timestamp|mime|size",
        "ailimit": "500",
    }
    while len(out) < limit:
        data = client.get_json(f"{fandom.base_url(wiki)}/api.php", params)
        for im in data.get("query", {}).get("allimages", []):
            name = im["name"].replace("_", " ")
            out[name] = {
                "missing": False,
                "file_url": im.get("url", ""),
                "page_url": im.get("descriptionurl", ""),
                "uploader": im.get("user", ""),
                "uploaded_at": im.get("timestamp", ""),
                "mime": im.get("mime", ""),
            }
        cont = data.get("continue", {}).get("aicontinue")
        if not cont:
            break
        params = {**params, "aicontinue": cont}
    return out


def harvest(
    client: HttpClient,
    tables: Tables,
    *,
    wiki: str,
    game_id: str,
    category: str = "",
    dest: str | Path,
    prefix: str = "",
    project_root: str | Path | None = None,
    depth: int = 1,
    per_page: int = 4,
    min_side: int = 480,
    max_pages: int = 2000,
    file_pattern: str | None = None,
    exclude_pattern: str | None = None,
    progress: bool = False,
    checkpoint=None,
    require_structure: bool = True,
) -> tuple[Tables, list[str]]:
    """Download up to ``per_page`` images per location page and register them.

    With ``require_structure`` (the default) only files whose place matches a real
    built structure on Wikidata are downloaded (see ``autolink.real_structures``).

    ``file_pattern`` (a regular expression) keeps only matching file names, e.g.
    ``^ACU`` for Assassin's Creed Unity images on a shared "Locations in Paris" page.
    ``exclude_pattern`` drops matching names, e.g. other games' tags (``\\bMW3\\b``).
    """
    if game_id not in set(tables.games["game_id"]):
        raise ValueError(f"unknown game_id {game_id!r}: add it to games.csv first")
    wanted: dict[str, str] = {}
    also: dict[str, list[str]] = {}
    if prefix:
        # File-name mode: the place comes from the file name ("ACUDB - Halle aux Bles").
        info = prefix_files(client, wiki, prefix)
        for name in info:
            if not is_wanted_file(name):
                continue
            rest = name[len(prefix) :] if name.lower().startswith(prefix.lower()) else name
            parsed = parse_filename(f"x/game data/{rest.strip(' -_')}")
            wanted[name] = parsed.place_hint or parsed.label
        pages = []
        per_page = 10**6
    else:
        pages = category_pages(client, wiki, category, depth=depth, limit=max_pages)
        files = page_files(client, wiki, pages)
        if file_pattern:
            rx = re.compile(file_pattern)
            files = {p: [f for f in fs if rx.search(f)] for p, fs in files.items()}
        if exclude_pattern:
            nx = re.compile(exclude_pattern)
            files = {p: [f for f in fs if not nx.search(f)] for p, fs in files.items()}
        for page, fs in files.items():
            place = strip_qualifier(page)
            for f in fs[: per_page * 3]:
                if f in wanted:
                    also.setdefault(f, []).append(page)
                else:
                    wanted[f] = place
        info = fandom.file_info(client, wiki, list(wanted)) if wanted else {}
    msgs = [f"{len(pages)} pages, {len(wanted)} candidate files"]
    real: dict[str, dict] = {}
    if require_structure and wanted:
        # Only images of places that match a real built structure are downloaded.
        from .autolink import real_structures

        real = real_structures(client, list(set(wanted.values())))
        before = len(wanted)
        wanted = {f: p for f, p in wanted.items() if p in real}
        msgs.append(
            f"{len(wanted)} of {before} files show one of {len(set(wanted.values()))} "
            "places that match a real structure"
        )
    known = set(tables.images["file_url"]) | set(tables.images["source_page_url"])
    per_count: dict[str, int] = {}
    people = 0
    rows = []
    flushed = 0
    where = (
        f"files named '{prefix}*'"
        if prefix
        else f"the category '{category}' (depth {depth}"
        + (f", files matching {file_pattern!r}" if file_pattern else "")
        + ")"
    )
    method = (
        f"Harvested from {wiki}.fandom.com, {where}, "
        f"with the MediaWiki API by gameheritage {__version__}"
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for name, page in wanted.items():
        meta = info.get(name) or info.get(name.replace("_", " "))
        if not meta or meta.get("missing") or not meta.get("file_url"):
            continue
        if meta["file_url"] in known or meta["page_url"] in known:
            continue
        if per_count.get(page, 0) >= per_page:
            continue
        stem = slugify(Path(name).stem)[:80] or "image"
        folder = Path(dest) / game_id / slugify(page)[:60]
        try:
            path, sha, n = client.download(meta["file_url"], folder / f"{stem}.download")
        except Exception as exc:  # report and continue
            msgs.append(f"failed {name}: {exc}")
            continue
        facts = inspect_file(path)
        if not facts.readable or max(facts.width or 0, facts.height or 0) < min_side:
            path.unlink(missing_ok=True)
            continue
        if shows_person(path, f"{(real.get(page) or {}).get('instance_of', '')} {page}"):
            path.unlink(missing_ok=True)  # a character render or portrait, not the place
            people += 1
            continue
        final = path.with_name(
            f"{stem[:60]}-{sha[:8]}" + EXT_FOR_FORMAT.get(facts.true_format, ".jpg")
        )
        path.replace(final)
        local = final.resolve()
        if project_root:
            with contextlib.suppress(ValueError):
                local = Path(os.path.relpath(local, Path(project_root).resolve()))
        per_count[page] = per_count.get(page, 0) + 1
        rows.append(
            {
                "image_id": image_id_for(sha),
                "role": "game",
                "game_id": game_id,
                "kind": guess_kind(name),
                "title": Path(name).stem.replace("_", " "),
                "in_game_place": page,
                "original_filename": name,
                "local_path": local.as_posix(),
                "sha256": sha,
                "phash": facts.phash,
                "width": facts.width,
                "height": facts.height,
                "true_format": facts.true_format,
                "bytes": n,
                "source_name": f"Fandom ({wiki})",
                "source_page_url": meta["page_url"],
                "file_url": meta["file_url"],
                "rights_status": "display_only",
                "collected_by": f"gameheritage {__version__}",
                "collection_method": method,
                "retrieved_at": now,
                "review_status": "todo",
                "notes": f"Fandom upload by {meta['uploader']} on {meta['uploaded_at'][:10]}"
                + ("" if prefix else f"; shown on page {fandom.page_url(wiki, page)}")
                + (f"; also on {', '.join(also[name])}" if also.get(name) else "")
                + (f"; wiki file page states: {meta['license']}" if meta.get("license") else ""),
            }
        )
        if progress and len(rows) % 50 == 0:
            print(f"  {len(rows)} images", flush=True)
        if checkpoint and len(rows) - flushed >= 100:
            checkpoint(rows[flushed:])
            flushed = len(rows)
    if checkpoint and len(rows) > flushed:
        checkpoint(rows[flushed:])
    tables.images = upsert(tables.images, rows, "image_id")
    msgs[0] += f"; registered {len(rows)} new images from {len(per_count)} pages"
    if people:
        msgs.append(f"skipped {people} images showing a person rather than the place")
    return tables, msgs


def prune_unlinked(
    tables: Tables, project_root: str | Path, log_path: str | Path | None = None
) -> tuple[Tables, list[str]]:
    """Remove harvested game images that were not matched to a real site.

    Run after ``autolink``: a harvested image whose place got no confident
    Wikidata match (``site_id`` empty) has no confirmed real-world counterpart.
    Its row is removed and its file deleted. Each removed image is listed in
    ``log_path`` (CSV) with its source URLs, so it can be harvested again if a
    reviewer later confirms the place. Mija's collection is never modified.
    """
    mine = _harvested(tables)
    bad = mine[mine["site_id"] == ""]
    removed = _remove(tables, bad, project_root, log_path, "no confident real-world match")
    return tables, [
        f"kept {len(mine) - len(bad)} harvested images matched to a real site; "
        f"removed {len(bad)} unmatched rows and {removed} files"
        + (f" (listed in {log_path})" if log_path and len(bad) else "")
    ]


# Fandom video files have no extension; their file URL is a still from the video.
# Stills of in-game footage (e.g. Civilization wonder movies) are kept; trailers,
# gameplay recordings and reviews (faces, overlays, commentary) are not.
_VIDEO_JUNK = re.compile(
    r"trailer|gameplay|walkthrough|playthrough|let'?s[ _]play|review|showcase|"
    r"\btour\b|reveal|introduction|\bno[ _]hud\b|interview|developer|livestream|"
    r"commentary|multiplayer|deathmatch|domination|\btdm\b|time[ _]lapse|\bofficial\b|"
    r"\bps[2345]\b|xbox|episode|\bpart[ _]\d",
    re.I,
)
_HAS_EXT = re.compile(r"\.[A-Za-z0-9]{2,4}$")


def is_wanted_file(name: str) -> bool:
    """False for icons, maps, menus, and video stills of trailers or gameplay."""
    if _SKIP_FILE.search(name):
        return False
    return bool(_HAS_EXT.search(name)) or not _VIDEO_JUNK.search(name)


def prune_files(
    tables: Tables,
    project_root: str | Path,
    *,
    pattern: str | None = None,
    game_prefix: str = "",
    log_path: str | Path | None = None,
) -> tuple[Tables, list[str]]:
    """Remove harvested images whose file name shows they are not pictures of the
    place: icons, maps, menus, videos, and (with ``pattern``) file names matching a
    regular expression, e.g. another game's tag on a shared page. ``game_prefix``
    limits the ``pattern`` rule to games whose id starts with it."""
    mine = _harvested(tables)
    bad = ~mine["original_filename"].map(is_wanted_file)
    if pattern:
        rx = re.compile(pattern)
        in_scope = mine["game_id"].str.startswith(game_prefix)
        bad |= in_scope & mine["original_filename"].map(lambda n: bool(rx.search(n)))
    removed = _remove(tables, mine[bad], project_root, log_path, "not a picture of the place")
    return tables, [f"removed {int(bad.sum())} rows and {removed} files"]


def find_people(tables: Tables, project_root: str | Path) -> list[str]:
    """IDs of harvested images that show a person rather than the place
    (see ``people.shows_person``). Sculpture-like places are exempt."""
    mine = _harvested(tables)
    types = dict(zip(tables.sites["site_id"], tables.sites["notes"]))
    out = []
    for r in mine.to_dict("records"):
        f = Path(project_root) / r["local_path"] if r["local_path"] else None
        if f and f.exists():
            kind = f"{types.get(r['site_id'], '')} {r['in_game_place']}"
            if shows_person(f, kind):
                out.append(r["image_id"])
    return out


def prune_people(
    tables: Tables,
    project_root: str | Path,
    log_path: str | Path | None = None,
    ids: list[str] | None = None,
) -> tuple[Tables, list[str]]:
    """Remove harvested images that show a person rather than the place.

    ``ids`` (from ``find_people``) skips the detection, so the slow part can run
    before a quick table update."""
    from .people import available

    if ids is None:
        if not available():
            return tables, ["face detectors not available: install opencv-python-headless"]
        ids = find_people(tables, project_root)
    mine = _harvested(tables)
    rows = mine[mine["image_id"].isin(ids)]
    removed = _remove(tables, rows, project_root, log_path, "shows a person, not the place")
    return tables, [f"removed {len(rows)} images showing a person ({removed} files)"]


def _harvested(tables: Tables):
    imgs = tables.images
    return imgs[(imgs["role"] == "game") & imgs["collected_by"].str.startswith("gameheritage")]


def _remove(tables: Tables, bad, project_root, log_path, reason: str) -> int:
    """Delete the rows in ``bad`` and their files; list them in ``log_path``."""
    if log_path and len(bad):
        import pandas as pd

        cols = ["image_id", "game_id", "in_game_place", "source_page_url", "file_url"]
        log = Path(log_path)
        log.parent.mkdir(parents=True, exist_ok=True)
        new = bad[cols].assign(reason=reason)
        old = pd.read_csv(log, dtype=str, keep_default_na=False) if log.exists() else None
        merged = pd.concat([old, new]) if old is not None else new
        merged.drop_duplicates("image_id").to_csv(log, index=False)  # safe to re-run
    removed = 0  # files gone, including any an interrupted earlier attempt deleted
    folders: set[Path] = set()
    for r in bad.to_dict("records"):
        if r["local_path"]:
            f = Path(project_root) / r["local_path"]
            if f.exists():
                f.unlink()
                folders.add(f.parent)
            removed += 1
    for d in folders:
        with contextlib.suppress(OSError):
            d.rmdir()  # only succeeds when the folder is now empty
    imgs = tables.images
    tables.images = imgs[~imgs["image_id"].isin(bad["image_id"])].reset_index(drop=True)
    return removed


def prune_non_structures(
    client: HttpClient, tables: Tables, project_root: str | Path
) -> tuple[Tables, list[str]]:
    """Remove harvested game images whose place is not a real structure.

    Only images this package downloaded (``collected_by`` starts with
    "gameheritage") are touched: their rows are removed and their files deleted.
    Mija's collection is never modified.
    """
    from .autolink import real_structures

    imgs = tables.images
    mine = imgs[(imgs["role"] == "game") & imgs["collected_by"].str.startswith("gameheritage")]
    real = real_structures(client, list(set(mine["in_game_place"])))
    bad = mine[~mine["in_game_place"].isin(real)]
    removed = 0
    for r in bad.to_dict("records"):
        if r["local_path"]:
            f = Path(project_root) / r["local_path"]
            if f.exists():
                f.unlink()
                removed += 1
    tables.images = imgs[~imgs["image_id"].isin(bad["image_id"])].reset_index(drop=True)
    return tables, [
        f"kept {len(mine) - len(bad)} harvested images of real structures; "
        f"removed {len(bad)} rows and {removed} files"
    ]
