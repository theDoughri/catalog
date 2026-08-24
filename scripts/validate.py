#!/usr/bin/env python3
"""Validate a Baggo catalog.

A catalog is one manifest.json at the repository root and an images/ folder
beside it. Nothing else is read.

Checks performed:
  1. manifest.json parses and validates against schema/manifest.schema.json.
  2. Slug uniqueness: categories and items each have their own namespace.
  3. Referential integrity: every item.category resolves to a category
     declared in the same file.
  4. Images: an item.image that EXISTS must be JPEG and exactly 1024x1024.
     A missing file is a warning, not a failure — the app falls back to the
     item's icon, and thumbnails are the app's business, not this repo's.
  5. Locales: every name map — and every optional item note — carries a
     non-empty value for all five locales the app ships (en, fr, es, de, ar).
  6. Text length: no translation of a category name, item name or item note
     exceeds the matching *_MAX_LENGTH. The app seeds all three into
     user-editable fields with those caps, so a longer one is truncated the
     moment the shopper edits that row. The catalog name is not checked: the
     app never seeds it into a field.

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
IMAGE_SIZE = (1024, 1024)

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


def validate_schema(instance, schema: dict, root: dict, path: str) -> None:
    """Append an error for every way `instance` violates `schema`."""
    if "$ref" in schema:
        validate_schema(instance, resolve_ref(root, schema["$ref"]), root, path)
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

    if isinstance(instance, list):
        min_items = schema.get("minItems")
        if min_items is not None and len(instance) < min_items:
            error(f"{path}: expected at least {min_items} item(s), got {len(instance)}")
        item_schema = schema.get("items")
        if item_schema is not None:
            for index, entry in enumerate(instance):
                validate_schema(entry, item_schema, root, f"{path}[{index}]")

    if isinstance(instance, dict):
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


def check_locales(name_map, path: str) -> None:
    if not isinstance(name_map, dict):
        return
    for locale in REQUIRED_LOCALES:
        value = name_map.get(locale)
        if not isinstance(value, str) or not value.strip():
            error(f"{path}: locale {locale!r} is missing or empty")


def check_length(text_map, limit: int, path: str) -> None:
    if not isinstance(text_map, dict):
        return
    for locale, value in text_map.items():
        if isinstance(value, str) and len(value) > limit:
            error(
                f"{path}: locale {locale!r} is {len(value)} characters, "
                f"over the {limit} the app can store"
            )


def check_image(image_path: str, item_slug: str, stats: dict) -> None:
    """Verify one item photo. Paths are relative to the manifest, at the root."""
    full = os.path.join(REPO_ROOT, image_path)
    if not os.path.isfile(full):
        stats["missing"] += 1
        warn(f"{image_path}: image for {item_slug!r} not present yet")
        return

    stats["present"] += 1
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

    if fmt != "JPEG":
        error(f"{image_path}: expected JPEG, got {fmt}")
    if size != IMAGE_SIZE:
        error(
            f"{image_path}: expected "
            f"{IMAGE_SIZE[0]}x{IMAGE_SIZE[1]}, got {size[0]}x{size[1]}"
        )


def main() -> int:
    schema = load_schema("manifest.schema.json")
    manifest = load_json("manifest.json")

    stats = {"present": 0, "missing": 0, "notes": 0}
    if errors:
        return report(0, 0, stats)

    validate_schema(manifest, schema, schema, "manifest.json")
    if not isinstance(manifest, dict):
        return report(0, 0, stats)

    check_locales(manifest.get("name"), "manifest.json.name")

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
            check_locales(category.get("name"), f"{where}.name")
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

            check_locales(item.get("name"), f"{where}.name")
            check_length(item.get("name"), ITEM_NAME_MAX_LENGTH, f"{where}.name")

            # note is optional, but once present it ships in every locale.
            if "note" in item:
                stats["notes"] += 1
                check_locales(item.get("note"), f"{where}.note")
                check_length(item.get("note"), NOTE_MAX_LENGTH, f"{where}.note")

            image = item.get("image")
            if isinstance(image, str):
                check_image(image, str(slug), stats)

    return report(len(item_slugs), len(category_slugs), stats)


def report(item_count: int, category_count: int, stats: dict) -> int:
    for message in warnings:
        print(f"WARNING  {message}")
    for message in errors:
        print(f"ERROR    {message}")

    print()
    print("Catalog summary")
    print(f"  items              {item_count}")
    print(f"  categories         {category_count}")
    print(f"  images present     {stats['present']}")
    print(f"  images missing     {stats['missing']}")
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
