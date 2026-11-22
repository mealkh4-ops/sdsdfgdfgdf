import os
import re
import time
import threading
import requests
import tempfile
from datetime import datetime, timezone, timedelta
import jdatetime
from flask import Flask
import psycopg2
from psycopg2.extras import RealDictCursor
from persian_text_guard import ProfanityFilter, WordList
import translators as ts

# =================== تنظیمات ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "70956629:jvLERRmmQGU4FxpB4G3pl12fPynPElOSpaE")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "18351322"))
API_BASE = f"https://api.splus.ir/bot{BOT_TOKEN}"

# FastSaver API
FASTSAVER_API_KEY = os.environ.get("FASTSAVER_API_KEY", "fs_sk_8h1x6d2s5o4o2s7k7m1u6t3h2q4i")
FASTSAVER_ENDPOINT = "https://api.fastsaver.io/v1/fetch"

FREE_LIMIT = 5  # تعداد لینک رایگان در ۲۴ ساعت
DATABASE_URL = os.environ.get("DATABASE_URL")

app = Flask(__name__)
profanity_filter = ProfanityFilter(WordList.persian_default())

# ================== روت سلامت ==================
@app.route('/')
@app.route('/health')
@app.route('/Health')
def health_check():
    return "Bot is alive and running!", 200

# ================== دیتابیس (PostgreSQL) ==================
def get_db_connection():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL is not set!")
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    
    # جدول کاربران
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                 chat_id BIGINT PRIMARY KEY,
                 first_name TEXT,
                 username TEXT,
                 is_blocked INTEGER DEFAULT 0,
                 license_key TEXT,
                 license_expires_at TIMESTAMP WITH TIME ZONE
                 )''')
    
    # جدول تاریخچه دانلودها
    c.execute('''CREATE TABLE IF NOT EXISTS user_activity (
                 id SERIAL PRIMARY KEY,
                 chat_id BIGINT,
                 url TEXT,
                 action_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (chat_id) REFERENCES users(chat_id)
                 )''')
                 
    # جدول پیام‌های انگلیسی
    c.execute('''CREATE TABLE IF NOT EXISTS messages_log (
                 id SERIAL PRIMARY KEY,
                 chat_id BIGINT,
                 message_text TEXT,
                 sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (chat_id) REFERENCES users(chat_id)
                 )''')
    
    # جدول محدودیت روزانه (اختیاری برای شمارش سریع‌تر)
    c.execute('''CREATE TABLE IF NOT EXISTS daily_limits (
                 chat_id BIGINT PRIMARY KEY,
                 count INTEGER DEFAULT 0,
                 reset_at TIMESTAMP WITH TIME ZONE,
                 FOREIGN KEY (chat_id) REFERENCES users(chat_id)
                 )''')
                 
    conn.commit()
    c.close()
    conn.close()

def save_user(chat_id, first_name, username):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''INSERT INTO users (chat_id, first_name, username)
                 VALUES (%s, %s, %s)
                 ON CONFLICT(chat_id) DO UPDATE SET
                 first_name=EXCLUDED.first_name,
                 username=EXCLUDED.username''', (chat_id, first_name, username))
    conn.commit()
    c.close()
    conn.close()

def is_user_blocked(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT is_blocked FROM users WHERE chat_id=%s', (chat_id,))
    row = c.fetchone()
    c.close()
    conn.close()
    return row and row[0] == 1

def block_user(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('UPDATE users SET is_blocked=1 WHERE chat_id=%s', (chat_id,))
    conn.commit()
    c.close()
    conn.close()

def get_user_license(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT license_key, license_expires_at FROM users WHERE chat_id=%s', (chat_id,))
    row = c.fetchone()
    c.close()
    conn.close()
    return row

def activate_license(chat_id, days=30):
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    license_key = f"LIC-{chat_id}-{int(time.time())}"
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''UPDATE users SET license_key=%s, license_expires_at=%s WHERE chat_id=%s''',
              (license_key, expires, chat_id))
    conn.commit()
    c.close()
    conn.close()
    return license_key

def is_license_active(chat_id):
    lic = get_user_license(chat_id)
    if lic and lic[1]:
        return lic[1] > datetime.now(timezone.utc)
    return False

def get_all_users():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''SELECT chat_id, first_name, username, is_blocked, license_key, license_expires_at 
                 FROM users ORDER BY chat_id''')
    rows = c.fetchall()
    c.close()
    conn.close()
    return rows

# ================== محدودیت روزانه ==================
def get_free_remaining(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now(timezone.utc)
    c.execute('SELECT count, reset_at FROM daily_limits WHERE chat_id=%s', (chat_id,))
    row = c.fetchone()
    if not row:
        c.close()
        conn.close()
        return FREE_LIMIT
    count, reset_at = row
    if now >= reset_at:
        c.close()
        conn.close()
        return FREE_LIMIT
    c.close()
    conn.close()
    return max(0, FREE_LIMIT - count)

def increment_free_usage(chat_id):
    conn = get_db_connection()
    c = conn.cursor()
    now = datetime.now(timezone.utc)
    reset = now + timedelta(hours=24)
    c.execute('SELECT count, reset_at FROM daily_limits WHERE chat_id=%s', (chat_id,))
    row = c.fetchone()
    if row:
        count, reset_at = row
        if now >= reset_at:
            count = 1
            reset_time = reset
        else:
            count += 1
            reset_time = reset_at
        c.execute('UPDATE daily_limits SET count=%s, reset_at=%s WHERE chat_id=%s',
                  (count, reset_time, chat_id))
    else:
        c.execute('INSERT INTO daily_limits (chat_id, count, reset_at) VALUES (%s, 1, %s)',
                  (chat_id, reset))
    conn.commit()
    c.close()
    conn.close()

# ================== توابع لاگ‌گیری ==================
def log_download(chat_id, url):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT INTO user_activity (chat_id, url) VALUES (%s, %s)', (chat_id, url))
    conn.commit()
    c.close()
    conn.close()

def log_message(chat_id, text):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('INSERT INTO messages_log (chat_id, message_text) VALUES (%s, %s)', (chat_id, text))
    conn.commit()
    c.close()
    conn.close()

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
            f"🪙 سکه: {find('sekee', 'sekke_emami')} ریال\n"
            f"🪙 نیم: {find('nim', 'sekke_nim')} ریال\n"
            f"🪙 ربع: {find('rob', 'sekke_rob')} ریال\n───────────────\n"
            f"🕐 {iran_datetime_str()}"
        )
    except Exception as e:
        return f"⚠️ خطا: {e}"

# ================== ریلز با FastSaver ==================
def is_instagram_reel(text):
    return bool(re.search(r"instagram\.com/(reel|p)/", text))

def download_and_send_reel(chat_id, url, reply_to=None):
    try:
        resp = requests.get(
            FASTSAVER_ENDPOINT,
            params={"url": url},
            headers={"X-Api-Key": FASTSAVER_API_KEY},
            timeout=30
        )
        data = resp.json()

        if not data.get("ok"):
            error = data.get("detail", data.get("error", "خطای ناشناخته"))
            send(chat_id, f"⚠️ {error}", reply_to)
            return False

        media_type = data.get("type", "")
        if media_type == "album":
            items = data.get("items", [])
            if not items:
                send(chat_id, "⚠️ محتوایی پیدا نشد.", reply_to)
                return False
            download_url = items[0].get("download_url")
        elif media_type in ("video", "image"):
            download_url = data.get("download_url")
        else:
            send(chat_id, "⚠️ نوع محتوا پشتیبانی نمی‌شود.", reply_to)
            return False

        if not download_url:
            send(chat_id, "⚠️ لینک دانلود دریافت نشد.", reply_to)
            return False

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            video_resp = requests.get(download_url, stream=True, timeout=(10, 60))
            video_resp.raise_for_status()
            for chunk in video_resp.iter_content(chunk_size=65536):
                f.write(chunk)
            video_path = f.name

        send(chat_id, "📥 در حال ارسال ویدیو...", reply_to)
        result = send_video(chat_id, video_path, reply_to=reply_to)
        os.unlink(video_path)
        return result.get("ok", False)

    except requests.exceptions.Timeout:
        send(chat_id, "⚠️ زمان درخواست به پایان رسید.", reply_to)
        return False
    except requests.exceptions.RequestException as e:
        print(f"[FASTSAVER NETWORK ERROR] {e}", flush=True)
        send(chat_id, "⚠️ خطا در ارتباط با سرویس دانلود.", reply_to)
        return False
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
                                lic_status = "💎 فعال" if (r[4] and r[5] and r[5] > datetime.now(timezone.utc)) else "🆓 رایگان"
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
                    if success:
                        log_download(cid, txt)  # ثبت لینک دانلود شده
                        if not licensed:
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
                    # ثبت پیام انگلیسی قبل از ترجمه
                    log_message(cid, txt)
                    send(cid, translate(txt), m["message_id"])

        except Exception as e:
            print(f"[LOOP ERROR] {e}", flush=True)
            time.sleep(3)

# ================== اجرای برنامه ==================
if __name__ == "__main__":
    init_db()
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)