import os
import re
import time
import threading
import requests
import tempfile
from datetime import datetime, timezone, timedelta
from flask import Flask
import jdatetime
from persian_text_guard import ProfanityFilter, WordList
import translators as ts
import instaloader

BOT_TOKEN = os.environ.get("BOT_TOKEN", "70956629:jvLERRmmQGU4FxpB4G3pl12fPynPElOSpaE")
API_BASE = f"https://api.splus.ir/bot{BOT_TOKEN}"

app = Flask(__name__)

@app.route("/")
def health():
    return "alive"

# ---------- فیلتر الفاظ رکیک ----------
profanity_filter = ProfanityFilter(WordList.persian_default())

# ---------- تشخیص انگلیسی ----------
def is_english(text):
    letters = re.findall(r"[A-Za-z]", text)
    all_letters = re.findall(r"[A-Za-z\u0600-\u06FF]", text)
    if not all_letters:
        return False
    return len(letters) / len(all_letters) > 0.5

# ---------- تشخیص لینک اینستاگرام ----------
def is_instagram_reel(text):
    # الگوی ساده برای تشخیص لینک ریلز اینستاگرام
    pattern = r"instagram\.com/(reel|p)/"
    return bool(re.search(pattern, text))

# ---------- ترجمه ----------
def translate(text, target="fa"):
    services = ["google", "bing", "alibaba", "baidu", "youdao"]
    for service in services:
        try:
            result = ts.translate_text(
                text,
                translator=service,
                from_language="en",
                to_language="fa"
            )
            if result:
                if profanity_filter.contains_profanity(result):
                    print(f"[FILTERED] {service} output contained profanity", flush=True)
                    continue
                print(f"[OK] Translated by {service}", flush=True)
                return result
        except Exception as e:
            print(f"[{service} failed] {e}", flush=True)
            continue
    return "⚠️ ترجمه در حال حاضر در دسترس نیست یا محتوای نامناسب دارد."

# ---------- تاریخ و ساعت ایران ----------
def iran_datetime_str():
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    now = datetime.now(tehran_tz)
    jnow = jdatetime.datetime.fromgregorian(datetime=now)
    return jnow.strftime("%A %d %B %Y  -  %H:%M:%S")

# ---------- قیمت‌ها ----------
def get_prices():
    try:
        r = requests.get(
            "https://call1.tgju.org/ajax.json",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        d = r.json()
        cur = d.get("current", {})

        def find(*keys):
            for k in keys:
                item = cur.get(k, {})
                p = item.get("p", "")
                if p and p != "0":
                    return p
            return "—"

        usd = find("price_dollar_rl", "dollar_rl")
        gold_gram = find("geram18")
        full_coin = find("sekke", "sekke_buy", "sekke_emami")
        half_coin = find("nim", "sekke_nim")
        quarter = find("rob", "sekke_rob")

        return (
            "💰 قیمت‌های لحظه‌ای\n"
            "───────────────\n"
            f"💵 دلار:           {usd} ریال\n"
            "───────────────\n"
            f"🥇 گرم طلا (۱۸):  {gold_gram} ریال\n"
            f"🪙 سکه کامل:       {full_coin} ریال\n"
            f"🪙 نیم سکه:        {half_coin} ریال\n"
            f"🪙 ربع سکه:        {quarter} ریال\n"
            "───────────────\n"
            f"🕐 {iran_datetime_str()}"
        )
    except Exception as e:
        return f"⚠️ خطا در دریافت قیمت: {e}"

# ---------- API ----------
def api(method, **params):
    try:
        r = requests.post(f"{API_BASE}/{method}", json=params, timeout=60)
        return r.json()
    except Exception as e:
        print(e, flush=True)
        return {"ok": False}

def send(chat_id, text, reply_to=None):
    p = {"chat_id": chat_id, "text": text}
    if reply_to:
        p["reply_to_message_id"] = reply_to
    return api("sendMessage", **p)

# ---------- ارسال ویدیو ----------
def send_video(chat_id, video_path, caption=None, reply_to=None):
    """ارسال فایل ویدیویی از مسیر محلی به سروش پلاس"""
    url = f"{API_BASE}/sendVideo"
    try:
        with open(video_path, 'rb') as f:
            files = {'video': f}
            data = {'chat_id': chat_id}
            if caption:
                data['caption'] = caption
            if reply_to:
                data['reply_to_message_id'] = reply_to
            
            r = requests.post(url, data=data, files=files, timeout=120)
            return r.json()
    except Exception as e:
        print(f"[SEND VIDEO ERROR] {e}", flush=True)
        return {"ok": False}

# ---------- دانلود و ارسال ریلز ----------
def download_and_send_reel(chat_id, url, reply_to=None):
    """دانلود ریلز اینستاگرام و ارسال آن به سروش پلاس"""
    L = instaloader.Instaloader(
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        post_metadata_txt_pattern="",
    )
    
    # استخراج shortcode از لینک
    # مثال: https://www.instagram.com/reel/ABC123/ -> ABC123
    match = re.search(r"instagram\.com/(?:reel|p)/([^/?]+)", url)
    if not match:
        send(chat_id, "⚠️ لینک اینستاگرام معتبر نیست.", reply_to)
        return
    
    shortcode = match.group(1)
    
    try:
        # ایجاد پوشه موقت برای دانلود
        with tempfile.TemporaryDirectory() as tmpdir:
            L.dirname_pattern = tmpdir
            
            # دریافت پست و دانلود
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            L.download_post(post, target=shortcode)
            
            # پیدا کردن فایل ویدیویی دانلود شده
            video_file = None
            for f in os.listdir(tmpdir):
                if f.endswith('.mp4'):
                    video_file = os.path.join(tmpdir, f)
                    break
            
            if not video_file:
                send(chat_id, "⚠️ ویدیویی برای ارسال پیدا نشد.", reply_to)
                return
            
            # ارسال ویدیو
            send(chat_id, "📥 در حال ارسال ویدیو...", reply_to)
            result = send_video(chat_id, video_file, reply_to=reply_to)
            
            if not result.get("ok"):
                send(chat_id, "⚠️ خطا در ارسال ویدیو.", reply_to)
                
    except Exception as e:
        print(f"[REEL ERROR] {e}", flush=True)
        send(chat_id, f"⚠️ خطا در دانلود ریلز: {str(e)[:100]}", reply_to)

# ---------- حلقه اصلی ----------
def bot_loop():
    offset = 0
    print("BOT STARTED", flush=True)
    while True:
        try:
            res = api("getUpdates", offset=offset, timeout=30,
                      limit=100, allowed_updates=["message"])
            if not res.get("ok"):
                time.sleep(3)
                continue

            for u in res.get("result", []):
                offset = u["update_id"] + 1
                m = u.get("message")
                if not m:
                    continue

                txt = (m.get("text") or "").strip()
                cid = m["chat"]["id"]

                if not txt:
                    continue

                # ✅ فیلتر الفاظ رکیک
                if profanity_filter.contains_profanity(txt):
                    send(cid, "⚠️ خطا: پیام شما حاوی الفاظ نامناسب است.", m["message_id"])
                    continue

                # ✅ تشخیص لینک ریلز اینستاگرام
                if is_instagram_reel(txt):
                    download_and_send_reel(cid, txt, m["message_id"])
                    continue

                if txt.startswith("/start"):
                    send(cid,
                         "سلام! 👋\n"
                         "• متن انگلیسی بفرست تا ترجمه کنم\n"
                         "• بنویس «قیمت» تا قیمت دلار، طلا و سکه رو بفرستم\n"
                         "• لینک ریلز اینستاگرام بفرست تا دانلود و ارسال کنم",
                         m["message_id"])

                elif "قیمت" in txt or "price" in txt.lower():
                    send(cid, get_prices(), m["message_id"])

                elif is_english(txt):
                    send(cid, translate(txt), m["message_id"])

                else:
                    send(cid,
                         "لطفاً انگلیسی بفرست تا ترجمه کنم، بنویس «قیمت»، یا لینک ریلز اینستاگرام بفرست.",
                         m["message_id"])

        except Exception as e:
            print(e, flush=True)
            time.sleep(3)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)