"""Flag problem files in the raw collection. Nothing is deleted: flags go to review.

Flags (semicolon-separated in the ``flags`` column):

==============================  =====================================================
flag                            meaning
==============================  =====================================================
empty_file                      0 bytes
unreadable                      not a decodable image
placeholder                     a "real image" smaller than 5 KB (error page, stub)
extension_mismatch              e.g. a WebP file named ``.jpg``
icon_like                       longest side <= 256 px, or < 8 KB without dimensions
animated                        more than one frame
other_media                     audio/video/vector file (.ogg, .svg, ...)
no_extension                    the name has no usable extension
exact_duplicate                 same bytes as an earlier file (see ``duplicate_of``)
near_duplicate                  perceptual hash within 4 bits of an earlier image
copy_suffix                     name ends with " (1)" etc.
size_twin                       same size and extension in the same game (listing mode)
same_file_different_subjects    one real-image file saved under different subjects
real_reused_across_games        the same real image in several game folders
misfiled_in_real_folder         a non-photo (icon, game asset) in ``real images``
real_unsourced                  real image with no recorded source or license
==============================  =====================================================
"""

from __future__ import annotations

import re
from collections import defaultdict

import pandas as pd

from .imagefile import extension_mismatch, phash_distance

ICON_MAX_SIDE = 256
ICON_MAX_BYTES = 8_000
PLACEHOLDER_MAX_BYTES = 5_000
NEAR_DUP_BITS = 4
_COPY_RE = re.compile(r"\(\d+\)\.[A-Za-z]+$")


def _int(x: str) -> int | None:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def flag_manifest(manifest: pd.DataFrame) -> pd.DataFrame:
    """Return the manifest with ``flags`` and ``duplicate_of`` columns added."""
    m = manifest.copy().reset_index(drop=True)
    flags: list[list[str]] = [[] for _ in range(len(m))]
    dup_of = [""] * len(m)

    for i, r in m.iterrows():
        size = _int(r["bytes"])
        w, h = _int(r["width"]), _int(r["height"])
        is_real_area = r["area"] == "real images"
        if size == 0:
            flags[i].append("empty_file")
        if r["media"] == "other_media":
            flags[i].append("other_media")
        if not r["ext"]:
            flags[i].append("no_extension")
        if r["sha256"] and r["readable"] == "False" and size:
            flags[i].append("unreadable")
        if r["true_format"] and extension_mismatch(r["filename"], r["true_format"]):
            flags[i].append("extension_mismatch")
        orig = r["original_ext"].replace("jpeg", "jpg")
        ext = r["ext"].lstrip(".").replace("jpeg", "jpg")
        if orig and ext and orig != ext and not r["true_format"]:
            flags[i].append("extension_mismatch")
        if _int(r["n_frames"] or "1") and (_int(r["n_frames"] or "1") or 1) > 1:
            flags[i].append("animated")
        longest = max(w or 0, h or 0)
        if (longest and longest <= ICON_MAX_SIDE) or (
            not longest and size is not None and 0 < size < ICON_MAX_BYTES
        ):
            flags[i].append("icon_like")
        if is_real_area and size is not None and 0 < size < PLACEHOLDER_MAX_BYTES:
            flags[i].append("placeholder")
        if _COPY_RE.search(r["filename"]):
            flags[i].append("copy_suffix")
        if is_real_area and r["pattern"] != "real_numbered":
            flags[i].append("misfiled_in_real_folder")
        if is_real_area and r["pattern"] == "real_numbered":
            flags[i].append("real_unsourced")

    # Exact duplicates by content hash (first path in sort order is the original).
    order = m.sort_values("rel_path").index
    first_by_sha: dict[str, int] = {}
    for i in order:
        sha = m.at[i, "sha256"]
        if not sha or m.at[i, "media"] != "image":
            continue
        if sha in first_by_sha:
            flags[i].append("exact_duplicate")
            dup_of[i] = m.at[first_by_sha[sha], "rel_path"]
        else:
            first_by_sha[sha] = i

    # Listing mode: same size + extension within a game is a strong duplicate hint.
    has_hash = m["sha256"] != ""
    for _, grp in m[~has_hash & (m["media"] == "image")].groupby(["game_folder", "bytes", "ext"]):
        if len(grp) > 1 and grp["bytes"].iloc[0] not in ("", "0"):
            idx = list(grp.sort_values("rel_path").index)
            for j in idx[1:]:
                flags[j].append("size_twin")
                dup_of[j] = m.at[idx[0], "rel_path"]

    # Near duplicates within a game, by perceptual hash.
    for _, grp in m[(m["phash"] != "") & (m["media"] == "image")].groupby("game_folder"):
        items = [(i, m.at[i, "phash"]) for i in grp.sort_values("rel_path").index]
        for a in range(len(items)):
            ia, ha = items[a]
            if "exact_duplicate" in flags[ia] or "near_duplicate" in flags[ia]:
                continue
            for b in range(a + 1, len(items)):
                ib, hb = items[b]
                if "exact_duplicate" in flags[ib] or "near_duplicate" in flags[ib]:
                    continue
                if phash_distance(ha, hb) <= NEAR_DUP_BITS:
                    flags[ib].append("near_duplicate")
                    dup_of[ib] = m.at[ia, "rel_path"]

    # Real images: same content (or same name + size) under different subjects or games.
    real = m[(m["area"] == "real images") & (m["pattern"] == "real_numbered")]
    key = real.apply(
        lambda r: r["sha256"] or f"{r['bytes']}|{r['ext']}" if r["bytes"] else "", axis=1
    )
    groups: dict[str, list[int]] = defaultdict(list)
    for i, k in key.items():
        if k:
            groups[k].append(i)
    for idx in groups.values():
        if len(idx) < 2:
            continue
        subjects = {m.at[i, "real_subject"].lower() for i in idx}
        games = {m.at[i, "game_folder"] for i in idx}
        for i in idx:
            if len(subjects) > 1:
                flags[i].append("same_file_different_subjects")
            if len(games) > 1:
                flags[i].append("real_reused_across_games")

    m["flags"] = [";".join(dict.fromkeys(f)) for f in flags]
    m["duplicate_of"] = dup_of
    return m.astype("string").fillna("")


DROP_SUGGESTING = {
    "empty_file",
    "unreadable",
    "placeholder",
    "exact_duplicate",
    "icon_like",
    "other_media",
}


def suggestion(flags: str) -> str:
    """Machine suggestion for the triage sheet: ``drop``, ``check`` or ``keep``."""
    fs = set(filter(None, flags.split(";")))
    if fs & DROP_SUGGESTING:
        return "drop"
    if fs & {
        "near_duplicate",
        "size_twin",
        "same_file_different_subjects",
        "misfiled_in_real_folder",
        "copy_suffix",
    }:
        return "check"
    return "keep"


def summarize(flagged: pd.DataFrame) -> pd.DataFrame:
    """Counts of each flag per game folder."""
    rows = []
    for game, grp in flagged.groupby("game_folder"):
        counts: dict[str, int] = defaultdict(int)
        for f in grp["flags"]:
            for x in filter(None, f.split(";")):
                counts[x] += 1
        rows.append({"game_folder": game, "files": len(grp), **counts})
    return pd.DataFrame(rows).fillna(0)


def dedupe_images(tables) -> tuple[object, int]:
    """Mark duplicate images ``drop`` (one copy of each image is kept).

    Duplicates are images with the same content hash, the same original file on
    the source wiki, or (when contents were not read) the same game, size and
    extension where one name is a copy (" (1)") or flagged as a size twin. The
    copy kept is the first by path. Only rows still ``todo`` are changed, and
    each dropped row notes which image it duplicates.
    """
    imgs = tables.images.copy()
    imgs["_order"] = imgs["drive_path"].where(imgs["drive_path"] != "", imgs["local_path"])
    imgs = imgs.sort_values("_order", kind="stable")
    live = imgs["review_status"] != "drop"
    dup_of: dict[str, tuple[str, str]] = {}

    def mark(groups, basis: str) -> None:
        for _, grp in groups:
            ids = [i for i in grp["image_id"] if i not in dup_of]
            for i in ids[1:]:
                dup_of[i] = (ids[0], basis)

    has = lambda c: imgs[live & (imgs[c] != "")]  # noqa: E731
    mark(has("sha256").groupby("sha256"), "same file content")
    mark(has("file_url").groupby("file_url"), "same original file on the source wiki")
    game = has("bytes")
    game = game[(game["role"] == "game") & (game["sha256"] == "")]
    game = game.assign(_ext=game["original_filename"].str.lower().str.extract(r"(\.\w+)$")[0])
    for _, grp in game.groupby(["game_id", "bytes", "_ext"]):
        if len(grp) < 2:
            continue
        hinted = grp["flags"].str.contains("size_twin|copy_suffix", regex=True)
        if hinted.any():
            mark([(None, grp)], "same game, size and type")
    real = has("bytes")
    real = real[(real["role"] == "real") & (real["sha256"] == "")]
    mark(real.groupby(["bytes", "original_filename"]), "same size and name")
    mark(real.groupby("bytes"), "same size")
    imgs = imgs.drop(columns="_order").set_index("image_id", drop=False)
    n = 0
    for iid, (orig, basis) in dup_of.items():
        if imgs.at[iid, "review_status"] not in ("todo", ""):
            continue
        imgs.at[iid, "review_status"] = "drop"
        note = f"duplicate of {orig} ({basis})"
        old = imgs.at[iid, "notes"]
        imgs.at[iid, "notes"] = f"{old}; {note}" if old else note
        n += 1
    tables.images = imgs.reset_index(drop=True)
    return tables, n
