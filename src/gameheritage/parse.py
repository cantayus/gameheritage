"""Parse the raw collection's file names into labels, kinds and Fandom provenance.

Four naming families occur in the collection:

* Fandom page scrapes: ``<Page_Title>__File_<file>_<ext>.jpg`` and
  ``<Page_Title>__page_image.jpg``. The page title is the in-game place name.
* Fandom CDN names: ``https___static_wikia_nocookie_net_<wiki>_images_...``,
  which also reveal which wiki the file came from.
* Hand-named files with a series prefix: ``ACB DB Castra Praetoria.png``,
  ``ACSH Kanegasaki Castle 4.jpg``, ``Taj_Mahal_in-game__Civ6__png.png``.
* Real images: ``NN_<subject>[_<place>].ext``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

from .names import humanize, normalize_text

IMAGE_EXTS = {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
OTHER_MEDIA_EXTS = {".ogg", ".ogv", ".mp4", ".webm", ".svg"}
SYSTEM_FILES = {"desktop.ini", ".ds_store", "thumbs.db"}
CLOUD_STUB_EXTS = {".gdoc", ".gsheet", ".gslides"}

# Subfolders that describe a category rather than a place.
GENERIC_SUBFOLDERS = {
    "castles", "churches", "towns_and_villages", "fortresses", "shrines", "location_images",
    "building_images", "buildings", "concept_art", "landmarks", "wonders", "game data",
    "real images",
}  # fmt: skip

_EXT_TAIL = re.compile(r"_(?P<orig>jpe?g|png|gif|webp|svg)_?$", re.I)
_FANDOM_FILE = re.compile(r"^(?P<page>.+?)__File_(?P<file>.+)$")
_FANDOM_PAGE = re.compile(r"^(?P<page>.+?)__page_image$")
_CDN = re.compile(
    r"^https?_{3}static_wikia_nocookie_net_(?P<wiki>.+?)_images_[0-9a-f]_[0-9a-f]{2}_(?P<file>.+?)"
    r"(?:_revision_latest.*|_rev)?$"
)
_REAL = re.compile(r"^(?P<n>\d{2})_(?P<subject>.+)$")
_CIV_TAG = re.compile(r"(?:_{1,2}|\s*\()Civ(?:ilization)?(?P<n>\d|_?Rev)\)?_*$", re.I)
_COPY = re.compile(r"\s*\(\d+\)$")
_SEQ = re.compile(r"[\s_\-]+0*\d{1,2}[a-z]?$")
_RES = re.compile(r"\s*\b(?:4k|8k|hd|\d{3,4}x\d{3,4})\b", re.I)

SERIES_PREFIXES = [
    "AC Twelve Trials", "ACUDB -", "AC4DB -", "ACOD -", "ACB DB", "ACB ART", "ACB Art",
    "AC1 Art", "ACRGDB -", "ACRGDB", "ACRDB -", "ACRDB", "ACSH DB", "ACV DB", "ACS DB",
    "ACU DB", "AC2 DB", "ACII DB", "AC3 DB", "ACMir DB", "AC3R", "AC3L", "ACIII-GI", "ACIII",
    "ACIV", "AC4", "AC3", "AC2", "AC1", "ACB", "ACOd", "ACOD", "ACO THO", "ACO", "ACV", "ACSH",
    "ACS", "ACU", "ACR", "ACRev", "ACMir", "TWS2", "S2TW", "AoE4", "AoE2", "AOE2", "Kcd2",
    "KCD2", "KCD", "Kcr", "TWP", "TWRR", "RDR2", "RDR", "Rdr", "POPFS", "Poptt", "SB",
]  # fmt: skip
_PREFIX_RE = re.compile(
    r"^(?:" + "|".join(re.escape(p) for p in sorted(SERIES_PREFIXES, key=len, reverse=True)) + r")"
    r"(?=[\s_\-:])[\s_\-:]*"
)

KIND_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("database_entry", re.compile(r"\bdb\b|database|\bacudb\b|\bac4db\b", re.I)),
    ("map", re.compile(r"\bmaps?\b|animus map|minimap", re.I)),
    ("ui", re.compile(r"load(?:ing)?\s*screen|loadingscreen|\bmenu\b|\bhud\b", re.I)),
    ("icon", re.compile(r"\bicon\b|\blogo\b|\bemblem\b|\bcoa\b|coat of arms|\bbadge\b", re.I)),
    (
        "concept_art",
        re.compile(
            r"concept|artwork|completion art|illustration|sketch|\bac\w*\s+art\b|^art\b|"
            r"\bart\s+(?:of|by)\b|\bfan ?art\b",
            re.I,
        ),
    ),
    ("promo_art", re.compile(r"wallpaper|\bpromo|key ?art|\bcover\b|poster|trailer", re.I)),
    ("screenshot", re.compile(r"screenshot|\bscr\d*\b|\bss\d*\b|in-game|\bingame\b", re.I)),
    ("portrait", re.compile(r"portrait|\bcharacter\b|\bnpc\b", re.I)),
]

# Words describing the view or variant, not the place itself.
_VIEW_WORDS = re.compile(
    r"\b(?:in-game|completion art|concept art|concept|database image|db|screenshot|"
    r"interior|exterior|overlook|closeup|close-up|entrance|aerial|view|night|day|"
    r"variants?|model|wonder|historic moment|natural wonder|loading screen|load screen|"
    r"location cropped|cropped|location|map|frontlines|artwork|wallpaper)\b",
    re.I,
)


@dataclass
class ParsedName:
    pattern: str
    label: str
    place_hint: str
    kind_guess: str
    page_title: str = ""
    fandom_file: str = ""
    fandom_wiki_path: str = ""
    fandom_key: str = ""  # sanitised name as saved by the scraper, for matching
    page_key: str = ""
    series_prefix: str = ""
    real_subject: str = ""
    original_ext: str = ""
    subfolder_hint: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def guess_kind(text: str) -> str:
    t = text.replace("_", " ")
    for kind, rx in KIND_RULES:
        if rx.search(t):
            return kind
    return "unknown"


def _strip_variants(label: str) -> str:
    s = _COPY.sub("", label)
    s = _RES.sub("", s)
    s = _SEQ.sub("", s)
    return normalize_text(s.strip(" -_,"))


def place_from_label(label: str) -> str:
    """Strip view words, sequence numbers and copy markers to get a place name."""
    s = _strip_variants(label)
    s = re.sub(r"\s*\((?:civ\d|[^)]*edition)\)", "", s, flags=re.I)
    s = _VIEW_WORDS.sub(" ", s)
    s = re.sub(r"\s+-\s+.*$", "", s)  # "Medical Clinic - Living Room" -> "Medical Clinic"
    s = _strip_variants(s)
    s = re.sub(r"^(?:of|the)\s+(?=[A-Z])", "", s.strip(" -_,:"))
    return normalize_text(s)


def strip_game_name(label: str, game_folder: str) -> str:
    """Drop a trailing game title: ``College Empire Total War`` -> ``College``."""
    tokens = re.findall(r"[a-z0-9]+", game_folder.lower())
    words = label.split()
    for n in range(len(tokens), 1, -1):
        tail = " ".join(tokens[-n:])
        if " ".join(w.lower() for w in words[-n:]) == tail and len(words) > n:
            return " ".join(words[:-n])
    return label


def _split_ext_tail(stem: str) -> tuple[str, str]:
    m = _EXT_TAIL.search(stem)
    if m:
        return stem[: m.start()], m.group("orig").lower()
    return stem, ""


def sanitize_key(name: str) -> str:
    """How the scraper turned titles into file names: every non-alphanumeric -> ``_``."""
    return re.sub(r"[^A-Za-z0-9]", "_", name)


def parse_filename(rel_path: str) -> ParsedName:
    """Parse a path relative to the collection root: ``<game folder>/<area>/[sub/]file``."""
    parts = PurePosixPath(rel_path.replace("\\", "/")).parts
    filename = parts[-1]
    game_folder = parts[0] if len(parts) > 1 else ""
    subfolder = parts[-2] if len(parts) >= 3 else ""
    sub_hint = "" if subfolder.lower() in GENERIC_SUBFOLDERS else humanize(subfolder)
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    in_real = len(parts) >= 2 and "real images" in [p.lower() for p in parts[1:-1]]

    def place(label: str) -> str:
        return place_from_label(strip_game_name(label, game_folder))

    m = _REAL.match(stem)
    if in_real and m:
        subject = humanize(m.group("subject"))
        return ParsedName("real_numbered", subject, subject, "unknown", real_subject=subject)

    m = _CDN.match(stem)
    if m:
        raw = m.group("file")
        base, orig = _split_ext_tail(raw)
        label = humanize(base)
        return ParsedName(
            "fandom_cdn", label, place(label), guess_kind(base),
            fandom_file=f"{base}.{orig}" if orig else base, fandom_key=raw,
            fandom_wiki_path=m.group("wiki"), original_ext=orig, subfolder_hint=sub_hint,
        )  # fmt: skip

    m = _FANDOM_FILE.match(stem)
    if m:
        raw_page, raw_file = m.group("page"), m.group("file")
        page = humanize(raw_page)
        base, orig = _split_ext_tail(raw_file)
        label = humanize(base)
        return ParsedName(
            "fandom_file", label, place(page), guess_kind(f"{page} {base}"), page_title=page,
            fandom_file=f"{base}.{orig}" if orig else base, fandom_key=raw_file,
            page_key=raw_page, original_ext=orig, subfolder_hint=sub_hint,
        )  # fmt: skip

    m = _FANDOM_PAGE.match(stem)
    if m:
        page = humanize(m.group("page"))
        return ParsedName(
            "fandom_page_image", page, place(page), "unknown", page_title=page,
            page_key=m.group("page"), subfolder_hint=sub_hint,
        )  # fmt: skip

    base, orig = _split_ext_tail(stem)
    pattern = "fandom_download" if orig else "hand_named"
    # A hand-named file often keeps its wiki title: try it as-is (minus " (1)").
    clean_stem = _COPY.sub("", stem)
    ext_word = filename.rsplit(".", 1)[-1] if "." in filename else ""
    key = stem if orig else (sanitize_key(f"{clean_stem}.{ext_word}") if ext_word else "")
    raw_base = base
    base = _CIV_TAG.sub("", base)
    text = normalize_text(base.replace("_", " ") if orig else base)
    prefix = ""
    pm = _PREFIX_RE.match(text)
    if pm:
        prefix = pm.group(0).strip(" -_:")
        text = text[pm.end() :]
    label = humanize(text) if orig else normalize_text(_camel_split(text))
    kind = guess_kind(f"{prefix} {stem}")
    return ParsedName(
        pattern, label, place(label) or sub_hint, kind,
        fandom_file=f"{raw_base}.{orig}" if orig else (f"{clean_stem}.{ext_word}" if key else ""),
        fandom_key=key,
        series_prefix=prefix, original_ext=orig, subfolder_hint=sub_hint,
    )  # fmt: skip


def _camel_split(text: str) -> str:
    """Split CamelCase and hyphen-joined tokens: ``Achaia-OlourosFortress`` -> readable."""
    s = re.sub(r"(?<=[a-z])(?=[A-Z][a-z])", " ", text)
    # Achaia-OlourosFortress-coffer (CamelCase joined by hyphens), not Hotel-Dieu.
    if " " not in text and re.search(r"[a-z]-[A-Z]", text) and re.search(r"[a-z][A-Z]", text):
        return s.replace("-", " - ")
    return s
