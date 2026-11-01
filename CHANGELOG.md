# Changelog

## 0.1.0 (2026-11-01)

First public release of the dataset and package.

### For researchers

- The catalogue is installed with the package: `gh.load()` works offline.
  `gh.load(version="latest")` or `gh.load(version="0.1.0")` downloads a release
  from GitHub into a local cache.
- The release holds exactly what the project website shows: 76 games, 1,116
  real places, 1,452 in-game versions, 4,987 images and 3,285 candidate pairs.
  Automatic matches are included and marked `verified = false`.
- New columns: `sites.year`, `century`, `era`, `wikidata_type`, `web_page`;
  `depictions.verified`, `web_page`; `images.web_url`, `thumb_url`,
  `web_width`, `web_height`. Private columns (local and Drive paths) are left out.
- `Catalog.matches()`: one row per in-game version with the game, the place,
  its date and era, how it was matched, and a game image and a real photo with
  URLs and credits. `Catalog.search()`, `Catalog.timeline()` and
  `Catalog.places()` (an alias of `sites()`).
- `gh.download(..., size="web" | "thumb" | "original")`. Originals of Wikimedia
  photos are the full-resolution files; when an original is missing or
  unreachable, the web copy is used (`fallback`), and the `copy` column says which.
- `gh.describe()` and `docs/data_dictionary.md`: the meaning of every column,
  also written into `datapackage.json`.
- `gh.show()` accepts a row of `matches()` and uses the hosted web copies.
- Citation updated (`gh.cite()`, `CITATION.cff`); README rewritten for
  researchers; `examples/quickstart.ipynb`.
- `gameheritage build --site <website folder>` builds this kind of release.

### Before the public release

- Catalogue schema: games, sites, depictions, images, pairs, folder aliases;
  pydantic validation and a publish gate (no pair is released without a
  licensed real image and a sourced game image).
- Curation pipeline: `scan`, `register`, `flags`, `fandom-sources`, `autolink`,
  `real-images`, `enrich`, `describe`, `review export/import`, `validate`,
  `credits`, `build`, `web-images`.
- File-name parsers for the collection's four naming families; problem flags
  (duplicates, empty and placeholder files, WebP saved as .jpg, icons, real
  images saved under the wrong subject or reused across games).
- Fandom source recovery for scraped game images.
- Automatic linking of in-game places to Wikidata with confidence levels.
- Licensed real-world photos from Wikimedia Commons with full provenance.
- Credits as CSV, Markdown, BibTeX and CSL-JSON.
- User API: `load`, `Catalog` queries, `download` (with ATTRIBUTION.csv),
  `show` (side by side), `cite`.

### Website

- `gameheritage website --out <folder>` builds the public website as a static site
  that GitHub Pages can serve. It writes the page, its script and styles (from
  `gameheritage/web/`), `data/index.json`, one JSON file per place, and web-size
  WebP copies of every image shown, each image with its credit line.
- The site offers:
  - search across places, cities, in-game names and games;
  - a timeline of periods;
  - place pages with the real photograph and the in-game image side by side
    (or overlaid), history and in-game texts, facts and sources;
  - game pages in chronological order;
  - an About page with methods, rights, limitations, authors and a citation.
- `describe` now reads Wikipedia leads 20 at a time and falls back to Wikidata
  descriptions. `describe --in-game` adds the lead of each place's page on the
  game's Fandom wiki to `depictions.in_game_description` (CC BY-SA, with source).
- History: `history.enrich_dates` fills `sites.construction_date` from the earliest
  Wikidata date (inception, opening, start, first record) and
  `sites.historical_period` from Wikidata's "time period". Every place gets an era
  (Prehistory to 20th century, plus natural formations) and a century. Dates
  read from description text are marked approximate in
  `construction_date_basis`.
- Mija's own photographs of real places are linked to their sites and shown,
  credited to the project's collection.
- `gameheritage dates` runs the history step: Wikidata dates first, then dates
  read from the description text ("built in", "commissioned in", "laid out in"...).
- A rebuild removes the pages and images of places no longer shown, so a
  withdrawn match also disappears from the published site.
- Matches that were countries, regions, an ecoregion or historical events
  (Haiti, Jamaica, Japan, the September Massacres, the Great Fire of New York
  and others) are marked `drop` in `review/auto_links.csv`.

### Collection expansion

- `harvest`: download location images from a game's Fandom wiki, by category
  (`--category`, with subcategories to `--depth`) or by file-name prefix
  (`--prefix ACUDB`). New games are added with `--title` and `--series`. Before
  downloading, each place is checked against Wikidata, and only places that
  match a real built structure or natural formation are kept. Every image
  records its file page, file URL, uploader, upload date and the category used.
- `prune-harvest`: remove harvested images whose place is not a real
  structure. `prune-harvest --unlinked` runs after `autolink` and removes
  harvested images without a confident real-world match. The removed images
  are listed in `review/pruned_harvest.csv`. The team's own collection is
  never modified.
- `dedupe`: mark duplicate images as dropped. Duplicates are detected by
  identical content, the same Fandom file, or the same game, size and name
  pattern.
- `licenses`: fill in author and license for real photos downloaded with
  `real-images --no-license-lookup`. That option skips the slow Commons API
  and is used when Commons is rate-limiting.
- `autolink` uses more evidence:
  - Wikidata statements that link the place to the game
    (P1441 "present in work", P840 "narrative location").
  - Geographic coherence with the game's other places.
  - Fame, measured by Wikipedia sitelinks.
  - Penalties for namesakes and transport stops.
- `autolink` and the harvest filter also check the name itself:
  - A common noun (Wikidata has a lower-case concept with that label, e.g.
    "Grand Staircase", "Obelisk") only matches a well-known place.
  - A name that usually means something else (a person, a planet: "Rollo",
    "Mercury") does not match an obscure namesake.
  - Names made only of generic place words ("Tower Bridge", "Old North
    Church") are now searched, but need a well-known match.
- Where a game is set comes from its confident matches plus the places Wikidata
  ties to it. In a game set in one city or region, a namesake far outside
  it is rejected even if well known, unless it sits in a second cluster of
  the game's places (Venice in a mostly-Florence game). In a game set all over
  the world, obscure matches outside the countries Wikidata gives for the game
  are not applied. Matches whose real name only contains the in-game name
  ("Howler Monkey" → "Howler Monkey Hotel") need review.
- Games set in invented worlds that borrow real names are listed in
  `[autolink] fictional_worlds` in `gameheritage.toml`. Their places are matched
  only when Wikidata ties them to the game or a reviewer keeps them.
- A stricter `autolink` run withdraws earlier machine links it no longer
  accepts, unless a person reviewed them. Sites left without any game image are
  removed together with their downloaded photos.
- `harvest --exclude-pattern` skips file names such as other games' tags on
  shared pages. Maps, menus and video stills of trailers or gameplay are always
  skipped. `prune-harvest --files` applies the same rules to earlier harvests.
- The kinds of place that are collected are now built structures (buildings,
  monuments, parks, complexes, districts, settlements, sculptures) and
  natural formations (mountains, islands, caves, waterfalls...). People,
  regions and administrative areas are not collected.
- Table writes are safe when several jobs run at once. A save that would
  overwrite another job's changes is refused, and `tables.commit` retries.
  Tables are written to a temporary file and swapped in, and on POSIX systems
  writers take turns through a lock file (`data/.lock`).
- `licenses` sets `rights_status` from the license it reads.
- The HTTP client gives up on a request when the server asks it to wait for
  many minutes, instead of stalling a long download run.
