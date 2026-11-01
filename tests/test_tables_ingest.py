import pandas as pd

from gameheritage.clean import flag_manifest
from gameheritage.ingest import path_image_id, register_images, scan_directory, scan_listing
from gameheritage.tables import Tables, empty_table, upsert, validate_rows
from gameheritage.validate import validate


def test_upsert_fills_without_overwriting():
    df = upsert(empty_table("games"), [{"game_id": "a", "title": "A"}], "game_id")
    df = upsert(df, [{"game_id": "a", "title": "B", "series": "S"}], "game_id")
    row = df.set_index("game_id").loc["a"]
    assert row["title"] == "A" and row["series"] == "S"
    df = upsert(df, [{"game_id": "a", "title": "B", "series": ""}], "game_id", overwrite=True)
    row = df.set_index("game_id").loc["a"]
    assert row["title"] == "B" and row["series"] == "S"  # empty values never blank cells


def test_schema_errors_are_reported():
    df = pd.DataFrame([{"game_id": "Bad ID", "title": "x", "wikidata_qid": "nope"}])
    errs = validate_rows(df.reindex(columns=empty_table("games").columns, fill_value=""), "games")
    assert any("game_id" in e for e in errs) and any("wikidata_qid" in e for e in errs)


def test_register_from_listing_then_content(project, collection, tmp_path):
    t = Tables.load(project / "data")
    # 1) listing only: path-based IDs
    entries = [
        {"path": p.relative_to(collection).as_posix(), "size": p.stat().st_size}
        for p in collection.rglob("*")
        if p.is_file()
    ]
    t, unmapped = register_images(scan_listing({"entries": entries}), t)
    assert not unmapped
    ids_before = dict(zip(t.images["drive_path"], t.images["image_id"]))
    castra = "Assassin's Creed Brotherhood/game data/ACB DB Castra Praetoria.png"
    assert ids_before[castra] == path_image_id(castra)
    # 2) content scan: same IDs, content facts filled, duplicates folded into also_at
    t, _ = register_images(flag_manifest(scan_directory(collection)), t)
    assert dict(zip(t.images["drive_path"], t.images["image_id"]))[castra] == ids_before[castra]
    row = t.images.set_index("drive_path").loc[castra]
    assert row["sha256"] and row["true_format"] == "PNG" and row["width"] == "640"
    real = t.images[t.images["role"] == "real"]
    assert len(real) == 2  # registered from the listing before content was known
    game = t.images[t.images["game_id"] == "anno-1800"]
    assert len(game) == 2
    assert (game["source_page_url"] == "").all()  # only verified sources are recorded
    assert set(game["in_game_place"]) == {"Bakery", "Konglig Museum"}


def test_register_dedupes_by_content(project, collection):
    t = Tables.load(project / "data")
    t, _ = register_images(scan_directory(collection), t)
    real = t.images[t.images["role"] == "real"]
    assert len(real) == 1  # the pantheon file is a byte copy of the colosseum file
    assert "02_pantheon_rome.jpg" in real.iloc[0]["also_at"]
    assert not any(t.images["drive_path"].str.endswith("empty.png"))


def test_validate_publish_gate(project, collection):
    t = Tables.load(project / "data")
    t, _ = register_images(scan_directory(collection), t)
    game_id = t.images[t.images["role"] == "game"]["image_id"].iloc[0]
    real_id = t.images[t.images["role"] == "real"]["image_id"].iloc[0]
    t.sites = upsert(t.sites, [{"site_id": "colosseum", "name": "Colosseum"}], "site_id")
    t.depictions = upsert(
        t.depictions,
        [
            {
                "depiction_id": "acb--colosseum",
                "game_id": "assassins-creed-brotherhood",
                "site_id": "colosseum",
                "in_game_name": "Colosseum",
            }
        ],
        "depiction_id",
    )
    t.pairs = upsert(
        t.pairs,
        [
            {
                "pair_id": "p1",
                "depiction_id": "acb--colosseum",
                "game_image_id": game_id,
                "real_image_id": real_id,
                "review_status": "keep",
            }
        ],
        "pair_id",
    )
    rep = validate(t)
    assert any("publish gate" in e and "no free license" in e for e in rep.errors)
    t.images = upsert(
        t.images,
        [
            {
                "image_id": real_id,
                "license": "CC BY-SA 4.0",
                "source_page_url": "https://commons.wikimedia.org/wiki/File:X.jpg",
            },
            {"image_id": game_id, "source_page_url": "https://assassinscreed.fandom.com/wiki/F"},
        ],
        "image_id",
        overwrite=True,
    )
    assert validate(t).ok, str(validate(t))
