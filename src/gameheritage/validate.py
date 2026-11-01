"""Check the tables: row schemas, keys, cross-table links and the publish gate.

The publish gate: a pair marked ``keep`` can only be released when its real
image has a recorded free license and source page, and its game image has a
recorded source page.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .schema import PRIMARY_KEYS, is_free_license
from .tables import TABLE_NAMES, Tables, validate_rows


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __str__(self) -> str:
        lines = [f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)"]
        lines += [f"ERROR   {e}" for e in self.errors]
        lines += [f"warning {w}" for w in self.warnings]
        return "\n".join(lines)


def _fk(report: Report, df, col: str, valid: set[str], label: str) -> None:
    bad = df[(df[col] != "") & ~df[col].isin(valid)]
    for r in bad.head(20).to_dict("records"):
        report.errors.append(f"{label}: {col}={r[col]!r} does not exist")
    if len(bad) > 20:
        report.errors.append(f"{label}: ... and {len(bad) - 20} more bad {col} values")


def validate(tables: Tables, *, max_row_errors: int = 200) -> Report:
    rep = Report()
    for name in TABLE_NAMES:
        df = tables[name]
        errs = validate_rows(df, name)
        rep.errors += errs[:max_row_errors]
        if len(errs) > max_row_errors:
            rep.errors.append(f"{name}: ... and {len(errs) - max_row_errors} more row errors")
        pk = PRIMARY_KEYS[name]
        dups = df[pk][df[pk].duplicated()].unique().tolist()
        if dups:
            rep.errors.append(f"{name}: duplicate {pk} values {dups[:10]}")

    games = set(tables.games["game_id"])
    sites = set(tables.sites["site_id"])
    deps = set(tables.depictions["depiction_id"])
    _fk(rep, tables.folder_aliases, "game_id", games, "folder_aliases")
    _fk(rep, tables.depictions, "game_id", games, "depictions")
    _fk(rep, tables.depictions, "site_id", sites, "depictions")
    _fk(rep, tables.images, "game_id", games, "images")
    _fk(rep, tables.images, "site_id", sites, "images")
    _fk(rep, tables.images, "depiction_id", deps, "images")
    _fk(rep, tables.pairs, "depiction_id", deps, "pairs")

    imgs = tables.images.drop_duplicates("image_id").set_index("image_id")
    for r in tables.pairs.to_dict("records"):
        for col, role in (("game_image_id", "game"), ("real_image_id", "real")):
            iid = r[col]
            if iid not in imgs.index:
                rep.errors.append(f"pairs {r['pair_id']}: {col} {iid!r} does not exist")
            elif imgs.at[iid, "role"] != role:
                rep.errors.append(f"pairs {r['pair_id']}: {col} {iid!r} is not a {role} image")
        if r["review_status"] != "keep":
            continue
        real = imgs.loc[r["real_image_id"]] if r["real_image_id"] in imgs.index else None
        game = imgs.loc[r["game_image_id"]] if r["game_image_id"] in imgs.index else None
        if real is not None:
            if not is_free_license(real["license"]):
                rep.errors.append(
                    f"publish gate {r['pair_id']}: real image {r['real_image_id']} has no free "
                    f"license recorded ({real['license'] or 'none'})"
                )
            if not real["source_page_url"]:
                rep.errors.append(
                    f"publish gate {r['pair_id']}: real image {r['real_image_id']} "
                    "has no source page"
                )
        if game is not None and not game["source_page_url"]:
            rep.errors.append(
                f"publish gate {r['pair_id']}: game image {r['game_image_id']} has no source page"
            )

    qids = tables.sites["wikidata_qid"]
    dq = qids[(qids != "") & qids.duplicated()].unique().tolist()
    if dq:
        rep.warnings.append(f"sites: the same Wikidata QID is used by several sites: {dq[:10]}")
    real = tables.images[tables.images["role"] == "real"]
    nosrc = real[real["source_page_url"] == ""]
    if len(nosrc):
        rep.warnings.append(f"images: {len(nosrc)} real image(s) have no recorded source page")
    game = tables.images[tables.images["role"] == "game"]
    nosrc = game[game["source_page_url"] == ""]
    if len(nosrc):
        rep.warnings.append(f"images: {len(nosrc)} game image(s) have no recorded source page")
    noyear = tables.games[tables.games["release_year"] == ""]
    if len(noyear):
        rep.warnings.append(f"games: {len(noyear)} game(s) have no release year")
    return rep
