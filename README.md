# Mosbatesabz Product Scraper Pipeline

A multi-phase scraping and post-processing pipeline that collects product data (titles, prices, categories, descriptions, attributes, and images) from **mosbatesabz.com** and prepares it for use in another store under a new brand name.

---

## How It Works — Overview

The pipeline runs in four sequential steps:

```
Phase 1                Phase 2                 Phase 3                  Phase 4
Collect URLs    →    Scrape Products    →    Post-Process JSON    →    Analyse Categories
(gather links)      (open each link)        (clean & rebrand)         (summary report)
```

---

## Project Structure

```
.
├── first-phase-product-links-gatherer.py   # Phase 1 — collect product URLs
├── second-phase-product-links-opener.py    # Phase 2 — scrape each product page
├── process_products.py                     # Phase 3 — clean & rebrand JSON files
├── Categories.py                           # Phase 4 — analyse & report categories
│
├── product_links.txt                       # Output of Phase 1 (auto-created)
├── progress.txt                            # Resume tracker for Phase 2 (auto-created)
│
├── products/                               # Output of Phase 2 (auto-created)
│   ├── product_0001/
│   │   ├── data.json
│   │   └── images/
│   │       ├── product_0001_1.webp
│   │       └── ...
│   ├── product_0002/
│   │   └── ...
│   └── ...
│
├── categories_summary.json                 # Output of Phase 4
└── categories_report.txt                   # Output of Phase 4
```

---

## Requirements

- Python 3.8+
- The following packages:

```bash
pip install requests beautifulsoup4
```

> No browser automation is needed — all four scripts use plain HTTP requests.

---

## Step-by-Step Execution Guide

### Phase 1 — Collect Product URLs

**Script:** `first-phase-product-links-gatherer.py`

Opens every page of a category listing, finds all product links using the `product-image-link` CSS class, and saves them to `product_links.txt`.

**Before running**, open the script and set these two variables at the top:

| Variable | Description |
|----------|-------------|
| `BASE_URL` | The first page of the category you want to scrape |
| `TOTAL_PAGES` | How many listing pages to go through |

**Run:**
```bash
python first-phase-product-links-gatherer.py
```

**Output:** `product_links.txt` — one product URL per line.

---

### Phase 2 — Scrape Each Product Page

**Script:** `second-phase-product-links-opener.py`

Reads `product_links.txt`, visits each URL, and extracts:
- Title, regular price, sale price
- Categories, short description, full description
- Product attributes (weight, brand, dimensions, etc.)
- Gallery images (downloaded locally)

Each product is saved in its own subfolder under `products/`:

```
products/product_0001/
    data.json       ← all product fields as JSON
    images/
        product_0001_1.webp
        product_0001_2.webp
```

**Supports resuming** — if the script is interrupted, it picks up from where it left off using `progress.txt`. Simply re-run the same command.

**Run:**
```bash
python second-phase-product-links-opener.py
```

**Output:** `products/` folder with one subfolder per product.

---

### Phase 3 — Clean and Rebrand JSON Files

**Script:** `process_products.py`

Goes through every `data.json` file produced in Phase 2 and:

1. **Removes** the `"images"` key (external URLs), keeping only `"local_images"` (local paths).
2. **Replaces** all variants of the old store name with the new store name `فروشگاه آنلاین نوژا شاپ`.

Old name variants that are replaced:

| Old variant |
|-------------|
| داروخانه آنلاین مثبت سبز |
| داروخانه انلاین مثبت سبز |
| داروخانه آنلاین |
| داروخانه انلاین |
| مثبت سبز |
| (and zero-width-joiner variants of the above) |

**Run:**
```bash
python process_products.py
```

**Output:** All `data.json` files are updated in-place. A full log is written to `process_log.txt`.

---

### Phase 4 — Analyse Categories

**Script:** `Categories.py`

Reads every `data.json` file, collects all category values, deduplicates them, and produces a ranked report so you can see what categories exist and how many products belong to each one.

**Run:**
```bash
python Categories.py
```

**Output:**

| File | Contents |
|------|----------|
| `categories_report.txt` | Human-readable ranked list + per-category product titles |
| `categories_summary.json` | Machine-readable version of the same data |

Use this report to decide which categories to keep, merge, or add in your new store.

---

## Resuming After Interruption

| Phase | Resume behaviour |
|-------|-----------------|
| Phase 1 | Reruns from scratch — ensure it completes in one run or adjust `TOTAL_PAGES` accordingly |
| Phase 2 | Fully resumable — `progress.txt` tracks the last successfully processed index; just re-run |
| Phase 3 | Safe to re-run — replacements are idempotent (running twice will not break anything) |
| Phase 4 | Safe to re-run at any time |

---

## Notes

- A 1-second delay is added between requests in Phases 1 and 2 to avoid overwhelming the server.
- Phase 2 retries failed image downloads up to 3 times before skipping.
- All JSON files are saved with `ensure_ascii=False` so Persian text is stored as readable Unicode, not escaped sequences.
- Phase 3 sorts replacement variants by length (longest first) to prevent shorter substrings from being replaced before longer, more specific phrases.