import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
MIN_DISCOUNT = 40  # أقل نسبة خصم مطلوبة (40%)

SEEN_FILE = "seen_deals.json"

SCAN_URLS = {
    "jumia": [
        "https://www.jumia.com.eg/ar/flash-sales/",
        "https://www.jumia.com.eg/ar/mlp-clearance-sale/",
        "https://www.jumia.com.eg/ar/electronics/",
        "https://www.jumia.com.eg/ar/phones-tablets/",
        "https://www.jumia.com.eg/ar/computing/",
        "https://www.jumia.com.eg/ar/home-office-appliances/"
    ],
    "2b": [
        "https://2b.com.eg/ar/deals.html",
        "https://2b.com.eg/ar/computers.html",
        "https://2b.com.eg/ar/mobile-and-tablet.html",
        "https://2b.com.eg/ar/home-appliances.html",
        "https://2b.com.eg/ar/gaming.html"
    ],
    "amazon": [
        "https://www.amazon.eg/s?i=electronics&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
        "https://www.amazon.eg/s?i=computers&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
        "https://www.amazon.eg/s?i=kitchen&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE"
    ],
    "noon": [
        "https://www.noon.com/egypt-ar/electronics/",
        "https://www.noon.com/egypt-ar/mobiles/",
        "https://www.noon.com/egypt-ar/home-appliances/"
    ]
}

HEADERS = {
    "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Referer": "https://www.google.com.eg/",
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
    if len(seen_data) > 4000:
        keys = list(seen_data.keys())[-4000:]
        seen_data = {k: seen_data[k] for k in keys}
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_data, f, ensure_ascii=False, indent=2)

def send_telegram_msg(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("[!] تنبيه: BOT_TOKEN أو CHAT_ID غير موجودين في Secrets!")
        return
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(api_url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=15)
    if r.status_code != 200:
        print(f"[!] خطأ تليجرام: {r.text}")

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
            r = requests.post(api_url, json={
                "chat_id": CHAT_ID,
                "photo": deal["image"],
                "caption": caption,
                "parse_mode": "HTML"
            }, timeout=15)
            if r.status_code == 200:
                return
        send_telegram_msg(caption)
    except Exception as e:
        print(f"[!] خطأ إرسال تليجرام: {e}")

# ================= 1. فاحص جوميا =================
def scan_jumia():
    deals = []
    for url in SCAN_URLS["jumia"]:
        try:
            r = cureq.get(url, headers=HEADERS, impersonate="chrome120", timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select("article.prd")
            print(f"  -> جوميا ({url.split('/')[-2]}): تم فحص {len(cards)} منتج")
            for card in cards:
                link_el = card.select_one("a.core")
                if not link_el:
                    continue
                title = (card.select_one("h3.name") or link_el).get_text(strip=True)
                new_p = clean_num(card.select_one(".prc").get_text() if card.select_one(".prc") else "")
                old_p = clean_num(card.select_one(".old").get_text() if card.select_one(".old") else "")
                disc_el = card.select_one(".bdg._dsct")
                disc = clean_num(disc_el.get_text()) if disc_el else (round(((old_p - new_p) / old_p) * 100) if old_p > new_p else 0)

                if disc >= MIN_DISCOUNT and new_p > 0:
                    if old_p <= new_p:
                        old_p = round(new_p / (1 - disc / 100))
                    img_el = card.select_one("img.img")
                    img = (img_el.get("data-src") or img_el.get("src")) if img_el else ""
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
            cards = soup.select(".product-item-info")
            print(f"  -> 2B ({url.split('/')[-1]}): تم فحص {len(cards)} منتج")
            for card in cards:
                link_el = card.select_one("a.product-item-link")
                if not link_el:
                    continue
                title = link_el.get_text(strip=True)
                full_url = link_el.get("href", "")
                
                # قراءة السعر من خصائص الـ data أو النص
                new_el = card.select_one('[data-price-type="finalPrice"]') or card.select_one(".special-price .price")
                old_el = card.select_one('[data-price-type="oldPrice"]') or card.select_one(".old-price .price")
                
                new_p = clean_num(new_el.get("data-price-amount") if (new_el and new_el.has_attr("data-price-amount")) else (new_el.get_text() if new_el else ""))
                old_p = clean_num(old_el.get("data-price-amount") if (old_el and old_el.has_attr("data-price-amount")) else (old_el.get_text() if old_el else ""))

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

# ================= 3. فاحص نون مصر (بـ curl_cffi لتخطي HTTP2 Error) =================
def scan_noon():
    deals = []
    for url in SCAN_URLS["noon"]:
        try:
            r = cureq.get(url, headers=HEADERS, impersonate="chrome120", timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            
            # الطريقة الأولى: سحب من بيانات Next.js المدمجة
            next_script = soup.find("script", id="__NEXT_DATA__")
            if next_script and next_script.string:
                data = json.loads(next_script.string)
                hits = re.findall(r'\{[^{}]*"sku"[^{}]*"price"[^{}]*\}', next_script.string)
                print(f"  -> نون JSON ({url.split('/')[-2]}): تم فحص {len(hits)} عنصر")
                for h in hits:
                    try:
                        item = json.loads(h)
                        old_p = float(item.get("price", 0))
                        new_p = float(item.get("sale_price") or old_p)
                        if old_p > new_p > 0:
                            disc = round(((old_p - new_p) / old_p) * 100)
                            if disc >= MIN_DISCOUNT:
                                sku = item.get("sku", "")
                                slug = item.get("url", "product")
                                title = item.get("name", "منتج نون")
                                img_key = item.get("image_key", "")
                                img = f"https://f.nooncdn.com/p/{img_key}.jpg" if img_key else ""
                                full_url = f"https://www.noon.com/egypt-ar/{slug}/{sku}/p/"
                                deals.append({
                                    "id": f"noon_{sku}",
                                    "store": "Noon EG 🟡",
                                    "title": title,
                                    "new_price": new_p,
                                    "old_price": old_p,
                                    "discount": int(disc),
                                    "url": full_url,
                                    "image": img
                                })
                    except Exception:
                        continue

            # الطريقة الثانية: سحب من روابط HTML مباشرة
            cards = soup.select('a[href*="/egypt-ar/"][href*="/p/"]')
            if cards:
                print(f"  -> نون HTML ({url.split('/')[-2]}): تم فحص {len(cards)} كارت")
            for card in cards:
                text = card.get_text(" | ", strip=True)
                disc_match = re.search(r"(\d+)\s*%\s*(خصم|OFF)?", text, re.I)
                if not disc_match:
                    continue
                disc = int(disc_match.group(1))
                if disc < MIN_DISCOUNT or disc > 95:
                    continue
                prices = [clean_num(m.group(0)) for m in re.finditer(r"[\d,]+(\.\d+)?", text)]
                prices = [pr for pr in prices if pr > 50 and pr != disc]
                if not prices:
                    continue
                new_p = min(prices)
                old_p = max(prices) if len(prices) > 1 else round(new_p / (1 - disc / 100))
                href = card.get("href", "")
                full_url = f"https://www.noon.com{href}" if href.startswith("/") else href
                img_el = card.select_one("img")
                img = img_el.get("src") if img_el else ""
                title = (img_el.get("alt") if (img_el and img_el.get("alt")) else text.split("|")[0]).strip()
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
    return deals

# ================= 4. فاحص أمازون مصر =================
def scan_amazon():
    deals = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--disable-http2"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            locale="ar-EG"
        )
        page = context.new_page()

        for url in SCAN_URLS["amazon"]:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(3000)
                soup = BeautifulSoup(page.content(), "lxml")
                cards = soup.select('div[data-component-type="s-search-result"]')
                print(f"  -> أمازون: تم فحص {len(cards)} منتج")
                for card in cards:
                    asin = card.get("data-asin")
                    if not asin:
                        continue
                    title_el = card.select_one("h2")
                    title = title_el.get_text(strip=True) if title_el else "منتج أمازون"
                    new_el = card.select_one(".a-price:not(.a-text-price) .a-offscreen") or card.select_one(".a-price-whole")
                    old_el = card.select_one(".a-price.a-text-price .a-offscreen")
                    new_p = clean_num(new_el.get_text() if new_el else "")
                    old_p = clean_num(old_el.get_text() if old_el else "")

                    # البحث عن نسبة الخصم المكتوبة صراحة لو السعر القديم مش ظاهر
                    disc = 0
                    if old_p > new_p > 0:
                        disc = round(((old_p - new_p) / old_p) * 100)
                    else:
                        badge_text = card.get_text(" ", strip=True)
                        m = re.search(r"-\s*(\d+)\s*%|خصم\s*(\d+)\s*%", badge_text)
                        if m:
                            disc = int(m.group(1) or m.group(2))
                            if new_p > 0 and disc < 95:
                                old_p = round(new_p / (1 - disc / 100))

                    if disc >= MIN_DISCOUNT and new_p > 0:
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
        browser.close()
    return deals

def main():
    seen = load_seen()
    
    # إرسال رسالة ترحيب في أول مرة فقط للتأكد أن تليجرام مربوط صح
    if not seen:
        send_telegram_msg("✅ <b>تم تشغيل رادار الخصومات (40%+) بنجاح!</b>\nجاري الآن فحص (أمازون، نون، جوميا، 2B) وإرسال العروض فوراً...")
        seen["_init_"] = 1

    all_deals = []

    print("[*] جاري فحص جوميا...")
    all_deals.extend(scan_jumia())

    print("[*] جاري فحص 2B...")
    all_deals.extend(scan_2b())

    print("[*] جاري فحص نون...")
    all_deals.extend(scan_noon())

    print("[*] جاري فحص أمازون...")
    all_deals.extend(scan_amazon())

    print(f"[+] إجمالي العروض المكتشفة بخصم فوق {MIN_DISCOUNT}%: {len(all_deals)}")

    new_sent = 0
    for deal in all_deals:
        deal_id = deal["id"]
        if deal_id not in seen or deal["new_price"] < seen[deal_id]:
            send_deal_to_telegram(deal)
            seen[deal_id] = deal["new_price"]
            new_sent += 1
            time.sleep(1.2)

    save_seen(seen)
    print(f"[✓] تم إرسال {new_sent} عرض جديد وتحديث قاعدة البيانات.")

if __name__ == "__main__":
    main()
