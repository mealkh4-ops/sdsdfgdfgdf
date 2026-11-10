import os
import re
import time
import threading
import requests
from datetime import datetime, timezone, timedelta
from flask import Flask
import jdatetime

BOT_TOKEN = os.environ.get("BOT_TOKEN", "70956629:jvLERRmmQGU4FxpB4G3pl12fPynPElOSpaE")
API_BASE = f"https://api.splus.ir/bot{BOT_TOKEN}"

app = Flask(__name__)

@app.route("/")
def health():
    return "alive"

# ---------- تشخیص انگلیسی ----------
def is_english(text):
    letters = re.findall(r"[A-Za-z]", text)
    all_letters = re.findall(r"[A-Za-z\u0600-\u06FF]", text)
    if not all_letters:
        return False
    return len(letters) / len(all_letters) > 0.5

# ---------- ترجمه ----خ------
import translators as ts

def translate(text, target="fa"):
    # لیست سرویس‌های جایگزین (به ترتیب اولویت)
    services = ["google" , "bing", "alibaba", "baidu", "youdao", ]
    
    for service in services:
        try:
            result = ts.translate_text(
                text,
                translator=service,
                from_language="en",
                to_language="fa"
            )
            if result:
                print(f"[OK] Translated by {service}", flush=True)
                return result
        except Exception as e:
            print(f"[{service} failed] {e}", flush=True)
            continue
    
    return "⚠️ ترجمه در حال حاضر در دسترس نیست. لطفاً بعداً تلاش کنید."

# ---------- تاریخ و ساعت ایران (شمسی) ----------
def iran_datetime_str():
    # ساعت ایران = UTC + 3:30
    tehran_tz = timezone(timedelta(hours=3, minutes=30))
    now = datetime.now(tehran_tz)
    jnow = jdatetime.datetime.fromgregorian(datetime=now)
    return jnow.strftime("%A %d %B %Y  -  %H:%M:%S")

# ---------- گرفتن قیمت‌ها ----------
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

        # سکه امامی: هم sekee و هم sekee_buy رو امتحان می‌کنیم
        full_coin = find("sekee", "sekee_buy", "sekke_emami")

        # نیم و ربع سکه: طبق اطلاعات، nim و rob درست هستن
        half_coin = find("nim", "sekke_nim")
        quarter = find("rob", "sekke_rob")

        return (
            "💰 قیمت‌های لحظه‌ای\n"
            "───────────────\n"
            f"💵 _دلار:           {usd} تومان\n"
            f"🥇 گرم طلا (۱۸):  {gold_gram} تومان\n"
            f"🪙 سکه کامل:       {full_coin} تومان\n"
            f"🪙 نیم سکه:        {half_coin} تومان\n"
            f"🪙 ربع سکه:        {quarter} تومان\n"
            "───────────────\n"
            f"🕐 {iran_datetime_str()} (به وقت ایران)"
        )
    except Exception as e:
        return f"⚠️ خطا در دریافت قیمت: {e}"
# ---------- API بات ----------
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

# ---------- حلقه ربات ----------
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

                if txt.startswith("/start"):
                    send(cid,
                         "سلام! 👋\n"
                         "• متن انگلیسی بفرست تا ترجمه کنم\n"
                         "• بنویس «قیمت» تا قیمت دلار، طلا و سکه رو بفرستم",
                         m["message_id"])

                elif "قیمت" in txt or "price" in txt.lower():
                    send(cid, get_prices(), m["message_id"])

                elif is_english(txt):
                    send(cid, translate(txt), m["message_id"])

                else:
                    send(cid,
                         "لطفاً انگلیسی بفرست تا ترجمه کنم، یا بنویس «قیمت».",
                         m["message_id"])

        except Exception as e:
            print(e, flush=True)
            time.sleep(3)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
