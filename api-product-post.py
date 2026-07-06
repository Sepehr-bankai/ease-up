import argparse
import hashlib
import html
import json
import mimetypes
import os
import re
import sqlite3
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path, PureWindowsPath

import requests


SITE_URL = "https://nojashop.com"
PRODUCTS_DIR = Path("products_merged")
STATE_FILE = Path("upload_state.sqlite3")
ENGLISH_META_KEY = "نام_انگلیسی"
ENGLISH_FIELD_KEY = "field_6a3a3088beb7f"
INVISIBLE_MARKS = str.maketrans("", "", "\u200e\u200f\u200b\ufeff")
SHOW_MORE = "\u0646\u0645\u0627\u06cc\u0634 \u0628\u06cc\u0634\u062a\u0631"
IMAGE_MIMES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class ImportFailure(RuntimeError):
    pass


def load_env(path=Path(".env")):
    if not path.is_file():
        return
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if line.lower().startswith("$env:"):
            line = line[5:]
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            raise ImportFailure(f"Invalid .env entry on line {line_number}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def now():
    return datetime.now(timezone.utc).isoformat()


def clean_text(value):
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", str(value)).translate(INVISIBLE_MARKS)
    lines = [line.strip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line and line != SHOW_MORE)


def normal_key(value):
    text = clean_text(value).replace("\u200c", "").replace("\u064a", "\u06cc").replace("\u0643", "\u06a9")
    return " ".join(text.split()).casefold()


def remove_repeated_blocks(lines):
    lines = list(lines)
    while True:
        found = False
        for size in range(len(lines) // 2, 1, -1):
            for start in range(len(lines) - size * 2 + 1):
                if lines[start : start + size] == lines[start + size : start + size * 2]:
                    del lines[start + size : start + size * 2]
                    found = True
                    break
            if found:
                break
        if not found:
            return lines


def words(value):
    return set(re.findall(r"[\w\u0600-\u06ff]+", normal_key(value)))


def heading_kind(line, title):
    generic = (
        "\u062a\u0631\u06a9\u06cc\u0628\u0627\u062a",
        "\u0631\u0648\u0634 \u0645\u0635\u0631\u0641",
        "\u0646\u062d\u0648\u0647 \u0645\u0635\u0631\u0641",
        "\u0645\u0648\u0627\u0631\u062f \u0645\u0635\u0631\u0641",
        "\u0645\u0634\u062e\u0635\u0627\u062a",
        "\u0647\u0634\u062f\u0627\u0631",
        "\u0634\u0631\u0627\u06cc\u0637 \u0646\u06af\u0647\u062f\u0627\u0631\u06cc",
    )
    if any(normal_key(line).startswith(normal_key(prefix)) for prefix in generic):
        return "heading"
    prefixes = {
        "intro": "\u0645\u0639\u0631\u0641\u06cc ",
        "review": "\u0646\u0642\u062f \u0648 \u0628\u0631\u0631\u0633\u06cc ",
        "price": "\u0642\u06cc\u0645\u062a ",
        "buy": "\u062e\u0631\u06cc\u062f ",
        "features": "\u0648\u06cc\u0698\u06af\u06cc",
    }
    kind = next(
        (kind for kind, prefix in prefixes.items() if normal_key(line).startswith(normal_key(prefix))),
        "purpose" if line.endswith("\u0686\u06cc\u0633\u062a\u061f") else None,
    )
    if not kind:
        return None
    title_words = words(title)
    line_words = words(line) - words("\u0645\u0639\u0631\u0641\u06cc \u0646\u0642\u062f \u0648 \u0628\u0631\u0631\u0633\u06cc \u0642\u06cc\u0645\u062a \u062e\u0631\u06cc\u062f \u0648\u06cc\u0698\u06af\u06cc \u0647\u0627\u06cc \u0686\u06cc\u0633\u062a")
    overlap = len(line_words & title_words) / max(1, len(line_words))
    if overlap < 0.5:
        return None
    return kind


def join_fragments(lines):
    joined = []
    for line in lines:
        if joined and line.startswith(joined[-1] + " "):
            joined[-1] = line
        else:
            joined.append(line)
    return " ".join(joined)


def description_html(value, title=None):
    lines = remove_repeated_blocks(clean_text(value).splitlines())
    if not title:
        return "\n".join(f"<p>{html.escape(line)}</p>" for line in lines)

    output, paragraph, list_items = [], [], []

    def flush():
        if paragraph:
            output.append(f"<p>{html.escape(join_fragments(paragraph))}</p>")
            paragraph.clear()
        if list_items:
            output.append("<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in list_items) + "</ul>")
            list_items.clear()

    in_features = False
    previous_heading = None
    for line in lines:
        kind = heading_kind(line, title)
        if kind == previous_heading and not paragraph and kind not in {"heading", "features"}:
            kind = None
        if kind:
            flush()
            output.append(f"<h2>{html.escape(line)}</h2>")
            in_features = kind == "features"
            previous_heading = kind
        elif in_features:
            list_items.append(line)
        else:
            paragraph.append(line)
    flush()
    return "\n".join(output)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_product(product_dir):
    json_path = product_dir / "data.json"
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportFailure(f"Cannot read {json_path}: {exc}") from exc

    title = clean_text(data.get("title"))
    if not title:
        raise ImportFailure(f"{product_dir.name}: title is empty")
    english_name = clean_text(data.get("english_name"))
    if not english_name:
        raise ImportFailure(f"{product_dir.name}: english_name is empty")

    price = data.get("regular_price")
    try:
        if price is None or Decimal(str(price)) < 0:
            raise InvalidOperation
    except InvalidOperation as exc:
        raise ImportFailure(f"{product_dir.name}: invalid regular_price {price!r}") from exc

    root = product_dir.resolve()
    images = []
    for position, relative in enumerate(data.get("local_images") or [], 1):
        path = product_dir.joinpath(*PureWindowsPath(relative).parts).resolve()
        if root not in path.parents or not path.is_file():
            raise ImportFailure(f"{product_dir.name}: missing or unsafe image path {relative!r}")
        mime = mimetypes.guess_type(path.name)[0] or IMAGE_MIMES.get(path.suffix.lower())
        if not mime or not mime.startswith("image/"):
            raise ImportFailure(f"{product_dir.name}: unsupported image {path.name}")
        images.append({"position": position, "path": path, "mime": mime})
    if not images:
        raise ImportFailure(f"{product_dir.name}: no images")

    data["title"] = title
    data["english_name"] = english_name
    data["regular_price"] = str(price)
    return data, images


class State:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS media (
                product_key TEXT NOT NULL,
                local_path TEXT NOT NULL,
                position INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                media_id INTEGER NOT NULL,
                source_url TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (product_key, local_path)
            );
            CREATE TABLE IF NOT EXISTS products (
                product_key TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mappings (
                kind TEXT NOT NULL,
                scope INTEGER NOT NULL,
                name_key TEXT NOT NULL,
                name TEXT NOT NULL,
                remote_id INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (kind, scope, name_key)
            );
            CREATE TABLE IF NOT EXISTS failures (
                product_key TEXT PRIMARY KEY,
                stage TEXT NOT NULL,
                message TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    def close(self):
        self.db.close()

    def media(self, product_key, path):
        return self.db.execute(
            "SELECT * FROM media WHERE product_key=? AND local_path=?",
            (product_key, str(path)),
        ).fetchone()

    def save_media(self, product_key, item, sha256, media):
        with self.db:
            self.db.execute(
                """
                INSERT INTO media VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(product_key, local_path) DO UPDATE SET
                    position=excluded.position, sha256=excluded.sha256,
                    media_id=excluded.media_id, source_url=excluded.source_url,
                    updated_at=excluded.updated_at
                """,
                (
                    product_key,
                    str(item["path"]),
                    item["position"],
                    sha256,
                    media["id"],
                    media["source_url"],
                    now(),
                ),
            )

    def product(self, product_key):
        return self.db.execute("SELECT * FROM products WHERE product_key=?", (product_key,)).fetchone()

    def save_product(self, product_key, payload_hash, product):
        with self.db:
            self.db.execute(
                """
                INSERT INTO products VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(product_key) DO UPDATE SET
                    payload_hash=excluded.payload_hash, product_id=excluded.product_id,
                    status=excluded.status, updated_at=excluded.updated_at
                """,
                (product_key, payload_hash, product["id"], product["status"], now()),
            )
            self.db.execute("DELETE FROM failures WHERE product_key=?", (product_key,))

    def save_mapping(self, kind, scope, name, remote_id):
        with self.db:
            self.db.execute(
                """
                INSERT INTO mappings VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, scope, name_key) DO UPDATE SET
                    name=excluded.name, remote_id=excluded.remote_id, updated_at=excluded.updated_at
                """,
                (kind, scope, normal_key(name), name, remote_id, now()),
            )

    def mapping(self, kind, scope, name):
        return self.db.execute(
            "SELECT * FROM mappings WHERE kind=? AND scope=? AND name_key=?",
            (kind, scope, normal_key(name)),
        ).fetchone()

    def save_failure(self, product_key, stage, error):
        with self.db:
            self.db.execute(
                """
                INSERT INTO failures VALUES (?, ?, ?, ?)
                ON CONFLICT(product_key) DO UPDATE SET
                    stage=excluded.stage, message=excluded.message, updated_at=excluded.updated_at
                """,
                (product_key, stage, str(error), now()),
            )

    def save_cursor(self, product_key):
        with self.db:
            self.db.execute(
                """INSERT INTO settings VALUES ('all_cursor', ?, ?)
                   ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (product_key, now()),
            )


class SiteAPI:
    RETRY_STATUS = {429, 502, 503, 504}

    def __init__(self, site_url, consumer_key, consumer_secret, wp_user, wp_password, write_delay=2.5):
        self.site_url = site_url.rstrip("/")
        self.woo_auth = (consumer_key, consumer_secret)
        self.wp_auth = (wp_user, wp_password)
        self.write_delay = write_delay
        self.last_write = 0.0
        self.local = threading.local()
        self.write_lock = threading.Lock()

    def session(self):
        if not hasattr(self.local, "session"):
            self.local.session = requests.Session()
            self.local.session.headers["User-Agent"] = "NojaShop importer/1.0"
        return self.local.session

    def request(self, method, path, *, wordpress=False, write=False, **kwargs):
        auth = self.wp_auth if wordpress else self.woo_auth
        url = f"{self.site_url}/wp-json/{'wp/v2' if wordpress else 'wc/v3'}/{path.lstrip('/')}"
        for attempt in range(5):
            if write:
                with self.write_lock:
                    wait = self.write_delay - (time.monotonic() - self.last_write)
                    if wait > 0:
                        time.sleep(wait)
                    self.last_write = time.monotonic()
            try:
                response = self.session().request(method, url, auth=auth, timeout=(10, 90), **kwargs)
            except requests.RequestException as exc:
                if attempt == 4:
                    raise ImportFailure(f"{method} {path} failed: {exc}") from exc
                time.sleep(2**attempt)
                continue
            if response.status_code in self.RETRY_STATUS and attempt < 4:
                delay = response.headers.get("Retry-After")
                time.sleep(float(delay) if delay and delay.isdigit() else 2**attempt)
                continue
            if response.status_code >= 400:
                try:
                    detail = response.json()
                except ValueError:
                    detail = response.text[:500]
                raise ImportFailure(f"{method} {path} returned {response.status_code}: {detail}")
            return response
        raise ImportFailure(f"{method} {path} exhausted retries")

    def list_all(self, path, *, wordpress=False, **params):
        results = []
        page = 1
        while True:
            response = self.request(
                "GET", path, wordpress=wordpress, params={"per_page": 100, "page": page, **params}
            )
            items = response.json()
            results.extend(items)
            pages = int(response.headers.get("X-WP-TotalPages", page))
            if page >= pages or len(items) < 100:
                return results
            page += 1

    def check_permissions(self):
        message = (
            "WordPress credentials cannot upload media. Use an Application Password from a WordPress "
            "user with upload_files permission, and ensure the server forwards the Authorization header."
        )
        self.request("GET", "products", params={"per_page": 1})
        try:
            options = self.request("OPTIONS", "media", wordpress=True).json()
            methods = options.get("methods", []) if isinstance(options, dict) else []
            if "POST" not in methods:
                raise ImportFailure(message)
            self.request("GET", "media", wordpress=True, params={"context": "edit", "per_page": 1})
        except ImportFailure as exc:
            raise ImportFailure(
                message
            ) from exc

    def list_categories(self):
        return self.list_all("products/categories")

    def create_category(self, name):
        return self.request("POST", "products/categories", write=True, json={"name": name}).json()

    def list_attributes(self):
        return self.list_all("products/attributes")

    def create_attribute(self, name):
        slug = "noja-" + hashlib.sha256(normal_key(name).encode("utf-8")).hexdigest()[:12]
        return self.request(
            "POST",
            "products/attributes",
            write=True,
            json={
                "name": name,
                "slug": slug,
                "type": "select",
                "order_by": "menu_order",
                "has_archives": False,
            },
        ).json()

    def list_terms(self, attribute_id):
        return self.list_all(f"products/attributes/{attribute_id}/terms")

    def create_term(self, attribute_id, name):
        return self.request(
            "POST", f"products/attributes/{attribute_id}/terms", write=True, json={"name": name}
        ).json()

    def get_media(self, media_id):
        try:
            return self.request("GET", f"media/{media_id}", wordpress=True, params={"context": "edit"}).json()
        except ImportFailure as exc:
            if "returned 404" in str(exc):
                return None
            raise

    def find_media(self, slug):
        return self.list_all("media", wordpress=True, slug=slug, context="edit")

    def upload_media(self, path, mime, remote_name):
        headers = {"Content-Type": mime, "Content-Disposition": f'attachment; filename="{remote_name}"'}
        return self.request(
            "POST", "media", wordpress=True, write=True, headers=headers, data=path.read_bytes()
        ).json()

    def get_product(self, product_id):
        try:
            return self.request("GET", f"products/{product_id}").json()
        except ImportFailure as exc:
            if "returned 404" in str(exc):
                return None
            raise

    def find_products(self, sku):
        return self.request("GET", "products", params={"sku": sku, "per_page": 100}).json()

    def create_product(self, payload):
        return self.request("POST", "products", write=True, json=payload).json()

    def update_product(self, product_id, payload):
        return self.request("PUT", f"products/{product_id}", write=True, json=payload).json()


class Importer:
    def __init__(self, api, state, status="draft", media_workers=1, verify_existing=False):
        self.api = api
        self.state = state
        self.status = status
        self.media_workers = media_workers
        self.verify_existing = verify_existing
        self.categories = None
        self.attributes = None
        self.terms = {}

    @staticmethod
    def one_match(items, name, kind):
        matches = [item for item in items if normal_key(item["name"]) == normal_key(name)]
        if len(matches) > 1:
            ids = ", ".join(str(item["id"]) for item in matches)
            raise ImportFailure(f"Ambiguous {kind} {name!r}; matching IDs: {ids}")
        return matches[0] if matches else None

    def category_id(self, name):
        name = clean_text(name)
        saved = self.state.mapping("category", 0, name)
        if saved:
            return saved["remote_id"]
        if self.categories is None:
            self.categories = self.api.list_categories()
        category = self.one_match(self.categories, name, "category")
        if category is None:
            category = self.api.create_category(name)
            self.categories.append(category)
        self.state.save_mapping("category", 0, name, category["id"])
        return category["id"]

    def attribute_id(self, name):
        name = clean_text(name)
        saved = self.state.mapping("attribute", 0, name)
        if saved:
            return saved["remote_id"]
        if self.attributes is None:
            self.attributes = self.api.list_attributes()
        matches = [item for item in self.attributes if normal_key(item["name"]) == normal_key(name)]
        if len(matches) > 1:
            candidates = [(item, self.api.list_terms(item["id"])) for item in matches]
            populated = [(item, terms) for item, terms in candidates if terms]
            if len(populated) != 1:
                ids = ", ".join(str(item["id"]) for item in matches)
                raise ImportFailure(f"Ambiguous attribute {name!r}; matching IDs: {ids}")
            attribute, terms = populated[0]
            self.terms[attribute["id"]] = terms
        else:
            attribute = matches[0] if matches else None
        if attribute is None:
            attribute = self.api.create_attribute(name)
            self.attributes.append(attribute)
        self.state.save_mapping("attribute", 0, name, attribute["id"])
        return attribute["id"]

    def term_name(self, attribute_id, value):
        value = clean_text(value)
        saved = self.state.mapping("term", attribute_id, value)
        if saved:
            return saved["name"]
        if attribute_id not in self.terms:
            self.terms[attribute_id] = self.api.list_terms(attribute_id)
        term = self.one_match(self.terms[attribute_id], value, "attribute term")
        if term is None:
            term = self.api.create_term(attribute_id, value)
            self.terms[attribute_id].append(term)
        self.state.save_mapping("term", attribute_id, value, term["id"])
        return term["name"]

    def resolve_taxonomies(self, data):
        categories = [{"id": self.category_id(name)} for name in data.get("categories") or []]
        attributes = []
        for name, value in (data.get("attributes") or {}).items():
            attribute_id = self.attribute_id(name)
            option = self.term_name(attribute_id, value)
            attributes.append({"id": attribute_id, "visible": True, "variation": False, "options": [option]})
        return categories, attributes

    def ensure_media(self, product_key, item, title, sha256, saved):
        if saved and saved["sha256"] == sha256:
            if not self.verify_existing:
                return {"id": saved["media_id"], "alt": title}, None
            remote = self.api.get_media(saved["media_id"])
            if remote:
                return {"id": saved["media_id"], "alt": title}, None

        slug = f"{product_key.replace('_', '-')}-{item['position']:02d}-{sha256[:8]}"
        matches = self.api.find_media(slug)
        if len(matches) > 1:
            raise ImportFailure(f"Multiple media items use slug {slug!r}")
        media = matches[0] if matches else self.api.upload_media(
            item["path"], item["mime"], f"{slug}{item['path'].suffix.lower()}"
        )
        return {"id": media["id"], "alt": title}, media

    def ensure_images(self, product_key, items, title):
        prepared = [
            (item, file_hash(item["path"]), self.state.media(product_key, item["path"])) for item in items
        ]
        images = [None] * len(prepared)
        first_error = None
        with ThreadPoolExecutor(max_workers=self.media_workers) as pool:
            futures = {
                pool.submit(self.ensure_media, product_key, item, title, sha256, saved): (index, item, sha256)
                for index, (item, sha256, saved) in enumerate(prepared)
            }
            for future in as_completed(futures):
                index, item, sha256 = futures[future]
                try:
                    image, media = future.result()
                    if media:
                        self.state.save_media(product_key, item, sha256, media)
                    images[index] = image
                except Exception as exc:
                    first_error = first_error or exc
        if first_error:
            raise first_error
        return images

    def save_product(self, product_key, data, images, categories, attributes):
        saved = self.state.product(product_key)
        target_status = self.status
        if saved and saved["status"] == "publish" and target_status == "draft":
            target_status = "publish"
        payload = {
            "name": data["title"],
            "sku": product_key,
            "type": "simple",
            "status": target_status,
            "regular_price": data["regular_price"],
            "short_description": description_html(data.get("short_description")),
            "description": description_html(data.get("description"), data["title"]),
            "categories": categories,
            "attributes": attributes,
            "images": images,
            "meta_data": [
                {"key": ENGLISH_META_KEY, "value": data["english_name"]},
                {"key": f"_{ENGLISH_META_KEY}", "value": ENGLISH_FIELD_KEY},
            ],
        }
        if data.get("sale_price") not in (None, ""):
            payload["sale_price"] = str(data["sale_price"])
        def digest():
            return hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest()

        payload_hash = digest()

        if saved:
            if (
                not self.verify_existing
                and saved["payload_hash"] == payload_hash
                and saved["status"] == payload["status"]
            ):
                return {"id": saved["product_id"], "status": saved["status"], "sku": product_key}, "unchanged"
            remote = self.api.get_product(saved["product_id"])
            if remote and remote.get("sku") == product_key:
                if remote.get("status") == "publish" and payload["status"] == "draft":
                    payload["status"] = "publish"
                    payload_hash = digest()
                if saved["payload_hash"] == payload_hash and remote.get("status") == payload["status"]:
                    return remote, "unchanged"
                self.set_meta_ids(payload, remote)
                product = self.api.update_product(remote["id"], payload)
                self.state.save_product(product_key, payload_hash, product)
                return product, "updated"

        matches = self.api.find_products(product_key)
        if len(matches) > 1:
            raise ImportFailure(f"Multiple WooCommerce products use SKU {product_key!r}")
        if matches:
            if matches[0].get("status") not in {"draft", payload["status"]}:
                raise ImportFailure(f"Refusing to adopt non-draft product with SKU {product_key!r}")
            self.set_meta_ids(payload, matches[0])
            product = self.api.update_product(matches[0]["id"], payload)
            action = "recovered"
        else:
            product = self.api.create_product(payload)
            action = "created"
        self.state.save_product(product_key, payload_hash, product)
        return product, action

    @staticmethod
    def set_meta_ids(payload, remote):
        remote_meta = remote.get("meta_data", [])
        for item in payload["meta_data"]:
            matches = [saved for saved in remote_meta if saved.get("key") == item["key"]]
            if len(matches) > 1:
                raise ImportFailure(f"Product {remote['id']} has duplicate {item['key']} metadata")
            if matches:
                item["id"] = matches[0]["id"]
        payload["meta_data"].extend(
            {"id": item["id"], "key": "english_name", "value": None}
            for item in remote_meta if item.get("key") == "english_name"
        )

    def run(self, product_dir):
        product_key = product_dir.name
        stage = "preflight"
        try:
            data, image_items = load_product(product_dir)
            stage = "taxonomies"
            categories, attributes = self.resolve_taxonomies(data)
            stage = "media"
            images = self.ensure_images(product_key, image_items, data["title"])
            stage = "product"
            return self.save_product(product_key, data, images, categories, attributes)
        except Exception as exc:
            self.state.save_failure(product_key, stage, exc)
            raise


def select_products(args):
    if args.products:
        paths = [PRODUCTS_DIR / name.replace("product-", "product_", 1) for name in args.products]
    elif args.tracked:
        if not args.state.is_file():
            raise ImportFailure(f"State database not found: {args.state}")
        database = sqlite3.connect(args.state)
        try:
            names = [row[0] for row in database.execute("SELECT product_key FROM products ORDER BY product_key")]
        finally:
            database.close()
        paths = [PRODUCTS_DIR / name for name in names]
        if args.limit:
            paths = paths[: args.limit]
    else:
        paths = sorted(path for path in PRODUCTS_DIR.glob("product_*") if path.is_dir())
        if args.commit:
            cursor = None
            if args.state.is_file():
                database = sqlite3.connect(args.state)
                try:
                    cursor = database.execute(
                        "SELECT value FROM settings WHERE name='all_cursor'"
                    ).fetchone()
                except sqlite3.OperationalError:
                    pass
                finally:
                    database.close()
            if not cursor:
                answer = input("Last product number already processed [0]: ").strip() or "0"
                answer = answer.replace("product-", "").replace("product_", "")
                if not answer.isdigit() or int(answer) > len(paths):
                    raise ImportFailure(f"Enter a number from 0 to {len(paths)}")
                cursor = (f"product_{int(answer):05d}",)
                state = State(args.state)
                state.save_cursor(cursor[0])
                state.close()
            paths = [path for path in paths if path.name > cursor[0]]
            print(f"Resuming after {cursor[0]}: {len(paths)} product(s) remaining")
        if args.limit:
            paths = paths[: args.limit]
    missing = [str(path) for path in paths if not path.is_dir()]
    if missing:
        raise ImportFailure(f"Product folders not found: {', '.join(missing)}")
    return paths


def dry_run(paths):
    for path in paths:
        data, images = load_product(path)
        print(
            f"[DRY RUN] {path.name}: {len(images)} images, "
            f"{len(data.get('categories') or [])} categories, "
            f"{len(data.get('attributes') or {})} attributes — {data['title']}"
        )
    print(f"Validated {len(paths)} product(s). No network requests or state changes were made.")


def parser():
    result = argparse.ArgumentParser(description="Safely import local products into WooCommerce.")
    selection = result.add_mutually_exclusive_group(required=True)
    selection.add_argument("--product", dest="products", action="append", help="Product folder name; repeatable")
    selection.add_argument("--all", action="store_true", help="Process every product folder")
    selection.add_argument("--tracked", action="store_true", help="Process only products recorded in SQLite")
    result.add_argument("--limit", type=int, help="Limit --all or --tracked to the first N products")
    result.add_argument("--commit", action="store_true", help="Allow writes to WordPress and WooCommerce")
    result.add_argument("--publish", action="store_true", help="Publish imported products instead of drafting them")
    result.add_argument("--yes", action="store_true", help="Confirm a bulk --all commit")
    result.add_argument("--site", default=os.getenv("NOJASHOP_URL", SITE_URL))
    result.add_argument("--state", type=Path, default=STATE_FILE)
    result.add_argument("--write-delay", type=float, default=1.0, help="Minimum seconds between API writes")
    result.add_argument("--media-workers", type=int, default=2, help="Concurrent media checks/uploads (1-4)")
    result.add_argument("--verify-existing", action="store_true", help="Recheck checkpointed media and products")
    return result


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        load_env()
    except ImportFailure as exc:
        print(f"ERROR: {exc}")
        return 1
    args = parser().parse_args(argv)
    if args.limit is not None and (not (args.all or args.tracked) or args.limit < 1):
        parser().error("--limit requires --all or --tracked and a positive value")
    if args.commit and (args.all or args.tracked) and not args.yes:
        parser().error("bulk writes require --yes")
    if args.publish and not args.commit:
        parser().error("--publish requires --commit")
    if not 1 <= args.media_workers <= 4:
        parser().error("--media-workers must be between 1 and 4")

    try:
        paths = select_products(args)
        if not args.commit:
            dry_run(paths)
            return 0

        names = (
            "WOOCOMMERCE_CONSUMER_KEY",
            "WOOCOMMERCE_CONSUMER_SECRET",
            "WORDPRESS_USER",
            "WORDPRESS_APP_PASSWORD",
        )
        missing = [name for name in names if not os.getenv(name)]
        if missing:
            raise ImportFailure(f"Missing environment variables: {', '.join(missing)}")
        app_password = "".join(os.environ["WORDPRESS_APP_PASSWORD"].split())
        if len(app_password) != 24 or not app_password.isalnum():
            raise ImportFailure(
                "WORDPRESS_APP_PASSWORD is not a native WordPress Application Password. "
                "Generate one under Users → Profile → Application Passwords; do not use the normal login password."
            )

        state = State(args.state)
        try:
            api = SiteAPI(
                args.site,
                os.environ[names[0]],
                os.environ[names[1]],
                os.environ[names[2]],
                app_password,
                args.write_delay,
            )
            api.check_permissions()
            print("Credential and media permission check passed.")
            importer = Importer(
                api,
                state,
                "publish" if args.publish else "draft",
                args.media_workers,
                args.verify_existing,
            )
            failures = 0
            for index, path in enumerate(paths, 1):
                try:
                    product, action = importer.run(path)
                    print(f"[{index}/{len(paths)}] {path.name}: {action}, WooCommerce ID {product['id']}")
                    if args.all:
                        state.save_cursor(path.name)
                except Exception as exc:
                    failures += 1
                    print(f"[{index}/{len(paths)}] {path.name}: ERROR: {exc}")
            print(f"Finished: {len(paths) - failures} succeeded, {failures} failed. State: {args.state}")
            return 1 if failures else 0
        finally:
            state.close()
    except ImportFailure as exc:
        print(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
