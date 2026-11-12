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

# ================== تنظیمات ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "70956629:jvLERRmmQGU4FxpB4G3pl12fPynPElOSpaE")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
API_BASE = f"https://api.splus.ir/bot{BOT_TOKEN}"

FREE_LIMIT = 5  # تعداد لینک رایگان در ۲۴ ساعت

app = Flask(__name__)
profanity_filter = ProfanityFilter(WordList.persian_default())

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
    c.execute('''CREATE TABLE IF NOT EXISTS daily_limits
                 (chat_id INTEGER PRIMARY KEY,
                  count INTEGER DEFAULT 0,
                  reset_at TEXT)''')
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
    return row

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

def get_all_users():
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    c.execute('''SELECT u.chat_id, u.first_name, u.username, u.is_blocked,
                        l.is_active, l.expires_at, l.license_key
                 FROM users u LEFT JOIN licenses l ON u.chat_id = l.chat_id
                 ORDER BY u.chat_id''')
    rows = c.fetchall()
    conn.close()
    return rows

# ================== محدودیت روزانه ==================
def get_free_remaining(chat_id):
    """تعداد لینک رایگان باقی‌مانده در ۲۴ ساعت را برمی‌گرداند."""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    now = datetime.now()
    c.execute('SELECT count, reset_at FROM daily_limits WHERE chat_id=?', (chat_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return FREE_LIMIT
    count, reset_at = row
    reset = datetime.fromisoformat(reset_at)
    if now >= reset:
        conn.close()
        return FREE_LIMIT
    conn.close()
    return max(0, FREE_LIMIT - count)

def increment_free_usage(chat_id):
    """یک استفاده از سهمیه رایگان را ثبت می‌کند."""
    conn = sqlite3.connect('bot.db')
    c = conn.cursor()
    now = datetime.now()
    reset = now + timedelta(hours=24)
    c.execute('SELECT count, reset_at FROM daily_limits WHERE chat_id=?', (chat_id,))
    row = c.fetchone()
    if row:
        count, reset_at_str = row
        old_reset = datetime.fromisoformat(reset_at_str)
        if now >= old_reset:
            count = 1
            reset_str = reset.isoformat()
        else:
            count += 1
            reset_str = reset_at_str
        c.execute('UPDATE daily_limits SET count=?, reset_at=? WHERE chat_id=?',
                  (count, reset_str, chat_id))
    else:
        c.execute('INSERT INTO daily_limits (chat_id, count, reset_at) VALUES (?, 1, ?)',
                  (chat_id, reset.isoformat()))
    conn.commit()
    conn.close()

def is_license_active(chat_id):
    lic = get_user_license(chat_id)
    if lic and lic[0] == 1:
        return datetime.fromisoformat(lic[1]) > datetime.now()
    return False

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
            r = requests.post(url, data=data, files=files, timeout=180)
            return r.json()
    except Exception as e:
        print(f"[SEND VIDEO ERROR] {e}", flush=True)
        return {"ok": False}

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
        return False
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
                return False
            send(chat_id, "📥 در حال ارسال ویدیو...", reply_to)
            result = send_video(chat_id, video_file, reply_to=reply_to)
            return result.get("ok", False)
    except Exception as e:
        print(f"[REEL ERROR] {e}", flush=True)
        send(chat_id, f"⚠️ خطا: {str(e)[:100]}", reply_to)
        return False

# ================== حلقه اصلی ==================
def bot_loop():
    offset = 0
    print("BOT STARTED", flush=True)
    while True:
        try:
            res = api("getUpdates", offset=offset, timeout=30, limit=100, allowed_updates=["message"])
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

                # ✅ حالا چاپ امن است (بعد از تعریف m و txt و cid)
                print(f"[CHAT ID] {cid} | Name: {m['chat'].get('first_name', '')} | Text: {txt}", flush=True)

                if not txt:
                    continue

                chat = m.get("chat", {})
                save_user(cid, chat.get("first_name", ""), chat.get("username", ""))

                if is_user_blocked(cid):
                    continue

                # -------- دستورات ادمین --------
                if cid == ADMIN_ID:
                    if txt == "/users":
                        rows = get_all_users()
                        if not rows:
                            send(cid, "هیچ کاربری ثبت نشده.", m["message_id"])
                        else:
                            report = "👥 لیست کاربران:\n───────────────\n"
                            for r in rows[:30]:
                                blocked = "🚫" if r[3] else "✅"
                                lic_status = "💎 فعال" if (r[4] and r[5] and datetime.fromisoformat(r[5]) > datetime.now()) else "🆓 رایگان"
                                report += f"{blocked} {r[1]} | ID: {r[0]} | {lic_status}\n"
                            send(cid, report, m["message_id"])
                        continue
                    if txt.startswith("/activate "):
                        try:
                            target = int(txt.split(" ")[1])
                            key = activate_license(target, 30)
                            send(cid, f"✅ لایسنس ۳۰ روزه برای {target} فعال شد.\nکلید: {key}", m["message_id"])
                        except Exception:
                            send(cid, "فرمت: /activate 123456789", m["message_id"])
                        continue
                    if txt.startswith("/block "):
                        try:
                            block_user(int(txt.split(" ")[1]))
                            send(cid, "کاربر مسدود شد.", m["message_id"])
                        except Exception:
                            send(cid, "فرمت: /block 123456789", m["message_id"])
                        continue

                # -------- فیلتر الفاظ --------
                if profanity_filter.contains_profanity(txt):
                    send(cid, "⚠️ خطا: پیام حاوی الفاظ نامناسب است.", m["message_id"])
                    continue

                # -------- ریلز --------
                if is_instagram_reel(txt):
                    licensed = is_license_active(cid)
                    if not licensed:
                        remaining = get_free_remaining(cid)
                        if remaining <= 0:
                            send(cid,
                                 "⛔ محدودیت ۲۴ ساعته (۵ لینک رایگان) تمام شد.\n"
                                 "برای خرید اشتراک ماهانه به ادمین پیام دهید.",
                                 m["message_id"])
                            continue

                    success = download_and_send_reel(cid, txt, m["message_id"])
                    if success and not licensed:
                        increment_free_usage(cid)
                        remaining = get_free_remaining(cid)
                        send(cid,
                             f"✅ ارسال موفق.\n"
                             f"🔋 سهمیه رایگان باقی‌مانده: {remaining} از {FREE_LIMIT}",
                             m["message_id"])
                    continue

                # -------- سایر دستورات --------
                if txt.startswith("/start"):
                    licensed = is_license_active(cid)
                    if licensed:
                        lic = get_user_license(cid)
                        lic_msg = f"✅ اشتراک فعال تا {lic[1][:10]}"
                    else:
                        remaining = get_free_remaining(cid)
                        lic_msg = f"🆓 رایگان - سهمیه باقی‌مانده: {remaining} از {FREE_LIMIT}"
                    send(cid,
                         f"سلام! 👋\nوضعیت اشتراک: {lic_msg}\n\n"
                         "• متن انگلیسی بفرست تا ترجمه کنم\n"
                         "• بنویس «قیمت» تا قیمت‌های لحظه‌ای را بفرستم\n"
                         "• لینک ریلز اینستاگرام بفرست تا دانلود و ارسال کنم",
                         m["message_id"])
                elif "قیمت" in txt:
                    send(cid, get_prices(), m["message_id"])
                else:
                    send(cid, translate(txt), m["message_id"])

        except Exception as e:
            print(f"[LOOP ERROR] {e}", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    init_db()
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)