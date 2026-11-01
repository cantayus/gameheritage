"""Project settings, read from ``gameheritage.toml``.

Relative paths in the file are resolved against the file's own folder, so the
same project works from any working directory. Environment variables override
the file: ``GAMEHERITAGE_CONTACT``, ``EUROPEANA_API_KEY``.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

CONFIG_NAME = "gameheritage.toml"


@dataclass
class Settings:
    project_root: Path
    collection_root: Path
    data_dir: Path
    review_dir: Path
    images_root: Path
    cache_dir: Path
    release_dir: Path
    contact: str = ""
    europeana_key: str = ""
    min_interval: float = 1.0
    collector: str = ""
    collection_method: str = ""
    fictional_worlds: tuple[str, ...] = ()


def find_config(start: Path | None = None) -> Path | None:
    here = (start or Path.cwd()).resolve()
    for d in (here, *here.parents):
        if (d / CONFIG_NAME).exists():
            return d / CONFIG_NAME
    return None


def load_settings(path: str | Path | None = None) -> Settings:
    cfg_path = Path(path) if path else find_config()
    raw: dict = {}
    root = Path.cwd()
    if cfg_path:
        raw = tomllib.loads(Path(cfg_path).read_text(encoding="utf-8"))
        root = Path(cfg_path).resolve().parent
    paths = raw.get("paths", {})
    http = raw.get("http", {})
    coll = raw.get("collection", {})
    auto = raw.get("autolink", {})

    def p(key: str, default: str) -> Path:
        return (root / paths.get(key, default)).resolve()

    return Settings(
        project_root=root,
        collection_root=p("collection_root", "../Progress/New Data"),
        data_dir=p("data_dir", "data"),
        review_dir=p("review_dir", "review"),
        images_root=p("images_root", "../curated_images"),
        cache_dir=p("cache_dir", ".cache"),
        release_dir=p("release_dir", "release"),
        contact=os.environ.get("GAMEHERITAGE_CONTACT", http.get("contact", "")),
        europeana_key=os.environ.get("EUROPEANA_API_KEY", http.get("europeana_api_key", "")),
        min_interval=float(http.get("min_interval_seconds", 1.0)),
        collector=coll.get("collected_by", ""),
        collection_method=coll.get("method", ""),
        fictional_worlds=tuple(auto.get("fictional_worlds", ())),
    )
