import os
import json
import hmac
import hashlib
import sqlite3
from urllib.parse import parse_qsl
from flask import Flask, request, jsonify, Response
import requests

app = Flask(__name__)

# ================== تنظیمات ==================
# این مقادیر را در Render به عنوان Environment Variable تنظیم کنید
APP_TOKEN = os.environ.get("EITAA_APP_TOKEN", "80377164:Qlas&(-2Dexs(-jJNLf7-r?xGhy-3alyx]-jC1n{f-cYZJvi-dncKts-$e3,~p-o&qX)t-$y/CqY-LHZbl7-4JVaVA-2GRm5*-3aX7Wo-P{RfsY-AQ2wAC-w8N?nM-Mm1B1m-d*EgTe-HzUK8.-Ny67EB-J4Wwvc-uKKD#i-2tg,OF-koT5jO-WTq[Vv-4uSFbo-1brgqv-UBaI!i-Z6/P84-L")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "a")
DATABASE = "users.db"

if not APP_TOKEN:
    raise RuntimeError("EITAA_APP_TOKEN environment variable is not set!")
if not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_PASSWORD environment variable is not set!")

# ================== دیتابیس ==================
def init_db():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        chat_id INTEGER PRIMARY KEY,
        first_name TEXT,
        username TEXT,
        registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def add_user(chat_id, first_name="", username=""):
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("""INSERT INTO users (chat_id, first_name, username)
                 VALUES (?, ?, ?)
                 ON CONFLICT(chat_id) DO UPDATE SET
                    first_name=excluded.first_name,
                    username=excluded.username""",
              (chat_id, first_name, username))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()
    c.execute("SELECT chat_id FROM users")
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return users

# ================== اعتبارسنجی initData ==================
def verify_init_data(init_data_str, bot_token):
    try:
        params = dict(parse_qsl(init_data_str, keep_blank_values=True))
        received_hash = params.pop('hash', None)
        if not received_hash:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if hmac.compare_digest(calculated_hash, received_hash):
            return json.loads(params.get('user', '{}'))
        return None
    except Exception as e:
        print(f"Error verifying initData: {e}")
        return None

# ================== مسیرهای برنامک ==================
@app.route('/')
def index():
    html_content = """
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>برنامک من</title>
        <script src="https://developer.eitaa.com/eitaa-web-app.js"></script>
        <script>
            if (window.Eitaa && window.Eitaa.WebApp) {
                const initData = window.Eitaa.WebApp.initData;
                if (initData) {
                    fetch(window.location.origin + '/register-user', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ initData: initData })
                    })
                    .then(r => r.json())
                    .then(d => console.log('پاسخ سرور:', d))
                    .catch(e => console.error('خطا:', e));
                }
            }
        </script>
        <style>
            body { font-family: Tahoma, sans-serif; text-align: center; padding: 50px; }
        </style>
    </head>
    <body>
        <h1>سلام! به برنامک ما خوش آمدید.</h1>
        <p>اطلاعات شما در حال ثبت است...</p>
    </body>
    </html>
    """
    return Response(html_content, mimetype='text/html')

@app.route('/register-user', methods=['POST'])
def register_user():
    data = request.get_json(silent=True) or {}
    init_data = data.get('initData')

    if not init_data:
        return jsonify({"status": "error", "message": "initData missing"}), 400

    user = verify_init_data(init_data, APP_TOKEN)
    if user and user.get('id'):
        add_user(
            chat_id=user['id'],
            first_name=user.get('first_name', ''),
            username=user.get('username', '')
        )
        print(f"✅ کاربر جدید ثبت شد: {user['id']}")
        return jsonify({"status": "ok", "chat_id": user['id']})
    return jsonify({"status": "error", "message": "Invalid initData"}), 403

@app.route('/users')
def list_users():
    users = get_all_users()
    return jsonify({"count": len(users), "chat_ids": users})

# ================== پنل ادمین ==================
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if request.method == 'POST':
        password = request.form.get('password', '')
        if not hmac.compare_digest(password, ADMIN_PASSWORD):
            return "رمز عبور اشتباه است", 403

        image_url = (request.form.get('image_url') or '').strip()
        caption = request.form.get('caption', '')

        if not image_url:
            return "لطفاً آدرس عکس را وارد کنید", 400

        users = get_all_users()
        if not users:
            return "هیچ کاربری ثبت نشده است", 400

        success_count = 0
        for chat_id in users:
            try:
                url = "https://eitaayar.ir/api/app/sendFile"
                payload = {
                    "token": APP_TOKEN,
                    "chat_id": chat_id,
                    "file": image_url,
                    "caption": caption,
                }
                response = requests.post(url, json=payload, timeout=15)
                result = response.json()
                print(f"پاسخ ایتا برای {chat_id}: {result}")

                if result.get("ok") is True:
                    success_count += 1
                else:
                    print(f"خطا در ارسال به {chat_id}: {result}")
            except Exception as e:
                print(f"خطا در ارسال به {chat_id}: {e}")

        return f"پیام به {success_count} از {len(users)} کاربر ارسال شد."

    admin_html = """
    <!DOCTYPE html>
    <html lang="fa" dir="rtl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>پنل ادمین</title>
        <style>
            body { font-family: Tahoma, sans-serif; max-width: 600px; margin: 40px auto; padding: 20px; }
            input, textarea { width: 100%; padding: 10px; margin: 8px 0; box-sizing: border-box; }
            button { background: #EF7F1A; color: white; padding: 12px 20px; border: none; cursor: pointer; }
            label { font-weight: bold; margin-top: 15px; display: block; }
        </style>
    </head>
    <body>
        <h2>پنل ارسال پیام</h2>
        <form method="POST">
            <label>رمز عبور:</label>
            <input type="password" name="password" required>

            <label>آدرس عکس (URL):</label>
            <input type="url" name="image_url" placeholder="https://example.com/image.jpg" required>

            <label>کپشن:</label>
            <textarea name="caption" rows="4" placeholder="متن همراه عکس..."></textarea>

            <button type="submit">ارسال به همه کاربران</button>
        </form>
    </body>
    </html>
    """
    return Response(admin_html, mimetype='text/html')

# ================== راه‌اندازی ==================
init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))