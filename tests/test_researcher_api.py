"""The researcher-facing release and API: website-scope release, matches, search,
timeline, GitHub loading and image downloads."""

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pandas as pd
import pytest
import responses
from PIL import Image

import gameheritage as gh
from gameheritage.build import build_release, website_subset
from gameheritage.catalog import RAW_URL, RELEASE_FILES
from gameheritage.download import original_url
from gameheritage.tables import Tables, upsert

SITE = "https://example.org/site/"


def _jpeg(size=(60, 40), color=(10, 20, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def _webp(size=(30, 20)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 0, 0)).save(buf, "WEBP")
    return buf.getvalue()


@pytest.fixture
def tables() -> Tables:
    t = Tables()
    t.games = upsert(
        t.games,
        [
            {"game_id": "acu", "title": "Assassin's Creed Unity", "series": "Assassin's Creed",
             "release_year": "2014", "developer": "Ubisoft Montreal", "publisher": "Ubisoft"},
            {"game_id": "civ5", "title": "Civilization V", "series": "Civilization",
             "release_year": "2010"},
        ],
        "game_id",
    )  # fmt: skip
    t.sites = upsert(
        t.sites,
        [
            {"site_id": "notre-dame", "name": "Notre-Dame de Paris", "country": "France",
             "locality": "Paris", "construction_date": "1163", "notes": "type: cathedral",
             "wikidata_qid": "Q2981"},
            {"site_id": "petra", "name": "Petra", "country": "Jordan",
             "construction_date": "-799", "notes": "type: archaeological site",
             "wikidata_qid": "Q5788"},
            {"site_id": "mont-blanc", "name": "Mont Blanc", "country": "France",
             "notes": "type: mountain", "wikidata_qid": "Q583"},
            {"site_id": "hidden", "name": "Not on the site", "notes": "type: castle"},
        ],
        "site_id",
    )  # fmt: skip
    t.depictions = upsert(
        t.depictions,
        [
            {"depiction_id": "acu--notre-dame", "game_id": "acu", "site_id": "notre-dame",
             "in_game_name": "Notre-Dame", "review_status": "keep"},
            {"depiction_id": "civ5--petra", "game_id": "civ5", "site_id": "petra",
             "in_game_name": "Petra"},
            {"depiction_id": "acu--mont-blanc", "game_id": "acu", "site_id": "mont-blanc",
             "in_game_name": "Mont Blanc"},
            {"depiction_id": "acu--hidden", "game_id": "acu", "site_id": "hidden",
             "in_game_name": "Hidden"},
        ],
        "depiction_id",
    )  # fmt: skip
    commons = "https://upload.wikimedia.org/wikipedia/commons"
    t.images = upsert(
        t.images,
        [
            {"image_id": "gh-g1", "role": "game", "game_id": "acu", "kind": "screenshot",
             "depiction_id": "acu--notre-dame", "site_id": "notre-dame", "title": "ND",
             "source_page_url": "https://ac.fandom.com/wiki/File:ND.png",
             "file_url": "https://static.wikia.nocookie.net/ac/ND.png",
             "drive_path": "secret/ND.png", "rights_status": "display_only"},
            {"image_id": "gh-g2", "role": "game", "game_id": "civ5",
             "depiction_id": "civ5--petra", "site_id": "petra", "title": "Petra",
             "collected_by": "Mija Aleksandraviciute"},
            {"image_id": "gh-g3", "role": "game", "game_id": "acu",
             "depiction_id": "acu--mont-blanc", "site_id": "mont-blanc", "title": "MB"},
            {"image_id": "gh-r1", "role": "real", "site_id": "notre-dame", "title": "ND.jpg",
             "license": "CC BY-SA 4.0", "rights_status": "open",
             "source_page_url": "https://commons.wikimedia.org/wiki/File:ND.jpg",
             "file_url": f"{commons}/thumb/a/ab/ND.jpg/1280px-ND.jpg", "local_path": "x/ND.jpg"},
            {"image_id": "gh-r2", "role": "real", "site_id": "petra", "title": "Petra photo",
             "collected_by": "Mija Aleksandraviciute"},
            {"image_id": "gh-r9", "role": "real", "site_id": "hidden", "title": "unused"},
        ],
        "image_id",
    )  # fmt: skip
    t.pairs = upsert(
        t.pairs,
        [
            {"pair_id": "p1", "depiction_id": "acu--notre-dame", "game_image_id": "gh-g1",
             "real_image_id": "gh-r1"},
            {"pair_id": "p9", "depiction_id": "acu--hidden", "game_image_id": "gh-g1",
             "real_image_id": "gh-r9"},
        ],
        "pair_id",
    )  # fmt: skip
    return t


@pytest.fixture
def site_dir(tmp_path: Path) -> Path:
    """A built website: which places and images it shows, and their sizes."""
    root = tmp_path / "site"
    places = root / "data" / "places"
    places.mkdir(parents=True)

    def place(pid, deps, real):
        doc = {
            "id": pid,
            "real": [{"id": r} for r in real],
            "depictions": [
                {"id": d, "verified": v, "images": [{"id": i} for i in imgs]} for d, v, imgs in deps
            ],
        }
        (places / f"{pid}.json").write_text(json.dumps(doc))

    place("notre-dame", [("acu--notre-dame", True, ["gh-g1"])], ["gh-r1"])
    place("petra", [("civ5--petra", False, ["gh-g2"])], ["gh-r2"])
    place("mont-blanc", [("acu--mont-blanc", False, ["gh-g3"])], [])
    sizes = {i: [1200, 800] for i in ("gh-g1", "gh-g2", "gh-g3", "gh-r1", "gh-r2", "gh-r9")}
    (root / "data" / "image_sizes.json").write_text(json.dumps(sizes))
    return root


@pytest.fixture
def release(tables, site_dir, tmp_path) -> Path:
    return build_release(tables, tmp_path / "rel", "9.9.9", site_dir=site_dir, site_url=SITE)


def test_website_subset_is_what_the_site_shows(tables, site_dir):
    pub = website_subset(tables, site_dir, SITE)
    assert set(pub.sites["site_id"]) == {"notre-dame", "petra", "mont-blanc"}
    assert "gh-r9" not in set(pub.images["image_id"])  # its place is not on the site
    assert list(pub.pairs["pair_id"]) == ["p1"]
    assert set(pub.games["game_id"]) == {"acu", "civ5"}
    s = pub.sites.set_index("site_id")
    assert s.at["notre-dame", "century"] == "12th century"
    assert s.at["notre-dame", "era"] == "Middle Ages"
    assert s.at["petra", "century"] == "8th century BCE"
    assert s.at["petra", "era"] == "Ancient world"
    assert s.at["mont-blanc", "era"] == "Natural formations"
    assert s.at["notre-dame", "wikidata_type"] == "cathedral"
    assert s.at["petra", "web_page"] == SITE + "#/place/petra"
    d = pub.depictions.set_index("depiction_id")
    assert d.at["acu--notre-dame", "verified"] == "true"
    assert d.at["civ5--petra", "verified"] == "false"
    i = pub.images.set_index("image_id")
    assert i.at["gh-g1", "web_url"] == SITE + "img/full/gh-g1.webp"
    assert i.at["gh-g1", "thumb_url"] == SITE + "img/thumb/gh-g1.webp"
    assert i.at["gh-g1", "web_width"] == "1200"
    assert (pub.images["drive_path"] == "").all() and (pub.images["local_path"] == "").all()


def test_release_files_and_metadata(release):
    meta = json.loads((release / "release.json").read_text())
    assert meta["scope"] == "website" and meta["website"] == SITE
    assert meta["counts"] == {"games": 2, "sites": 3, "depictions": 3, "images": 5, "pairs": 1}
    header = (release / "images.csv").read_text().splitlines()[0].split(",")
    assert "drive_path" not in header and "local_path" not in header
    assert header[-4:] == ["web_url", "thumb_url", "web_width", "web_height"]
    dp = json.loads((release / "datapackage.json").read_text())
    sites = next(r for r in dp["resources"] if r["name"] == "sites")
    fields = {f["name"]: f for f in sites["schema"]["fields"]}
    assert fields["era"]["description"] and fields["name"]["description"]


def test_load_keeps_release_columns_and_queries(release):
    cat = gh.load(release)
    assert cat.meta["version"] == "9.9.9"
    assert "era" in cat.sites().columns
    assert list(cat.places(era="Middle Ages")["site_id"]) == ["notre-dame"]
    m = cat.matches(game="Assassin's Creed Unity")
    assert set(m["site_id"]) == {"notre-dame", "mont-blanc"}
    row = m.set_index("site_id").loc["notre-dame"]
    assert row["game_web_url"] == SITE + "img/full/gh-g1.webp"
    assert row["real_web_url"] == SITE + "img/full/gh-r1.webp"
    assert row["n_real_images"] == 1 and row["century"] == "12th century"
    assert "Ubisoft" in row["game_credit_line"]
    assert list(cat.matches(verified=True)["depiction_id"]) == ["acu--notre-dame"]
    assert set(cat.matches(verified=False)["depiction_id"]) == {"civ5--petra", "acu--mont-blanc"}
    assert list(cat.matches(era=["Ancient world"])["site_id"]) == ["petra"]
    assert list(cat.matches(country="jordan")["site_id"]) == ["petra"]


def test_search_finds_places_games_and_in_game_names(release):
    cat = gh.load(release)
    assert set(cat.search("civilization")["site_id"]) == {"petra"}
    assert set(cat.search("PARIS")["site_id"]) == {"notre-dame"}
    assert set(cat.search("Mont")["site_id"]) == {"mont-blanc"}
    assert cat.search("  ").empty


def test_timeline_orders_eras(release):
    cat = gh.load(release)
    tl = cat.timeline()
    assert list(tl["era"]) == ["Ancient world", "Middle Ages", "Natural formations"]
    assert tl.set_index("era").at["Middle Ages", "places"] == 1
    cent = cat.timeline("century")
    assert list(cent["century"]) == ["8th century BCE", "12th century"]


def test_describe_lists_every_column():
    d = gh.describe("sites")
    assert {"site_id", "era", "century", "web_page"} <= set(d["column"])
    assert (d["description"] != "").all()
    assert set(gh.describe()["table"]) == {"games", "sites", "depictions", "images", "pairs"}
    with pytest.raises(KeyError):
        gh.describe("nope")


def test_cite():
    assert "Aleksandraviciute" in gh.cite() and gh.__version__ in gh.cite()
    assert gh.cite("bibtex").startswith("@misc{")
    with pytest.raises(ValueError):
        gh.cite("chicago")


def test_original_url_of_commons_thumbnail():
    thumb = "https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/ND.jpg/1280px-ND.jpg"
    assert original_url(thumb) == "https://upload.wikimedia.org/wikipedia/commons/a/ab/ND.jpg"
    other = "https://static.wikia.nocookie.net/ac/ND.png"
    assert original_url(other) == other


@responses.activate
def test_download_web_copies_with_attribution(release, tmp_path):
    cat = gh.load(release)
    for i in ("gh-g1", "gh-r1"):
        responses.get(SITE + f"img/full/{i}.webp", body=_webp())
    rows = cat.matches(site="Notre-Dame de Paris")
    with pytest.warns(UserWarning, match="copyrighted"):
        out = gh.download(rows, tmp_path / "dl", catalog=cat)
    assert set(out["copy"]) == {"web"}
    assert (tmp_path / "dl" / "game" / "gh-g1.webp").exists()
    assert (tmp_path / "dl" / "real" / "gh-r1.webp").exists()
    attr = pd.read_csv(tmp_path / "dl" / "ATTRIBUTION.csv")
    assert set(attr["image_id"]) == {"gh-g1", "gh-r1"}
    assert attr["credit_line"].str.len().min() > 20


@responses.activate
def test_download_original_and_fallback(release, tmp_path):
    cat = gh.load(release)
    full = "https://upload.wikimedia.org/wikipedia/commons/a/ab/ND.jpg"
    responses.get(full, body=_jpeg())
    out = gh.download(["gh-r1"], tmp_path / "o", catalog=cat, size="original")
    assert out.loc[0, "copy"] == "original" and out.loc[0, "path"].endswith(".jpg")
    # Mija's photo has no source URL: the web copy stands in.
    responses.get(SITE + "img/full/gh-r2.webp", body=_webp())
    out = gh.download(["gh-r2"], tmp_path / "o", catalog=cat, size="original")
    assert out.loc[0, "copy"] == "web"
    out = gh.download(["gh-r2"], tmp_path / "p", catalog=cat, size="original", fallback=False)
    assert out.loc[0, "status"] == "no file available"


@responses.activate
def test_download_reports_failures_and_licenses(release, tmp_path):
    cat = gh.load(release)
    out = gh.download(["gh-r1", "gh-r2"], tmp_path / "f", catalog=cat, licenses=["CC BY"])
    assert set(out["status"]) == {"skipped: license"}
    responses.get(re.compile(r".*gh-g3\.webp"), status=404)
    with pytest.warns(UserWarning, match="copyrighted"):
        out = gh.download(["gh-g3"], tmp_path / "f", catalog=cat)
    assert out.loc[0, "status"].startswith("failed: web")
    with pytest.raises(ValueError):
        gh.download(["gh-r1"], tmp_path / "f", catalog=cat, size="huge")


@responses.activate
def test_fetch_release_from_github(release, tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEHERITAGE_CACHE", str(tmp_path / "cache"))
    for name in RELEASE_FILES:
        f = release / name
        url = RAW_URL.format(repo="cantayus/gameheritage", ref="v9.9.9", name=name)
        if f.exists():
            responses.get(url, body=f.read_bytes())
        else:
            responses.get(url, status=404)
    cat = gh.load(version="9.9.9")
    assert cat.meta["version"] == "9.9.9" and len(cat.sites()) == 3
    calls = len(responses.calls)
    gh.load(version="v9.9.9")  # tagged releases are cached
    assert len(responses.calls) == calls
    assert (tmp_path / "cache" / "releases" / "v9.9.9" / "sites.csv").exists()


@responses.activate
def test_fetch_release_failure_is_clear(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEHERITAGE_CACHE", str(tmp_path / "cache"))
    responses.get(re.compile(r"https://raw\.githubusercontent\.com/.*"), status=404)
    with pytest.raises(RuntimeError, match="could not download release"):
        gh.load(version="0.0.1")


def test_bundled_release_loads():
    cat = gh.load()
    assert len(cat.sites()) > 0 and len(cat.games()) > 0
    assert cat.meta.get("scope") == "website"
    assert {"era", "century", "year"} <= set(cat.sites().columns)
    assert "web_url" in cat.images().columns
