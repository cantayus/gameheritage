"""Attribution and references for every image: how and where it was obtained.

Outputs, all generated from the ``images`` table:

* ``credits.csv``: one row per image with a ready-to-print credit line.
* ``CREDITS.md``: human-readable credits grouped by game and by site.
* ``image_references.bib``: one BibTeX ``@misc`` entry per image.
* ``image_references.json``: CSL-JSON (imports into Zotero, Mendeley, Pandoc).

Real images follow the TASL rule (Title, Author, Source, License). Game images
credit the game and its developer/publisher, name the page the file came from
and say who collected it, how and when.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from .tables import Tables

KIND_LABEL = {
    "screenshot": "Screenshot",
    "concept_art": "Concept art",
    "database_entry": "In-game database image",
    "map": "Map",
    "render": "Render",
    "icon": "Icon",
    "ui": "Interface image",
    "portrait": "Portrait",
    "promo_art": "Promotional art",
    "modern_photo": "Photograph",
    "historical_photo": "Historical photograph",
    "painting_or_engraving": "Painting or engraving",
    "unknown": "Image",
}


def _s(x) -> str:
    return "" if x is None or (isinstance(x, float) and pd.isna(x)) else str(x).strip()


def _year(date: str) -> str:
    m = re.match(r"(\d{4})", _s(date))
    return m.group(1) if m else ""


def credit_line(img: dict, game: dict | None = None) -> str:
    """One-sentence attribution for an image row (dict of ``images`` columns)."""
    title = _s(img.get("title")) or _s(img.get("original_filename")) or _s(img.get("image_id"))
    author = _s(img.get("author"))
    source = _s(img.get("source_name"))
    page = _s(img.get("source_page_url"))
    lic = _s(img.get("license"))
    lic_url = _s(img.get("license_url"))
    retrieved = _s(img.get("retrieved_at"))[:10]
    if img.get("role") == "real":
        line = f'"{title}"' + (f" by {author}" if author else "")
        if lic:
            line += f", licensed {lic}" + (f" ({lic_url})" if lic_url else "")
        if source:
            line += f", via {source}"
        if page:
            line += f": {page}"
        if retrieved:
            line += f" (retrieved {retrieved})"
        return line + "."
    game = game or {}
    gtitle = _s(game.get("title")) or _s(img.get("game_id"))
    year = _s(game.get("release_year"))
    makers = "; ".join(x for x in (_s(game.get("developer")), _s(game.get("publisher"))) if x)
    kind = KIND_LABEL.get(_s(img.get("kind")) or "unknown", "Image")
    line = f'{kind} "{title}" from {gtitle}' + (f" ({year})" if year else "")
    if makers:
        line += f", © {makers}"
    line += "."
    if page:
        line += f" Source: {source + ', ' if source else ''}{page}."
    elif source:
        line += f" Source: {source}."
    who = _s(img.get("collected_by"))
    if who or retrieved:
        line += (
            " Collected" + (f" by {who}" if who else "") + (f" on {retrieved}" if retrieved else "")
        )
        line += "."
    line += " Copyrighted game asset, reproduced for research and commentary."
    return line


def _game_lookup(tables: Tables) -> dict[str, dict]:
    if not len(tables.games):
        return {}
    return {r["game_id"]: r for r in tables.games.to_dict("records")}


def credits_table(tables: Tables, image_ids: list[str] | None = None) -> pd.DataFrame:
    games = _game_lookup(tables)
    imgs = tables.images
    if image_ids is not None:
        imgs = imgs[imgs["image_id"].isin(image_ids)]
    rows = []
    for r in imgs.to_dict("records"):
        rows.append(
            {
                "image_id": r["image_id"],
                "role": r["role"],
                "game_id": r["game_id"],
                "site_id": r["site_id"],
                "title": r["title"],
                "author": r["author"],
                "source_name": r["source_name"],
                "source_page_url": r["source_page_url"],
                "file_url": r["file_url"],
                "license": r["license"],
                "license_url": r["license_url"],
                "rights_status": r["rights_status"],
                "collected_by": r["collected_by"],
                "collection_method": r["collection_method"],
                "retrieved_at": r["retrieved_at"],
                "credit_line": credit_line(r, games.get(r["game_id"])),
            }
        )
    return pd.DataFrame(rows)


def _bib_escape(s: str) -> str:
    return _s(s).replace("{", "\\{").replace("}", "\\}").replace("&", "\\&").replace("%", "\\%")


def bibtex(tables: Tables, image_ids: list[str] | None = None) -> str:
    games = _game_lookup(tables)
    entries = []
    for r in credits_table(tables, image_ids).to_dict("records"):
        g = games.get(r["game_id"], {})
        author = r["author"] if r["role"] == "real" else (g.get("developer") or g.get("title", ""))
        fields = {
            "title": r["title"] or r["image_id"],
            "author": author,
            "year": _year(r["retrieved_at"]) if r["role"] == "game" else "",
            "howpublished": f"\\url{{{r['source_page_url']}}}" if r["source_page_url"] else "",
            "note": "; ".join(
                x
                for x in (
                    r["source_name"],
                    f"License: {r['license']}" if r["license"] else "",
                    f"Accessed {r['retrieved_at'][:10]}" if r["retrieved_at"] else "",
                    r["collection_method"] if r["role"] == "game" else "",
                )
                if x
            ),
        }
        body = ",\n".join(
            f"  {k} = {{{v if k == 'howpublished' else _bib_escape(v)}}}"
            for k, v in fields.items()
            if v
        )
        entries.append(f"@misc{{{r['image_id']},\n{body}\n}}")
    return "\n\n".join(entries) + "\n"


def csl_json(tables: Tables, image_ids: list[str] | None = None) -> list[dict]:
    games = _game_lookup(tables)
    items = []
    for r in credits_table(tables, image_ids).to_dict("records"):
        g = games.get(r["game_id"], {})
        author = r["author"] if r["role"] == "real" else (g.get("developer") or "")
        item = {
            "id": r["image_id"],
            "type": "graphic",
            "title": r["title"] or r["image_id"],
            "URL": r["source_page_url"] or r["file_url"],
            "publisher": r["source_name"],
            "note": r["credit_line"],
        }
        if author:
            item["author"] = [{"literal": author}]
        if r["license"]:
            item["license"] = r["license"]
        m = re.match(r"(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?", r["retrieved_at"])
        if m:  # ISO dates only; anything else is left out rather than guessed
            item["accessed"] = {"date-parts": [[int(x) for x in m.groups() if x]]}
        items.append(item)
    return items


def write_credits(
    tables: Tables, out_dir: str | Path, image_ids: list[str] | None = None
) -> list[Path]:
    """Write credits.csv, CREDITS.md, image_references.bib and image_references.json."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    df = credits_table(tables, image_ids)
    paths = [out / "credits.csv", out / "CREDITS.md", out / "image_references.bib"]
    df.to_csv(paths[0], index=False, encoding="utf-8", lineterminator="\n")
    games = _game_lookup(tables)
    sites = {r["site_id"]: r for r in tables.sites.to_dict("records")}
    lines = [
        "# Image credits",
        "",
        "Every image in this release, with where it came from, who collected it and under",
        "what terms. Real-world photographs carry free licenses. Game images are",
        "copyrighted by their developers and publishers and appear for research and",
        "commentary.",
        "",
    ]
    if len(df):
        real = df[df["role"] == "real"]
        if len(real):
            lines += ["## Real-world images", ""]
            for site_id, grp in real.groupby(real["site_id"].replace("", "unassigned")):
                name = sites.get(site_id, {}).get("name", site_id)
                lines += [f"### {name}", ""] + [f"- {c}" for c in grp["credit_line"]] + [""]
        game = df[df["role"] == "game"]
        if len(game):
            lines += ["## Game images", ""]
            for game_id, grp in game.groupby("game_id"):
                name = games.get(game_id, {}).get("title", game_id) or "Unknown game"
                lines += [f"### {name}", ""] + [f"- {c}" for c in grp["credit_line"]] + [""]
    text_src = tables.sites[tables.sites["description_source"] != ""]
    if len(text_src):
        lines += ["## Text sources", ""]
        for r in text_src.to_dict("records"):
            lic = f", {r['description_license']}" if r["description_license"] else ""
            lines.append(f"- {r['name']}: {r['description_source']}{lic}")
        lines.append("")
    paths[1].write_text("\n".join(lines), "utf-8")
    paths[2].write_text(bibtex(tables, image_ids), "utf-8")
    p4 = out / "image_references.json"
    p4.write_text(json.dumps(csl_json(tables, image_ids), indent=1, ensure_ascii=False), "utf-8")
    return [*paths, p4]
