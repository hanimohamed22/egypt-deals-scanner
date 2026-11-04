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

def smart_fetch(url, session):
    """جلب الصفحة مباشرة، وفي حالة حظر 403 أو كابتشا يستخدم بوابة عبور بديلة"""
    try:
        r = session.get(url, headers=HEADERS, timeout=25)
        if r.status_code == 200 and len(r.text) > 5000 and "api-services-support@amazon.com" not in r.text:
            return r.text, 200
    except Exception:
        pass

    # بديل رقم 1: عبر بوابة CorsProxy / AllOrigins لتخطي حظر IP الداتا سنتر
    proxy_urls = [
        f"https://api.allorigins.win/raw?url={urllib.parse.quote(url)}",
        f"https://api.codetabs.com/v1/proxy?quest={urllib.parse.quote(url)}"
    ]
    for p_url in proxy_urls:
        try:
            pr = requests.get(p_url, timeout=30)
            if pr.status_code == 200 and len(pr.text) > 5000:
                return pr.text, 200
        except Exception:
            continue

    return "", 403

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

# ================= 1. فاحص 2B مصر (بوابة GraphQL الرسمية + HTML) =================
def scan_2b():
    deals = []
    session = cureq.Session(impersonate="chrome120")
    
    # أولاً: سحب مباشر من بوابة GraphQL الرسمية لـ 2B (لا تحظر السيرفرات أبداً)
    gql_url = "https://2b.com.eg/graphql"
    search_terms = ["a", "e", "i", "s", "pro", "samsung", "hp", "dell", "xiaomi", "tornado"]
    gql_items_count = 0
    
    for term in search_terms:
        query = """
        {
          products(search: "%s", pageSize: 50) {
            items {
              name
              sku
              url_key
              small_image { url }
              price_range {
                minimum_price {
                  regular_price { value }
                  final_price { value }
                  discount { percent_off }
                }
              }
            }
          }
        }
        """ % term
        try:
            r = session.post(gql_url, json={"query": query}, headers={"Store": "ar"}, timeout=20)
            if r.status_code == 200:
                items = r.json().get("data", {}).get("products", {}).get("items", [])
                gql_items_count += len(items)
                for item in items:
                    pr = item.get("price_range", {}).get("minimum_price", {})
                    old_p = float(pr.get("regular_price", {}).get("value") or 0)
                    new_p = float(pr.get("final_price", {}).get("value") or 0)
                    disc = float(pr.get("discount", {}).get("percent_off") or 0)
                    if old_p > new_p > 0:
                        if disc == 0:
                            disc = round(((old_p - new_p) / old_p) * 100)
                        url_key = item.get("url_key", "")
                        full_url = f"https://2b.com.eg/ar/{url_key}.html"
                        deals.append({
                            "id": f"2b_{item.get('sku')}",
                            "store": "2B Egypt 💻",
                            "title": item.get("name", "منتج 2B"),
                            "new_price": new_p,
                            "old_price": old_p,
                            "discount": int(round(disc)),
                            "url": full_url,
                            "image": item.get("small_image", {}).get("url", "")
                        })
        except Exception:
            continue

    print(f"  -> 2B GraphQL API: تم فحص {gql_items_count} منتج بنجاح")
    return deals

# ================= 2. فاحص جوميا مصر =================
def scan_jumia():
    deals = []
    session = cureq.Session(impersonate="chrome120")
    urls = [
        "https://www.jumia.com.eg/ar/flash-sales/",
        "https://www.jumia.com.eg/ar/electronics/",
        "https://www.jumia.com.eg/ar/phones-tablets/",
        "https://www.jumia.com.eg/ar/computing/",
        "https://www.jumia.com.eg/ar/home-office-appliances/"
    ]
    for url in urls:
        html, code = smart_fetch(url, session)
        soup = BeautifulSoup(html, "lxml") if html else None
        cards = soup.select("article.prd") if soup else []
        cat_name = url.split("/")[-2]
        print(f"  -> جوميا ({cat_name}): كود {code} | تم فحص {len(cards)} منتج")
        
        for card in cards:
            link_el = card.select_one("a.core")
            if not link_el:
                continue
            title = (card.select_one("h3.name") or link_el).get_text(strip=True)
            new_p = clean_num(card.select_one(".prc").get_text() if card.select_one(".prc") else "")
            old_p = clean_num(card.select_one(".old").get_text() if card.select_one(".old") else "")
            disc_el = card.select_one(".bdg._dsct")
            disc = clean_num(disc_el.get_text()) if disc_el else (round(((old_p - new_p) / old_p) * 100) if old_p > new_p else 0)

            if disc > 0 and new_p > 0:
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
    return deals

# ================= 3. فاحص نون مصر =================
def scan_noon():
    deals = []
    session = cureq.Session(impersonate="safari_ios")
    urls = [
        "https://www.noon.com/egypt-ar/electronics/",
        "https://www.noon.com/egypt-ar/mobiles/",
        "https://www.noon.com/egypt-ar/laptops/",
        "https://www.noon.com/egypt-ar/eg-mega-deals/"
    ]
    for url in urls:
        html, code = smart_fetch(url, session)
        if not html:
            print(f"  -> نون ({url.split('/')[-2]}): كود {code} | 0 منتج")
            continue
        soup = BeautifulSoup(html, "lxml")
        cards = soup.select('a[href*="/egypt-ar/"][href*="/p/"]')
        
        # استخراج إضافي من JSON نون المدمج في الصفحة
        json_matches = re.findall(r'"sku"\s*:\s*"([^"]+)"[^{}]*?"name"\s*:\s*"([^"]+)"[^{}]*?"price"\s*:\s*([\d.]+)[^{}]*?"sale_price"\s*:\s*([\d.]+)', html)
        print(f"  -> نون ({url.split('/')[-2]}): كود {code} | تم فحص {len(cards) + len(json_matches)} منتج")

        for sku, title, old_s, new_s in json_matches:
            old_p, new_p = float(old_s), float(new_s)
            if old_p > new_p > 0:
                disc = round(((old_p - new_p) / old_p) * 100)
                deals.append({
                    "id": f"noon_{sku}",
                    "store": "Noon EG 🟡",
                    "title": title,
                    "new_price": new_p,
                    "old_price": old_p,
                    "discount": int(disc),
                    "url": f"https://www.noon.com/egypt-ar/{sku}/p/",
                    "image": ""
                })

        for card in cards:
            text = card.get_text(" | ", strip=True)
            disc_match = re.search(r"(\d+)\s*%", text)
            if not disc_match:
                continue
            disc = int(disc_match.group(1))
            if disc > 95:
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
    return deals

# ================= 4. فاحص أمازون مصر =================
def scan_amazon():
    deals = []
    session = cureq.Session(impersonate="safari_ios")
    urls = [
        "https://www.amazon.eg/s?k=electronics&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
        "https://www.amazon.eg/s?k=phones&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
        "https://www.amazon.eg/s?k=laptop&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE"
    ]
    for url in urls:
        html, code = smart_fetch(url, session)
        soup = BeautifulSoup(html, "lxml") if html else None
        cards = [c for c in (soup.select('div[data-asin]') if soup else []) if c.get("data-asin")]
        print(f"  -> أمازون: كود {code} | تم فحص {len(cards)} منتج")
        for card in cards:
            asin = card.get("data-asin")
            title_el = card.select_one("h2") or card.select_one(".a-size-base-plus")
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            new_el = card.select_one(".a-price:not(.a-text-price) .a-offscreen") or card.select_one(".a-price-whole")
            old_el = card.select_one(".a-price.a-text-price .a-offscreen")
            new_p = clean_num(new_el.get_text() if new_el else "")
            old_p = clean_num(old_el.get_text() if old_el else "")

            if old_p > new_p > 0:
                disc = round(((old_p - new_p) / old_p) * 100)
                img_el = card.select_one("img.s-image") or card.select_one("img")
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
    return deals

def main():
    seen = load_seen()
    all_deals = []

    print("[*] جاري فحص 2B...")
    all_deals.extend(scan_2b())

    print("[*] جاري فحص جوميا...")
    all_deals.extend(scan_jumia())

    print("[*] جاري فحص نون...")
    all_deals.extend(scan_noon())

    print("[*] جاري فحص أمازون...")
    all_deals.extend(scan_amazon())

    # إزالة التكرارات بنفس الـ ID
    unique_deals = {d["id"]: d for d in all_deals}.values()
    strong_deals = [d for d in unique_deals if d["discount"] >= MIN_DISCOUNT]
    
    print(f"[+] إجمالي المنتجات المفحوصة بخصومات: {len(unique_deals)} | منها فوق {MIN_DISCOUNT}%: {len(strong_deals)}")

    # إرسال أعلى 3 عروض كعينة لو مفيش عرض معدي 40% في أول تجربة
    if "_sample_sent_" not in seen and not strong_deals and unique_deals:
        top_samples = sorted(unique_deals, key=lambda x: x["discount"], reverse=True)[:3]
        send_telegram_msg(f"📡 <b>تقرير الفحص:</b>\nتم فحص <b>{len(unique_deals)}</b> منتج بنجاح! إليك أعلى 3 خصومات متاحة حالياً كعينة:")
        for d in top_samples:
            send_deal_to_telegram(d)
            time.sleep(1)
        seen["_sample_sent_"] = 1

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
