"""
Script to process all product JSON files inside the 'products_merged' folder.

What it does for every products_merged/product_XXXXX/*.json file:
1. Removes the "images" key (external links).
2. Removes the "sale_price" key.
3. Keeps "local_images" untouched.
4. Searches the entire JSON content for store-name variants and replaces
   them with "فروشگاه آنلاین نوژا شاپ".
5. Saves the updated JSON back to the same file.
6. Logs every action to a log file and to the console.

Usage:
    Place this script next to the 'products_merged' folder and run:
        python process_products_merged.py
"""

import json
import logging
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Root folder — the output of merge_products.py
PRODUCTS_DIR = Path("products_merged")

# Log file path
LOG_FILE = Path("process_log.txt")

# Replacement text that will substitute all store-name variants
REPLACEMENT_TEXT = "فروشگاه آنلاین نوژا شاپ"

# Store-name variants to find and replace (longest first to avoid partial matches)
STORE_NAME_VARIANTS = [
    "داروخانه آنلاین مثبت سبز",
    "داروخانه انلاین مثبت سبز",
    "داروخانه آنلاین مثبت‌ سبز",
    "داروخانه انلاین مثبت‌ سبز",
    "داروخانه آنلاین",
    "داروخانه انلاین",
    "مثبت سبز",
    "مجله سبز",
    "مجله ی سبز",
    "مثبت‌ سبز",
]
STORE_NAME_VARIANTS.sort(key=len, reverse=True)

# Keys to remove from every JSON file
KEYS_TO_REMOVE = ["images", "sale_price"]

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def remove_keys(data: dict) -> list[str]:
    """
    Remove unwanted top-level keys from the JSON data.
    Returns a list of keys that were actually removed.
    """
    removed = []
    for key in KEYS_TO_REMOVE:
        if key in data:
            del data[key]
            removed.append(key)
    return removed


def replace_store_names_in_text(text: str) -> tuple[str, int]:
    """Replace all store-name variants in a string. Returns (new_text, count)."""
    total = 0
    for variant in STORE_NAME_VARIANTS:
        pattern = re.escape(variant)
        matches = re.findall(pattern, text)
        if matches:
            total += len(matches)
            text = re.sub(pattern, REPLACEMENT_TEXT, text)
    return text, total


def process_json_value(value):
    """
    Recursively walk any JSON value and replace store-name variants
    inside all strings. Returns (processed_value, replacement_count).
    """
    if isinstance(value, str):
        return replace_store_names_in_text(value)

    if isinstance(value, dict):
        new_dict = {}
        total = 0
        for k, v in value.items():
            new_v, count = process_json_value(v)
            new_dict[k] = new_v
            total += count
        return new_dict, total

    if isinstance(value, list):
        new_list = []
        total = 0
        for item in value:
            new_item, count = process_json_value(item)
            new_list.append(new_item)
            total += count
        return new_list, total

    # int, float, bool, None — leave as-is
    return value, 0


# ---------------------------------------------------------------------------
# Per-file processing
# ---------------------------------------------------------------------------

def process_single_json_file(json_path: Path) -> None:
    logger.info(f"Processing: {json_path}")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"  Failed to parse {json_path.name}: {e}")
        return
    except Exception as e:
        logger.error(f"  Unexpected error reading {json_path.name}: {e}")
        return

    # Step 1: remove unwanted keys
    removed_keys = remove_keys(data)
    if removed_keys:
        logger.info(f"  Removed keys: {', '.join(removed_keys)}")
    else:
        logger.info(f"  No target keys found to remove.")

    # Step 2: replace store-name variants in all string values
    updated_data, replacements_count = process_json_value(data)
    logger.info(f"  Replaced {replacements_count} store-name occurrence(s).")

    # Step 3: save back to the same file
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(updated_data, f, ensure_ascii=False, indent=2)
        logger.info(f"  Saved: {json_path.name}")
    except Exception as e:
        logger.error(f"  Failed to write {json_path.name}: {e}")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def find_product_json_files(products_dir: Path) -> list[Path]:
    json_files = []

    if not products_dir.exists():
        logger.error(f"Directory not found: {products_dir.resolve()}")
        return json_files

    for subfolder in sorted(products_dir.iterdir()):
        if subfolder.is_dir() and subfolder.name.startswith("product_"):
            for json_file in subfolder.glob("*.json"):
                json_files.append(json_file)

    return json_files


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    logger.info("=" * 60)
    logger.info("Starting product JSON processing")
    logger.info(f"Source folder : {PRODUCTS_DIR.resolve()}")
    logger.info(f"Keys to remove: {KEYS_TO_REMOVE}")
    logger.info("=" * 60)

    json_files = find_product_json_files(PRODUCTS_DIR)

    if not json_files:
        logger.warning("No JSON files found. Check the folder name and structure.")
        return

    logger.info(f"Found {len(json_files)} JSON file(s).\n")

    success_count = 0
    error_count = 0

    for json_file in json_files:
        try:
            process_single_json_file(json_file)
            success_count += 1
        except Exception as e:
            logger.error(f"Unhandled error on {json_file}: {e}")
            error_count += 1

    logger.info("=" * 60)
    logger.info(f"Done. Success: {success_count}  |  Errors: {error_count}")
    logger.info(f"Log saved at : {LOG_FILE.resolve()}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()