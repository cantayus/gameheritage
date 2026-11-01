"""Download catalogue images, with an attribution file next to them."""

from __future__ import annotations

import re
import shutil
import warnings
from pathlib import Path

import pandas as pd

from .imagefile import EXT_FOR_FORMAT, inspect_file
from .schema import license_family
from .sources.http import HttpClient

GAME_NOTICE = (
    "Game images are copyrighted by their developers and publishers. They are provided "
    "for research and commentary; check your own use against your local copyright law."
)


_COMMONS_THUMB = re.compile(r"^(https://upload\.wikimedia\.org/wikipedia/[^/]+)/thumb/(.+)/[^/]+$")


def original_url(file_url: str) -> str:
    """The full-size file behind a Wikimedia thumbnail URL (other URLs unchanged)."""
    m = _COMMONS_THUMB.match(file_url or "")
    return f"{m.group(1)}/{m.group(2)}" if m else (file_url or "")


def _image_ids(rows, role: str) -> list[str]:
    if isinstance(rows, str):
        return [rows]
    if not isinstance(rows, pd.DataFrame):
        return list(dict.fromkeys(rows))
    if "image_id" in rows.columns:
        df = rows if role == "both" or "role" not in rows else rows[rows["role"] == role]
        return list(dict.fromkeys(df["image_id"]))
    ids: list[str] = []
    if role in ("both", "game") and "game_image_id" in rows:
        ids += list(rows["game_image_id"])
    if role in ("both", "real") and "real_image_id" in rows:
        ids += list(rows["real_image_id"])
    return list(dict.fromkeys(i for i in ids if i))


def download(
    rows: pd.DataFrame,
    dest: str | Path,
    *,
    catalog=None,
    role: str = "both",
    size: str = "web",
    fallback: bool = True,
    licenses: list[str] | None = None,
    overwrite: bool = False,
    client: HttpClient | None = None,
    local_roots: list[str | Path] | None = None,
) -> pd.DataFrame:
    """Download the images behind ``rows`` to ``dest``.

    ``rows`` is any table from the catalogue with image IDs: ``images()``,
    ``matches()`` (its first game and real image), ``pairs()``, or a list of IDs.

    * ``role``: ``"real"``, ``"game"`` or ``"both"``.
    * ``size``: ``"web"`` (default) is the project's copy (WebP, longest side
      1200 px), fast and always available; ``"thumb"`` is 400 px; ``"original"``
      is the full file from its source (Wikimedia Commons, Fandom). With
      ``fallback`` (default), an original that has no source URL or fails to
      download is replaced by the web copy; the ``copy`` column says which one
      you got.
    * ``licenses``: keep real images whose license family is one of these, e.g.
      ``["CC0", "CC BY", "Public domain"]``. Families match exactly: ``"CC BY"``
      does not include CC BY-SA or CC BY-NC (list ``"CC BY-SA"`` to add it).
    * ``local_roots``: folders to copy from instead of downloading, when a
      row's ``local_path`` or ``drive_path`` exists under one of them.

    Files are saved as ``dest/<role>/<image_id>.<ext>``. ``dest/ATTRIBUTION.csv``
    lists the credit line and source of every file in the folder (merged across
    calls). Returns the table for this call.
    """
    if role not in ("both", "game", "real"):
        raise ValueError("role must be 'both', 'game' or 'real'")
    if size not in ("web", "thumb", "original"):
        raise ValueError("size must be 'web', 'thumb' or 'original'")
    if catalog is None:
        from .catalog import load

        catalog = load()
    dest = Path(dest)
    imgs = catalog.images().drop_duplicates("image_id").set_index("image_id")
    ids = [i for i in _image_ids(rows, role) if i in imgs.index]
    if client is None:
        from .sources.http import public_client

        client = public_client()
    roots = [Path(r) for r in (local_roots or [])]
    wanted_families = {license_family(x) for x in (licenses or [])}
    out = []
    warned = False
    for iid in ids:
        r = imgs.loc[iid]
        if role != "both" and r["role"] != role:
            continue
        # Exact license families: "CC BY" does not admit CC BY-NC or CC BY-SA.
        if r["role"] == "real" and licenses and license_family(r["license"]) not in wanted_families:
            out.append({"image_id": iid, "role": r["role"], "status": "skipped: license"})
            continue
        if r["role"] == "game" and not warned:
            warnings.warn(GAME_NOTICE, stacklevel=2)
            warned = True
        folder = dest / r["role"]
        existing = list(folder.glob(f"{iid}.*")) if folder.exists() else []
        if existing and not overwrite:
            out.append(_row(iid, r, existing[0], "exists"))
            continue
        src = next(
            (
                root / p
                for root in roots
                for p in (r["local_path"], r["drive_path"])
                if p and (root / p).exists()
            ),
            None,
        )
        tmp = folder / f"{iid}.download"
        hosted = {"web": r.get("web_url", ""), "thumb": r.get("thumb_url", "")}
        hosted["thumb"] = hosted["thumb"] or hosted["web"]
        # Sources to try, in order: (url or local file, which copy it is).
        tries: list[tuple[object, str]] = []
        if size == "original":
            if src:
                tries.append((src, "original"))
            if r["file_url"]:
                tries.append((original_url(r["file_url"]), "original"))
            if fallback and hosted["web"]:
                tries.append((hosted["web"], "web"))
        else:
            if hosted[size]:
                tries.append((hosted[size], size))
            elif src:
                tries.append((src, "original"))
            elif fallback and r["file_url"]:
                tries.append((original_url(r["file_url"]), "original"))
        if not tries:
            out.append({"image_id": iid, "role": r["role"], "status": "no file available"})
            continue
        status, copy, errors = "", "", []
        for where, copy in tries:
            try:
                folder.mkdir(parents=True, exist_ok=True)
                if isinstance(where, Path):
                    shutil.copy2(where, tmp)
                    status = "copied"
                else:
                    client.download(str(where), tmp)
                    status = "downloaded"
                break
            except Exception as exc:  # try the next copy; the status column reports it
                errors.append(f"{copy}: {exc}")
                status = ""
        if not status:
            tmp.unlink(missing_ok=True)
            out.append(
                {"image_id": iid, "role": r["role"], "status": "failed: " + "; ".join(errors)}
            )
            continue
        facts = inspect_file(tmp, compute_phash=False)
        if copy == "original" and r["sha256"] and facts.sha256 != r["sha256"]:
            status += " (checksum differs from catalogue)"
        final = tmp.with_suffix(EXT_FOR_FORMAT.get(facts.true_format, ".bin"))
        tmp.replace(final)
        out.append(_row(iid, r, final, status, copy))
    table = pd.DataFrame(out)
    if len(table):
        dest.mkdir(parents=True, exist_ok=True)
        attr = dest / "ATTRIBUTION.csv"
        merged = table
        if attr.exists():  # keep credits of files from earlier calls into the same folder
            old = pd.read_csv(attr, dtype=str, keep_default_na=False)
            merged = pd.concat([old[~old["image_id"].isin(table["image_id"])], table.astype(str)])
        merged.to_csv(attr, index=False, encoding="utf-8", lineterminator="\n")
    return table


def _row(iid: str, r: pd.Series, path: Path, status: str, copy: str = "original") -> dict:
    return {
        "image_id": iid,
        "role": r["role"],
        "path": str(path),
        "status": status,
        "copy": copy,
        "title": r["title"],
        "source_page_url": r["source_page_url"],
        "license": r["license"],
        "credit_line": r["credit_line"],
    }
