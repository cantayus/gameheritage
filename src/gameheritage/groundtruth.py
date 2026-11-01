"""Find, review and download real-world ("ground truth") images with full provenance.

1. ``propose_candidates`` collects candidate photos for each site from its
   Wikidata image (P18), its Commons category (P373) and, optionally, a Commons
   search, keeping only free licenses. Optionally adds Europeana results.
2. A person picks images in the review sheet (``decision = keep``).
3. ``download_approved`` downloads the kept files and registers them in
   ``images`` with source page, author, license, license URL, retrieval date
   and the exact method used, so every image can be cited.
"""

from __future__ import annotations

import contextlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import __version__
from .imagefile import EXT_FOR_FORMAT, inspect_file
from .ingest import image_id_for
from .names import slugify
from .review import contact_sheet, read_sheet, write_sheet
from .schema import is_free_license
from .sources import commons, europeana, wikidata
from .sources.http import HttpClient
from .tables import Tables, upsert

# File titles that are rarely a photo of the building itself.
_NOT_A_PHOTO = re.compile(
    r"\b(map|plan|floor ?plan|diagram|logo|coat of arms|flag|locator|seal|stamp|coin|"
    r"ticket|sign|plaque|svg|icon|chart|scheme|drawing|elevation|section)\b",
    re.I,
)
_HISTORIC = re.compile(r"\b(1[5-8]\d\d|19[0-4]\d)\b|engraving|etching|lithograph|painting", re.I)

CANDIDATE_COLUMNS = [
    "site_id", "site_name", "rank", "decision", "kind", "source", "title", "page_url",
    "file_url", "thumb_url", "width", "height", "license", "license_url", "author", "credit",
    "attribution_required", "is_free", "why",
]  # fmt: skip


def _kind(title: str, mime: str, date_created: str) -> str:
    text = f"{title} {date_created}"
    if re.search(r"engraving|etching|lithograph|painting|drawing", text, re.I):
        return "painting_or_engraving"
    if _HISTORIC.search(text):
        return "historical_photo"
    return "modern_photo" if mime in ("image/jpeg", "image/tiff", "image/webp") else "unknown"


def _score(f: commons.CommonsFile, from_p18: bool) -> float:
    s = 0.0
    if from_p18:
        s += 100
    if not f.is_free:
        s -= 1000
    if _NOT_A_PHOTO.search(f.title):
        s -= 50
    if f.mime not in ("image/jpeg", "image/png", "image/webp", "image/tiff"):
        s -= 50
    s += min(f.width * f.height / 1e6, 24)  # prefer resolution, capped
    if f.width and f.height and f.width >= f.height:
        s += 3  # landscape views match game screenshots better
    return s


def propose_candidates(
    client: HttpClient,
    tables: Tables,
    *,
    site_ids: list[str] | None = None,
    per_site: int = 6,
    category_scan: int = 40,
    search: bool = True,
    europeana_key: str = "",
) -> pd.DataFrame:
    """Candidate real images for sites that have a Wikidata QID."""
    sites = tables.sites[tables.sites["wikidata_qid"] != ""]
    if site_ids:
        sites = sites[sites["site_id"].isin(site_ids)]
    facts = wikidata.site_facts(client, list(sites["wikidata_qid"]))
    rows: list[dict] = []
    for s in sites.to_dict("records"):
        f = facts.get(s["wikidata_qid"], {})
        titles: list[str] = []
        p18 = f.get("image", "")
        if p18:
            titles.append(commons.normalize_title(p18))
        cat = s["commons_category"] or f.get("commons_category", "")
        if cat:
            titles += commons.category_files(client, cat, limit=category_scan)
        if search and len(titles) < per_site:
            titles += commons.search_files(client, s["name"], limit=category_scan)
        titles = list(dict.fromkeys(titles))
        files = commons.file_info(client, titles) if titles else []
        p18_title = commons.normalize_title(p18) if p18 else ""
        scored = sorted(files, key=lambda x: _score(x, x.title == p18_title), reverse=True)
        kept = [x for x in scored if x.is_free][:per_site]
        for rank, x in enumerate(kept, 1):
            why = ["Wikidata image (P18)"] if x.title == p18_title else []
            if cat and x.title != p18_title:
                why.append(f"Commons category {cat}")
            rows.append(
                {
                    "site_id": s["site_id"],
                    "site_name": s["name"],
                    "rank": rank,
                    "decision": "",
                    "kind": _kind(x.title, x.mime, x.date_created),
                    "source": commons.SOURCE_NAME,
                    "title": x.title.removeprefix("File:"),
                    "page_url": x.page_url,
                    "file_url": x.file_url,
                    "thumb_url": x.thumb_url,
                    "width": x.width,
                    "height": x.height,
                    "license": x.license,
                    "license_url": x.license_url,
                    "author": x.author,
                    "credit": x.credit,
                    "attribution_required": x.attribution_required,
                    "is_free": x.is_free,
                    "why": "; ".join(why) or "Commons search",
                }
            )
        if europeana_key:
            for j, item in enumerate(
                [
                    i
                    for i in europeana.search(client, s["name"], europeana_key, rows=10)
                    if i["is_free"]
                ][:3],
                1,
            ):
                rows.append(
                    {
                        "site_id": s["site_id"],
                        "site_name": s["name"],
                        "rank": len(kept) + j,
                        "decision": "",
                        "kind": "historical_photo",
                        "source": europeana.SOURCE_NAME,
                        "title": item["title"],
                        "page_url": item["page_url"],
                        "file_url": item["file_url"],
                        "thumb_url": item["thumb_url"],
                        "license": item["license"],
                        "license_url": item["rights_url"],
                        "author": item["creator"],
                        "credit": item["provider"],
                        "is_free": True,
                        "why": "Europeana search",
                    }
                )
    return pd.DataFrame(rows, columns=CANDIDATE_COLUMNS)


def export_candidates(df: pd.DataFrame, out_dir: str | Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    csv = write_sheet(df, out_dir / "groundtruth.csv")
    html_path = contact_sheet(
        df,
        out_dir / "groundtruth.html",
        title="Ground-truth candidates",
        src=lambda r: r["thumb_url"] or r["file_url"],
        caption=lambda r: f"#{r['rank']} {r['license']}",
        sub=lambda r: f"{r['title']} — {r['author']}",
        group="site_name",
    )
    return csv, html_path


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def download_approved(
    client: HttpClient,
    tables: Tables,
    sheet: str | Path | pd.DataFrame,
    images_root: str | Path,
    *,
    project_root: str | Path | None = None,
    use_thumbnail: bool = False,
) -> tuple[Tables, list[str]]:
    """Download candidates marked ``keep`` and register them with provenance.

    Files go to ``images_root/real/<site_id>/``. ``local_path`` is stored
    relative to ``project_root`` when given. Returns the tables and messages.
    """
    df = sheet if isinstance(sheet, pd.DataFrame) else read_sheet(sheet, ["decision", "file_url"])
    df = df[df["decision"] == "keep"]
    root = Path(images_root)
    msgs: list[str] = []
    rows = []
    for r in df.to_dict("records"):
        if not is_free_license(r["license"]):
            msgs.append(f"skipped {r['title']}: license {r['license']!r} is not free")
            continue
        url = r["thumb_url"] if use_thumbnail and r.get("thumb_url") else r["file_url"]
        stem = slugify(Path(r["title"]).stem)[:80] or "image"
        tmp = root / "real" / r["site_id"] / f"{stem}.download"
        try:
            path, sha, n = client.download(url, tmp)
        except Exception as exc:  # keep going; report at the end
            msgs.append(f"failed {r['title']}: {exc}")
            continue
        facts = inspect_file(path)
        ext = EXT_FOR_FORMAT.get(facts.true_format, Path(r["title"]).suffix.lower() or ".bin")
        final = path.with_name(f"{stem[:60]}-{sha[:8]}{ext}")  # content in the name: no clashes
        path.replace(final)
        local = final
        if project_root:
            try:
                local = final.resolve().relative_to(Path(project_root).resolve())
            except ValueError:
                local = final.resolve()
        rows.append(
            {
                "image_id": image_id_for(sha),
                "role": "real",
                "site_id": r["site_id"],
                "kind": r["kind"] or "modern_photo",
                "title": r["title"],
                "original_filename": r["title"],
                "local_path": Path(local).as_posix(),
                "sha256": sha,
                "phash": facts.phash,
                "width": facts.width,
                "height": facts.height,
                "true_format": facts.true_format,
                "bytes": n,
                "source_name": r["source"],
                "source_page_url": r["page_url"],
                "file_url": url,
                "author": r["author"],
                "notes": f"credit line on source: {r['credit']}" if r.get("credit") else "",
                "license": r["license"],
                "license_url": r["license_url"],
                "rights_status": "open",
                "attribution_required": r.get("attribution_required", ""),
                "collected_by": f"gameheritage {__version__}",
                "collection_method": (
                    f"{r['source']} API ({'thumbnail' if use_thumbnail else 'original file'}); "
                    f"candidate reason: {r.get('why', '')}; approved in review sheet"
                ),
                "retrieved_at": _now(),
                "review_status": "keep",
            }
        )
        msgs.append(f"downloaded {r['title']} -> {final.name}")
    tables.images = upsert(tables.images, rows, "image_id")  # never overwrite human edits
    return tables, msgs


# --------------------------------------------------------------------------- automatic


def auto_real_images(
    client: HttpClient,
    tables: Tables,
    images_root: str | Path,
    **kwargs,
) -> tuple[Tables, list[str]]:
    """Download and register real images; see :func:`real_image_rows`."""
    rows, msgs = real_image_rows(client, tables, images_root, **kwargs)
    tables.images = upsert(tables.images, rows, "image_id")
    return tables, msgs


def real_image_rows(
    client: HttpClient,
    tables: Tables,
    images_root: str | Path,
    *,
    project_root: str | Path | None = None,
    thumb_width: int = 1280,
    site_ids: list[str] | None = None,
    limit: int | None = None,
    extra_per_site: int = 0,
    free_only: bool = False,
    progress: bool = False,
    checkpoint=None,
    use_categories: bool = False,
    read_licenses: bool = True,
) -> tuple[list[dict], list[str]]:
    """Download photos of linked sites from Wikimedia Commons; return image rows.

    Each site gets its Wikidata representative image (P18) and, with
    ``extra_per_site``, up to that many more from the item's other Wikidata image
    properties (panoramic, aerial, night, interior, winter views) and, with
    ``use_categories``, from its Commons category. Licenses
    are always recorded; ``free_only`` skips non-free files (the publish gate
    excludes them from releases either way). Files are saved at ``thumb_width``
    px and named with their content hash, so names never clash. ``checkpoint``,
    if given, receives each batch of 100 new rows as soon as it is ready.
    """
    real = tables.images[(tables.images["role"] == "real") & (tables.images["source_name"] != "")]
    have = real.groupby("site_id").size().to_dict()
    known = set(real["source_page_url"])
    want_n = 1 + extra_per_site
    sites = tables.sites[tables.sites["wikidata_qid"] != ""]
    sites = sites[sites["site_id"].map(lambda x: have.get(x, 0) < want_n).astype(bool)]
    if site_ids:
        sites = sites[sites["site_id"].isin(site_ids)]
    if limit:
        sites = sites.head(limit)
    msgs: list[str] = []
    want: dict[str, str] = {}
    why: dict[str, str] = {}
    # Wikidata image properties first (query service: fast, not rate-limited).
    imgs_by_qid = wikidata.item_images(client, list(sites["wikidata_qid"]))
    for s in sites.sort_values("site_id").to_dict("records"):
        picked = have.get(s["site_id"], 0)
        options = imgs_by_qid.get(s["wikidata_qid"], [])
        if not options:
            msgs.append(f"no Wikidata image: {s['site_id']}")
        for name, label in options:
            if picked >= want_n:
                break
            nt = commons.normalize_title(name)
            if nt in want:
                continue
            want[nt] = s["site_id"]
            why[nt] = f"Wikidata {label} ({'P18' if label == 'image' else label})"
            picked += 1
        # Commons categories only when asked (the Commons API is slow from cloud machines).
        cat = s["commons_category"]
        if use_categories and picked < want_n and cat:
            try:
                titles = commons.category_files(client, cat, limit=30)
            except Exception as exc:  # one bad category must not stop the run
                msgs.append(f"category {cat}: {exc}")
                titles = []
            for t in titles:
                if picked >= want_n:
                    break
                nt = commons.normalize_title(t)
                if nt in want or _NOT_A_PHOTO.search(t):
                    continue
                want[nt] = s["site_id"]
                why[nt] = f"Commons category '{cat}'"
                picked += 1
    if progress:
        print(f"  {len(want)} candidate photos", flush=True)
    if read_licenses:
        files = commons.file_info(client, list(want), thumb_width=thumb_width)
    else:
        # License and author are read later (``gameheritage licenses``): the Commons
        # API is heavily rate-limited from cloud machines, the image host is not.
        files = []
        for t in want:
            page, thumb, orig = commons.direct_urls(t, thumb_width)
            if not re.search(r"\.(jpe?g|png|webp)$", t, re.I):
                continue
            files.append(
                commons.CommonsFile(
                    title=t, page_url=page, file_url=orig, thumb_url=thumb, width=0,
                    height=0, mime="image/jpeg", sha1="", size=0, license="",
                    license_url="", license_code="", author="", credit="",
                    attribution_required=None, usage_terms="", description="",
                    date_created="", object_name="", is_free=False,
                )
            )  # fmt: skip
    files = [f for f in files if f.page_url not in known]
    root = Path(images_root)
    rows: list[dict] = []
    flushed = 0
    for f in files:
        key = f.title if f.title in want else commons.normalize_title(f.title)
        site_id = want.get(key)
        if not site_id:
            continue
        if free_only and not f.is_free:
            msgs.append(f"skipped {f.title}: license {f.license!r}")
            continue
        if not f.mime.startswith("image/") or f.mime == "image/svg+xml":
            continue
        url = f.thumb_url or f.file_url
        stem = slugify(Path(f.title.removeprefix("File:")).stem)[:60] or "image"
        dest = root / "real" / site_id / f"{stem}.download"
        try:
            path, sha, n = client.download(url, dest)
        except Exception as exc:
            if url == f.file_url or not f.file_url:
                msgs.append(f"failed {f.title}: {exc}")
                continue
            try:  # small originals have no thumbnail at this width
                url = f.file_url
                path, sha, n = client.download(url, dest)
            except Exception as exc2:  # report and continue
                msgs.append(f"failed {f.title}: {exc2}")
                continue
        facts_f = inspect_file(path)
        ext = EXT_FOR_FORMAT.get(facts_f.true_format, ".jpg")
        final = path.with_name(f"{stem}-{sha[:8]}{ext}")
        path.replace(final)
        local = final.resolve()
        if project_root:
            with contextlib.suppress(ValueError):  # other drive on Windows: keep absolute
                local = Path(os.path.relpath(local, Path(project_root).resolve()))
        rows.append(
            {
                "image_id": image_id_for(sha),
                "role": "real",
                "site_id": site_id,
                "kind": _kind(f.title, f.mime, f.date_created),
                "title": f.title.removeprefix("File:"),
                "original_filename": f.title.removeprefix("File:"),
                "local_path": local.as_posix(),
                "sha256": sha,
                "phash": facts_f.phash,
                "width": facts_f.width,
                "height": facts_f.height,
                "true_format": facts_f.true_format,
                "bytes": n,
                "source_name": commons.SOURCE_NAME,
                "source_page_url": f.page_url,
                "file_url": url,
                "author": f.author,
                "license": f.license,
                "license_url": f.license_url,
                "rights_status": "open" if f.is_free else "unknown",
                # (license empty = not read yet; run `gameheritage licenses`)
                "attribution_required": f.attribution_required,
                "collected_by": f"gameheritage {__version__}",
                "collection_method": (
                    f"{why.get(key, 'Commons file')} of the linked site, read from the "
                    "Wikimedia Commons API (imageinfo/extmetadata); "
                    f"{thumb_width}px thumbnail"
                ),
                "retrieved_at": _now(),
                "review_status": "keep",
                "notes": f"credit line on Commons: {f.credit}" if f.credit else "",
            }
        )
        if progress and len(rows) % 50 == 0:
            print(f"  downloaded {len(rows)} images", flush=True)
        if checkpoint and len(rows) - flushed >= 100:
            checkpoint(rows[flushed:])
            flushed = len(rows)
    if checkpoint and len(rows) > flushed:
        checkpoint(rows[flushed:])
    msgs.insert(0, f"downloaded {len(rows)} real images")
    return rows, msgs


_KIND_RANK = {"screenshot": 0, "unknown": 1, "render": 1, "database_entry": 2, "concept_art": 3}


def make_pairs(tables: Tables) -> tuple[Tables, int]:
    """Pair every linked game image with its site's best real image.

    The best real image of a site (sourced, not dropped) is used for all of the site's
    game images; one pair per depiction is marked ``featured``. Existing pairs and
    their review decisions are kept.
    """
    imgs = tables.images
    real = imgs[
        (imgs["role"] == "real")
        & (imgs["site_id"] != "")
        & (imgs["source_page_url"] != "")
        & (imgs["review_status"] != "drop")
    ]
    if not len(real):
        return tables, 0
    real = real.assign(
        _free=real["license"].map(is_free_license).astype(bool),
        _p18=real["collection_method"].str.contains("P18", regex=False),
        _px=pd.to_numeric(real["width"], errors="coerce").fillna(0),
    )
    # Prefer freely licensed photos, then the Wikidata image, then resolution.
    best_real = real.sort_values(["_free", "_p18", "_px"], ascending=False)
    best_real = best_real.drop_duplicates("site_id")
    best_real = best_real.set_index("site_id")["image_id"]
    game = imgs[
        (imgs["role"] == "game")
        & (imgs["depiction_id"] != "")
        & imgs["site_id"].isin(best_real.index)
        & (imgs["review_status"] != "drop")
    ].copy()
    game["_rank"] = game["kind"].map(_KIND_RANK).fillna(4)
    game["_bytes"] = pd.to_numeric(game["bytes"], errors="coerce").fillna(0)
    featured = set(
        game.sort_values(["_rank", "_bytes"], ascending=[True, False]).drop_duplicates(
            "depiction_id"
        )["image_id"]
    )
    existing = set(tables.pairs["pair_id"])
    rows = []
    for r in game.to_dict("records"):
        real_id = best_real[r["site_id"]]
        pair_id = f"{r['image_id']}--{real_id}"
        if pair_id in existing:
            continue
        rows.append(
            {
                "pair_id": pair_id,
                "depiction_id": r["depiction_id"],
                "game_image_id": r["image_id"],
                "real_image_id": real_id,
                "featured": r["image_id"] in featured,
                "review_status": "todo",
            }
        )
    tables.pairs = upsert(tables.pairs, rows, "pair_id")
    return tables, len(rows)


def backfill_licenses(client: HttpClient, tables: Tables) -> tuple[Tables, int]:
    """Read author and license for Commons images downloaded without them."""
    imgs = tables.images
    todo = imgs[(imgs["source_name"] == commons.SOURCE_NAME) & (imgs["license"] == "")]
    titles = [commons.normalize_title(t) for t in todo["original_filename"]]
    info = {f.title: f for f in commons.file_info(client, titles)} if titles else {}
    rows = []
    for r in todo.to_dict("records"):
        f = info.get(commons.normalize_title(r["original_filename"]))
        if not f:
            continue
        rows.append(
            {
                "image_id": r["image_id"],
                "author": f.author,
                "license": f.license,
                "license_url": f.license_url,
                "attribution_required": f.attribution_required,
                "rights_status": "open" if f.is_free else "unknown",
                "source_page_url": f.page_url,
            }
        )
    tables.images = upsert(tables.images, rows, "image_id")
    tables = settle_rights(tables)
    return tables, len(rows)


def settle_rights(tables: Tables) -> Tables:
    """Set ``rights_status`` from the license where it is still the download-time
    placeholder (empty or ``unknown``): ``open`` for free licenses."""
    imgs = tables.images.copy()
    mask = (
        (imgs["role"] == "real")
        & (imgs["license"] != "")
        & imgs["rights_status"].isin(["", "unknown"])
    )
    free = imgs.loc[mask, "license"].map(is_free_license)
    imgs.loc[mask, "rights_status"] = free.map({True: "open", False: "unknown"})
    tables.images = imgs
    return tables
