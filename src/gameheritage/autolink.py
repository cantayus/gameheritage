"""Automatic linking of in-game places to real-world sites on Wikidata.

Every game image must have a real-world equivalent. This module proposes one
for each in-game place at scale:

1. ``place_groups`` groups a game's images by in-game place name.
2. ``link_places`` searches Wikidata for every name (batched through the query
   service) and scores each candidate on name similarity, whether it is a
   physical place with coordinates, what kind of thing it is, and how close it
   lies to the game's other confidently matched places.
3. ``apply_links`` turns confident matches into sites and depictions and links
   the images; everything stays ``todo`` until a person confirms it.

Low-confidence matches are only written to the review sheet.
"""

from __future__ import annotations

import contextlib
import math
import re
import unicodedata
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from .names import slugify
from .sources import wikidata
from .sources.http import HttpClient
from .tables import Tables, upsert

SKIP_KINDS = {"icon", "ui", "portrait"}
SKIP_FLAGS = {"icon_like", "empty_file", "unreadable", "other_media", "misfiled_in_real_folder"}
GENERIC = re.compile(
    r"^(?:img|image|dsc|scr|screenshot|screen|wallpaper|artwork|art|map|untitled|photo|pic|"
    r"render|concept|promo|logo|icon|banner|loading|load|title|cover|key|\d+|[a-z]{1,2})$",
    re.I,
)
BAD_TYPES = re.compile(
    r"\b(?:human|family name|given name|surname|fictional|video game|film|television|"
    r"tv series|album|song|single|novel|book|band|musical|wikimedia|disambiguation|taxon|"
    r"species|genus|chemical|company|business|enterprise|organization|software|episode|"
    r"character|literary work|asteroid|scholarly article|journal|magazine|sports team|"
    r"football club|award|military unit|battle|war\b|military operation|calendar|"
    r"painting|sculpture series|group of humans|ethnic group|language|occupation|"
    r"profession|religion|deity|mythological|legendary|title of authority|noble title)\b",
    re.I,
)
GOOD_TYPES = re.compile(
    r"\b(?:building|church|cathedral|basilica|chapel|temple|mosque|shrine|synagogue|castle|"
    r"fortress|fort|fortification|citadel|palace|monument|memorial|tower|bridge|square|"
    r"plaza|street|road|district|quarter|neighbo(?:u)?rhood|city|town|village|"
    r"human settlement|island|mountain|hill|lake|river|archaeological site|ruins|abbey|"
    r"monastery|convent|priory|museum|theat(?:re|er)|amphitheat(?:re|er)|stadium|"
    r"lighthouse|harbo(?:u)?r|port|gate|wall|tomb|mausoleum|pyramid|statue|park|"
    r"garden|cave|canyon|valley|market|hall|house|villa|estate|mill|mine|landmark|"
    r"wonder|heritage|baths|forum|aqueduct|necropolis|cemetery|school|university|"
    r"library|hospital|prison|barracks|arsenal|shipyard|dock|canal|fountain|obelisk|"
    r"arch|triumphal arch|colosseum|circus|hippodrome|sanctuary|oracle|agora|acropolis|"
    r"skyscraper|hotel|restaurant|bar|pub|inn|shop|store|bank|cinema|commune|"
    r"municipality|capital|metropolis|region|province|peninsula|bay|beach|cape|"
    r"waterfall|volcano|desert|forest|reef|national park|geographic)\b",
    re.I,
)


GENERIC_WORDS = set(
    "saint sainte san santa st ste pont palais church chapel castle house tower fort fortress "
    "palace temple shrine district street square plaza market bridge gate harbour harbor port "
    "village town city camp farm mill mine hall cafe bar shop inn tavern ruins ruin cave "
    "island lake river hill mountain valley forest field road wall walls camp base outpost "
    "barracks dock docks warehouse factory estate manor garden park station sixth first second "
    "third north south east west upper lower old new great little interior exterior main".split()
)


def usable_term(term: str) -> bool:
    """False for empty, very short or generic one-word names ("Saint", "Palais")."""
    words = _norm(term).split()
    if not words:
        return False
    if len(words) == 1:
        return len(words[0]) > 4 and words[0] not in GENERIC_WORDS
    return True


def all_generic(term: str) -> bool:
    """Every word is a generic place word: "Tower Bridge", "Old North Church", "East Gate".

    Such names are searched, but a match must be well known (``GENERIC_MIN_FAME``
    Wikipedia sitelinks) unless Wikidata ties it to the game.
    """
    words = _norm(term).split()
    return bool(words) and all(w in GENERIC_WORDS for w in words)


GENERIC_MIN_FAME = 10


TRANSPORT = re.compile(
    r"\b(?:metro|railway|train|subway|tram|bus|underground) (?:station|stop)|\bairport\b", re.I
)


# Built structures (what the project collects): buildings, monuments, fortifications,
# religious sites, bridges, urban squares and streets, archaeological sites.
STRUCTURE_TYPES = re.compile(
    r"\b(?:building|church|cathedral|basilica|chapel|temple|mosque|shrine|synagogue|"
    r"castle|fortress|fort\b|fortification|citadel|palace|monument|memorial|tower|bridge|"
    r"gate|city wall|wall\b|tomb|mausoleum|pyramid|statue|abbey|monastery|convent|priory|"
    r"museum|theat(?:re|er)|amphitheat(?:re|er)|stadium|lighthouse|baths|thermae|forum|"
    r"aqueduct|arch\b|triumphal arch|obelisk|fountain|colosseum|circus|hippodrome|sanctuary|"
    r"agora|acropolis|archaeological site|ruins|villa|mansion|house|hall|hotel|market|"
    r"square|plaza|street|harbou?r|dock|canal|mill|barracks|arsenal|prison|hospital|school|"
    r"university|library|skyscraper|cemetery|necropolis|structure|landmark|stupa|pagoda|"
    r"ziggurat|minaret|bell tower|campanile|cloister|estate|manor|château|chateau|keep|"
    r"bastion|watchtower|lighthouse|dam|mine\b|quarry|well\b|cistern|odeon|stoa|"
    r"sculpture|relief|colossus|world heritage site|heritage|"
    # built environments: parks, complexes, districts, ports, roads, settlements
    r"park|garden|complex|campus|compound|quarter|district|neighbo(?:u)?rhood|rione|"
    r"arrondissement|old town|historic cent(?:re|er)|port\b|pier|wharf|quay|road|avenue|"
    r"boulevard|railway station|train station|station|city|town|village|hamlet|"
    r"human settlement|settlement|commune|municipality|frazione|capital|metropolis|"
    r"neighborhood|suburb|plantation|farm|ranch|camp|oppidum|hillfort|earthwork|cordon)(?:e?s)?\b",
    re.I,
)
# Natural formations, also collected: mountains, rock formations, caves, waters...
NATURAL_TYPES = re.compile(
    r"\b(?:mountain|mount\b|volcano|hill|cliff|rock formation|rock\b|monolith|inselberg|"
    r"cave|cavern|canyon|gorge|ravine|valley|waterfall|cascade|lake|river|spring|geyser|"
    r"hot spring|island|islet|archipelago|atoll|beach|bay\b|cape\b|headland|peninsula|"
    r"fjord|glacier|ice cap|desert|dune|oasis|forest|reef|coral|crater|caldera|plateau|"
    r"mesa|butte|natural arch|sinkhole|cenote|natural monument|natural wonder|"
    r"national park|nature reserve|protected area|salt flat|salt pan|lagoon|delta|wetland|"
    r"marsh|swamp|strait|massif|summit|peak|ridge|pass\b|karst|stone forest|tepui)(?:e?s)?\b",
    re.I,
)
# Administrative areas are neither: a phrase here never counts as built or natural.
ADMIN = re.compile(
    r"\b(?:region|province|country|state of|historical region|geographic region|"
    r"administrative territorial entity|autonomous community|federal state|county|"
    r"sovereign state|oblast|prefecture|department of)\b",
    re.I,
)


def place_kind(instance_of: str, description: str = "") -> str:
    """``"built"``, ``"natural"`` or ``""`` (not collected: regions, people, works...)."""
    inst = ADMIN.sub(" ", instance_of)
    if STRUCTURE_TYPES.search(NATURAL_TYPES.sub(" ", inst)):
        return "built"
    if NATURAL_TYPES.search(inst):
        return "natural"
    if ADMIN.search(instance_of):
        return ""
    desc = ADMIN.sub(" ", description)
    if STRUCTURE_TYPES.search(NATURAL_TYPES.sub(" ", desc)):
        return "built"
    if NATURAL_TYPES.search(desc):
        return "natural"
    return ""


def is_built(instance_of: str, description: str = "") -> bool:
    """True for places the project collects: built structures (buildings, sculptures,
    parks, complexes, settlements) and natural formations. Not regions or people."""
    return bool(place_kind(instance_of, description))


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    t = re.sub(r"\b(?:the|of|de|la|le|di|del|des|du)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def similarity(a: str, b: str) -> float:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    contain = len(ta & tb) / max(1, min(len(ta), len(tb)))
    return max(ratio, 0.9 * contain if min(len(ta), len(tb)) >= 2 else ratio)


def name_signals(term: str, qids: list[str], summ: dict[str, dict]) -> tuple[bool, int]:
    """What Wikidata says about a name itself, regardless of which item matches.

    Returns ``(common_noun, rival_fame)``:

    * ``common_noun``: the name is an ordinary noun phrase ("Grand Staircase",
      "Engine Room", "Police"). Wikidata writes English labels of concepts in
      lower case, so a lower-case item with this label, or matches that are an
      instance of a class with this name, give it away.
    * ``rival_fame``: sitelinks of the most famous item with this name that is
      not a place (a person, a planet, a concept). "Rollo" usually means the
      Viking, not a statue of him.
    """
    nt = _norm(term)
    common, rival = False, 0
    for q in qids:
        s = summ.get(q)
        if not s:
            continue
        label = s.get("label") or ""
        same = similarity(term, label) >= 0.95
        if same and label == label.lower() and re.search(r"[a-z]", label):
            common = True
        types = {_norm(t) for t in (s.get("instance_of") or "").split(";")}
        if nt in types:
            common = True
        if same and (s.get("latitude") is None or not place_kind(s.get("instance_of") or "", "")):
            rival = max(rival, int(s.get("sitelinks") or 0))
    return common, rival


def _haversine(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(min(1.0, a)))


# Spread of a game's places (median distance from their centre): below
# COMPACT_KM it is set in one city or region, above WIDE_KM all over the world.
WIDE_KM = 1500.0
COMPACT_KM = 150.0  # a game set in one city or region
MIN_ANCHORS = 5  # own matches needed to locate a game without Wikidata's help
WORLD_FAMOUS = 100  # sitelinks of a place famous enough to appear anywhere
COUNTRY = re.compile(r"\b(?:country|sovereign state)\b", re.I)


def _isolated(s: dict, pts: list[tuple[float, float]], km: float = 150.0) -> bool:
    """No other place of the game lies within ``km``: not part of a second
    cluster (Venice in a mostly-Florence game), but a lone namesake."""
    if s.get("latitude") is None:
        return False
    near = [p for p in pts if 1.0 < _haversine(s["latitude"], s["longitude"], p[0], p[1]) <= km]
    return not near


def _extra_words(term: str, label: str) -> bool:
    """The label contains the in-game name plus words of its own (not generic ones)."""
    t, lab = set(_norm(term).split()), _norm(label).split()
    extra = [w for w in lab if w not in t and w not in GENERIC_WORDS and not w.isdigit()]
    return bool(t) and t <= set(lab) and bool(extra)


def _split(text: str) -> list[str]:
    return [t.strip() for t in str(text).split(";") if t.strip()]


_SOFT = {
    "several places share this name",
    "outside the game's usual area",
    "little-known namesake in a worldwide game",
    "outside the countries Wikidata gives for the game",
    "the real name has extra words",
}


def _robust_center(pts: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Median centre and median radius, recomputed without far outliers."""

    def center(points):
        lat = sorted(p[0] for p in points)[len(points) // 2]
        lon = sorted(p[1] for p in points)[len(points) // 2]
        d = sorted(_haversine(lat, lon, a, b) for a, b in points)
        return lat, lon, d[len(d) // 2]

    lat, lon, r = center(pts)
    core = [p for p in pts if _haversine(lat, lon, *p) <= max(2 * r, 200)]
    if len(core) >= 3:
        lat, lon, r = center(core)
    return lat, lon, r


def place_groups(tables: Tables, manifest: pd.DataFrame) -> pd.DataFrame:
    """Group linkable game images by (game, in-game place)."""
    by_path = manifest.set_index("rel_path")
    rows = []
    imgs = tables.images[
        (tables.images["role"] == "game") & (tables.images["review_status"] != "drop")
    ]
    for r in imgs.to_dict("records"):
        if r["kind"] in SKIP_KINDS or set(r["flags"].split(";")) & SKIP_FLAGS:
            continue
        if r["drive_path"] in by_path.index:
            p = by_path.loc[r["drive_path"]]
            place = (p["place_hint"] or p["subfolder_hint"]).strip()
            label, sub = p["label"], p["subfolder_hint"]
        else:  # harvested images carry their place in the images table
            place, label, sub = r["in_game_place"].strip(), r["title"], ""
        if not place or GENERIC.match(place) or len(_norm(place)) < 3:
            continue
        segments = [s.strip() for s in re.split(r"\s+-\s+", label) if len(s.strip()) > 3]
        rows.append(
            {
                "game_id": r["game_id"],
                "place": place,
                "place_key": _norm(place),
                "terms": "|".join(t for t in dict.fromkeys([place, *segments, sub]) if t),
                "image_id": r["image_id"],
            }
        )
    if not rows:
        return pd.DataFrame(columns=["game_id", "place", "terms", "n_images", "image_ids"])
    df = pd.DataFrame(rows)
    out = (
        df.groupby(["game_id", "place_key"], sort=False)
        .agg(
            place=("place", "first"),
            terms=("terms", lambda s: "|".join(dict.fromkeys("|".join(s).split("|")))),
            n_images=("image_id", "size"),
            image_ids=("image_id", ";".join),
        )
        .reset_index()
        .drop(columns="place_key")
    )
    return out.sort_values(["game_id", "n_images"], ascending=[True, False]).reset_index(drop=True)


def _candidate_score(term: str, ordinal: int, s: dict) -> tuple[float, float, list[str]]:
    why = []
    sim = similarity(term, s["label"])
    score = 0.6 * sim
    if ordinal == 0:
        score += 0.15
    elif ordinal == 1:
        score += 0.08
    types = f"{s['instance_of']} {s['description']}"
    if BAD_TYPES.search(types) and not GOOD_TYPES.search(s["instance_of"]):
        score -= 1.0
        why.append("not a place")
    elif GOOD_TYPES.search(types):
        score += 0.15
    if s["latitude"] is not None:
        score += 0.2
    else:
        score -= 0.3
        why.append("no coordinates")
    if TRANSPORT.search(types) and not re.search(r"station|stop|airport", term, re.I):
        score -= 0.4
        why.append("transport stop")
    if s["image"]:
        score += 0.05
    if s.get("wikipedia_url"):
        score += 0.1
    # Fame: famous landmarks have articles in many languages.
    score += min(0.3, 0.1 * math.log10(1 + s.get("sitelinks", 0)))
    return score, sim, why


def link_places(
    client: HttpClient,
    groups: pd.DataFrame,
    *,
    per_term: int = 8,
    max_terms: int = 3,
    progress: bool = False,
    known: dict[str, set[str]] | None = None,
    fictional: set[str] | frozenset[str] = frozenset(),
) -> tuple[pd.DataFrame, dict[str, dict]]:
    """Best Wikidata match for every place group, with a confidence level.

    Games in ``fictional`` are set in invented worlds that borrow real names:
    their matches are never applied automatically unless Wikidata confirms them.

    ``known`` maps game_id to the QIDs Wikidata itself ties to that game
    ("present in work" / "narrative location"); such candidates get a strong
    bonus, since Wikidata confirms the place is in the game.

    Returns the link table (one row per place group) and the Wikidata summaries
    of all candidates, keyed by QID.
    """
    known = known or {}
    term_lists = [
        [t for t in ts.split("|") if usable_term(t)][:max_terms] for ts in groups["terms"]
    ]
    all_terms = sorted({t for ts in term_lists for t in ts if t})
    if progress:
        print(f"  searching Wikidata for {len(all_terms)} names...", flush=True)
    hits = wikidata.search_many(client, all_terms, limit=per_term)
    qids = sorted({q for qs in hits.values() for q in qs} | {q for v in known.values() for q in v})
    if progress:
        print(f"  fetching {len(qids)} candidate items...", flush=True)
    summ = wikidata.summaries(client, qids)

    scored: list[list[dict]] = []
    for game_id, terms in zip(groups["game_id"], term_lists):
        cands = []
        confirmed = known.get(game_id, set())
        for term in terms:
            for ordinal, q in enumerate(hits.get(term, [])):
                s = summ.get(q)
                if not s:
                    continue
                score, sim, why = _candidate_score(term, ordinal, s)
                if q in confirmed and sim >= 0.6:
                    score += 0.5
                    why = [w for w in why if w == "not a place"]
                cands.append(
                    {
                        "qid": q,
                        "score": score,
                        "sim": sim,
                        "why": why,
                        "term": term,
                        "confirmed": q in confirmed,
                    }
                )
        best: dict[str, dict] = {}
        for c in cands:
            if c["qid"] not in best or c["score"] > best[c["qid"]]["score"]:
                best[c["qid"]] = c
        scored.append(sorted(best.values(), key=lambda c: c["score"], reverse=True))

    # Geographic coherence: where do this game's confident matches cluster, and how
    # spread out are they? A Rome-only game (small radius) strongly penalises far
    # candidates; a game spanning continents is not penalised at all.
    # Where is the game set? Its own confident matches, plus the places Wikidata
    # ties to the game (countries aside), locate it once there are enough of them.
    centers: dict[str, tuple[float, float, float]] = {}
    countries: dict[str, set[str]] = {}
    anchors: dict[str, list[tuple[float, float]]] = {}
    tmp = groups.assign(_top=[c[0] if c else None for c in scored])
    for game_id, grp in tmp.groupby("game_id"):
        pts, nations = [], set()
        for q in known.get(game_id, ()):
            s = summ.get(q)
            if not s or s.get("latitude") is None:
                continue
            if COUNTRY.search(s.get("instance_of") or ""):
                nations.add(s["label"])
            elif place_kind(s.get("instance_of") or "", s.get("description") or ""):
                pts.append((s["latitude"], s["longitude"]))  # not regions or seas
        countries[game_id] = nations
        for c in grp["_top"]:
            if c and c["sim"] >= 0.95 and c["score"] >= 0.9 and not c["why"]:
                s = summ[c["qid"]]
                pts.append((s["latitude"], s["longitude"]))
        anchors[game_id] = pts
        if len(pts) >= MIN_ANCHORS:
            centers[game_id] = _robust_center(pts)

    rows = []
    for g, cands in zip(groups.to_dict("records"), scored):
        center = centers.get(g["game_id"])
        for c in cands:
            s = summ[c["qid"]]
            if center and s["latitude"] is not None:
                d = _haversine(center[0], center[1], s["latitude"], s["longitude"])
                c["dist_km"] = round(d)
                r = center[2]
                if r < 400:
                    if d <= max(2 * r, 150):
                        c["score"] += 0.25
                    elif d > max(3 * r, 400):
                        c["score"] -= 0.6
                        c["why"].append("far from the game's other places")
                elif r < 1500:
                    if d <= 2 * r:
                        c["score"] += 0.1
                    elif d > 6 * r:
                        c["score"] -= 0.3
        cands.sort(key=lambda c: c["score"], reverse=True)
        top = cands[0] if cands else None
        if top:
            s = summ[top["qid"]]
            namesakes = [c for c in cands if c["sim"] >= 0.9]
            near = center is not None and top.get("dist_km", 1e9) <= max(2 * center[2], 150)
            fame = s.get("sitelinks", 0)
            others = max((summ[c["qid"]].get("sitelinks", 0) for c in namesakes[1:]), default=0)
            dominant = fame >= 20 and fame >= 3 * max(1, others)
            common, rival = name_signals(top["term"], hits.get(top["term"], []), summ)
            compact = center is not None and center[2] < COMPACT_KM
            far = (
                compact
                and top.get("dist_km", 0) > max(3 * center[2], 150)
                and _isolated(s, anchors.get(g["game_id"], []))
            )
            wide = center is None or center[2] >= WIDE_KM
            nations = countries.get(g["game_id"], set())
            abroad = bool(nations) and not (set(_split(s.get("country", ""))) & nations)
            if top.get("confirmed"):
                pass  # Wikidata says this place is in this game
            elif g["game_id"] in fictional:
                top["why"].append("the game's world is fictional")
            elif common and fame < 20:
                top["why"].append("common noun, not a proper name")
            elif all_generic(top["term"]) and fame < GENERIC_MIN_FAME:
                top["why"].append("generic name")
            elif rival >= max(10, 3 * fame):
                top["why"].append("the name usually means something else")
            elif top["sim"] < 0.95 and fame < 10 and _extra_words(top["term"], s["label"]):
                # "Howler Monkey" is not the "Howler Monkey Hotel" in Costa Rica.
                top["why"].append("the real name has extra words")
            elif far and fame < WORLD_FAMOUS:
                # A game set in one city or region: even a well-known namesake far
                # outside it is wrong.
                top["why"].append("outside the game's usual area")
            elif len(namesakes) >= 2 and not (near or dominant):
                top["why"].append("several places share this name")
            elif center is not None and not near and not dominant and not wide:
                top["why"].append("outside the game's usual area")
            elif wide and abroad and fame < 30:
                top["why"].append("outside the countries Wikidata gives for the game")
            elif wide and fame < 15:
                # Games set all over the world: only well-known places pass unaided.
                top["why"].append("little-known namesake in a worldwide game")
            conf = (
                "high"
                if top["sim"] >= 0.85 and top["score"] >= 1.1 and not top["why"]
                else "medium"
                if top["sim"] >= 0.6 and top["score"] >= 0.85 and set(top["why"]) <= _SOFT
                else "low"
            )
        else:
            s, conf = {}, "none"
        rows.append(
            {
                "game_id": g["game_id"],
                "place": g["place"],
                "n_images": g["n_images"],
                "confidence": conf,
                "decision": "",
                "wikidata_qid": top["qid"] if top else "",
                "label": s.get("label", ""),
                "description": s.get("description", ""),
                "instance_of": s.get("instance_of", ""),
                "country": s.get("country", ""),
                "latitude": s.get("latitude", ""),
                "longitude": s.get("longitude", ""),
                "commons_image": s.get("image", ""),
                "matched_term": top["term"] if top else "",
                "similarity": round(top["sim"], 2) if top else "",
                "score": round(top["score"], 2) if top else "",
                "distance_km": top.get("dist_km", "") if top else "",
                "issues": "; ".join(dict.fromkeys(top["why"])) if top else "no candidate",
                "wikidata_confirms": "yes" if top and top.get("confirmed") else "",
                "alternatives": " | ".join(
                    f"{c['qid']} {summ[c['qid']]['label']} ({summ[c['qid']]['description'][:40]})"
                    for c in cands[1:4]
                ),
                "image_ids": g["image_ids"],
            }
        )
    return pd.DataFrame(rows), summ


def _site_id(label: str, qid: str, taken: dict[str, str]) -> str:
    base = slugify(label) or qid.lower()
    if taken.get(base, qid) == qid:
        return base
    return f"{base}-{qid.lower()}"


def depiction_id_for(game_id: str, place: str) -> str:
    return f"{game_id}--{slugify(place)[:60]}"


def _accepted(r: dict, summ: dict, levels: tuple[str, ...], architecture_only: bool) -> bool:
    """Whether ``apply_links`` applies this sheet row (see its docstring)."""
    decision = str(r.get("decision", "")).strip().lower()
    if decision == "drop" or not r["wikidata_qid"]:
        return False
    if decision == "keep":
        return True
    if architecture_only:
        s0 = summ.get(r["wikidata_qid"], {})
        inst = s0.get("instance_of", r.get("instance_of", ""))
        desc = s0.get("description", r.get("description", ""))
        if not is_built(inst, desc):
            return False  # regions, people and works are not collected
    clean_medium = r["confidence"] == "medium" and not str(r.get("issues", "")).strip()
    return r["confidence"] in levels or (clean_medium and "medium-clean" in levels)


def _undo_withdrawn(tables: Tables, rejected: list[dict]) -> tuple[Tables, int]:
    """Remove machine links that a newer, stricter matching run no longer accepts.

    Only depictions the matcher created ("Auto-linked" basis) and nobody has
    reviewed are removed; a reviewer's ``keep`` or edits are never undone. Their
    images lose ``depiction_id``/``site_id`` where these still point at the link.
    """
    if not rejected or not len(tables.depictions):
        return tables, 0
    deps = tables.depictions
    auto = deps[
        deps["basis_note"].str.startswith("Auto-linked") & deps["review_status"].isin(["", "todo"])
    ]
    gone = {depiction_id_for(r["game_id"], r["place"]) for r in rejected}
    gone &= set(auto["depiction_id"])
    if not gone:
        return tables, 0
    site_of = dict(zip(deps["depiction_id"], deps["site_id"]))
    imgs = tables.images.copy()
    linked = imgs["depiction_id"].isin(gone)
    same_site = imgs["site_id"] == imgs["depiction_id"].map(site_of).fillna("")
    imgs.loc[linked & same_site, "site_id"] = ""
    imgs.loc[linked, "depiction_id"] = ""
    tables.images = imgs
    tables.depictions = deps[~deps["depiction_id"].isin(gone)].reset_index(drop=True)
    pairs = tables.pairs  # machine-made pairs of the withdrawn link go too
    unreviewed = pairs["review_status"].isin(["", "todo"])
    tables.pairs = pairs[~(pairs["depiction_id"].isin(gone) & unreviewed)].reset_index(drop=True)
    return tables, len(gone)


def prune_orphan_sites(tables: Tables, project_root: str | Path) -> tuple[Tables, int, int]:
    """Remove sites no in-game depiction points at any more, with the real photos
    the package downloaded for them (rows, files and pairs). Returns the tables,
    the number of sites and the number of photo files removed."""
    deps = tables.depictions
    used = set(deps.loc[deps["review_status"] != "drop", "site_id"])
    used |= set(tables.images.loc[tables.images["role"] == "game", "site_id"])
    orphan = set(tables.sites["site_id"]) - used - {""}
    if not orphan:
        return tables, 0, 0
    imgs = tables.images
    photos = imgs[
        (imgs["role"] == "real")
        & imgs["site_id"].isin(orphan)
        & imgs["collected_by"].str.startswith("gameheritage")
    ]
    removed = 0
    for p in photos["local_path"]:
        f = Path(project_root) / p if p else None
        if f and f.exists():
            f.unlink()
            removed += 1
            with contextlib.suppress(OSError):
                f.parent.rmdir()
    gone_imgs = set(photos["image_id"])
    tables.images = imgs[~imgs["image_id"].isin(gone_imgs)].reset_index(drop=True)
    gone_deps = set(deps.loc[deps["site_id"].isin(orphan), "depiction_id"])
    pairs = tables.pairs
    tables.pairs = pairs[
        ~pairs["real_image_id"].isin(gone_imgs) & ~pairs["depiction_id"].isin(gone_deps)
    ].reset_index(drop=True)
    tables.depictions = deps[~deps["depiction_id"].isin(gone_deps)].reset_index(drop=True)
    tables.sites = tables.sites[~tables.sites["site_id"].isin(orphan)].reset_index(drop=True)
    return tables, len(orphan), removed


def _undo_dropped(tables: Tables, links: pd.DataFrame) -> tuple[Tables, int]:
    """Remove links created for rows now marked ``drop``.

    The depiction is marked ``drop`` (kept for the record) and its images lose
    their ``depiction_id``/``site_id``, but only where they still point at the
    dropped match, so later human re-links are left alone.
    """
    dropped = links[links["decision"].astype(str).str.strip().str.lower() == "drop"]
    if not len(dropped):
        return tables, 0
    deps = tables.depictions.set_index("depiction_id", drop=False)
    imgs = tables.images.copy()
    n = 0
    for r in dropped.to_dict("records"):
        dep_id = depiction_id_for(r["game_id"], r["place"])
        if dep_id not in deps.index:
            continue
        site_id = deps.at[dep_id, "site_id"]
        deps.at[dep_id, "review_status"] = "drop"
        mask = (imgs["depiction_id"] == dep_id) & (imgs["site_id"] == site_id)
        imgs.loc[mask, ["depiction_id", "site_id"]] = ""
        n += 1
    tables.depictions = deps.reset_index(drop=True)
    tables.images = imgs
    return tables, n


def merge_decisions(new: pd.DataFrame, old: pd.DataFrame) -> pd.DataFrame:
    """Carry earlier review decisions (and hand-corrected QIDs) into a fresh link sheet."""
    if old is None or not len(old) or "decision" not in old:
        return new
    old = old[old["decision"].astype(str).str.strip() != ""]
    keep = {
        (r["game_id"], r["place"]): (r["decision"], r["wikidata_qid"])
        for r in old.to_dict("records")
    }
    out = new.copy()
    for i, r in out.iterrows():
        prev = keep.get((r["game_id"], r["place"]))
        if prev:
            out.at[i, "decision"] = prev[0]
            if prev[0] == "keep" and prev[1]:
                out.at[i, "wikidata_qid"] = prev[1]
    return out


def apply_links(
    tables: Tables,
    links: pd.DataFrame,
    summ: dict[str, dict],
    *,
    levels: tuple[str, ...] = ("high",),
    architecture_only: bool = True,
) -> tuple[Tables, int]:
    """Create sites and depictions from accepted links and attach their images.

    Rows with ``decision == "keep"`` are always applied; rows with a blank
    decision are applied when their confidence is in ``levels`` (the level
    ``"medium-clean"`` admits medium matches that have no listed issues);
    ``drop`` rows never are. With ``architecture_only`` (the default) only matches
    that are built places (see ``is_built``) are applied automatically; a person's
    ``keep`` always applies. Existing rows and human edits are not overwritten.
    """
    today = date.today().isoformat()
    tables, _undone = _undo_dropped(tables, links)
    records = links.to_dict("records")
    ok = [_accepted(r, summ, levels, architecture_only) for r in records]
    tables, _withdrawn = _undo_withdrawn(tables, [r for r, a in zip(records, ok) if not a])
    taken = dict(zip(tables.sites["site_id"], tables.sites["wikidata_qid"]))
    by_qid = {q: s for s, q in taken.items() if q}
    sites, deps, imgs = [], [], []
    n = 0
    for r, accepted in zip(records, ok):
        if not accepted:
            continue
        q = r["wikidata_qid"]
        s = summ.get(q, {})
        site_id = by_qid.get(q) or _site_id(s.get("label") or r["label"], q, taken)
        taken[site_id], by_qid[q] = q, site_id
        sites.append(
            {
                "site_id": site_id,
                "name": s.get("label") or r["label"],
                "wikidata_qid": q,
                "country": s.get("country", ""),
                "locality": s.get("located_in", ""),
                "latitude": s.get("latitude"),
                "longitude": s.get("longitude"),
                "construction_date": s.get("inception_year"),
                "architectural_style": s.get("style", ""),
                "heritage_designation": s.get("heritage", ""),
                "commons_category": s.get("commons_category", ""),
                "wikipedia_url": s.get("wikipedia_url", ""),
                "facts_source": f"Wikidata {wikidata.ENTITY_URL.format(q)} (retrieved {today})",
                "notes": f"type: {s.get('instance_of', '')}"[:200],
            }
        )
        dep_id = depiction_id_for(r["game_id"], r["place"])
        exact = float(r["similarity"] or 0) >= 0.9
        deps.append(
            {
                "depiction_id": dep_id,
                "game_id": r["game_id"],
                "site_id": site_id,
                "in_game_name": r["place"],
                "depiction_class": 1 if exact else "",
                "basis_note": (
                    f"Auto-linked: in-game name '{r['place']}' matched Wikidata '{s.get('label')}' "
                    f"({r['confidence']} confidence, score {r['score']})"
                ),
                "basis_source": wikidata.ENTITY_URL.format(q),
            }
        )
        for iid in filter(None, str(r["image_ids"]).split(";")):
            imgs.append({"image_id": iid, "depiction_id": dep_id, "site_id": site_id})
        n += 1
    tables.sites = upsert(tables.sites, sites, "site_id")
    tables.depictions = upsert(tables.depictions, deps, "depiction_id")
    tables.images = upsert(tables.images, imgs, "image_id")
    return tables, n


def real_structures(
    client: HttpClient, names: list[str], *, min_sim: float = 0.85
) -> dict[str, dict]:
    """Which names match a real built structure on Wikidata.

    A name passes when one of its search results has coordinates, is a kind of
    structure (building, monument, fortification, religious site, bridge, square,
    archaeological site...), is not a person or a work, and has a label at least
    ``min_sim`` similar to the name. Returns name -> best match summary.
    """
    usable = sorted({n for n in names if usable_term(n)})
    hits = wikidata.search_many(client, usable, limit=5)
    summ = wikidata.summaries(client, sorted({q for qs in hits.values() for q in qs}))
    out: dict[str, dict] = {}
    for n in usable:
        best, best_key = None, None
        common, rival = name_signals(n, hits.get(n, []), summ)
        for q in hits.get(n, []):
            s = summ.get(q)
            if not s or s["latitude"] is None:
                continue
            if not is_built(s["instance_of"], s["description"]):
                continue
            if BAD_TYPES.search(s["instance_of"]) and not STRUCTURE_TYPES.search(s["instance_of"]):
                continue
            sim = similarity(n, s["label"])
            if sim < min_sim:
                continue
            key = (sim, s.get("sitelinks", 0))
            if best_key is None or key > best_key:
                best, best_key = s, key
        if best:
            fame = int(best.get("sitelinks") or 0)
            if common and fame < 20:
                continue  # "Grand Staircase", "Squash Court": a kind of room, not a place
            if all_generic(n) and fame < GENERIC_MIN_FAME:
                continue  # "Upper Town": only a well-known place passes
            if rival >= max(10, 3 * fame):
                continue  # "Rollo" the Viking, "Mercury" the planet
            out[n] = best
    return out
