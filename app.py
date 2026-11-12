import os
import re
import time
import threading
import requests
import tempfile
import sqlite3
from datetime import datetime, timezone, timedelta
from flask import Flask
import jdatetime
from persian_text_guard import ProfanityFilter, WordList
import translators as ts
import instaloader
from pyrate_limiter import Duration, Rate, Limiter, SQLiteBucket

# ================== تنظیمات ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "70956629:jvLERRmmQGU4FxpB4G3pl12fPynPElOSpaE")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))  # شناسه چت ادمین را در Render ست کنید
API_BASE = f"https://api.splus.ir/bot{BOT_TOKEN}"

app = Flask(__name__)

# ================== فیلتر و ابزارها ==================
profanity_filter = ProfanityFilter(WordList.persian_default())

# محدودیت ۵ درخواست در ۲۴ ساعت (Leaky Bucket)
rate = Rate(5, Duration.DAY)
bucket = SQLiteBucket.init_from_file([rate], db_path="limits.db")
limiter = Limiter(bucket)


# ================== دیتابیس ==================
def init_db():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (chat_id INTEGER PRIMARY KEY,
                  first_name TEXT,
                  username TEXT,
                  is_blocked INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE IF NOT EXISTS licenses
                 (chat_id INTEGER PRIMARY KEY,
                  is_active INTEGER DEFAULT 0,
                  expires_at TEXT,
                  license_key TEXT)''')
    conn.commit()
    conn.close()

def save_user(chat_id, first_name, username):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''INSERT INTO users (chat_id, first_name, username)
                 VALUES (?, ?, ?)
                 ON CONFLICT(chat_id) DO UPDATE SET
                 first_name=excluded.first_name,
                 username=excluded.username''', (chat_id, first_name, username))
    conn.commit()
    conn.close()

def is_user_blocked(chat_id):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT is_blocked FROM users WHERE chat_id=?', (chat_id,))
    row = c.fetchone()
    conn.close()
    return row and row[0] == 1

def block_user(chat_id):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('UPDATE users SET is_blocked=1 WHERE chat_id=?', (chat_id,))
    conn.commit()
    conn.close()

def get_user_license(chat_id):
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('SELECT is_active, expires_at, license_key FROM licenses WHERE chat_id=?', (chat_id,))
    row = c.fetchone()
    conn.close()
    return row  # (is_active, expires_at, license_key)

def activate_license(chat_id, days=30):
    expires = (datetime.now() + timedelta(days=days)).isoformat()
    license_key = f"LIC-{chat_id}-{int(time.time())}"
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''INSERT INTO licenses (chat_id, is_active, expires_at, license_key)
                 VALUES (?, 1, ?, ?)
                 ON CONFLICT(chat_id) DO UPDATE SET
                 is_active=1, expires_at=excluded.expires_at, license_key=excluded.license_key''',
              (chat_id, expires, license_key))
    conn.commit()
    conn.close()
    return license_key

def get_all_licenses():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''SELECT u.chat_id, u.first_name, u.username, l.is_active, l.expires_at, l.license_key
                 FROM users u LEFT JOIN licenses l ON u.chat_id = l.chat_id
                 ORDER BY u.chat_id''')
    rows = c.fetchall()
    conn.close()
    return rows


# ================== API سروش ==================
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

def send_video(chat_id, video_path, caption=None, reply_to=None):
    url = f"{API_BASE}/sendVideo"
    try:
        with open(video_path, 'rb') as f:
            files = {'video': f}
            data = {'chat_id': chat_id}
            if caption: data['caption'] = caption
            if reply_to: data['reply_to_message_id'] = reply_to
            r = requests.post(url, data=data, files=files, timeout=120)
            return r.json()
    except Exception as e:
        print(f"[SEND VIDEO ERROR] {e}", flush=True)
        return {"ok": False}


# ================== محدودیت و لایسنس ==================
def can_send_reel(chat_id):
    """بررسی اینکه آیا کاربر مجاز به ارسال ریلز است یا نه."""
    lic = get_user_license(chat_id)
    if lic and lic[0] == 1:  # لایسنس فعال
        expires = datetime.fromisoformat(lic[1])
        if expires > datetime.now():
            return True, "license"
        else:
            # لایسنس منقضی شده
            pass
    
    # بررسی محدودیت رایگان ۲۴ ساعته
    try:
        limiter.try_acquire(str(chat_id))
        return True, "free"
    except Exception:
        return False, "limit"


# ================== ترجمه ==================
def translate(text, target="fa"):
    services = ["google", "bing", "alibaba", "baidu", "youdao"]
    for service in services:
        try:
            result = ts.translate_text(text, translator=service, from_language="en", to_language="fa")
            if result:
                if profanity_filter.contains_profanity(result):
                    continue
                return result
        except Exception:
            continue
    return "⚠️ ترجمه در دسترس نیست."

# ================== تاریخ ==================
def iran_datetime_str():
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    now = datetime.now(tehran_tz)
    jnow = jdatetime.datetime.fromgregorian(datetime=now)
    return jnow.strftime("%A %d %B %Y  -  %H:%M:%S")

# ================== قیمت ==================
def get_prices():
    try:
        r = requests.get("https://call1.tgju.org/ajax.json", timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        cur = r.json().get("current", {})
        def find(*keys):
            for k in keys:
                p = cur.get(k, {}).get("p", "")
                if p and p != "0": return p
            return "—"
        return (
            "💰 قیمت‌های لحظه‌ای\n───────────────\n"
            f"💵 دلار: {find('price_dollar_rl', 'dollar_rl')} ریال\n───────────────\n"
            f"🥇 گرم طلا: {find('geram18')} ریال\n"
            f"🪙 سکه: {find('sekee', 'sekee_buy', 'sekke_emami')} ریال\n"
            f"🪙 نیم: {find('nim', 'sekke_nim')} ریال\n"
            f"🪙 ربع: {find('rob', 'sekke_rob')} ریال\n───────────────\n"
            f"🕐 {iran_datetime_str()}"
        )
    except Exception as e:
        return f"⚠️ خطا: {e}"

# ================== ریلز ==================
def is_instagram_reel(text):
    return bool(re.search(r"instagram\.com/(reel|p)/", text))

def download_and_send_reel(chat_id, url, reply_to=None):
    L = instaloader.Instaloader(download_videos=True, download_video_thumbnails=False,
                                download_geotags=False, download_comments=False,
                                save_metadata=False, post_metadata_txt_pattern="")
    match = re.search(r"instagram\.com/(?:reel|p)/([^/?]+)", url)
    if not match:
        send(chat_id, "⚠️ لینک معتبر نیست.", reply_to)
        return
    shortcode = match.group(1)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            L.dirname_pattern = tmpdir
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            L.download_post(post, target=shortcode)
            video_file = None
            for f in os.listdir(tmpdir):
                if f.endswith('.mp4'):
                    video_file = os.path.join(tmpdir, f)
                    break
            if not video_file:
                send(chat_id, "⚠️ ویدیو پیدا نشد.", reply_to)
                return
            send(chat_id, "📥 در حال ارسال ویدیو...", reply_to)
            send_video(chat_id, video_file, reply_to=reply_to)
    except Exception as e:
        print(f"[REEL ERROR] {e}", flush=True)
        send(chat_id, f"⚠️ خطا: {str(e)[:100]}", reply_to)

# ================== حلقه اصلی ==================
def bot_loop():
    offset = 0
    print("BOT STARTED", flush=True)
    while True:
        try:
            res = api("getUpdates", offset=offset, timeout=30, limit=100, allowed_updates=["message"])
            if not res.get("ok"):
                time.sleep(3); continue

            for u in res.get("result", []):
                offset = u["update_id"] + 1
                m = u.get("message")
                print(f"[CHAT ID] {cid} | Name: {m['chat'].get('first_name', '')} | Text: {txt}", flush=True)

                if not m: continue
                txt = (m.get("text") or "").strip()
                cid = m["chat"]["id"]
                
                if not txt: continue

                chat = m.get("chat", {})
                save_user(cid, chat.get("first_name", ""), chat.get("username", ""))

                if is_user_blocked(cid):
                    continue

                # -------- دستورات ادمین --------
                if cid == ADMIN_ID:
                    if txt == "/users":
                        rows = get_all_licenses()
                        report = "👥 کاربران:\n"
                        for r in rows[:30]:
                            status = "✅" if r[3] else "⏳"
                            report += f"{status} {r[1]} | ID: {r[0]} | لایسنس: {r[5] or '—'}\n"
                        send(cid, report, m["message_id"])
                        continue
                    if txt.startswith("/activate "):
                        try:
                            target = int(txt.split(" ")[1])
                            key = activate_license(target, 30)
                            send(cid, f"✅ لایسنس برای {target} فعال شد.\nکلید: {key}", m["message_id"])
                        except:
                            send(cid, "فرمت: /activate 123456789", m["message_id"])
                        continue
                    if txt.startswith("/block "):
                        try:
                            block_user(int(txt.split(" ")[1]))
                            send(cid, "کاربر مسدود شد.", m["message_id"])
                        except:
                            send(cid, "فرمت: /block 123456789", m["message_id"])
                        continue

                # -------- فیلتر الفاظ --------
                if profanity_filter.contains_profanity(txt):
                    send(cid, "⚠️ خطا: الفاظ نامناسب.", m["message_id"])
                    continue

                # -------- ریلز --------
                if is_instagram_reel(txt):
                    allowed, reason = can_send_reel(cid)
                    if not allowed:
                        send(cid, "⛔ محدودیت ۲۴ ساعته (۵ لینک) تمام شد.\nبرای اشتراک به ادمین پیام دهید.", m["message_id"])
                        continue
                    # اگر لایسنس فعال بود، محدودیت را اعمال نکن
                    if reason == "free":
                        pass  # محدودیت در can_send_reel اعمال شده
                    download_and_send_reel(cid, txt, m["message_id"])
                    continue

                # -------- سایر دستورات --------
                if txt.startswith("/start"):
                    lic = get_user_license(cid)
                    lic_status = "✅ فعال" if (lic and lic[0] and datetime.fromisoformat(lic[1]) > datetime.now()) else "⏳ رایگان"
                    send(cid,
                         f"سلام! 👋\nوضعیت: {lic_status}\n"
                         "• انگلیسی بفرست تا ترجمه کنم\n• «قیمت» برای قیمت‌ها\n• لینک ریلز اینستاگرام بفرست (۵ بار در ۲۴ ساعت رایگان)",
                         m["message_id"])
                elif "قیمت" in txt:
                    send(cid, get_prices(), m["message_id"])
                else:
                    send(cid, translate(txt), m["message_id"])

        except Exception as e:
            print(e, flush=True); time.sleep(3)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)