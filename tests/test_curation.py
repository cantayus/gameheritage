import pandas as pd
import pytest

from gameheritage import review
from gameheritage.autolink import _robust_center, apply_links, similarity, usable_term
from gameheritage.credits import bibtex, credit_line, csl_json, write_credits
from gameheritage.groundtruth import make_pairs
from gameheritage.ingest import register_images, scan_directory
from gameheritage.tables import Tables, upsert


@pytest.fixture
def linked(project, collection):
    t = Tables.load(project / "data")
    t, _ = register_images(scan_directory(collection), t)
    game = t.images[t.images["title"] == "Castra Praetoria"]["image_id"].iloc[0]
    links = pd.DataFrame(
        [
            {
                "game_id": "assassins-creed-brotherhood",
                "place": "Castra Praetoria",
                "confidence": "high",
                "decision": "",
                "wikidata_qid": "Q1049836",
                "label": "Castra Praetoria",
                "similarity": 1.0,
                "score": 1.4,
                "image_ids": game,
            },
            {
                "game_id": "assassins-creed-brotherhood",
                "place": "Sixth",
                "confidence": "low",
                "decision": "",
                "wikidata_qid": "Q2133",
                "label": "O'Higgins",
                "similarity": 0.2,
                "score": 0.3,
                "image_ids": "",
            },
        ]
    )
    summ = {
        "Q1049836": {
            "label": "Castra Praetoria",
            "instance_of": "castra; archaeological site",
            "description": "Roman fort",
            "country": "Italy",
            "latitude": 41.9,
            "longitude": 12.5,
            "commons_category": "Castra Praetoria",
        }
    }
    t, n = apply_links(t, links, summ)
    assert n == 1  # the low-confidence row is left for review
    return t, game


def test_apply_links_creates_site_and_depiction(linked):
    t, game = linked
    assert list(t.sites["site_id"]) == ["castra-praetoria"]
    dep = t.depictions.iloc[0]
    assert dep["site_id"] == "castra-praetoria" and dep["depiction_class"] == "1"
    img = t.images.set_index("image_id").loc[game]
    assert img["site_id"] == "castra-praetoria" and img["depiction_id"] == dep["depiction_id"]


def test_make_pairs_and_credits(linked, tmp_path):
    t, game = linked
    t.images = upsert(
        t.images,
        [
            {
                "image_id": "gh-realcastra01",
                "role": "real",
                "site_id": "castra-praetoria",
                "title": "Castra Praetoria wall.jpg",
                "author": "Jane Doe",
                "license": "CC BY-SA 4.0",
                "license_url": "https://creativecommons.org/licenses/by-sa/4.0",
                "source_name": "Wikimedia Commons",
                "source_page_url": "https://commons.wikimedia.org/wiki/File:Castra.jpg",
                "retrieved_at": "2026-10-31T20:00:00Z",
                "review_status": "keep",
            }
        ],
        "image_id",
    )
    t, n = make_pairs(t)
    assert n == 1 and t.pairs.iloc[0]["featured"] == "True"
    real = t.images.set_index("image_id").loc["gh-realcastra01"].to_dict()
    line = credit_line({**real, "image_id": "gh-realcastra01"})
    assert line.startswith('"Castra Praetoria wall.jpg" by Jane Doe, licensed CC BY-SA 4.0')
    g = t.images.set_index("image_id").loc[game].to_dict()
    gl = credit_line(g, t.games.set_index("game_id").loc["assassins-creed-brotherhood"].to_dict())
    assert "Assassin's Creed Brotherhood (2010), © Ubisoft Montreal; Ubisoft" in gl
    assert "@misc{gh-realcastra01" in bibtex(t)
    assert any(item["id"] == "gh-realcastra01" for item in csl_json(t))
    paths = write_credits(t, tmp_path / "credits")
    assert all(p.exists() for p in paths)


def test_review_roundtrip(project, collection, tmp_path):
    from gameheritage.clean import flag_manifest

    t = Tables.load(project / "data")
    m = flag_manifest(scan_directory(collection))
    t, _ = register_images(m, t)
    csv, html = review.export_image_triage(t, m, tmp_path / "review", collection)
    sheet = pd.read_csv(csv, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    assert "suggestion" in sheet and html.read_text().count("<figure>") == len(sheet)
    sheet.loc[0, "decision"] = "drop"
    sheet.loc[1, "new_kind"] = "screenshot"
    sheet.to_csv(csv, index=False, encoding="utf-8-sig")
    t, n = review.import_image_triage(t, csv)
    assert n == 2
    sheet.loc[2, "decision"] = "maybe"
    sheet.to_csv(csv, index=False, encoding="utf-8-sig")
    with pytest.raises(review.ReviewError):
        review.import_image_triage(t, csv)


def test_autolink_helpers():
    assert similarity("Castel Sant Angelo", "Castel Sant'Angelo") == 1.0
    assert similarity("Hotel de Cluny", "Hôtel de Cluny") == 1.0
    assert not usable_term("Saint") and not usable_term("Palais") and usable_term("Colosseum")
    rome = [(41.9, 12.5), (41.89, 12.49), (41.91, 12.47), (52.0, 5.0)]
    lat, _lon, r = _robust_center(rome)
    assert abs(lat - 41.9) < 0.1 and r < 50
