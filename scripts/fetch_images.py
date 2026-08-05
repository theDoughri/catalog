#!/usr/bin/env python3
"""Download and crop the item images listed in each list's items_images.csv.

Every list directory carries an items_images.csv next to its manifest
(e.g. lists/default/items_images.csv) with two columns:

  name  - the item slug, matching the items in that list's manifest
  link  - a photo URL you picked by hand, e.g. from pixabay.com or
          unsplash.com; leave empty until chosen

Accepted link forms:

  * any direct image URL (.jpg / .png / .webp)
  * an Unsplash photo page (https://unsplash.com/photos/<id>) - the
    full-resolution download endpoint is used automatically
  * a Pixabay photo page or cdn.pixabay.com link - the page is resolved
    and the 1920px rendition preferred

Every source image must be at least 1024x1024 (the script never upscales).
It is center-cropped to exactly 1024x1024 and saved as a JPEG at the item's
declared `image` path (e.g. lists/default/images/apples.jpg). Items whose
link is still empty are reported and skipped; existing image files are
skipped too unless --force is given, so the script is safe to re-run as the
CSV fills up.

At the end of every run the script also refreshes a `thumbnails/` folder
next to each list's `images/` folder: every available image gets a 128x128
thumbnail under the same file name, stale thumbnails are regenerated, and
thumbnails whose source image no longer exists are removed.

Usage:
  python3 scripts/fetch_images.py
  python3 scripts/fetch_images.py --force --only apples
  python3 scripts/fetch_images.py --dry-run   # show resolved URLs, no download

Requires Python 3.9+ and Pillow (python3 -m pip install --user Pillow).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_NAME = "items_images.csv"
TARGET = 1024
THUMB = 128
MAX_DOWNLOAD_BYTES = 40 * 1024 * 1024
REQUEST_DELAY = 0.3  # polite pause between downloads, seconds

USER_AGENT = (
    "BaggoCatalogImageFetcher/2.0 (+https://github.com/theDoughri/catalog)"
)
# Some hosts reject non-browser clients with 403; retried with this UA.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
CURL = shutil.which("curl")

_ssl_context = None


def ssl_context() -> ssl.SSLContext:
    """Default SSL context, using certifi's CA bundle when available."""
    global _ssl_context
    if _ssl_context is None:
        _ssl_context = ssl.create_default_context()
        try:  # helps macOS pythons whose system store is incomplete
            import certifi

            _ssl_context.load_verify_locations(certifi.where())
        except ImportError:
            pass
    return _ssl_context


def fetch_url(
    url: str,
    timeout: int = 60,
    user_agent: str | None = None,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> tuple[int, bytes | None]:
    """GET `url`, returning (http_status, body-or-None).

    Uses curl when available (several image hosts reject Python's TLS
    fingerprint with 403 while accepting curl); falls back to urllib.
    Status 0 means the request failed before an HTTP response; -1 means
    the body exceeded `max_bytes`.
    """
    user_agent = user_agent or USER_AGENT

    if url.startswith("file:"):  # local files (used by the test-suite)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return 200, response.read()
        except Exception as exc:  # noqa: BLE001
            print(f"    ! {exc}")
            return 0, None

    if CURL:
        handle = tempfile.NamedTemporaryFile(delete=False)
        handle.close()
        command = [
            CURL, "-sS", "-L",
            "--max-time", str(timeout),
            "--max-filesize", str(max_bytes),
            "-A", user_agent,
            "-H", "Accept: */*",
            "-o", handle.name,
            "-w", "%{http_code}",
            url,
        ]
        try:
            proc = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout + 15
            )
            if proc.returncode == 63:  # --max-filesize exceeded
                print("    ! skipping oversized download")
                return -1, None
            status = int(proc.stdout.strip() or 0)
            if proc.returncode != 0 and status == 0:
                message = proc.stderr.strip().splitlines()
                print(f"    ! curl: {message[-1] if message else 'request failed'}")
                return 0, None
            with open(handle.name, "rb") as body:
                data = body.read(max_bytes + 1)
            if len(data) > max_bytes:
                print("    ! skipping oversized download")
                return -1, None
            return status, data
        except Exception as exc:  # noqa: BLE001
            print(f"    ! {exc}")
            return 0, None
        finally:
            try:
                os.unlink(handle.name)
            except OSError:
                pass

    # urllib fallback (no curl on this system)
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent, "Accept": "*/*"}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=timeout, context=ssl_context()
        ) as response:
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                print("    ! skipping oversized download")
                return -1, None
            return response.status, data
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception as exc:  # noqa: BLE001
        print(f"    ! {exc}")
        return 0, None


def request_with_retries(url: str, retries: int = 3) -> bytes | None:
    """fetch_url plus backoff on rate-limits and a browser-UA retry on 403."""
    user_agent = USER_AGENT
    tried_browser_ua = False
    attempt = 0
    while attempt < retries:
        status, data = fetch_url(url, user_agent=user_agent)
        if status == 200 and data is not None:
            return data
        if status == -1:  # oversized: no point retrying
            return None
        if status == 403 and not tried_browser_ua:
            tried_browser_ua = True  # doesn't consume a retry
            user_agent = BROWSER_UA
            time.sleep(1)
            continue
        attempt += 1
        if status in (0, 429, 500, 502, 503) and attempt < retries:
            time.sleep(2 ** attempt)
            continue
        if status:
            print(f"    ! HTTP {status} from {urllib.parse.urlsplit(url).netloc}")
        return None
    return None


# --------------------------------------------------------------------------
# Link resolution: turn whatever was pasted into direct image URL candidates.
# --------------------------------------------------------------------------

PIXABAY_SIZE_SUFFIX = re.compile(r"_(\d{3,4})(\.[A-Za-z]+)$")
OG_IMAGE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']'
)


def pixabay_variants(image_url: str) -> list[str]:
    """Prefer the 1920px rendition of a cdn.pixabay.com image."""
    match = PIXABAY_SIZE_SUFFIX.search(image_url)
    if match and match.group(1) != "1920":
        return [PIXABAY_SIZE_SUFFIX.sub(r"_1920\2", image_url), image_url]
    return [image_url]


def resolve_pixabay_page(page_url: str) -> list[str]:
    """Extract the photo URL from a pixabay.com photo page via og:image."""
    html = request_with_retries(page_url)
    if html is None:
        print("    ! could not open the Pixabay page; paste the direct image "
              "address instead (right-click the photo > Copy Image Address)")
        return []
    match = OG_IMAGE.search(html.decode("utf-8", errors="replace"))
    if not match:
        print("    ! no image found on the Pixabay page")
        return []
    return pixabay_variants(match.group(1) or match.group(2))


def candidate_urls(link: str) -> list[str]:
    """Ordered list of direct image URLs to try for a CSV link."""
    link = link.strip().strip('"').strip("'")
    parts = urllib.parse.urlsplit(link)
    host = parts.netloc.lower()

    # Unsplash photo page -> its full-resolution download endpoint.
    if host.endswith("unsplash.com") and host != "images.unsplash.com":
        path = parts.path.rstrip("/")
        if not path.endswith("/download"):
            path += "/download"
        page = urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        return [page + "?force=true&w=2048", page]

    # Direct Unsplash CDN link -> ask their CDN for a 2048px JPEG.
    if host == "images.unsplash.com":
        query = dict(urllib.parse.parse_qsl(parts.query))
        query.update({"w": "2048", "q": "85", "fm": "jpg", "fit": "max"})
        big = urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, parts.path,
             urllib.parse.urlencode(query), "")
        )
        return [big] + ([link] if link != big else [])

    # Pixabay photo page -> resolve to the CDN image.
    if host.endswith("pixabay.com") and host != "cdn.pixabay.com":
        return resolve_pixabay_page(link)

    # Direct Pixabay CDN link -> prefer the 1920px rendition.
    if host == "cdn.pixabay.com":
        return pixabay_variants(link)

    return [link]


# --------------------------------------------------------------------------
# Image processing.
# --------------------------------------------------------------------------

def crop_to_target(data: bytes) -> bytes | None:
    """Center-crop to exactly TARGET x TARGET JPEG; None if unusable."""
    from PIL import Image, ImageOps

    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image)
    except Exception as exc:  # noqa: BLE001 - not an image, try next candidate
        print(f"    ! cannot decode image ({exc})")
        return None

    width, height = image.size
    if width < TARGET or height < TARGET:
        print(f"    ! image is {width}x{height}; both sides must be >= {TARGET}")
        return None

    if image.mode != "RGB":
        image = image.convert("RGB")

    scale = TARGET / min(width, height)
    if scale < 1.0:
        image = image.resize(
            (max(TARGET, round(width * scale)), max(TARGET, round(height * scale))),
            Image.LANCZOS,
        )
    width, height = image.size
    left = (width - TARGET) // 2
    top = (height - TARGET) // 2
    image = image.crop((left, top, left + TARGET, top + TARGET))

    output = io.BytesIO()
    image.save(output, format="JPEG", quality=88, optimize=True, progressive=True)
    return output.getvalue()


def make_thumbnails(list_dir: str) -> tuple[int, int]:
    """Sync <list_dir>/thumbnails with <list_dir>/images.

    Every .jpg in images/ gets a THUMB x THUMB thumbnail of the same name;
    stale thumbnails (older than their source) are regenerated and orphaned
    thumbnails (source image gone) are deleted.
    Returns (created_or_updated, removed).
    """
    from PIL import Image, ImageOps

    images_dir = os.path.join(list_dir, "images")
    thumbs_dir = os.path.join(list_dir, "thumbnails")
    if not os.path.isdir(images_dir):
        return 0, 0
    os.makedirs(thumbs_dir, exist_ok=True)

    updated = removed = 0
    sources = sorted(
        f for f in os.listdir(images_dir) if f.lower().endswith(".jpg")
    )
    for name in sources:
        src = os.path.join(images_dir, name)
        dst = os.path.join(thumbs_dir, name)
        if os.path.isfile(dst) and os.path.getmtime(dst) >= os.path.getmtime(src):
            continue
        try:
            with Image.open(src) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode != "RGB":
                    image = image.convert("RGB")
                width, height = image.size
                side = min(width, height)
                left = (width - side) // 2
                top = (height - side) // 2
                image = image.crop((left, top, left + side, top + side))
                image = image.resize((THUMB, THUMB), Image.LANCZOS)
                image.save(dst, format="JPEG", quality=85, optimize=True)
            updated += 1
        except Exception as exc:  # noqa: BLE001 - a bad file must not stop the run
            print(f"  ! thumbnail failed for {name}: {exc}")

    source_names = set(sources)
    for name in os.listdir(thumbs_dir):
        if name.lower().endswith(".jpg") and name not in source_names:
            os.remove(os.path.join(thumbs_dir, name))
            removed += 1
    return updated, removed


# --------------------------------------------------------------------------
# CSV + manifests.
# --------------------------------------------------------------------------

def read_links(csv_path: str) -> dict[str, str] | None:
    """Slug -> link map from a list's items_images.csv; None if absent."""
    if not os.path.isfile(csv_path):
        return None
    links: dict[str, str] = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            name = (row.get("name") or "").strip()
            if name:
                links[name] = (row.get("link") or "").strip()
    return links


def iter_lists():
    """Yield (list_slug, list_dir, items) for every list in the catalog."""
    with open(os.path.join(REPO_ROOT, "manifest.json"), encoding="utf-8") as handle:
        root = json.load(handle)
    for entry in root.get("lists", []):
        rel_path = entry["path"]
        with open(os.path.join(REPO_ROOT, rel_path), encoding="utf-8") as handle:
            manifest = json.load(handle)
        list_dir = os.path.join(REPO_ROOT, os.path.dirname(rel_path))
        yield entry["slug"], list_dir, manifest.get("items", [])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", action="append", default=None,
                        help="only fetch this item slug (repeatable)")
    parser.add_argument("--force", action="store_true",
                        help="refetch even when the image file already exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the resolved image URLs without downloading")
    args = parser.parse_args()

    if not args.dry_run:
        try:
            import PIL  # noqa: F401
        except ImportError:
            print("Pillow is required: python3 -m pip install --user Pillow")
            return 1

    saved, present, no_link = 0, 0, []
    failed: list[str] = []
    unknown: list[str] = []
    missing_rows: list[str] = []
    missing_csv: list[str] = []
    thumbs_updated = thumbs_removed = 0

    for list_slug, list_dir, items in iter_lists():
        print(f"list: {list_slug}")
        csv_path = os.path.join(list_dir, CSV_NAME)
        links = read_links(csv_path)
        if links is None:
            print(f"  ! {os.path.relpath(csv_path, REPO_ROOT)} not found")
            missing_csv.append(list_slug)
            continue

        seen_slugs: set[str] = set()
        for item in items:
            slug = item["slug"]
            seen_slugs.add(slug)
            dest = os.path.join(list_dir, item["image"])
            if args.only and slug not in args.only:
                continue
            if os.path.isfile(dest) and not args.force:
                present += 1
                continue
            link = links.get(slug, "")
            if not link:
                no_link.append(slug)
                continue

            print(f"  {slug} ({item['name']['en']})")
            if args.dry_run:
                for url in candidate_urls(link):
                    print(f"    would try: {url}")
                saved += 1
                continue
            written = False
            for url in candidate_urls(link):
                time.sleep(REQUEST_DELAY)
                data = request_with_retries(url)
                if data is None:
                    continue
                cropped = crop_to_target(data)
                if cropped is None:
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as handle:
                    handle.write(cropped)
                print(f"    saved {os.path.relpath(dest, REPO_ROOT)}")
                saved += 1
                written = True
                break
            if not written:
                print(f"    ! FAILED - check the link in {CSV_NAME}")
                failed.append(slug)

        unknown += sorted(set(links) - seen_slugs)
        missing_rows += sorted(s for s in seen_slugs if s not in links)

        if not args.dry_run:
            created, removed = make_thumbnails(list_dir)
            thumbs_updated += created
            thumbs_removed += removed

    print()
    print("Fetch summary")
    print(f"  saved             {saved}")
    print(f"  already present   {present}")
    print(f"  no link yet       {len(no_link)}")
    print(f"  failed            {len(failed)}")
    print(f"  thumbnails        {thumbs_updated} updated, {thumbs_removed} removed")
    if failed:
        print("  failed items: " + ", ".join(failed))
    if unknown:
        print("  CSV names matching no item: " + ", ".join(unknown))
    if missing_rows:
        print("  items missing from the CSV: " + ", ".join(missing_rows))
    if missing_csv:
        print("  lists without a CSV: " + ", ".join(missing_csv))
    if failed:
        print("\nRe-run a fixed item with: "
              "python3 scripts/fetch_images.py --force --only <slug>")
    return 1 if failed or missing_csv else 0


if __name__ == "__main__":
    sys.exit(main())
