"""Product scraping pipeline.

Put one URL per line in ``links.txt`` beside this script. JSON is also accepted
and may contain direct product URLs, category URLs, or both::

    [
      {"url": "<category-url>", "pages": 2},
      "<product-url>"
    ]

Commands:
    python product_pipeline.py        # writes complete products to products_merged
    python product_pipeline.py names  # writes product_names.json
    python product_pipeline.py merge-names products products-1 products_merged
"""

import argparse
import difflib
import hashlib
import json
import os
import re
import tempfile
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup


HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"}
LINKS_FILE = Path("links.txt")
PROGRESS_FILE = Path(".product_names_progress.json")
SCRAPE_PROGRESS_FILE = Path(".product_scrape_progress.json")
PRODUCTS_DIR = Path("products_merged")
SOURCE_HOST = None


def load_env(path=Path(".env")):
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("$env:"):
            line = line[5:]
        key, separator, value = line.partition("=")
        if separator:
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def configure_source():
    global SOURCE_HOST
    load_env()
    source = os.getenv("SOURCE_SITE_URL", "").rstrip("/")
    if urlparse(source).scheme not in {"http", "https"} or not urlparse(source).netloc:
        raise ValueError("SOURCE_SITE_URL must be set to a valid HTTP URL in .env")
    SOURCE_HOST = urlparse(source).hostname
    HEADERS["Referer"] = f"{source}/"


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


def normalize_name(value):
    value = unicodedata.normalize("NFKC", value or "")
    value = value.translate(str.maketrans("يىكۀةؤإأ", "ییکههواا"))
    value = value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    return re.sub(r"[^0-9a-z\u0600-\u06ff]+", " ", value.lower()).strip()


def save_json(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for attempt in range(20):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(.1)


def load_inputs(path):
    content = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".txt":
        data = [line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    else:
        data = json.loads(content)
    if isinstance(data, dict):
        data = data.get("links", data.get("urls"))
    if not isinstance(data, list):
        raise ValueError("links.json must be a JSON list, or an object with a 'links' key")

    entries = []
    for item in data:
        item = {"url": item} if isinstance(item, str) else item
        if not isinstance(item, dict) or not isinstance(item.get("url") or item.get("link"), str):
            raise ValueError(f"Invalid link entry: {item!r}")
        url = (item.get("url") or item["link"]).strip()
        if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
            raise ValueError(f"Invalid HTTP URL: {url}")
        host = urlparse(url).hostname
        if SOURCE_HOST and host != SOURCE_HOST and not host.endswith(f".{SOURCE_HOST}"):
            raise ValueError(f"URL is outside SOURCE_SITE_URL: {url}")
        pages = item.get("pages")
        entries.append({"url": url, "pages": max(1, int(pages)) if pages is not None else None})
    return entries


def get(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def category_page(url):
    print(f"Discovering: {url}")
    soup = BeautifulSoup(get(url), "html.parser")
    links = [a["href"] for a in soup.select("a.product-image-link[href]")]
    page_numbers = [int(tag.get_text(strip=True)) for tag in soup.select(".page-numbers") if tag.get_text(strip=True).isdigit()]
    return links, max(page_numbers, default=1)


def discover_product_urls(entries, workers):
    urls = [entry["url"] for entry in entries if "/product/" in entry["url"]]
    categories = [entry for entry in entries if "/product/" not in entry["url"]]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        first_pages = list(pool.map(category_page, [entry["url"] for entry in categories]))
        remaining = []
        for entry, (links, detected_pages) in zip(categories, first_pages):
            urls.extend(links)
            total_pages = entry["pages"] or detected_pages
            remaining.extend(f"{entry['url'].rstrip('/')}/page/{page}/" for page in range(2, total_pages + 1))
        for links, _ in pool.map(category_page, remaining):
            urls.extend(links)
    return list(dict.fromkeys(urls))


def english_name(soup):
    for selector in (".product-en-title", ".product-english-title", ".english-title", "[class*='english-name']"):
        if tag := soup.select_one(selector):
            return clean_text(tag.get_text(" ", strip=True)) or None

    title = soup.select_one("h1.product_title, h1.entry-title")
    if not title:
        return None
    # ponytail: nearby Latin text is the site's current layout fallback; add a selector above if it changes.
    blocked = {"add to wishlist", "compare"}
    for text_node in title.find_all_next(string=True, limit=20):
        text = clean_text(str(text_node))
        latin_words = re.findall(r"[A-Za-z][A-Za-z0-9+.-]*", text)
        if len(latin_words) >= 2 and text.lower() not in blocked:
            return text
    return None


def product_names(url):
    soup = BeautifulSoup(get(url), "html.parser")
    title = soup.select_one("h1.product_title, h1.entry-title")
    return {
        "farsi_name": clean_text(title.get_text(" ", strip=True)) if title else None,
        "english_name": english_name(soup),
    }


def price_value(value):
    if not value:
        return None
    value = value.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
    digits = re.sub(r"[^0-9]", "", value)
    return digits or None


def product_data(soup):
    price = soup.select_one(".wd-single-price .price, p.price")
    regular = price.select_one("del") if price else None
    sale = price.select_one("ins") if price else None
    amount = price.select_one(".woocommerce-Price-amount") if price else None
    regular_price = price_value((regular or amount).get_text(" ", strip=True)) if regular or amount else None
    sale_price = price_value(sale.get_text(" ", strip=True)) if sale else None
    short = soup.select_one(".woocommerce-product-details__short-description")
    description = soup.select_one("#tab-content-description, #tab-description")
    attributes = {}
    for row in soup.select("table.shop_attributes tr"):
        name, value = row.select_one("th"), row.select_one("td")
        if name and value:
            attributes[clean_text(name.get_text(" ", strip=True))] = clean_text(value.get_text(" ", strip=True))
    images = []
    for tag in soup.select(".woocommerce-product-gallery [data-large_image], .woocommerce-product-gallery a[href]"):
        image_url = tag.get("data-large_image") or tag.get("href")
        if image_url and image_url.startswith(("http://", "https://")):
            images.append(image_url)
    title = soup.select_one("h1.product_title, h1.entry-title")
    return {
        "title": clean_text(title.get_text(" ", strip=True)) if title else None,
        "english_name": english_name(soup),
        "regular_price": regular_price,
        "sale_price": sale_price,
        "categories": [clean_text(tag.get_text(" ", strip=True)) for tag in soup.select(".posted_in a")],
        "short_description": clean_text(short.get_text("\n", strip=True)) if short else None,
        "description": clean_text(description.get_text("\n", strip=True)) if description else None,
        "attributes": attributes,
        "images": list(dict.fromkeys(images)),
    }


def download_image(url, path):
    for attempt in range(3):
        try:
            response = requests.get(url, headers=HEADERS, timeout=30)
            response.raise_for_status()
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(response.content)
            temporary.replace(path)
            return
        except requests.RequestException:
            if attempt == 2:
                raise
            time.sleep(2**attempt)


def scrape_product(url, index, output):
    soup = BeautifulSoup(get(url), "html.parser")
    data = product_data(soup)
    missing = [key for key in ("title", "english_name", "regular_price", "images") if not data[key]]
    if missing:
        raise ValueError(f"Missing {', '.join(missing)} at {url}")
    product_dir = output / f"product_{index:05d}"
    images_dir = product_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    local_images = []
    for position, image_url in enumerate(data["images"], 1):
        suffix = Path(urlparse(image_url).path).suffix.lower()
        if suffix not in {".gif", ".jpeg", ".jpg", ".png", ".webp"}:
            suffix = ".jpg"
        image_path = images_dir / f"product_{index:05d}_{position}{suffix}"
        download_image(image_url, image_path)
        local_images.append(image_path.relative_to(product_dir).as_posix())
    data["local_images"] = local_images
    data["source_url"] = url
    save_json(product_dir / "data.json", data)


def scrape_products(urls, output=PRODUCTS_DIR, progress=SCRAPE_PROGRESS_FILE):
    state = json.loads(progress.read_text(encoding="utf-8")) if progress.exists() else {}
    done = set(state.get("completed_urls", []))
    failures = 0
    for index, url in enumerate(urls, 1):
        if url in done:
            continue
        try:
            print(f"[{index}/{len(urls)}] Scraping {url}")
            scrape_product(url, index, output)
        except (OSError, ValueError, requests.RequestException) as error:
            failures += 1
            print(f"  failed: {error}")
            continue
        done.add(url)
        save_json(progress, {**state, "product_urls": urls, "completed_urls": list(done)})
    print(f"Saved {len(done)} product(s) to {output}; {failures} failed")
    return failures


def fetch_name(url):
    try:
        return product_names(url), None
    except requests.RequestException as error:
        return None, error


def export_names(urls, workers, state, output=Path("product_names.json"), progress=PROGRESS_FILE):
    saved = json.loads(output.read_text(encoding="utf-8-sig")) if output.exists() else []
    names = [{key: item.get(key) for key in ("farsi_name", "english_name")} for item in saved]
    completed = {(item.get("farsi_name"), item.get("english_name")) for item in names}
    done_urls = set(state.get("completed_urls", []))
    pending = [url for url in urls if url not in done_urls]
    print(f"Resuming: {len(done_urls)}/{len(urls)} completed")
    pool = ThreadPoolExecutor(max_workers=workers)
    futures = []
    try:
        for start in range(0, len(pending), workers):
            futures = [pool.submit(fetch_name, url) for url in pending[start:start + workers]]
            for future in as_completed(futures):
                url = pending[start + futures.index(future)]
                item, error = future.result()
                print(f"[{len(done_urls) + 1}/{len(urls)}] name: {url}")
                if error:
                    print(f"  failed: {error}")
                    continue
                if tuple(item.values()) not in completed:
                    names.append(item)
                    completed.add(tuple(item.values()))
                done_urls.add(url)
            state["completed_urls"] = list(done_urls)
            save_json(output, names)
            save_json(progress, state)
    except KeyboardInterrupt:
        for future in futures:
            future.cancel()
        print("\nPaused safely. Run the same command to resume.")
        return
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    save_json(output, names)
    print(f"Saved {len(names)} names to {output}")


def merge_names(roots, names_file=Path("product_names.json"), report=Path("unmatched_products.json")):
    names = json.loads(names_file.read_text(encoding="utf-8-sig"))
    source = [item for item in names if item.get("farsi_name") and item.get("english_name")]
    exact = {item["farsi_name"]: item["english_name"] for item in source}
    buckets = defaultdict(set)
    for item in source:
        buckets[normalize_name(item["farsi_name"])].add(item["english_name"])
    normalized = {key: next(iter(values)) for key, values in buckets.items() if len(values) == 1}
    candidates = [(normalize_name(item["farsi_name"]), item) for item in source]
    updated = already = exact_count = normalized_count = 0
    unmatched = []
    for root in roots:
        for path in Path(root).glob("product_*/*.json"):
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if data.get("english_name"):
                already += 1
                continue
            title = data.get("title")
            english = exact.get(title)
            if english:
                exact_count += 1
            elif english := normalized.get(normalize_name(title)):
                normalized_count += 1
            else:
                target = normalize_name(title)
                suggestions = sorted(
                    ((difflib.SequenceMatcher(None, target, candidate).ratio(), item) for candidate, item in candidates),
                    key=lambda match: match[0], reverse=True,
                )[:3] if title else []
                unmatched.append({
                    "file": str(path),
                    "farsi_name": title,
                    "suggestions": [
                        {"score": round(score, 3), "farsi_name": item["farsi_name"], "english_name": item["english_name"]}
                        for score, item in suggestions
                    ],
                })
                continue
            data["english_name"] = english
            save_json(path, data)
            updated += 1
    save_json(report, unmatched)
    print(f"Updated {updated}: exact={exact_count}, normalized={normalized_count}; already={already}; unmatched={len(unmatched)}")
    print(f"Unmatched report: {report}")


def self_test():
    assert normalize_name("محصول كیفی ۱۰") == normalize_name("محصول کیفی 10")
    soup = BeautifulSoup('<h1 class="product_title">نام فارسی</h1><p class="product-en-title">Test Product 20ml</p>', "html.parser")
    assert english_name(soup) == "Test Product 20ml"
    soup = BeautifulSoup('<h1 class="product_title">نام فارسی</h1><div>Fallback Product Name 10ml</div>', "html.parser")
    assert english_name(soup) == "Fallback Product Name 10ml"
    soup = BeautifulSoup(
        '<h1 class="product_title">نام محصول</h1><p class="product-en-title">Test Product</p>'
        '<p class="price"><del><span class="woocommerce-Price-amount">۲۰۰,۰۰۰ تومان</span></del>'
        '<ins><span class="woocommerce-Price-amount">۱۵۰,۰۰۰ تومان</span></ins></p>'
        '<span class="posted_in"><a>مراقبت پوست</a></span>'
        '<table class="shop_attributes"><tr><th>برند</th><td>تست</td></tr></table>'
        '<div class="woocommerce-product-gallery"><a href="https://test.invalid/a.webp"></a></div>',
        "html.parser",
    )
    extracted = product_data(soup)
    assert extracted["regular_price"] == "200000" and extracted["sale_price"] == "150000"
    assert extracted["attributes"] == {"برند": "تست"} and len(extracted["images"]) == 1
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        links = root / "links.json"
        links.write_text(json.dumps({"links": ["https://test.invalid/product/test/"]}), encoding="utf-8")
        assert load_inputs(links)[0]["url"].endswith("/product/test/")
        text_links = root / "links.txt"
        text_links.write_text("# comment\nhttps://test.invalid/product/test/\n", encoding="utf-8")
        assert len(load_inputs(text_links)) == 1

        product_dir = root / "products" / "product_00001"
        product_dir.mkdir(parents=True)
        data_path = product_dir / "data.json"
        data_path.write_text(json.dumps({"title": "نام فارسی", "keep": 1}), encoding="utf-8")
        names_path = root / "product_names.json"
        names_path.write_text(json.dumps([{"farsi_name": "نام فارسی", "english_name": "Test Product"}]), encoding="utf-8")
        merge_names([root / "products"], names_path, root / "unmatched.json")
        merged = json.loads(data_path.read_text(encoding="utf-8"))
        assert merged == {"title": "نام فارسی", "keep": 1, "english_name": "Test Product"}

        output = root / "names.json"
        save_json(output, [{"farsi_name": "نام فارسی", "english_name": "Test Product"}])
        state = {"completed_urls": ["https://test.invalid/product/test/"]}
        export_names(state["completed_urls"], 2, state, output, root / "progress.json")
        assert len(json.loads(output.read_text(encoding="utf-8"))) == 1
    print("Self-test passed")


def worker_count(value):
    value = int(value)
    if not 1 <= value <= 10:
        raise argparse.ArgumentTypeError("workers must be between 1 and 10")
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="scrape", choices=("scrape", "names", "merge-names", "self-test"))
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--workers", type=worker_count, default=5)
    parser.add_argument("--output", type=Path, default=PRODUCTS_DIR)
    args = parser.parse_args()
    if args.command == "self-test":
        self_test()
    elif args.command == "merge-names":
        merge_names(args.paths or ["products"])
    else:
        configure_source()
        links_path = Path(args.paths[0]) if args.paths else LINKS_FILE
        input_hash = hashlib.sha256(links_path.read_bytes()).hexdigest()
        progress = PROGRESS_FILE if args.command == "names" else SCRAPE_PROGRESS_FILE
        state = json.loads(progress.read_text(encoding="utf-8")) if progress.exists() else {}
        if state.get("input_hash") == input_hash and state.get("product_urls"):
            urls = state["product_urls"]
            print(f"Loaded {len(urls)} cached product URLs")
        else:
            urls = discover_product_urls(load_inputs(links_path), args.workers)
            state = {"input_hash": input_hash, "product_urls": urls, "completed_urls": []}
            save_json(progress, state)
        print(f"Found {len(urls)} unique product URLs")
        if args.command == "names":
            export_names(urls, args.workers, state)
        else:
            scrape_products(urls, args.output, progress)


if __name__ == "__main__":
    main()
