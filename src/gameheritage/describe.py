"""Short site descriptions from Wikipedia, with source and license recorded."""

from __future__ import annotations

import logging
import re

from .sources import wikipedia
from .sources.http import HttpClient, HttpError
from .tables import Tables, upsert

log = logging.getLogger(__name__)


def first_sentences(text: str, n: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text.strip())
    return " ".join(parts[:n]).strip()


def describe_sites(client: HttpClient, tables: Tables, sentences: int = 3) -> tuple[Tables, int]:
    """Fill empty descriptions from each site's English Wikipedia article.

    Articles are read 20 at a time. Sites without an article get their short
    Wikidata description (CC0). Text that the team writes itself should go
    straight into ``description`` with ``description_source = "project team"``;
    it is never overwritten.
    """
    todo = [s for s in tables.sites.to_dict("records") if not s["description"]]
    titles = {
        s["site_id"]: s["wikipedia_url"].rsplit("/wiki/", 1)[-1] for s in todo if s["wikipedia_url"]
    }
    try:
        found = wikipedia.intros(client, list(titles.values()), sentences=sentences + 2)
    except HttpError as exc:
        log.warning("Wikipedia intros failed: %s", exc)
        found = {}
    from urllib.parse import unquote

    rows = []
    for s in todo:
        info = found.get(unquote(titles.get(s["site_id"], "")).replace("_", " "))
        if info:
            rows.append(
                {
                    "site_id": s["site_id"],
                    "description": first_sentences(info["extract"], sentences),
                    "description_source": info["page_url"] or s["wikipedia_url"],
                    "description_license": info["license"],
                }
            )
    have = {r["site_id"] for r in rows}
    rest = [s for s in todo if s["site_id"] not in have and s["wikidata_qid"]]
    if rest:
        from .sources import wikidata

        summ = wikidata.summaries(client, [s["wikidata_qid"] for s in rest])
        for s in rest:
            desc = (summ.get(s["wikidata_qid"]) or {}).get("description", "")
            if desc:
                rows.append(
                    {
                        "site_id": s["site_id"],
                        "description": desc[0].upper() + desc[1:] + ".",
                        "description_source": wikidata.ENTITY_URL.format(s["wikidata_qid"]),
                        "description_license": "CC0 1.0",
                    }
                )
    tables.sites = upsert(tables.sites, rows, "site_id")
    return tables, len(rows)


FANDOM_LICENSE = "CC BY-SA 3.0"
_DROP_TEMPLATE = re.compile(
    r"infobox|quote|image|gallery|stub|disambig|spoiler|^era|navbox|cite|ref|"
    r"^lang|main|see also|^for$|redirect|about|cleanup|update|icon"
)
_PAGE_IN_NOTES = re.compile(r"shown on page (https://[^;\s]+/wiki/([^;\s]+))")


def wikitext_lead(text: str, sentences: int = 3) -> str:
    """Plain text of a wiki page's lead section (before the first heading)."""
    import mwparserfromhell as mw

    lead = mw.parse(text).get_sections(include_lead=True, flat=True)[0]
    for node in list(lead.filter_templates(recursive=False)):
        name = str(node.name).strip().lower()
        pos = [str(p.value).strip() for p in node.params if not p.showkey]
        named = [p for p in node.params if p.showkey]
        if named or not pos or _DROP_TEMPLATE.search(name):
            lead.remove(node)  # infoboxes, quotes, notices
        else:  # link-like templates ({{W|Kii Province|Kii}}): keep the shown text
            shown = pos[1] if name.startswith("nihongo") and len(pos) > 1 else pos[-1]
            lead.replace(node, mw.parse(shown).strip_code())
    for node in list(lead.filter_tags(recursive=False)):
        if str(node.tag).lower() in {"ref", "gallery", "references", "table", "div"}:
            lead.remove(node)
    for link in list(lead.filter_wikilinks(recursive=False)):
        if re.match(r"(?i)\s*(file|image|category|media)\s*:", str(link.title)):
            lead.remove(link)
    plain = lead.strip_code(normalize=True, collapse=True, keep_template_params=False)
    plain = re.sub(r"\[\[|\]\]|'''?|__\w+__", "", plain)
    plain = re.sub(r"\(\s*[:;,][^)]*\)|\(\s*\)", "", plain)  # emptied "(lang: ...)" asides
    plain = re.sub(r"\s+([,.;:])", r"\1", plain)
    plain = re.sub(r"\s+", " ", plain).strip()
    return first_sentences(plain, sentences)


def describe_depictions(
    client: HttpClient, tables: Tables, sentences: int = 3, *, refresh: bool = False
) -> tuple[Tables, int]:
    """Fill ``in_game_description`` from the lead of each place's page on the
    game's Fandom wiki (CC BY-SA), 50 pages per request. ``refresh`` rewrites
    descriptions taken from Fandom earlier (never ones written by the team)."""
    if refresh:
        d = tables.depictions.copy()
        machine = d["in_game_description_license"] == FANDOM_LICENSE
        d.loc[machine, ["in_game_description", "in_game_description_source"]] = ""
        d.loc[machine, "in_game_description_license"] = ""
        tables.depictions = d
    from urllib.parse import unquote

    from .sources import fandom

    wiki_of = dict(zip(tables.games["game_id"], tables.games["fandom_wiki"]))
    pages: dict[str, str] = {}  # depiction_id -> exact page title from harvest notes
    for r in tables.images[tables.images["depiction_id"] != ""].to_dict("records"):
        m = _PAGE_IN_NOTES.search(r["notes"] or "")
        if m and r["depiction_id"] not in pages:
            pages[r["depiction_id"]] = unquote(m.group(2)).replace("_", " ")
    todo: dict[str, list[tuple[str, str]]] = {}  # wiki -> [(depiction_id, title)]
    for d in tables.depictions.to_dict("records"):
        wiki = wiki_of.get(d["game_id"], "")
        if d["in_game_description"] or not wiki or d["review_status"] == "drop":
            continue
        title = pages.get(d["depiction_id"]) or d["in_game_name"]
        todo.setdefault(wiki, []).append((d["depiction_id"], title))
    rows = []
    for wiki, items in todo.items():
        titles = list(dict.fromkeys(t for _, t in items))
        found: dict[str, tuple[str, str]] = {}
        for i in range(0, len(titles), 50):
            chunk = titles[i : i + 50]
            try:
                data = client.get_json(
                    f"{fandom.base_url(wiki)}/api.php",
                    {
                        "action": "query",
                        "format": "json",
                        "formatversion": "2",
                        "prop": "revisions",
                        "rvprop": "content",
                        "rvslots": "main",
                        "redirects": "1",
                        "titles": "|".join(chunk),
                    },
                )
            except HttpError as exc:
                log.warning("Fandom %s: %s", wiki, exc)
                continue
            q = data.get("query", {})
            back = {t: t for t in chunk}
            for step in ("normalized", "redirects"):
                for m in q.get(step, []):
                    for orig, cur in list(back.items()):
                        if cur == m["from"]:
                            back[orig] = m["to"]
            by_title = {}
            for p in q.get("pages", []):
                revs = p.get("revisions") or []
                if p.get("missing") or not revs:
                    continue
                by_title[p["title"]] = revs[0].get("slots", {}).get("main", {}).get("content", "")
            for orig, final in back.items():
                text = by_title.get(final, "")
                if not text or text.lstrip().lower().startswith("#redirect"):
                    continue
                try:
                    lead = wikitext_lead(text, sentences)
                except Exception:  # odd markup: skip this page
                    continue
                if len(lead) >= 40:
                    found[orig] = (lead, fandom.page_url(wiki, final))
        for dep_id, title in items:
            if title in found:
                lead, url = found[title]
                rows.append(
                    {
                        "depiction_id": dep_id,
                        "in_game_description": lead,
                        "in_game_description_source": url,
                        "in_game_description_license": FANDOM_LICENSE,
                    }
                )
    tables.depictions = upsert(tables.depictions, rows, "depiction_id")
    return tables, len(rows)
