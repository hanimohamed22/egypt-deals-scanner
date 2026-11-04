import os
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from curl_cffi import requests as cureq

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("CHAT_ID", "").strip()
MIN_DISCOUNT = 40

SEEN_FILE = "seen_deals.json"

HEADERS = {
    "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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

# ================= 1. فاحص 2B (يعمل بكفاءة عالية) =================
def scan_2b():
    deals = []
    session = cureq.Session(impersonate="chrome120")
    urls = [
        "https://2b.com.eg/ar/home-appliances.html",
        "https://2b.com.eg/ar/computers.html",
        "https://2b.com.eg/ar/mobile-and-tablet.html",
        "https://2b.com.eg/ar/gaming.html"
    ]
    for url in urls:
        try:
            r = session.get(url, headers=HEADERS, timeout=25)
            soup = BeautifulSoup(r.text, "lxml")
            cards = soup.select(".product-item-info")
            print(f"  -> 2B ({url.split('/')[-1]}): كود {r.status_code} | تم فحص {len(cards)} منتج")
            for card in cards:
                link_el = card.select_one("a.product-item-link")
                if not link_el:
                    continue
                title = link_el.get_text(strip=True)
                full_url = link_el.get("href", "")
                
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

# ================= 2. فاحص جوميا (يعمل بكفاءة عالية) =================
def scan_jumia():
    deals = []
    urls = [
        "https://www.jumia.com.eg/ar/flash-sales/",
        "https://www.jumia.com.eg/ar/electronics/",
        "https://www.jumia.com.eg/ar/phones-tablets/"
    ]
    for url in urls:
        try:
            jina_url = f"https://r.jina.ai/{url}"
            r = requests.get(jina_url, headers={"Accept": "text/plain"}, timeout=30)
            lines = r.text.splitlines()
            print(f"  -> جوميا ({url.split('/')[-2]}): كود {r.status_code} | تم فحص القراءة")
            
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

# ================= 3. فاحص نون (عبر جينة لتخطي الحظر) =================
def scan_noon():
    deals = []
    urls = [
        "https://www.noon.com/egypt-ar/electronics/",
        "https://www.noon.com/egypt-ar/mobiles/"
    ]
    for url in urls:
        try:
            jina_url = f"https://r.jina.ai/{url}"
            r = requests.get(jina_url, headers={"Accept": "text/plain"}, timeout=30)
            lines = r.text.splitlines()
            print(f"  -> نون ({url.split('/')[-2]}): كود {r.status_code}")
            
            pattern = re.compile(r"\[([^\]]+)\]\((https://www\.noon\.com/egypt-ar/[^\)]+/p/)\)")
            for i, line in enumerate(lines):
                for match in pattern.finditer(line):
                    title, link = match.group(1), match.group(2)
                    if len(title) < 5:
                        continue
                    context_Block = " ".join(lines[max(0, i-2):min(len(lines), i+3)])
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
                            "store": "Noon EG 🟡",
                            "title": title,
                            "new_price": new_p,
                            "old_price": old_p,
                            "discount": disc,
                            "url": link,
                            "image": ""
                        })
        except Exception as e:
            print(f"[!] خطأ في نون: {e}")
    return deals

# ================= 4. فاحص أمازون (عبر جينة للتجاوز) =================
def scan_amazon():
    deals = []
    urls = [
        "https://www.amazon.eg/-/ar/gp/b-days",
        "https://www.amazon.eg/s?k=electronics&language=ar_AE"
    ]
    for url in urls:
        try:
            jina_url = f"https://r.jina.ai/{url}"
            r = requests.get(jina_url, headers={"Accept": "text/plain"}, timeout=30)
            lines = r.text.splitlines()
            print(f"  -> أمازون: كود {r.status_code}")
            
            pattern = re.compile(r"\[([^\]]+)\]\((https://www\.amazon\.eg/[^\)]+/dp/[A-Z0-9]{10}[^\)]*)\)")
            for i, line in enumerate(lines):
                for match in pattern.finditer(line):
                    title, link = match.group(1), match.group(2)
                    if len(title) < 5 or "amazon" in title.lower():
                        continue
                    context_Block = " ".join(lines[max(0, i-2):min(len(lines), i+3)])
                    arabic_map = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
                    norm_ctx = context_Block.translate(arabic_map)
                    
                    disc_m = re.search(r"-\s*(\d{2})\s*%", norm_ctx) or re.search(r"(\d{2})\s*%\s*خصم", norm_ctx)
                    prices = [clean_num(m.group(0)) for m in re.finditer(r"([\d,]+\.\d+|[\d,]+)\s*(?:جنيه|ج\.م|EGP)", norm_ctx)]
                    prices = [p for p in prices if p > 30]
                    
                    if prices and disc_m:
                        disc = int(disc_m.group(1) or disc_m.group(2))
                        new_p = min(prices)
                        old_p = max(prices) if len(set(prices)) > 1 else round(new_p / (1 - disc / 100))
                        clean_url = link.split("?")[0]
                        deals.append({
                            "id": clean_url,
                            "store": "Amazon EG 🛒",
                            "title": title,
                            "new_price": new_p,
                            "old_price": old_p,
                            "discount": disc,
                            "url": clean_url,
                            "image": ""
                        })
        except Exception as e:
            print(f"[!] خطأ في أمازون: {e}")
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

    unique_deals = list({d["id"]: d for d in all_deals}.values())
    strong_deals = [d for d in unique_deals if d["discount"] >= MIN_DISCOUNT]
    
    print(f"[+] إجمالي المنتجات المستخرجة بخصومات: {len(unique_deals)} | منها فوق {MIN_DISCOUNT}%: {len(strong_deals)}")

    if "_sample_sent_v3_" not in seen and unique_deals:
        top_samples = sorted(unique_deals, key=lambda x: x["discount"], reverse=True)[:3]
        send_telegram_msg(f"📡 <b>تقرير الرادار الشامل (أمازون، نون، جوميا، 2B):</b>\nتم استخراج <b>{len(unique_deals)}</b> عرض بنجاح! إليك أعلى 3 عروض مؤكدة:")
        for d in top_samples:
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
    print(f"[✓] تم إرسال {new_sent} عرض (40%+) وتحديث قاعدة البيانات.")

if __name__ == "__main__":
    main()
