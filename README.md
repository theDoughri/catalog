# Baggo Official Catalog

Data-only repository for the item catalog shipped with the Baggo shopping list
app: JSON manifests plus item images. No application code lives here.

## Layout

```
manifest.json                  root manifest — catalog identity and list index
categories.json                global category definitions
schema/                        JSON Schema (draft 2020-12) for each file type
lists/
  default/
    manifest.json              the "Groceries" list
    items_images.csv           hand-picked photo link per item (name,link)
    images/                    1024x1024 JPEG item images
    thumbnails/                128x128 JPEG thumbnails (generated)
scripts/validate.py            validator (run locally and in CI)
scripts/fetch_images.py        downloads + crops the images listed in the CSV
.github/workflows/validate.yml CI entry point
```

## Contracts

**Slugs are permanent.** Item slugs, category slugs, list slugs and icon names
are all `^[a-z0-9]+(-[a-z0-9]+)*$` and are never renamed once merged — clients
key off them. A wrong slug is retired by adding a new one, not by renaming.

**Names are locale maps.** Every `name` object carries non-empty `en` and `ar`
values from the start. Additional locales may be added later; `en` and `ar`
stay required.

**Categories are global.** `categories.json` is shared by every list. An item's
`category` must resolve to a slug defined there.

**Notes are optional, but complete.** An item may carry a `note`: one short
buying or storage tip, as a locale map with the same shape and the same
required locales as `name`. Most items have none — a note earns its place only
when it tells the shopper something the item name does not (how to pick a ripe
one, where it keeps best, what to do before cooking). It is a single plain
sentence, no markup and no line breaks, written to fit under the item in the
app. Clients must render an item with no `note` exactly as before.

**Icons are semantic.** An icon name describes the depicted thing (`carrot`,
`jar`, `bottle`), not a specific glyph in a specific font, so the app can
re-point a name to a better glyph without touching this repo. Names come from
Baggo's fixed icon vocabulary; where no exact glyph exists, the closest name in
the same family is used (`apple` for fruit without a dedicated glyph, `carrot`
or `plant` for vegetables).

**Images are exactly 1024x1024 JPEG**, stored at `lists/<list>/images/<slug>.jpg`
and referenced by an item's `image` field as a path relative to the list
manifest. The path is declared even when the file has not been produced yet:
missing images are expected and valid — the app falls back to the item's icon,
and the validator reports them as warnings rather than failures.

## Fetching item images

Item photos are hand-picked. Each list directory carries an
`items_images.csv` next to its manifest (e.g. `lists/default/items_images.csv`)
with one row per item (`name` is the item slug, `link` is the photo URL); fill
the `link` column from sources such as pixabay.com or unsplash.com, then run:

```bash
pip install Pillow
python scripts/fetch_images.py
# redo one item after changing its link:
python scripts/fetch_images.py --force --only apples
```

For each row with a link, the script downloads the photo, center-crops it to
exactly 1024x1024, and saves the JPEG at the item's declared `image` path.
Links may be pasted straight from the browser: an Unsplash photo page is
resolved through its full-resolution download endpoint, a Pixabay photo page
or cdn.pixabay.com link is resolved to its largest rendition, and any direct
image URL works as-is. Sources must be at least 1024x1024 — the script never
upscales; too-small or broken links are reported as failed and the file is
left missing (which the validator treats as a warning, not an error).

At the end of every run the script refreshes `thumbnails/` next to each
list's `images/` folder: every image gets a 128x128 JPEG thumbnail under the
same file name, stale thumbnails are regenerated, and thumbnails whose
source image was removed are deleted. The folder is fully derived — never
edit it by hand.

Re-running is safe: rows with an empty link and items whose image file
already exists are skipped, so the CSV can be filled gradually. The summary
also flags CSV rows that match no item and items missing from the CSV. When
adding new items to a list, add a matching CSV row. Check each source's
license terms when picking (Pixabay and Unsplash images are free to use in
apps without attribution).

## Running the validator

```bash
# Pillow is only needed once image files exist
pip install Pillow
python scripts/validate.py
```

It exits 0 on success and 1 on failure, checking schema conformance, slug
uniqueness, referential integrity, image format/dimensions, and locale
completeness, then prints a summary. CI runs the same command on every push and
pull request.

## Adding another list later

Create `lists/<slug>/` with a `manifest.json` following
`schema/list-manifest.schema.json`, an `images/` directory and an
`items_images.csv` beside it, add any
categories the new items need to `categories.json` (reusing existing category
slugs wherever they fit), then append an entry to the `lists` array in the root
manifest pointing at the new manifest path and bump the root `version`. Item
slugs only need to be unique within their own list; category slugs and list
slugs are unique across the whole catalog.
