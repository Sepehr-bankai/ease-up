"""
Script to process all product JSON files inside the 'products' folder.

What it does for every products/product_XXXX/*.json file:
1. Removes the "images" key (external links) and keeps only "local_images".
2. Searches the entire JSON content for store-name variants such as:
   "داروخانه انلاین", "داروخانه انلاین مثبت سبز", "مثبت سبز",
   "داروخانه آنلاین مثبت سبز", "داروخانه آنلاین", and similar variants,
   and replaces them with "فروشگاه آنلاین نوژا شاپ".
3. Saves the updated JSON back to the same file.
4. Logs every action (file processed, replacements made, errors) to a log file
   and to the console.

Usage:
    Place this script in the parent folder that contains the "products" folder,
    then run:
        python process_products.py
"""

import json
import logging
import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Root folder that contains all product_XXXX subfolders
PRODUCTS_DIR = Path("products")

# Log file path
LOG_FILE = Path("process_log.txt")

# Replacement text that will substitute all store-name variants
REPLACEMENT_TEXT = "فروشگاه آنلاین نوژا شاپ"

# List of store-name variants to search for and replace.
# Sorted by length (longest first) so longer/more specific phrases
# are replaced before their shorter substrings.
STORE_NAME_VARIANTS = [
    "داروخانه آنلاین مثبت سبز",
    "داروخانه انلاین مثبت سبز",
    "داروخانه آنلاین مثبت‌ سبز",
    "داروخانه انلاین مثبت‌ سبز",
    "داروخانه آنلاین",
    "داروخانه انلاین",
    "مثبت سبز",
    "مثبت‌ سبز",
]

# Sort variants by length descending to avoid partial/overlapping replacements
STORE_NAME_VARIANTS.sort(key=len, reverse=True)

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


def remove_images_key(data: dict) -> bool:
    """
    Remove the external 'images' key from the JSON data, keeping 'local_images'.
    Returns True if the key was found and removed, False otherwise.
    """
    if "images" in data:
        del data["images"]
        return True
    return False


def replace_store_names_in_text(text: str) -> tuple[str, int]:
    """
    Replace all store-name variants found in a given text string.
    Returns a tuple of (updated_text, number_of_replacements).
    """
    total_replacements = 0
    updated_text = text

    for variant in STORE_NAME_VARIANTS:
        # Use regex to count and replace, escaping special characters
        pattern = re.escape(variant)
        matches = re.findall(pattern, updated_text)
        if matches:
            total_replacements += len(matches)
            updated_text = re.sub(pattern, REPLACEMENT_TEXT, updated_text)

    return updated_text, total_replacements


def process_json_value(value):
    """
    Recursively process any JSON value (dict, list, str, etc.)
    and replace store-name variants inside all string values.
    Returns a tuple of (processed_value, number_of_replacements).
    """
    total_replacements = 0

    if isinstance(value, str):
        new_value, count = replace_store_names_in_text(value)
        return new_value, count

    elif isinstance(value, dict):
        new_dict = {}
        for key, val in value.items():
            new_val, count = process_json_value(val)
            new_dict[key] = new_val
            total_replacements += count
        return new_dict, total_replacements

    elif isinstance(value, list):
        new_list = []
        for item in value:
            new_item, count = process_json_value(item)
            new_list.append(new_item)
            total_replacements += count
        return new_list, total_replacements

    else:
        # Numbers, booleans, None, etc. - return as is
        return value, 0


def process_single_json_file(json_path: Path) -> None:
    """
    Process a single product JSON file:
    - Remove external 'images' key
    - Replace store-name variants throughout the JSON
    - Save the updated JSON back to disk
    """
    logger.info(f"Processing file: {json_path}")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Failed to read/parse JSON file {json_path}: {e}")
        return
    except Exception as e:
        logger.error(f"Unexpected error reading {json_path}: {e}")
        return

    # Step 1: remove external "images" key
    images_removed = remove_images_key(data)
    if images_removed:
        logger.info(f"  Removed external 'images' key from {json_path.name}")
    else:
        logger.info(f"  No 'images' key found in {json_path.name}")

    # Step 2: replace store-name variants across the whole JSON
    updated_data, replacements_count = process_json_value(data)
    logger.info(f"  Replaced {replacements_count} store-name occurrence(s) in {json_path.name}")

    # Step 3: save the updated JSON back to the same file
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(updated_data, f, ensure_ascii=False, indent=2)
        logger.info(f"  Successfully saved updated file: {json_path.name}")
    except Exception as e:
        logger.error(f"Failed to write updated JSON to {json_path}: {e}")


def find_product_json_files(products_dir: Path):
    """
    Find all JSON files inside each product_XXXX subfolder under products_dir.
    Returns a list of Path objects pointing to JSON files.
    """
    json_files = []

    if not products_dir.exists():
        logger.error(f"Products directory not found: {products_dir.resolve()}")
        return json_files

    # Iterate over all subfolders that start with "product_"
    for subfolder in sorted(products_dir.iterdir()):
        if subfolder.is_dir() and subfolder.name.startswith("product_"):
            # Find all .json files inside this subfolder
            for json_file in subfolder.glob("*.json"):
                json_files.append(json_file)

    return json_files


def main():
    """
    Main entry point: find and process all product JSON files.
    """
    logger.info("=" * 60)
    logger.info("Starting product JSON processing script")
    logger.info("=" * 60)

    json_files = find_product_json_files(PRODUCTS_DIR)

    if not json_files:
        logger.warning("No JSON files found to process. Please check the 'products' folder structure.")
        return

    logger.info(f"Found {len(json_files)} JSON file(s) to process.")

    success_count = 0
    error_count = 0

    for json_file in json_files:
        try:
            process_single_json_file(json_file)
            success_count += 1
        except Exception as e:
            logger.error(f"Unhandled error while processing {json_file}: {e}")
            error_count += 1

    logger.info("=" * 60)
    logger.info(f"Processing complete. Success: {success_count}, Errors: {error_count}")
    logger.info(f"Log file saved at: {LOG_FILE.resolve()}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
