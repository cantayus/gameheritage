import pytest

from gameheritage.clean import flag_manifest, suggestion
from gameheritage.ingest import scan_directory, scan_listing
from gameheritage.names import humanize, slugify
from gameheritage.parse import guess_kind, parse_filename, place_from_label, sanitize_key
from gameheritage.schema import is_free_license


@pytest.mark.parametrize(
    "path, pattern, label, place, kind",
    [
        (
            "A Plague Tale  Innocence/game data/Afflicted_Church__File_Church_Courtyard_jpg.jpg",
            "fandom_file", "Church Courtyard", "Afflicted Church", "unknown",
        ),
        ("Anno 1800/game data/Bakery__page_image.jpg", "fandom_page_image", "Bakery", "Bakery",
         "unknown"),
        ("AC/game data/ACB DB Castra Praetoria.png", "hand_named", "Castra Praetoria",
         "Castra Praetoria", "database_entry"),
        ("AC/game data/ACB Art Colosseum.jpg", "hand_named", "Colosseum", "Colosseum",
         "concept_art"),
        ("LA noire/game data/LA County Art Museum - Entrance.JPG", "hand_named",
         "LA County Art Museum - Entrance", "LA County Art Museum", "unknown"),
        ("civilization 4/game data/Mt__Rushmore__Civ4__jpg.png", "fandom_download",
         "Mt Rushmore", "Mt Rushmore", "unknown"),
        ("Empire  Total War/game data/College_Empire_Total_War___File_College_png.jpg",
         "fandom_file", "College", "College", "unknown"),
        ("Battlefield 1/game data/Soissons/Soissons Frontlines Aerodrome.png", "hand_named",
         "Soissons Frontlines Aerodrome", "Soissons Aerodrome", "unknown"),
    ],
)  # fmt: skip
def test_parse_patterns(path, pattern, label, place, kind):
    p = parse_filename(path)
    assert (p.pattern, p.label, p.place_hint, p.kind_guess) == (pattern, label, place, kind)


def test_parse_cdn_and_real():
    p = parse_filename(
        "Like a dragon Ishin/game data/"
        "https___static_wikia_nocookie_net_yakuza_images_7_7c_Shinsengumi_Barracks_png_revision_"
        "latest_cb_202.jpg"
    )
    assert p.pattern == "fandom_cdn"
    assert p.fandom_wiki_path == "yakuza"
    assert p.fandom_file == "Shinsengumi_Barracks.png"
    r = parse_filename("Sekiro/real images/01_ashina_castle_japan.jpg")
    assert r.pattern == "real_numbered" and r.real_subject == "ashina castle japan"


def test_hand_named_fandom_key_drops_copy_suffix():
    p = parse_filename("LA noire/game data/Hall of Records - Entrance (1).jpg")
    assert p.fandom_file == "Hall of Records - Entrance.jpg"
    assert p.fandom_key == sanitize_key("Hall of Records - Entrance.jpg")


def test_names():
    assert slugify("Assassin’s Creed: Unity") == "assassins-creed-unity"
    assert humanize("Shakespeare_s_Theater") == "Shakespeare's Theater"
    assert place_from_label("Model Statue of Liberty") == "Statue of Liberty"
    assert guess_kind("AC3 Map Bunker Hill") == "map"


@pytest.mark.parametrize(
    "lic, ok",
    [
        ("CC BY-SA 4.0", True),
        ("Public domain", True),
        ("CC0", True),
        ("cc-by-sa-3.0", True),
        ("CC BY-NC 2.0", False),
        ("CC BY-ND 4.0", False),
        ("GFDL", False),
        ("Fair use", False),
        ("", False),
    ],
)
def test_free_license(lic, ok):
    assert is_free_license(lic) is ok


def test_scan_directory_and_flags(collection):
    m = scan_directory(collection, compute_phash=True)
    assert "desktop.ini" not in set(m["filename"])
    f = flag_manifest(m).set_index("filename")
    assert "empty_file" in f.at["empty.png", "flags"]
    assert "icon_like" in f.at["AoE icon.png", "flags"]
    assert "extension_mismatch" in f.at["Webp_saved_as_jpg.jpg", "flags"]
    assert f.at["Webp_saved_as_jpg.jpg", "true_format"] == "WEBP"
    assert "exact_duplicate" in f.at["02_pantheon_rome.jpg", "flags"]
    assert "same_file_different_subjects" in f.at["01_colosseum_rome.jpg", "flags"]
    assert "real_unsourced" in f.at["01_colosseum_rome.jpg", "flags"]
    assert "copy_suffix" in f.at["Colosseum copy (1).jpg", "flags"]
    assert suggestion(f.at["empty.png", "flags"]) == "drop"


def test_scan_listing_flags(listing):
    m = flag_manifest(scan_listing(listing)).set_index("filename")
    assert "desktop.ini" not in m.index
    assert "placeholder" in m.at["02_senpou_temple_japan.jpg", "flags"]
    assert "size_twin" in m.at["Senpou Temple 2.jpg", "flags"]
    assert "other_media" in m.at["sound.ogg", "flags"]
