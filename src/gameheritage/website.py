"""Build the public website: a static site of real places and their in-game versions.

``build_website`` writes a folder that any static host (GitHub Pages) can serve:

* ``index.html``, ``assets/`` - the page and its script and styles (from ``web/``);
* ``data/index.json`` - games, places and depictions for search and browsing;
* ``data/places/<site_id>.json`` - one file per place: facts, descriptions,
  real photos and in-game images, each with its credit line;
* ``img/full/<image_id>.webp`` and ``img/thumb/<image_id>.webp`` - web copies.

What is shown: every non-dropped depiction linked to a site, with its game images
and the site's real photos. Matches nobody has reviewed yet are marked as such.
"""

from __future__ import annotations

import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import pandas as pd
from PIL import Image

from . import __version__, history
from .autolink import place_kind
from .sources.http import HttpClient
from .tables import Tables

FULL_SIDE = 1200
THUMB_SIDE = 400
TEMPLATE = Path(__file__).parent / "web"


def _shown(tables: Tables) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Depictions, game images and real images that the site shows."""
    deps = tables.depictions[
        (tables.depictions["review_status"] != "drop") & (tables.depictions["site_id"] != "")
    ]
    imgs = tables.images[tables.images["review_status"] != "drop"]
    game = imgs[(imgs["role"] == "game") & imgs["depiction_id"].isin(deps["depiction_id"])]
    deps = deps[deps["depiction_id"].isin(game["depiction_id"])]
    from_team = imgs["collected_by"].str.startswith("Mija")
    real = imgs[
        (imgs["role"] == "real")
        & imgs["site_id"].isin(deps["site_id"])
        & (((imgs["local_path"] != "") & (imgs["source_page_url"] != "")) | from_team)
    ]
    return deps, game, real


def _source_file(
    row: dict, project_root: Path, cache: Path, client: HttpClient | None
) -> Path | None:
    """Local file of an image: the downloaded copy, or the original fetched from
    its file URL (Mija's collection lives on Drive; its Fandom originals are public)."""
    if row["local_path"]:
        p = project_root / row["local_path"]
        if p.exists():
            return p
    cached = cache / row["image_id"]  # also where copies from the team's Drive are put
    if cached.exists():
        return cached
    if not row["file_url"] or client is None:
        return None
    try:
        path, _sha, _n = client.download(row["file_url"], cached.with_suffix(".download"))
    except Exception:
        return None
    path.replace(cached)
    return cached


def _webp(src: Path, dest: Path, side: int, quality: int) -> tuple[int, int] | None:
    if dest.exists():
        with Image.open(dest) as im:
            return im.size
    try:
        with Image.open(src) as im:
            im.seek(0)
            im = im.convert("RGB")
            im.thumbnail((side, side), Image.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, "WEBP", quality=quality, method=5)
            return im.size
    except Exception:
        return None


def make_images(
    tables: Tables,
    out_dir: str | Path,
    project_root: str | Path,
    cache_dir: str | Path,
    *,
    client_factory=None,
    workers: int = 4,
    progress=None,
) -> dict[str, tuple[int, int]]:
    """Write web copies of every image the site shows; returns image_id -> (w, h)."""
    _deps, game, real = _shown(tables)
    rows = pd.concat([game, real]).drop_duplicates("image_id").to_dict("records")
    out, root = Path(out_dir), Path(project_root)
    cache = Path(cache_dir) / "web_sources"
    cache.mkdir(parents=True, exist_ok=True)
    sizes: dict[str, tuple[int, int]] = {}
    clients = [client_factory() if client_factory else None for _ in range(workers)]

    def work(k_row):
        k, row = k_row
        src = _source_file(row, root, cache, clients[k % workers])
        if src is None:
            return row["image_id"], None
        full = _webp(src, out / "img" / "full" / f"{row['image_id']}.webp", FULL_SIDE, 74)
        _webp(src, out / "img" / "thumb" / f"{row['image_id']}.webp", THUMB_SIDE, 66)
        return row["image_id"], full

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for image_id, size in pool.map(work, enumerate(rows)):
            done += 1
            if size:
                sizes[image_id] = size
            if progress and done % 100 == 0:
                progress(done, len(rows), len(sizes))
    if progress:
        progress(done, len(rows), len(sizes))
    return sizes


def _clean(text: str) -> str:
    """Tidy extracted text: drop emptied brackets such as "Karnak ()"."""
    t = re.sub(r"\(\s*[;,:]?\s*\)", "", text or "")
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _locality(text: str) -> str:
    parts = [p.strip() for p in (text or "").split(";") if p.strip()]
    return ", ".join(dict.fromkeys(parts[:2]))


def original_name(row: dict) -> str:
    """File name of the full-resolution original on the image host: <image_id><ext>."""
    src = row["local_path"] or row["drive_path"] or row["original_filename"]
    ext = Path(src).suffix.lower() if src else ""
    return row["image_id"] + (ext if ext and len(ext) <= 5 else ".jpg")


def _year(value: str) -> str:
    return value[:4] if value and value[:4].lstrip("-").isdigit() else value


def _game_credit(row: dict, game: dict) -> dict:
    owner = game.get("publisher") or game.get("developer") or "the game's publisher"
    return {
        "text": f"Screenshot from {game.get('title', '')}, © {owner}.",
        "source": row["source_page_url"],
        "via": row["source_name"] or "Fandom",
        "note": (row["notes"] or "").split(";")[0],
    }


def _real_credit(row: dict) -> dict:
    if not row["source_page_url"] and (row["collected_by"] or "").startswith("Mija"):
        return {
            "text": "Collected by Mija Aleksandraviciute for the project",
            "license": "original source not yet recorded",
            "license_url": "",
            "source": "",
            "via": "Project collection",
        }
    return {
        "text": row["author"] or "Unknown author",
        "license": row["license"] or "see source",
        "license_url": row["license_url"],
        "source": row["source_page_url"],
        "via": row["source_name"] or "Wikimedia Commons",
    }


def export_data(
    tables: Tables, out_dir: str | Path, sizes: dict[str, tuple[int, int]]
) -> dict[str, int]:
    """Write ``data/index.json`` and ``data/places/<site_id>.json``."""
    out = Path(out_dir) / "data"
    (out / "places").mkdir(parents=True, exist_ok=True)
    deps, game_imgs, real_imgs = _shown(tables)
    game_imgs = game_imgs[game_imgs["image_id"].isin(sizes)]
    real_imgs = real_imgs[real_imgs["image_id"].isin(sizes)]
    deps = deps[deps["depiction_id"].isin(game_imgs["depiction_id"])]
    games = {g["game_id"]: g for g in tables.games.to_dict("records")}
    sites = {s["site_id"]: s for s in tables.sites.to_dict("records")}
    kept_pairs = set(tables.pairs.loc[tables.pairs["review_status"] == "keep", "depiction_id"])

    # Real photos: free licenses first, then the largest.
    real_imgs = real_imgs.assign(
        _free=(real_imgs["rights_status"] == "open").astype(int),
        _w=pd.to_numeric(real_imgs["width"], errors="coerce").fillna(0),
    ).sort_values(["_free", "_w"], ascending=False)
    real_by_site = {k: v for k, v in real_imgs.groupby("site_id")}
    game_by_dep = {k: v for k, v in game_imgs.groupby("depiction_id")}

    index_deps, place_rows = [], []
    for site_id, sdeps in deps.groupby("site_id"):
        s = sites.get(site_id)
        if not s:
            continue
        reals = real_by_site.get(site_id)
        real_list = [] if reals is None else reals.to_dict("records")
        dep_list = []
        for d in sdeps.to_dict("records"):
            g = games.get(d["game_id"], {})
            imgs = game_by_dep[d["depiction_id"]].to_dict("records")
            verified = d["review_status"] == "keep" or d["depiction_id"] in kept_pairs
            dep_list.append(
                {
                    "id": d["depiction_id"],
                    "game": d["game_id"],
                    "name": d["in_game_name"],
                    "verified": verified,
                    "text": _clean(d["in_game_description"]),
                    "text_source": d["in_game_description_source"],
                    "text_license": d["in_game_description_license"],
                    "images": [
                        {
                            "id": r["image_id"],
                            "w": sizes[r["image_id"]][0],
                            "h": sizes[r["image_id"]][1],
                            "o": original_name(r),
                            "credit": _game_credit(r, g),
                        }
                        for r in imgs
                    ],
                }
            )
            index_deps.append(
                [site_id, d["game_id"], d["in_game_name"], imgs[0]["image_id"], int(verified)]
            )
        dep_list.sort(key=lambda x: games.get(x["game"], {}).get("release_year") or "9999")
        year = history.parse_year(s["construction_date"])
        kind = place_kind((s["notes"] or "").removeprefix("type: "))
        era = history.era_of(year, natural=kind == "natural" and year is None)
        place = {
            "id": site_id,
            "name": s["name"],
            "qid": s["wikidata_qid"],
            "country": s["country"],
            "locality": _locality(s["locality"]),
            "lat": s["latitude"],
            "lon": s["longitude"],
            "built": _year(s["construction_date"]),
            "year": year,
            "year_text": history.year_text(year),
            "century": history.century_of(year),
            "era": era,
            "period": s["historical_period"],
            "date_basis": s["construction_date_basis"]
            or ("Wikidata, inception" if year is not None else ""),
            "style": s["architectural_style"],
            "heritage": s["heritage_designation"],
            "type": (s["notes"] or "").removeprefix("type: "),
            "wikipedia": s["wikipedia_url"],
            "text": _clean(s["description"]),
            "text_source": s["description_source"],
            "text_license": s["description_license"],
            "real": [
                {
                    "id": r["image_id"],
                    "w": sizes[r["image_id"]][0],
                    "h": sizes[r["image_id"]][1],
                    "title": r["title"],
                    "o": original_name(r),
                    "credit": _real_credit(r),
                }
                for r in real_list[:6]
            ],
            "depictions": dep_list,
        }
        (out / "places" / f"{site_id}.json").write_text(
            json.dumps(place, ensure_ascii=False, separators=(",", ":")), "utf-8"
        )
        place_rows.append(
            {
                "id": site_id,
                "name": s["name"],
                "country": s["country"],
                "locality": _locality(s["locality"]),
                "built": _year(s["construction_date"]),
                "year": year,
                "era": era,
                "century": place["century"],
                "type": place["type"].split(";")[0],
                "real": real_list[0]["image_id"] if real_list else "",
            }
        )

    used_games = {d[1] for d in index_deps}
    game_rows = [
        {
            "id": g["game_id"],
            "title": g["title"],
            "series": g["series"],
            "year": _year(g["release_year"]),
            "developer": g["developer"],
            "publisher": g["publisher"],
            "wikipedia": g["wikipedia_url"],
        }
        for g in tables.games.to_dict("records")
        if g["game_id"] in used_games
    ]
    index = {
        "eras": [e[0] for e in history.ERAS] + [history.NATURAL, history.UNDATED],
        "built": date.today().isoformat(),
        "version": __version__,
        "games": sorted(game_rows, key=lambda g: g["title"].lower()),
        "places": sorted(place_rows, key=lambda p: p["name"].lower()),
        "depictions": index_deps,  # [site_id, game_id, in-game name, image_id, verified]
    }
    (out / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")), "utf-8"
    )
    # Places no longer shown (a match withdrawn since the last build).
    live = {p["id"] for p in place_rows}
    for f in (out / "places").glob("*.json"):
        if f.stem not in live:
            f.unlink()
    shown = {i for p in place_rows for i in [p["real"]] if i}
    shown |= {
        im["id"]
        for f in (out / "places").glob("*.json")
        for d in [json.loads(f.read_text("utf-8"))]
        for im in d["real"] + [x for dep in d["depictions"] for x in dep["images"]]
    }
    removed = 0
    for sub in ("full", "thumb"):
        for f in (Path(out_dir) / "img" / sub).glob("*.webp"):
            if f.stem not in shown:
                f.unlink()
                removed += sub == "full"
    return {
        "places": len(place_rows),
        "games": len(game_rows),
        "depictions": len(index_deps),
        "game_images": len(game_imgs),
        "real_images": len(real_imgs),
        "images_shown": len(shown),
        "images_removed": removed,
    }


def copy_template(out_dir: str | Path) -> None:
    """Copy the page, script and styles into the site folder.

    ``site.json`` holds settings people edit by hand (the address of the server
    with the full-resolution originals); it is created once and never replaced.
    """
    for f in TEMPLATE.rglob("*"):
        if f.is_file():
            dest = Path(out_dir) / f.relative_to(TEMPLATE)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
    settings = Path(out_dir) / "site.json"
    if not settings.exists():
        settings.write_text(json.dumps({"originals_base": ""}, indent=2) + "\n", "utf-8")
