import os
import re
import time
import threading
import requests
from flask import Flask

BOT_TOKEN = os.environ.get("BOT_TOKEN", "70956629:jvLERRmmQGU4FxpB4G3pl12fPynPElOSpaE")
API_BASE = f"https://api.splus.ir/bot{BOT_TOKEN}"

app = Flask(__name__)

@app.route("/")
def health():
    return "alive"

def is_english(text):
    letters = re.findall(r"[A-Za-z]", text)
    all_letters = re.findall(r"[A-Za-z\u0600-\u06FF]", text)
    if not all_letters:
        return False
    return len(letters) / len(all_letters) > 0.5

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
                    send(cid, "سلام! متن انگلیسی بفرست تا ترجمه کنم.", m["message_id"])
                elif is_english(txt):
                    send(cid, translate(txt), m["message_id"])
                else:
                    send(cid, "لطفاً انگلیسی بفرست.", m["message_id"])
        except Exception as e:
            print(e, flush=True)
            time.sleep(3)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
