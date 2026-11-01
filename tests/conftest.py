from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from gameheritage.tables import Tables, upsert


def _img(path: Path, size=(640, 360), color=(120, 90, 60), fmt=None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, format=fmt)
    return path


@pytest.fixture
def collection(tmp_path: Path) -> Path:
    """A tiny raw collection laid out like Mija's Drive folder."""
    root = tmp_path / "New Data"
    g = root / "Assassin's Creed Brotherhood" / "game data"
    _img(g / "ACB DB Castra Praetoria.png", color=(10, 20, 30))
    _img(g / "ACB Art Colosseum.jpg", color=(200, 10, 10))
    _img(g / "Colosseum copy (1).jpg", color=(200, 10, 10))  # same bytes as above? no: re-encoded
    _img(g / "AoE icon.png", size=(64, 64))
    _img(g / "Webp_saved_as_jpg.jpg", fmt="WEBP", color=(5, 200, 5))
    (g / "empty.png").write_bytes(b"")
    r = root / "Assassin's Creed Brotherhood" / "real images"
    _img(r / "01_colosseum_rome.jpg", size=(800, 600), color=(1, 2, 3))
    (r / "02_pantheon_rome.jpg").write_bytes((r / "01_colosseum_rome.jpg").read_bytes())
    (root / "Assassin's Creed Brotherhood" / "desktop.ini").write_text("x")
    a = root / "Anno 1800" / "game data"
    _img(a / "Bakery__page_image.jpg", fmt="WEBP")
    _img(a / "Konglig_Museum__File_Konglig_Museum_png.jpg")
    return root


@pytest.fixture
def project(tmp_path: Path, collection: Path) -> Path:
    """A project folder with config and seeded games/aliases."""
    proj = tmp_path / "proj"
    (proj / "data").mkdir(parents=True)
    (proj / "gameheritage.toml").write_text(
        f'[paths]\ncollection_root = "{collection.as_posix()}"\nimages_root = "imgs"\n'
        '[http]\ncontact = "test@example.org"\n'
    )
    t = Tables()
    t.games = upsert(
        t.games,
        [
            {
                "game_id": "assassins-creed-brotherhood",
                "title": "Assassin's Creed Brotherhood",
                "series": "Assassin's Creed",
                "fandom_wiki": "assassinscreed",
                "developer": "Ubisoft Montreal",
                "publisher": "Ubisoft",
                "release_year": "2010",
            },
            {"game_id": "anno-1800", "title": "Anno 1800", "fandom_wiki": "anno1800"},
        ],
        "game_id",
    )
    t.folder_aliases = upsert(
        t.folder_aliases,
        [
            {
                "folder_name": "Assassin's Creed Brotherhood",
                "game_id": "assassins-creed-brotherhood",
            },
            {"folder_name": "Anno 1800", "game_id": "anno-1800"},
        ],
        "folder_name",
    )
    t.save(proj / "data")
    return proj


@pytest.fixture
def listing(tmp_path: Path) -> Path:
    p = tmp_path / "listing.json"
    p.write_text(
        json.dumps(
            {
                "entries": [
                    {"path": "Sekiro/real images/01_senpou_temple_japan.jpg", "size": 1967},
                    {"path": "Sekiro/real images/02_senpou_temple_japan.jpg", "size": 1967},
                    {"path": "Sekiro/game data/Senpou Temple 1.jpg", "size": 90000},
                    {"path": "Sekiro/game data/Senpou Temple 2.jpg", "size": 90000},
                    {"path": "Sekiro/desktop.ini", "size": 10},
                    {"path": "Age of Empires ii/game data/sound.ogg", "size": 4000},
                ]
            }
        )
    )
    return p
