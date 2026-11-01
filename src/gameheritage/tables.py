"""Read, write and validate the catalogue's CSV tables."""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from .schema import MODELS, PRIMARY_KEYS, columns

TABLE_NAMES = ("games", "sites", "depictions", "images", "pairs", "folder_aliases")


def empty_table(name: str) -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="string") for c in columns(name)})


def read_table(data_dir: str | Path, name: str) -> pd.DataFrame:
    """Read one table as strings ('' for missing), with every schema column present."""
    path = Path(data_dir) / f"{name}.csv"
    if not path.exists():
        return empty_table(name)
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    unknown = [c for c in df.columns if c not in columns(name)]
    if unknown:
        raise ValueError(f"{path}: unknown columns {unknown}")
    for c in columns(name):
        if c not in df.columns:
            df[c] = ""
    return df[columns(name)].astype("string").fillna("")


def write_table(df: pd.DataFrame, data_dir: str | Path, name: str) -> Path:
    """Write a table in schema column order, sorted by primary key, UTF-8, LF endings."""
    path = Path(data_dir) / f"{name}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    for c in columns(name):
        if c not in out.columns:
            out[c] = ""
    out = out[columns(name)].astype("string").fillna("")
    out = out.sort_values(PRIMARY_KEYS[name], kind="stable").reset_index(drop=True)
    # Write a temporary file and swap it in, so readers never see half a table.
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    out.to_csv(tmp, index=False, encoding="utf-8", lineterminator="\n")
    os.replace(tmp, path)
    return path


def _clean(record: dict) -> dict:
    """Drop empty cells so that model defaults apply."""
    return {k: v for k, v in record.items() if v not in ("", None)}


def defaults(name: str) -> dict[str, str]:
    """Non-empty default values of a table's columns (e.g. review_status -> "todo")."""
    out = {}
    for f, info in MODELS[name].model_fields.items():
        d = info.default
        if d is None or d is ... or d == "":
            continue
        out[f] = str(getattr(d, "value", d))
    return out


def validate_rows(df: pd.DataFrame, name: str) -> list[str]:
    """Check every row against its pydantic model. Returns error messages."""
    model = MODELS[name]
    pk = PRIMARY_KEYS[name]
    errors: list[str] = []
    for i, rec in enumerate(df.to_dict("records")):
        try:
            model(**_clean(rec))
        except ValidationError as exc:
            for e in exc.errors():
                loc = ".".join(str(x) for x in e["loc"])
                errors.append(f"{name} row {i + 2} ({pk}={rec.get(pk)!r}): {loc}: {e['msg']}")
    return errors


class ConcurrentEditError(RuntimeError):
    pass


def _mtime(data_dir: str | Path, name: str) -> float:
    p = Path(data_dir) / f"{name}.csv"
    return p.stat().st_mtime if p.exists() else 0.0


def _digest(df: pd.DataFrame) -> int:
    return int(pd.util.hash_pandas_object(df.astype("string").fillna(""), index=False).sum())


@dataclass
class Tables:
    """All catalogue tables, as string DataFrames."""

    games: pd.DataFrame = field(default_factory=lambda: empty_table("games"))
    sites: pd.DataFrame = field(default_factory=lambda: empty_table("sites"))
    depictions: pd.DataFrame = field(default_factory=lambda: empty_table("depictions"))
    images: pd.DataFrame = field(default_factory=lambda: empty_table("images"))
    pairs: pd.DataFrame = field(default_factory=lambda: empty_table("pairs"))
    folder_aliases: pd.DataFrame = field(default_factory=lambda: empty_table("folder_aliases"))

    @classmethod
    def load(cls, data_dir: str | Path) -> Tables:
        for _ in range(10):  # re-read if another step saved while we were reading
            before = {n: _mtime(data_dir, n) for n in TABLE_NAMES}
            t = cls(**{n: read_table(data_dir, n) for n in TABLE_NAMES})
            if before == {n: _mtime(data_dir, n) for n in TABLE_NAMES}:
                break
        t._loaded = {n: (before[n], _digest(t[n])) for n in TABLE_NAMES}
        return t

    def save(self, data_dir: str | Path, *, force: bool = False) -> list[str]:
        """Write the tables that changed since ``load``.

        Refuses (``ConcurrentEditError``) when a file on disk changed after this
        copy was loaded, e.g. by another step running at the same time or a hand
        edit, unless ``force``. Returns the names of the tables written.
        """
        loaded = getattr(self, "_loaded", {})
        changed = [n for n in TABLE_NAMES if n not in loaded or _digest(self[n]) != loaded[n][1]]
        if not force:
            stale = [n for n in changed if n in loaded and _mtime(data_dir, n) != loaded[n][0]]
            if stale:
                raise ConcurrentEditError(
                    f"{', '.join(stale)} changed on disk since they were loaded "
                    "(another step running, or a hand edit). Nothing was saved; re-run the step."
                )
        for n in changed:
            write_table(self[n], data_dir, n)
        self._loaded = {n: (_mtime(data_dir, n), _digest(self[n])) for n in TABLE_NAMES}
        return changed

    def __getitem__(self, name: str) -> pd.DataFrame:
        return getattr(self, name)

    def __setitem__(self, name: str, df: pd.DataFrame) -> None:
        setattr(self, name, df)


def _fmt(v) -> str:
    """One cell as text: whole-number floats lose their ".0", None/NaN become ""."""
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:  # NaN
            return ""
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def upsert(
    df: pd.DataFrame, rows: list[dict], key: str, *, overwrite: bool = False
) -> pd.DataFrame:
    """Insert rows by primary key. Existing rows keep their values unless ``overwrite``.

    With ``overwrite=False`` only empty cells of existing rows are filled, so
    human edits in the CSV are never replaced by machine output. Empty values in
    ``rows`` never blank out existing cells.
    """
    if not rows:
        return df
    cols = list(df.columns)
    incoming = pd.DataFrame([{k: _fmt(v) for k, v in r.items()} for r in rows])
    incoming = incoming[[c for c in incoming.columns if c in cols]]
    incoming = incoming.astype("string").fillna("")
    # Several updates for one key: later non-empty values win.
    incoming = incoming.replace("", pd.NA).groupby(key, sort=False).last().reset_index()
    incoming = incoming.astype("string").fillna("")
    base = df.astype("string").fillna("")
    base = base[~base[key].duplicated()].set_index(key, drop=False)
    inc = incoming.set_index(key, drop=False)
    existing = inc.index.intersection(base.index)
    if len(existing):
        upd = inc.loc[existing]
        for c in upd.columns:
            if c == key:
                continue
            new_vals = upd[c]
            has_new = new_vals != ""
            if not overwrite:
                has_new &= base.loc[existing, c] == ""
            idx = new_vals.index[has_new]
            if len(idx):
                base.loc[idx, c] = new_vals[has_new]
    added = inc.loc[inc.index.difference(base.index, sort=False)]
    if len(added):
        added = added.reindex(columns=cols, fill_value="").astype("string").fillna("")
        table = next(
            (n for n, k in PRIMARY_KEYS.items() if k == key and set(columns(n)) == set(cols)), None
        )
        for c, v in (defaults(table) if table else {}).items():
            added.loc[added[c] == "", c] = v
        base = pd.concat([base, added])
    return base.reset_index(drop=True)[cols].astype("string").fillna("")


def commit(data_dir: str | Path, change, *, attempts: int = 8) -> Tables:
    """Load fresh tables, apply ``change(tables)``, save; retry if another step saved.

    Lets slow steps (downloads) run side by side: each collects its rows first and
    only touches the tables for the second it takes to merge them.
    """
    import time

    for i in range(attempts):
        with _lock(data_dir):  # one writer at a time across processes (where supported)
            t = Tables.load(data_dir)
            change(t)
            try:
                t.save(data_dir)
                return t
            except ConcurrentEditError:
                pass
        time.sleep(1 + i)
    raise ConcurrentEditError(f"could not save after {attempts} attempts")


@contextlib.contextmanager
def _lock(data_dir: str | Path):
    """An exclusive lock on ``data_dir/.lock`` (POSIX); a no-op elsewhere."""
    try:
        import fcntl
    except ImportError:  # Windows: rely on the save-time check alone
        yield
        return
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(data_dir) / ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
