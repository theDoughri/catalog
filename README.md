# Baggo Catalog

Data-only repository for the catalogs Baggo publishes: one JSON manifest plus
item photos per catalog. No application code lives here, and nothing is built
or deployed — the files on the branch ARE the published catalogs.

A catalog is a starting pantry — the things a shopper already buys, each with a
category, a unit, a photo and sometimes a tip — so a new install opens
on a list worth using instead of an empty page. This repository is the official
one; it is also the template. Fork it, put your own items in the manifest, point
the app at your fork, and that is your catalog.

Baggo offers SEVERAL catalogs of its own, and each is a catalog in the ordinary
sense — its own folder, its own permanent `id`, its own `version`, installed,
updated and removed like anybody else's. They differ in what they DRESS the
pantry in, not in what a catalog is:

| Folder       | `id`                   | Name                       |
| ------------ | ---------------------- | -------------------------- |
| `official/`  | `dev.baggo.official`   | Baggo Catalog (Official)   |
| `handwoven/` | `dev.baggo.handwoven`  | Baggo Catalog (Handwoven)  |

`official/` is the one a fresh install seeds from. `handwoven/` currently
carries the same groceries and the same categories, and is where artwork of its
own goes. A device may hold both, but their item names collide by design, so
the second install asks whether to keep the items already there or let the
incoming catalog take them over.

Folder names are URL path segments — the app fetches
`.../<folder>/manifest.json` — so they stay lowercase and hyphenated whatever
the catalog calls itself. `name` inside the manifest is what a user reads.

## The whole format

```
official/
  manifest.json   the catalog: identity, version, categories, items
  images/         1024x1024 JPEG item photos, one per item slug
handwoven/
  manifest.json
  images/
schema/           JSON Schema (draft 2020-12) for the manifest
```

A catalog folder holds a manifest and its photos and nothing else. `manifest.json`
is the catalog: the app fetches that one file, and then the photos it names.
Image paths are relative to the MANIFEST, so a folder is self-contained and can
be moved, copied or forked whole.

```json
{
  "schema_version": 2,
  "id": "uk.baggo.official",
  "name": "Baggo Official",
  "version": 7,
  "description": "The starter pantry Baggo ships with: ...",
  "homepage": "https://github.com/theDoughri/catalog",
  "author": "theDoughri",
  "default_locale": "en",
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
split across a root manifest, a list manifest and `categories.json`. A client
that meets a HIGHER number imports what it can and warns that the catalog was
made for a newer Baggo, rather than refusing it.

`id` is this catalog's permanent identity, chosen once and never changed.
Baggo keys an installed catalog off it — not off the URL it came from, not off
a filename — so the same catalog fetched from a mirror or handed over as a file
is still recognised as the same catalog, and an item's identity is (this id,
its slug). Reverse-DNS is the recommendation. Changing it publishes a second
catalog that happens to look like the first.

`name` is what a user sees in the catalog list and on the screen that asks
them to approve this catalog — they approve a catalog, not a URL, so the file
has to say what it is. `version` is the release number: an integer the author
increments on every publish, and what clients compare. Not a tag, not a date.

`description`, `homepage`, `author` and `license` are optional, and are for
the human deciding whether to install this. `homepage` is where they report a
problem with it, so publish one.

`default_locale` is the language a client falls back to when a name has no
entry for the app's own; `expires` (days, default 7, minimum 1) is how often a
client re-checks a catalog it fetched by URL. Both may be left out.

Items carry no icon of their own. An item without a photo renders its
CATEGORY's icon, which is the only icon the app has ever drawn for a row.

## Format, and house rules

Two sets of rules apply to this file, and a fork should know which is which.

The FORMAT is what any Baggo catalog may be, and `schema/manifest.schema.json`
describes exactly that: `slug`, `category` and `name` on an item, `slug` and
`name` on a category, everything else optional. A `name` or a `note` may be a
plain string instead of a locale map — language-neutral, shown as written —
and no locale is mandatory. An `image` may be an absolute `https://` URL
instead of a path, which is what lets a standalone JSON catalog link out to
photos it does not host. A catalog is capped at 5 MB, 5,000 items, 200
categories and 2 MB per image.

The HOUSE RULES are these catalogs' own, and are stricter than the format:
every name and note is a locale map carrying all five app locales, and every
photo is exactly 1024x1024 JPEG. They are written down under Contracts below —
there is no validator in the repository any more, so they are read and applied
by whoever edits a manifest. They
are the right rules for the official catalog and the wrong ones to inherit
blindly — a fork serving one country in one language is a perfectly valid
catalog, and only these house rules would object.

## Making your own

1. Fork this repository, or start an empty one with a `manifest.json` and an
   `images/` folder beside it.
2. Set `id` to something nobody else will use (reverse-DNS of a domain or
   GitHub account you control), `name` to yours and `version` to 1, and point
   `homepage` at wherever someone should report a problem with it.
3. Replace the `categories` and `items` with yours. Keep the rules below.
4. Add photos under `images/`, or leave them out — an item with no file
   renders its category's icon instead.
5. Push. Your catalog is the raw URL of the manifest:
   `https://raw.githubusercontent.com/<you>/<repo>/<branch>/manifest.json`

Nothing needs building or deploying. The file on the branch IS the published
catalog.

## Contracts

**The `id` is permanent, and so are slugs.** Item slugs, category slugs and icon names are all
`^[a-z0-9]+(-[a-z0-9]+)*$` and are never renamed once released — clients key
off them. A wrong slug is retired by adding a new one, not by renaming.

**Names are locale maps.** House rule, not format. Every `name` here carries a
non-empty value for all five locales the app ships: `en`, `fr`, `es`, `de`,
`ar`. The app has no other translation source for catalog names, so a missing
one would leave a shopper with a blank row. The format itself asks for none of
them, and accepts a plain string as a language-neutral name.

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
are expected and valid — the app falls back to the category's icon. The one
size is a
house rule; the format accepts JPEG, PNG or WebP from 64x64 up (512x512 or
larger preferred, square recommended) and up to 2 MB, because Baggo downscales
what it downloads and never upscales.

**Only the full-size photo is published.** Thumbnails are the app's business.
Baggo resizes each photo when it downloads it, so this repository carries no
derived files that could fall out of step with their sources.

**Text has caps.** A category name, item name or note translation must stay
within what the app can store (34, 34 and 144 characters), because
the app seeds all three into fields the shopper can edit — anything longer
would be silently truncated the first time they opened that row.

## Branching

git flow (https://nvie.com/posts/a-successful-git-branching-model/): work lands
on `develop`, `main` holds released catalog only. Feature branches come off
`develop`; a release branch merges into `main` and back into `develop`.

**The app currently fetches `develop`.** Baggo names
`raw.githubusercontent.com/theDoughri/catalog/develop/<folder>/manifest.json`,
so a push to `develop` reaches devices on their next re-check — there is no
release step in between, and no draft state. Edit accordingly: bump the
`version` integer in the same commit that changes a manifest, because that
number, not the tag and not the commit, is what clients compare, and a change
published without one is a change no device will ever pick up.

`main` still holds released catalogs, and the pointer is one constant in the
app (`CatalogService.defaultCatalogUrl`); moving it back to `main` is that one
edit plus a release branch.
