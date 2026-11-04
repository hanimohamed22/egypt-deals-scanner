import os
import re
import json
import time
import random
import atexit
import urllib.parse
import html as htmllib
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq
from playwright.sync_api import sync_playwright

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
PROXY = os.getenv("PROXY_URL", "").strip() or None
MIN_DISCOUNT = int(os.getenv("MIN_DISCOUNT", "40"))
SEEN_FILE = "seen_deals.json"

ARABIC_MAP = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
HEADERS = {"Accept-Language": "ar-EG,ar;q=0.9,en;q=0.7"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
REPORT = []


def norm(text):
    return str(text or "").translate(ARABIC_MAP)


def clean_num(text):
    m = re.search(r"\d+(?:\.\d+)?", norm(text).replace(",", ""))
    return float(m.group(0)) if m else 0.0


def save_debug(name, content):
    safe = re.sub(r"[^\w]+", "_", name)
    with open(f"debug_{safe}.html", "w", encoding="utf-8") as f:
        f.write(content or "")


# ================= Browser (Playwright) =================
_pw = None
_browser = None
_ctx = None


def get_ctx():
    global _pw, _browser, _ctx
    if _ctx is None:
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        _ctx = _browser.new_context(
            locale="ar-EG",
            timezone_id="Africa/Cairo",
            user_agent=UA,
            viewport={"width": 1366, "height": 900},
        )
        # نوقف الصور والخطوط والفيديو عشان السرعة
        _ctx.route("**/*", lambda route: route.abort()
                   if route.request.resource_type in ("image", "font", "media")
                   else route.continue_())
    return _ctx


def close_browser():
    global _pw, _browser, _ctx
    try:
        if _browser:
            _browser.close()
        if _pw:
            _pw.stop()
    except Exception:
        pass
    _pw = _browser = _ctx = None


atexit.register(close_browser)


def fetch_browser(url, wait_selector=None):
    """يرجّع (html, error) باستخدام متصفح حقيقي"""
    page = get_ctx().new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=15000)
            except Exception:
                pass
        for _ in range(4):  # lazy loading
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(700)
        html_text = page.content()
    except Exception as e:
        return "", str(e)[:80]
    finally:
        page.close()

    if "Enter the characters" in html_text or "api-services-support@amazon" in html_text:
        return "", "CAPTCHA"
    return html_text, None


# ================= Fetch عادي (curl_cffi) =================
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


def parse_amazon(html_text):
    soup = BeautifulSoup(html_text, "lxml")
    deals = []
    for card in soup.select("div[data-asin]"):
        asin = card.get("data-asin", "")
        if len(asin) != 10:
            continue
        title_el = card.select_one("h2")
        img_el = card.select_one("img.s-image") or card.select_one("img")
        title = title_el.get_text(strip=True) if title_el else (
            (img_el.get("alt") or "") if img_el else "")
        if len(title) < 4:
            continue

        new_el = card.select_one(".a-price:not(.a-text-price) .a-offscreen")
        old_el = card.select_one(".a-price.a-text-price .a-offscreen")
        new_p = clean_num(new_el.get_text()) if new_el else 0
        old_p = clean_num(old_el.get_text()) if old_el else 0
        if new_p < 20:
            continue

        if not old_p:
            m = re.search(r"-\s*(\d{1,2})\s*%", norm(card.get_text(" ", strip=True)))
            if not m:
                continue
            disc = int(m.group(1))
            old_p = round(new_p / (1 - disc / 100))
        elif old_p > new_p:
            disc = round((old_p - new_p) / old_p * 100)
        else:
            continue

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
    return deals


def parse_noon(html_text):
    soup = BeautifulSoup(html_text, "lxml")
    deals, seen_links = [], set()
    for a in soup.select('a[href*="/p/"]'):
        href = a.get("href", "")
        full_url = f"https://www.noon.com{href}" if href.startswith("/") else href
        key = full_url.split("?")[0]
        if not key or key in seen_links:
            continue

        text = norm(a.get_text(" | ", strip=True))
        m = re.search(r"(\d{1,2})\s*%", text)
        if not m:
            continue
        disc = int(m.group(1))
        if not 5 <= disc <= 95:
            continue

        prices = [clean_num(x) for x in re.findall(r"\d[\d,]*(?:\.\d+)?", text)]
        prices = [p for p in prices if p > 50 and p != disc]
        if not prices:
            continue

        new_p = min(prices)
        old_p = max(prices) if len(set(prices)) > 1 else round(new_p / (1 - disc / 100))
        img_el = a.select_one("img")
        img = img_el.get("src", "") if img_el else ""
        title = (img_el.get("alt") if img_el and img_el.get("alt")
                 else text.split("|")[0]).strip()

        seen_links.add(key)
        deals.append({
            "id": key,
            "store": "Noon EG 🟡",
            "title": title,
            "new_price": new_p,
            "old_price": old_p,
            "discount": disc,
            "url": full_url,
            "image": img,
        })
    return deals


def parse_jumia(html_text):
    soup = BeautifulSoup(html_text, "lxml")
    deals = []
    for art in soup.select("article.prd"):
        a = art.select_one("a.core")
        if not a or not a.get("href"):
            continue
        href = a["href"]
        link = f"https://www.jumia.com.eg{href}" if href.startswith("/") else href

        name_el = art.select_one(".name")
        title = name_el.get_text(strip=True) if name_el else ""
        if len(title) < 4:
            continue

        prc = art.select_one(".prc")
        old = art.select_one(".old")
        new_p = clean_num(prc.get_text()) if prc else 0
        old_p = clean_num(old.get_text()) if old else 0
        disc_el = art.select_one("._dsct") or art.select_one(".bdg")
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
    return deals


# ================= Sources =================
# كل مصدر: (الاسم، الروابط، الـ parser، المحرك، selector ينتظره المتصفح)
SOURCES = [
    ("2B", [
        "https://2b.com.eg/ar/home-appliances.html",
        "https://2b.com.eg/ar/home-appliances.html?p=2",
        "https://2b.com.eg/ar/computers.html",
        "https://2b.com.eg/ar/mobile-and-tablet.html",
        "https://2b.com.eg/ar/gaming.html",
    ], parse_2b, "http", None),
    ("Amazon", [
        "https://www.amazon.eg/s?k=electronics&rh=p_n_pct-off-with-tax%3A26901062031",
        "https://www.amazon.eg/s?k=phones&rh=p_n_pct-off-with-tax%3A26901062031",
        "https://www.amazon.eg/s?k=laptop&rh=p_n_pct-off-with-tax%3A26901062031",
        "https://www.amazon.eg/s?k=kitchen&rh=p_n_pct-off-with-tax%3A26901062031",
    ], parse_amazon, "browser", "div[data-asin]"),
    ("Noon", [
        "https://www.noon.com/egypt-ar/electronics/",
        "https://www.noon.com/egypt-ar/mobiles/",
        "https://www.noon.com/egypt-ar/laptops/",
    ], parse_noon, "browser", 'a[href*="/p/"]'),
    ("Jumia", [
        "https://www.jumia.com.eg/ar/flash-sales/",
        "https://www.jumia.com.eg/ar/electronics/",
        "https://www.jumia.com.eg/ar/phones-tablets/",
    ], parse_jumia, "browser", "article.prd"),
]


def run_source(name, urls, parser, engine, wait_selector):
    deals = []
    for url in urls:
        p = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(p.query).get("k", [None])[0]
        short = q or p.path.strip("/").split("/")[-1][:25] or p.netloc

        if engine == "browser":
            html_text, err = fetch_browser(url, wait_selector)
        else:
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
    try:
        for name, urls, parser, engine, wait_sel in SOURCES:
            print(f"[*] جاري فحص {name}...")
            all_deals.extend(run_source(name, urls, parser, engine, wait_sel))
    finally:
        close_browser()

    unique = list({d["id"]: d for d in all_deals}.values())
    strong = [d for d in unique if d["discount"] >= MIN_DISCOUNT]
    print(f"[+] إجمالي: {len(unique)} | فوق {MIN_DISCOUNT}%: {len(strong)}")

    new_deals = [d for d in strong
                 if d["id"] not in seen or d["new_price"] < seen[d["id"]]]
    for deal in new_deals:
        send_deal(deal)
        seen[deal["id"]] = deal["new_price"]
        time.sleep(1.2)

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
