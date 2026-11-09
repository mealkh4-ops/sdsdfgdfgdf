import os
import re
import time
import threading
import requests
from datetime import datetime
from flask import Flask

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

# ---------- ترجمه ----------
def translate(text):
    try:
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": "en|fa"},
            timeout=15,
        )
        d = r.json()
        if d.get("responseStatus") == 200:
            return d["responseData"]["translatedText"]
        return "ترجمه نشد."
    except Exception as e:
        return f"خطا: {e}"

# ---------- گرفتن قیمت‌ها ----------
def get_prices():
    try:
        r = requests.get("https://call1.tgju.org/ajax.json", timeout=15)
        d = r.json()
        cur = d.get("current", {})

        def val(key):
            item = cur.get(key, {})
            p = item.get("p", "")
            return p if p else "—"

        usd       = val("price_dollar_rl")
        gold_gram = val("geram18")            # گرم طلای ۱۸ عیار
        full_coin = val("sekke-e-emami")      # سکه کامل (امامی)
        half_coin = val("sekke-nim")          # نیم سکه
        quarter   = val("sekke-rob")          # ربع سکه

        now = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")

        return (
            "💰 قیمت‌های لحظه‌ای\n"
            "───────────────\n"
            f"💵 دلار:          {usd} تومان\n"
            f"🥇 گرم طلا (۱۸): {gold_gram} تومان\n"
            f"🪙 سکه کامل:      {full_coin} تومان\n"
            f"🪙 نیم سکه:       {half_coin} تومان\n"
            f"🪙 ربع سکه:       {quarter} تومان\n"
            "───────────────\n"
            f"🕐 ساعت: {now}"
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

                # /start
                if txt.startswith("/start"):
                    send(cid,
                         "سلام! 👋\n"
                         "• متن انگلیسی بفرست تا ترجمه کنم\n"
                         "• بنویس «قیمت» تا قیمت دلار، طلا و سکه رو بفرستم",
                         m["message_id"])

                # قیمت
                elif "قیمت" in txt or "price" in txt.lower():
                    send(cid, get_prices(), m["message_id"])

                # انگلیسی → ترجمه
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
