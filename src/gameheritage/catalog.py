"""The user-facing catalogue: load a release and query it with pandas."""

from __future__ import annotations

import json
import os
from importlib import resources
from pathlib import Path

import pandas as pd

from .credits import credit_line, credits_table
from .schema import PRIMARY_KEYS
from .tables import TABLE_NAMES, Tables, empty_table

PACKAGED = "_release"


def _packaged_dir() -> Path | None:
    try:
        p = resources.files("gameheritage") / PACKAGED
    except (ModuleNotFoundError, TypeError):  # pragma: no cover
        return None
    p = Path(str(p))
    return p if (p / "games.csv").exists() else None


def _read_dir(path: Path) -> Tables:
    t = {}
    for name in TABLE_NAMES:
        f = path / f"{name}.csv"
        if f.exists():
            df = pd.read_csv(f, dtype=str, keep_default_na=False, encoding="utf-8-sig")
            base = empty_table(name)
            for c in base.columns:
                if c not in df.columns:
                    df[c] = ""
            # Schema columns first, then any columns the release adds (era, web_url...).
            cols = list(base.columns) + [c for c in df.columns if c not in base.columns]
            df = df[cols].astype("string").fillna("")
            t[name] = df.drop_duplicates(PRIMARY_KEYS[name])
        else:
            t[name] = empty_table(name)
    return Tables(**t)


def _match(series: pd.Series, value) -> pd.Series:
    if isinstance(value, (list, tuple, set)):
        vals = {str(v).lower() for v in value}
        return series.str.lower().isin(vals)
    return series.str.lower() == str(value).lower()


def _filter(
    df: pd.DataFrame, search_cols: list[str], search: str | None, **filters
) -> pd.DataFrame:
    out = df
    for col, value in filters.items():
        if value is None:
            continue
        if col not in out.columns:
            raise KeyError(f"unknown column {col!r}; columns are {list(out.columns)}")
        out = out[_match(out[col], value)]
    if search:
        mask = pd.Series(False, index=out.index)
        for c in search_cols:
            mask |= out[c].str.contains(search, case=False, regex=False)
        out = out[mask]
    return out.reset_index(drop=True)


class Catalog:
    """Games, sites, depictions, images and curated pairs, as pandas DataFrames."""

    def __init__(self, tables: Tables, source: str = "", meta: dict | None = None) -> None:
        self.tables = tables
        self.source = source
        self.meta = meta or {}

    def __repr__(self) -> str:
        c = self.summary()
        return (
            f"<gameheritage.Catalog {self.meta.get('version', 'working copy')}: "
            f"{c['games']} games, {c['sites']} sites, {c['images']} images, {c['pairs']} pairs>"
        )

    def summary(self) -> dict[str, int]:
        return {n: len(self.tables[n]) for n in ("games", "sites", "depictions", "images", "pairs")}

    def games(self, search: str | None = None, **filters) -> pd.DataFrame:
        """Games, e.g. ``cat.games(series="Assassin's Creed")`` or ``cat.games(search="war")``."""
        return _filter(self.tables.games, ["title", "series"], search, **filters)

    def sites(self, search: str | None = None, **filters) -> pd.DataFrame:
        """Real places, e.g. ``cat.sites(country="Italy")``, ``cat.sites(era="Middle Ages")``
        or ``cat.sites(search="castle")``. Any column can be a filter; a list matches any
        of its values."""
        return _filter(self.tables.sites, ["name", "alt_names", "locality"], search, **filters)

    places = sites

    def depictions(self, search: str | None = None, **filters) -> pd.DataFrame:
        """In-game versions of real places, e.g. ``cat.depictions(game_id="far-cry-3")``."""
        return _filter(self.tables.depictions, ["in_game_name"], search, **filters)

    def matches(
        self,
        game=None,
        site=None,
        *,
        era: str | list[str] | None = None,
        country: str | list[str] | None = None,
        verified: bool | None = None,
    ) -> pd.DataFrame:
        """One row per in-game version of a real place, ready to compare.

        Each row has the game, the real place (with country, year, century and
        era), how the match was made, the number of game and real images, and one
        game image and one real photo with URLs and credits. ``game`` and ``site``
        accept IDs or names (or lists); ``verified=False`` keeps the automatic
        matches that no one has reviewed yet.
        """
        d = self.tables.depictions
        if game is not None:
            d = d[d["game_id"].isin(self._game_ids(game))]
        if site is not None:
            d = d[d["site_id"].isin(self._site_ids(site))]
        if verified is not None and "verified" in d.columns:
            d = d[(d["verified"] == "true") == verified]
        g = self.tables.games.set_index("game_id")
        s = self.tables.sites.set_index("site_id")
        site_cols = [
            c
            for c in ("name", "country", "locality", "year", "century", "era", "wikidata_qid")
            if c in s.columns
        ]
        out = d.join(g[["title", "series", "release_year"]], on="game_id").join(
            s[site_cols].rename(columns={"name": "site_name", "wikidata_qid": "site_qid"}),
            on="site_id",
        )
        out = out.rename(columns={"title": "game_title"})
        if era is not None and "era" in out.columns:
            out = out[_match(out["era"], era)]
        if country is not None:
            out = out[_match(out["country"], country)]
        imgs = self.images()
        firsts = {}
        for role, key in (("game", "depiction_id"), ("real", "site_id")):
            sub = imgs[imgs["role"] == role]
            if role == "real" and "rights_status" in sub:  # free photos first
                sub = sub.assign(_o=(sub["rights_status"] != "open").astype(int)).sort_values("_o")
            firsts[role] = sub.drop_duplicates(key).set_index(key)
            counts = sub[key].value_counts()
            out[f"n_{role}_images"] = out[key].map(counts).fillna(0).astype(int)
            fields = (
                "image_id",
                "web_url",
                "file_url",
                "source_page_url",
                "license",
                "credit_line",
            )
            for field in fields:
                col = firsts[role].get(field)
                out[f"{role}_{field}"] = out[key].map(col) if col is not None else ""
        keep = [
            "depiction_id", "game_id", "game_title", "series", "release_year", "in_game_name",
            "site_id", "site_name", "country", "locality", "year", "century", "era", "site_qid",
            "verified", "review_status", "basis_note", "n_game_images", "n_real_images",
        ]  # fmt: skip
        keep = [c for c in keep if c in out.columns]
        rest = [c for c in out.columns if c.startswith(("game_", "real_")) and c not in keep]
        return out[keep + rest].reset_index(drop=True).fillna("")

    def search(self, text: str) -> pd.DataFrame:
        """Find places and games by name, other names, city or in-game name.

        Returns matching in-game versions (see :meth:`matches`), so a search for a
        game lists its places and a search for a place lists its games.
        """
        t = text.strip().lower()
        if not t:
            return self.matches().iloc[0:0]
        s, g, d = self.tables.sites, self.tables.games, self.tables.depictions

        def hit(df, cols):
            mask = pd.Series(False, index=df.index)
            for c in cols:
                mask |= df[c].str.lower().str.contains(t, regex=False)
            return df[mask]

        site_ids = set(hit(s, ["name", "alt_names", "locality", "country"])["site_id"])
        game_ids = set(hit(g, ["title", "series"])["game_id"])
        dep_ids = set(hit(d, ["in_game_name"])["depiction_id"])
        m = self.matches()
        return m[
            m["site_id"].isin(site_ids)
            | m["game_id"].isin(game_ids)
            | m["depiction_id"].isin(dep_ids)
        ].reset_index(drop=True)

    def timeline(self, by: str = "era") -> pd.DataFrame:
        """Number of places, games and in-game versions per ``era`` or ``century``."""
        from .history import ERAS, NATURAL, UNDATED

        if by not in self.tables.sites.columns:
            raise KeyError(f"this catalogue has no {by!r} column (use a website release)")
        m = self.matches()
        out = (
            m.groupby(by)
            .agg(
                places=("site_id", "nunique"),
                games=("game_id", "nunique"),
                in_game_versions=("depiction_id", "nunique"),
            )
            .reset_index()
        )
        if by == "era":
            order = [e[0] for e in ERAS] + [NATURAL, UNDATED]
            out["_o"] = out["era"].map({e: i for i, e in enumerate(order)})
            return out.sort_values("_o").drop(columns="_o").reset_index(drop=True)
        years = m.groupby("century")["year"].apply(
            lambda y: pd.to_numeric(y, errors="coerce").min()
        )
        out["_o"] = out["century"].map(years)
        out = out[out["century"] != ""]
        return out.sort_values("_o").drop(columns="_o").reset_index(drop=True)

    def images(self, search: str | None = None, **filters) -> pd.DataFrame:
        """Images, e.g. ``cat.images(role="real", license="CC BY-SA 4.0")``."""
        df = _filter(self.tables.images, ["title", "original_filename"], search, **filters)
        games = {r["game_id"]: r for r in self.tables.games.to_dict("records")}
        df = df.copy()
        df["credit_line"] = [credit_line(r, games.get(r["game_id"])) for r in df.to_dict("records")]
        return df

    def _game_ids(self, game) -> set[str]:
        g = self.tables.games
        vals = game if isinstance(game, (list, tuple, set)) else [game]
        vals = {str(v).lower() for v in vals}
        return set(
            g[g["game_id"].str.lower().isin(vals) | g["title"].str.lower().isin(vals)]["game_id"]
        )

    def _site_ids(self, site) -> set[str]:
        s = self.tables.sites
        vals = site if isinstance(site, (list, tuple, set)) else [site]
        vals = {str(v).lower() for v in vals}
        return set(
            s[s["site_id"].str.lower().isin(vals) | s["name"].str.lower().isin(vals)]["site_id"]
        )

    def pairs(
        self,
        game=None,
        site=None,
        match: str | None = None,
        depiction_class: int | None = None,
        featured: bool | None = None,
    ) -> pd.DataFrame:
        """Curated comparisons with game, site and both images' URLs and credits.

        ``game`` and ``site`` accept IDs or names (or lists of them).
        ``match`` accepts a match level, with spaces or underscores.
        """
        p = self.tables.pairs.merge(
            self.tables.depictions[
                ["depiction_id", "game_id", "site_id", "in_game_name", "depiction_class"]
            ],
            on="depiction_id",
            how="left",
        )
        if game is not None:
            p = p[p["game_id"].isin(self._game_ids(game))]
        if site is not None:
            p = p[p["site_id"].isin(self._site_ids(site))]
        if match:
            p = p[p["match_level"] == match.strip().lower().replace(" ", "_").replace(",", "")]
        if depiction_class is not None:
            p = p[p["depiction_class"] == str(depiction_class)]
        if featured is not None:
            p = p[p["featured"].str.lower().isin(["true", "1"] if featured else ["false", "0", ""])]
        g = self.tables.games.set_index("game_id")["title"]
        s = self.tables.sites.set_index("site_id")["name"]
        imgs = self.images().set_index("image_id")
        out = p.assign(
            game_title=p["game_id"].map(g),
            site_name=p["site_id"].map(s),
        )
        for side in ("game", "real"):
            col = f"{side}_image_id"
            for field in (
                "file_url",
                "web_url",
                "source_page_url",
                "license",
                "credit_line",
                "local_path",
                "drive_path",
            ):
                if field not in imgs.columns:
                    continue
                out[f"{side}_{field}"] = out[col].map(imgs[field]) if len(imgs) else ""
        return out.reset_index(drop=True).fillna("")

    def credits(self, image_ids: list[str] | None = None) -> pd.DataFrame:
        """Credit lines and provenance for images (all images when ``image_ids`` is None)."""
        return credits_table(self.tables, image_ids)

    def download(self, rows: pd.DataFrame, dest: str | Path, **kwargs) -> pd.DataFrame:
        from .download import download

        return download(rows, dest, catalog=self, **kwargs)


REPO = "cantayus/gameheritage"
RAW_URL = "https://raw.githubusercontent.com/{repo}/{ref}/src/gameheritage/_release/{name}"
RELEASE_FILES = (
    "release.json", "datapackage.json", "games.csv", "sites.csv", "depictions.csv",
    "images.csv", "pairs.csv", "credits.csv", "CREDITS.md", "image_references.bib",
)  # fmt: skip


def cache_dir() -> Path:
    """Where downloaded releases and images are cached (``GAMEHERITAGE_CACHE`` overrides)."""
    env = os.environ.get("GAMEHERITAGE_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CACHE_HOME") or (
        os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    )
    return (Path(base) if base else Path.home() / ".cache") / "gameheritage"


def fetch_release(version: str = "latest", *, refresh: bool = False, client=None) -> Path:
    """Download a release from GitHub into the cache and return its folder.

    ``version`` is a release such as ``"0.1.0"`` (the git tag ``v0.1.0``) or
    ``"latest"`` for the newest data on the main branch. Tagged releases never
    change, so they are downloaded once; ``"latest"`` is refreshed when
    ``refresh=True`` or when the cached copy is more than a day old.
    """
    import time

    from .sources.http import public_client

    ref = (
        "main"
        if version in ("latest", "main")
        else (version if version.startswith("v") else f"v{version}")
    )
    repo = os.environ.get("GAMEHERITAGE_REPO", REPO)
    dest = cache_dir() / "releases" / ref
    marker = dest / "release.json"
    stale = ref == "main" and marker.exists() and time.time() - marker.stat().st_mtime > 86400
    if marker.exists() and not (refresh or stale):
        return dest
    client = client or public_client()
    tmp = dest.with_name(dest.name + ".part")
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for name in RELEASE_FILES:
            try:
                client.download(RAW_URL.format(repo=repo, ref=ref, name=name), tmp / name)
            except Exception:
                if (name.endswith(".csv") and name != "credits.csv") or name == "release.json":
                    raise
    except Exception as exc:
        if marker.exists():  # offline or GitHub unreachable: keep using the cached copy
            import warnings

            warnings.warn(f"could not refresh release {ref} ({exc}); using the cached copy",
                          stacklevel=2)  # fmt: skip
            return dest
        raise RuntimeError(
            f"could not download release {ref!r} from github.com/{repo}: {exc}"
        ) from exc
    if dest.exists():
        import shutil

        shutil.rmtree(dest)
    tmp.rename(dest)
    return dest


def load(
    source: str | Path | None = None, *, version: str | None = None, refresh: bool = False
) -> Catalog:
    """Load a catalogue.

    * ``gh.load()``: the release installed with the package (works offline).
    * ``gh.load(version="latest")``: the newest release on GitHub; a version such
      as ``"0.1.0"`` loads that tagged release (downloaded once, then cached).
    * ``gh.load("path/to/folder")``: a release folder (with ``release.json``) or a
      curation project's ``data/`` folder.

    The ``GAMEHERITAGE_DATA`` variable points ``gh.load()`` at another folder.
    """
    if version is not None:
        path = fetch_release(version, refresh=refresh)
    elif source is None:
        env = os.environ.get("GAMEHERITAGE_DATA")
        path = Path(env) if env else _packaged_dir()
        if path is None:
            raise FileNotFoundError(
                "No bundled release found. Use gh.load(version='latest') or pass a folder."
            )
    else:
        path = Path(source)
    if not (path / "games.csv").exists():
        raise FileNotFoundError(f"{path} does not contain games.csv")
    meta = {}
    if (path / "release.json").exists():
        meta = json.loads((path / "release.json").read_text("utf-8"))
    return Catalog(_read_dir(path), source=str(path), meta=meta)
