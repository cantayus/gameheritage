"""Review sheets for human curation, and the HTML contact sheets that go with them.

The package writes CSV sheets (open them in Google Sheets or Excel), a person
fills the ``decision`` column, and ``import_*`` merges the edits back into the
tables after checking them. Every sheet also gets a local HTML contact sheet:
a grid of thumbnails with IDs, for reviewing many images at a glance.

Decisions: ``keep``, ``drop``, ``fix``, ``needs_info`` (blank = not reviewed yet).
"""

from __future__ import annotations

import html
import os
from pathlib import Path

import pandas as pd

from .clean import suggestion
from .names import slugify
from .tables import Tables, upsert

DECISIONS = {"", "keep", "drop", "fix", "needs_info"}


class ReviewError(ValueError):
    pass


def write_sheet(df: pd.DataFrame, path: str | Path) -> Path:
    """CSV with a BOM so Excel opens accented names correctly."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")
    return path


def read_sheet(path: str | Path, required: list[str]) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ReviewError(f"{path}: missing columns {missing}")
    bad = []
    for i, d in enumerate(df["decision"].str.strip().str.lower()):
        if d not in DECISIONS:
            bad.append(f"row {i + 2}: decision {d!r} (use keep, drop, fix, needs_info or blank)")
    if bad:
        raise ReviewError(f"{path}:\n  " + "\n  ".join(bad))
    df["decision"] = df["decision"].str.strip().str.lower()
    return df


# --------------------------------------------------------------------------- images


def export_image_triage(
    tables: Tables, manifest: pd.DataFrame, out_dir: str | Path, collection_root: str | Path
) -> tuple[Path, Path]:
    """Sheet + contact sheet of images still marked ``todo``."""
    out_dir = Path(out_dir)
    flags = (
        manifest.drop_duplicates("rel_path").set_index("rel_path")["flags"]
        if "flags" in manifest
        else pd.Series(dtype=str)
    )
    imgs = tables.images[tables.images["review_status"].isin(["todo", ""])].copy()
    imgs["flags"] = [
        ";".join(dict.fromkeys(filter(None, f"{a};{flags.get(p, '')}".split(";"))))
        for a, p in zip(imgs["flags"], imgs["drive_path"])
    ]
    sheet = pd.DataFrame(
        {
            "image_id": imgs["image_id"],
            "game_id": imgs["game_id"],
            "role": imgs["role"],
            "title": imgs["title"],
            "kind": imgs["kind"],
            "flags": imgs["flags"],
            "suggestion": [suggestion(f) for f in imgs["flags"]],
            "decision": "",
            "new_kind": "",
            "note": "",
            "drive_path": imgs["drive_path"],
        }
    ).sort_values(["game_id", "suggestion", "title"])
    csv = write_sheet(sheet, out_dir / "image_triage.csv")
    html_path = contact_sheet(
        sheet,
        out_dir / "image_triage.html",
        title="Image triage",
        src=lambda r: _rel(Path(collection_root) / r["drive_path"], out_dir),
        caption=lambda r: f"{r['image_id']} · {r['kind']} · {r['suggestion']}",
        sub=lambda r: r["title"] + (f" — {r['flags']}" if r["flags"] else ""),
        group="game_id",
    )
    return csv, html_path


def import_image_triage(tables: Tables, path: str | Path) -> tuple[Tables, int]:
    df = read_sheet(path, ["image_id", "decision", "new_kind", "note"])
    from .schema import ImageKind

    kinds = {k.value for k in ImageKind}
    bad = [k for k in df["new_kind"] if k and k not in kinds]
    if bad:
        raise ReviewError(f"{path}: unknown kinds {sorted(set(bad))}; use one of {sorted(kinds)}")
    rows = []
    for r in df.to_dict("records"):
        if not (r["decision"] or r["new_kind"] or r["note"]):
            continue
        row = {"image_id": r["image_id"]}
        if r["decision"]:
            row["review_status"] = r["decision"]
        if r["new_kind"]:
            row["kind"] = r["new_kind"]
        if r["note"]:
            row["notes"] = r["note"]
        rows.append(row)
    known = set(tables.images["image_id"])
    unknown = [r["image_id"] for r in rows if r["image_id"] not in known]
    if unknown:
        raise ReviewError(f"{path}: unknown image_id(s) {unknown[:5]}")
    tables.images = upsert(tables.images, rows, "image_id", overwrite=True)
    return tables, len(rows)


# --------------------------------------------------------------------------- places


def propose_places(tables: Tables, manifest: pd.DataFrame, min_images: int = 1) -> pd.DataFrame:
    """Group kept-or-todo game images by (game, in-game place name)."""
    imgs = tables.images[
        (tables.images["role"] == "game") & ~tables.images["review_status"].isin(["drop"])
    ]
    places = manifest.drop_duplicates("rel_path").set_index("rel_path")
    rows = []
    for r in imgs.to_dict("records"):
        if r["drive_path"] not in places.index:
            continue
        p = places.loc[r["drive_path"]]
        place = p["place_hint"] or p["subfolder_hint"]
        if not place or "icon_like" in p.get("flags", ""):
            continue
        rows.append({"game_id": r["game_id"], "place": place, "image_id": r["image_id"]})
    if not rows:
        return pd.DataFrame(
            columns=["game_id", "place", "n_images", "example_image_id", "image_ids"]
        )
    df = pd.DataFrame(rows)
    df["key"] = df["place"].str.lower()
    out = (
        df.groupby(["game_id", "key"])
        .agg(
            place=("place", "first"),
            n_images=("image_id", "size"),
            example_image_id=("image_id", "first"),
            image_ids=("image_id", lambda s: ";".join(s)),
        )
        .reset_index()
        .drop(columns="key")
    )
    out = out[out["n_images"] >= min_images]
    return out.sort_values(["game_id", "n_images"], ascending=[True, False])


def export_places(
    tables: Tables, manifest: pd.DataFrame, out_dir: str | Path, collection_root: str | Path
) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    places = propose_places(tables, manifest)
    done = set(zip(tables.depictions["game_id"], tables.depictions["in_game_name"].str.lower()))
    places = places[
        [(g, p.lower()) not in done for g, p in zip(places["game_id"], places["place"])]
    ]
    img = tables.images.set_index("image_id")
    sheet = places.assign(
        decision="",
        site_name="",
        wikidata_qid="",
        depiction_class="",
        note="",
        example_path=[img.at[i, "drive_path"] for i in places["example_image_id"]],
    )[
        [
            "game_id", "place", "n_images", "decision", "site_name", "wikidata_qid",
            "depiction_class", "note", "example_image_id", "example_path", "image_ids",
        ]
    ]  # fmt: skip
    csv = write_sheet(sheet, out_dir / "places.csv")
    html_path = contact_sheet(
        sheet,
        out_dir / "places.html",
        title="In-game places",
        src=lambda r: _rel(Path(collection_root) / r["example_path"], out_dir),
        caption=lambda r: f"{r['place']} ({r['n_images']})",
        sub=lambda r: r["game_id"],
        group="game_id",
    )
    return csv, html_path


def import_places(tables: Tables, path: str | Path) -> tuple[Tables, int]:
    """Create sites and depictions for kept places, and link their images."""
    req = ["game_id", "place", "decision", "site_name", "wikidata_qid", "depiction_class"]
    df = read_sheet(path, [*req, "image_ids"])
    sites, deps, img_rows = [], [], []
    taken = dict(zip(tables.sites["site_id"], tables.sites["wikidata_qid"]))
    by_qid = {q: sid for sid, q in taken.items() if q}
    for r in df[df["decision"] == "keep"].to_dict("records"):
        cls = r["depiction_class"].strip()
        if cls not in ("", "1", "2"):
            raise ReviewError(f"{path}: depiction_class must be 1, 2 or blank, got {cls!r}")
        name = r["site_name"].strip()
        qid = r["wikidata_qid"].strip()
        site_id = ""
        if qid and qid in by_qid:  # the same real place is always the same site
            site_id = by_qid[qid]
        elif name:
            site_id = slugify(name)
            if taken.get(site_id, qid) != qid:  # same name, different place
                site_id = f"{site_id}-{(qid or 'x').lower()}"
            taken[site_id] = qid
            if qid:
                by_qid[qid] = site_id
            sites.append({"site_id": site_id, "name": name, "wikidata_qid": qid})
        dep_id = f"{r['game_id']}--{slugify(r['place'])[:60]}"
        deps.append(
            {
                "depiction_id": dep_id,
                "game_id": r["game_id"],
                "site_id": site_id,
                "in_game_name": r["place"],
                "depiction_class": cls,
                "notes": r.get("note", ""),
            }
        )
        for iid in filter(None, r["image_ids"].split(";")):
            img_rows.append({"image_id": iid, "depiction_id": dep_id, "site_id": site_id})
    tables.sites = upsert(tables.sites, sites, "site_id")
    tables.depictions = upsert(tables.depictions, deps, "depiction_id")
    tables.images = upsert(tables.images, img_rows, "image_id")
    return tables, len(deps)


# --------------------------------------------------------------------------- generic


def import_decisions(
    tables: Tables, path: str | Path, table: str, key: str, extra: dict[str, str] | None = None
) -> tuple[Tables, int]:
    """Apply a sheet's ``decision`` (and mapped extra columns) to ``table`` by ``key``."""
    extra = extra or {}
    df = read_sheet(path, [key, "decision", *extra])
    rows = []
    for r in df.to_dict("records"):
        row = {key: r[key]}
        if r["decision"]:
            row["review_status"] = r["decision"]
        for sheet_col, table_col in extra.items():
            if r.get(sheet_col, ""):
                row[table_col] = r[sheet_col]
        if len(row) > 1:
            rows.append(row)
    tables[table] = upsert(tables[table], rows, key, overwrite=True)
    return tables, len(rows)


def _rel(target: Path, start: Path) -> str:
    try:
        return Path(os.path.relpath(target, start)).as_posix()
    except ValueError:  # different drive on Windows
        return target.resolve().as_uri()


_CSS = """
body{font:14px/1.4 system-ui,sans-serif;margin:16px;background:#f7f6f3;color:#222}
h1{font-size:20px} h2{font-size:16px;margin:24px 0 8px;border-bottom:1px solid #ccc}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}
figure{margin:0;background:#fff;border:1px solid #ddd;border-radius:6px;padding:6px}
img{width:100%;height:140px;object-fit:contain;background:#eee}
figcaption{font-size:12px;word-break:break-word} .sub{color:#666}
"""


def contact_sheet(df, path, *, title, src, caption, sub=None, group=None) -> Path:
    """Static HTML grid of thumbnails. ``src``/``caption``/``sub`` take a row dict."""
    path = Path(path)
    parts = [
        f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title>",
        f"<style>{_CSS}</style><h1>{html.escape(title)} ({len(df)})</h1>",
    ]
    groups = df.groupby(group, sort=False) if group else [("", df)]
    for gname, grp in groups:
        if group:
            parts.append(f"<h2>{html.escape(str(gname))} ({len(grp)})</h2>")
        parts.append("<div class='grid'>")
        for r in grp.to_dict("records"):
            s = html.escape(src(r), quote=True)
            parts.append(
                f"<figure><a href='{s}'><img loading='lazy' src='{s}' alt=''></a>"
                f"<figcaption><b>{html.escape(caption(r))}</b>"
                + (f"<div class='sub'>{html.escape(sub(r))}</div>" if sub else "")
                + "</figcaption></figure>"
            )
        parts.append("</div>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts), "utf-8")
    return path
