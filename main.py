import os
import re
import json
import time
import urllib.parse
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
MIN_DISCOUNT = 40  # نسبة الخصم المطلوبة 40%

SEEN_FILE = "seen_deals.json"

HEADERS_DESKTOP = {
    "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
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
        return
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(api_url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=15)

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

# ================= 1. فاحص أمازون مصر (موبايل + قارئ نصوص شامل) =================
def scan_amazon():
    deals = []
    session = cureq.Session(impersonate="safari_ios")
    mobile_headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Accept-Language": "ar-EG,ar;q=0.9"
    }
    urls = [
        "https://www.amazon.eg/s?k=electronics&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
        "https://www.amazon.eg/s?k=phones&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
        "https://www.amazon.eg/s?k=laptop&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
        "https://www.amazon.eg/s?k=kitchen&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
        "https://www.amazon.eg/s?k=خصم&language=ar_AE"
    ]
    for url in urls:
        try:
            r = session.get(url, headers=mobile_headers, timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            cards = [c for c in soup.select('div[data-asin]') if c.get("data-asin") and len(c.get("data-asin", "")) == 10]
            print(f"  -> أمازون: كود {r.status_code} | تم فحص {len(cards)} منتج")
            
            for card in cards:
                asin = card.get("data-asin")
                text_all = card.get_text(" | ", strip=True)
                
                title_el = card.select_one("h2") or card.select_one(".a-size-base-plus") or card.select_one(".a-size-small")
                img_el = card.select_one("img.s-image") or card.select_one("img")
                title = title_el.get_text(strip=True) if title_el else (img_el.get("alt") if img_el else "منتج أمازون")
                if not title or len(title) < 4:
                    continue

                # استخراج كل الأسعار المكتوبة جنب ج.م أو EGP
                arabic_map = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
                norm_text = text_all.translate(arabic_map)
                price_matches = re.findall(r"(?:ج\.م\.?|EGP)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:ج\.م\.?|EGP)", norm_text)
                prices = []
                for m1, m2 in price_matches:
                    val = clean_num(m1 or m2)
                    if val >= 20:
                        prices.append(val)

                # أو من عناصر السعر التقليدية
                if not prices:
                    for p_el in card.select(".a-offscreen, .a-price-whole"):
                        val = clean_num(p_el.get_text())
                        if val >= 20:
                            prices.append(val)

                if not prices:
                    continue

                new_p = min(prices)
                old_p = max(prices) if len(set(prices)) > 1 else 0

                # البحث عن نسبة الخصم المكتوبة في الكارت مثل -45% أو (45% خصم)
                disc_match = re.search(r"-\s*(\d{2})\s*%|(\d{2})\s*%\s*(?:خصم|off)", norm_text, re.I)
                disc = int(disc_match.group(1) or disc_match.group(2)) if disc_match else 0

                if old_p > new_p and disc == 0:
                    disc = round(((old_p - new_p) / old_p) * 100)
                elif disc > 0 and old_p <= new_p and disc < 95:
                    old_p = round(new_p / (1 - disc / 100))

                if disc > 0 and new_p > 0:
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
            print(f"[!] خطأ في أمازون: {e}")
    return deals

# ================= 2. فاحص 2B مصر (الروابط الصافية التي نجحت) =================
def scan_2b():
    deals = []
    urls = [
        "https://2b.com.eg/ar/home-appliances.html",
        "https://2b.com.eg/ar/home-appliances.html?p=2",
        "https://2b.com.eg/ar/computers.html",
        "https://2b.com.eg/ar/mobile-and-tablet.html",
        "https://2b.com.eg/ar/gaming.html"
    ]
    for url in urls:
        try:
            r = cureq.get(url, headers=HEADERS_DESKTOP, impersonate="chrome120", timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select(".product-item-info")
            print(f"  -> 2B ({url.split('/')[-1]}): كود {r.status_code} | تم فحص {len(cards)} منتج")
            for card in cards:
                link_el = card.select_one("a.product-item-link")
                if not link_el:
                    continue
                title = link_el.get_text(strip=True)
                full_url = link_el.get("href", "")
                
                # قراءة كل الأسعار الموجودة في الكارت
                prices = []
                for p_el in card.select('[data-price-amount], .price'):
                    val = clean_num(p_el.get("data-price-amount") or p_el.get_text())
                    if val > 30:
                        prices.append(val)
                if len(set(prices)) >= 2:
                    new_p = min(prices)
                    old_p = max(prices)
                    disc = round(((old_p - new_p) / old_p) * 100)
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
            print(f"[!] خطأ في 2B: {e}")
    return deals

# ================= 3. فاحص نون مصر =================
def scan_noon():
    deals = []
    session = cureq.Session(impersonate="safari_ios")
    urls = [
        "https://www.noon.com/egypt-ar/electronics/",
        "https://www.noon.com/egypt-ar/mobiles/",
        "https://www.noon.com/egypt-ar/laptops/"
    ]
    for url in urls:
        try:
            r = session.get(url, timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            # جلب أي رابط منتج في الصفحة أو قراءة البيانات من سكريبت Next.js
            cards = soup.select('a[href*="/p/"]')
            print(f"  -> نون ({url.split('/')[-2]}): كود {r.status_code} | تم فحص {len(cards)} كارت")
            for card in cards:
                text = card.get_text(" | ", strip=True)
                arabic_map = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
                norm_text = text.translate(arabic_map)
                disc_match = re.search(r"(\d{2})\s*%", norm_text)
                if not disc_match:
                    continue
                disc = int(disc_match.group(1))
                if disc > 95 or disc < 5:
                    continue
                prices = [clean_num(m.group(0)) for m in re.finditer(r"[\d,]+(?:\.\d+)?", norm_text)]
                prices = [pr for pr in prices if pr > 50 and pr != disc]
                if not prices:
                    continue
                new_p = min(prices)
                old_p = max(prices) if len(set(prices)) > 1 else round(new_p / (1 - disc / 100))
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
            print(f"[!] خطأ في نون: {e}")
    return deals

# ================= 4. فاحص جوميا مصر (عبر بوابة القراءة السحابية لتخطي 403) =================
def scan_jumia():
    deals = []
    urls = [
        "https://www.jumia.com.eg/ar/flash-sales/",
        "https://www.jumia.com.eg/ar/electronics/",
        "https://www.jumia.com.eg/ar/phones-tablets/"
    ]
    for url in urls:
        try:
            # استخدام بوابة Jina Reader لتخطي حظر Cloudflare 403 على سيرفرات GitHub
            jina_url = f"https://r.jina.ai/{url}"
            r = requests.get(jina_url, headers={"Accept": "text/plain"}, timeout=30)
            lines = r.text.splitlines()
            print(f"  -> جوميا ({url.split('/')[-2]}): كود {r.status_code} | حجم النص {len(r.text)} حرف")
            
            # استخراج المنتجات التي تحتوي على روابط جوميا وأسعارها من النص المنسق
            pattern = re.compile(r"\[([^\]]+)\]\((https://www\.jumia\.com\.eg/[^\)]+\.html)\)")
            for i, line in enumerate(lines):
                for match in pattern.finditer(line):
                    title, link = match.group(1), match.group(2)
                    if "image" in title.lower() or len(title) < 5:
                        continue
                    context_Block = " ".join(lines[max(0, i-2):min(len(lines), i+5)])
                    arabic_map = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
                    norm_ctx = context_Block.translate(arabic_map)
                    
                    disc_m = re.search(r"(\d{2})\s*%", norm_ctx)
                    prices = [clean_num(m.group(0)) for m in re.finditer(r"([\d,]+\.\d+|[\d,]+)\s*(?:جنيه|ج\.م|EGP)", norm_ctx)]
                    prices = [p for p in prices if p > 30]
                    
                    if prices and disc_m:
                        disc = int(disc_m.group(1))
                        new_p = min(prices)
                        old_p = max(prices) if len(set(prices)) > 1 else round(new_p / (1 - disc / 100))
                        deals.append({
                            "id": link.split("?")[0],
                            "store": "Jumia EG 🟠",
                            "title": title,
                            "new_price": new_p,
                            "old_price": old_p,
                            "discount": disc,
                            "url": link,
                            "image": ""
                        })
        except Exception as e:
            print(f"[!] خطأ في جوميا: {e}")
    return deals

def main():
    seen = load_seen()
    all_deals = []

    print("[*] جاري فحص أمازون...")
    all_deals.extend(scan_amazon())

    print("[*] جاري فحص 2B...")
    all_deals.extend(scan_2b())

    print("[*] جاري فحص نون...")
    all_deals.extend(scan_noon())

    print("[*] جاري فحص جوميا...")
    all_deals.extend(scan_jumia())

    unique_deals = list({d["id"]: d for d in all_deals}.values())
    strong_deals = [d for d in unique_deals if d["discount"] >= MIN_DISCOUNT]
    
    print(f"[+] إجمالي المنتجات المستخرجة بخصومات: {len(unique_deals)} | منها فوق {MIN_DISCOUNT}%: {len(strong_deals)}")

    # إرسال أعلى 3 عروض كعينة فورية على تليجرام للتأكد من شكل الرسائل
    if "_sample_sent_v2_" not in seen and unique_deals:
        top_samples = sorted(unique_deals, key=lambda x: x["discount"], reverse=True)[:3]
        send_telegram_msg(f"📡 <b>تقرير الرادار الحي:</b>\nتم استخراج <b>{len(unique_deals)}</b> عرض بخصومات مؤكدة! إليك أعلى 3 عروض تم التقاطها الآن:")
        for d in top_samples:
            send_deal_to_telegram(d)
            time.sleep(1)
        seen["_sample_sent_v2_"] = 1

    new_sent = 0
    for deal in strong_deals:
        deal_id = deal["id"]
        if deal_id not in seen or deal["new_price"] < seen[deal_id]:
            send_deal_to_telegram(deal)
            seen[deal_id] = deal["new_price"]
            new_sent += 1
            time.sleep(1.2)

    save_seen(seen)
    print(f"[✓] تم إرسال {new_sent} عرض (40%+) وتحديث قاعدة البيانات.")

if __name__ == "__main__":
    main()
