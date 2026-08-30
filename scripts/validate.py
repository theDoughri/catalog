#!/usr/bin/env python3
"""Validate a Baggo catalog.

A catalog is one manifest.json at the repository root and an images/ folder
beside it. Nothing else is read.

Two kinds of rule are checked here, and the difference matters to anyone
forking this repository.

FORMAT — true of every Baggo catalog, and what the schema describes:
  1. manifest.json parses and validates against schema/manifest.schema.json.
  2. Slug uniqueness: categories and items each have their own namespace.
  3. Referential integrity: every item.category resolves to a category
     declared in the same file.
  4. Limits: 5 MB of manifest, 5,000 items, 200 categories, 2 MB per image.
  5. Images: an item.image that EXISTS is JPEG, PNG or WebP and at least
     64x64. A missing file is a warning, not a failure — the app falls back
     to the category's icon, and the path is declared before the file lands.

HOUSE RULES — this catalog's own, stricter than the format allows for:
  6. Locales: every name — and every optional item note — is a locale MAP
     carrying a non-empty value for all five locales the app ships
     (en, fr, es, de, ar). The format permits a plain string and permits one
     language; the official catalog ships five, because the app has no other
     translation source for catalog text.
  7. Photos are exactly 1024x1024 JPEG. The format accepts anything from
     64x64 up; publishing one size keeps the repository predictable.
  8. Text length: no translation of a category name, item name or item note
     exceeds the matching *_MAX_LENGTH. The app seeds all three into
     user-editable fields with those caps, so a longer one is truncated the
     moment the shopper edits that row.

Exit code 0 on pass, 1 on failure. Requires Python 3 (stdlib) and Pillow;
Pillow is only needed when image files are actually present.
"""
from __future__ import annotations

import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_DIR = os.path.join(REPO_ROOT, "schema")
REQUIRED_LOCALES = ("en", "fr", "es", "de", "ar")

# Format limits. Every Baggo catalog is held to these; a client may refuse
# one that is not, so the validator refuses it first.
MANIFEST_MAX_BYTES = 5 * 1024 * 1024
IMAGE_MAX_BYTES = 2 * 1024 * 1024
IMAGE_MIN_EDGE = 64
IMAGE_PREFERRED_EDGE = 512
ACCEPTED_IMAGE_FORMATS = ("JPEG", "PNG", "WEBP")

# House rule: one published size, one published format. Baggo downscales what
# it downloads and never upscales, so the source is the largest thing anyone
# needs and every derived size is the app's business.
IMAGE_SIZE = (1024, 1024)
IMAGE_FORMAT = "JPEG"

# Mirrors InputLimits.categoryName in the Baggo app.
CATEGORY_NAME_MAX_LENGTH = 34

# Mirrors InputLimits.itemName in the Baggo app. Same reasoning as the note
# cap below: the name is seeded into the field the shopper renames items in,
# so a longer one is truncated the first time they edit it.
ITEM_NAME_MAX_LENGTH = 34

# Mirrors InputLimits.note in the Baggo app. A note is seeded into the same
# field the shopper writes their own notes in, so anything longer would be
# truncated the moment they edited that item. Translations run longer than
# the English they came from — French and German especially — so the cap is
# checked per locale, not on the source text.
NOTE_MAX_LENGTH = 144

errors: list[str] = []
warnings: list[str] = []


def error(message: str) -> None:
    errors.append(message)


def warn(message: str) -> None:
    warnings.append(message)


# --------------------------------------------------------------------------
# Minimal JSON Schema (draft 2020-12 subset) validator.
#
# Deliberately hand-rolled so the validator runs on a stdlib-only Python: the
# supported keywords are exactly the ones schema/*.schema.json use.
# --------------------------------------------------------------------------

TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def resolve_ref(root: dict, ref: str) -> dict:
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported $ref: {ref}")
    node = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        node = node[part]
    return node


def describe_shape(schema: dict) -> str:
    """A short name for an anyOf branch that carries no description."""
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    return str(schema.get("type", "value"))


def collect_errors(instance, schema: dict, root: dict, path: str) -> list[str]:
    """Validate against `schema` in isolation and return what failed.

    `anyOf` needs to try a branch without its failures reaching the report:
    a name that IS a plain string must not be blamed for not being a locale
    map. Swapping the module-level list out for the duration is how a
    hand-rolled validator gets a sandbox.
    """
    global errors
    outer, errors = errors, []
    try:
        validate_schema(instance, schema, root, path)
        return errors
    finally:
        errors = outer


def validate_schema(instance, schema: dict, root: dict, path: str) -> None:
    """Append an error for every way `instance` violates `schema`."""
    if "$ref" in schema:
        validate_schema(instance, resolve_ref(root, schema["$ref"]), root, path)
        return

    if "anyOf" in schema:
        # One shape has to fit; which one it was is not worth reporting.
        # What IS worth reporting is the description of each shape that did
        # not, since that is the whole vocabulary the author may choose from.
        branches = schema["anyOf"]
        if any(
            not collect_errors(instance, branch, root, path) for branch in branches
        ):
            return
        shapes = " or ".join(
            branch.get("description") or describe_shape(branch)
            for branch in branches
        )
        error(f"{path}: {instance!r} is none of the accepted shapes — expected {shapes}")
        return

    expected = schema.get("type")
    if expected is not None:
        py_type = TYPE_MAP[expected]
        # bool is a subclass of int in Python; JSON Schema keeps them apart.
        ok = isinstance(instance, py_type) and not (
            expected in ("integer", "number") and isinstance(instance, bool)
        )
        if not ok:
            error(f"{path}: expected type {expected}, got {type(instance).__name__}")
            return

    if "const" in schema and instance != schema["const"]:
        error(f"{path}: expected constant {schema['const']!r}, got {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        error(f"{path}: {instance!r} is not one of {schema['enum']!r}")

    if isinstance(instance, str):
        pattern = schema.get("pattern")
        if pattern is not None and not re.search(pattern, instance):
            error(f"{path}: {instance!r} does not match pattern {pattern}")
        min_length = schema.get("minLength")
        if min_length is not None and len(instance) < min_length:
            error(f"{path}: string shorter than minLength {min_length}")

    if isinstance(instance, int) and not isinstance(instance, bool):
        minimum = schema.get("minimum")
        if minimum is not None and instance < minimum:
            error(f"{path}: {instance} is below minimum {minimum}")
        maximum = schema.get("maximum")
        if maximum is not None and instance > maximum:
            error(f"{path}: {instance} is above maximum {maximum}")

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(instance) < min_items:
            error(f"{path}: expected at least {min_items} item(s), got {len(instance)}")
        max_items = schema.get("maxItems")
        if max_items is not None and len(instance) > max_items:
            error(f"{path}: expected at most {max_items} item(s), got {len(instance)}")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, entry in enumerate(instance):
                validate_schema(entry, item_schema, root, f"{path}[{index}]")

    if isinstance(instance, dict):
        min_properties = schema.get("minProperties")
        if min_properties is not None and len(instance) < min_properties:
            error(
                f"{path}: expected at least {min_properties} propert"
                f"{'y' if min_properties == 1 else 'ies'}, got {len(instance)}"
            )

        for key in schema.get("required", []):
            if key not in instance:
                error(f"{path}: missing required property {key!r}")

        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        additional = schema.get("additionalProperties", True)

        for key, value in instance.items():
            child = f"{path}.{key}" if path else key
            matched = False
            if key in properties:
                validate_schema(value, properties[key], root, child)
                matched = True
            for pattern, subschema in pattern_properties.items():
                if re.search(pattern, key):
                    validate_schema(value, subschema, root, child)
                    matched = True
            if not matched and additional is False:
                error(f"{path}: unexpected property {key!r}")


def load_json(relative_path: str):
    full = os.path.join(REPO_ROOT, relative_path)
    if not os.path.isfile(full):
        error(f"{relative_path}: file not found")
        return None
    try:
        with open(full, encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        error(f"{relative_path}: invalid JSON ({exc})")
        return None


def load_schema(name: str):
    full = os.path.join(SCHEMA_DIR, name)
    if not os.path.isfile(full):
        error(f"schema/{name}: file not found")
        return None
    try:
        with open(full, encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        error(f"schema/{name}: invalid JSON ({exc})")
        return None


def check_locales(text, path: str, languages: set[str]) -> None:
    """House rule: this catalog's text is a locale map, in all five locales.

    The FORMAT allows a plain string — language-neutral, shown as written —
    and allows a map that carries one language. Neither is wrong; both are
    wrong HERE, where a shopper opening the app in Arabic has no other
    source for the word.
    """
    if isinstance(text, str):
        error(
            f"{path}: {text!r} is a plain string. The format allows one, but "
            f"this catalog writes a locale map: every name and note ships in "
            f"{', '.join(REQUIRED_LOCALES)}"
        )
        return
    if not isinstance(text, dict):
        return
    languages.update(key for key in text if isinstance(key, str))
    for locale in REQUIRED_LOCALES:
        value = text.get(locale)
        if not isinstance(value, str) or not value.strip():
            error(f"{path}: locale {locale!r} is missing or empty")


def check_length(text, limit: int, path: str) -> None:
    # A plain string is one translation of one; the cap is the app's field
    # size either way, so it is measured the same.
    translations = {None: text} if isinstance(text, str) else text
    if not isinstance(translations, dict):
        return
    for locale, value in translations.items():
        if isinstance(value, str) and len(value) > limit:
            where = f"{path}: " if locale is None else f"{path}: locale {locale!r} "
            error(
                f"{where}is {len(value)} characters, "
                f"over the {limit} the app can store"
            )


def check_image(image_path: str, item_slug: str, stats: dict) -> None:
    """Verify one item photo.

    An `https://` reference is somebody else's file: the format allows one
    (that is what lets a standalone catalog link out instead of carrying an
    images folder), and this repository has none, so it is counted and left
    alone rather than downloaded at validation time.

    A relative path is ours, and resolves against the manifest — which lives
    at the repository root, so against the root.
    """
    if image_path.startswith("https://"):
        stats["remote"] += 1
        return

    full = os.path.join(REPO_ROOT, image_path)
    if not os.path.isfile(full):
        stats["missing"] += 1
        warn(f"{image_path}: image for {item_slug!r} not present yet")
        return

    stats["present"] += 1

    # Format rule 9.3: an image is capped whatever its dimensions say.
    file_bytes = os.path.getsize(full)
    if file_bytes > IMAGE_MAX_BYTES:
        error(
            f"{image_path}: {file_bytes / 1024 / 1024:.1f} MB, over the "
            f"{IMAGE_MAX_BYTES // 1024 // 1024} MB an image may weigh"
        )

    try:
        from PIL import Image  # imported lazily: only needed when images exist
    except ImportError:
        error(
            "Pillow is required to verify image files "
            "(pip install Pillow), but it is not installed"
        )
        return

    try:
        with Image.open(full) as image:
            fmt = image.format
            size = image.size
    except Exception as exc:  # noqa: BLE001 - any decode failure is a hard error
        error(f"{image_path}: cannot be read as an image ({exc})")
        return

    # Format first, then the house rule — so a fork that loosens the second
    # still gets told when it has left the format behind.
    if fmt not in ACCEPTED_IMAGE_FORMATS:
        error(
            f"{image_path}: {fmt} is not an accepted image format "
            f"({', '.join(ACCEPTED_IMAGE_FORMATS)})"
        )
    elif fmt != IMAGE_FORMAT:
        error(f"{image_path}: this catalog publishes {IMAGE_FORMAT} only, got {fmt}")

    shortest = min(size)
    if shortest < IMAGE_MIN_EDGE:
        error(
            f"{image_path}: {size[0]}x{size[1]} is under the "
            f"{IMAGE_MIN_EDGE}x{IMAGE_MIN_EDGE} minimum — Baggo downscales "
            f"what it fetches and never upscales"
        )
        return

    # Above the floor and under what a phone can fill: legal, and a photo the
    # app will have to render smaller than it would like. A fork that has
    # relaxed the house rule below still wants to hear about it.
    if shortest < IMAGE_PREFERRED_EDGE:
        warn(
            f"{image_path}: {size[0]}x{size[1]} is under the "
            f"{IMAGE_PREFERRED_EDGE}x{IMAGE_PREFERRED_EDGE} preferred; the "
            f"app will not upscale it"
        )

    if size != IMAGE_SIZE:
        error(
            f"{image_path}: expected "
            f"{IMAGE_SIZE[0]}x{IMAGE_SIZE[1]}, got {size[0]}x{size[1]}"
        )


def check_manifest_size() -> None:
    """Format rule 9.1: the whole catalog is one 5 MB download, at most."""
    full = os.path.join(REPO_ROOT, "manifest.json")
    if not os.path.isfile(full):
        return
    size = os.path.getsize(full)
    if size > MANIFEST_MAX_BYTES:
        error(
            f"manifest.json: {size / 1024 / 1024:.1f} MB, over the "
            f"{MANIFEST_MAX_BYTES // 1024 // 1024} MB a catalog may weigh"
        )


def check_default_locale(manifest: dict, languages: set[str]) -> None:
    """A declared fallback locale that nothing in the file speaks is a typo.

    Not a failure: the format's own fallback chain — app language, then
    default_locale, then the first entry in the map — still lands somewhere.
    """
    declared = manifest.get("default_locale")
    if isinstance(declared, str) and languages and declared not in languages:
        warn(
            f"manifest.json.default_locale: {declared!r} appears in no name "
            f"or note; clients will fall through to the first entry instead"
        )


def main() -> int:
    schema = load_schema("manifest.schema.json")
    manifest = load_json("manifest.json")

    stats = {"present": 0, "missing": 0, "remote": 0, "notes": 0}
    languages: set[str] = set()
    if errors:
        return report(None, 0, 0, stats, languages)

    check_manifest_size()
    validate_schema(manifest, schema, schema, "manifest.json")
    if not isinstance(manifest, dict):
        return report(None, 0, 0, stats, languages)

    # Categories: unique slugs, complete locales, names the app can store.
    category_slugs: set[str] = set()
    categories = manifest.get("categories", [])
    if isinstance(categories, list):
        for index, category in enumerate(categories):
            if not isinstance(category, dict):
                continue
            where = f"manifest.json.categories[{index}]"
            slug = category.get("slug")
            if slug in category_slugs:
                error(f"{where}: duplicate category slug {slug!r}")
            elif isinstance(slug, str):
                category_slugs.add(slug)
            check_locales(category.get("name"), f"{where}.name", languages)
            check_length(
                category.get("name"), CATEGORY_NAME_MAX_LENGTH, f"{where}.name"
            )

    # Items: unique slugs, a category that exists, locales, caps, photo.
    item_slugs: set[str] = set()
    items = manifest.get("items", [])
    if isinstance(items, list):
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            where = f"manifest.json.items[{index}]"
            slug = item.get("slug")
            if slug in item_slugs:
                error(f"{where}: duplicate item slug {slug!r}")
            elif isinstance(slug, str):
                item_slugs.add(slug)

            category = item.get("category")
            if isinstance(category, str) and category not in category_slugs:
                error(
                    f"{where}: category {category!r} is not one of this "
                    f"manifest's categories"
                )

            check_locales(item.get("name"), f"{where}.name", languages)
            check_length(item.get("name"), ITEM_NAME_MAX_LENGTH, f"{where}.name")

            # note is optional, but once present it ships in every locale.
            if "note" in item:
                stats["notes"] += 1
                check_locales(item.get("note"), f"{where}.note", languages)
                check_length(item.get("note"), NOTE_MAX_LENGTH, f"{where}.note")

            image = item.get("image")
            if isinstance(image, str):
                check_image(image, str(slug), stats)

    check_default_locale(manifest, languages)

    return report(manifest, len(item_slugs), len(category_slugs), stats, languages)


def report(
    manifest,
    item_count: int,
    category_count: int,
    stats: dict,
    languages: set[str],
) -> int:
    for message in warnings:
        print(f"WARNING  {message}")
    for message in errors:
        print(f"ERROR    {message}")

    identity = manifest if isinstance(manifest, dict) else {}
    print()
    print("Catalog summary")
    # The first three lines are what a user is shown before they install a
    # catalog, so they are what the author should be reading here too.
    print(f"  id                 {identity.get('id', '(missing)')}")
    print(f"  name               {identity.get('name', '(missing)')}")
    print(f"  version            {identity.get('version', '(missing)')}")
    print(f"  languages          {', '.join(sorted(languages)) or '(none)'}")
    print(f"  items              {item_count}")
    print(f"  categories         {category_count}")
    print(f"  images present     {stats['present']}")
    print(f"  images missing     {stats['missing']}")
    print(f"  images linked out  {stats['remote']}")
    print(f"  items with notes   {stats['notes']}")
    print(f"  warnings           {len(warnings)}")
    print(f"  errors             {len(errors)}")

    if errors:
        print("\nFAILED")
        return 1
    print("\nPASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
