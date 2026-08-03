#!/usr/bin/env python3
"""Validate the Baggo official catalog.

Checks performed:
  1. manifest.json, categories.json and every list manifest parse and validate
     against their JSON Schema in schema/.
  2. Slug uniqueness: items within a list, categories globally, lists globally.
  3. Referential integrity: item.category exists in categories.json, and every
     list entry in the root manifest points to an existing manifest file whose
     own slug matches.
  4. Images: an item.image that EXISTS must be JPEG and exactly 1024x1024.
     A missing file is a warning, not a failure.
  5. Locales: every name map carries a non-empty value for all five locales
     the app ships (en, fr, es, de, ar).

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


def check_image(image_path: str, item_slug: str, list_dir: str, stats: dict) -> None:
    full = os.path.join(REPO_ROOT, list_dir, image_path)
    if not os.path.isfile(full):
        stats["missing"] += 1
        warn(f"{list_dir}/{image_path}: image for {item_slug!r} not present yet")
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
        error(f"{list_dir}/{image_path}: cannot be read as an image ({exc})")
        return

    if fmt != "JPEG":
        error(f"{list_dir}/{image_path}: expected JPEG, got {fmt}")
    if size != IMAGE_SIZE:
        error(
            f"{list_dir}/{image_path}: expected "
            f"{IMAGE_SIZE[0]}x{IMAGE_SIZE[1]}, got {size[0]}x{size[1]}"
        )


def main() -> int:
    root_schema = load_schema("root-manifest.schema.json")
    categories_schema = load_schema("categories.schema.json")
    list_schema = load_schema("list-manifest.schema.json")

    root_manifest = load_json("manifest.json")
    categories = load_json("categories.json")

    if errors:
        return report(0, 0, {"present": 0, "missing": 0})

    validate_schema(root_manifest, root_schema, root_schema, "manifest.json")
    validate_schema(categories, categories_schema, categories_schema, "categories.json")

    # Categories: unique slugs, complete locales.
    category_slugs: set[str] = set()
    if isinstance(categories, list):
        for index, category in enumerate(categories):
            if not isinstance(category, dict):
                continue
            slug = category.get("slug")
            if slug in category_slugs:
                error(f"categories.json[{index}]: duplicate category slug {slug!r}")
            elif isinstance(slug, str):
                category_slugs.add(slug)
            check_locales(category.get("name"), f"categories.json[{index}].name")

    check_locales(
        root_manifest.get("name") if isinstance(root_manifest, dict) else None,
        "manifest.json.name",
    )

    # Lists: unique slugs, resolvable paths, then per-list validation.
    stats = {"present": 0, "missing": 0}
    item_total = 0
    list_slugs: set[str] = set()
    entries = root_manifest.get("lists", []) if isinstance(root_manifest, dict) else []

    for index, entry in enumerate(entries):
        where = f"manifest.json.lists[{index}]"
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if slug in list_slugs:
            error(f"{where}: duplicate list slug {slug!r}")
        elif isinstance(slug, str):
            list_slugs.add(slug)
        check_locales(entry.get("name"), f"{where}.name")

        rel_path = entry.get("path")
        if not isinstance(rel_path, str):
            continue
        list_manifest = load_json(rel_path)
        if list_manifest is None:
            error(f"{where}: path {rel_path!r} does not resolve to a manifest")
            continue

        validate_schema(list_manifest, list_schema, list_schema, rel_path)
        if not isinstance(list_manifest, dict):
            continue

        if list_manifest.get("slug") != slug:
            error(
                f"{rel_path}: slug {list_manifest.get('slug')!r} does not match "
                f"{slug!r} declared in the root manifest"
            )
        check_locales(list_manifest.get("name"), f"{rel_path}.name")

        list_dir = os.path.dirname(rel_path)
        item_slugs: set[str] = set()
        for item_index, item in enumerate(list_manifest.get("items", [])):
            if not isinstance(item, dict):
                continue
            item_total += 1
            item_where = f"{rel_path}.items[{item_index}]"
            item_slug = item.get("slug")
            if item_slug in item_slugs:
                error(f"{item_where}: duplicate item slug {item_slug!r}")
            elif isinstance(item_slug, str):
                item_slugs.add(item_slug)

            category = item.get("category")
            if isinstance(category, str) and category not in category_slugs:
                error(
                    f"{item_where}: category {category!r} is not defined in "
                    f"categories.json"
                )

            check_locales(item.get("name"), f"{item_where}.name")

            image = item.get("image")
            if isinstance(image, str):
                check_image(image, str(item_slug), list_dir, stats)

    return report(item_total, len(category_slugs), stats)


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
    print(f"  warnings           {len(warnings)}")
    print(f"  errors             {len(errors)}")

    if errors:
        print("\nFAILED")
        return 1
    print("\nPASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
