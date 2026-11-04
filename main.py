import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
MIN_DISCOUNT = 40  # أقل نسبة خصم مطلوبة (40%)

SEEN_FILE = "seen_deals.json"

# روابط محدثة ومجربة تعمل من السيرفرات الخارجية
SCAN_URLS = {
    "jumia": [
        "https://www.jumia.com.eg/flash-sales/",
        "https://www.jumia.com.eg/mlp-clearance-sale/",
        "https://www.jumia.com.eg/electronics/",
        "https://www.jumia.com.eg/phones-tablets/",
        "https://www.jumia.com.eg/computing/",
        "https://www.jumia.com.eg/home-office-appliances/"
    ],
    "2b": [
        "https://2b.com.eg/ar/home-appliances.html?product_list_limit=64",
        "https://2b.com.eg/ar/computers-laptops.html?product_list_limit=64",
        "https://2b.com.eg/ar/mobiles-tablets.html?product_list_limit=64",
        "https://2b.com.eg/ar/tv-audio-video.html?product_list_limit=64",
        "https://2b.com.eg/ar/accessories.html?product_list_limit=64",
        "https://2b.com.eg/ar/gaming.html?product_list_limit=64"
    ],
    "amazon": [
        "https://www.amazon.eg/s?k=خصم&i=electronics&language=ar_AE",
        "https://www.amazon.eg/s?k=عروض&i=computers&language=ar_AE",
        "https://www.amazon.eg/s?k=خصم&i=kitchen&language=ar_AE",
        "https://www.amazon.eg/s?k=خصم&i=mobile-apps&rh=n%3A21832883031&language=ar_AE"
    ],
    "noon": [
        "https://www.noon.com/egypt-ar/electronics/",
        "https://www.noon.com/egypt-ar/mobiles/",
        "https://www.noon.com/egypt-ar/laptops/",
        "https://www.noon.com/egypt-ar/home-appliances/"
    ]
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

# ================= 1. فاحص جوميا مصر (HTML + JSON Store) =================
def scan_jumia():
    deals = []
    session = cureq.Session(impersonate="chrome120")
    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cookie": "sl=ar;"
    }
    for url in SCAN_URLS["jumia"]:
        try:
            r = session.get(url, headers=headers, timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select("article.prd")
            
            # لو الـ HTML محول، نقرأ من كائن window.__STORE__ الداخلي لجوميا
            if not cards and "window.__STORE__" in r.text:
                m = re.search(r"window\.__STORE__\s*=\s*(\{.*?\});", r.text, re.S)
                if m:
                    store_data = json.loads(m.group(1))
                    prods = store_data.get("products", [])
                    print(f"  -> جوميا Store ({url.split('/')[-2]}): تم فحص {len(prods)} منتج")
                    for p in prods:
                        prices = p.get("prices", {})
                        new_p = clean_num(prices.get("rawPrice") or prices.get("price"))
                        old_p = clean_num(prices.get("oldPrice"))
                        disc = clean_num(prices.get("discount"))
                        if old_p > new_p > 0 and disc == 0:
                            disc = round(((old_p - new_p) / old_p) * 100)
                        if disc > 0 and new_p > 0:
                            if old_p <= new_p:
                                old_p = round(new_p / (1 - disc / 100))
                            full_url = f"https://www.jumia.com.eg{p.get('url', '')}"
                            deals.append({
                                "id": full_url.split("?")[0],
                                "store": "Jumia EG 🟠",
                                "title": p.get("displayName") or p.get("name", "منتج جوميا"),
                                "new_price": new_p,
                                "old_price": old_p,
                                "discount": int(disc),
                                "url": full_url,
                                "image": p.get("image", "")
                            })
                    continue

            print(f"  -> جوميا ({url.split('/')[-2]}): كود {r.status_code} | تم فحص {len(cards)} منتج")
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
        except Exception as e:
            print(f"[!] خطأ في جوميا ({url}): {e}")
    return deals

# ================= 2. فاحص 2B مصر =================
def scan_2b():
    deals = []
    session = cureq.Session(impersonate="chrome120")
    for url in SCAN_URLS["2b"]:
        try:
            r = session.get(url, timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select(".product-item-info")
            cat_name = url.split("/")[-1].split("?")[0]
            print(f"  -> 2B ({cat_name}): كود {r.status_code} | تم فحص {len(cards)} منتج")
            for card in cards:
                link_el = card.select_one("a.product-item-link")
                if not link_el:
                    continue
                title = link_el.get_text(strip=True)
                full_url = link_el.get("href", "")
                
                new_el = card.select_one('[data-price-type="finalPrice"]') or card.select_one(".special-price .price")
                old_el = card.select_one('[data-price-type="oldPrice"]') or card.select_one(".old-price .price")
                
                new_p = clean_num(new_el.get("data-price-amount") if (new_el and new_el.has_attr("data-price-amount")) else (new_el.get_text() if new_el else ""))
                old_p = clean_num(old_el.get("data-price-amount") if (old_el and old_el.has_attr("data-price-amount")) else (old_el.get_text() if old_el else ""))

                if old_p > new_p > 0:
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
            print(f"[!] خطأ في 2B ({url}): {e}")
    return deals

# ================= 3. فاحص أمازون مصر (بمحاكاة موبايل لتخطي كابتشا الديسكتوب) =================
def scan_amazon():
    deals = []
    session = cureq.Session(impersonate="safari_ios")
    mobile_headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Accept-Language": "ar-EG,ar;q=0.9"
    }
    for url in SCAN_URLS["amazon"]:
        try:
            r = session.get(url, headers=mobile_headers, timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select('div[data-component-type="s-search-result"]') or soup.select('div[data-asin]')
            cards = [c for c in cards if c.get("data-asin")]
            print(f"  -> أمازون: كود {r.status_code} | تم فحص {len(cards)} منتج")
            for card in cards:
                asin = card.get("data-asin")
                title_el = card.select_one("h2") or card.select_one(".a-size-base-plus") or card.select_one(".a-size-medium")
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
        except Exception as e:
            print(f"[!] خطأ في أمازون: {e}")
    return deals

# ================= 4. فاحص نون مصر =================
def scan_noon():
    deals = []
    session = cureq.Session(impersonate="safari_ios")
    for url in SCAN_URLS["noon"]:
        try:
            r = session.get(url, timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select('a[href*="/egypt-ar/"][href*="/p/"]')
            print(f"  -> نون ({url.split('/')[-2]}): كود {r.status_code} | تم فحص {len(cards)} منتج")
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
        except Exception as e:
            print(f"[!] خطأ في نون: {e}")
    return deals

def main():
    seen = load_seen()
    all_deals = []

    print("[*] جاري فحص جوميا...")
    all_deals.extend(scan_jumia())

    print("[*] جاري فحص 2B...")
    all_deals.extend(scan_2b())

    print("[*] جاري فحص أمازون...")
    all_deals.extend(scan_amazon())

    print("[*] جاري فحص نون...")
    all_deals.extend(scan_noon())

    # فلترة العروض التي تحقق نسبة الخصم المطلوبة (40% فما فوق)
    strong_deals = [d for d in all_deals if d["discount"] >= MIN_DISCOUNT]
    print(f"[+] إجمالي المنتجات المفحوصة بخصومات: {len(all_deals)} | منها فوق {MIN_DISCOUNT}%: {len(strong_deals)}")

    # لو دي أول مرة يشتغل ومفيش ولا عرض معدي 40% في اللحظة دي، يبعتلك أعلى 3 خصومات موجودة كتجربة حية
    if "_first_test_done_" not in seen and not strong_deals and all_deals:
        top_samples = sorted(all_deals, key=lambda x: x["discount"], reverse=True)[:3]
        send_telegram_msg(f"📡 <b>تقرير الفحص الأول:</b>\nتم فحص <b>{len(all_deals)}</b> منتج بنجاح! لا يوجد حالياً منتج تخطى {MIN_DISCOUNT}%، ودي أعلى 3 خصومات متاحة الآن كعينة تجربة:")
        for d in top_samples:
            send_deal_to_telegram(d)
            time.sleep(1)
        seen["_first_test_done_"] = 1

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
