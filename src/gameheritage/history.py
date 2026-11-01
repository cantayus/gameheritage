"""The historical dimension: dates, centuries and eras of the real places.

``enrich_dates`` fills ``construction_date`` (the earliest of inception, opening,
start or first written record on Wikidata) and ``historical_period`` (Wikidata's
"time period", e.g. "Neolithic"). ``era_of`` and ``century_of`` turn a year into
the labels the website browses by.
"""

from __future__ import annotations

import re

from .sources import wikidata
from .sources.http import HttpClient
from .tables import Tables, upsert

# (label, first year, last year); years are negative for BCE.
ERAS = [
    ("Prehistory", -100000, -3001),
    ("Ancient world", -3000, 499),
    ("Middle Ages", 500, 1499),
    ("Early modern", 1500, 1799),
    ("19th century", 1800, 1899),
    ("20th century and later", 1900, 2100),
]
NATURAL = "Natural formations"
UNDATED = "Date not recorded"


def parse_year(value: str) -> int | None:
    v = (value or "").strip()
    if not v:
        return None
    neg = v.startswith("-")
    digits = v.lstrip("+-").split("-", 1)[0]
    return (-1 if neg else 1) * int(digits) if digits.isdigit() else None


def era_of(year: int | None, natural: bool = False) -> str:
    if natural:
        return NATURAL
    if year is None:
        return UNDATED
    for label, start, end in ERAS:
        if start <= year <= end:
            return label
    return ERAS[-1][0] if year > 0 else ERAS[0][0]


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def century_of(year: int | None) -> str:
    """1163 -> "12th century"; -2560 -> "26th century BCE"; -6999 -> "about 7,000 BCE"."""
    if year is None:
        return ""
    if year < -3000:
        return f"about {round(abs(year), -2):,} BCE"
    if year <= 0:
        return f"{_ordinal((abs(year) - 1) // 100 + 1)} century BCE"
    return f"{_ordinal((year - 1) // 100 + 1)} century"


def year_text(year: int | None) -> str:
    if year is None:
        return ""
    return f"{abs(year)} BCE" if year < 0 else str(year)


def enrich_dates(client: HttpClient, tables: Tables) -> tuple[Tables, int]:
    """Fill missing construction dates and historical periods from Wikidata."""
    sites = tables.sites
    todo = sites[(sites["wikidata_qid"] != "")]
    found = wikidata.dates(client, list(todo["wikidata_qid"]))
    rows = []
    for s in todo.to_dict("records"):
        d = found.get(s["wikidata_qid"])
        if not d:
            continue
        row = {"site_id": s["site_id"]}
        if not s["construction_date"] and d["year"] is not None:
            row["construction_date"] = str(d["year"])
            row["construction_date_basis"] = f"Wikidata, {d['prop']}"
        if d["periods"] and not s["historical_period"]:
            row["historical_period"] = "; ".join(d["periods"][:3])
        if len(row) > 1:
            rows.append(row)
    tables.sites = upsert(tables.sites, rows, "site_id")
    return tables, len(rows)


_BUILT = re.compile(
    r"\b(?:built|constructed|completed|founded|erected|consecrated|opened|established|"
    r"commissioned|inaugurated|dedicated|laid out|"
    r"dating from|dates back to|dates from|begun|raised)\b[^.]{0,40}?"
    r"\b(?:in|between|around|about|circa|c\.|from|during|by)?\s*"
    r"(?:(?P<cent>\d{1,2})(?:st|nd|rd|th)[ -]century(?P<cbc> BC| BCE)?|"
    r"(?P<year>\d{3,4})(?P<ybc> BC| BCE)?)",
    re.I,
)


def year_from_text(text: str) -> int | None:
    """A cautious reading of 'built in 1163' or 'founded in the 12th century'."""
    m = _BUILT.search(text or "")
    if not m:
        return None
    if m.group("cent"):
        c = int(m.group("cent"))
        return -(c - 1) * 100 - 50 if m.group("cbc") else (c - 1) * 100 + 50
    y = int(m.group("year"))
    if y > 2030:
        return None
    bce = m.group("ybc") or re.match(r"[\s\u2013\u2014-]*\d{0,4}\s*(BC|BCE)\b", text[m.end() :])
    return -y if bce else y


def dates_from_descriptions(tables: Tables) -> tuple[Tables, int]:
    """Fill still-missing construction dates from the descriptions' own wording."""
    rows = []
    for s in tables.sites.to_dict("records"):
        if s["construction_date"] or not s["description"]:
            continue
        y = year_from_text(s["description"])
        if y is not None:
            rows.append(
                {
                    "site_id": s["site_id"],
                    "construction_date": str(y),
                    "construction_date_basis": "description text (approximate)",
                }
            )
    tables.sites = upsert(tables.sites, rows, "site_id")
    return tables, len(rows)
