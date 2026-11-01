import io
import re

import responses
from PIL import Image

from gameheritage.harvest import harvest
from gameheritage.sources.http import HttpClient
from gameheritage.tables import Tables, upsert


def _jpeg(size=(800, 450)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 80, 70)).save(buf, "JPEG")
    return buf.getvalue()


@responses.activate
def test_harvest_registers_images_with_provenance(tmp_path):
    api = "https://assassinscreed.fandom.com/api.php"

    def handler(request):
        q = request.params
        if q.get("list") == "categorymembers":
            body = {"query": {"categorymembers": [{"ns": 0, "title": "Notre-Dame de Paris"}]}}
        elif q.get("prop") == "images":
            body = {
                "query": {
                    "pages": [
                        {
                            "title": "Notre-Dame de Paris",
                            "images": [
                                {"title": "File:ACU Notre-Dame.png"},
                                {"title": "File:Templar icon.png"},
                                {"title": "File:ACU Notre-Dame tiny.png"},
                            ],
                        }
                    ]
                }
            }
        else:
            pages = []
            for t in q["titles"].split("|"):
                name = t.removeprefix("File:")
                pages.append(
                    {
                        "title": t,
                        "imageinfo": [
                            {
                                "url": f"https://static.wikia.nocookie.net/ac/{name}",
                                "descriptionurl": f"https://assassinscreed.fandom.com/wiki/{t}",
                                "user": "Editor",
                                "timestamp": "2015-01-02T00:00:00Z",
                                "extmetadata": {},
                            }
                        ],
                    }
                )
            body = {"query": {"pages": pages}}
        import json

        return 200, {}, json.dumps(body)

    responses.add_callback(responses.GET, re.compile(re.escape(api) + ".*"), callback=handler)
    responses.get("https://static.wikia.nocookie.net/ac/ACU Notre-Dame.png", body=_jpeg())
    responses.get(
        "https://static.wikia.nocookie.net/ac/ACU Notre-Dame tiny.png", body=_jpeg((100, 60))
    )
    t = Tables()
    t.games = upsert(
        t.games, [{"game_id": "assassins-creed-unity", "title": "AC Unity"}], "game_id"
    )
    client = HttpClient(cache_dir=tmp_path / "c", min_interval=0)
    t, msgs = harvest(
        client,
        t,
        wiki="assassinscreed",
        game_id="assassins-creed-unity",
        category="Locations in Assassin's Creed Unity",
        dest=tmp_path / "imgs",
        require_structure=False,  # the Wikidata check is tested separately
    )
    assert len(t.images) == 1, msgs  # icon filtered by name, tiny image by size
    row = t.images.iloc[0]
    assert row["in_game_place"] == "Notre-Dame de Paris"
    assert row["source_page_url"].endswith("File:ACU Notre-Dame.png")
    assert "Editor" in row["notes"] and row["rights_status"] == "display_only"
    assert "Locations in Assassin's Creed Unity" in row["collection_method"]


def test_prune_unlinked_keeps_matched_and_mijas_images(tmp_path):
    from gameheritage.harvest import prune_unlinked

    (tmp_path / "h").mkdir()
    for n in ("a", "b"):
        (tmp_path / "h" / f"{n}.jpg").write_bytes(b"x")
    mine = {"role": "game", "collected_by": "gameheritage 0.1.0"}
    t = Tables()
    t.images = upsert(
        t.images,
        [
            {"image_id": "a", **mine, "site_id": "pantheon", "local_path": "h/a.jpg"},
            {"image_id": "b", **mine, "in_game_place": "Abandoned Church", "local_path": "h/b.jpg"},
            {"image_id": "m", "role": "game", "collected_by": "Mija Aleksandraviciute"},
        ],
        "image_id",
    )
    log = tmp_path / "pruned.csv"
    t, msgs = prune_unlinked(t, tmp_path, log)
    assert set(t.images["image_id"]) == {"a", "m"}, msgs
    assert (tmp_path / "h" / "a.jpg").exists() and not (tmp_path / "h" / "b.jpg").exists()
    assert "Abandoned Church" in log.read_text()
    prune_unlinked(t, tmp_path, log)  # re-running does not duplicate the log
    assert log.read_text().count("Abandoned Church") == 1
