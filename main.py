import re
import requests
import json
import os
import threading
from flask import Flask
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ================== FLASK SERVER (لـ UptimeRobot) ==================
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "🤖 البوت يعمل! استخدم /health للتحقق.", 200

@app_flask.route('/health')
def health():
    return "OK", 200

def run_flask():
    app_flask.run(host="0.0.0.0", port=8080)

# ================== CONFIG ==================
BOT_TOKEN = os.getenv("BOT_TOKEN")
MAILBLINKER_TOKEN = os.getenv("MAILBLINKER_TOKEN")

if not BOT_TOKEN or not MAILBLINKER_TOKEN:
    raise ValueError("⚠️ أضف BOT_TOKEN و MAILBLINKER_TOKEN في Secrets!")

CREATE_MAIL = "https://mailblinker.com/api/mail/create-mail"
GET_MESSAGES = "https://mailblinker.com/api/mail/messages"
GET_OTP_LINK = "https://mailblinker.com/api/mail/last-unread-otp-or-link"

HEADERS = {
    "Authorization": f"Bearer {MAILBLINKER_TOKEN}",
    "Content-Type": "application/json"
}

DATA_FILE = "user_emails.json"

# ================== HELPER FUNCTIONS ==================
def load_emails():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_emails(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("📧 إنشاء إيميل")],
        [KeyboardButton("🔐 جلب OTP")],
        [KeyboardButton("📨 كل الرسائل من إيميل")],
        [KeyboardButton("📬 كل الإيميلات")],
        [KeyboardButton("🔍 البحث عن رسائل إيميل")]
    ], resize_keyboard=True)

# ================== HANDLERS ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    all_data = load_emails()
    user_emails = all_data.get(user_id, [])
    context.user_data["emails"] = user_emails
    context.user_data["current_email"] = user_emails[-1] if user_emails else None
    context.user_data["waiting_for_email_search"] = False
    await update.message.reply_text(
        "👋 أهلاً بك!\nاختر خدمة من القائمة 👇",
        reply_markup=main_keyboard()
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()

    all_data = load_emails()
    if user_id not in all_data:
        all_data[user_id] = []
    context.user_data["emails"] = all_data[user_id]
    if not context.user_data.get("current_email") and all_data[user_id]:
        context.user_data["current_email"] = all_data[user_id][-1]

    # وضع البحث عن إيميل
    if context.user_data.get("waiting_for_email_search"):
        email = text.strip()
        if "@" not in email or "." not in email:
            await update.message.reply_text("❌ يبدو أن هذا ليس إيميلًا صالحًا. أعد المحاولة:")
            return
        context.user_data["waiting_for_email_search"] = False
        await fetch_messages_by_email(update, context, email)
        return

    # معالجة الخيارات
    if text == "📧 إنشاء إيميل":
        try:
            response = requests.post(CREATE_MAIL, headers=HEADERS)
            response.raise_for_status()
            data = response.json()
            email = data.get("email")
            if not email:
                await update.message.reply_text("❌ لم يتم استلام إيميل صالح من الخادم.")
                return

            # تحديث البيانات في الذاكرة
            context.user_data["emails"].append(email)
            context.user_data["current_email"] = email

            # حفظ في الملف
            all_data = load_emails()
            if user_id not in all_data:
                all_data[user_id] = []
            if email not in all_data[user_id]:
                all_data[user_id].append(email)
            save_emails(all_data)

            await update.message.reply_text(f"✅ تم إنشاء الإيميل بنجاح:\n`{email}`", parse_mode="Markdown")

        except Exception as e:
            await update.message.reply_text(f"❌ خطأ أثناء إنشاء الإيميل:\n{str(e)}")

    elif text == "🔐 جلب OTP":
        email = context.user_data.get("current_email")
        if not email:
            await update.message.reply_text("⚠️ لا يوجد إيميل نشط. أنشئ إيميل أولًا.")
            return
        await fetch_otp(update, context, email)

    elif text == "📨 كل الرسائل من إيميل":
        email = context.user_data.get("current_email")
        if not email:
            await update.message.reply_text("⚠️ لا يوجد إيميل نشط. أنشئ إيميل أولًا.")
            return
        await fetch_all_messages(update, context, email)

    elif text == "📬 كل الإيميلات":
        emails = context.user_data.get("emails", [])
        if not emails:
            await update.message.reply_text("📭 لم تُنشئ أي إيميلات بعد.")
        else:
            msg = "📬 *الإيميلات التي أنشأتها:*\n\n"
            for i, e in enumerate(emails, 1):
                status = " (نشط)" if e == context.user_data.get("current_email") else ""
                msg += f"{i}. `{e}`{status}\n"
            await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "🔍 البحث عن رسائل إيميل":
        context.user_data["waiting_for_email_search"] = True
        await update.message.reply_text("✉️ أرسل الإيميل الذي تريد جلب رسائله:")

    else:
        await update.message.reply_text("⚠️ اختر خيارًا من القائمة:", reply_markup=main_keyboard())

# ================== FETCH FUNCTIONS ==================
async def fetch_messages_by_email(update: Update, context: ContextTypes.DEFAULT_TYPE, email: str):
    try:
        r = requests.post(GET_MESSAGES, headers=HEADERS, json={"email": email})
        r.raise_for_status()
        data = r.json()
        messages = data.get("messages", [])
        if not messages:
            await update.message.reply_text("📭 لا توجد رسائل لهذا الإيميل.")
            return
        await update.message.reply_text(f"📨 وُجدت {len(messages)} رسالة للإيميل:\n`{email}`", parse_mode="Markdown")
        for msg in messages[:5]:
            body = msg.get("body", "")
            preview = body[:1000] + ("..." if len(body) > 1000 else "")
            await update.message.reply_text(f"📄 *رسالة:*", parse_mode="Markdown")
            await update.message.reply_text(preview)
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في جلب الرسائل:\n{str(e)}")

async def fetch_otp(update: Update, context: ContextTypes.DEFAULT_TYPE, email: str):
    try:
        r = requests.post(GET_OTP_LINK, headers=HEADERS, json={"email": email})
        r.raise_for_status()
        result = r.json()
        otp_or_link = result.get("otp") or result.get("link")
        if not otp_or_link:
            await update.message.reply_text("❌ لم يتم العثور على OTP أو رابط جديد.")
            return
        if result.get("otp"):
            await update.message.reply_text(f"🔐 *OTP جديد:*\n`{otp_or_link}`", parse_mode="Markdown")
        elif result.get("link"):
            await update.message.reply_text(f"🔗 *رابط تحقق:*\n{otp_or_link}", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"📦 *نتيجة غير معروفة:*\n`{otp_or_link}`", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في جلب OTP:\n{str(e)}")

async def fetch_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE, email: str):
    try:
        r = requests.post(GET_MESSAGES, headers=HEADERS, json={"email": email})
        r.raise_for_status()
        data = r.json()
        messages = data.get("messages", [])

        if not messages:
            await update.message.reply_text("📭 لا توجد رسائل.")
            return

        nitro_found = False
        for msg in messages[:5]:
            body = msg.get("body", "")
            subject = msg.get("subject", "")

            preview = body[:1000] + ("..." if len(body) > 1000 else "")
            await update.message.reply_text(f"📄 *رسالة:*", parse_mode="Markdown")
            await update.message.reply_text(preview)

            if "DISCORD NITRO" in body or "DISCORD NITRO" in subject:
                match = re.search(r"https?://[^\s]+", body)
                if match:
                    nitro_link = match.group(0).split(">")[0].split("<")[0].split()[0]
                    keyboard = InlineKeyboardMarkup([
                        [InlineKeyboardButton("🎮 افتح هدية Discord Nitro", url=nitro_link)]
                    ])
                    await update.message.reply_text(
                        "🎉 *تم العثور على هدية Discord Nitro!*",
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                    nitro_found = True
                    break

        if not nitro_found:
            await update.message.reply_text("🔍 لم يتم العثور على هدية Discord Nitro في آخر 5 رسائل.")

    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في جلب الرسائل:\n{str(e)}")

# ================== MAIN ==================
def run_telegram_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Telegram Bot جاهز ويعمل...")
    app.run_polling()

if __name__ == "__main__":
    # تشغيل Flask في الخلفية
    threading.Thread(target=run_flask, daemon=True).start()
    # تشغيل البوت
    run_telegram_bot()