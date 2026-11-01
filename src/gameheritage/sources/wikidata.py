"""Wikidata: entity search, facts about sites and games, and SPARQL.

Facts are read from these properties:

=================  ======  =================================================
field              prop    meaning
=================  ======  =================================================
image              P18     representative image on Commons
commons_category   P373    Commons category with more images
coordinates        P625    latitude and longitude
inception          P571    construction or founding date
country            P17     country
located_in         P131    administrative location
style              P149    architectural style
heritage           P1435   heritage designation (e.g. UNESCO World Heritage)
instance_of        P31     what kind of thing it is
present_in_work    P1441   works (including games) that feature it
publication_date   P577    a game's release date
developer          P178    a game's developer
publisher          P123    a game's publisher
narrative_location P840    where a game is set
=================  ======  =================================================
"""

from __future__ import annotations

import re
from urllib.parse import unquote

from .http import HttpClient

API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
ENTITY_URL = "https://www.wikidata.org/wiki/{}"

PROPS = {
    "image": "P18",
    "commons_category": "P373",
    "coordinates": "P625",
    "inception": "P571",
    "country": "P17",
    "located_in": "P131",
    "style": "P149",
    "heritage": "P1435",
    "instance_of": "P31",
    "present_in_work": "P1441",
    "publication_date": "P577",
    "developer": "P178",
    "publisher": "P123",
    "narrative_location": "P840",
}


def search(client: HttpClient, text: str, *, limit: int = 7, language: str = "en") -> list[dict]:
    """Entity search: [{qid, label, description}] ordered by Wikidata's relevance."""
    data = client.get_json(
        API,
        {
            "action": "wbsearchentities",
            "search": text,
            "language": language,
            "uselang": language,
            "type": "item",
            "limit": str(limit),
            "format": "json",
            "maxlag": "5",
        },
    )
    return [
        {"qid": r["id"], "label": r.get("label", ""), "description": r.get("description", "")}
        for r in data.get("search", [])
    ]


def get_entities(client: HttpClient, qids: list[str], *, language: str = "en") -> dict[str, dict]:
    """Raw entities (labels, descriptions, claims, sitelinks), 50 per request."""
    out: dict[str, dict] = {}
    qids = [q for q in dict.fromkeys(qids) if q]
    for i in range(0, len(qids), 50):
        data = client.get_json(
            API,
            {
                "action": "wbgetentities",
                "ids": "|".join(qids[i : i + 50]),
                "props": "labels|descriptions|claims|sitelinks|aliases",
                "languages": language,
                "sitefilter": f"{language}wiki",
                "format": "json",
                "maxlag": "5",
            },
        )
        out.update(data.get("entities", {}))
    return out


def _values(entity: dict, prop: str) -> list:
    vals = []
    for claim in entity.get("claims", {}).get(prop, []):
        if claim.get("rank") == "deprecated":
            continue
        snak = claim.get("mainsnak", {})
        if snak.get("snaktype") == "value":
            vals.append(snak["datavalue"]["value"])
    return vals


def _first(entity: dict, prop: str):
    vals = _values(entity, prop)
    return vals[0] if vals else None


def year_from_time(value: dict | None) -> int | None:
    """``{"time": "+1889-03-31T00:00:00Z"}`` -> 1889; BCE dates are negative."""
    if not value or "time" not in value:
        return None
    t = value["time"]
    sign = -1 if t.startswith("-") else 1
    try:
        return sign * int(t[1:].split("-", 1)[0])
    except ValueError:
        return None


def label_of(entity: dict, language: str = "en") -> str:
    return entity.get("labels", {}).get(language, {}).get("value", "")


def _qids(entity: dict, prop: str) -> list[str]:
    return [v["id"] for v in _values(entity, prop) if isinstance(v, dict) and "id" in v]


def labels(client: HttpClient, qids: list[str], language: str = "en") -> dict[str, str]:
    ents = get_entities(client, qids, language=language)
    return {q: label_of(e, language) for q, e in ents.items()}


def site_facts(client: HttpClient, qids: list[str], language: str = "en") -> dict[str, dict]:
    """Facts for heritage sites, keyed by QID, with linked labels resolved."""
    ents = get_entities(client, qids, language=language)
    linked: set[str] = set()
    for e in ents.values():
        for p in ("country", "located_in", "style", "heritage", "instance_of"):
            linked.update(_qids(e, PROPS[p]))
    names = labels(client, sorted(linked), language) if linked else {}
    out = {}
    for qid, e in ents.items():
        coord = _first(e, PROPS["coordinates"]) or {}
        site = e.get("sitelinks", {}).get(f"{language}wiki", {})
        title = site.get("title", "")

        def names_for(prop: str, entity: dict = e) -> str:
            return "; ".join(names.get(q, q) for q in _qids(entity, PROPS[prop]))

        out[qid] = {
            "qid": qid,
            "label": label_of(e, language),
            "description": e.get("descriptions", {}).get(language, {}).get("value", ""),
            "aliases": "; ".join(a["value"] for a in e.get("aliases", {}).get(language, [])[:5]),
            "image": _first(e, PROPS["image"]) or "",
            "commons_category": _first(e, PROPS["commons_category"]) or "",
            "latitude": coord.get("latitude"),
            "longitude": coord.get("longitude"),
            "inception_year": year_from_time(_first(e, PROPS["inception"])),
            "country": names_for("country"),
            "located_in": names_for("located_in"),
            "style": names_for("style"),
            "heritage": names_for("heritage"),
            "instance_of": names_for("instance_of"),
            "wikipedia_title": title,
            "wikipedia_url": (
                f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}" if title else ""
            ),
            "entity_url": ENTITY_URL.format(qid),
        }
    return out


def game_facts(client: HttpClient, qids: list[str], language: str = "en") -> dict[str, dict]:
    """Release year, developers and publishers for games, keyed by QID."""
    ents = get_entities(client, qids, language=language)
    linked: set[str] = set()
    for e in ents.values():
        linked.update(_qids(e, PROPS["developer"]) + _qids(e, PROPS["publisher"]))
    names = labels(client, sorted(linked), language) if linked else {}
    out = {}
    for qid, e in ents.items():
        years = [year_from_time(v) for v in _values(e, PROPS["publication_date"])]
        years = [y for y in years if y is not None]
        site = e.get("sitelinks", {}).get(f"{language}wiki", {})
        title = site.get("title", "")
        out[qid] = {
            "qid": qid,
            "label": label_of(e, language),
            "description": e.get("descriptions", {}).get(language, {}).get("value", ""),
            "release_year": min(years) if years else None,
            "developer": "; ".join(names.get(q, q) for q in _qids(e, PROPS["developer"])),
            "publisher": "; ".join(names.get(q, q) for q in _qids(e, PROPS["publisher"])),
            "wikipedia_url": (
                f"https://{language}.wikipedia.org/wiki/{title.replace(' ', '_')}" if title else ""
            ),
            "entity_url": ENTITY_URL.format(qid),
        }
    return out


def sparql(client: HttpClient, query: str) -> list[dict]:
    """Run a SPARQL query; returns rows as {var: value}."""
    data = client.get_json(
        SPARQL,
        {"query": query, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
    )
    rows = []
    for b in data.get("results", {}).get("bindings", []):
        rows.append({k: v.get("value", "") for k, v in b.items()})
    return rows


def features_in_work(client: HttpClient, game_qid: str) -> list[dict]:
    """Items that Wikidata says are present in a game (P1441), with label and image."""
    q = f"""
    SELECT ?item ?itemLabel ?image WHERE {{
      ?item wdt:P1441 wd:{game_qid} .
      OPTIONAL {{ ?item wdt:P18 ?image }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}"""
    rows = sparql(client, q)
    return [
        {
            "qid": r["item"].rsplit("/", 1)[-1],
            "label": r.get("itemLabel", ""),
            "image": r.get("image", ""),
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- SPARQL batch helpers
# The query service (query.wikidata.org) is used for bulk work because it batches
# many lookups into one request; the MediaWiki API is rate-limited per IP.


def _lit(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def search_many(
    client: HttpClient, terms: list[str], *, limit: int = 5, batch: int = 15, language: str = "en"
) -> dict[str, list[str]]:
    """Wikidata entity search for many terms at once (via the query service).

    Returns term -> QIDs in Wikidata's relevance order.
    """
    out: dict[str, list[str]] = {}
    terms = [t for t in dict.fromkeys(terms) if t.strip()]
    for i in range(0, len(terms), batch):
        chunk = terms[i : i + batch]
        values = " ".join(_lit(t) for t in chunk)
        q = f"""SELECT ?term ?item ?ordinal WHERE {{
          VALUES ?term {{ {values} }}
          SERVICE wikibase:mwapi {{
            bd:serviceParam wikibase:endpoint "www.wikidata.org"; wikibase:api "EntitySearch";
                            mwapi:search ?term; mwapi:language "{language}".
            ?item wikibase:apiOutputItem mwapi:item.
            ?ordinal wikibase:apiOrdinal true.
          }}
          FILTER(?ordinal < {limit})
        }}"""
        rows = sparql(client, q)
        found: dict[str, list[tuple[int, str]]] = {t: [] for t in chunk}
        for r in rows:
            found.setdefault(r["term"], []).append(
                (int(r["ordinal"]), r["item"].rsplit("/", 1)[-1])
            )
        for t, items in found.items():
            out[t] = [q for _, q in sorted(items)]
    return out


def summaries(
    client: HttpClient, qids: list[str], *, batch: int = 40, language: str = "en"
) -> dict[str, dict]:
    """Label, description, type, location, image and links for many items (query service).

    Two light queries per batch (single values, then multi-valued labels), so that
    items with hundreds of statements (big cities) do not time out.
    """
    out: dict[str, dict] = {}
    qids = [q for q in dict.fromkeys(qids) if q]
    for i in range(0, len(qids), batch):
        chunk = qids[i : i + batch]
        values = " ".join(f"wd:{q}" for q in chunk)
        q1 = f"""SELECT ?item ?itemLabel ?itemDescription ?links (SAMPLE(?image) AS ?img)
                       (SAMPLE(?coord) AS ?co) (SAMPLE(?cat) AS ?ca) (MIN(?inception) AS ?inc)
                       (SAMPLE(?article) AS ?art) WHERE {{
          VALUES ?item {{ {values} }}
          OPTIONAL {{ ?item wikibase:sitelinks ?links }}
          OPTIONAL {{ ?item wdt:P18 ?image }}
          OPTIONAL {{ ?item wdt:P625 ?coord }}
          OPTIONAL {{ ?item wdt:P373 ?cat }}
          OPTIONAL {{ ?item wdt:P571 ?inception }}
          OPTIONAL {{ ?article schema:about ?item;
                               schema:isPartOf <https://{language}.wikipedia.org/> }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "{language},en,mul". }}
        }} GROUP BY ?item ?itemLabel ?itemDescription ?links"""
        for r in sparql(client, q1):
            qid = r["item"].rsplit("/", 1)[-1]
            d = {
                "qid": qid,
                "label": r.get("itemLabel", ""),
                "description": r.get("itemDescription", ""),
                "image": unquote(r["img"].rsplit("/", 1)[-1]) if r.get("img") else "",
                "latitude": None,
                "longitude": None,
                "commons_category": r.get("ca", ""),
                "inception_year": None,
                "country": "",
                "instance_of": "",
                "located_in": "",
                "style": "",
                "heritage": "",
                "wikipedia_url": unquote(r["art"]) if r.get("art") else "",
                "sitelinks": int(r.get("links") or 0),
                "entity_url": ENTITY_URL.format(qid),
            }
            co = r.get("co", "")
            if co and not co.startswith("<"):  # Earth coordinates only
                m = re.search(r"Point\(([-\d.eE]+) ([-\d.eE]+)\)", co)
                if m:
                    d["latitude"], d["longitude"] = float(m.group(2)), float(m.group(1))
            inc = r.get("inc", "")
            if inc:
                d["inception_year"] = year_from_time(
                    {"time": inc if inc.startswith(("-", "+")) else "+" + inc}
                )
            out[qid] = d
        q2 = f"""SELECT ?item ?p (GROUP_CONCAT(DISTINCT ?vl; separator="; ") AS ?vals) WHERE {{
          VALUES ?item {{ {values} }}
          VALUES (?p ?prop) {{ ("country" wdt:P17) ("instance_of" wdt:P31)
                               ("located_in" wdt:P131) ("style" wdt:P149) ("heritage" wdt:P1435) }}
          ?item ?prop ?v . ?v rdfs:label ?vl . FILTER(LANG(?vl) = "{language}")
        }} GROUP BY ?item ?p"""
        for r in sparql(client, q2):
            qid = r["item"].rsplit("/", 1)[-1]
            if qid in out:
                out[qid][r["p"]] = r.get("vals", "")
    return out


def places_in_works(client: HttpClient, work_qids: list[str]) -> dict[str, set[str]]:
    """Real places Wikidata ties to each game: items "present in work" (P1441) of
    the game, plus the game's "narrative location" (P840). Returns game QID -> QIDs."""
    out: dict[str, set[str]] = {q: set() for q in work_qids}
    for i in range(0, len(work_qids), 40):
        values = " ".join(f"wd:{q}" for q in work_qids[i : i + 40])
        q = f"""SELECT ?work ?item WHERE {{
          VALUES ?work {{ {values} }}
          {{ ?item wdt:P1441 ?work . }} UNION {{ ?work wdt:P840 ?item . }}
          ?item wdt:P625 ?coord .
        }}"""
        for r in sparql(client, q):
            out.setdefault(r["work"].rsplit("/", 1)[-1], set()).add(r["item"].rsplit("/", 1)[-1])
    return out


# Image properties on Wikidata items, best first.
IMAGE_PROPS = {
    "P18": "image",
    "P4291": "panoramic view",
    "P8592": "aerial view",
    "P3451": "nighttime view",
    "P5775": "interior image",
    "P5252": "winter view",
}


def item_images(client: HttpClient, qids: list[str], *, batch: int = 50) -> dict[str, list]:
    """All Commons images Wikidata lists for each item: QID -> [(file name, property label)]."""
    out: dict[str, list] = {q: [] for q in qids}
    props = " ".join(f"(wdt:{p} {_lit(lab)})" for p, lab in IMAGE_PROPS.items())
    for i in range(0, len(qids), batch):
        values = " ".join(f"wd:{q}" for q in qids[i : i + batch])
        q = f"""SELECT ?item ?img ?lab WHERE {{
          VALUES ?item {{ {values} }}
          VALUES (?prop ?lab) {{ {props} }}
          ?item ?prop ?img .
        }}"""
        for r in sparql(client, q):
            qid = r["item"].rsplit("/", 1)[-1]
            name = unquote(r["img"].rsplit("/", 1)[-1])
            if all(name != n for n, _ in out.setdefault(qid, [])):
                out[qid].append((name, r["lab"]))
    order = list(IMAGE_PROPS.values())
    for qid in out:
        out[qid].sort(key=lambda x: order.index(x[1]) if x[1] in order else 99)
    return out


DATE_PROPS = {
    "P571": "inception",
    "P1619": "official opening",
    "P580": "start time",
    "P729": "service entry",
    "P1249": "earliest written record",
    "P575": "discovered",
}


def dates(client: HttpClient, qids: list[str], batch: int = 150) -> dict[str, dict]:
    """Earliest known date of each item (inception, opening, start, first record)
    and its historical period labels (P2348). Returns qid -> {"year", "prop",
    "periods"}; ``year`` is negative for BCE."""
    out: dict[str, dict] = {}
    props = " ".join(f"wdt:{p}" for p in DATE_PROPS)
    for i in range(0, len(qids), batch):
        values = " ".join(f"wd:{q}" for q in qids[i : i + batch] if q)
        rows = sparql(
            client,
            f"""SELECT ?item ?p ?t ?periodLabel WHERE {{
              VALUES ?item {{ {values} }}
              OPTIONAL {{ VALUES ?p {{ {props} }} ?item ?p ?t . }}
              OPTIONAL {{ ?item wdt:P2348 ?period . }}
              SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
            }}""",
        )
        for r in rows:
            q = r["item"].rsplit("/", 1)[-1]
            d = out.setdefault(q, {"year": None, "prop": "", "periods": []})
            if r.get("t"):
                t = r["t"]
                y = year_from_time({"time": t if t.startswith(("+", "-")) else "+" + t})
                if y is not None and (d["year"] is None or y < d["year"]):
                    d["year"], d["prop"] = y, DATE_PROPS.get(r["p"].rsplit("/", 1)[-1], "")
            label = r.get("periodLabel", "")
            if label and not label.startswith("Q") and label not in d["periods"]:
                d["periods"].append(label)
    return out
