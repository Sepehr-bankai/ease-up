import json
import logging
import os
import sys
import time
from pathlib import Path

import requests


CK = "ck_28f95061ce1ac8bc764454fd0821f025afdcf659"
CS = "cs_e4f3466fada109e56cbf3302455d25a24c514600"

MEDIA_URL = "https://nojashop.com/wp-json/wp/v2/media"
PRODUCT_URL = "https://nojashop.com/wp-json/wc/v3/products"
ROOT = Path(__file__).resolve().parent / "merged_products"
JSON_FILENAME = "data.json"
RETRIES = 3
TIMEOUT = 120

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(Path(__file__).with_name("uploader.log"), encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("uploader")


def save_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, path)


def post_with_retry(url, **kwargs):
    for attempt in range(1, RETRIES + 1):
        try:
            if hasattr(kwargs.get("data"), "seek"):
                kwargs["data"].seek(0)
            response = requests.post(url, timeout=TIMEOUT, **kwargs)
            if response.status_code < 500 and response.status_code != 429:
                return response
            log.warning("HTTP %s from %s (attempt %s/%s)", response.status_code, url, attempt, RETRIES)
        except requests.RequestException as error:
            log.warning("Request failed: %s (attempt %s/%s)", error, attempt, RETRIES)
        if attempt < RETRIES:
            time.sleep(2 ** (attempt - 1))
    return response if "response" in locals() else None


def upload_image(image_path):
    with image_path.open("rb") as image:
        response = post_with_retry(
            MEDIA_URL,
            params={"consumer_key": CK, "consumer_secret": CS},
            headers={
                "Content-Disposition": f'attachment; filename="{image_path.name}"',
                "User-Agent": "Mozilla/5.0",
            },
            data=image,
        )
    if response is None or response.status_code != 201:
        detail = response.text[:1000] if response is not None else "no response"
        raise RuntimeError(f"media upload failed: {detail}")
    media = response.json()
    return {
        "filename": image_path.name,
        "media_id": media["id"],
        "url": media["source_url"],
        "download_url": media["source_url"],
        "upload_timestamp": media.get("date_gmt") or media.get("date"),
        "slug": media.get("slug"),
        "status": media.get("status"),
        "mime_type": media.get("mime_type"),
        "alt_text": media.get("alt_text"),
        "media_details": media.get("media_details"),
    }


def product_payload(data):
    return {
        "name": data["title"],
        "type": "simple",
        "status": "draft",
        "regular_price": data["regular_price"],
        "sale_price": data.get("sale_price", ""),
        "short_description": data["short_description"],
        "description": data["description"],
        "categories": [{"name": category} for category in data.get("categories", [])],
        "attributes": [
            {"name": key, "visible": True, "options": [value]}
            for key, value in data.get("attributes", {}).items()
        ],
        "images": [{"src": image["url"]} for image in data.get("uploaded_images", [])],
    }


def process_product(directory):
    json_path = directory / JSON_FILENAME
    if not json_path.is_file():
        log.error("%s: missing %s", directory.name, JSON_FILENAME)
        return False

    try:
        with json_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        log.error("%s: invalid JSON: %s", directory.name, error)
        return False

    uploaded = data.setdefault("uploaded_images", [])
    uploaded_names = {item.get("filename") for item in uploaded}
    images_directory = directory / "images"
    if not images_directory.is_dir():
        log.warning("%s: missing images directory", directory.name)
    else:
        images = sorted(path for path in images_directory.iterdir() if path.is_file())
        if not images:
            log.warning("%s: empty images directory", directory.name)
        for image_path in images:
            if image_path.name in uploaded_names:
                log.info("%s: already uploaded %s", directory.name, image_path.name)
                continue
            try:
                log.info("%s: uploading %s", directory.name, image_path.name)
                uploaded.append(upload_image(image_path))
                uploaded_names.add(image_path.name)
                save_json(json_path, data)
            except Exception as error:
                log.exception("%s: image %s failed: %s", directory.name, image_path.name, error)

    if data.get("woocommerce_product_id"):
        log.info("%s: product already created as %s", directory.name, data["woocommerce_product_id"])
        return True

    try:
        response = post_with_retry(
            PRODUCT_URL,
            params={"consumer_key": CK, "consumer_secret": CS},
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
            json=product_payload(data),
        )
        if response is None or response.status_code != 201:
            detail = response.text[:2000] if response is not None else "no response"
            raise RuntimeError(f"product creation failed: {detail}")
        product = response.json()
        data.update(
            woocommerce_product_id=product["id"],
            woocommerce_product_url=product.get("permalink"),
            woocommerce_product_slug=product.get("slug"),
            woocommerce_product_sku=product.get("sku"),
            woocommerce_product_status=product.get("status"),
            woocommerce_date_created=product.get("date_created_gmt") or product.get("date_created"),
        )
        save_json(json_path, data)
        log.info("%s: created product %s", directory.name, product["id"])
        return True
    except Exception as error:
        log.exception("%s: %s", directory.name, error)
        return False


def self_check():
    payload = product_payload(
        {
            "title": "test",
            "regular_price": "10",
            "short_description": "short",
            "description": "long",
            "categories": ["cat"],
            "attributes": {"size": "small"},
            "uploaded_images": [{"url": "https://example.test/image.jpg"}],
        }
    )
    assert payload["name"] == "test"
    assert payload["categories"] == [{"name": "cat"}]
    assert payload["attributes"][0]["options"] == ["small"]
    assert payload["images"] == [{"src": "https://example.test/image.jpg"}]
    print("self-check passed")


def main():
    if sys.argv[1:] == ["--check"]:
        self_check()
        return 0
    if not ROOT.is_dir():
        log.error("Products directory does not exist: %s", ROOT)
        return 1
    directories = sorted(path for path in ROOT.iterdir() if path.is_dir() and path.name.startswith("product_"))
    log.info("Found %s product directories", len(directories))
    succeeded = 0
    for directory in directories:
        try:
            succeeded += process_product(directory)
        except Exception:
            log.exception("%s: unexpected failure", directory.name)
    log.info("Finished: %s succeeded, %s failed", succeeded, len(directories) - succeeded)
    return int(succeeded != len(directories))


if __name__ == "__main__":
    raise SystemExit(main())
