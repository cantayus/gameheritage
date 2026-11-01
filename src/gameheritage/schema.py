"""Table schemas for the catalogue.

The catalogue is five linked tables plus one helper table:

* ``games``: one row per game (or merged edition).
* ``sites``: one row per real-world structure or place.
* ``depictions``: one game's version of one site.
* ``images``: one row per unique image file (game or real), with provenance.
* ``pairs``: a curated game-image to real-image comparison.
* ``folder_aliases``: maps raw collection folder names to ``game_id``.

A site is kept separate from its depictions so that, for example, the Colosseum
appears once however many games show it.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator

QID_RE = re.compile(r"^Q[1-9]\d*$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class Role(str, Enum):
    GAME = "game"
    REAL = "real"


class ImageKind(str, Enum):
    SCREENSHOT = "screenshot"
    CONCEPT_ART = "concept_art"
    DATABASE_ENTRY = "database_entry"
    MAP = "map"
    RENDER = "render"
    ICON = "icon"
    UI = "ui"
    PORTRAIT = "portrait"
    PROMO_ART = "promo_art"
    MODERN_PHOTO = "modern_photo"
    HISTORICAL_PHOTO = "historical_photo"
    PAINTING_OR_ENGRAVING = "painting_or_engraving"
    UNKNOWN = "unknown"


class SiteType(str, Enum):
    BUILDING = "building"
    MONUMENT = "monument"
    FORTIFICATION = "fortification"
    RELIGIOUS_SITE = "religious_site"
    CITY_OR_DISTRICT = "city_or_district"
    LANDSCAPE = "landscape"
    OBJECT = "object"
    OTHER = "other"


class PresentState(str, Enum):
    STANDING = "standing"
    RUIN = "ruin"
    DESTROYED = "destroyed"
    REBUILT = "rebuilt"
    UNKNOWN = "unknown"


class MatchLevel(str, Enum):
    SAME_STRUCTURE_SAME_VIEW = "same_structure_same_view"
    SAME_STRUCTURE_OTHER_VIEW = "same_structure_other_view"
    SAME_SITE_OTHER_PART = "same_site_other_part"
    STYLE_ANALOGUE = "style_analogue"


class RightsStatus(str, Enum):
    OPEN = "open"  # free license or public domain, recorded
    DISPLAY_ONLY = "display_only"  # copyrighted game asset: show small, credit, link
    UNKNOWN = "unknown"


class ReviewStatus(str, Enum):
    TODO = "todo"
    KEEP = "keep"
    DROP = "drop"
    FIX = "fix"
    NEEDS_INFO = "needs_info"


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True, str_strip_whitespace=True)

    @staticmethod
    def _check_qid(value: str | None) -> str | None:
        if value and not QID_RE.match(value):
            raise ValueError(f"not a Wikidata QID: {value!r}")
        return value

    @staticmethod
    def _check_url(value: str | None) -> str | None:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError(f"not an http(s) URL: {value!r}")
        return value


class Game(_Row):
    game_id: str
    title: str
    series: str | None = None
    edition_note: str | None = None
    release_year: int | None = None
    developer: str | None = None
    publisher: str | None = None
    genre: str | None = None
    depicted_period_start: int | None = None
    depicted_period_end: int | None = None
    depicted_regions: str | None = None
    wikidata_qid: str | None = None
    wikipedia_url: str | None = None
    fandom_wiki: str | None = None
    facts_source: str | None = None
    tracker_status: str | None = None
    notes: str | None = None

    _qid = field_validator("wikidata_qid")(_Row._check_qid)
    _url = field_validator("wikipedia_url")(_Row._check_url)

    @field_validator("game_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"game_id must be a lowercase slug: {v!r}")
        return v


class Site(_Row):
    site_id: str
    name: str
    alt_names: str | None = None
    wikidata_qid: str | None = None
    site_type: SiteType | None = None
    country: str | None = None
    locality: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    construction_date: str | None = None
    construction_date_basis: str | None = None  # which date it is, and from where
    historical_period: str | None = None  # e.g. "Neolithic; Bronze Age" (Wikidata P2348)
    architectural_style: str | None = None
    heritage_designation: str | None = None
    present_state: PresentState | None = None
    commons_category: str | None = None
    wikipedia_url: str | None = None
    description: str | None = None
    description_source: str | None = None
    description_license: str | None = None
    facts_source: str | None = None
    review_status: ReviewStatus = ReviewStatus.TODO
    notes: str | None = None

    _qid = field_validator("wikidata_qid")(_Row._check_qid)
    _url = field_validator("wikipedia_url")(_Row._check_url)

    @field_validator("site_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not SLUG_RE.match(v):
            raise ValueError(f"site_id must be a lowercase slug: {v!r}")
        return v

    @field_validator("latitude")
    @classmethod
    def _lat(cls, v: float | None) -> float | None:
        if v is not None and not -90 <= v <= 90:
            raise ValueError("latitude out of range")
        return v

    @field_validator("longitude")
    @classmethod
    def _lon(cls, v: float | None) -> float | None:
        if v is not None and not -180 <= v <= 180:
            raise ValueError("longitude out of range")
        return v


class Depiction(_Row):
    depiction_id: str
    game_id: str
    site_id: str | None = None
    in_game_name: str
    period_in_game: str | None = None
    depiction_class: int | None = None  # 1 = named real place, 2 = inspired by
    basis_note: str | None = None
    basis_source: str | None = None
    # What the game shows: the lead of the place's page on the game's wiki.
    in_game_description: str | None = None
    in_game_description_source: str | None = None
    in_game_description_license: str | None = None
    review_status: ReviewStatus = ReviewStatus.TODO
    notes: str | None = None

    @field_validator("depiction_class")
    @classmethod
    def _cls(cls, v: int | None) -> int | None:
        if v is not None and v not in (1, 2):
            raise ValueError("depiction_class must be 1 (named real place) or 2 (inspired by)")
        return v


class Image(_Row):
    image_id: str
    role: Role
    game_id: str | None = None
    depiction_id: str | None = None
    site_id: str | None = None
    kind: ImageKind = ImageKind.UNKNOWN
    title: str | None = None
    in_game_place: str | None = None
    original_filename: str | None = None
    drive_path: str | None = None
    also_at: str | None = None
    local_path: str | None = None
    sha256: str | None = None
    phash: str | None = None
    width: int | None = None
    height: int | None = None
    true_format: str | None = None
    bytes: int | None = None
    source_name: str | None = None
    source_page_url: str | None = None
    file_url: str | None = None
    author: str | None = None
    license: str | None = None
    license_url: str | None = None
    rights_status: RightsStatus = RightsStatus.UNKNOWN
    attribution_required: bool | None = None
    collected_by: str | None = None
    collection_method: str | None = None
    retrieved_at: str | None = None
    flags: str | None = None
    review_status: ReviewStatus = ReviewStatus.TODO
    notes: str | None = None

    _urls = field_validator("source_page_url", "file_url", "license_url")(_Row._check_url)


class Pair(_Row):
    pair_id: str
    depiction_id: str
    game_image_id: str
    real_image_id: str
    match_level: MatchLevel | None = None
    curator: str | None = None
    note: str | None = None
    featured: bool = False
    review_status: ReviewStatus = ReviewStatus.TODO


class FolderAlias(_Row):
    folder_name: str
    game_id: str
    note: str | None = None


MODELS: dict[str, type[_Row]] = {
    "games": Game,
    "sites": Site,
    "depictions": Depiction,
    "images": Image,
    "pairs": Pair,
    "folder_aliases": FolderAlias,
}

PRIMARY_KEYS: dict[str, str] = {
    "games": "game_id",
    "sites": "site_id",
    "depictions": "depiction_id",
    "images": "image_id",
    "pairs": "pair_id",
    "folder_aliases": "folder_name",
}


def columns(table: str) -> list[str]:
    """Column names of a table, in file order."""
    return list(MODELS[table].model_fields)


FREE_LICENSE_PREFIXES = ("public domain", "pd", "cc0", "cc by", "cc-by")
NONFREE_MARKERS = ("nc", "nd", "noncommercial", "noderivs")


def is_free_license(short_name: str | None) -> bool:
    """True for public domain, CC0, CC BY and CC BY-SA (any version).

    These are the licenses the project redistributes. NC and ND variants,
    GFDL-only and anything unrecognised return False.
    """
    if not short_name:
        return False
    s = short_name.strip().lower().replace("_", " ")
    if not s.startswith(FREE_LICENSE_PREFIXES):
        return False
    tokens = set(re.split(r"[\s\-/]+", s))
    return not tokens.intersection(NONFREE_MARKERS)


def license_family(short_name: str | None) -> str:
    """Normalise a license name to its family: ``"CC BY-SA 4.0"`` -> ``"cc by-sa"``.

    Families: ``public domain``, ``cc0``, ``cc by``, ``cc by-sa``, ``cc by-nc``,
    ``cc by-nc-sa``, ``cc by-nd``, ``cc by-nc-nd``; anything else returns the
    lowercased name.
    """
    s = (short_name or "").strip().lower().replace("_", " ")
    if not s:
        return ""
    if s.startswith(("public domain", "pd")):
        return "public domain"
    if s.startswith("cc0") or "publicdomain/zero" in s:
        return "cc0"
    m = re.match(r"cc[ -]by((?:[ -](?:nc|sa|nd))*)", s)
    if m:
        parts = re.findall(r"nc|sa|nd", m.group(1))
        return "cc by" + "".join(f"-{x}" for x in parts)
    return s
