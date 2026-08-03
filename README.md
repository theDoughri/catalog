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
    images/                    1024x1024 JPEG item images
scripts/validate.py            validator (run locally and in CI)
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

## Running the validator

```bash
pip install Pillow          # only needed once image files exist
python scripts/validate.py
```

It exits 0 on success and 1 on failure, checking schema conformance, slug
uniqueness, referential integrity, image format/dimensions, and locale
completeness, then prints a summary. CI runs the same command on every push and
pull request.

## Adding another list later

Create `lists/<slug>/` with a `manifest.json` following
`schema/list-manifest.schema.json` and an `images/` directory beside it, add any
categories the new items need to `categories.json` (reusing existing category
slugs wherever they fit), then append an entry to the `lists` array in the root
manifest pointing at the new manifest path and bump the root `version`. Item
slugs only need to be unique within their own list; category slugs and list
slugs are unique across the whole catalog.
