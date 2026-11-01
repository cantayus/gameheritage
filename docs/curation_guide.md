# Curation guide

How to review the catalogue. Everything happens in spreadsheets: open the CSV
files in `review/` with Google Sheets or Excel, fill the `decision` column,
save as CSV, and run the import command shown for each sheet.

Decisions: `keep`, `drop`, `fix` (something is wrong, see note), `needs_info`,
or leave blank (not reviewed yet). Nothing is ever deleted: `drop` only keeps a
row out of releases.

Each sheet has a matching `.html` contact sheet. Open it in a browser to see
the thumbnails with their IDs.

## 1. In-game places matched to real sites (`review/auto_links.csv`)

One row per in-game place, with the Wikidata item it was matched to.

| Column | Meaning |
| --- | --- |
| `confidence` | `high` (already applied), `medium`, `low`, `none` |
| `label`, `description`, `country` | the matched real place |
| `issues` | why it is not `high` (e.g. "several places share this name") |
| `alternatives` | the next candidates, as `Q-number Label (description)` |
| `distance_km` | distance from the game's other places |

* Correct match: `decision = keep`.
* Wrong match but the right place is in `alternatives`: paste its Q-number into
  `wikidata_qid` and set `keep`.
* The right place is not offered: look it up on wikidata.org, paste the
  Q-number, set `keep`.
* No real equivalent (fictional place): `drop`.

Then run `gameheritage autolink --from-sheet` and `gameheritage real-images`.

Images harvested by the package whose place had no confident match were
removed by `prune-harvest --unlinked` and listed in
`review/pruned_harvest.csv`. If you `keep` one of those places, harvest its
game again: the files are downloaded again and linked.

## 2. Image triage (`review/image_triage.csv`)

Run `gameheritage review export images` first. `suggestion` is the machine's
guess from the flags (`drop` for empty files, duplicates and icons; `check`
for near-duplicates or files in the wrong folder). Set `decision`, and
optionally `new_kind` (screenshot, concept_art, database_entry, map, render,
promo_art, ...). Import with
`gameheritage review import images review/image_triage.csv`.

## 3. Pairs

`gameheritage real-images` pairs every linked game image with the site's
photo. Review pairs by setting `review_status` to `keep` and `match_level`
(`same_structure_same_view`, `same_structure_other_view`,
`same_site_other_part`, `style_analogue`) directly in `data/pairs.csv`, or
through an exported sheet. Only `keep` pairs are released.

## Rules for adding images by hand

* Real-world images: only from Wikimedia Commons, Europeana, museums or
  archives with a free license (public domain, CC0, CC BY, CC BY-SA). Record
  the source page, author and license; `gameheritage validate` refuses pairs
  without them.
* Game images: record the page the file came from. Do not take screenshots to
  get around a missing download link.
