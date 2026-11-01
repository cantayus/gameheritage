"""Regression tests for problems found in code review."""

import time

import pandas as pd
import pytest

from gameheritage.autolink import apply_links, merge_decisions
from gameheritage.groundtruth import make_pairs
from gameheritage.schema import license_family
from gameheritage.sources.fandom import base_url, wiki_from_cdn_path
from gameheritage.tables import ConcurrentEditError, Tables, empty_table, upsert


def test_upsert_formats_whole_numbers():
    df = upsert(
        empty_table("games"),
        [{"game_id": "a", "title": "A", "release_year": 2011.0}, {"game_id": "b", "title": "B"}],
        "game_id",
    )
    assert df.set_index("game_id").at["a", "release_year"] == "2011"


def test_save_refuses_when_file_changed_on_disk(tmp_path):
    t = Tables()
    t.games = upsert(t.games, [{"game_id": "a", "title": "A"}], "game_id")
    t.save(tmp_path)
    mine = Tables.load(tmp_path)
    other = Tables.load(tmp_path)
    time.sleep(0.01)
    other.games = upsert(other.games, [{"game_id": "b", "title": "B"}], "game_id")
    other.save(tmp_path)
    mine.games = upsert(mine.games, [{"game_id": "c", "title": "C"}], "game_id")
    with pytest.raises(ConcurrentEditError):
        mine.save(tmp_path)
    assert set(Tables.load(tmp_path).games["game_id"]) == {"a", "b"}


def test_license_family_is_exact():
    assert license_family("CC BY 4.0") == "cc by"
    assert license_family("CC BY-NC-SA 2.0") == "cc by-nc-sa"
    assert license_family("CC BY-SA 3.0") != license_family("CC BY 3.0")


def test_language_wikis():
    assert wiki_from_cdn_path("assassinscreed_de") == "assassinscreed/de"
    assert base_url("assassinscreed/de") == "https://assassinscreed.fandom.com/de"
    assert wiki_from_cdn_path("dawnofman_gamepedia_en") == "dawnofman"


def _links(decision=""):
    return pd.DataFrame(
        [
            {
                "game_id": "g",
                "place": "Pantheon",
                "confidence": "high",
                "decision": decision,
                "wikidata_qid": "Q99309",
                "label": "Pantheon",
                "similarity": "1.0",
                "score": "1.5",
                "image_ids": "img1",
            }
        ]
    )


def _tables():
    t = Tables()
    t.games = upsert(t.games, [{"game_id": "g", "title": "G"}], "game_id")
    t.images = upsert(t.images, [{"image_id": "img1", "role": "game", "game_id": "g"}], "image_id")
    return t


def test_drop_undoes_an_applied_link():
    summ = {"Q99309": {"label": "Pantheon", "instance_of": "Roman temple"}}
    t, n = apply_links(_tables(), _links(), summ)
    assert n == 1 and t.images.iloc[0]["site_id"] == "pantheon"
    t, n = apply_links(t, _links("drop"), summ)
    assert n == 0
    assert t.images.iloc[0]["site_id"] == "" and t.images.iloc[0]["depiction_id"] == ""
    assert t.depictions.iloc[0]["review_status"] == "drop"


def test_merge_decisions_keeps_review():
    old = _links("keep").assign(wikidata_qid="Q123")
    merged = merge_decisions(_links(), old)
    assert merged.iloc[0]["decision"] == "keep" and merged.iloc[0]["wikidata_qid"] == "Q123"


def test_make_pairs_skips_dropped_and_unsourced_real_images():
    summ = {"Q99309": {"label": "Pantheon", "instance_of": "Roman temple"}}
    t, _ = apply_links(_tables(), _links(), summ)
    real = {
        "role": "real",
        "site_id": "pantheon",
        "license": "CC BY-SA 4.0",
        "source_page_url": "https://commons.wikimedia.org/wiki/File:P.jpg",
    }
    t.images = upsert(
        t.images,
        [
            {"image_id": "big", **real, "width": "4000", "review_status": "drop"},
            {"image_id": "small", **real, "width": "1000"},
            {"image_id": "nosrc", **real, "width": "3000", "source_page_url": ""},
        ],
        "image_id",
    )
    t, n = make_pairs(t)
    assert n == 1 and t.pairs.iloc[0]["real_image_id"] == "small"


def test_make_pairs_on_empty_tables():
    _t, n = make_pairs(Tables())
    assert n == 0


def test_regions_and_people_are_not_collected():
    summ = {"Q99309": {"label": "Galicia", "instance_of": "autonomous community of Spain"}}
    t, n = apply_links(_tables(), _links(), summ)
    assert n == 0 and t.images.iloc[0]["site_id"] == ""


def test_is_built():
    from gameheritage.autolink import is_built

    assert is_built("Roman temple; archaeological site")
    assert is_built("urban park", "park in Paris")
    assert is_built("commune of France")
    assert is_built("mountain", "mountain in the Alps")  # natural formations count
    assert is_built("national park")
    assert not is_built("human", "Italian painter")
    assert not is_built("autonomous community of Spain")


def test_name_signals_spot_common_nouns_and_rivals():
    from gameheritage.autolink import all_generic, name_signals, usable_term

    summ = {
        "Q1": {
            "label": "Grand Staircase",
            "instance_of": "grand staircase",
            "latitude": 38.9,
            "sitelinks": 1,
        },
        "Q2": {"label": "obelisk", "instance_of": "", "latitude": None, "sitelinks": 75},
        "Q3": {"label": "Rollo", "instance_of": "human", "latitude": None, "sitelinks": 80},
        "Q4": {"label": "Rollo", "instance_of": "statue", "latitude": 49.4, "sitelinks": 1},
        "Q5": {
            "label": "Tower Bridge",
            "instance_of": "bascule bridge",
            "latitude": 51.5,
            "sitelinks": 86,
        },
    }
    assert name_signals("Grand Staircase", ["Q1"], summ)[0]
    assert name_signals("Obelisk", ["Q2"], summ) == (True, 75)
    assert name_signals("Rollo", ["Q3", "Q4"], summ) == (False, 80)
    assert name_signals("Tower Bridge", ["Q5"], summ) == (False, 0)
    assert usable_term("Tower Bridge") and all_generic("Tower Bridge")
    assert not all_generic("Notre-Dame de Paris")


def test_great_wall_counts_as_built():
    from gameheritage.autolink import is_built

    assert is_built("cordon", "series of fortifications built along the border of China")


def test_stricter_run_withdraws_unreviewed_auto_links(tmp_path):
    from gameheritage.autolink import prune_orphan_sites

    summ = {"Q99309": {"label": "Pantheon", "instance_of": "Roman temple"}}
    t, n = apply_links(_tables(), _links(), summ)
    assert n == 1 and t.images.iloc[0]["site_id"] == "pantheon"
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "photo.jpg").write_bytes(b"x")
    real = {"image_id": "photo", "role": "real", "site_id": "pantheon"}
    real |= {"collected_by": "gameheritage 0.1.0", "local_path": "p/photo.jpg"}
    t.images = upsert(t.images, [real], "image_id")
    weaker = _links().assign(confidence="low", issues="common noun, not a proper name")
    t, n = apply_links(t, weaker, summ)
    assert n == 0 and t.images.set_index("image_id").at["img1", "site_id"] == ""
    assert not len(t.depictions)
    t, sites, files = prune_orphan_sites(t, tmp_path)
    assert (sites, files) == (1, 1) and not len(t.sites)
    assert "photo" not in set(t.images["image_id"]) and not (tmp_path / "p").exists()


def test_reviewed_links_are_not_withdrawn():
    summ = {"Q99309": {"label": "Pantheon", "instance_of": "Roman temple"}}
    t, _ = apply_links(_tables(), _links(), summ)
    t.depictions.loc[0, "review_status"] = "keep"
    t, _ = apply_links(t, _links().assign(confidence="low"), summ)
    assert len(t.depictions) == 1 and t.images.iloc[0]["site_id"] == "pantheon"


def test_second_cluster_is_not_isolated():
    from gameheritage.autolink import _extra_words, _isolated

    florence = [(43.77, 11.25)] * 6
    venice = [(45.44, 12.33), (45.43, 12.34)]
    frari = {"latitude": 45.437, "longitude": 12.326}
    hesse = {"latitude": 50.5, "longitude": 9.7}
    assert not _isolated(frari, florence + venice)
    assert _isolated(hesse, florence + venice)
    assert _extra_words("Howler Monkey", "Howler Monkey Hotel")
    assert not _extra_words("Agora of Athens", "Agora of Athens")
    assert not _extra_words("Tower Bridge", "Tower Bridge Road")  # "road" is generic
