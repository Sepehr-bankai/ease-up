import requests
from bs4 import BeautifulSoup
import time

# ===================== تنظیمات (اینجا رو پر کن) =====================
BASE_URL = "https://mosbatesabz.com/product-category/beauty-and-personal-care"  # لینک خام صفحه اول سایت، بدون /page/...
TOTAL_PAGES = 2  # تعداد کل صفحاتی که باید پیمایش شوند

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}

# سلکتور کلاس لینک محصول (همونی که گفتی: product-image-link)
PRODUCT_LINK_CLASS = "product-image-link"
# =====================================================================


def get_page_url(page_number):
    """
    صفحه اول = BASE_URL خام
    صفحات بعدی = BASE_URL + /page/{n}/
    """
    if page_number == 1:
        return BASE_URL
    return f"{BASE_URL.rstrip('/')}/page/{page_number}/"


def get_product_links_from_page(html):
    """
    از روی هر صفحه، لینک تمام محصولات (تا 24 محصول بر اساس ساختار سایت) رو استخراج می‌کند.
    """
    soup = BeautifulSoup(html, "html.parser")
    links = []

    # پیدا کردن همه‌ی تگ‌های <a> با کلاس product-image-link
    a_tags = soup.find_all("a", class_=PRODUCT_LINK_CLASS)

    for a in a_tags:
        href = a.get("href")
        if href:
            links.append(href)

    return links


def scrape_all_product_links():
    all_links = []

    for page_num in range(1, TOTAL_PAGES + 1):
        url = get_page_url(page_num)
        print(f"در حال دریافت صفحه {page_num}: {url}")

        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"خطا در دریافت صفحه {page_num}: {e}")
            continue

        page_links = get_product_links_from_page(response.text)
        print(f"  -> {len(page_links)} link got discovered.")

        all_links.extend(page_links)

        # کمی تاخیر برای جلوگیری از بلاک شدن
        time.sleep(1)

    return all_links


if __name__ == "__main__":
    product_links = scrape_all_product_links()

    print(f"\nتعداد کل لینک‌های پیدا شده: {len(product_links)}")

    # ذخیره لینک‌ها در یک فایل متنی برای استفاده در مرحله بعد
    with open("product_links.txt", "w", encoding="utf-8") as f:
        for link in product_links:
            f.write(link + "\n")

    print("لینک‌ها در فایل product_links.txt ذخیره شدند.")