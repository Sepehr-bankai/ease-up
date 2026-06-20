"""
Script to extract all unique categories from product JSON files.

What it does:
1. Reads all JSON files inside products/product_XXXX/ subfolders
2. Extracts the "categories" field from each product
3. Counts how many products belong to each category
4. Saves a summary JSON and a readable text report
5. Prints a clean summary to console
"""

import json
import logging
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PRODUCTS_DIR = Path("products")
OUTPUT_JSON = Path("categories_summary.json")
OUTPUT_REPORT = Path("categories_report.txt")

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


def find_product_json_files(products_dir: Path):
    """Find all JSON files inside product_XXXX subfolders."""
    json_files = []

    if not products_dir.exists():
        logger.error(f"Products directory not found: {products_dir.resolve()}")
        return json_files

    for subfolder in sorted(products_dir.iterdir()):
        if subfolder.is_dir() and subfolder.name.startswith("product_"):
            for json_file in subfolder.glob("*.json"):
                json_files.append(json_file)

    return json_files


def extract_categories(json_files: list) -> tuple[Counter, dict, int, int]:
    """
    Extract categories from all product JSON files.
    Returns:
        - category_counter: Counter of {category: count}
        - category_to_products: dict of {category: [product titles]}
        - success_count: number of files successfully read
        - error_count: number of files that failed
    """
    category_counter = Counter()
    category_to_products = {}
    success_count = 0
    error_count = 0

    for json_file in json_files:
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            categories = data.get("categories", [])
            title = data.get("title", json_file.name)

            if isinstance(categories, list):
                for cat in categories:
                    cat = cat.strip()
                    if cat:
                        category_counter[cat] += 1
                        if cat not in category_to_products:
                            category_to_products[cat] = []
                        category_to_products[cat].append(title)
            elif isinstance(categories, str) and categories.strip():
                cat = categories.strip()
                category_counter[cat] += 1
                if cat not in category_to_products:
                    category_to_products[cat] = []
                category_to_products[cat].append(title)

            success_count += 1

        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Failed to read {json_file}: {e}")
            error_count += 1
        except Exception as e:
            logger.error(f"Unexpected error reading {json_file}: {e}")
            error_count += 1

    return category_counter, category_to_products, success_count, error_count


def save_results(category_counter: Counter, category_to_products: dict,
                 total_files: int, success_count: int, error_count: int):
    """Save summary JSON and readable text report."""

    # --- Save JSON summary ---
    sorted_categories = [
        {"category": cat, "product_count": count, "products": category_to_products[cat]}
        for cat, count in category_counter.most_common()
    ]

    summary = {
        "total_files_processed": total_files,
        "success": success_count,
        "errors": error_count,
        "unique_category_count": len(category_counter),
        "categories": sorted_categories
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    logger.info(f"JSON summary saved to: {OUTPUT_JSON.resolve()}")

    # --- Save text report ---
    lines = []
    lines.append("=" * 60)
    lines.append("گزارش کتگوری‌های محصولات")
    lines.append("=" * 60)
    lines.append(f"تعداد کل فایل‌های پردازش شده : {total_files}")
    lines.append(f"موفق                          : {success_count}")
    lines.append(f"خطا                           : {error_count}")
    lines.append(f"تعداد کتگوری‌های یکتا         : {len(category_counter)}")
    lines.append("=" * 60)
    lines.append("")
    lines.append("کتگوری‌ها (مرتب بر اساس تعداد محصول):")
    lines.append("-" * 60)

    for rank, (cat, count) in enumerate(category_counter.most_common(), start=1):
        lines.append(f"{rank:>3}. {cat}  ({count} محصول)")

    lines.append("")
    lines.append("=" * 60)
    lines.append("جزئیات محصولات هر کتگوری:")
    lines.append("=" * 60)

    for cat, count in category_counter.most_common():
        lines.append(f"\n▸ {cat}  [{count} محصول]")
        for product_title in category_to_products[cat]:
            lines.append(f"    - {product_title}")

    report_text = "\n".join(lines)

    with open(OUTPUT_REPORT, "w", encoding="utf-8") as f:
        f.write(report_text)

    logger.info(f"Text report saved to: {OUTPUT_REPORT.resolve()}")

    return report_text


def main():
    print("=" * 60)
    print("products categories extrtaction")
    print("=" * 60)

    json_files = find_product_json_files(PRODUCTS_DIR)

    if not json_files:
        logger.warning("no files found.")
        return

    print(f"\nnumber of files: {len(json_files)}\n")

    category_counter, category_to_products, success_count, error_count = extract_categories(json_files)

    report = save_results(
        category_counter, category_to_products,
        total_files=len(json_files),
        success_count=success_count,
        error_count=error_count
    )

    print("\n" + report)

    # Print quick summary at the end
    print("\n" + "=" * 60)
    print(f"✅ تعداد کتگوری‌های یکتا: {len(category_counter)}")
    print(f"📄 گزارش متنی: {OUTPUT_REPORT.resolve()}")
    print(f"📦 خروجی JSON: {OUTPUT_JSON.resolve()}")
    print("=" * 60)


if __name__ == "__main__":
    main()