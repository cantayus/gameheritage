"""Build a manifest of the raw collection and register its images.

The raw collection (Mija's Google Drive folder) is read-only input. ``scan_directory``
walks it and hashes every file; ``scan_listing`` builds the same manifest from a
JSON file listing (names and sizes only) when the files are not available locally.
``register_images`` turns content-hashed manifest rows into ``images`` table rows.
"""

from __future__ import annotations

import hashlib
import json
import os
import unicodedata
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import pandas as pd

from .imagefile import MIME, inspect_file
from .names import normalize_text
from .parse import CLOUD_STUB_EXTS, IMAGE_EXTS, OTHER_MEDIA_EXTS, SYSTEM_FILES, parse_filename
from .tables import Tables, upsert

MANIFEST_COLUMNS = [
    "rel_path", "game_folder", "area", "subfolder", "filename", "ext", "media", "bytes", "mtime",
    "sha256", "true_format", "width", "height", "n_frames", "phash", "readable", "error",
    "pattern", "label", "place_hint", "kind_guess", "page_title", "fandom_file",
    "fandom_wiki_path", "fandom_key", "page_key", "series_prefix", "real_subject",
    "original_ext", "subfolder_hint",
]  # fmt: skip

DEFAULT_COLLECTOR = "Mija Aleksandraviciute"
DEFAULT_METHOD = (
    "Downloaded from fan wikis (mostly Fandom) with a batch script, June-August 2026, "
    "and stored in the project's Google Drive folder; retrieved_at is the file's date there"
)


def _media(ext: str) -> str:
    if ext in IMAGE_EXTS:
        return "image"
    if ext in OTHER_MEDIA_EXTS:
        return "other_media"
    if ext in CLOUD_STUB_EXTS:
        return "cloud_stub"
    return "unknown"


def normalize_path(rel_path: str) -> str:
    """Forward slashes and NFC Unicode, so Windows, Mac and Drive paths compare equal."""
    return unicodedata.normalize("NFC", rel_path.replace("\\", "/")).strip("/")


def _split(rel_path: str) -> dict:
    parts = PurePosixPath(rel_path).parts
    area = parts[1] if len(parts) >= 3 else "root"
    subfolder = "/".join(parts[2:-1]) if len(parts) >= 4 else ""
    filename = parts[-1]
    ext = PurePosixPath(filename).suffix.lower()
    if ext and not ext[1:].isalnum():
        ext = ""
    if len(ext) > 6:  # "...St. Basil's Cathedral (Wonder Movies)" has no real extension
        ext = ""
    return {
        "rel_path": rel_path,
        "game_folder": normalize_text(parts[0]) if len(parts) > 1 else "",
        "area": area.lower() if area != "root" else "root",
        "subfolder": subfolder,
        "filename": filename,
        "ext": ext,
        "media": _media(ext),
    }


def _row(rel_path: str, size: int | None, mtime: float | None) -> dict:
    rel_path = normalize_path(rel_path)
    row = {c: "" for c in MANIFEST_COLUMNS}
    row.update(_split(rel_path))
    row["bytes"] = "" if size is None else str(int(size))
    if mtime:
        row["mtime"] = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")
    row.update({k: str(v) for k, v in parse_filename(rel_path).as_dict().items()})
    return row


def is_system_file(name: str) -> bool:
    return name.lower() in SYSTEM_FILES or name.startswith("~$")


def scan_listing(listing: str | Path | dict) -> pd.DataFrame:
    """Manifest from a listing: ``{"entries": [{"path", "size", "mtimeMs"?}, ...]}``.

    Paths are relative to the collection root. No file contents are read, so
    hash, format and dimension columns stay empty.
    """
    data = listing if isinstance(listing, dict) else json.loads(Path(listing).read_text("utf-8"))
    rows = []
    for e in data["entries"]:
        name = PurePosixPath(normalize_path(e["path"])).name
        if is_system_file(name):
            continue
        mt = e.get("mtimeMs")
        rows.append(_row(e["path"], e.get("size"), mt / 1000 if mt else None))
    return pd.DataFrame(rows, columns=MANIFEST_COLUMNS).astype("string")


def scan_directory(
    root: str | Path,
    *,
    games: Iterable[str] | None = None,
    previous: pd.DataFrame | None = None,
    compute_phash: bool = True,
    progress: bool = False,
) -> pd.DataFrame:
    """Walk the collection root and inspect every file, read-only.

    ``games`` limits the scan to these game folders. Rows of ``previous`` whose
    path, size and date are unchanged are reused, so re-runs only hash new files.
    """
    root = Path(root)
    wanted = {normalize_text(g) for g in games} if games else None
    cache: dict[str, dict] = {}
    if previous is not None and len(previous):
        cache = {r["rel_path"]: r for r in previous.to_dict("records")}
    rows: list[dict] = []
    folders = sorted(p for p in root.iterdir() if p.is_dir())
    for folder in folders:
        if wanted and normalize_text(folder.name) not in wanted:
            continue
        for dirpath, _dirs, files in os.walk(folder):
            for name in sorted(files):
                if is_system_file(name):
                    continue
                full = Path(dirpath) / name
                rel = full.relative_to(root).as_posix()
                st = full.stat()
                row = _row(rel, st.st_size, st.st_mtime)
                rel = normalize_path(rel)
                old = cache.get(rel)
                if (
                    old
                    and old.get("sha256")
                    and old.get("bytes") == row["bytes"]
                    and old.get("mtime") == row["mtime"]
                ):
                    rows.append({c: old.get(c, row[c]) for c in MANIFEST_COLUMNS})
                    continue
                if row["media"] in ("image", "unknown"):
                    facts = inspect_file(full, compute_phash=compute_phash)
                    row.update({k: "" if v is None else str(v) for k, v in facts.as_dict().items()})
                    row["bytes"] = str(facts.bytes)
                rows.append(row)
                if progress and len(rows) % 200 == 0:
                    print(f"  scanned {len(rows)} files", flush=True)
    return pd.DataFrame(rows, columns=MANIFEST_COLUMNS).astype("string").fillna("")


def image_id_for(sha256: str) -> str:
    """Stable image ID from content: ``gh-`` + first 12 hex digits of SHA-256."""
    return f"gh-{sha256[:12]}"


def fandom_urls(wiki: str, page_title: str, fandom_file: str) -> tuple[str, str]:
    """Best-known source page and file-page URL on a Fandom wiki."""
    from .sources.fandom import file_page_url, page_url

    file_url = file_page_url(wiki, fandom_file) if wiki and fandom_file else ""
    page = page_url(wiki, page_title) if wiki and page_title else ""
    return (file_url or page), page


def path_image_id(rel_path: str) -> str:
    """Image ID for a file registered before its content was read: from its path."""
    return "gh-p" + hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:11]


CONTENT_FIELDS = ("sha256", "phash", "width", "height", "true_format", "bytes")


def register_images(
    manifest: pd.DataFrame,
    tables: Tables,
    *,
    collected_by: str = DEFAULT_COLLECTOR,
    method: str = DEFAULT_METHOD,
) -> tuple[Tables, list[str]]:
    """Add the manifest's image files to ``tables.images``.

    Identity rules, so IDs never change once assigned:

    * a path that is already registered (``drive_path`` or ``also_at``) keeps its ID,
      and its content facts are filled in if they were missing;
    * a file with the same content as a registered image is added to that
      image's ``also_at`` (one row per unique image);
    * anything else gets a new ID: ``gh-`` + content hash when the file has been
      read, or ``gh-p`` + path hash when only a listing was available.

    Returns the updated tables and the raw folder names that have no alias.
    """

    aliases = dict(zip(tables.folder_aliases["folder_name"], tables.folder_aliases["game_id"]))
    aliases = {normalize_text(k): v for k, v in aliases.items()}
    m = manifest[(manifest["media"] == "image") & (manifest["bytes"] != "0")]
    m = m.sort_values("rel_path")
    unmapped = sorted({f for f in m["game_folder"] if f not in aliases})

    by_path: dict[str, str] = {}
    by_sha: dict[str, str] = {}
    for r in tables.images.to_dict("records"):
        for path in [r["drive_path"], *filter(None, r["also_at"].split(";"))]:
            if path:
                by_path[path] = r["image_id"]
        if r["sha256"]:
            by_sha[r["sha256"]] = r["image_id"]

    new: dict[str, dict] = {}
    fills: list[dict] = []
    extra_paths: dict[str, list[str]] = {}
    records = m.to_dict("records")
    # Paths that already have an ID go first, so their content hash is claimed by
    # the existing row before a byte-identical copy could get a new ID.
    records.sort(key=lambda r: (r["rel_path"] not in by_path, r["rel_path"]))
    for r in records:
        rel, sha = r["rel_path"], r["sha256"]
        iid = by_path.get(rel)
        if iid:
            place = r["place_hint"] or r["subfolder_hint"]
            fills.append(
                {"image_id": iid, "in_game_place": place, **{f: r[f] for f in CONTENT_FIELDS}}
            )
            if sha:
                by_sha.setdefault(sha, iid)
            continue
        if sha and sha in by_sha:
            extra_paths.setdefault(by_sha[sha], []).append(rel)
            by_path[rel] = by_sha[sha]
            continue
        iid = image_id_for(sha) if sha else path_image_id(rel)
        game_id = aliases.get(r["game_folder"], "")
        role = "real" if r["area"] == "real images" and r["pattern"] == "real_numbered" else "game"
        new[iid] = {
            "image_id": iid,
            "role": role,
            "game_id": game_id,
            "kind": r["kind_guess"] or "unknown",
            "title": r["label"],
            "in_game_place": (r["place_hint"] or r["subfolder_hint"]) if role == "game" else "",
            "original_filename": r["filename"],
            "drive_path": rel,
            **{f: r[f] for f in CONTENT_FIELDS},
            # Source pages are only recorded once verified (``fandom-sources``).
            "source_name": "",
            "source_page_url": "",
            "rights_status": "display_only" if role == "game" else "unknown",
            "collected_by": collected_by,
            "collection_method": method,
            "retrieved_at": r["mtime"],
            "review_status": "todo",
        }
        by_path[rel] = iid
        if sha:
            by_sha[sha] = iid

    for iid, paths in extra_paths.items():
        if iid in new:
            new[iid]["also_at"] = ";".join(paths)
    images = upsert(tables.images, list(new.values()) + fills, "image_id")
    if extra_paths:
        images = images.set_index("image_id", drop=False)
        for iid, paths in extra_paths.items():
            if iid in new or iid not in images.index:
                continue
            old = [p for p in images.at[iid, "also_at"].split(";") if p]
            images.at[iid, "also_at"] = ";".join(dict.fromkeys(old + paths))
        images = images.reset_index(drop=True)
    tables.images = images
    return tables, unmapped


def mime_for(true_format: str) -> str:
    return MIME.get(true_format, "")
