import json

import pandas as pd
import pytest

import gameheritage as gh
from gameheritage.build import build_release, publishable
from gameheritage.cli import main
from gameheritage.tables import Tables, upsert


@pytest.fixture
def curated(project, collection):
    """Run scan + register through the CLI, then curate one pair by hand."""
    import os

    cwd = os.getcwd()
    os.chdir(project)
    try:
        main(["scan"])
        main(["register"])
    finally:
        os.chdir(cwd)
    t = Tables.load(project / "data")
    game = t.images[t.images["title"] == "Castra Praetoria"]["image_id"].iloc[0]
    real_path = project / "imgs" / "real" / "castra.jpg"
    real_path.parent.mkdir(parents=True)
    real_path.write_bytes((collection / "Anno 1800/game data/Bakery__page_image.jpg").read_bytes())
    t.sites = upsert(
        t.sites, [{"site_id": "castra-praetoria", "name": "Castra Praetoria"}], "site_id"
    )
    t.depictions = upsert(
        t.depictions,
        [
            {
                "depiction_id": "acb--castra",
                "game_id": "assassins-creed-brotherhood",
                "site_id": "castra-praetoria",
                "in_game_name": "Castra Praetoria",
                "depiction_class": "1",
                "review_status": "keep",
            }
        ],
        "depiction_id",
    )
    t.images = upsert(
        t.images,
        [
            {
                "image_id": game,
                "review_status": "keep",
                "depiction_id": "acb--castra",
                "site_id": "castra-praetoria",
                "source_page_url": "https://assassinscreed.fandom.com/wiki/File:ACB.png",
            },
            {
                "image_id": "gh-real00000001",
                "role": "real",
                "site_id": "castra-praetoria",
                "title": "Castra",
                "local_path": "imgs/real/castra.jpg",
                "license": "CC BY 4.0",
                "source_name": "Wikimedia Commons",
                "source_page_url": "https://commons.wikimedia.org/wiki/File:C.jpg",
                "review_status": "keep",
            },
        ],
        "image_id",
        overwrite=True,
    )
    t.pairs = upsert(
        t.pairs,
        [
            {
                "pair_id": "p1",
                "depiction_id": "acb--castra",
                "game_image_id": game,
                "real_image_id": "gh-real00000001",
                "match_level": "same_structure_other_view",
                "featured": "True",
                "review_status": "keep",
            }
        ],
        "pair_id",
    )
    t.save(project / "data")
    return t, game


def test_publishable_keeps_only_curated(curated):
    t, game = curated
    pub = publishable(t)
    assert set(pub.pairs["pair_id"]) == {"p1"}
    assert set(pub.images["image_id"]) == {game, "gh-real00000001"}
    assert (pub.images["drive_path"] == "").all()
    assert set(pub.games["game_id"]) == {"assassins-creed-brotherhood"}


def test_build_load_query_download(curated, tmp_path, project):
    t, _game = curated
    out = build_release(t, tmp_path / "rel", "2026.11")
    meta = json.loads((out / "release.json").read_text())
    assert meta["counts"]["pairs"] == 1
    dp = json.loads((out / "datapackage.json").read_text())
    assert {r["name"] for r in dp["resources"]} == {
        "games", "sites", "depictions", "images", "pairs",
    }  # fmt: skip
    assert (out / "CREDITS.md").exists() and (out / "image_references.bib").exists()

    cat = gh.load(out)
    assert "1 pairs" in repr(cat)
    pairs = cat.pairs(game="Assassin's Creed Brotherhood", match="same structure other view")
    assert len(pairs) == 1 and pairs.loc[0, "site_name"] == "Castra Praetoria"
    assert "licensed CC BY 4.0" in pairs.loc[0, "real_credit_line"]
    assert len(cat.sites(search="castra")) == 1
    assert len(cat.games(series="assassin's creed")) == 1

    # download copies local files and writes an attribution table
    full = gh.load(project / "data")
    res = gh.download(
        full.pairs(), tmp_path / "dl", catalog=full, role="real", local_roots=[project]
    )
    assert list(res["status"]) == ["copied"]
    assert (tmp_path / "dl" / "ATTRIBUTION.csv").exists()
    attr = pd.read_csv(tmp_path / "dl" / "ATTRIBUTION.csv")
    assert attr.loc[0, "license"] == "CC BY 4.0"

    img = gh.show(full.pairs().iloc[0], local_roots=[project, project.parent / "New Data"])
    assert img.width > 0
    # the game image lives in the collection, the real one in the project
    assert "Caliskan" in gh.cite() and gh.cite("bibtex").startswith("@misc{")


def test_cli_validate_and_credits(curated, project, capsys):
    import os

    cwd = os.getcwd()
    os.chdir(project)
    try:
        main(["validate"])  # unsourced raw real images only warn; the kept pair is clean
        main(["credits"])
        main(["build", "--version", "test"])
    finally:
        os.chdir(cwd)
    assert (project / "credits" / "credits.csv").exists()
    assert (project / "release" / "test" / "pairs.csv").exists()
