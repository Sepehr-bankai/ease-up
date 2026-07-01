"""
Merge all product subfolders from products/, products-1/ ... products-6/
into a single new folder called "products_merged/" with sequential numbering.

Example:
    products/product_0001/     →  products_merged/product_00001/
    products/product_0002/     →  products_merged/product_00002/
    products-1/product_0001/   →  products_merged/product_00003/
    products-1/product_0002/   →  products_merged/product_00004/
    ...

Usage:
    Place this script next to your products/ folders and run:
        python merge_products.py
"""

import shutil
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Source folders to scan, in the order they will be numbered
SOURCE_FOLDERS = [
    Path("products"),
    Path("products-1"),
    Path("products-2"),
    Path("products-3"),
    Path("products-4"),
    Path("products-5"),
    Path("products-6"),
]

# Output folder (will be created fresh — must not already exist)
OUTPUT_FOLDER = Path("products_merged")

# ---------------------------------------------------------------------------


def find_product_subfolders(source: Path) -> list[Path]:
    """Return sorted list of product_XXXX subfolders inside a source folder."""
    if not source.exists():
        print(f"  [skip] '{source}' does not exist.")
        return []

    subfolders = sorted(
        p for p in source.iterdir()
        if p.is_dir() and p.name.startswith("product_")
    )
    return subfolders


def main():
    print("=" * 60)
    print("Product folder merger")
    print("=" * 60)

    if OUTPUT_FOLDER.exists():
        print(f"\n❌  '{OUTPUT_FOLDER}' already exists.")
        print("    Please rename or delete it before running this script.")
        return

    OUTPUT_FOLDER.mkdir()
    print(f"\n✅  Created output folder: {OUTPUT_FOLDER.resolve()}\n")

    global_index = 1  # running counter across all source folders

    for source in SOURCE_FOLDERS:
        print(f"📂  Scanning: {source}/")
        subfolders = find_product_subfolders(source)

        if not subfolders:
            print(f"    No product_XXXX subfolders found.\n")
            continue

        print(f"    Found {len(subfolders)} subfolder(s).")

        for subfolder in subfolders:
            new_name = f"product_{global_index:05d}"
            destination = OUTPUT_FOLDER / new_name

            shutil.copytree(subfolder, destination)
            print(f"    {source.name}/{subfolder.name}  →  {OUTPUT_FOLDER.name}/{new_name}")

            global_index += 1

        print()

    total = global_index - 1
    print("=" * 60)
    print(f"✅  Done! {total} folders merged into '{OUTPUT_FOLDER}/'")
    print(f"    Numbering: product_00001  →  product_{total:05d}")
    print("=" * 60)


if __name__ == "__main__":
    main()