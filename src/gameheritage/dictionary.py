"""What every column of the released tables means.

``FIELDS[table][column]`` is a one-line description. :func:`describe` returns a
table's columns as a DataFrame, and :func:`markdown` writes the data dictionary
in ``docs/data_dictionary.md``.
"""

from __future__ import annotations

import pandas as pd

TABLE_DOCS = {
    "games": "One row per game (or merged edition) with at least one matched place.",
    "sites": "One row per real place: a building, monument, district, settlement or "
    "natural formation. A place appears once however many games show it.",
    "depictions": "One game's version of one real place. The link between a game and a "
    "site, with the in-game name and how the match was made.",
    "images": "One row per image file, game or real, with its source, author, license "
    "and how it was collected.",
    "pairs": "Candidate comparisons: one game image and one real image of the same place.",
}

FIELDS: dict[str, dict[str, str]] = {
    "games": {
        "game_id": "Identifier of the game (lowercase slug), e.g. 'assassins-creed-unity'",
        "title": "Title of the game",
        "series": "Series or franchise",
        "edition_note": "Editions or expansions merged into this row",
        "release_year": "Year of first release",
        "developer": "Developer(s), from Wikidata",
        "publisher": "Publisher(s), from Wikidata",
        "genre": "Genre(s), from Wikidata",
        "depicted_period_start": "First year of the historical period the game is set in",
        "depicted_period_end": "Last year of the historical period the game is set in",
        "depicted_regions": "Regions or countries the game is set in",
        "wikidata_qid": "Wikidata item of the game",
        "wikipedia_url": "English Wikipedia article of the game",
        "fandom_wiki": "The game's Fandom wiki, where most game images come from",
        "facts_source": "Where the game facts come from, with retrieval date",
        "tracker_status": "Curation status of the game in the project",
        "notes": "Curators' notes",
    },
    "sites": {
        "site_id": "Identifier of the place (lowercase slug), e.g. 'notre-dame-de-paris'",
        "name": "Name of the place (English label on Wikidata)",
        "alt_names": "Other names, separated by semicolons",
        "wikidata_qid": "Wikidata item of the place",
        "site_type": "Project category (building, monument, ...); often empty, see wikidata_type",
        "country": "Country, from Wikidata",
        "locality": "City or area the place is in, from Wikidata",
        "latitude": "Latitude (WGS84), from Wikidata",
        "longitude": "Longitude (WGS84), from Wikidata",
        "construction_date": "Year built, founded or first recorded; negative for BCE",
        "construction_date_basis": "Which date that is and where it comes from "
        "(e.g. 'Wikidata, inception'; 'description text (approximate)')",
        "historical_period": "Historical period(s) on Wikidata (P2348), e.g. 'Neolithic'",
        "architectural_style": "Architectural style(s), from Wikidata",
        "heritage_designation": "Heritage status, e.g. 'World Heritage Site', from Wikidata",
        "present_state": "Standing, ruin, destroyed or rebuilt, when recorded",
        "commons_category": "Wikimedia Commons category of the place",
        "wikipedia_url": "English Wikipedia article of the place",
        "description": "Opening sentences of the Wikipedia article (or Wikidata description)",
        "description_source": "Source page of the description",
        "description_license": "License of the description text (Wikipedia: CC BY-SA 4.0)",
        "facts_source": "Where the place facts come from, with retrieval date",
        "review_status": "Curation status of the place: todo (not yet reviewed), keep, drop",
        "notes": "Curators' notes; 'type: ...' gives the Wikidata type",
    },
    "depictions": {
        "depiction_id": "Identifier: '<game_id>--<place slug>'",
        "game_id": "The game (games.game_id)",
        "site_id": "The real place (sites.site_id)",
        "in_game_name": "Name of the place in the game or on its wiki",
        "period_in_game": "Period in which the game shows the place, when recorded",
        "depiction_class": "1 = the game names the real place; 2 = inspired by it",
        "basis_note": "How the match was made, e.g. an automatic Wikidata match and its score",
        "basis_source": "Evidence for the match (Wikidata item or reference)",
        "in_game_description": "Opening of the place's page on the game's Fandom wiki",
        "in_game_description_source": "Source page of the in-game description",
        "in_game_description_license": "License of the in-game description (Fandom: CC BY-SA 3.0)",
        "review_status": "keep = reviewed by the project; todo = automatic, not yet reviewed",
        "notes": "Curators' notes",
    },
    "images": {
        "image_id": "Identifier of the image ('gh-' + hash)",
        "role": "'game' (in-game image) or 'real' (photograph or picture of the real place)",
        "game_id": "Game the image comes from (game images)",
        "depiction_id": "In-game version the image shows (game images)",
        "site_id": "Real place the image shows",
        "kind": "screenshot, concept_art, render, modern_photo, historical_photo, ...",
        "title": "Title of the image (file page title or the collector's name for it)",
        "in_game_place": "Place name the collector or wiki gives the image",
        "original_filename": "File name at the source",
        "sha256": "SHA-256 of the original file, for de-duplication and integrity checks",
        "phash": "Perceptual hash of the original file, for near-duplicate detection",
        "width": "Width of the original file in pixels",
        "height": "Height of the original file in pixels",
        "true_format": "Format of the original file as read from its bytes (jpeg, png, webp...)",
        "bytes": "Size of the original file in bytes",
        "source_name": "Where the image comes from, e.g. 'Wikimedia Commons' or a Fandom wiki",
        "source_page_url": "The image's page at the source (file page with author and license)",
        "file_url": "Direct link to the original file at the source",
        "author": "Author or uploader",
        "license": "License, e.g. 'CC BY-SA 4.0', 'Public domain'; empty for game assets",
        "license_url": "Link to the license",
        "rights_status": "open = free license or public domain; display_only = copyrighted "
        "game asset shown for research and commentary; unknown = not yet recorded",
        "attribution_required": "Whether the license requires attribution",
        "collected_by": "Who collected the image (a project member or the project's scripts)",
        "collection_method": "How the image was collected",
        "retrieved_at": "Date the image was retrieved",
        "flags": "Problems found by the checks (duplicates, wrong format, ...)",
        "review_status": "keep, todo (not yet reviewed) or drop",
        "notes": "Curators' notes",
    },
    "pairs": {
        "pair_id": "Identifier of the pair",
        "depiction_id": "In-game version compared (depictions.depiction_id)",
        "game_image_id": "Game image (images.image_id)",
        "real_image_id": "Real image (images.image_id)",
        "match_level": "same_structure_same_view, same_structure_other_view, "
        "same_site_other_part or style_analogue, when reviewed",
        "curator": "Who made or reviewed the pair",
        "note": "Curator's note",
        "featured": "Whether the pair is a featured example",
        "review_status": "keep (reviewed) or todo (automatic candidate)",
    },
}


def extra_fields() -> dict[str, list[tuple[str, str, str]]]:
    from .build import RELEASE_EXTRA

    return RELEASE_EXTRA


def describe(table: str | None = None) -> pd.DataFrame:
    """Columns of the released tables with their meaning.

    ``describe("sites")`` lists one table; ``describe()`` lists all of them.
    """
    extra = extra_fields()
    names = [table] if table else list(FIELDS)
    rows = []
    for name in names:
        if name not in FIELDS:
            raise KeyError(f"unknown table {name!r}; tables are {list(FIELDS)}")
        for col, text in FIELDS[name].items():
            rows.append({"table": name, "column": col, "description": text})
        for col, _, text in extra.get(name, []):
            rows.append({"table": name, "column": col, "description": text})
    return pd.DataFrame(rows)


def markdown() -> str:
    from .build import PRIVATE_IMAGE_COLUMNS

    extra = extra_fields()
    out = [
        "# Data dictionary",
        "",
        "The released tables are linked by their identifiers: a **depiction** joins a "
        "**game** to a real **site**; **images** belong to a depiction (game images) or "
        "to a site (real images); **pairs** put one game image next to one real image.",
        "",
        "```",
        "games ──< depictions >── sites",
        "              │            │",
        "        game images   real images",
        "              └── pairs ───┘",
        "```",
        "",
        'In Python, `gameheritage.describe("sites")` returns the same descriptions.',
        "",
    ]
    for name, cols in FIELDS.items():
        out += [f"## {name}", "", TABLE_DOCS[name], "", "| Column | Meaning |", "|---|---|"]
        for col, text in cols.items():
            if name == "images" and col in PRIVATE_IMAGE_COLUMNS:
                continue
            out.append(f"| `{col}` | {text} |")
        for col, _, text in extra.get(name, []):
            out.append(f"| `{col}` | {text} |")
        out.append("")
    return "\n".join(out)
