"""Command-line interface for the curation pipeline.

Run ``gameheritage --help`` for the list of steps. Every step reads
``gameheritage.toml`` (searched from the current folder upwards), loads the
tables from ``data/``, does its work and saves the tables again.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

from . import __version__
from .config import Settings, load_settings
from .tables import Tables, upsert

MANIFEST = "raw_manifest.csv"


def _client(s: Settings):
    from .sources.http import HttpClient

    return HttpClient(contact=s.contact, cache_dir=s.cache_dir, min_interval=s.min_interval)


def _manifest(s: Settings) -> pd.DataFrame:
    p = s.data_dir / MANIFEST
    if not p.exists():
        sys.exit(f"No manifest at {p}. Run `gameheritage scan` first.")
    return pd.read_csv(p, dtype=str, keep_default_na=False).astype("string").fillna("")


def _save_manifest(s: Settings, df: pd.DataFrame) -> Path:
    p = s.data_dir / MANIFEST
    p.parent.mkdir(parents=True, exist_ok=True)
    df.sort_values("rel_path").to_csv(p, index=False, encoding="utf-8", lineterminator="\n")
    return p


# --------------------------------------------------------------------------- steps


def cmd_scan(s: Settings, a) -> None:
    from .clean import flag_manifest
    from .ingest import scan_directory, scan_listing

    prev_path = s.data_dir / MANIFEST
    prev = pd.read_csv(prev_path, dtype=str, keep_default_na=False) if prev_path.exists() else None
    if a.listing:
        new = scan_listing(a.listing)
    else:
        root = Path(a.root) if a.root else s.collection_root
        print(f"Scanning {root} (read-only)...")
        new = scan_directory(root, games=a.games or None, previous=prev, progress=True)
    if prev is not None and len(prev):
        if a.listing:
            # A listing never overwrites content facts already hashed from real files.
            hashed = prev[prev["sha256"] != ""]
            new = new[~new["rel_path"].isin(hashed["rel_path"])]
        # Keep rows for files not in this scan; replace scanned ones.
        prev = prev[~prev["rel_path"].isin(new["rel_path"])]
        if not a.listing:
            scanned = set(new["game_folder"])
            prev = prev[~prev["game_folder"].isin(scanned)]
        new = pd.concat([prev.astype("string"), new], ignore_index=True)
    new = flag_manifest(new.drop(columns=["flags", "duplicate_of"], errors="ignore"))
    p = _save_manifest(s, new)
    hashed = int((new["sha256"] != "").sum())
    print(f"Manifest: {len(new)} files ({hashed} content-hashed) -> {p}")


def cmd_register(s: Settings, a) -> None:
    from .ingest import register_images

    t = Tables.load(s.data_dir)
    m = _manifest(s)
    kwargs = {}
    if s.collector:
        kwargs["collected_by"] = s.collector
    if s.collection_method:
        kwargs["method"] = s.collection_method
    t, unmapped = register_images(m, t, **kwargs)
    t = apply_manifest_flags(t, m)
    t.save(s.data_dir)
    print(f"images.csv: {len(t.images)} images")
    if unmapped:
        print("Folders without a row in folder_aliases.csv (add them):")
        for f in unmapped:
            print(f"  {f}")


def apply_manifest_flags(t: Tables, m: pd.DataFrame) -> Tables:
    """Copy each file's flags onto its image row (union over all copies)."""
    flags_by_path = dict(zip(m["rel_path"], m["flags"]))
    rows = []
    for r in t.images.to_dict("records"):
        paths = [r["drive_path"], *filter(None, r["also_at"].split(";"))]
        fs = [f for p in paths for f in flags_by_path.get(p, "").split(";") if f]
        if r["also_at"]:
            fs.append("has_copies")
        if fs:
            rows.append({"image_id": r["image_id"], "flags": ";".join(dict.fromkeys(fs))})
    t.images = upsert(t.images, rows, "image_id", overwrite=True)
    return t


def cmd_flags(s: Settings, a) -> None:
    from .clean import summarize

    m = _manifest(s)
    summ = summarize(m)
    with pd.option_context("display.max_rows", 200, "display.width", 200):
        print(summ.to_string(index=False))


def cmd_fandom_sources(s: Settings, a) -> None:
    """Find each Fandom-scraped game image on its wiki and record its source page."""
    from .sources import fandom
    from .sources.fandom import wiki_from_cdn_path

    t = Tables.load(s.data_dir)
    m = _manifest(s)
    client = _client(s)
    wikis = dict(zip(t.games["game_id"], t.games["fandom_wiki"]))
    m1 = m.drop_duplicates("rel_path").set_index("rel_path")
    items: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    file_keys: dict[str, dict[str, str]] = defaultdict(dict)
    page_keys: dict[str, dict[str, str]] = defaultdict(dict)
    for r in t.images[t.images["role"] == "game"].to_dict("records"):
        if a.games and r["game_id"] not in a.games:
            continue
        if r["drive_path"] not in m1.index or (r["file_url"] and not a.refresh):
            continue
        p = m1.loc[r["drive_path"]]
        wiki = wiki_from_cdn_path(p["fandom_wiki_path"]) or wikis.get(r["game_id"], "")
        if not wiki:
            continue
        if p["fandom_key"]:
            file_keys[wiki][p["fandom_key"]] = p["label"]
            items[wiki].append((r["image_id"], "file", p["fandom_key"]))
        elif p["page_key"]:
            page_keys[wiki][p["page_key"]] = p["page_title"]
            items[wiki].append((r["image_id"], "page", p["page_key"]))
    from .sources.http import HttpError

    total = 0
    for wiki in items:
        rows = []
        try:
            fmap = fandom.resolve_titles(client, wiki, file_keys[wiki], files=True)
            pmap = fandom.resolve_titles(client, wiki, page_keys[wiki], files=False)
            pimg = fandom.page_images(client, wiki, list(set(pmap.values()))) if pmap else {}
            names = list(set(fmap.values()) | {v for v in pimg.values() if v})
            info = fandom.file_info(client, wiki, names) if names else {}
        except HttpError as exc:
            print(f"  {wiki}: skipped ({exc})")
            continue
        info = {k.replace("_", " ").lower(): v for k, v in info.items()}
        found = 0
        for iid, how, key in items[wiki]:
            page_title = pmap.get(key, "") if how == "page" else ""
            fname = pimg.get(page_title, "") if how == "page" else fmap.get(key, "")
            meta = info.get(fname.replace("_", " ").lower()) if fname else None
            if not meta or meta.get("missing"):
                if page_title:
                    rows.append(
                        {
                            "image_id": iid,
                            "source_name": f"Fandom ({wiki})",
                            "source_page_url": fandom.page_url(wiki, page_title),
                        }
                    )
                continue
            found += 1
            note = f"Fandom upload by {meta['uploader']} on {meta['uploaded_at'][:10]}"
            if page_title:
                note += f"; main image of page {fandom.page_url(wiki, page_title)}"
            if meta.get("license"):
                note += f"; wiki file page states: {meta['license']}"
            rows.append(
                {
                    "image_id": iid,
                    "source_name": f"Fandom ({wiki})",
                    "source_page_url": meta["page_url"],
                    "file_url": meta["file_url"],
                    "notes": note,
                }
            )
        print(f"  {wiki}: {found}/{len(items[wiki])} files found on the wiki", flush=True)
        # Machine-owned fields only; --refresh may replace earlier lookups.
        t.images = upsert(t.images, rows, "image_id", overwrite=a.refresh)
        t.save(s.data_dir)  # save per wiki, so an interruption loses little
        total += len(rows)
    print(f"Updated source records for {total} game images ({client.requests_made} requests)")


def cmd_autolink(s: Settings, a) -> None:
    """Match in-game places to Wikidata and attach confident matches."""
    import json

    from .autolink import apply_links, link_places, place_groups
    from .review import write_sheet

    t = Tables.load(s.data_dir)
    sheet = Path(a.sheet) if a.sheet else s.review_dir / "auto_links.csv"
    cache = s.cache_dir / "autolink_summaries.json"
    if a.from_sheet:
        links = pd.read_csv(sheet, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        summ = json.loads(cache.read_text("utf-8")) if cache.exists() else {}
        # Q-numbers typed by hand need their own labels and facts.
        missing = [
            q
            for q, d in zip(links["wikidata_qid"], links["decision"])
            if q and d.strip().lower() == "keep" and q not in summ
        ]
        if missing:
            from .sources import wikidata

            summ.update(wikidata.summaries(_client(s), missing))
    else:
        from .link import auto_link_games
        from .sources import wikidata
        from .tables import commit

        client = _client(s)
        made = {}

        def games_step(tt: Tables) -> None:
            _, made["n"] = auto_link_games(client, tt)

        t = commit(s.data_dir, games_step)
        print(f"Linked {made['n']} more games to Wikidata")
        gq = {g: q for g, q in zip(t.games["game_id"], t.games["wikidata_qid"]) if q}
        in_work = wikidata.places_in_works(client, list(gq.values()))
        known = {g: in_work.get(q, set()) for g, q in gq.items()}
        print(f"Wikidata ties {sum(map(len, known.values()))} places to {len(gq)} games")
        groups = place_groups(t, _manifest(s))
        if a.games:
            groups = groups[groups["game_id"].isin(a.games)]
        print(f"{len(groups)} place groups covering {int(groups['n_images'].sum())} images")
        links, summ = link_places(
            client, groups, progress=True, known=known, fictional=set(s.fictional_worlds)
        )
        old = summ
        if cache.exists():
            old = {**json.loads(cache.read_text("utf-8")), **summ}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(old), "utf-8")
        from .autolink import merge_decisions

        prev = None
        if sheet.exists():
            prev = pd.read_csv(sheet, dtype=str, keep_default_na=False, encoding="utf-8-sig")
        links = merge_decisions(links.astype(str), prev)  # earlier review decisions survive
        if a.games and prev is not None:
            rest = prev[~prev["game_id"].isin(a.games)]
            links = pd.concat([rest, links], ignore_index=True)
        write_sheet(links, sheet)
        print(f"Wrote {sheet}: " + str(links["confidence"].value_counts().to_dict()))
        if a.no_apply:
            return
    from .tables import commit

    applied = {}

    def apply_step(tt: Tables) -> None:
        from .autolink import prune_orphan_sites

        _, applied["n"] = apply_links(tt, links, summ, levels=tuple(a.levels))
        _, applied["orphans"], applied["files"] = prune_orphan_sites(tt, s.project_root)

    t = commit(s.data_dir, apply_step)  # safe alongside running harvests
    print(f"Applied {applied['n']} links: {len(t.sites)} sites, {len(t.depictions)} depictions")
    if applied["orphans"]:
        print(
            f"Removed {applied['orphans']} sites no game image points at any more, "
            f"with {applied['files']} photo files"
        )


def cmd_real_images(s: Settings, a) -> None:
    """Download photos of linked sites, then pair game and real images."""
    from .groundtruth import make_pairs, real_image_rows
    from .tables import commit

    def save_rows(rows: list[dict]) -> None:
        commit(s.data_dir, lambda t: setattr(t, "images", upsert(t.images, rows, "image_id")))

    if not a.pairs_only:
        client = _client(s)
        snapshot = Tables.load(s.data_dir)
        site_ids = a.sites or list(
            snapshot.sites.loc[snapshot.sites["wikidata_qid"] != "", "site_id"]
        )
        if a.limit:
            site_ids = site_ids[: a.limit]
        total, no_image = 0, 0
        # Work in chunks of 50 sites so photos appear (and are saved) as they arrive.
        for start in range(0, len(site_ids), 50):
            chunk = site_ids[start : start + 50]
            snapshot = Tables.load(s.data_dir)
            rows, msgs = real_image_rows(
                client,
                snapshot,
                s.images_root,
                project_root=s.project_root,
                thumb_width=a.width,
                site_ids=chunk,
                extra_per_site=a.extra,
                free_only=a.free_only,
                checkpoint=save_rows,
                read_licenses=not a.no_license_lookup,
            )
            total += len(rows)
            no_image += sum(m.startswith("no Wikidata") for m in msgs)
            print(
                f"sites {start + len(chunk)}/{len(site_ids)}: {total} photos so far",
                flush=True,
            )
        print(f"downloaded {total} real images; {no_image} sites have no Wikidata image")
    made = {}

    def pair(t: Tables) -> None:
        _, made["n"] = make_pairs(t)

    t = commit(s.data_dir, pair)
    print(f"{made['n']} new pairs; {len(t.pairs)} pairs in total")


def cmd_licenses(s: Settings, a) -> None:
    from .groundtruth import backfill_licenses
    from .tables import commit

    client = _client(s)
    out = {}

    def run(t: Tables) -> None:
        _, out["n"] = backfill_licenses(client, t)

    commit(s.data_dir, run)
    print(f"Recorded author and license for {out['n']} images")


def cmd_dedupe(s: Settings, a) -> None:
    """Drop duplicate images (one copy of each is kept)."""
    from .clean import dedupe_images
    from .tables import commit

    made = {}

    def run(t: Tables) -> None:
        _, made["n"] = dedupe_images(t)

    t = commit(s.data_dir, run)
    live = int((t.images["review_status"] != "drop").sum())
    print(f"Marked {made['n']} duplicates as drop; {live} distinct images remain")


def cmd_harvest(s: Settings, a) -> None:
    """Expand the collection with location images from a Fandom wiki category."""
    from .harvest import harvest
    from .tables import commit

    if a.title:
        game = {
            "game_id": a.game,
            "title": a.title,
            "series": a.series or "",
            "fandom_wiki": a.wiki,
        }
        commit(s.data_dir, lambda t: setattr(t, "games", upsert(t.games, [game], "game_id")))
    t = Tables.load(s.data_dir)
    dest = Path(a.dest) if a.dest else s.project_root.parent / "harvested_game_images"

    def save_rows(rows: list[dict]) -> None:
        commit(s.data_dir, lambda tt: setattr(tt, "images", upsert(tt.images, rows, "image_id")))

    _t, msgs = harvest(
        _client(s),
        t,
        wiki=a.wiki,
        game_id=a.game,
        category=a.category,
        prefix=a.prefix,
        dest=dest,
        project_root=s.project_root,
        depth=a.depth,
        per_page=a.per_page,
        file_pattern=a.file_pattern,
        exclude_pattern=a.exclude_pattern,
        progress=True,
        checkpoint=save_rows,
        require_structure=not a.any_place,
    )
    print("\n".join(msgs[:20]))


def cmd_prune_harvest(s: Settings, a) -> None:
    from .harvest import prune_files, prune_non_structures, prune_people, prune_unlinked
    from .tables import commit

    out = {}
    log = Path(s.review_dir) / "pruned_harvest.csv"
    if a.unlinked or a.files or a.people:
        ids = None
        if a.people:  # detect first, outside the table update
            from .harvest import find_people
            from .people import available

            if not available():
                sys.exit("face detectors not available: install opencv-python-headless")
            ids = find_people(Tables.load(s.data_dir), s.project_root)

        def run_local(t: Tables) -> None:
            if a.people:
                _, msgs = prune_people(t, s.project_root, log, ids=ids)
            elif a.files:
                _, msgs = prune_files(
                    t, s.project_root, pattern=a.pattern, game_prefix=a.games, log_path=log
                )
            else:
                _, msgs = prune_unlinked(t, s.project_root, log)
            out["msgs"] = msgs

        commit(s.data_dir, run_local)
        print("\n".join(out["msgs"]))
        return
    client = _client(s)

    def run(t: Tables) -> None:
        _, out["msgs"] = prune_non_structures(client, t, s.project_root)

    commit(s.data_dir, run)
    print("\n".join(out["msgs"]))


def cmd_dates(s: Settings, a) -> None:
    """Fill construction dates and historical periods (Wikidata, then description text)."""
    from . import history
    from .tables import commit

    client = _client(s)
    out = {}

    def run(t: Tables) -> Tables:
        t, out["wikidata"] = history.enrich_dates(client, t)
        t, out["text"] = history.dates_from_descriptions(t)
        return t

    commit(s.data_dir, run)
    print(
        f"dates from Wikidata for {out['wikidata']} places, from description text for {out['text']}"
    )


def cmd_website(s: Settings, a) -> None:
    """Build the static website (pages, data and web-size images) into a folder."""
    from . import website

    out = Path(a.out)
    t = Tables.load(s.data_dir)
    sizes_file = out / "data" / "image_sizes.json"
    if not a.data_only:

        def progress(done, total, ok):
            print(f"images {done}/{total}: {ok} ready", flush=True)

        sizes = website.make_images(
            t,
            out,
            s.project_root,
            s.cache_dir,
            client_factory=lambda: _client(s),
            workers=a.workers,
            progress=progress,
        )
        sizes_file.parent.mkdir(parents=True, exist_ok=True)
        sizes_file.write_text(json.dumps(sizes), "utf-8")
    if a.images_only:
        return
    sizes = {k: tuple(v) for k, v in json.loads(sizes_file.read_text("utf-8")).items()}
    counts = website.export_data(t, out, sizes)
    website.copy_template(out)
    print("Website written to", out, counts)


def cmd_review(s: Settings, a) -> None:
    from . import review

    t = Tables.load(s.data_dir)
    if a.action == "export":
        m = _manifest(s)
        if a.queue == "images":
            paths = review.export_image_triage(t, m, s.review_dir, s.collection_root)
        elif a.queue == "places":
            paths = review.export_places(t, m, s.review_dir, s.collection_root)
        else:
            sys.exit(f"export not available for {a.queue}; use `gameheritage {a.queue}`")
        print("Wrote " + " and ".join(str(p) for p in paths))
        return
    if not a.file:
        sys.exit("review import needs a FILE")
    if a.queue == "images":
        t, n = review.import_image_triage(t, a.file)
    elif a.queue == "places":
        t, n = review.import_places(t, a.file)
    elif a.queue == "games":
        from .link import import_game_links

        t, n = import_game_links(t, a.file)
    elif a.queue == "sites":
        from .link import import_site_links

        t, n = import_site_links(t, a.file)
    elif a.queue == "pairs":
        t, n = review.import_decisions(
            t, a.file, "pairs", "pair_id", {"match_level": "match_level", "featured": "featured"}
        )
    else:
        sys.exit(f"unknown queue {a.queue}")
    t.save(s.data_dir)
    print(f"Applied {n} row(s) from {a.file}")


def cmd_link(s: Settings, a) -> None:
    from .link import propose_game_links, propose_site_links, write_link_sheet

    t = Tables.load(s.data_dir)
    client = _client(s)
    if a.what == "games":
        df = propose_game_links(client, t)
        out = s.review_dir / "game_links.csv"
    else:
        df = propose_site_links(client, t)
        out = s.review_dir / "site_links.csv"
    write_link_sheet(df, out)
    print(f"Wrote {len(df)} proposals -> {out}")


def cmd_enrich(s: Settings, a) -> None:
    from .link import enrich_games, enrich_sites

    t = Tables.load(s.data_dir)
    client = _client(s)
    t, n = (enrich_games if a.what == "games" else enrich_sites)(client, t)
    t.save(s.data_dir)
    print(f"Enriched {n} {a.what} from Wikidata")


def cmd_describe(s: Settings, a) -> None:
    from .describe import describe_sites
    from .tables import commit

    out = {}
    if a.in_game:
        from .describe import describe_depictions

        client = _client(s)
        t0 = Tables.load(s.data_dir)
        _, n = describe_depictions(client, t0, sentences=a.sentences, refresh=True)
        rows = t0.depictions[t0.depictions["in_game_description"] != ""].to_dict("records")
        commit(
            s.data_dir,
            lambda t: setattr(
                t, "depictions", upsert(t.depictions, rows, "depiction_id", overwrite=True)
            ),
        )
        print(f"Added {n} in-game descriptions from Fandom")
        return
    t = Tables.load(s.data_dir)
    t, n = describe_sites(_client(s), t, sentences=a.sentences)
    rows = t.sites[t.sites["description"] != ""].to_dict("records")
    commit(s.data_dir, lambda tt: setattr(tt, "sites", upsert(tt.sites, rows, "site_id")))
    out["n"] = n
    print(f"Added {n} descriptions from Wikipedia and Wikidata")


def cmd_groundtruth(s: Settings, a) -> None:
    from . import groundtruth as gt

    t = Tables.load(s.data_dir)
    client = _client(s)
    if a.action == "propose":
        df = gt.propose_candidates(
            client,
            t,
            site_ids=a.sites or None,
            per_site=a.per_site,
            europeana_key=s.europeana_key,
        )
        paths = gt.export_candidates(df, s.review_dir)
        print(f"{len(df)} candidates -> " + " and ".join(str(p) for p in paths))
        return
    sheet = a.file or (s.review_dir / "groundtruth.csv")
    t, msgs = gt.download_approved(
        client,
        t,
        sheet,
        s.images_root,
        project_root=s.project_root,
        use_thumbnail=a.thumbnail,
    )
    t.save(s.data_dir)
    print("\n".join(msgs))


def cmd_validate(s: Settings, a) -> None:
    from .validate import validate

    rep = validate(Tables.load(s.data_dir))
    print(rep)
    if not rep.ok:
        sys.exit(1)


def cmd_credits(s: Settings, a) -> None:
    from .credits import write_credits

    t = Tables.load(s.data_dir)
    out = Path(a.out) if a.out else s.project_root / "credits"
    for p in write_credits(t, out):
        print(f"Wrote {p}")


def cmd_build(s: Settings, a) -> None:
    """Build a dataset release (optionally copied into the package)."""
    import re

    from .build import build_release

    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", a.version) or ".." in a.version:
        sys.exit("--version may contain only letters, digits, '.', '_' and '-'")
    t = Tables.load(s.data_dir)
    pkg = s.project_root / "src" / "gameheritage" / "_release" if a.package else None
    out = build_release(
        t,
        s.release_dir / a.version,
        a.version,
        include_unreviewed=a.preview,
        package_copy=pkg,
        site_dir=a.site,
        site_url=a.site_url,
    )
    print(f"Release {a.version} -> {out}" + (f" (copied into {pkg})" if pkg else ""))


def cmd_web_images(s: Settings, a) -> None:
    from .build import make_web_images, publishable

    t = Tables.load(s.data_dir)
    out = Path(a.out) if a.out else s.project_root / "web_images"
    ids = list(publishable(t, include_unreviewed=a.preview).images["image_id"])
    done = make_web_images(
        t,
        out,
        collection_root=s.collection_root,
        project_root=s.project_root,
        max_side=a.max_side,
        image_ids=ids,
    )
    print(f"{len(done)} web images in {out}")


# --------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    from .build import SITE_URL

    p = argparse.ArgumentParser(
        prog="gameheritage",
        description="Curation pipeline for the video game digital heritage catalogue.",
    )
    p.add_argument("--version", action="version", version=f"gameheritage {__version__}")
    p.add_argument("--config", help="path to gameheritage.toml (default: search upwards)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("scan", help="inventory the raw collection (read-only) into a manifest")
    sp.add_argument("--root", help="collection root (default: collection_root in the config)")
    sp.add_argument("--games", nargs="*", help="only these game folders")
    sp.add_argument("--listing", help="build from a JSON listing instead of reading files")
    sp.set_defaults(func=cmd_scan)

    sp = sub.add_parser("register", help="add content-hashed images to images.csv")
    sp.set_defaults(func=cmd_register)

    sp = sub.add_parser("flags", help="print problem-flag counts per game")
    sp.set_defaults(func=cmd_flags)

    sp = sub.add_parser("fandom-sources", help="record Fandom source pages for game images")
    sp.add_argument("--games", nargs="*", help="only these game_ids")
    sp.add_argument("--refresh", action="store_true", help="look up images already sourced")
    sp.set_defaults(func=cmd_fandom_sources)

    sp = sub.add_parser("autolink", help="match in-game places to real sites on Wikidata")
    sp.add_argument("--games", nargs="*", help="only these game_ids")
    sp.add_argument("--sheet", help="link sheet path (default review/auto_links.csv)")
    sp.add_argument("--from-sheet", action="store_true", help="apply a reviewed sheet")
    sp.add_argument("--no-apply", action="store_true", help="only write the sheet")
    sp.add_argument(
        "--levels",
        nargs="*",
        default=["high", "medium-clean"],
        help="confidence levels applied without review",
    )
    sp.set_defaults(func=cmd_autolink)

    sp = sub.add_parser("real-images", help="download linked sites' photos and make pairs")
    sp.add_argument("--sites", nargs="*", help="only these site_ids")
    sp.add_argument("--limit", type=int, help="at most this many sites")
    sp.add_argument("--width", type=int, default=1280, help="thumbnail width in px")
    sp.add_argument("--pairs-only", action="store_true", help="only (re)build pairs")
    sp.add_argument("--extra", type=int, default=0, help="extra photos per site from Commons")
    sp.add_argument("--free-only", action="store_true", help="skip non-free licenses")
    sp.add_argument(
        "--no-license-lookup",
        action="store_true",
        help="download now, read licenses later with `gameheritage licenses`",
    )
    sp.set_defaults(func=cmd_real_images)

    sp = sub.add_parser("licenses", help="read missing authors and licenses from Commons")
    sp.set_defaults(func=cmd_licenses)

    sp = sub.add_parser("dedupe", help="drop duplicate images")
    sp.set_defaults(func=cmd_dedupe)

    sp = sub.add_parser("harvest", help="add location images from a Fandom wiki category")
    sp.add_argument("--wiki", required=True, help="Fandom subdomain, e.g. assassinscreed")
    sp.add_argument("--game", required=True, help="game_id (new games need --title)")
    sp.add_argument("--category", default="", help="e.g. 'Locations in Attika'")
    sp.add_argument("--prefix", default="", help="harvest files named PREFIX*, e.g. ACUDB")
    sp.add_argument("--title", help="game title, to add a new game")
    sp.add_argument("--series", help="series, for a new game")
    sp.add_argument("--depth", type=int, default=1, help="subcategory depth")
    sp.add_argument("--per-page", type=int, default=4, help="images per location page")
    sp.add_argument("--dest", help="folder for downloaded images")
    sp.add_argument("--file-pattern", help="regex on file names, e.g. '^ACU' for AC Unity")
    sp.add_argument("--exclude-pattern", help="skip file names matching this regex")
    sp.add_argument(
        "--any-place", action="store_true", help="also download places that are not structures"
    )
    sp.set_defaults(func=cmd_harvest)

    sp = sub.add_parser("prune-harvest", help="remove harvested images that are not structures")
    sp.add_argument(
        "--unlinked",
        action="store_true",
        help="after autolink: remove harvested images not matched to a real site",
    )
    sp.add_argument(
        "--files",
        action="store_true",
        help="remove harvested maps, menus, icons and videos (and --pattern matches)",
    )
    sp.add_argument(
        "--people",
        action="store_true",
        help="remove harvested images that show a person rather than the place",
    )
    sp.add_argument("--pattern", help="with --files: also remove file names matching this regex")
    sp.add_argument("--games", default="", help="with --pattern: only games whose id starts so")
    sp.set_defaults(func=cmd_prune_harvest)

    sp = sub.add_parser("website", help="build the static website into a folder")
    sp.add_argument("--out", required=True, help="output folder, e.g. a GitHub Pages repo")
    sp.add_argument("--images-only", action="store_true", help="only make web images")
    sp.add_argument("--data-only", action="store_true", help="reuse images; write data")
    sp.add_argument("--workers", type=int, default=4, help="parallel image downloads")
    sp.set_defaults(func=cmd_website)

    sp = sub.add_parser("review", help="export or import a review sheet")
    sp.add_argument("action", choices=["export", "import"])
    sp.add_argument("queue", choices=["images", "places", "games", "sites", "pairs"])
    sp.add_argument("file", nargs="?")
    sp.set_defaults(func=cmd_review)

    sp = sub.add_parser("link", help="propose Wikidata links for games or sites")
    sp.add_argument("what", choices=["games", "sites"])
    sp.set_defaults(func=cmd_link)

    sp = sub.add_parser("enrich", help="fill game or site facts from Wikidata")
    sp.add_argument("what", choices=["games", "sites"])
    sp.set_defaults(func=cmd_enrich)

    sp = sub.add_parser("describe", help="fill site descriptions from Wikipedia")
    sp.add_argument("--sentences", type=int, default=2)
    sp.add_argument(
        "--in-game",
        action="store_true",
        help="describe in-game places from the games' Fandom wikis instead",
    )
    sp.set_defaults(func=cmd_describe)

    sp = sub.add_parser("dates", help="fill construction dates and historical periods")
    sp.set_defaults(func=cmd_dates)

    sp = sub.add_parser("groundtruth", help="propose or download real-world images")
    sp.add_argument("action", choices=["propose", "download"])
    sp.add_argument("file", nargs="?", help="reviewed groundtruth.csv (download)")
    sp.add_argument("--sites", nargs="*", help="only these site_ids (propose)")
    sp.add_argument("--per-site", type=int, default=6)
    sp.add_argument("--thumbnail", action="store_true", help="download 1280 px versions")
    sp.set_defaults(func=cmd_groundtruth)

    sp = sub.add_parser("validate", help="check tables, links and the publish gate")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("credits", help="write credits and image references")
    sp.add_argument("--out")
    sp.set_defaults(func=cmd_credits)

    sp = sub.add_parser("build", help="build a dataset release")
    sp.add_argument("--version", required=True, help="e.g. 2026.11")
    sp.add_argument("--preview", action="store_true", help="include unreviewed rows")
    sp.add_argument("--package", action="store_true", help="also bundle it into the package")
    sp.add_argument(
        "--site",
        help="a website built by `gameheritage website`: release exactly what it shows, "
        "automatic matches flagged, with era, century and hosted image URLs",
    )
    sp.add_argument("--site-url", default=SITE_URL, help="where that website is published")
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser("web-images", help="make WebP copies for the website")
    sp.add_argument("--out")
    sp.add_argument("--max-side", type=int, default=1600)
    sp.add_argument("--preview", action="store_true", help="include unreviewed images")
    sp.set_defaults(func=cmd_web_images)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    settings = load_settings(args.config)
    # Face detectors for the person check (see people.py) can live in the cache.
    os.environ.setdefault("GAMEHERITAGE_MODELS", str(settings.cache_dir / "models"))
    args.func(settings, args)


if __name__ == "__main__":  # pragma: no cover
    main()
