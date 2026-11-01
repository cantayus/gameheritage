import re

import pytest
import responses

from gameheritage.sources import commons, europeana, fandom, wikidata
from gameheritage.sources.http import HttpClient, pace_key


@pytest.fixture
def client(tmp_path):
    return HttpClient(contact="test@example.org", cache_dir=tmp_path / "cache", min_interval=0)


def test_pacing_groups_wikimedia_apis():
    assert pace_key("commons.wikimedia.org") == pace_key("www.wikidata.org") == "wikimedia-api"
    assert pace_key("en.wikipedia.org") == "wikimedia-api"
    assert pace_key("query.wikidata.org") == "query.wikidata.org"
    assert pace_key("assassinscreed.fandom.com") == "assassinscreed.fandom.com"


@responses.activate
def test_commons_file_info_parses_license(client):
    client.intervals = {}
    responses.get(
        re.compile(r"https://commons\.wikimedia\.org/w/api\.php.*"),
        json={
            "query": {
                "pages": [
                    {
                        "title": "File:Colosseo 2020.jpg",
                        "imageinfo": [
                            {
                                "url": "https://upload.wikimedia.org/x/Colosseo_2020.jpg",
                                "thumburl": "https://upload.wikimedia.org/x/1280px-C.jpg",
                                "descriptionurl": "https://commons.wikimedia.org/wiki/File:C.jpg",
                                "width": 4000,
                                "height": 3000,
                                "mime": "image/jpeg",
                                "sha1": "abc",
                                "size": 123,
                                "extmetadata": {
                                    "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                    "LicenseUrl": {"value": "https://creativecommons.org/b"},
                                    "Artist": {"value": '<a href="/u">Jane &amp; Co</a>'},
                                    "AttributionRequired": {"value": "true"},
                                },
                            }
                        ],
                    }
                ]
            }
        },
    )
    files = commons.file_info(client, ["Colosseo 2020.jpg"])
    assert len(files) == 1
    f = files[0]
    assert f.is_free and f.author == "Jane & Co" and f.attribution_required is True
    assert "User-Agent" in responses.calls[0].request.headers
    assert "test@example.org" in responses.calls[0].request.headers["User-Agent"]
    # second call is served from the disk cache
    commons.file_info(client, ["Colosseo 2020.jpg"])
    assert len(responses.calls) == 1


@responses.activate
def test_http_retries_after_429(client):
    client.intervals = {}
    url = "https://example.org/api"
    responses.get(url, status=429, headers={"Retry-After": "0"})
    responses.get(url, json={"ok": True})
    client.max_wait = 0.01
    assert client.get_json(url) == {"ok": True}


@responses.activate
def test_wikidata_search_many_and_summaries(client):
    client.intervals = {}
    responses.get(
        re.compile(r"https://query\.wikidata\.org/sparql.*"),
        json={
            "results": {
                "bindings": [
                    {
                        "term": {"value": "Colosseum"},
                        "item": {"value": "http://www.wikidata.org/entity/Q10285"},
                        "ordinal": {"value": "0"},
                    },
                    {
                        "term": {"value": "Colosseum"},
                        "item": {"value": "http://www.wikidata.org/entity/Q1"},
                        "ordinal": {"value": "1"},
                    },
                ]
            }
        },
    )
    assert wikidata.search_many(client, ["Colosseum"]) == {"Colosseum": ["Q10285", "Q1"]}


def test_wikidata_year_parsing():
    assert wikidata.year_from_time({"time": "+1889-03-31T00:00:00Z"}) == 1889
    assert wikidata.year_from_time({"time": "-0070-01-01T00:00:00Z"}) == -70
    assert wikidata.year_from_time(None) is None


def test_fandom_urls_and_matching():
    assert fandom.file_page_url("assassinscreed", "ACB DB Castra Praetoria.png") == (
        "https://assassinscreed.fandom.com/wiki/File:ACB_DB_Castra_Praetoria.png"
    )
    assert fandom.wiki_from_cdn_path("dawnofman_gamepedia_en") == "dawnofman"
    assert fandom._matches("Mt__Rushmore__Civ4__jpg", "Mt. Rushmore (Civ4).jpg")
    assert fandom._matches("Taj_Mahal_in-game__Civ6__png", "Taj Mahal in-game (Civ6).png")
    assert not fandom._matches("Colosseum_png", "Pantheon.png")


def test_europeana_rights():
    assert europeana.is_open_rights("http://creativecommons.org/publicdomain/mark/1.0/")
    assert not europeana.is_open_rights("http://rightsstatements.org/vocab/InC/1.0/")
    assert europeana.rights_label("http://creativecommons.org/licenses/by-sa/4.0/") == (
        "CC BY-SA 4.0"
    )
