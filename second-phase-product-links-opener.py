import requests
from bs4 import BeautifulSoup
import json
import os
import re
import time

# ===================== تنظیمات =====================
LINKS_FILE = "product_links.txt"   # فایل لینک‌های مرحله قبل
OUTPUT_DIR = "products"            # پوشه خروجی (یک پوشه برای هر محصول)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://mosbatesabz.com/",
    "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
}

PROGRESS_FILE = "progress.txt"  # برای ادامه از همان نقطه قبلی
# =====================================================


def clean_text(text):
    """حذف فاصله‌های اضافه و خطوط خالی"""
    text = re.sub(r'\n\s*\n+', '\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def extract_price(soup):
    """
    قیمت اصلی و قیمت با تخفیف رو برمی‌گردونه.
    اگر تخفیف نداشته باشه، regular_price و sale_price یکسان هستند.
    """
    price_div = soup.find('div', class_='wd-single-price')
    regular_price = None
    sale_price = None

    if price_div:
        p_tag = price_div.find('p', class_='price')
        if p_tag:
            del_tag = p_tag.find('del')
            ins_tag = p_tag.find('ins')

            if del_tag and ins_tag:
                # محصول تخفیف دارد
                regular_price = del_tag.get_text(strip=True)
                sale_price = ins_tag.get_text(strip=True)
            else:
                # تخفیف ندارد - فقط یک قیمت
                amount = p_tag.find('span', class_='woocommerce-Price-amount')
                if amount:
                    regular_price = amount.get_text(strip=True)
                    sale_price = regular_price

    # پاکسازی اعداد (فقط ارقام و کاما)
    def clean_price(p):
        if not p:
            return None
        digits = re.sub(r'[^\d,]', '', p)
        return digits.replace(',', '')

    return clean_price(regular_price), clean_price(sale_price)


def extract_title(soup):
    h1 = soup.find('h1', class_='product_title')
    return h1.get_text(strip=True) if h1 else None


def extract_short_description(soup):
    sd = soup.find('div', class_='woocommerce-product-details__short-description')
    if not sd:
        return None
    return clean_text(sd.get_text('\n', strip=True))


def extract_full_description(soup):
    desc = soup.find('div', id='tab-content-description')
    if not desc:
        return None
    return clean_text(desc.get_text('\n', strip=True))


def extract_sku(soup):
    sku_span = soup.find('span', class_='sku')
    return sku_span.get_text(strip=True) if sku_span else None


def extract_category(soup):
    posted_in = soup.find('span', class_='posted_in')
    if not posted_in:
        return []
    categories = []
    for a in posted_in.find_all('a'):
        categories.append(a.get_text(strip=True))
    return categories


def extract_attributes(soup):
    """جدول مشخصات محصول رو به صورت دیکشنری برمی‌گرداند"""
    attributes = {}
    table = soup.find('table', class_='shop_attributes')
    if not table:
        return attributes

    for tr in table.find_all('tr'):
        th = tr.find('th')
        td = tr.find('td')
        if th and td:
            key = clean_text(th.get_text(strip=True))
            value = clean_text(td.get_text(' ', strip=True))
            attributes[key] = value

    return attributes


def extract_gallery_images(soup):
    """لیست لینک عکس‌های اصلی (سایز کامل) محصول"""
    images = []
    
    # روش 1: سعی کنید از gallery div استاندارد WooCommerce استفاده کنید
    gallery = soup.find('div', class_='woocommerce-product-gallery')
    if gallery:
        for a in gallery.find_all('a', class_='woocommerce-product-gallery__image'):
            href = a.get('href')
            if href and href not in images:
                images.append(href)
    
    # روش 2: اگر روش 1 کار نکرد، در figure تگ‌ها جستجو کنید
    if not images:
        figures = soup.find_all('figure')
        for figure in figures:
            # بررسی برای تگ‌های img درون figure
            img_tags = figure.find_all('img')
            for img in img_tags:
                img_src = img.get('src') or img.get('data-src')
                if img_src and img_src not in images:
                    # تمام URL‌های معتبر را اضافه کنید
                    if img_src.startswith('http'):
                        images.append(img_src)
            
            # بررسی برای تگ‌های a درون figure (لینک تصاویر)
            img_links = figure.find_all('a', href=True)
            for link in img_links:
                href = link.get('href')
                if href and href.startswith('http') and href not in images:
                    images.append(href)
    
    # روش 3: به دنبال تصاویر بزرگ‌تر در صفحه (اگر روش‌های قبلی کافی نباشد)
    if not images:
        # تمام img تگ‌ها را جستجو کنید و فیلتر کنید
        all_imgs = soup.find_all('img', src=re.compile(r'\.(webp|jpg|jpeg|png)'))
        for img in all_imgs:
            src = img.get('src', '')
            # تصاویر کوچک را حذف کنید (مثل آیکن‌ها و لوگو‌ها)
            if src and 'mosbatesabz.com' in src and '-150x' not in src and src not in images:
                images.append(src)

    return images


def download_image(url, save_path):
    """دانلود تصویر و ذخیره در مسیر مشخص"""
    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            with open(save_path, 'wb') as f:
                f.write(resp.content)
            return True
        except requests.RequestException as e:
            print(f"    خطا در دانلود عکس (تلاش {attempt+1}/3) {url}: {e}")
            time.sleep(1)
    return False


def extract_product_data(url):
    """تمام اطلاعات یک صفحه محصول رو استخراج می‌کند"""
    response = requests.get(url, headers=HEADERS, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    data = {
        "title": extract_title(soup),
        "regular_price": None,
        "sale_price": None,
        "categories": extract_category(soup),
        "short_description": extract_short_description(soup),
        "description": extract_full_description(soup),
        "attributes": extract_attributes(soup),
        "images": extract_gallery_images(soup),
    }

    data["regular_price"], data["sale_price"] = extract_price(soup)

    return data


def slugify(text):
    """تبدیل متن به اسم فایل/پوشه مناسب"""
    text = re.sub(r'[^\w\-]', '_', text)
    text = re.sub(r'_+', '_', text)
    return text.strip('_')[:80]


def process_product(url, index):
    print(f"[{index}] در حال دریافت: {url}")

    try:
        data = extract_product_data(url)
    except requests.RequestException as e:
        print(f"    خطا: {e}")
        return False

    # ساخت اسم پوشه بر اساس شماره محصول
    folder_name = f"product_{index:04d}"
    product_dir = os.path.join(OUTPUT_DIR, folder_name)
    images_dir = os.path.join(product_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    # دانلود عکس‌ها
    local_images = []
    for i, img_url in enumerate(data["images"], start=1):
        ext = os.path.splitext(img_url)[1].split('?')[0]  # مثلا .webp
        if not ext or len(ext) > 6:
            ext = ".jpg"

        filename = f"{folder_name}_{i}{ext}"
        save_path = os.path.join(images_dir, filename)

        print(f"    دانلود عکس {i}/{len(data['images'])}: {filename}")
        if download_image(img_url, save_path):
            local_images.append(os.path.join("images", filename))

        time.sleep(0.3)

    data["local_images"] = local_images

    # ذخیره JSON اطلاعات محصول
    json_path = os.path.join(product_dir, "data.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"    ذخیره شد در: {product_dir}")
    print()
    return True


def load_progress():
    """آخرین ایندکس پردازش شده رو می‌خواند (0 یعنی هنوز شروع نشده)"""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if content.isdigit():
                return int(content)
    return 0


def save_progress(index):
    """ایندکس فعلی رو ذخیره می‌کند تا در صورت قطع شدن، از همینجا ادامه پیدا کند"""
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        f.write(str(index))


def main():
    if not os.path.exists(LINKS_FILE):
        print(f"فایل {LINKS_FILE} پیدا نشد. اول مرحله اول رو اجرا کن.")
        return

    with open(LINKS_FILE, "r", encoding="utf-8") as f:
        links = [line.strip() for line in f if line.strip()]

    print(f"تعداد کل محصولات: {len(links)}")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    last_done = load_progress()
    if last_done > 0:
        print(f"ادامه از محصول شماره {last_done + 1} (محصولات قبلی قبلاً پردازش شده‌اند)\n")
    else:
        print()

    for i, link in enumerate(links, start=1):
        if i <= last_done:
            continue  # این محصول قبلاً پردازش شده، رد شو

        success = process_product(link, i)

        # فقط در صورت موفقیت، پیشرفت رو ذخیره کن
        if success:
            save_progress(i)

        time.sleep(1)

    print("پردازش تمام محصولات به پایان رسید.")


if __name__ == "__main__":
    main()
