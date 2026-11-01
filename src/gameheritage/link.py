"""Link games and sites to Wikidata, and fill their facts from it.

Linking only proposes candidates; a person confirms them in a review sheet.
Enrichment fills empty cells only, and records the source in ``facts_source``.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from .review import read_sheet, write_sheet
from .sources import wikidata
from .sources.http import HttpClient
from .tables import Tables, upsert

GAME_WORDS = ("video game", "game", "expansion", "downloadable content")


def _score_game(c: dict) -> int:
    d = c["description"].lower()
    return 2 if "video game" in d else (1 if any(w in d for w in GAME_WORDS) else 0)


def propose_game_links(client: HttpClient, tables: Tables, limit: int = 7) -> pd.DataFrame:
    """Top Wikidata candidates for every game without a QID."""
    rows = []
    todo = tables.games[tables.games["wikidata_qid"] == ""]
    for g in todo.to_dict("records"):
        cands = wikidata.search(client, g["title"], limit=limit)
        cands.sort(key=_score_game, reverse=True)
        best = cands[0] if cands and _score_game(cands[0]) > 0 else None
        rows.append(
            {
                "game_id": g["game_id"],
                "title": g["title"],
                "decision": "",
                "wikidata_qid": best["qid"] if best else "",
                "candidate_label": best["label"] if best else "",
                "candidate_description": best["description"] if best else "",
                "other_candidates": " | ".join(
                    f"{c['qid']} {c['label']} ({c['description']})"
                    for c in cands
                    if not best or c["qid"] != best["qid"]
                )[:500],
            }
        )
    return pd.DataFrame(rows)


def import_game_links(tables: Tables, path: str | Path) -> tuple[Tables, int]:
    df = read_sheet(path, ["game_id", "decision", "wikidata_qid"])
    rows = [
        {"game_id": r["game_id"], "wikidata_qid": r["wikidata_qid"]}
        for r in df.to_dict("records")
        if r["decision"] == "keep" and r["wikidata_qid"]
    ]
    tables.games = upsert(tables.games, rows, "game_id", overwrite=True)
    return tables, len(rows)


def enrich_games(client: HttpClient, tables: Tables) -> tuple[Tables, int]:
    """Fill release year, developer, publisher and Wikipedia link from Wikidata."""
    qids = [q for q in tables.games["wikidata_qid"] if q]
    facts = wikidata.game_facts(client, qids)
    today = date.today().isoformat()
    rows = []
    for g in tables.games.to_dict("records"):
        f = facts.get(g["wikidata_qid"])
        if not f:
            continue
        rows.append(
            {
                "game_id": g["game_id"],
                "release_year": f["release_year"],
                "developer": f["developer"],
                "publisher": f["publisher"],
                "wikipedia_url": f["wikipedia_url"],
                "facts_source": f"Wikidata {f['entity_url']} (retrieved {today})",
            }
        )
    tables.games = upsert(tables.games, rows, "game_id")
    return tables, len(rows)


def propose_site_links(
    client: HttpClient, tables: Tables, context: dict[str, str] | None = None, limit: int = 7
) -> pd.DataFrame:
    """Top Wikidata candidates for every site without a QID.

    ``context`` maps site_id to extra search words (a city or country) used when
    the bare name is ambiguous.
    """
    context = context or {}
    rows = []
    for s in tables.sites[tables.sites["wikidata_qid"] == ""].to_dict("records"):
        cands = wikidata.search(client, s["name"], limit=limit)
        extra = context.get(s["site_id"], "")
        if extra:
            first = wikidata.search(client, f"{s['name']} {extra}", limit=3)
            firsts = {c["qid"] for c in first}
            cands = first + [c for c in cands if c["qid"] not in firsts]
        best = cands[0] if cands else None
        rows.append(
            {
                "site_id": s["site_id"],
                "name": s["name"],
                "decision": "",
                "wikidata_qid": best["qid"] if best else "",
                "candidate_label": best["label"] if best else "",
                "candidate_description": best["description"] if best else "",
                "other_candidates": " | ".join(
                    f"{c['qid']} {c['label']} ({c['description']})" for c in cands[1:]
                )[:500],
            }
        )
    return pd.DataFrame(rows)


def import_site_links(tables: Tables, path: str | Path) -> tuple[Tables, int]:
    df = read_sheet(path, ["site_id", "decision", "wikidata_qid"])
    rows = [
        {"site_id": r["site_id"], "wikidata_qid": r["wikidata_qid"]}
        for r in df.to_dict("records")
        if r["decision"] == "keep" and r["wikidata_qid"]
    ]
    tables.sites = upsert(tables.sites, rows, "site_id", overwrite=True)
    return tables, len(rows)


def enrich_sites(client: HttpClient, tables: Tables) -> tuple[Tables, int]:
    """Fill coordinates, dates, country, style, heritage status and links from Wikidata."""
    qids = [q for q in tables.sites["wikidata_qid"] if q]
    facts = wikidata.site_facts(client, qids)
    today = date.today().isoformat()
    rows = []
    for s in tables.sites.to_dict("records"):
        f = facts.get(s["wikidata_qid"])
        if not f:
            continue
        rows.append(
            {
                "site_id": s["site_id"],
                "alt_names": f["aliases"],
                "country": f["country"],
                "locality": f["located_in"],
                "latitude": f["latitude"],
                "longitude": f["longitude"],
                "construction_date": f["inception_year"],
                "architectural_style": f["style"],
                "heritage_designation": f["heritage"],
                "commons_category": f["commons_category"],
                "wikipedia_url": f["wikipedia_url"],
                "facts_source": f"Wikidata {f['entity_url']} (retrieved {today})",
            }
        )
    tables.sites = upsert(tables.sites, rows, "site_id")
    return tables, len(rows)


def write_link_sheet(df: pd.DataFrame, path: str | Path) -> Path:
    return write_sheet(df, path)


def auto_link_games(client: HttpClient, tables: Tables) -> tuple[Tables, int]:
    """Give games without a QID their best Wikidata match (a video game item whose
    label matches the title). Machine choices are noted in ``facts_source``."""
    from .autolink import similarity

    todo = tables.games[tables.games["wikidata_qid"] == ""]
    if not len(todo):
        return tables, 0
    hits = wikidata.search_many(client, list(todo["title"]), limit=6)
    summ = wikidata.summaries(client, sorted({q for qs in hits.values() for q in qs}))
    rows = []
    for g in todo.to_dict("records"):
        best = None
        for q in hits.get(g["title"], []):
            s = summ.get(q, {})
            text = f"{s.get('description', '')} {s.get('instance_of', '')}".lower()
            if "video game" not in text and "expansion" not in text:
                continue
            if similarity(g["title"], s.get("label", "")) >= 0.8:
                best = q
                break
        if best:
            rows.append(
                {
                    "game_id": g["game_id"],
                    "wikidata_qid": best,
                    "facts_source": f"Wikidata QID matched automatically on {date.today()}",
                }
            )
    tables.games = upsert(tables.games, rows, "game_id")
    return tables, len(rows)
