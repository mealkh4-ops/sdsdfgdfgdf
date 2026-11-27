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

# ================== تنظیمات ==================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "70956629:jvLERRmmQGU4FxpB4G3pl12fPynPElOSpaE")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "18351322"))
API_BASE = f"https://api.splus.ir/bot{BOT_TOKEN}"

FASTSAVER_API_KEY = os.environ.get("FASTSAVER_API_KEY", "fs_sk_8h1x6d2s5o4o2s7k7m1u6t3h2q4i")
FASTSAVER_ENDPOINT = "https://api.fastsaver.io/v1/fetch"

FREE_LIMIT = 5
DATABASE_URL = os.environ.get("DATABASE_URL")

app = Flask(__name__)
profanity_filter = ProfanityFilter(WordList.persian_default())

# ================== State ==================
USER_STATE = {}

STATE_MAIN = "main"
STATE_INSTA = "insta_wait"          # حالت واحد برای ریلز/استوری/پست
STATE_TRANSLATE_MENU = "translate_menu"
STATE_TRANSLATE_FA2EN = "translate_fa2en_wait"
STATE_TRANSLATE_EN2FA = "translate_en2fa_wait"
STATE_SUBSCRIBE = "subscribe"

# ================== کیبوردها ==================
MAIN_MENU = {
    "keyboard": [
        [{"text": "📥 دانلود از اینستاگرام"}],
        [{"text": "💰 نرخ دلار و طلا"}],
        [{"text": "🌐 ترجمه"}],
        [{"text": "🛒 خرید اشتراک"}]
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False
}

BACK_MENU = {
    "keyboard": [[{"text": "🔙 بازگشت به منو"}]],
    "resize_keyboard": True,
    "one_time_keyboard": False
}

TRANSLATE_MENU = {
    "keyboard": [
        [{"text": "🇮🇷 → 🇬🇧 ترجمه فارسی به انگلیسی"}],
        [{"text": "🇬🇧 → 🇮🇷 ترجمه انگلیسی به فارسی"}],
        [{"text": "🔙 بازگشت به منو"}]
    ],
    "resize_keyboard": True,
    "one_time_keyboard": False
}

SUBSCRIBE_MENU = {
    "keyboard": [[{"text": "🔙 بازگشت به منو"}]],
    "resize_keyboard": True,
    "one_time_keyboard": False
}

# ================== دکمه‌ها ==================
BTN_INSTA = "📥 دانلود از اینستاگرام"
BTN_PRICES = "💰 نرخ دلار و طلا"
BTN_TRANSLATE = "🌐 ترجمه"
BTN_SUBSCRIBE = "🛒 خرید اشتراک"
BTN_BACK = "🔙 بازگشت به منو"
BTN_FA2EN = "🇮🇷 → 🇬🇧 ترجمه فارسی به انگلیسی"
BTN_EN2FA = "🇬🇧 → 🇮🇷 ترجمه انگلیسی به فارسی"

# ================== روت سلامت ==================
@app.route('/')
@app.route('/health')
@app.route('/Health')
def health_check():
    return "Bot is alive and running!", 200

# ================== دیتابیس ==================
def get_db_connection():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL is not set!")
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                 chat_id BIGINT PRIMARY KEY,
                 first_name TEXT,
                 username TEXT,
                 is_blocked INTEGER DEFAULT 0,
                 license_key TEXT,
                 license_expires_at TIMESTAMP WITH TIME ZONE
                 )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_activity (
                 id SERIAL PRIMARY KEY,
                 chat_id BIGINT,
                 url TEXT,
                 action_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (chat_id) REFERENCES users(chat_id)
                 )''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages_log (
                 id SERIAL PRIMARY KEY,
                 chat_id BIGINT,
                 message_text TEXT,
                 sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                 FOREIGN KEY (chat_id) REFERENCES users(chat_id)
                 )''')
    c.execute('''CREATE TABLE IF NOT EXISTS daily_limits (
                 chat_id BIGINT PRIMARY KEY,
                 count INTEGER DEFAULT 0,
                 reset_at TIMESTAMP WITH TIME ZONE,
                 FOREIGN KEY (chat_id) REFERENCES users(chat_id)
                 )''')
    c.execute('''CREATE TABLE IF NOT EXISTS admin_broadcasts (
                 id SERIAL PRIMARY KEY,
                 chat_id BIGINT,
                 message_id BIGINT,
                 kind TEXT,
                 sent_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                 )''')
    conn.commit()
    c.close()
    conn.close()

def save_user(chat_id, first_name, username):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('SELECT 1 FROM users WHERE chat_id=%s', (chat_id,))
    exists = c.fetchone() is not None
    c.execute('''INSERT INTO users (chat_id, first_name, username)
                 VALUES (%s, %s, %s)
                 ON CONFLICT(chat_id) DO UPDATE SET
                 first_name=EXCLUDED.first_name,
                 username=EXCLUDED.username''', (chat_id, first_name, username))
    conn.commit()
    c.close()
    conn.close()
    return not exists

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
        c.close(); conn.close()
        return FREE_LIMIT
    count, reset_at = row
    c.close(); conn.close()
    if now >= reset_at:
        return FREE_LIMIT
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
    c.close(); conn.close()

# ================== لاگ ==================
def log_download(chat_id, url):
    conn = get_db_connection(); c = conn.cursor()
    c.execute('INSERT INTO user_activity (chat_id, url) VALUES (%s, %s)', (chat_id, url))
    conn.commit(); c.close(); conn.close()

def log_message(chat_id, text):
    conn = get_db_connection(); c = conn.cursor()
    c.execute('INSERT INTO messages_log (chat_id, message_text) VALUES (%s, %s)', (chat_id, text))
    conn.commit(); c.close(); conn.close()

# ================== توابع ادمین ==================
def save_admin_broadcast(chat_id, message_id, kind):
    conn = get_db_connection(); c = conn.cursor()
    c.execute('''INSERT INTO admin_broadcasts (chat_id, message_id, kind)
                 VALUES (%s, %s, %s)''', (chat_id, message_id, kind))
    conn.commit(); c.close(); conn.close()

def get_admin_broadcasts(kind=None, chat_id=None):
    conn = get_db_connection(); c = conn.cursor()
    if kind == "all":
        c.execute('''SELECT chat_id, message_id FROM admin_broadcasts
                     WHERE kind='all' ORDER BY sent_at DESC''')
    elif chat_id is not None:
        c.execute('''SELECT chat_id, message_id FROM admin_broadcasts
                     WHERE chat_id=%s ORDER BY sent_at DESC''', (chat_id,))
    else:
        c.execute('''SELECT chat_id, message_id FROM admin_broadcasts ORDER BY sent_at DESC''')
    rows = c.fetchall(); c.close(); conn.close()
    return rows

def delete_admin_broadcasts(kind=None, chat_id=None):
    targets = get_admin_broadcasts(kind=kind, chat_id=chat_id)
    deleted, failed = 0, 0
    for cid, mid in targets:
        res = api("deleteMessage", chat_id=cid, message_id=mid)
        if res.get("ok"):
            deleted += 1
        else:
            failed += 1
    conn = get_db_connection(); c = conn.cursor()
    if kind == "all":
        c.execute("DELETE FROM admin_broadcasts WHERE kind='all'")
    elif chat_id is not None:
        c.execute("DELETE FROM admin_broadcasts WHERE chat_id=%s", (chat_id,))
    conn.commit(); c.close(); conn.close()
    return deleted, failed

def broadcast_to_all(text):
    users = get_all_users()
    sent, failed = 0, 0
    for r in users:
        cid = r[0]
        if r[3]:
            continue
        res = send(cid, text)
        if res.get("ok"):
            mid = res.get("result", {}).get("message_id")
            if mid:
                save_admin_broadcast(cid, mid, "all")
            sent += 1
        else:
            failed += 1
        time.sleep(0.05)
    return sent, failed

def send_to_one(chat_id, text):
    res = send(chat_id, text)
    if res.get("ok"):
        mid = res.get("result", {}).get("message_id")
        if mid:
            save_admin_broadcast(chat_id, mid, "single")
        return True
    return False

def factor_last_24h(chat_id):
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    conn = get_db_connection(); c = conn.cursor()
    c.execute('''SELECT url, action_time FROM user_activity
                 WHERE chat_id=%s AND action_time >= %s
                 ORDER BY action_time DESC''', (chat_id, since))
    downloads = c.fetchall()
    c.execute('''SELECT message_text, sent_at FROM messages_log
                 WHERE chat_id=%s AND sent_at >= %s
                 ORDER BY sent_at DESC''', (chat_id, since))
    messages = c.fetchall()
    c.close(); conn.close()

    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    def fmt(dt):
        return dt.astimezone(tehran_tz).strftime("%Y-%m-%d %H:%M")

    lines = [f"📊 فعالیت ۲۴ ساعت اخیر کاربر {chat_id}", "───────────────"]
    lines.append(f"📥 دانلودها ({len(downloads)}):")
    if downloads:
        for url, t in downloads:
            lines.append(f"• {fmt(t)} — {url[:60]}")
    else:
        lines.append("• بدون دانلود")
    lines.append("───────────────")
    lines.append(f"✍️ پیام‌های ثبت‌شده ({len(messages)}):")
    if messages:
        for txt, t in messages:
            lines.append(f"• {fmt(t)} — {txt[:60]}")
    else:
        lines.append("• بدون پیام")
    return "\n".join(lines)

# ================== API سروش ==================
def api(method, **params):
    try:
        r = requests.post(f"{API_BASE}/{method}", json=params, timeout=60)
        return r.json()
    except Exception as e:
        print(e, flush=True)
        return {"ok": False}

def send(chat_id, text, reply_to=None, keyboard=None):
    p = {"chat_id": chat_id, "text": text}
    if reply_to:
        p["reply_to_message_id"] = reply_to
    if keyboard:
        p["reply_markup"] = keyboard
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

def send_photo(chat_id, photo_path, caption=None, reply_to=None):
    url = f"{API_BASE}/sendPhoto"
    try:
        with open(photo_path, 'rb') as f:
            files = {'photo': f}
            data = {'chat_id': chat_id}
            if caption: data['caption'] = caption
            if reply_to: data['reply_to_message_id'] = reply_to
            r = requests.post(url, data=data, files=files, timeout=120)
            return r.json()
    except Exception as e:
        print(f"[SEND PHOTO ERROR] {e}", flush=True)
        return {"ok": False}

# ================== ترجمه ==================
def translate(text, from_lang="en", to_lang="fa"):
    services = ["google", "bing", "alibaba", "baidu", "youdao"]
    for service in services:
        try:
            result = ts.translate_text(text, translator=service,
                                       from_language=from_lang, to_language=to_lang)
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

def greeting_time():
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    h = datetime.now(tehran_tz).hour
    if 5 <= h < 12:
        return "صبح بخیر"
    if 12 <= h < 17:
        return "ظهر بخیر"
    if 17 <= h < 21:
        return "عصر بخیر"
    return "شب بخیر"

# ================== قیمت ==================
def get_prices():
    try:
        r = requests.get("https://call1.tgju.org/ajax.json", timeout=15,
                         headers={"User-Agent": "Mozilla/5.0"})
        cur = r.json().get("current", {})
        def find(*keys):
            for k in keys:
                p = cur.get(k, {}).get("p", "")
                if p and p != "0":
                    return p
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

# ================== اینستاگرام (ریلز / استوری / پست) ==================
def is_instagram_url(text):
    return bool(re.search(r"instagram\.com/(reel|reels|p|tv|stories)/", text))

def is_instagram_story(text):
    return bool(re.search(r"instagram\.com/stories/", text))

def download_and_send_instagram(chat_id, url, reply_to=None):
    """دانلود ریلز/پست/استوری. در صورت خطا None برمی‌گرداند (یعنی خطا در دانلود)."""
    tmp_files = []
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
            return None

        media_type = data.get("type", "")

        # ساخت لیست آیتم‌های قابل دانلود
        items = []
        if media_type == "album":
            for it in data.get("items", []):
                durl = it.get("download_url")
                if durl:
                    items.append((durl, it.get("type", "video")))
        elif media_type in ("video", "image"):
            durl = data.get("download_url")
            if durl:
                items.append((durl, media_type))

        if not items:
            send(chat_id, "⚠️ محتوایی پیدا نشد.", reply_to)
            return None

        send(chat_id, "📥 در حال دریافت و ارسال محتوا...", reply_to)

        sent_any = False
        for durl, itype in items:
            is_video = (itype == "video")
            suffix = ".mp4" if is_video else ".jpg"

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                media_resp = requests.get(durl, stream=True, timeout=(10, 120))
                media_resp.raise_for_status()
                for chunk in media_resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                fpath = f.name
            tmp_files.append(fpath)

            if is_video:
                result = send_video(chat_id, fpath, reply_to=reply_to)
            else:
                result = send_photo(chat_id, fpath, reply_to=reply_to)

            if result.get("ok"):
                sent_any = True
            else:
                print(f"[SEND MEDIA FAILED] {result}", flush=True)

        return True if sent_any else None

    except requests.exceptions.Timeout:
        send(chat_id, "⚠️ زمان درخواست به پایان رسید.", reply_to)
        return None
    except requests.exceptions.RequestException as e:
        print(f"[FASTSAVER NETWORK ERROR] {e}", flush=True)
        send(chat_id, "⚠️ خطا در ارتباط با سرویس دانلود.", reply_to)
        return None
    except Exception as e:
        print(f"[INSTA ERROR] {e}", flush=True)
        send(chat_id, f"⚠️ خطا: {str(e)[:100]}", reply_to)
        return None
    finally:
        for fp in tmp_files:
            try:
                os.unlink(fp)
            except Exception:
                pass

# ================== هندلر اصلی ==================
def handle_message(cid, txt, msg_id, chat_meta):
    is_new = save_user(cid, chat_meta.get("first_name", ""), chat_meta.get("username", ""))

    if is_user_blocked(cid):
        return

    # -------- دستورات ادمین --------
    if cid == ADMIN_ID:
        if txt == "/users":
            rows = get_all_users()
            if not rows:
                send(cid, "هیچ کاربری ثبت نشده.", msg_id)
            else:
                report = "👥 لیست کاربران:\n───────────────\n"
                for r in rows[:30]:
                    blocked = "🚫" if r[3] else "✅"
                    lic_status = "💎 فعال" if (r[4] and r[5] and r[5] > datetime.now(timezone.utc)) else "🆓 رایگان"
                    report += f"{blocked} {r[1]} | ID: {r[0]} | {lic_status}\n"
                send(cid, report, msg_id)
            return

        if txt.startswith("/activate "):
            try:
                target = int(txt.split(" ")[1])
                key = activate_license(target, 30)
                send(cid, f"✅ لایسنس ۳۰ روزه برای {target} فعال شد.\nکلید: {key}", msg_id)
            except Exception:
                send(cid, "فرمت: /activate 123456789", msg_id)
            return

        if txt.startswith("/block "):
            try:
                block_user(int(txt.split(" ")[1]))
                send(cid, "کاربر مسدود شد.", msg_id)
            except Exception:
                send(cid, "فرمت: /block 123456789", msg_id)
            return

        if txt.startswith("/all"):
            body = txt[len("/all"):].strip()
            if not body:
                send(cid, "فرمت: /all متن پیام", msg_id); return
            s, f = broadcast_to_all(body)
            send(cid, f"📤 ارسال شد به {s} کاربر. ناموفق: {f}", msg_id)
            return

        if txt.startswith("/send"):
            parts = txt.split(maxsplit=2)
            if len(parts) < 3:
                send(cid, "فرمت: /send chatid متن پیام", msg_id); return
            try:
                target = int(parts[1])
            except ValueError:
                send(cid, "chatid باید عدد باشد.", msg_id); return
            ok = send_to_one(target, parts[2])
            send(cid, "✅ ارسال شد." if ok else "⚠️ ارسال ناموفق.", msg_id)
            return

        if txt.strip() == "/delete all":
            d, f = delete_admin_broadcasts(kind="all")
            send(cid, f"🗑 حذف‌شده: {d} | ناموفق: {f}", msg_id)
            return

        if txt.startswith("/delete"):
            parts = txt.split()
            if len(parts) < 2:
                send(cid, "فرمت: /delete all  یا  /delete chatid", msg_id); return
            if parts[1] == "all":
                d, f = delete_admin_broadcasts(kind="all")
                send(cid, f"🗑 حذف‌شده: {d} | ناموفق: {f}", msg_id)
            else:
                try:
                    target = int(parts[1])
                except ValueError:
                    send(cid, "chatid باید عدد باشد.", msg_id); return
                d, f = delete_admin_broadcasts(chat_id=target)
                send(cid, f"🗑 حذف‌شده برای {target}: {d} | ناموفق: {f}", msg_id)
            return

        if txt.startswith("/factor"):
            parts = txt.split()
            if len(parts) < 2:
                send(cid, "فرمت: /factor chatid", msg_id); return
            try:
                target = int(parts[1])
            except ValueError:
                send(cid, "chatid باید عدد باشد.", msg_id); return
            send(cid, factor_last_24h(target), msg_id)
            return

    # -------- فیلتر الفاظ --------
    if profanity_filter.contains_profanity(txt):
        send(cid, "⚠️ خطا: پیام حاوی الفاظ نامناسب است.", msg_id, MAIN_MENU)
        return

    state = USER_STATE.get(cid, STATE_MAIN)

    # -------- /start --------
    if txt.startswith("/start"):
        USER_STATE[cid] = STATE_MAIN
        licensed = is_license_active(cid)
        if licensed:
            lic = get_user_license(cid)
            lic_msg = f"✅ اشتراک فعال تا {lic[1][:10]}"
        else:
            remaining = get_free_remaining(cid)
            lic_msg = f"🆓 رایگان - سهمیه باقی‌مانده: {remaining} از {FREE_LIMIT}"

        if is_new:
            name = chat_meta.get("first_name", "دوست عزیز")
            welcome = (
                f"{greeting_time()} {name} عزیز 🌟\n"
                f"به ربات ما خوش آمدید!\n"
                f"🕐 {iran_datetime_str()}\n───────────────\n"
                f"وضعیت اشتراک: {lic_msg}\n\n"
                "از منوی زیر یک گزینه انتخاب کنید:"
            )
        else:
            welcome = (
                f"سلام دوباره {chat_meta.get('first_name', 'دوست عزیز')} 👋\n"
                f"وضعیت اشتراک: {lic_msg}\n\n"
                "از منوی زیر یک گزینه انتخاب کنید:"
            )
        send(cid, welcome, msg_id, MAIN_MENU)
        return

    # -------- دکمه بازگشت --------
    if txt == BTN_BACK:
        USER_STATE[cid] = STATE_MAIN
        send(cid, "🏠 به منوی اصلی بازگشتید.", msg_id, MAIN_MENU)
        return

    # -------- منوی اصلی --------
    if txt == BTN_INSTA:
        USER_STATE[cid] = STATE_INSTA
        send(cid,
             "📥 لطفاً لینک ریلز، پست یا استوری اینستاگرام را ارسال کنید.\n\n"
             "✅ لینک‌های پشتیبانی‌شده:\n"
             "• ریلز: instagram.com/reel/...\n"
             "• پست: instagram.com/p/...\n"
             "• استوری: instagram.com/stories/...\n\n"
             "💡 می‌توانید چندین لینک را به صورت متوالی ارسال کنید.",
             msg_id, BACK_MENU)
        return

    if txt == BTN_PRICES:
        USER_STATE[cid] = STATE_MAIN
        send(cid, get_prices(), msg_id, MAIN_MENU)
        return

    if txt == BTN_TRANSLATE:
        USER_STATE[cid] = STATE_TRANSLATE_MENU
        send(cid, "🌐 لطفاً نوع ترجمه را انتخاب کنید:", msg_id, TRANSLATE_MENU)
        return

    if txt == BTN_SUBSCRIBE:
        USER_STATE[cid] = STATE_SUBSCRIBE
        send(cid,
             "🛒 برای خرید اشتراک ماهانه لطفاً به آیدی زیر پیام دهید:\n\n"
             "👉 @akh_133",
             msg_id, SUBSCRIBE_MENU)
        return

    # -------- زیرمنوی ترجمه --------
    if state == STATE_TRANSLATE_MENU:
        if txt == BTN_FA2EN:
            USER_STATE[cid] = STATE_TRANSLATE_FA2EN
            send(cid, "✍️ متن فارسی خود را ارسال کنید تا به انگلیسی ترجمه شود.", msg_id, BACK_MENU)
            return
        if txt == BTN_EN2FA:
            USER_STATE[cid] = STATE_TRANSLATE_EN2FA
            send(cid, "✍️ متن انگلیسی خود را ارسال کنید تا به فارسی ترجمه شود.", msg_id, BACK_MENU)
            return
        send(cid, "⚠️ لطفاً یکی از گزینه‌های منوی ترجمه را انتخاب کنید.", msg_id, TRANSLATE_MENU)
        return

    # -------- دریافت متن برای ترجمه --------
    if state == STATE_TRANSLATE_FA2EN:
        result = translate(txt, from_lang="fa", to_lang="en")
        USER_STATE[cid] = STATE_TRANSLATE_MENU
        send(cid, f"🌐 ترجمه:\n\n{result}", msg_id, TRANSLATE_MENU)
        return

    if state == STATE_TRANSLATE_EN2FA:
        log_message(cid, txt)
        result = translate(txt, from_lang="en", to_lang="fa")
        USER_STATE[cid] = STATE_TRANSLATE_MENU
        send(cid, f"🌐 ترجمه:\n\n{result}", msg_id, TRANSLATE_MENU)
        return

    # -------- دریافت لینک اینستاگرام (ریلز/پست/استوری) --------
    if state == STATE_INSTA:
        if not is_instagram_url(txt):
            send(cid,
                 "⚠️ خطا: لینک نامعتبر است.\n"
                 "لطفاً لینک ریلز، پست یا استوری اینستاگرام ارسال کنید.\n"
                 "مثال: https://www.instagram.com/reel/XXXXX/",
                 msg_id, BACK_MENU)
            return

        licensed = is_license_active(cid)
        if not licensed:
            remaining = get_free_remaining(cid)
            if remaining <= 0:
                send(cid,
                     "⛔ محدودیت ۲۴ ساعته (۵ لینک رایگان) تمام شد.\n"
                     "برای خرید اشتراک به منوی «خرید اشتراک» مراجعه کنید.",
                     msg_id, MAIN_MENU)
                USER_STATE[cid] = STATE_MAIN
                return

        success = download_and_send_instagram(cid, txt, msg_id)

        # ⚠️ نکته مهم: حالت را روی STATE_INSTA نگه می‌داریم تا کاربر
        # بتواند بدون بازگشت به منو، پشت سر هم لینک بفرستد.
        USER_STATE[cid] = STATE_INSTA

        if success:
            log_download(cid, txt)
            if not licensed:
                increment_free_usage(cid)
                remaining = get_free_remaining(cid)
                send(cid,
                     f"✅ ارسال موفق.\n"
                     f"🔋 سهمیه رایگان باقی‌مانده: {remaining} از {FREE_LIMIT}\n\n"
                     f"📎 می‌توانید لینک بعدی را ارسال کنید.",
                     msg_id, BACK_MENU)
            else:
                send(cid,
                     "✅ ارسال موفق.\n\n📎 می‌توانید لینک بعدی را ارسال کنید.",
                     msg_id, BACK_MENU)
        else:
            # حتی در صورت خطا، حالت را در همان بخش نگه می‌داریم
            send(cid,
                 "🔁 می‌توانید دوباره تلاش کنید یا لینک دیگری ارسال کنید.",
                 msg_id, BACK_MENU)
        return

    # -------- حالت خرید اشتراک --------
    if state == STATE_SUBSCRIBE:
        send(cid,
             "⚠️ لطفاً فقط از منوی زیر استفاده کنید یا بازگشت بزنید.",
             msg_id, SUBSCRIBE_MENU)
        return

    # -------- پیام خارج از منو --------
    send(cid,
         "⚠️ لطفاً یکی از گزینه‌های منو را انتخاب کنید.",
         msg_id, MAIN_MENU)

# ================== حلقه اصلی ==================
def bot_loop():
    offset = 0
    print("BOT STARTED", flush=True)
    while True:
        try:
            res = api("getUpdates", offset=offset, timeout=30, limit=100,
                      allowed_updates=["message"])
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
                print(f"[CHAT ID] {cid} | Name: {m['chat'].get('first_name','')} | Text: {txt}", flush=True)
                try:
                    handle_message(cid, txt, m["message_id"], m.get("chat", {}))
                except Exception as e:
                    print(f"[HANDLE ERROR] {e}", flush=True)
                    try:
                        send(cid, f"⚠️ خطای داخلی: {str(e)[:80]}", m["message_id"], MAIN_MENU)
                    except Exception:
                        pass

        except Exception as e:
            print(f"[LOOP ERROR] {e}", flush=True)
            time.sleep(3)

# ================== اجرا ==================
if __name__ == "__main__":
    init_db()
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)