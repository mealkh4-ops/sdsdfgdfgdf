import requests

# ۱. توکن خود را از ایتایار دریافت و اینجا قرار دهید
TOKEN = "80377164:Qlas&(-2Dexs(-jJNLf7-r?xGhy-3alyx]-jC1n{f-cYZJvi-dncKts-$e3,~p-o&qX)t-$y/CqY-LHZbl7-4JVaVA-2GRm5*-3aX7Wo-P{RfsY-AQ2wAC-w8N?nM-Mm1B1m-d*EgTe-HzUK8.-Ny67EB-J4Wwvc-uKKD#i-2tg,OF-koT5jO-WTq[Vv-4uSFbo-1brgqv-UBaI!i-Z6/P84-L"
# ۲. شناسه چتی که می‌خواهید پیام به آن ارسال شود (مثلاً شناسه کانال یا گروه)
CHAT_ID = 9602119"

def send_message(chat_id, text):
    # آدرس دقیق API بر اساس مستندات ایتایار
    url = f"https://eitaayar.ir/api/{TOKEN}/sendMessage"
    
    # پارامترهای ارسالی
    payload = {
        "chat_id": chat_id,
        "text": text
    }
    
    try:
        # ارسال درخواست POST
        response = requests.post(url, data=payload, timeout=10)
        response.raise_for_status() # بررسی خطاهای HTTP
        
        result = response.json()
        
        if result.get("ok"):
            print("پیام با موفقیت ارسال شد.")
            return True
        else:
            print(f"خطا در ارسال: {result}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"خطای شبکه یا اتصال: {e}")
        return False

# ۳. فراخوانی تابع برای ارسال پاسخ
# این کد را می‌توانید در هر جایی که منطق تشخیص «سلام» را پیاده‌سازی کردید، صدا بزنید
send_message(CHAT_ID, "علیک سلام")