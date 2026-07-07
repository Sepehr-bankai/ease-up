# Product Scraping and WooCommerce Synchronization Pipeline

An end-to-end Python pipeline that discovers product pages, extracts structured product data, downloads gallery images, and safely synchronizes the result with WordPress and WooCommerce.

The pipeline is deliberately split into two responsibilities:

- `product_pipeline.py` owns discovery, scraping, image downloads, local persistence, and scrape resume state.
- `api-product-post.py` owns validation, taxonomy resolution, media upload, product synchronization, and upload state.

<p align="center">
  <img src="https://media.giphy.com/media/LmNwrBhejkK9EFP504/giphy.gif" width="420" alt="Developer building an automation pipeline">
</p>

> The scraper converts unstable web pages into a stable local data contract. The uploader consumes that contract without needing to understand the source website.

## What the pipeline does

```text
Product/category URLs
        |
        v
Discover pages and pagination
        |
        v
Extract product fields and gallery URLs
        |
        v
Download images and write products_merged/product_*/data.json
        |
        v
Validate local product data
        |
        v
Resolve WooCommerce categories and attributes
        |
        v
Upload or reuse WordPress media
        |
        v
Create or update WooCommerce products
        |
        v
Persist state in SQLite
```

## Features

### Scraping

- Accepts direct product URLs and category URLs from text or JSON.
- Detects category pagination and generates remaining page URLs.
- Deduplicates discovered product URLs while preserving order.
- Extracts:
  - Persian and English names
  - Regular and sale prices
  - Categories
  - Short and full descriptions
  - Product attributes
  - Full-size gallery image URLs
- Normalizes Persian and Arabic price digits for WooCommerce.
- Downloads images with bounded retries and atomic file replacement.
- Writes one isolated folder per product under `products_merged`.
- Resumes from `.product_scrape_progress.json` after interruption.

### WooCommerce synchronization

- Performs an offline dry run before any server write.
- Validates required fields, prices, image types, and safe local paths.
- Creates or reuses categories, attributes, and attribute terms.
- Uploads images through the WordPress REST API.
- Reuses existing media using SHA-256 hashes and stable slugs.
- Creates or updates products through the WooCommerce REST API.
- Uses the product folder name as a stable SKU.
- Skips unchanged payloads.
- Stores product, media, mapping, failure, and resume state in SQLite.
- Retries temporary network and API failures.
- Creates drafts by default; publishing requires an explicit option.

<p align="center">
  <img src="https://media.giphy.com/media/coxQHKASG60HrHtvkt/giphy.gif" width="420" alt="Automated API synchronization workflow">
</p>

> Synchronization is idempotent: rerunning the uploader should reuse unchanged media and update the same SKU instead of creating duplicates.

## Project structure

| Path | Responsibility |
|---|---|
| `product_pipeline.py` | URL discovery, pagination, extraction, image downloads, product-folder creation, and scrape progress |
| `api-product-post.py` | Validation, WordPress media upload, WooCommerce synchronization, retries, and state management |
| `requirements.txt` | Runtime dependencies |
| `.env.example` | Credential template without real values |
| `.env` | Local credentials; ignored by Git |
| `links.txt` | Default scraper input |
| `.product_scrape_progress.json` | Completed URLs and full-scrape resume state |
| `.product_names_progress.json` | Optional name-only workflow state |
| `product_names.json` | Optional name-only output |
| `products_merged/product_*/data.json` | Structured uploader input |
| `products_merged/product_*/images/` | Downloaded local images |
| `upload_state.sqlite3` | Persistent uploader state |
| `LEARNING.md` | Detailed walkthrough of the actual code |

Generated data and state files do not exist in a fresh checkout.

## Data contract

Each scraped product is stored as:

```text
products_merged/
└── product_00001/
    ├── data.json
    └── images/
        ├── product_00001_1.webp
        └── product_00001_2.webp
```

A representative `data.json` contains:

```json
{
  "title": "نام محصول",
  "english_name": "English Product Name",
  "regular_price": "200000",
  "sale_price": "150000",
  "categories": ["مراقبت پوست"],
  "short_description": "...",
  "description": "...",
  "attributes": {
    "برند": "نمونه"
  },
  "images": [
    "<source-image-url>"
  ],
  "local_images": [
    "images/product_00001_1.webp"
  ],
  "source_url": "<source-product-url>"
}
```

Uploader-required fields are `title`, `english_name`, `regular_price`, and at least one valid `local_images` entry.

## Installation

Python 3.11 or newer is recommended.

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

If `python` resolves to another environment on Windows:

```powershell
py -3.11 -m pip install -r requirements.txt
```

## Configuration

Add your own credentials to `.env`:

```dotenv
SOURCE_SITE_URL=https://your-source-site.example
WOOCOMMERCE_URL=https://your-store.example
WOOCOMMERCE_CONSUMER_KEY=ck_xxxxxxxxxxxxxxxxxxxx
WOOCOMMERCE_CONSUMER_SECRET=cs_xxxxxxxxxxxxxxxxxxxx
WORDPRESS_USER=your_username
WORDPRESS_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
```

Both site URLs are required. They live only in `.env`; neither Python entry point contains a real website address. Scraper input is restricted to the configured source host and its subdomains.

The WordPress account must have permission to upload media. Use a dedicated Application Password, not the account's normal password.

## Running the full scraper

Place one direct product or category URL on each line of `links.txt`:

```text
<product-url>
<category-url>
```

Run the default full scrape:

```powershell
python product_pipeline.py
```

This writes complete products to `products_merged` and resumes through `.product_scrape_progress.json`.

Use another input or output path:

```powershell
python product_pipeline.py scrape my-links.json --output products_merged --workers 4
```

The worker count affects category-page discovery. Product records and their images are written sequentially so each completed URL has a clear persistence boundary.

## Optional name-only workflow

Collect names without creating full product folders:

```powershell
python product_pipeline.py names links.txt --workers 4
```

Merge collected English names into existing product folders:

```powershell
python product_pipeline.py merge-names products products-1 products_merged
```

Uncertain matches are reported in `unmatched_products.json` rather than applied automatically.

## Running the uploader

### 1. Validate locally

No API requests or state changes are made:

```powershell
python api-product-post.py --all
```

Validate one product:

```powershell
python api-product-post.py --product product_00001
```

### 2. Synchronize drafts

```powershell
python api-product-post.py --all --commit --yes
```

### 3. Publish only after review

```powershell
python api-product-post.py --all --commit --yes --publish
```

Useful controls:

| Option | Effect |
|---|---|
| `--limit N` | Process only the first `N` bulk-selected products |
| `--tracked` | Process products already recorded in SQLite |
| `--media-workers N` | Use 1–4 concurrent media workers |
| `--write-delay SECONDS` | Set the minimum delay between API writes |
| `--verify-existing` | Recheck checkpointed remote media and products |
| `--state PATH` | Use another SQLite state file |

## Reliability model

### Scraper

- Input-file SHA-256 prevents reuse of URL discovery state for a different input.
- Product URLs are deduplicated before indexing.
- Required scraped fields are validated before a product is saved.
- Images are written to temporary files before replacement.
- A URL is marked complete only after its images and `data.json` are saved.
- Failed URLs remain pending for the next run.

### Uploader

- Local data is validated before server access.
- HTTP 429, 502, 503, and 504 responses are retried up to five times.
- API writes are rate-limited by a shared lock and configurable delay.
- Media identity uses the local path, file hash, stable slug, and remote ID.
- Product identity uses the folder name as SKU.
- Payload hashes prevent unchanged product writes.
- Failed stages are recorded as `preflight`, `taxonomies`, `media`, or `product`.
- The bulk cursor advances only after a successful product.

## Security

- Never commit `.env`, API keys, or WordPress credentials.
- Use a dedicated WordPress Application Password.
- Give the API user only the permissions required for products and media.
- Never print authorization headers or secrets while debugging.
- Keep the image path containment validation in `load_product`.
- Run a dry run and create drafts before publishing.
- Respect the source website's terms, robots policy, and request limits.

## Limitations

- Extraction depends on the source website's current HTML and CSS selectors.
- English-name fallback detection is heuristic and may need manual review.
- Price normalization assumes integer currency values.
- Gallery downloads are sequential and may be slow for image-heavy catalogs.
- A stable input URL order is important because product indices become SKUs.
- There is no live integration test against a staging WordPress installation.
- External GIFs may not render on networks that block Giphy; all technical information remains available in text.

## Development roadmap

Practical next improvements, if the project needs them:

- Add fixture-based parser tests for multiple real page layouts.
- Record structured scrape failures alongside completed URLs.
- Detect content type from image responses instead of relying on URL suffixes.
- Add a staging integration test for WordPress media and WooCommerce products.
- Export a review report before publication.
- Add Docker support only when reproducible deployment becomes necessary.

## Verification

Run the built-in extraction and merge checks:

```powershell
python product_pipeline.py self-test
```

Compile both entry points:

```powershell
python -m py_compile product_pipeline.py api-product-post.py
```

For a detailed explanation of the implementation, read [`LEARNING.md`](LEARNING.md).
