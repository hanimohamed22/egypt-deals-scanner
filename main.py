import os
import re
import json
import time
import random
import html as htmllib
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PROXY = os.getenv("PROXY_URL", "").strip() or None
MIN_DISCOUNT = int(os.getenv("MIN_DISCOUNT", "40"))
SEEN_FILE = "seen_deals.json"

ARABIC_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
HEADERS = {"Accept-Language": "ar-EG,ar;q=0.9,en;q=0.7"}
REPORT = []  # حالة كل مصدر في التشغيل ده


def norm(text):
    return str(text).translate(ARABIC_MAP)


def clean_num(text):
    m = re.search(r"\d+(?:\.\d+)?", norm(text or "").replace(",", ""))
    return float(m.group(0)) if m else 0.0


def save_debug(name, content):
    with open(f"debug_{name}.html", "w", encoding="utf-8") as f:
        f.write(content)


# ================= Fetch موحد =================
def fetch(url, impersonate="chrome120", retries=3):
    """يرجّع (html, error). لو نجح error = None"""
    proxies = {"http": PROXY, "https": PROXY} if PROXY else None
    err = "unknown"
    for attempt in range(1, retries + 1):
        try:
            r = cureq.get(url, headers=HEADERS, impersonate=impersonate,
                          proxies=proxies, timeout=30)
            if "Enter the characters" in r.text or "api-services-support@amazon" in r.text:
                err = "CAPTCHA"
            elif r.status_code == 200:
                return r.text, None
            else:
                err = f"HTTP {r.status_code}"
        except Exception as e:
            err = str(e)[:80]
        if attempt < retries:
            time.sleep(4 * attempt + random.random() * 2)
    return "", err


# ================= Seen / Telegram =================
def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_seen(seen):
    if len(seen) > 4000:
        seen = dict(list(seen.items())[-4000:])
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=2)


def send_telegram_msg(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("[!] BOT_TOKEN أو CHAT_ID فاضي")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=15,
    )
    if r.status_code != 200:
        print(f"[!] تليجرام رفض: {r.status_code} {r.text[:200]}")


def send_deal(deal):
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
    if img.startswith("http"):
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                json={"chat_id": CHAT_ID, "photo": img,
                      "caption": caption, "parse_mode": "HTML"},
                timeout=15,
            )
            if r.status_code == 200:
                return
        except Exception as e:
            print(f"[!] خطأ sendPhoto: {e}")
    send_telegram_msg(caption)


# ================= Parsers =================
def parse_2b(html_text):
    soup = BeautifulSoup(html_text, "lxml")
    deals = []
    for card in soup.select(".product-item-info"):
        link_el = card.select_one("a.product-item-link")
        if not link_el:
            continue
        title = link_el.get_text(strip=True)
        full_url = link_el.get("href", "")
        prices = [clean_num(p.get("data-price-amount"))
                  for p in card.select("[data-price-amount]")]
        prices = [p for p in prices if p > 30]
        if len(set(prices)) < 2:
            continue
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
    return deals

# parse_amazon / parse_noon / parse_jumia تتضاف هنا بنفس الشكل:
# كل واحدة ترجع list من الـ dicts نفس اللي فوق.


# ================= Sources =================
SOURCES = [
    ("2B", [
        "https://2b.com.eg/ar/home-appliances.html",
        "https://2b.com.eg/ar/home-appliances.html?p=2",
        "https://2b.com.eg/ar/computers.html",
        "https://2b.com.eg/ar/mobile-and-tablet.html",
        "https://2b.com.eg/ar/gaming.html",
    ], parse_2b),
    # ("Amazon", [...], parse_amazon),
    # ("Noon", [...], parse_noon),
    # ("Jumia", [...], parse_jumia),
]


def run_source(name, urls, parser):
    deals = []
    for url in urls:
        short = url.split("/")[-1][:25]
        html_text, err = fetch(url)
        if err:
            REPORT.append(f"❌ {name} ({short}): {err}")
            print(f"  [!] {name} {short}: {err}")
            continue
        try:
            found = parser(html_text)
        except Exception as e:
            REPORT.append(f"⚠️ {name} ({short}): خطأ parser {str(e)[:60]}")
            continue
        if not found:
            save_debug(f"{name}_{short}", html_text)
        REPORT.append(f"✅ {name} ({short}): {len(found)}")
        print(f"  -> {name} ({short}): {len(found)} عرض")
        deals.extend(found)
        time.sleep(random.uniform(2, 4))
    return deals


def main():
    seen = load_seen()
    all_deals = []

    for name, urls, parser in SOURCES:
        print(f"[*] جاري فحص {name}...")
        all_deals.extend(run_source(name, urls, parser))

    unique = list({d["id"]: d for d in all_deals}.values())
    strong = [d for d in unique if d["discount"] >= MIN_DISCOUNT]
    print(f"[+] إجمالي: {len(unique)} | فوق {MIN_DISCOUNT}%: {len(strong)}")

    new_deals = [d for d in strong
                 if d["id"] not in seen or d["new_price"] < seen[d["id"]]]
    for deal in new_deals:
        send_deal(deal)
        seen[deal["id"]] = deal["new_price"]
        time.sleep(1.2)

    # تقرير الحالة: يتبعت لو فيه فشل، أو مرة في اليوم لو عايز
    failures = [r for r in REPORT if not r.startswith("✅")]
    if failures or os.getenv("ALWAYS_REPORT") == "1":
        send_telegram_msg(
            "📊 <b>تقرير التشغيل:</b>\n" + "\n".join(REPORT) +
            f"\n\n🔥 عروض جديدة: {len(new_deals)} | إجمالي فوق {MIN_DISCOUNT}%: {len(strong)}"
        )

    save_seen(seen)
    print(f"[✓] تم إرسال {len(new_deals)} عرض.")


if __name__ == "__main__":
    main()
