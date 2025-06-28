# bot.py (نسخه نهایی، یکپارچه و با استایل استاندارد)

import logging
import sqlite3
import time
import random
import string
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.helpers import escape_markdown
from telegram.error import NetworkError, TimedOut

# --- بخش ۱: تنظیمات اصلی ---
# لطفاً این بخش را با اطلاعات خودتان ویرایش کنید
##################################################################
BOT_TOKEN = "7766206584:AAG5VMI7QmwPFKssyDLCxSgXr4wMuWTFH-Y"
ADMIN_IDS = [7712543793]
##################################################################

# تعریف حالت‌های مکالمه
(ADD_QA_QUESTION, ADD_QA_ANSWER, ADD_ADMIN_ID, REQ_SERVICE_DESC,
 REQ_SERVICE_PHONE, CHECK_TRACKING_CODE, MSG_TO_USER_ID,
 MSG_TO_USER_TEXT) = range(8)


# --- بخش ۲: مدیریت پایگاه داده (Database) ---

def get_db_connection():
    """یک اتصال جدید به پایگاه داده ایجاد و برمی‌گرداند."""
    conn = sqlite3.connect('bot_database.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_db_add_is_blocked():
    """ستون is_blocked را در صورت عدم وجود به جدول کاربران اضافه می‌کند."""
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute('ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0')
        print("INFO: ستون is_blocked به جدول users اضافه شد.")
    except sqlite3.OperationalError:
        # ستون از قبل وجود دارد
        pass
    conn.commit()
    conn.close()


def setup_database():
    """جداول اولیه را در پایگاه داده ایجاد می‌کند."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)')
    c.execute(
        'CREATE TABLE IF NOT EXISTS qa ('
        'id INTEGER PRIMARY KEY AUTOINCREMENT, '
        'question TEXT UNIQUE, '
        'answer TEXT)'
    )
    c.execute(
        'CREATE TABLE IF NOT EXISTS users ('
        'user_id INTEGER PRIMARY KEY, first_name TEXT, username TEXT, '
        'start_time TEXT, is_blocked INTEGER DEFAULT 0)'
    )
    c.execute(
        'CREATE TABLE IF NOT EXISTS services ('
        'tracking_code TEXT PRIMARY KEY, user_id INTEGER, user_phone TEXT, '
        'service_description TEXT, status TEXT, request_time TEXT)'
    )
    conn.commit()
    conn.close()


def generate_tracking_code():
    """یک کد رهگیری منحصر به فرد ایجاد می‌کند."""
    timestamp = time.strftime("%Y%m%d")
    random_part = ''.join(random.choices(
        string.ascii_uppercase + string.digits, k=4
    ))
    return f"SRV-{timestamp}-{random_part}"


def db_add_admin(user_id):
    conn = get_db_connection()
    conn.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()


def db_remove_admin(user_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()


def db_get_all_admins():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM admins')
    db_admins = {row['user_id'] for row in cursor.fetchall()}
    all_admin_ids = db_admins.union(set(ADMIN_IDS))
    conn.close()
    return list(all_admin_ids)


def db_add_or_update_user(user_id, first_name, username):
    conn = get_db_connection()
    current_time = time.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        'INSERT INTO users (user_id, first_name, username, start_time) '
        'VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET '
        'first_name=excluded.first_name, username=excluded.username',
        (user_id, first_name, username, current_time)
    )
    conn.commit()
    conn.close()


def db_get_all_users_with_status():
    conn = get_db_connection()
    users = conn.execute(
        'SELECT user_id, first_name, username, is_blocked '
        'FROM users ORDER BY start_time DESC'
    ).fetchall()
    conn.close()
    return users


def db_block_user(user_id):
    conn = get_db_connection()
    conn.execute('UPDATE users SET is_blocked = 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()


def db_unblock_user(user_id):
    conn = get_db_connection()
    conn.execute('UPDATE users SET is_blocked = 0 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()


def db_is_user_blocked(user_id):
    conn = get_db_connection()
    result = conn.execute(
        'SELECT is_blocked FROM users WHERE user_id = ?', (user_id,)
    ).fetchone()
    conn.close()
    return result and result['is_blocked'] == 1


def db_add_service_request(user_id, phone, description):
    conn = get_db_connection()
    tracking_code = generate_tracking_code()
    current_time = time.strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        'INSERT INTO services (tracking_code, user_id, user_phone, '
        'service_description, status, request_time) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (tracking_code, user_id, phone, description, "ارسال شده", current_time)
    )
    conn.commit()
    conn.close()
    return tracking_code


def db_get_service_by_tracking_code(code):
    conn = get_db_connection()
    service = conn.execute(
        'SELECT s.*, u.first_name, u.username FROM services s '
        'JOIN users u ON s.user_id = u.user_id WHERE s.tracking_code = ?',
        (code,)
    ).fetchone()
    conn.close()
    return service


def db_get_all_services():
    conn = get_db_connection()
    services = conn.execute(
        'SELECT s.*, u.first_name FROM services s '
        'JOIN users u ON s.user_id = u.user_id ORDER BY s.request_time DESC'
    ).fetchall()
    conn.close()
    return services


def db_delete_service_request(tracking_code):
    conn = get_db_connection()
    conn.execute('DELETE FROM services WHERE tracking_code = ?', (tracking_code,))
    conn.commit()
    conn.close()


def db_update_service_status(tracking_code, new_status):
    conn = get_db_connection()
    conn.execute(
        'UPDATE services SET status = ? WHERE tracking_code = ?',
        (new_status, tracking_code)
    )
    conn.commit()
    conn.close()


def db_add_qa(question, answer):
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO qa (question, answer) VALUES (?, ?)', (question, answer)
    )
    conn.commit()
    conn.close()


def db_get_all_qa():
    conn = get_db_connection()
    qas = conn.execute('SELECT * FROM qa ORDER BY id DESC').fetchall()
    conn.close()
    return qas


def db_delete_qa(qa_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM qa WHERE id = ?', (qa_id,))
    conn.commit()
    conn.close()


def db_get_stats():
    conn = get_db_connection()
    total_users = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    total_services = conn.execute(
        'SELECT COUNT(*) FROM services').fetchone()[0]
    total_qa = conn.execute('SELECT COUNT(*) FROM qa').fetchone()[0]
    conn.close()
    return {"users": total_users, "services": total_services, "qa": total_qa}


# --- بخش ۳: ساخت کیبوردها (Keyboards) ---

def admin_main_keyboard():
    keyboard = [
        [
            InlineKeyboardButton("➕ ثبت سوال جدید", callback_data='add_qa'),
            InlineKeyboardButton("📋 لیست سوالات", callback_data='list_qa')
        ],
        [
            InlineKeyboardButton("📊 آمار ربات", callback_data='stats'),
            InlineKeyboardButton("👥 کاربران (بلاک/آنبلاک)", callback_data='list_users')
        ],
        [
            InlineKeyboardButton("🆕 ثبت ادمین جدید", callback_data='add_admin'),
            InlineKeyboardButton("🗑️ حذف ادمین", callback_data='list_admins_for_delete')
        ],
        [
            InlineKeyboardButton("🛠️ لیست خدمات", callback_data='list_services'),
            InlineKeyboardButton("🗑️ حذف درخواست", callback_data='list_services_for_delete')
        ],
        [InlineKeyboardButton("✉️ پیام به کاربر", callback_data='send_message_to_user')]
    ]
    return InlineKeyboardMarkup(keyboard)


def user_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("💬 پرسش", callback_data='ask_question')],
        [InlineKeyboardButton("✅ دریافت خدمات", callback_data='request_service')],
        [InlineKeyboardButton("🔍 استعلام کد رهگیری", callback_data='check_tracking_code')]
    ]
    return InlineKeyboardMarkup(keyboard)


def service_status_keyboard(tracking_code):
    statuses = ["ارسال شده", "درحال بررسی", "پاسخ داده شده"]
    keyboard = [[InlineKeyboardButton(
        status, callback_data=f"set_status_{tracking_code}_{status}"
    )] for status in statuses]
    keyboard.append(
        [InlineKeyboardButton("⬅️ بازگشت به لیست خدمات", callback_data='list_services')]
    )
    return InlineKeyboardMarkup(keyboard)


def back_to_admin_panel_keyboard():
    keyboard = [[InlineKeyboardButton(
        "⬅️ بازگشت به پنل ادمین", callback_data='admin_panel'
    )]]
    return InlineKeyboardMarkup(keyboard)


# --- بخش ۴: مدیریت دستورات و دکمه‌ها (Handlers) ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if db_is_user_blocked(user.id):
        return
    db_add_or_update_user(user.id, user.first_name, user.username)
    if user.id in db_get_all_admins():
        await admin_panel(update, context)
    else:
        text = (
            f"سلام {user.first_name}! به ربات ما خوش آمدید. "
            "لطفاً یک گزینه را انتخاب کنید:"
        )
        await update.message.reply_text(text, reply_markup=user_main_keyboard())


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in db_get_all_admins():
        await update.message.reply_text("شما ادمین نیستید.")
        return
    text = "به پنل ادمین خوش آمدید. لطفاً یک گزینه را انتخاب کنید:"
    reply_markup = admin_main_keyboard()
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)


async def add_qa_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text("لطفا سوال جدید را وارد کنید:")
    return ADD_QA_QUESTION


async def add_qa_question_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['question'] = update.message.text
    await update.message.reply_text(
        "عالی! حالا جواب مربوط به این سوال را وارد کنید:"
    )
    return ADD_QA_ANSWER


async def add_qa_answer_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = context.user_data['question']
    answer = update.message.text
    db_add_qa(question, answer)
    await update.message.reply_text(
        "✅ سوال و جواب جدید با موفقیت ذخیره شد.",
        reply_markup=back_to_admin_panel_keyboard()
    )
    return ConversationHandler.END


async def list_qa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    qas = db_get_all_qa()
    if not qas:
        await query.edit_message_text(
            "هیچ سوالی ثبت نشده است.",
            reply_markup=back_to_admin_panel_keyboard()
        )
        return
    keyboard = [
        [InlineKeyboardButton(
            f"❌ {qa['question'][:30]}...",
            callback_data=f"delete_qa_{qa['id']}"
        )] for qa in qas
    ]
    keyboard.append(
        [InlineKeyboardButton("⬅️ بازگشت", callback_data='admin_panel')]
    )
    await query.edit_message_text(
        "برای حذف، روی سوال مورد نظر کلیک کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def delete_qa_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    qa_id = int(query.data.split('_')[2])
    db_delete_qa(qa_id)
    await query.answer(text="سوال حذف شد!")
    await list_qa(update, context)


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    stats_data = db_get_stats()
    text = (
        f"📊 *آمار ربات:*\n\n"
        f"👤 *تعداد کل کاربران:* {stats_data['users']}\n"
        f"💬 *تعداد پرسش و پاسخ‌ها:* {stats_data['qa']}\n"
        f"🛠️ *تعداد خدمات ثبت شده:* {stats_data['services']}"
    )
    await query.edit_message_text(
        text,
        parse_mode='MarkdownV2',
        reply_markup=back_to_admin_panel_keyboard()
    )


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    users = db_get_all_users_with_status()
    keyboard = []
    text = r"👥 *لیست کاربران ربات:*" + "\n\n"
    if not users:
        text += r"هنوز کاربری ثبت نام نکرده است\."
    else:
        for user in users:
            first_name = escape_markdown(
                user['first_name'] or "ناشناس", version=2
            )
            status_text = "⛔️ بلاک شده" if user['is_blocked'] else "✅ فعال"
            text += f"👤 {first_name} \(`{user['user_id']}`\) \- وضعیت: *{status_text}*\n"
            if user['is_blocked']:
                keyboard.append([InlineKeyboardButton(
                    f"✅ آنبلاک کاربر: {user['first_name']}",
                    callback_data=f"unblock_user_{user['user_id']}"
                )])
            else:
                if user['user_id'] not in ADMIN_IDS:
                    keyboard.append([InlineKeyboardButton(
                        f"🚫 بلاک کاربر: {user['first_name']}",
                        callback_data=f"block_user_{user['user_id']}"
                    )])
            text += r"\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-" + "\n"

    keyboard.append(
        [InlineKeyboardButton("⬅️ بازگشت", callback_data='admin_panel')]
    )
    await query.edit_message_text(
        text,
        parse_mode='MarkdownV2',
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def block_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id_to_block = int(query.data.split('_')[2])
    db_block_user(user_id_to_block)
    await query.answer(f"کاربر {user_id_to_block} بلاک شد.")
    await list_users(update, context)


async def unblock_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id_to_unblock = int(query.data.split('_')[2])
    db_unblock_user(user_id_to_unblock)
    await query.answer(f"کاربر {user_id_to_unblock} آنبلاک شد.")
    await list_users(update, context)


async def add_admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "لطفا شناسه عددی ادمین جدید را وارد کنید:"
    )
    return ADD_ADMIN_ID


async def add_admin_id_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_admin_id = int(update.message.text)
        db_add_admin(new_admin_id)
        text = f"✅ ادمین با شناسه `{new_admin_id}` با موفقیت اضافه شد\."
        await update.message.reply_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=back_to_admin_panel_keyboard()
        )
    except ValueError:
        text = r"خطا: لطفاً فقط شناسه عددی وارد کنید\."
        await update.message.reply_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=back_to_admin_panel_keyboard()
        )
    return ConversationHandler.END


async def list_admins_for_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    admins = db_get_all_admins()
    super_admin = ADMIN_IDS[0]
    deletable_admins = [admin_id for admin_id in admins if admin_id != super_admin]
    if not deletable_admins:
        await query.edit_message_text(
            "ادمین دیگری برای حذف وجود ندارد.",
            reply_markup=back_to_admin_panel_keyboard()
        )
        return
    keyboard = [
        [InlineKeyboardButton(
            f"❌ حذف ادمین: {admin_id}",
            callback_data=f"delete_admin_{admin_id}"
        )] for admin_id in deletable_admins
    ]
    keyboard.append(
        [InlineKeyboardButton("⬅️ بازگشت", callback_data='admin_panel')]
    )
    await query.edit_message_text(
        "برای حذف، روی شناسه ادمین مورد نظر کلیک کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def delete_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin_id_to_delete = int(query.data.split('_')[2])
    db_remove_admin(admin_id_to_delete)
    await query.answer(text=f"ادمین {admin_id_to_delete} حذف شد!")
    await list_admins_for_delete(update, context)


async def list_services(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    services = db_get_all_services()
    if not services:
        text = r"هیچ درخواستی برای خدمات ثبت نشده است\."
        await query.edit_message_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=back_to_admin_panel_keyboard()
        )
        return
    keyboard_buttons = []
    text = "🛠️ *لیست درخواست‌های خدمات:*\n\n"
    for service in services:
        status = escape_markdown(service['status'], version=2)
        text += (
            f"▫️ *کد:* `{service['tracking_code']}`\n"
            f"▪️ *وضعیت:* {status}\n"
            r"\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-\-" + "\n"
        )
        keyboard_buttons.append([InlineKeyboardButton(
            f"🔧 جزئیات: {service['tracking_code']}",
            callback_data=f"view_service_{service['tracking_code']}"
        )])
    keyboard_buttons.append(
        [InlineKeyboardButton("⬅️ بازگشت به پنل", callback_data='admin_panel')]
    )
    await query.edit_message_text(
        text,
        parse_mode='MarkdownV2',
        reply_markup=InlineKeyboardMarkup(keyboard_buttons)
    )


async def view_service_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    tracking_code = query.data.split('_')[2]
    service = db_get_service_by_tracking_code(tracking_code)
    if not service:
        await query.edit_message_text(
            "این درخواست پیدا نشد!",
            reply_markup=back_to_admin_panel_keyboard()
        )
        return
    first_name = escape_markdown(service['first_name'], version=2)
    phone = escape_markdown(service['user_phone'], version=2)
    description = escape_markdown(service['service_description'], version=2)
    status = escape_markdown(service['status'], version=2)
    text = (
        f"⚙️ *جزئیات:* `{tracking_code}`\n\n"
        f"▪️ *نام:* {first_name}\n"
        f"▪️ *تلفن:* `{phone}`\n"
        f"▪️ *توضیحات:* {description}\n\n"
        f"*وضعیت فعلی: {status}*\n\n"
        f"لطفاً وضعیت جدید را انتخاب کنید:"
    )
    await query.edit_message_text(
        text,
        parse_mode='MarkdownV2',
        reply_markup=service_status_keyboard(tracking_code)
    )


async def update_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split('_')
    tracking_code, new_status = parts[2], parts[3]
    db_update_service_status(tracking_code, new_status)
    text = f"✅ وضعیت درخواست `{tracking_code}` به *{new_status}* تغییر یافت\."
    await query.edit_message_text(
        text,
        parse_mode='MarkdownV2',
        reply_markup=back_to_admin_panel_keyboard()
    )


async def list_services_for_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    services = db_get_all_services()
    if not services:
        await query.edit_message_text(
            "هیچ درخواستی برای حذف وجود ندارد.",
            reply_markup=back_to_admin_panel_keyboard()
        )
        return
    keyboard = [
        [InlineKeyboardButton(
            f"❌ حذف: {s['tracking_code']}",
            callback_data=f"delete_service_{s['tracking_code']}"
        )] for s in services
    ]
    keyboard.append(
        [InlineKeyboardButton("⬅️ بازگشت", callback_data='admin_panel')]
    )
    await query.edit_message_text(
        "برای حذف، روی درخواست مورد نظر کلیک کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def delete_service_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    tracking_code = query.data.split('_')[2]
    db_delete_service_request(tracking_code)
    await query.answer(f"درخواست {tracking_code} حذف شد.")
    await list_services_for_delete(update, context)


async def send_message_to_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        "لطفا شناسه عددی کاربری که می‌خواهید به او پیام دهید را وارد کنید:"
    )
    return MSG_TO_USER_ID


async def msg_to_user_id_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data['target_user_id'] = int(update.message.text)
        await update.message.reply_text("حالا متن پیام خود را بنویسید:")
        return MSG_TO_USER_TEXT
    except ValueError:
        text = r"خطا: شناسه باید عددی باشد\."
        await update.message.reply_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=back_to_admin_panel_keyboard()
        )
        return ConversationHandler.END


async def msg_to_user_text_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_id = context.user_data['target_user_id']
    message_text = update.message.text
    try:
        await context.bot.send_message(
            chat_id=target_id, text=f"پیامی از طرف ادمین:\n\n{message_text}"
        )
        text = r"✅ پیام شما با موفقیت برای کاربر ارسال شد\."
        await update.message.reply_text(
            text,
            parse_mode='MarkdownV2',
            reply_markup=back_to_admin_panel_keyboard()
        )
    except Exception as e:
        await update.message.reply_text(
            f"خطا در ارسال پیام: {e}",
            reply_markup=back_to_admin_panel_keyboard()
        )
    return ConversationHandler.END


async def get_id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f"شناسه کاربری شما: {user_id}")


async def ask_question_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if db_is_user_blocked(update.effective_user.id):
        return
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "لطفاً سوال خود را تایپ کرده و ارسال کنید."
    )


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if db_is_user_blocked(user.id):
        return
    user_message = update.message.text
    all_qa = db_get_all_qa()
    response = (
        "متاسفانه سوال شما در لیست ما موجود نیست. "
        "می‌توانید از دکمه 'دریافت خدمات' استفاده کنید."
    )
    for qa in all_qa:
        if qa['question'] == user_message:
            response = qa['answer']
            break
    await update.message.reply_text(response)


async def request_service_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if db_is_user_blocked(update.effective_user.id):
        return
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "لطفاً نوع کاری که می‌خواهید را به طور کامل توضیح دهید:"
    )
    return REQ_SERVICE_DESC


async def req_service_desc_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['service_desc'] = update.message.text
    await update.message.reply_text(
        "متشکرم. لطفاً شماره تلفن خود را برای تماس وارد کنید:"
    )
    return REQ_SERVICE_PHONE


async def req_service_phone_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = context.user_data['service_desc']
    phone = update.message.text
    user_id = update.effective_user.id
    tracking_code = db_add_service_request(user_id, phone, description)
    text = (
        f"درخواست شما با موفقیت ثبت شد\.\n"
        f"کد رهگیری شما: `{tracking_code}`\n\n"
        r"لطفاً این کد را برای مراجعات بعدی نزد خود نگه دارید\."
    )
    await update.message.reply_text(text, parse_mode='MarkdownV2')
    user_info = update.effective_user
    admin_message = (
        f"🔔 *درخواست خدمات جدید*\n\n"
        f"توسط: {escape_markdown(user_info.first_name, version=2)}\n"
        f"شناسه: `{user_id}`\n"
        f"کد رهگیری: `{tracking_code}`\n"
        f"تلفن: `{escape_markdown(phone, version=2)}`"
    )
    for admin_id in db_get_all_admins():
        try:
            await context.bot.send_message(
                chat_id=admin_id, text=admin_message, parse_mode='MarkdownV2'
            )
        except Exception as e:
            print(f"Failed to send notification to admin {admin_id}: {e}")
    return ConversationHandler.END


async def check_tracking_code_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if db_is_user_blocked(update.effective_user.id):
        return
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        "لطفاً کد رهگیری خود را وارد کنید:"
    )
    return CHECK_TRACKING_CODE


async def check_tracking_code_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text
    service = db_get_service_by_tracking_code(code)
    if service:
        status = escape_markdown(service['status'], version=2)
        req_time = escape_markdown(service['request_time'], version=2)
        text = (
            f"وضعیت درخواست شما با کد `{service['tracking_code']}`:\n\n"
            f"*وضعیت فعلی:* {status}\n"
            f"*تاریخ ثبت:* {req_time}"
        )
    else:
        text = r"کد رهگیری وارد شده معتبر نیست\."
    await update.message.reply_text(text, parse_mode='MarkdownV2')
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("عملیات لغو شد.")
    context.user_data.clear()
    return ConversationHandler.END


# --- بخش ۵: اجرای اصلی ربات (Main) ---

def main():
    # تنظیمات اولیه لاگ‌گیری
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )

    # ساخت دیتابیس و جداول
    setup_database()
    migrate_db_add_is_blocked()
    for admin_id in ADMIN_IDS:
        db_add_admin(admin_id)

    # ساخت اپلیکیشن ربات
    app = Application.builder().token(BOT_TOKEN).build()

    # تعریف مکالمات
    add_qa_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_qa_start, pattern='^add_qa$')],
        states={
            ADD_QA_QUESTION: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, add_qa_question_received)],
            ADD_QA_ANSWER: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, add_qa_answer_received)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )
    add_admin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_admin_start, pattern='^add_admin$')],
        states={
            ADD_ADMIN_ID: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, add_admin_id_received)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )
    req_service_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(request_service_start, pattern='^request_service$')],
        states={
            REQ_SERVICE_DESC: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, req_service_desc_received)],
            REQ_SERVICE_PHONE: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, req_service_phone_received)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )
    check_tracking_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(
            check_tracking_code_start, pattern='^check_tracking_code$')],
        states={
            CHECK_TRACKING_CODE: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, check_tracking_code_received)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )
    send_message_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(
            send_message_to_user_start, pattern='^send_message_to_user$')],
        states={
            MSG_TO_USER_ID: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, msg_to_user_id_received)],
            MSG_TO_USER_TEXT: [MessageHandler(
                filters.TEXT & ~filters.COMMAND, msg_to_user_text_received)]
        },
        fallbacks=[CommandHandler('cancel', cancel)],
        per_message=False
    )

    # افزودن تمام کنترل‌کننده‌ها به اپلیکیشن
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('panel', admin_panel))
    app.add_handler(CommandHandler('id', get_id_command))

    app.add_handler(add_qa_conv)
    app.add_handler(add_admin_conv)
    app.add_handler(req_service_conv)
    app.add_handler(check_tracking_conv)
    app.add_handler(send_message_conv)

    callback_handlers = {
        r'^admin_panel$': admin_panel,
        r'^list_qa$': list_qa,
        r'^delete_qa_': delete_qa_callback,
        r'^stats$': stats,
        r'^list_users$': list_users,
        r'^list_services$': list_services,
        r'^ask_question$': ask_question_start,
        r'^list_admins_for_delete$': list_admins_for_delete,
        r'^delete_admin_': delete_admin_callback,
        r'^block_user_': block_user_callback,
        r'^unblock_user_': unblock_user_callback,
        r'^list_services_for_delete$': list_services_for_delete,
        r'^delete_service_': delete_service_callback,
        r'^view_service_': view_service_details,
        r'^set_status_': update_status_callback,
    }
    for pattern, handler in callback_handlers.items():
        app.add_handler(CallbackQueryHandler(handler, pattern=pattern))

    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, handle_user_message
    ))

    print("ربات با موفقیت راه‌اندازی شد...")
    # اجرای پایدار ربات
    while True:
        try:
            app.run_polling(allowed_updates=Update.ALL_TYPES)
        except (NetworkError, TimedOut):
            logging.warning("خطای شبکه یا تایم‌اوت. تلاش مجدد تا ۱۰ ثانیه دیگر...")
            time.sleep(10)
        except Exception as e:
            logging.error(f"خطای پیش‌بینی نشده: {e}", exc_info=True)
            logging.info("تلاش مجدد تا ۳۰ ثانیه دیگر...")
            time.sleep(30)


if __name__ == '__main__':
    main()