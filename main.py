import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq
from playwright.sync_api import sync_playwright

# إعدادات تليجرام ونسبة الخصم المطلوبة
BOT_TOKEN = os.getenv("BOT_TOKEN", "PUT_YOUR_TOKEN_HERE")
CHAT_ID = os.getenv("CHAT_ID", "PUT_YOUR_CHAT_ID_HERE")
MIN_DISCOUNT = 40  # أقل نسبة خصم مطلوبة (40%)

SEEN_FILE = "seen_deals.json"

# روابط صفحات العروض والأقسام (تقدر تزود أي قسم تحبه هنا)
SCAN_URLS = {
    "jumia": [
        "https://www.jumia.com.eg/ar/flash-sales/",
        "https://www.jumia.com.eg/ar/electronics/",
        "https://www.jumia.com.eg/ar/phones-tablets/",
        "https://www.jumia.com.eg/ar/computing/"
    ],
    "2b": [
        "https://2b.com.eg/ar/deals.html",
        "https://2b.com.eg/ar/computers.html",
        "https://2b.com.eg/ar/mobile-and-tablet.html",
        "https://2b.com.eg/ar/home-appliances.html"
    ],
    "noon": [
        "https://www.noon.com/egypt-ar/electronics/",
        "https://www.noon.com/egypt-ar/eg-mega-deals/"
    ],
    "amazon": [
        "https://www.amazon.eg/s?i=electronics&rh=p_n_deal_type%3A26901100031&language=ar_AE",
        "https://www.amazon.eg/s?i=computers&rh=p_n_deal_type%3A26901100031&language=ar_AE"
    ]
}

HEADERS = {
    "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

def clean_num(text):
    if not text:
        return 0.0
    arabic_map = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    text = str(text).translate(arabic_map).replace(",", "")
    match = re.search(r"\d+(\.\d+)?", text)
    return float(match.group(0)) if match else 0.0

def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_seen(seen_data):
    # الاحتفاظ بآخر 3000 منتج فقط حتى لا يكبر حجم الملف
    if len(seen_data) > 3000:
        keys = list(seen_data.keys())[-3000:]
        seen_data = {k: seen_data[k] for k in keys}
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_data, f, ensure_ascii=False, indent=2)

def send_deal_to_telegram(deal):
    savings = deal["old_price"] - deal["new_price"]
    caption = (
        f"🔥 <b>خصم {deal['discount']}% من {deal['store']}</b>\n\n"
        f"📦 <b>المنتج:</b> {deal['title']}\n\n"
        f"💰 <b>السعر بعد الخصم:</b> {deal['new_price']:,.0f} ج.م\n"
        f"❌ <b>السعر قبل الخصم:</b> <s>{deal['old_price']:,.0f} ج.م</s>\n"
        f"💸 <b>قيمة التوفير:</b> {savings:,.0f} ج.م\n\n"
        f"🔗 <a href='{deal['url']}'>اضغط هنا لفتح صفحة المنتج والشراء</a>"
    )

    try:
        if deal.get("image") and deal["image"].startswith("http"):
            api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": CHAT_ID,
                "photo": deal["image"],
                "caption": caption,
                "parse_mode": "HTML"
            }
            r = requests.post(api_url, json=payload, timeout=15)
            if r.status_code == 200:
                return
        # في حالة عدم وجود صورة أو فشل إرسال الصورة، يرسل نص كامل
        api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        requests.post(api_url, json=payload, timeout=15)
    except Exception as e:
        print(f"[!] خطأ إرسال تليجرام: {e}")

# ================= 1. فاحص جوميا =================
def scan_jumia():
    deals = []
    for url in SCAN_URLS["jumia"]:
        try:
            r = cureq.get(url, headers=HEADERS, impersonate="chrome120", timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select("article.prd"):
                link_el = card.select_one("a.core")
                if not link_el:
                    continue
                title = (card.select_one("h3.name") or link_el).get_text(strip=True)
                new_p = clean_num(card.select_one(".prc").get_text() if card.select_one(".prc") else "")
                old_p = clean_num(card.select_one(".old").get_text() if card.select_one(".old") else "")
                disc_el = card.select_one(".bdg._dsct")
                disc = clean_num(disc_el.get_text()) if disc_el else (round(((old_p - new_p) / old_p) * 100) if old_p > new_p else 0)

                if disc >= MIN_DISCOUNT and new_p > 0:
                    if old_p == 0:
                        old_p = round(new_p / (1 - disc / 100))
                    img_el = card.select_one("img.img")
                    img = img_el.get("data-src") or img_el.get("src") if img_el else ""
                    href = link_el.get("href", "")
                    full_url = f"https://www.jumia.com.eg{href}" if href.startswith("/") else href
                    deals.append({
                        "id": full_url.split("?")[0],
                        "store": "Jumia EG 🟠",
                        "title": title,
                        "new_price": new_p,
                        "old_price": old_p,
                        "discount": int(disc),
                        "url": full_url,
                        "image": img
                    })
        except Exception as e:
            print(f"[!] خطأ في جوميا ({url}): {e}")
    return deals

# ================= 2. فاحص 2B مصر =================
def scan_2b():
    deals = []
    for url in SCAN_URLS["2b"]:
        try:
            r = cureq.get(url, headers=HEADERS, impersonate="chrome120", timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            for card in soup.select(".product-item-info"):
                link_el = card.select_one("a.product-item-link")
                if not link_el:
                    continue
                title = link_el.get_text(strip=True)
                full_url = link_el.get("href", "")
                new_el = card.select_one(".special-price .price") or card.select_one('[data-price-type="finalPrice"]')
                old_el = card.select_one(".old-price .price") or card.select_one('[data-price-type="oldPrice"]')
                new_p = clean_num(new_el.get_text() if new_el else "")
                old_p = clean_num(old_el.get_text() if old_el else "")

                if old_p > new_p > 0:
                    disc = round(((old_p - new_p) / old_p) * 100)
                    if disc >= MIN_DISCOUNT:
                        img_el = card.select_one("img.product-image-photo")
                        img = (img_el.get("data-src") or img_el.get("src")) if img_el else ""
                        deals.append({
                            "id": full_url.split("?")[0],
                            "store": "2B Egypt 💻",
                            "title": title,
                            "new_price": new_p,
                            "old_price": old_p,
                            "discount": int(disc),
                            "url": full_url,
                            "image": img
                        })
        except Exception as e:
            print(f"[!] خطأ في 2B ({url}): {e}")
    return deals

# ================= 3. فاحص أمازون ونون بالمتصفح الخفي =================
def scan_amazon_and_noon():
    deals = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=HEADERS["User-Agent"], locale="ar-EG")
        page = context.new_page()

        # فحص أمازون مصر
        for url in SCAN_URLS["amazon"]:
            try:
                page.goto(url, timeout=45000)
                page.wait_for_timeout(4000)
                soup = BeautifulSoup(page.content(), "lxml")
                for card in soup.select('div[data-component-type="s-search-result"]'):
                    asin = card.get("data-asin")
                    if not asin:
                        continue
                    title_el = card.select_one("h2")
                    title = title_el.get_text(strip=True) if title_el else "منتج أمازون"
                    new_el = card.select_one(".a-price:not(.a-text-price) .a-offscreen")
                    old_el = card.select_one(".a-price.a-text-price .a-offscreen")
                    new_p = clean_num(new_el.get_text() if new_el else "")
                    old_p = clean_num(old_el.get_text() if old_el else "")

                    if old_p > new_p > 0:
                        disc = round(((old_p - new_p) / old_p) * 100)
                        if disc >= MIN_DISCOUNT:
                            img_el = card.select_one("img.s-image")
                            img = img_el.get("src") if img_el else ""
                            deals.append({
                                "id": f"amz_{asin}",
                                "store": "Amazon EG 🛒",
                                "title": title,
                                "new_price": new_p,
                                "old_price": old_p,
                                "discount": int(disc),
                                "url": f"https://www.amazon.eg/-/ar/dp/{asin}",
                                "image": img
                            })
            except Exception as e:
                print(f"[!] خطأ في أمازون ({url}): {e}")

        # فحص نون مصر
        for url in SCAN_URLS["noon"]:
            try:
                page.goto(url, timeout=45000)
                page.wait_for_timeout(5000)
                soup = BeautifulSoup(page.content(), "lxml")
                for card in soup.select('a[href*="/egypt-ar/"][href*="/p/"]'):
                    href = card.get("href", "")
                    text = card.get_text(" | ", strip=True)
                    disc_match = re.search(r"(\d+)\s*%", text)
                    if not disc_match:
                        continue
                    disc = int(disc_match.group(1))
                    if disc < MIN_DISCOUNT:
                        continue

                    img_el = card.select_one("img")
                    img = img_el.get("src") if img_el else ""
                    title = img_el.get("alt") if (img_el and img_el.get("alt")) else text.split("|")[0]

                    # استخراج الأسعار من الكارت
                    prices = [clean_num(m.group(0)) for m in re.finditer(r"[\d,]+(\.\d+)?", text)]
                    prices = [pr for pr in prices if pr > 50 and pr != disc]
                    new_p = min(prices) if prices else 0
                    old_p = max(prices) if len(prices) > 1 else round(new_p / (1 - disc / 100))

                    if new_p > 0:
                        full_url = f"https://www.noon.com{href}" if href.startswith("/") else href
                        deals.append({
                            "id": full_url.split("?")[0],
                            "store": "Noon EG 🟡",
                            "title": title,
                            "new_price": new_p,
                            "old_price": old_p,
                            "discount": disc,
                            "url": full_url,
                            "image": img
                        })
            except Exception as e:
                print(f"[!] خطأ في نون ({url}): {e}")

        browser.close()
    return deals

def main():
    seen = load_seen()
    all_deals = []

    print("[*] جاري فحص جوميا...")
    all_deals.extend(scan_jumia())

    print("[*] جاري فحص 2B...")
    all_deals.extend(scan_2b())

    print("[*] جاري فحص أمازون ونون...")
    all_deals.extend(scan_amazon_and_noon())

    print(f"[+] إجمالي العروض المكتشفة بخصم فوق {MIN_DISCOUNT}%: {len(all_deals)}")

    new_sent = 0
    for deal in all_deals:
        deal_id = deal["id"]
        # يرسل لو المنتج جديد أو لو سعره نزل أكتر من آخر مرة اتبعت فيها
        if deal_id not in seen or deal["new_price"] < seen[deal_id]:
            send_deal_to_telegram(deal)
            seen[deal_id] = deal["new_price"]
            new_sent += 1
            time.sleep(1.5)  # لتجنب حظر الرسائل السريعة في تليجرام

    if new_sent > 0:
        save_seen(seen)
        print(f"[✓] تم إرسال {new_sent} عرض جديد وتحديث قاعدة البيانات.")
    else:
        print("[-] لا توجد عروض جديدة لم ترسل من قبل.")

if __name__ == "__main__":
    main()
