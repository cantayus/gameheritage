"""Build a dataset release: publishable rows, several formats, credits and metadata.

A release folder holds:

* ``games.csv``, ``sites.csv``, ``depictions.csv``, ``images.csv``, ``pairs.csv``
  (plus ``.json``, and ``.parquet`` when pyarrow is installed);
* ``datapackage.json``: Frictionless Data Package description of the tables;
* ``release.json``: version, build date and row counts;
* ``credits.csv``, ``CREDITS.md``, ``image_references.bib``, ``image_references.json``.

Image files are not part of a release: rows carry source and file URLs.
"""

from __future__ import annotations

import contextlib
import json
import logging
import shutil
import tempfile
import typing
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import __version__
from .credits import write_credits
from .dictionary import FIELDS
from .schema import MODELS, PRIMARY_KEYS, is_free_license
from .tables import TABLE_NAMES, Tables

log = logging.getLogger(__name__)

RELEASE_TABLES = ("games", "sites", "depictions", "images", "pairs")
PRIVATE_IMAGE_COLUMNS = ("drive_path", "also_at", "local_path")
SITE_URL = "https://cantayus.github.io/video-game-digital-heritage/"

# Columns a release adds to the curation tables (name, type, description).
RELEASE_EXTRA = {
    "sites": [
        (
            "wikidata_type",
            "string",
            "What Wikidata says the place is (instance of), e.g. 'cathedral'",
        ),
        ("year", "integer", "Year the place was built or founded; negative for BCE"),
        ("century", "string", "Century of that year, e.g. '12th century', '26th century BCE'"),
        ("era", "string", "Historical era used on the website (Prehistory ... 20th century)"),
        ("web_page", "string", "The place's page on the project website"),
    ],
    "depictions": [
        ("verified", "boolean", "True when a project member reviewed the match"),
        ("web_page", "string", "The real place's page on the project website"),
    ],
    "images": [
        ("web_url", "string", "Web copy hosted by the project (WebP, longest side 1200 px)"),
        ("thumb_url", "string", "Thumbnail hosted by the project (WebP, longest side 400 px)"),
        ("web_width", "integer", "Width of the web copy in pixels"),
        ("web_height", "integer", "Height of the web copy in pixels"),
    ],
}


def publishable(tables: Tables, *, include_unreviewed: bool = False) -> Tables:
    """The subset of rows that may be released.

    Default: kept pairs, kept images that pass the rights rules (real images with
    a free license and source page; game images with a source page), and the
    sites, depictions and games they refer to. ``include_unreviewed`` makes a
    preview that keeps everything not marked ``drop``.
    """
    t = Tables(**{n: tables[n].copy() for n in TABLE_NAMES})
    imgs = t.images[t.images["review_status"] != "drop"]
    if include_unreviewed:
        pairs = t.pairs[t.pairs["review_status"] != "drop"]
    else:
        pairs = t.pairs[t.pairs["review_status"] == "keep"]
        ok_real = (imgs["role"] == "real") & imgs["license"].map(is_free_license).astype(bool)
        ok_real &= imgs["source_page_url"] != ""
        ok_game = (imgs["role"] == "game") & (imgs["source_page_url"] != "")
        imgs = imgs[(imgs["review_status"] == "keep") & (ok_real | ok_game)]
        pair_ids = set(pairs["game_image_id"]) | set(pairs["real_image_id"])
        pairs = pairs[
            pairs["game_image_id"].isin(imgs["image_id"])
            & pairs["real_image_id"].isin(imgs["image_id"])
        ]
        imgs = imgs[imgs["image_id"].isin(set(imgs["image_id"]) | pair_ids)]
    dep_ids = set(pairs["depiction_id"]) | set(imgs["depiction_id"])
    deps = t.depictions[
        t.depictions["depiction_id"].isin(dep_ids)
        | (include_unreviewed & (t.depictions["review_status"] != "drop"))
        | (t.depictions["review_status"] == "keep")
    ]
    site_ids = set(deps["site_id"]) | set(imgs["site_id"])
    sites = t.sites[
        t.sites["site_id"].isin(site_ids)
        | (include_unreviewed & (t.sites["review_status"] != "drop"))
    ]
    game_ids = set(deps["game_id"]) | set(imgs["game_id"])
    games = t.games if include_unreviewed else t.games[t.games["game_id"].isin(game_ids)]
    imgs = imgs.copy()
    for c in PRIVATE_IMAGE_COLUMNS:
        imgs[c] = ""
    return Tables(
        games=games, sites=sites, depictions=deps, images=imgs, pairs=pairs,
        folder_aliases=t.folder_aliases.iloc[0:0],
    )  # fmt: skip


def website_subset(tables: Tables, site_dir: str | Path, site_url: str = SITE_URL) -> Tables:
    """Exactly what the project website shows, with the release's extra columns.

    ``site_dir`` is a website built by ``gameheritage website``; its
    ``data/places/*.json`` say which places and images are published and
    ``data/image_sizes.json`` gives the size of each web copy. Automatic
    matches are included and marked ``verified = false``.
    """
    from .autolink import place_kind
    from .history import century_of, era_of, parse_year

    site_dir = Path(site_dir)
    places = [
        json.loads(f.read_text("utf-8"))
        for f in sorted((site_dir / "data" / "places").glob("*.json"))
    ]
    sizes = json.loads((site_dir / "data" / "image_sizes.json").read_text("utf-8"))
    site_ids = {p["id"] for p in places}
    dep_ids = {d["id"] for p in places for d in p["depictions"]}
    verified = {d["id"]: d["verified"] for p in places for d in p["depictions"]}
    img_ids = {i["id"] for p in places for i in p["real"]}
    img_ids |= {i["id"] for p in places for d in p["depictions"] for i in d["images"]}
    base = site_url.rstrip("/") + "/"

    sites = tables.sites[tables.sites["site_id"].isin(site_ids)].copy()
    sites["wikidata_type"] = (
        sites["notes"].str.extract(r"type: ([^\n]*)", expand=False).fillna("").str.strip()
    )
    years = [parse_year(v) for v in sites["construction_date"]]
    kinds = [place_kind(t) for t in sites["wikidata_type"]]
    sites["year"] = ["" if y is None else str(y) for y in years]
    sites["century"] = [century_of(y) for y in years]
    sites["era"] = [era_of(y, natural=k == "natural" and y is None) for y, k in zip(years, kinds)]
    sites["web_page"] = base + "#/place/" + sites["site_id"]

    deps = tables.depictions[tables.depictions["depiction_id"].isin(dep_ids)].copy()
    deps["verified"] = deps["depiction_id"].map(lambda d: "true" if verified.get(d) else "false")
    deps["web_page"] = base + "#/place/" + deps["site_id"]

    imgs = tables.images[tables.images["image_id"].isin(img_ids)].copy()
    imgs["web_url"] = base + "img/full/" + imgs["image_id"] + ".webp"
    imgs["thumb_url"] = base + "img/thumb/" + imgs["image_id"] + ".webp"
    imgs["web_width"] = imgs["image_id"].map(lambda i: str(sizes.get(i, ["", ""])[0]))
    imgs["web_height"] = imgs["image_id"].map(lambda i: str(sizes.get(i, ["", ""])[1]))
    for c in PRIVATE_IMAGE_COLUMNS:
        imgs[c] = ""

    pairs = tables.pairs[
        (tables.pairs["review_status"] != "drop")
        & tables.pairs["game_image_id"].isin(img_ids)
        & tables.pairs["real_image_id"].isin(img_ids)
    ]
    games = tables.games[tables.games["game_id"].isin(set(deps["game_id"]))]
    return Tables(
        games=games, sites=sites, depictions=deps, images=imgs, pairs=pairs,
        folder_aliases=tables.folder_aliases.iloc[0:0],
    )  # fmt: skip


def _release_columns(name: str, df: pd.DataFrame) -> list[str]:
    cols = list(MODELS[name].model_fields)
    if name == "images":
        cols = [c for c in cols if c not in PRIVATE_IMAGE_COLUMNS]
    return cols + [c for c, _, _ in RELEASE_EXTRA.get(name, []) if c in df.columns]


def _write_release_table(df: pd.DataFrame, out: Path, name: str) -> pd.DataFrame:
    cols = _release_columns(name, df)
    df = df.copy()
    for c in cols:
        if c not in df.columns:
            df[c] = ""
    df = df[cols].astype("string").fillna("")
    df = df.sort_values(PRIMARY_KEYS[name], kind="stable").reset_index(drop=True)
    df.to_csv(out / f"{name}.csv", index=False, encoding="utf-8", lineterminator="\n")
    return df


_TYPES = {int: "integer", float: "number", bool: "boolean", str: "string"}


def _field_type(annotation) -> str:
    args = [a for a in typing.get_args(annotation) if a is not type(None)] or [annotation]
    a = args[0]
    if isinstance(a, type) and issubclass(a, str):
        return "string"
    return _TYPES.get(a, "string")


def datapackage(version: str, counts: dict[str, int], extra: bool = False) -> dict:
    resources = []
    for name in RELEASE_TABLES:
        model = MODELS[name]
        fields = [
            {
                "name": f,
                "type": _field_type(info.annotation),
                "description": FIELDS[name].get(f, ""),
            }
            for f, info in model.model_fields.items()
            if not (name == "images" and f in PRIVATE_IMAGE_COLUMNS)
        ]
        if extra:
            fields += [
                {"name": c, "type": t, "description": d} for c, t, d in RELEASE_EXTRA.get(name, [])
            ]
        resources.append(
            {
                "name": name,
                "path": f"{name}.csv",
                "format": "csv",
                "mediatype": "text/csv",
                "encoding": "utf-8",
                "rows": counts.get(name, 0),
                "schema": {"fields": fields, "primaryKey": PRIMARY_KEYS[name]},
            }
        )
    return {
        "name": "gameheritage",
        "title": "Video game digital heritage: historical structures in games and real life",
        "version": version,
        "licenses": [
            {
                "name": "CC-BY-4.0",
                "path": "https://creativecommons.org/licenses/by/4.0/",
                "title": "Metadata and project-written text; third-party images and Wikipedia "
                "text keep their own licenses (see credits.csv)",
            }
        ],
        "contributors": [
            {"title": "Cantay Caliskan", "role": "author"},
            {"title": "Mija Aleksandraviciute", "role": "author"},
        ],
        "resources": resources,
    }


def build_release(
    tables: Tables,
    out_dir: str | Path,
    version: str,
    *,
    include_unreviewed: bool = False,
    package_copy: str | Path | None = None,
    site_dir: str | Path | None = None,
    site_url: str = SITE_URL,
) -> Path:
    """Write a release to ``out_dir`` (replacing it). Optionally copy it into the package.

    With ``site_dir`` (a built website) the release holds exactly what the
    website shows, automatic matches included and flagged, plus the columns
    in ``RELEASE_EXTRA``. Without it, only reviewed rows are released
    (``include_unreviewed`` makes a preview of everything not dropped).
    """
    final = Path(out_dir)
    final.parent.mkdir(parents=True, exist_ok=True)
    # Build next to the target and swap in at the end: a failed build never
    # destroys the previous release.
    out = Path(tempfile.mkdtemp(prefix=f".{final.name}-", dir=final.parent))
    if site_dir is not None:
        pub = website_subset(tables, site_dir, site_url)
    else:
        pub = publishable(tables, include_unreviewed=include_unreviewed)
    counts = {}
    for name in RELEASE_TABLES:
        df = _write_release_table(pub[name], out, name)
        counts[name] = len(df)
        df.to_json(out / f"{name}.json", orient="records", force_ascii=False, indent=1)
        with contextlib.suppress(ImportError):
            df.to_parquet(out / f"{name}.parquet", index=False)
    write_credits(pub, out)
    dp = datapackage(version, counts, extra=site_dir is not None)
    (out / "datapackage.json").write_text(json.dumps(dp, indent=2, ensure_ascii=False), "utf-8")
    meta = {
        "version": version,
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "builder": f"gameheritage {__version__}",
        "scope": "website"
        if site_dir is not None
        else ("preview" if include_unreviewed else "reviewed"),
        "preview": include_unreviewed,
        "website": site_url if site_dir is not None else "",
        "counts": counts,
    }
    (out / "release.json").write_text(json.dumps(meta, indent=2))
    if final.exists():
        shutil.rmtree(final)
    out.rename(final)
    out = final
    if package_copy:
        dest = Path(package_copy)
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        # The package carries the CSV tables, metadata and credits; the JSON and
        # Parquet copies stay in the release folder.
        for f in out.iterdir():
            if f.suffix in (".csv", ".md", ".bib") or f.name in (
                "release.json",
                "datapackage.json",
            ):
                shutil.copy2(f, dest / f.name)
    return out


def make_web_images(
    tables: Tables,
    out_dir: str | Path,
    *,
    collection_root: str | Path,
    project_root: str | Path,
    max_side: int = 1600,
    quality: int = 82,
    image_ids: list[str] | None = None,
) -> list[Path]:
    """WebP copies (longest side ``max_side``) for images available locally.

    Pass ``image_ids`` (normally the publishable images) to respect the publish
    gate; the CLI always does.
    """
    from PIL import Image

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    done = []
    imgs = tables.images
    if image_ids is not None:
        imgs = imgs[imgs["image_id"].isin(image_ids)]
    for r in imgs.to_dict("records"):
        src = None
        if r["local_path"]:
            src = Path(project_root) / r["local_path"]
        elif r["drive_path"]:
            src = Path(collection_root) / r["drive_path"]
        if not src or not src.exists():
            continue
        dest = out / f"{r['image_id']}.webp"
        if dest.exists():
            done.append(dest)
            continue
        try:
            with Image.open(src) as im:
                im = im.convert("RGB")
                im.thumbnail((max_side, max_side))
                im.save(dest, "WEBP", quality=quality, method=5)
        except (OSError, ValueError) as exc:  # unreadable file: skip, keep going
            log.warning("skipped %s: %s", src, exc)
            continue
        done.append(dest)
    return done


def release_table(path: str | Path, name: str) -> pd.DataFrame:
    return pd.read_csv(Path(path) / f"{name}.csv", dtype=str, keep_default_na=False)
