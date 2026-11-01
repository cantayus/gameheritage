# gameheritage

**Digital heritage in video games: historical structures in games, side by side
with the real places.**

`gameheritage` is the dataset and Python package of the Video Game Digital
Heritage project (Cantay Caliskan and Mija Aleksandraviciute, University of
Rochester). Video games rebuild cathedrals, temples, bridges, battlefields and
mountains that millions of players come to know first on a screen. The
catalogue links each of those in-game places to the real place it represents.
For both sides it records the images, the historical facts and the source of
every item.

The same data can be browsed on the project website:
<https://cantayus.github.io/video-game-digital-heritage/>

| Release 0.1.0 (1 November 2026) | |
| --- | --- |
| Games | 76 (Assassin's Creed, Civilization, Battlefield, Call of Duty, Total War, Fallout, ...) |
| Real places | 1,116 in 103 countries, from the Neolithic to the 21st century |
| In-game versions of those places | 1,452 |
| Game images | 3,440 (from the games' Fandom wikis and the project's own collection) |
| Photographs of the real places | 1,547 (1,442 under an open license, mostly Wikimedia Commons) |
| Candidate image pairs (game image + real photo) | 3,285 |

## Install

```bash
pip install "git+https://github.com/cantayus/gameheritage@v0.1.0"
```

Python 3.10 or newer. The catalogue is installed with the package, so loading
it needs no internet connection; only image downloads do.

## Quick start

```python
import gameheritage as gh

cat = gh.load()                       # the catalogue (release 0.1.0)
cat                                   # <gameheritage.Catalog 0.1.0: 76 games, 1116 sites, ...>

# Find places and games by name, city or in-game name
cat.search("Notre-Dame")
cat.search("Kyoto")

# One row per in-game version of a real place, ready to compare
unity = cat.matches(game="Assassin's Creed Unity")
unity[["in_game_name", "site_name", "century", "era", "game_web_url", "real_web_url"]]

# Filter by history and geography
cat.matches(era="Middle Ages", country="France")
cat.places(era="Ancient world")       # the real places, with year, century and era
cat.timeline()                        # places, games and in-game versions per era

# Look at a pair and download the images, with a credit line for every file
gh.show(unity.iloc[0])                # game image and real photo side by side (Jupyter)
gh.download(unity, "images/")         # web copies + images/ATTRIBUTION.csv
gh.download(unity, "originals/", role="real", size="original",
            licenses=["Public domain", "CC0", "CC BY", "CC BY-SA"])

gh.describe("sites")                  # what every column means
print(gh.cite())                      # how to cite the dataset
```

A longer walk-through is in [`examples/quickstart.ipynb`](examples/quickstart.ipynb).

## What you can do with it

| Function | What it gives you |
| --- | --- |
| `gh.load()` | The catalogue installed with the package. `gh.load(version="latest")` downloads the newest release from GitHub; `gh.load(version="0.1.0")` pins one. |
| `cat.matches(game, site, era=, country=, verified=)` | One row per in-game version: game, real place, year, century, era, how the match was made, image counts, and one game image and one real photo with URLs and credits. |
| `cat.search(text)` | Matches whose game, place, city, country or in-game name contains the text. |
| `cat.places(...)` / `cat.sites(...)` | The real places. Any column can be a filter (`country="Japan"`, `era="Early modern"`); `search=` looks in names and cities. |
| `cat.games(...)` | The games, e.g. `cat.games(series="Civilization")`. |
| `cat.images(...)` | Every image with source page, original file URL, hosted web copy, author, license and a ready-made `credit_line`. |
| `cat.pairs(...)` | Candidate comparisons of one game image with one real photo. |
| `cat.timeline(by="era" or "century")` | Counts per historical period. |
| `cat.credits()` | Provenance and credit line for every image. |
| `gh.download(rows, folder, role=, size=, licenses=)` | The images behind any of these tables, saved as `folder/game/` and `folder/real/` with `ATTRIBUTION.csv`. |
| `gh.show(row)` | Game image and real photo side by side, with full credits, as a PIL image. |
| `gh.describe(table)` | The data dictionary. |
| `gh.cite(style="text" or "bibtex")` | The citation. |

Every table is a pandas DataFrame, so you can join, group and export as usual.

## The data

Five linked tables. A **depiction** joins a **game** to a real **site**.
**Images** belong to a depiction (game images) or to a site (real
photographs). **Pairs** put one game image next to one real photo.

```
games ──< depictions >── sites
              │            │
        game images   real images
              └── pairs ───┘
```

| Table | One row per | Main columns |
| --- | --- | --- |
| `games` | game | title, series, release year, developer, publisher, Wikidata item, Fandom wiki |
| `sites` | real place | name, Wikidata item and type, country, city, coordinates, year built, century, era, historical period, style, heritage status, Wikipedia description |
| `depictions` | game's version of a place | in-game name, how it was matched, verified or not, the in-game description from the game's wiki |
| `images` | image file | role (game or real), kind, source page, original file URL, hosted web copy, size, SHA-256 and perceptual hash, author, license, rights status, who collected it and when |
| `pairs` | candidate comparison | game image, real image, match level |

Every column is described in [`docs/data_dictionary.md`](docs/data_dictionary.md)
and in `gh.describe()`. The release folder also has a Frictionless
[`datapackage.json`](src/gameheritage/_release/datapackage.json). The CSV files
can be read directly from any language, for example in R:

```r
base <- "https://raw.githubusercontent.com/cantayus/gameheritage/v0.1.0/src/gameheritage/_release/"
sites <- read.csv(paste0(base, "sites.csv"))
```

### History: years, centuries and eras

Each place has the year it was built, founded or first recorded (`year`,
negative for BCE), the century, and one of these eras:

| Era | Years | Places in 0.1.0 |
| --- | --- | --- |
| Prehistory | before 3000 BCE | 4 |
| Ancient world | 3000 BCE – 499 CE | 80 |
| Middle Ages | 500 – 1499 | 128 |
| Early modern | 1500 – 1799 | 132 |
| 19th century | 1800 – 1899 | 102 |
| 20th century and later | 1900 – | 135 |
| Natural formations | (mountains, islands, caves, rivers...) | 155 |
| Date not recorded | | 380 |

Dates come from Wikidata (inception, official opening, start time, earliest
written record). Where Wikidata has none, a date is read from the opening of
the Wikipedia article ("built in 1163", "commissioned in 1631"). Those dates are
marked `description text (approximate)` in `construction_date_basis`.

## How the data were made

1. **Game images.** Mija Aleksandraviciute collected in-game images of real
   places from the games' Fandom wikis (June–August 2026). The package then
   harvested further location images from the same wikis. For each file it
   recorded the file page, the original file URL, the uploader and the upload
   date. Maps, menus, icons, video stills and pictures of people were left out.
2. **Matching.** Each in-game place name was searched on Wikidata. A match was
   accepted when it was confident and consistent with the rest of the game. The
   checks were: name similarity, the place's fame (Wikipedia sitelinks),
   Wikidata statements that tie the place to the game, distance from the
   game's other places, and the countries the game is set in. Generic nouns
   ("Grand Staircase"), namesakes ("Rollo") and games set in invented worlds
   that borrow real names (Red Dead Redemption, GTA, BioShock and others) are
   handled by stricter rules. Only built structures and natural formations are
   kept; countries, regions, people and events are not. How each match was made
   is in `depictions.basis_note`.
3. **Real photographs** come from Wikimedia Commons under free licenses
   (mostly CC BY-SA and CC BY; also public domain, CC0, the Free Art License,
   GFDL and open government licenses). Each has its author, license and file
   page. A small number (62) are from the project's own collection; their
   source is not yet recorded (`rights_status = unknown`).
4. **Facts and texts** come from Wikidata (CC0) and Wikipedia (CC BY-SA 4.0).
   The in-game descriptions come from the games' Fandom wikis (CC BY-SA 3.0).
   Each text keeps its source and license.
5. **Duplicates** are removed when files have the same content (SHA-256), are
   the same Fandom file, or share game, size and name pattern.

The package that does all of this is in this repository (see *For the project
team* below), so every step can be inspected and re-run.

### Limitations

* **Matches are automatic.** In release 0.1.0 no match has yet been reviewed by
  the project team: `verified` is `false` for all 1,452 in-game versions.
  Reviewed matches will be marked `verified = true` in later releases. Check
  the matches you rely on, using the in-game name, the image and
  `basis_note`.
* An in-game version is a game's representation of a place, which may be
  partial, stylised or anachronistic. The `pairs` match level says how close a
  reviewed comparison is.
* About a third of the places have no recorded date (`era = "Date not
  recorded"`).
* Coverage follows what the games' wikis document. It is not a sample of all
  games or all heritage sites.

## Rights: using the images

* **Game images** are copyrighted by their developers and publishers. The
  project shows small copies for research, criticism and commentary, credits
  the game and links to the source. If you reuse them, judge your own use
  against your local copyright law (for example fair use or the quotation
  exception).
* **Photographs** keep their own licenses, listed per file. Most require
  attribution, and CC BY-SA requires sharing alike. Use
  `licenses=["Public domain", "CC0", "CC BY"]` in `gh.download` to keep only
  files without share-alike terms.
* Every download folder gets an `ATTRIBUTION.csv` with the credit line,
  source and license of each file. Keep it with the images.
* The catalogue's own metadata is CC BY 4.0. Wikidata facts are CC0, and texts
  taken from Wikipedia or Fandom keep their CC BY-SA licenses.

The `size` option of `gh.download` chooses the copy:

| `size=` | What you get |
| --- | --- |
| `"web"` (default) | The project's hosted copy: WebP, longest side 1200 px. Fast and always available. |
| `"thumb"` | 400 px thumbnails. |
| `"original"` | The full file from its source (Wikimedia Commons, Fandom). If there is no source file, or it cannot be reached, the web copy is used and the `copy` column says so (`fallback=False` turns this off). |

## Versions and reproducibility

Each release has a version number and a git tag (`v0.1.0`). Installing a tag
installs that exact catalogue. `gh.load(version="0.1.0")` downloads a tagged
release into a local cache (`~/.cache/gameheritage`, or `GAMEHERITAGE_CACHE`);
a tagged release never changes. Report the version you used, which
`gh.cite()` includes.

## Citation

> Caliskan, C., & Aleksandraviciute, M. (2026). *Digital heritage in video
> games: Historical structures in video games and their real-world
> counterparts* (Version 0.1.0) [Data set and Python package]. University of
> Rochester. https://github.com/cantayus/gameheritage

`gh.cite("bibtex")` gives the BibTeX entry and [`CITATION.cff`](CITATION.cff)
the citation file. Please also credit the individual images you show, using
their `credit_line`.

## For the project team: the curation pipeline

The same package builds the catalogue. Install it with the curation extras
from a clone of this repository:

```bash
pip install -e ".[curate,dev]"
```

Settings live in `gameheritage.toml`. Set `[http] contact` first: Wikimedia
asks every API client for an email address or project URL. The working
tables (`data/`) and review sheets (`review/`) live in the project's shared
folder, not in this repository.

```bash
gameheritage scan                 # inventory the raw collection, read-only (hashes files)
gameheritage register             # one images.csv row per unique file
gameheritage flags                # duplicates, broken files, icons, mismatched real images
gameheritage fandom-sources       # source page + file URL for each game image
gameheritage autolink             # match in-game places to Wikidata sites
gameheritage real-images          # licensed photos per linked site, then pairs
gameheritage enrich sites         # coordinates, dates, style, heritage status
gameheritage dates                # construction dates and historical periods
gameheritage describe             # descriptions from Wikipedia (CC BY-SA)
gameheritage describe --in-game   # in-game descriptions from the games' Fandom wikis
gameheritage review export images # sheets + HTML contact sheets for review
gameheritage validate             # schema, links and the publish gate
gameheritage website --out ../site                     # the website
gameheritage build --version 0.1.0 --site ../site --package   # this release
```

`autolink` writes `review/auto_links.csv` with a confidence level for every
in-game place. Only `high` matches, and `medium` matches with no open issue,
are applied automatically. Set the `decision` column to `keep` or `drop` and
run `gameheritage autolink --from-sheet` to apply a review.

To expand the collection:

```bash
gameheritage harvest --wiki assassinscreed --game assassins-creed-unity \
    --category "Locations in Assassin's Creed Unity"
gameheritage harvest --wiki fallout --game fallout-4 --title "Fallout 4" \
    --series Fallout --category "Fallout 4 locations" --depth 0
gameheritage autolink
gameheritage prune-harvest --unlinked     # drop harvested images with no real match
gameheritage dedupe
```

`harvest` downloads only images of places that match a real built structure or
natural formation on Wikidata. It skips maps, menus, icons, video stills and
pictures of people (statues excepted; uses OpenCV's face detectors). The team's
original collection is never modified. The review steps are explained in
[`docs/curation_guide.md`](docs/curation_guide.md), and the changes in
[`CHANGELOG.md`](CHANGELOG.md).

## Authors

Cantay Caliskan and Mija Aleksandraviciute, University of Rochester. Short
biographies are in [`AUTHORS.md`](AUTHORS.md).

## License

Code: MIT. Catalogue metadata and project-written text: CC BY 4.0. Wikidata
facts: CC0. Text from Wikipedia and Fandom: CC BY-SA. Images keep their own
terms, listed per file.
