import os
import json
import hmac
import hashlib
import sqlite3
from urllib.parse import parse_qsl
from flask import Flask, request, jsonify, render_template, redirect, url_for

app = Flask(__name__)

# ================== تنظیمات ==================
# این مقادیر را در Render به عنوان Environment Variable تنظیم کنید
APP_TOKEN = os.environ.get("EITAA_APP_TOKEN", "80377164:Qlas&(-2Dexs(-jJNLf7-r?xGhy-3alyx]-jC1n{f-cYZJvi-dncKts-$e3,~p-o&qX)t-$y/CqY-LHZbl7-4JVaVA-2GRm5*-3aX7Wo-P{RfsY-AQ2wAC-w8N?nM-Mm1B1m-d*EgTe-HzUK8.-Ny67EB-J4Wwvc-uKKD#i-2tg,OF-koT5jO-WTq[Vv-4uSFbo-1brgqv-UBaI!i-Z6/P84-L")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "Ali")
DATABASE = "users.db"

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
    c.execute("INSERT OR IGNORE INTO users (chat_id, first_name, username) VALUES (?, ?, ?)",
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

        if calculated_hash == received_hash:
            user_json = json.loads(params.get('user', '{}'))
            return user_json
        return None
    except Exception as e:
        print(f"Error verifying initData: {e}")
        return None

# ================== API برای برنامک ==================
@app.route('/register-user', methods=['POST'])
def register_user():
    """این endpoint توسط برنامک (سمت کلاینت) فراخوانی می‌شود"""
    data = request.json
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
    else:
        return jsonify({"status": "error", "message": "Invalid initData"}), 403

# ================== پنل ادمین ==================
@app.route('/admin', methods=['GET', 'POST'])
def admin_panel():
    if request.method == 'POST':
        password = request.form.get('password')
        if password != ADMIN_PASSWORD:
            return "رمز عبور اشتباه است", 403

        image_url = request.form.get('image_url')
        caption = request.form.get('caption')

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
            "caption": caption
        }
        response = requests.post(url, json=payload, timeout=15)
        result = response.json()
        
        # این خط را اضافه کنید تا پاسخ دقیق ایتا را ببینید
        print(f"پاسخ ایتا برای {chat_id}: {result}")
        
        if result.get("ok") == True:
            success_count += 1
    except Exception as e:
        print(f"خطا در ارسال به {chat_id}: {e}")
      
            except Exception as e:
                print(f"خطا در ارسال به {chat_id}: {e}")

        return f"پیام به {success_count} از {len(users)} کاربر ارسال شد."

    return render_template('admin.html')

@app.route('/users')
def list_users():
    users = get_all_users()
    return jsonify({"count": len(users), "chat_ids": users})

@app.route('/')
def index():
    # به جای ریدایرکت به ادمین، فایل HTML را نشان بده
    return render_template('index.html') 
# ================== راه‌اندازی ==================
init_db()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))