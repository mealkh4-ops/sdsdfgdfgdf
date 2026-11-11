import os
import re
import time
import threading
import tempfile
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask
import jdatetime
from persian_text_guard import ProfanityFilter, WordList
import translators as ts
import instaloader

# ---------- تنظیمات ----------
BOT_TOKEN = os.environ.get("CJAGGI0PARDYRLWTDYMRRBRZXMEKDDWSROEWSDHGVFPZVPIOUFGBBBIVHNVVBPKN")
if not BOT_TOKEN:
    raise SystemExit("BOT_TOKEN environment variable is not set!")

API_BASE = f"https://botapi.rubika.ir/v3/{BOT_TOKEN}"

IG_USER = os.environ.get("IG_USER")           # اختیاری
IG_SESSION_FILE = os.environ.get("IG_SESSION") # اختیاری (مسیر session)

app = Flask(__name__)


@app.route("/")
def health():
    return "alive"


# ---------- فیلتر الفاظ ----------
profanity_filter = ProfanityFilter(WordList.persian_default())


def is_profane(text: str) -> bool:
    try:
        return profanity_filter.contains_profanity(text)
    except Exception:
        return False


# ---------- تشخیص انگلیسی ----------
def is_english(text: str) -> bool:
    letters = re.findall(r"[A-Za-z]", text)
    if len(letters) < 3:
        return False
    all_letters = re.findall(r"[A-Za-z\u0600-\u06FF]", text)
    if not all_letters:
        return False
    return len(letters) / len(all_letters) > 0.4


# ---------- تشخیص لینک اینستاگرام ----------
IG_PATTERN = re.compile(
    r"(?:instagram\.com|instagr\.am)/(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)


def extract_ig_shortcode(text: str):
    m = IG_PATTERN.search(text)
    return m.group(1) if m else None


# ---------- ترجمه ----------
def translate(text: str) -> str:
    services = ["google", "bing", "alibaba", "baidu", "youdao"]
    original = text.strip()
    for service in services:
        try:
            result = ts.translate_text(
                text,
                translator=service,
                from_language="en",
                to_language="fa",
            )
            if not result:
                continue
            result = result.strip()
            if not result or result == original:
                continue
            if is_profane(result):
                print(f"[FILTERED] {service} output contained profanity", flush=True)
                continue
            print(f"[OK] Translated by {service}", flush=True)
            return result
        except Exception as e:
            print(f"[{service} failed] {e}", flush=True)
            continue
    return "⚠️ ترجمه در حال حاضر در دسترس نیست یا محتوای نامناسب دارد."


# ---------- تاریخ ایران ----------
def iran_datetime_str() -> str:
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    now = datetime.now(tehran_tz)
    jnow = jdatetime.datetime.fromgregorian(datetime=now)
    return jnow.strftime("%A %d %B %Y  -  %H:%M:%S")


# ---------- قیمت‌ها ----------
def get_prices() -> str:
    try:
        r = requests.get(
            "https://call1.tgju.org/ajax.json",
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        cur = r.json().get("current", {})

        def find(*keys):
            for k in keys:
                p = cur.get(k, {}).get("p", "")
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
def api(method: str, **params) -> dict:
    try:
        r = requests.post(f"{API_BASE}/{method}", json=params, timeout=60)
        return r.json()
    except Exception as e:
        print(f"[API ERROR] {method}: {e}", flush=True)
        return {"ok": False}


def send(chat_id, text, reply_to=None):
    p = {"chat_id": chat_id, "text": text}
    if reply_to:
        p["reply_to_message_id"] = reply_to
    return api("sendMessage", **p)


# ---------- ارسال ویدیو (آپلود مرحله‌ای) ----------
def upload_and_send_video(chat_id, video_path, caption=None, reply_to=None):
    """
    روبیکا معمولاً آپلود مستقیم فایل قبول نمی‌کنه.
    اول آپلود می‌کنیم، file_id می‌گیریم، بعد sendFile/sendVideo صدا می‌زنیم.
    اگر API شما آپلود مستقیم داره، این تابع رو متناسب با docs اصلاح کن.
    """
    # 1) آپلود فایل — مسیر/اسم متد و فیلدها باید با docs روبیکا هماهنگ شه
    try:
        upload_url = f"{API_BASE}/uploadFile"
        with open(video_path, "rb") as f:
            files = {"file": ("video.mp4", f, "video/mp4")}
            data = {"type": "video"}
            up = requests.post(upload_url, data=data, files=files, timeout=300).json()
        if not up.get("ok"):
            print(f"[UPLOAD FAIL] {up}", flush=True)
            return {"ok": False, "raw": up}
        file_id = up.get("result", {}).get("file_id") or up.get("file_id")
        if not file_id:
            print(f"[UPLOAD NO FILE_ID] {up}", flush=True)
            return {"ok": False, "raw": up}
    except Exception as e:
        print(f"[UPLOAD ERROR] {e}", flush=True)
        return {"ok": False}

    # 2) ارسال با file_id
    payload = {"chat_id": chat_id, "file_id": file_id}
    if caption:
        payload["caption"] = caption
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    return api("sendFile", **payload)  # یا sendVideo — بسته به API


# ---------- دانلود و ارسال ریلز ----------
def _make_instaloader(tmpdir: str) -> instaloader.Instaloader:
    L = instaloader.Instaloader(
        download_videos=True,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        post_metadata_txt_pattern=None,
    )
    L.dirname_pattern = os.path.join(tmpdir, "{target}")

    # اگر session داریم، لود کن
    if IG_SESSION_FILE and os.path.exists(IG_SESSION_FILE):
        try:
            L.load_session_from_file(IG_USER, IG_SESSION_FILE)
            print("[IG] session loaded", flush=True)
        except Exception as e:
            print(f"[IG session load failed] {e}", flush=True)
    elif IG_USER:
        try:
            L.login(IG_USER, os.environ["IG_PASS"])
            print("[IG] logged in", flush=True)
        except Exception as e:
            print(f"[IG login failed] {e}", flush=True)
    else:
        print("[IG] no session — may fail on most reels", flush=True)

    return L


def _find_mp4(root_dir: str):
    for root, _, files in os.walk(root_dir):
        for f in files:
            if f.lower().endswith(".mp4"):
                return os.path.join(root, f)
    return None


def download_and_send_reel(chat_id, text, reply_to=None):
    shortcode = extract_ig_shortcode(text)
    if not shortcode:
        send(chat_id, "⚠️ لینک اینستاگرام معتبر نیست.", reply_to)
        return

    send(chat_id, "📥 در حال دانلود ویدیو از اینستاگرام...", reply_to)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            L = _make_instaloader(tmpdir)
            post = instaloader.Post.from_shortcode(L.context, shortcode)
            L.download_post(post, target=shortcode)

            video_file = _find_mp4(tmpdir)
            if not video_file:
                send(chat_id, "⚠️ ویدیویی برای ارسال پیدا نشد.", reply_to)
                return

            size_mb = os.path.getsize(video_file) / (1024 * 1024)
            if size_mb > 50:
                send(chat_id, f"⚠️ فایل خیلی بزرگه ({size_mb:.1f}MB).", reply_to)
                return

            result = upload_and_send_video(
                chat_id, video_file, caption=None, reply_to=reply_to
            )
            if not result.get("ok"):
                send(chat_id, "⚠️ خطا در ارسال ویدیو.", reply_to)

    except instaloader.exceptions.LoginRequiredException:
        send(chat_id, "⚠️ برای دانلود این پست، ادمین باید وارد اینستاگرام بشه.", reply_to)
    except instaloader.exceptions.ProfileNotExistsException:
        send(chat_id, "⚠️ این پست وجود نداره.", reply_to)
    except instaloader.exceptions.PrivateProfileNotFollowedException:
        send(chat_id, "⚠️ این پست از اکانت خصوصیه.", reply_to)
    except Exception as e:
        print(f"[REEL ERROR] {e}", flush=True)
        send(chat_id, f"⚠️ خطا در دانلود ریلز: {str(e)[:100]}", reply_to)


# ---------- نرمال‌سازی آپدیت روبیکا ----------
def normalize_update(u: dict):
    """
    روبیکا در وب‌هوک ساختاری مثل:
      {"update": {"type": "NewMessage", "chat_id": "...", "new_message": {...}}}
    برمی‌گردونه. برای getUpdates هم معمولاً همین شکل تو یه لیست هست.
    این تابع اون رو به dict ساده تبدیل می‌کنه.
    """
    upd = u.get("update") if isinstance(u, dict) else None
    if not upd:
        return None
    if upd.get("type") != "NewMessage":
        return None
    nm = upd.get("new_message") or {}
    text = (nm.get("text") or "").strip()
    if not text:
        return None
    return {
        "chat_id": upd.get("chat_id"),
        "message_id": nm.get("message_id"),
        "sender_id": nm.get("sender_id"),
        "text": text,
    }


# ---------- حلقه اصلی ----------
def bot_loop():
    start_id = None
    print("BOT STARTED", flush=True)

    while True:
        try:
            params = {}
            if start_id is not None:
                params["start_id"] = start_id
            res = api("getUpdates", **params)

            if not res.get("ok"):
                time.sleep(3)
                continue

            # روبیکا معمولاً result یا updates برمی‌گردونه
            updates = res.get("result") or res.get("updates") or []
            next_id = res.get("next_id") or res.get("start_id")

            for u in updates:
                info = normalize_update(u)
                if not info:
                    continue

                cid = info["chat_id"]
                txt = info["text"]
                mid = info["message_id"]

                # فیلتر الفاظ
                if is_profane(txt):
                    send(cid, "⚠️ پیام شما حاوی الفاظ نامناسب است.", mid)
                    continue

                # لینک اینستاگرام
                if extract_ig_shortcode(txt):
                    threading.Thread(
                        target=download_and_send_reel,
                        args=(cid, txt, mid),
                        daemon=True,
                    ).start()
                    continue

                if txt.startswith("/start"):
                    send(
                        cid,
                        "سلام! 👋\n"
                        "• متن انگلیسی بفرست تا ترجمه کنم\n"
                        "• بنویس «قیمت» تا قیمت دلار، طلا و سکه رو بفرستم\n"
                        "• لینک ریلز اینستاگرام بفرست تا دانلود و ارسال کنم",
                        mid,
                    )

                elif "قیمت" in txt or "price" in txt.lower():
                    send(cid, get_prices(), mid)

                elif is_english(txt):
                    send(cid, translate(txt), mid)

                else:
                    send(
                        cid,
                        "لطفاً انگلیسی بفرست تا ترجمه کنم، بنویس «قیمت»، "
                        "یا لینک ریلز اینستاگرام بفرست.",
                        mid,
                    )

            # ذخیره start_id برای درخواست بعدی
            if next_id is not None:
                start_id = next_id

        except Exception as e:
            print(f"[LOOP ERROR] {e}", flush=True)
            time.sleep(3)


# ---------- اجرا ----------
if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)