import os
import re
import json
import time
import html as htmllib
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
MIN_DISCOUNT = 40
SEEN_FILE = "seen_deals.json"

ARABIC_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

HEADERS_DESKTOP = {
    "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
}


def norm(text):
    return str(text).translate(ARABIC_MAP)


def clean_num(text):
    if not text:
        return 0.0
    m = re.search(r"\d+(?:\.\d+)?", norm(text).replace(",", ""))
    return float(m.group(0)) if m else 0.0


def save_debug(name, content):
    """يحفظ الصفحة لو الفحص رجع صفر منتجات عشان نشوف السبب"""
    with open(f"debug_{name}.html", "w", encoding="utf-8") as f:
        f.write(content)


# ================= Seen / Telegram =================
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
        seen_data = dict(list(seen_data.items())[-4000:])
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen_data, f, ensure_ascii=False, indent=2)


def send_telegram_msg(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"[!] تليجرام رفض الرسالة: {r.status_code} {r.text[:200]}")


def send_deal_to_telegram(deal):
    savings = deal["old_price"] - deal["new_price"]
    title = htmllib.escape(deal["title"][:150])
    url = htmllib.escape(deal["url"], quote=True)
    caption = (
        f"🔥 <b>خصم {deal['discount']}% من {deal['store']}</b>\n\n"
        f"📦 <b>المنتج:</b> {title}\n\n"
        f"💰 <b>السعر بعد الخصم:</b> {deal['new_price']:,.0f} ج.م\n"
        f"❌ <b>السعر قبل الخصم:</b> <s>{deal['old_price']:,.0f} ج.م</s>\n"
        f"💸 <b>قيمة التوفير:</b> {savings:,.0f} ج.م\n\n"
        f"🔗 <a href='{url}'>اضغط هنا لفتح صفحة المنتج والشراء</a>"
    )
    img = deal.get("image") or ""
    try:
        if img.startswith("http"):
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                json={"chat_id": CHAT_ID, "photo": img,
                      "caption": caption, "parse_mode": "HTML"},
                timeout=15,
            )
            if r.status_code == 200:
                return
        send_telegram_msg(caption)
    except Exception as e:
        print(f"[!] خطأ إرسال تليجرام: {e}")


# ================= 1. أمازون مصر =================
def scan_amazon():
    deals = []
    session = cureq.Session(impersonate="safari_ios")
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Accept-Language": "ar-EG,ar;q=0.9",
    }
    urls = [
        "https://www.amazon.eg/s?k=electronics&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
        "https://www.amazon.eg/s?k=phones&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
        "https://www.amazon.eg/s?k=laptop&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
        "https://www.amazon.eg/s?k=kitchen&rh=p_n_pct-off-with-tax%3A26901062031&language=ar_AE",
    ]
    for i, url in enumerate(urls, 1):
        try:
            r = session.get(url, headers=headers, timeout=25)
            if "Enter the characters" in r.text or "api-services-support@amazon" in r.text:
                print(f"  [!] أمازون ({i}) رجّعت CAPTCHA")
                continue

            soup = BeautifulSoup(r.text, "lxml")
            cards = [c for c in soup.select("div[data-asin]") if len(c.get("data-asin", "")) == 10]
            print(f"  -> أمازون ({i}): كود {r.status_code} | {len(cards)} كارت")
            if not cards:
                save_debug(f"amazon_{i}", r.text)

            for card in cards:
                asin = card.get("data-asin")
                text_all = norm(card.get_text(" | ", strip=True))

                title_el = card.select_one("h2")
                img_el = card.select_one("img.s-image") or card.select_one("img")
                title = title_el.get_text(strip=True) if title_el else (img_el.get("alt") or "" if img_el else "")
                if len(title) < 4:
                    continue

                price_matches = re.findall(
                    r"(?:ج\.م\.?|EGP)\s*([\d,]+(?:\.\d+)?)|([\d,]+(?:\.\d+)?)\s*(?:ج\.م\.?|EGP)",
                    text_all,
                )
                prices = [clean_num(m1 or m2) for m1, m2 in price_matches]
                prices = [p for p in prices if p >= 20]
                if not prices:
                    continue

                new_p = min(prices)
                old_p = max(prices) if len(set(prices)) > 1 else 0

                disc_match = re.search(r"-\s*(\d{2})\s*%|(\d{2})\s*%\s*(?:خصم|off)", text_all, re.I)
                disc = int(disc_match.group(1) or disc_match.group(2)) if disc_match else 0

                if old_p > new_p and disc == 0:
                    disc = round((old_p - new_p) / old_p * 100)
                elif disc > 0 and old_p <= new_p and disc < 95:
                    old_p = round(new_p / (1 - disc / 100))

                if disc > 0 and new_p > 0:
                    deals.append({
                        "id": f"amz_{asin}",
                        "store": "Amazon EG 🛒",
                        "title": title,
                        "new_price": new_p,
                        "old_price": old_p,
                        "discount": int(disc),
                        "url": f"https://www.amazon.eg/-/ar/dp/{asin}",
                        "image": img_el.get("src", "") if img_el else "",
                    })
        except Exception as e:
            print(f"[!] خطأ في أمازون: {e}")
    return deals


# ================= 2. 2B مصر (شغال، مع تحسينات بسيطة) =================
def scan_2b():
    deals = []
    urls = [
        "https://2b.com.eg/ar/home-appliances.html",
        "https://2b.com.eg/ar/home-appliances.html?p=2",
        "https://2b.com.eg/ar/computers.html",
        "https://2b.com.eg/ar/mobile-and-tablet.html",
        "https://2b.com.eg/ar/gaming.html",
    ]
    for url in urls:
        try:
            r = cureq.get(url, headers=HEADERS_DESKTOP, impersonate="chrome120", timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select(".product-item-info")
            print(f"  -> 2B ({url.split('/')[-1]}): كود {r.status_code} | {len(cards)} كارت")
            for card in cards:
                link_el = card.select_one("a.product-item-link")
                if not link_el:
                    continue
                title = link_el.get_text(strip=True)
                full_url = link_el.get("href", "")

                prices = []
                for p_el in card.select("[data-price-amount]"):
                    val = clean_num(p_el.get("data-price-amount"))
                    if val > 30:
                        prices.append(val)
                if len(set(prices)) >= 2:
                    new_p, old_p = min(prices), max(prices)
                    disc = round((old_p - new_p) / old_p * 100)
                    img_el = card.select_one("img.product-image-photo")
                    img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""
                    deals.append({
                        "id": full_url.split("?")[0],
                        "store": "2B Egypt 💻",
                        "title": title,
                        "new_price": new_p,
                        "old_price": old_p,
                        "discount": int(disc),
                        "url": full_url,
                        "image": img,
                    })
        except Exception as e:
            print(f"[!] خطأ في 2B: {e}")
    return deals


# ================= 3. نون مصر =================
def scan_noon():
    deals = []
    session = cureq.Session(impersonate="safari_ios")
    urls = [
        "https://www.noon.com/egypt-ar/electronics/",
        "https://www.noon.com/egypt-ar/mobiles/",
        "https://www.noon.com/egypt-ar/laptops/",
    ]
    for url in urls:
        try:
            r = session.get(url, timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select('a[href*="/p/"]')
            print(f"  -> نون ({url.split('/')[-2]}): كود {r.status_code} | {len(cards)} رابط")
            if not cards:
                save_debug(f"noon_{url.split('/')[-2]}", r.text)

            for card in cards:
                text = norm(card.get_text(" | ", strip=True))
                disc_match = re.search(r"(\d{1,2})\s*%", text)
                if not disc_match:
                    continue
                disc = int(disc_match.group(1))
                if not 5 <= disc <= 95:
                    continue

                prices = [clean_num(x) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
                prices = [p for p in prices if p > 50 and p != disc]
                if not prices:
                    continue

                new_p = min(prices)
                old_p = max(prices) if len(set(prices)) > 1 else round(new_p / (1 - disc / 100))

                href = card.get("href", "")
                full_url = f"https://www.noon.com{href}" if href.startswith("/") else href
                img_el = card.select_one("img")
                img = img_el.get("src", "") if img_el else ""
                title = (img_el.get("alt") if img_el and img_el.get("alt") else text.split("|")[0]).strip()

                deals.append({
                    "id": full_url.split("?")[0],
                    "store": "Noon EG 🟡",
                    "title": title,
                    "new_price": new_p,
                    "old_price": old_p,
                    "discount": disc,
                    "url": full_url,
                    "image": img,
                })
        except Exception as e:
            print(f"[!] خطأ في نون: {e}")
    return deals


# ================= 4. جوميا مصر (مباشرة بدل Jina) =================
def scan_jumia():
    deals = []
    urls = [
        "https://www.jumia.com.eg/ar/flash-sales/",
        "https://www.jumia.com.eg/ar/electronics/",
        "https://www.jumia.com.eg/ar/phones-tablets/",
    ]
    for url in urls:
        try:
            r = cureq.get(url, headers=HEADERS_DESKTOP, impersonate="chrome120", timeout=30)
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select("article.prd")
            print(f"  -> جوميا ({url.split('/')[-2]}): كود {r.status_code} | {len(cards)} كارت")
            if not cards:
                save_debug(f"jumia_{url.split('/')[-2]}", r.text)

            for art in cards:
                a = art.select_one("a.core")
                if not a or not a.get("href"):
                    continue
                href = a["href"]
                link = f"https://www.jumia.com.eg{href}" if href.startswith("/") else href

                name_el = art.select_one(".name")
                title = name_el.get_text(strip=True) if name_el else ""
                if len(title) < 4:
                    continue

                new_el = art.select_one(".prc")
                old_el = art.select_one(".old")
                disc_el = art.select_one("._dsct") or art.select_one(".bdg")

                new_p = clean_num(new_el.get_text()) if new_el else 0
                old_p = clean_num(old_el.get_text()) if old_el else 0
                disc = int(clean_num(disc_el.get_text())) if disc_el else 0

                if new_p <= 0:
                    continue
                if not disc and old_p > new_p:
                    disc = round((old_p - new_p) / old_p * 100)
                if disc <= 0:
                    continue
                if not old_p:
                    old_p = round(new_p / (1 - disc / 100))

                img_el = art.select_one("img")
                img = (img_el.get("data-src") or img_el.get("src") or "") if img_el else ""

                deals.append({
                    "id": link.split("?")[0],
                    "store": "Jumia EG 🟠",
                    "title": title,
                    "new_price": new_p,
                    "old_price": old_p,
                    "discount": disc,
                    "url": link,
                    "image": img,
                })
        except Exception as e:
            print(f"[!] خطأ في جوميا: {e}")
    return deals


# ================= Main =================
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

    print(f"[+] إجمالي: {len(unique_deals)} | فوق {MIN_DISCOUNT}%: {len(strong_deals)}")

    if "_sample_sent_v3_" not in seen and unique_deals:
        top = sorted(unique_deals, key=lambda x: x["discount"], reverse=True)[:3]
        send_telegram_msg(
            f"📡 <b>تقرير الرادار الحي:</b>\nتم استخراج <b>{len(unique_deals)}</b> عرض بخصومات. إليك أعلى 3:"
        )
        for d in top:
            send_deal_to_telegram(d)
            time.sleep(1)
        seen["_sample_sent_v3_"] = 1

    new_sent = 0
    for deal in strong_deals:
        deal_id = deal["id"]
        if deal_id not in seen or deal["new_price"] < seen[deal_id]:
            send_deal_to_telegram(deal)
            seen[deal_id] = deal["new_price"]
            new_sent += 1
            time.sleep(1.2)

    save_seen(seen)
    print(f"[✓] تم إرسال {new_sent} عرض وتحديث قاعدة البيانات.")


if __name__ == "__main__":
    main()
