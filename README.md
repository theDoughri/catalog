# Baggo Catalog

Data-only repository for the item catalog shipped with the Baggo shopping list
app: one JSON manifest plus item photos. No application code lives here.

A catalog is a starting pantry — the things a shopper already buys, each with a
category, a unit, a photo and sometimes a tip — so a new install opens
on a list worth using instead of an empty page. This repository is the official
one; it is also the template. Fork it, put your own items in the manifest, point
the app at your fork, and that is your catalog.

## The whole format

```
manifest.json     the catalog: identity, version, categories, items
images/           1024x1024 JPEG item photos, one per item slug
items_images.csv  hand-picked photo link per item (name,link) — authoring only
schema/           JSON Schema (draft 2020-12) for the manifest
scripts/          validate.py, fetch_images.py
```

`manifest.json` is the catalog. There is nothing else to read: the app fetches
that one file, and then the photos it names.

```json
{
  "schema_version": 2,
  "version": 6,
  "categories": [
    { "slug": "fruits", "icon": "apple", "name": { "en": "Fruits", ... } }
  ],
  "items": [
    {
      "slug": "apples",
      "category": "fruits",
      "unit": "kg",
      "name": { "en": "Apples", ... },
      "note": { "en": "Keep them cold and away from bananas.", ... },
      "image": "images/apples.jpg"
    }
  ]
}
```

`schema_version` is the shape of the file — 2 is this one, 1 was the older
split across a root manifest, a list manifest and `categories.json`. `version`
is the release number, and every field below it is something the app renders:
nothing is published that nothing reads.

Items carry no icon of their own. An item without a photo renders its
CATEGORY's icon, which is the only icon the app has ever drawn for a row.

## Making your own

1. Fork this repository (or start an empty one with the same three pieces).
2. Set `version` to 1.
3. Replace the `categories` and `items` with yours. Keep the rules below.
4. Add photos under `images/`, or leave them out — an item with no file
   renders its category's icon instead, and the validator only warns.
5. Run `python scripts/validate.py` until it passes.
6. Push to `main`. Your catalog is the raw URL of that branch:
   `https://raw.githubusercontent.com/<you>/<repo>/main/`

Nothing needs building or deploying. The file on `main` IS the published
catalog.

## Contracts

**Slugs are permanent.** Item slugs, category slugs and icon names are all
`^[a-z0-9]+(-[a-z0-9]+)*$` and are never renamed once released — clients key
off them. A wrong slug is retired by adding a new one, not by renaming.

**Names are locale maps.** Every `name` carries a non-empty value for all five
locales the app ships: `en`, `fr`, `es`, `de`, `ar`. The app has no other
translation source for catalog names, so a missing one would leave a shopper
with a blank row.

**Categories are local to the manifest.** An item's `category` must resolve to
a `slug` in the same file's `categories` array. Their order is shelf order: the
app seeds category rank from it.

**Notes are optional, but complete.** An item may carry a `note`: one short
buying or storage tip, as a locale map with the same shape and the same
required locales as `name`. Most items have none — a note earns its place only
when it tells the shopper something the item name does not (how to pick a ripe
one, where it keeps best, what to do before cooking). It is a single plain
sentence, no markup and no line breaks.

**Icons are semantic, and belong to categories.** An icon name describes the
depicted thing (`carrot`, `jar`, `bottle`), not a specific glyph in a specific
font, so the app can re-point a name to a better glyph without touching this
repo. Names come from Baggo's fixed icon vocabulary; where no exact glyph
exists, the closest name in the same family is used (`apple` for a fruit
category, `carrot` or `plant` for vegetables). Only categories carry one — it
is what every row of that category falls back to when it has no photo.

**Images are exactly 1024x1024 JPEG**, stored at `images/<slug>.jpg` and
referenced by an item's `image` field as a path relative to the manifest. The
path is declared even when the file has not been produced yet: missing images
are expected and valid — the app falls back to the category's icon, and the
validator reports them as warnings rather than failures.

**Only the full-size photo is published.** Thumbnails are the app's business.
Baggo resizes each photo when it downloads it, so this repository carries no
derived files that could fall out of step with their sources.

**Text has caps.** A category name, item name or note translation that exceeds
what the app can store (34, 34 and 144 characters) fails validation, because
the app seeds all three into fields the shopper can edit — anything longer
would be silently truncated the first time they opened that row.

## Branching

git flow (https://nvie.com/posts/a-successful-git-branching-model/): work lands
on `develop`, `main` holds released catalog only. Feature branches come off
`develop`; a release branch merges into `main` and back into `develop`.

**A merge to `main` is the release, in the literal sense.** The app fetches
`raw.githubusercontent.com/theDoughri/catalog/main/`, so nothing on `develop`
reaches a device. Bump the `version` integer on the release branch, because
that number, not the tag, is what clients compare.

## Fetching item images

Item photos are hand-picked. `items_images.csv` carries one row per item
(`name` is the item slug, `link` is the photo URL); fill the `link` column from
sources such as pixabay.com or unsplash.com, then run:

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

Re-running is safe: rows with an empty link and items whose image file
already exists are skipped, so the CSV can be filled gradually. The summary
also flags CSV rows that match no item and items missing from the CSV. When
adding new items, add a matching CSV row. Check each source's license terms
when picking (Pixabay and Unsplash images are free to use in apps without
attribution).

The CSV is an authoring tool. It is not part of the published format — a
catalog with photos already in `images/` needs no CSV at all.

## Running the validator

```bash
# Pillow is only needed once image files exist
pip install Pillow
python scripts/validate.py
```

It exits 0 on success and 1 on failure, checking schema conformance, slug
uniqueness, referential integrity, image format/dimensions, locale
completeness and text length, then prints a summary. CI runs the same command
on every push and pull request.
