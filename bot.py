import random
import asyncio
import logging
import re
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

# ============ КОНФИГ ============
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher(bot)
dp.middleware.setup(LoggingMiddleware())

# ============ БАЗА ДАННЫХ ============
conn = sqlite3.connect("proxies.db", check_same_thread=False)
cursor = conn.cursor()

cursor.executescript("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        is_active BOOLEAN DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS proxies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER,
        proxy_data TEXT,
        is_sold BOOLEAN DEFAULT 0,
        user_id INTEGER,
        rent_hours INTEGER,
        activated_at DATETIME,
        expires_at DATETIME,
        is_active BOOLEAN DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        balance REAL DEFAULT 0,
        username TEXT,
        full_name TEXT,
        registered DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_id INTEGER,
        proxy_id INTEGER,
        status TEXT,
        rent_days INTEGER,
        created DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS captcha (
        user_id INTEGER PRIMARY KEY,
        correct_item TEXT,
        attempts INTEGER DEFAULT 0
    );
""")
conn.commit()

# ============ ФУНКЦИИ БАЗЫ ============
def add_proxy_batch(product_id, proxies_list):
    count = 0
    for proxy in proxies_list:
        if proxy.strip():
            cursor.execute("INSERT INTO proxies (product_id, proxy_data) VALUES (?, ?)", (product_id, proxy.strip()))
            count += 1
    conn.commit()
    return count

def activate_proxy(proxy_id, user_id, rent_hours):
    now = datetime.now()
    expires = now + timedelta(hours=rent_hours)
    cursor.execute("""
        UPDATE proxies SET is_sold=1, user_id=?, rent_hours=?, activated_at=?, expires_at=?, is_active=1
        WHERE id=?
    """, (user_id, rent_hours, now, expires, proxy_id))
    conn.commit()
    return expires

def deactivate_expired():
    now = datetime.now()
    cursor.execute("UPDATE proxies SET is_active=0 WHERE expires_at < ? AND is_active=1", (now,))
    conn.commit()
    return cursor.rowcount

def save_captcha(user_id, correct_item):
    cursor.execute("INSERT OR REPLACE INTO captcha (user_id, correct_item, attempts) VALUES (?, ?, 0)", (user_id, correct_item))
    conn.commit()

def check_captcha(user_id, selected_item):
    result = cursor.execute("SELECT correct_item, attempts FROM captcha WHERE user_id=?", (user_id,)).fetchone()
    if not result:
        return False, "❌ Капча не найдена. Начните заново."
    correct, attempts = result
    if selected_item == correct:
        cursor.execute("DELETE FROM captcha WHERE user_id=?", (user_id,))
        conn.commit()
        return True, None
    attempts += 1
    if attempts >= 3:
        cursor.execute("DELETE FROM captcha WHERE user_id=?", (user_id,))
        conn.commit()
        return False, "❌ Превышено количество попыток. Напишите /start заново."
    cursor.execute("UPDATE captcha SET attempts=? WHERE user_id=?", (attempts, user_id))
    conn.commit()
    return False, f"❌ Неверно! Осталось попыток: {3 - attempts}"

# ============ КАПЧА ============
FRUITS = ["🍌 Банан", "🌭 Сосиска", "🍑 Персик", "🍒 Вишни", "🌿 Щавель", "🍓 Клубника"]

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    user_id = message.from_user.id
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?, ?, ?)",
                   (user_id, message.from_user.username, message.from_user.full_name))
    conn.commit()
    
    if cursor.execute("SELECT user_id FROM captcha WHERE user_id=?", (user_id,)).fetchone():
        await show_main_menu(message)
        return
    
    correct = random.choice(FRUITS)
    save_captcha(user_id, correct)
    buttons = FRUITS.copy()
    random.shuffle(buttons)
    markup = InlineKeyboardMarkup(row_width=2)
    for fruit in buttons:
        markup.add(InlineKeyboardButton(fruit, callback_data=f"captcha_{fruit}"))
    await message.answer(f"🤖 *Докажите, что вы человек!*\nВыберите: *{correct}*\nУ вас 3 попытки",
                         reply_markup=markup, parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data.startswith("captcha_"))
async def handle_captcha(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    selected = callback.data.replace("captcha_", "")
    result, error = check_captcha(user_id, selected)
    if error:
        await callback.message.edit_text(error)
        await callback.answer()
        return
    if result:
        await callback.message.edit_text("✅ Капча пройдена!")
        await asyncio.sleep(1)
        await show_main_menu(callback.message)
    await callback.answer()

# ============ ГЛАВНОЕ МЕНЮ ДЛЯ ПОЛЬЗОВАТЕЛЯ ============
async def show_main_menu(message: types.Message):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("📦 Купить", callback_data="catalog"),
               InlineKeyboardButton("📋 Мои прокси", callback_data="my_proxies"),
               InlineKeyboardButton("💰 Баланс", callback_data="balance"))
    
    # Если админ - показываем админ-панель
    if message.from_user.id == ADMIN_ID:
        markup.add(InlineKeyboardButton("⚙️ Админ-панель", callback_data="admin_panel"))
    
    await message.answer("🛒 *Добро пожаловать в магазин прокси!*", reply_markup=markup, parse_mode="Markdown")

# ============ АДМИН-ПАНЕЛЬ ============
@dp.callback_query_handler(lambda c: c.data == "admin_panel", user_id=ADMIN_ID)
async def admin_panel(callback: types.CallbackQuery):
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📤 Залить прокси", callback_data="upload_menu"),
        InlineKeyboardButton("📦 Создать товар", callback_data="add_product"),
        InlineKeyboardButton("📊 Склад", callback_data="stock"),
        InlineKeyboardButton("📋 Заказы", callback_data="orders"),
        InlineKeyboardButton("💰 Пополнить баланс", callback_data="add_balance"),
        InlineKeyboardButton("🔙 Назад", callback_data="back_to_menu")
    )
    await callback.message.edit_text("⚙️ *Админ-панель*\nВыберите действие:", reply_markup=markup, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "back_to_menu")
async def back_to_menu(callback: types.CallbackQuery):
    await show_main_menu(callback.message)
    await callback.answer()

# ============ КНОПКА "ЗАЛИТЬ ПРОКСИ" ============
@dp.callback_query_handler(lambda c: c.data == "upload_menu", user_id=ADMIN_ID)
async def upload_menu(callback: types.CallbackQuery):
    products = cursor.execute("SELECT id, name FROM products WHERE is_active=1").fetchall()
    
    if not products:
        await callback.message.edit_text("❌ Сначала создайте товар через «Создать товар»")
        await callback.answer()
        return
    
    markup = InlineKeyboardMarkup(row_width=2)
    for p in products:
        markup.add(InlineKeyboardButton(f"📤 {p[1]} (ID:{p[0]})", callback_data=f"upload_file_{p[0]}"))
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_panel"))
    
    await callback.message.edit_text("📤 *Выберите товар для заливки прокси:*", reply_markup=markup, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data.startswith("upload_file_"), user_id=ADMIN_ID)
async def upload_file_button(callback: types.CallbackQuery):
    product_id = int(callback.data.split("_")[2])
    
    global uploading_product_id
    uploading_product_id = product_id
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("❌ Отмена", callback_data="upload_menu"))
    
    await callback.message.edit_text(
        f"📤 *Загрузка прокси в товар ID:{product_id}*\n\n"
        f"1️⃣ Создайте файл `proxies.txt`\n"
        f"2️⃣ Каждая строка: `ip:port:login:pass`\n"
        f"3️⃣ Отправьте файл в этот чат\n\n"
        f"📌 После отправки файла прокси автоматически загрузятся",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    await callback.answer()

# ============ ОБРАБОТЧИК ФАЙЛА ============
uploading_product_id = None

@dp.message_handler(content_types=['document'], user_id=ADMIN_ID)
async def handle_upload_file(message: types.Message):
    global uploading_product_id
    
    if uploading_product_id is None:
        await message.answer("❌ Сначала выберите товар через «Залить прокси» в админ-панели")
        return
    
    product_id = uploading_product_id
    
    if not message.document.file_name.endswith('.txt'):
        await message.answer("❌ Отправьте файл в формате .txt")
        return
    
    file = await bot.get_file(message.document.file_id)
    file_path = f"/tmp/{message.document.file_name}"
    await bot.download_file(file.file_path, file_path)
    
    with open(file_path, 'r') as f:
        proxies = [line.strip() for line in f if line.strip()]
    
    if not proxies:
        await message.answer("❌ Файл пустой")
        return
    
    count = add_proxy_batch(product_id, proxies)
    uploading_product_id = None
    
    await message.answer(f"✅ Загружено {count} прокси в товар #{product_id}!\n"
                         f"📊 Теперь в наличии {count} шт.")
    
    # Возвращаем в админ-панель
    await admin_panel(message)

# ============ КНОПКА "СОЗДАТЬ ТОВАР" ============
@dp.callback_query_handler(lambda c: c.data == "add_product", user_id=ADMIN_ID)
async def add_product_button(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "📦 *Создание товара*\n\n"
        "Введите команду:\n"
        "`/add <название> <описание>`\n\n"
        "Пример:\n"
        "`/add USA_IPv4 'Анонимные прокси США'`",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.message_handler(commands=['add'], user_id=ADMIN_ID)
async def add_product(message: types.Message):
    args = message.text.split(maxsplit=2)
    if len(args) < 3:
        await message.answer("❌ /add <название> <описание>\nПример: /add USA_IPv4 'Анонимные прокси США'")
        return
    cursor.execute("INSERT INTO products (name, description) VALUES (?, ?)", (args[1], args[2]))
    conn.commit()
    product_id = cursor.lastrowid
    
    await message.answer(f"✅ Товар создан! ID: {product_id}\nТеперь залейте прокси через админ-панель")

# ============ КНОПКА "СКЛАД" ============
@dp.callback_query_handler(lambda c: c.data == "stock", user_id=ADMIN_ID)
async def show_stock_button(callback: types.CallbackQuery):
    products = cursor.execute("""
        SELECT p.id, p.name, COUNT(pr.id), 
        SUM(CASE WHEN pr.is_sold=0 AND pr.is_active=0 THEN 1 ELSE 0 END)
        FROM products p LEFT JOIN proxies pr ON p.id = pr.product_id 
        WHERE p.is_active=1 GROUP BY p.id
    """).fetchall()
    
    if not products:
        await callback.message.edit_text("❌ Товаров нет")
        await callback.answer()
        return
    
    text = "📊 *Склад:*\n\n"
    for p in products:
        free = p[3] if p[3] else 0
        text += f"#{p[0]} {p[1]}\n"
        text += f"   📦 Всего: {p[2]}, ✅ Свободно: {free}\n\n"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_panel"))
    
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    await callback.answer()

# ============ КНОПКА "ЗАКАЗЫ" ============
@dp.callback_query_handler(lambda c: c.data == "orders", user_id=ADMIN_ID)
async def show_orders_button(callback: types.CallbackQuery):
    orders = cursor.execute("""
        SELECT o.id, u.user_id, u.full_name, o.rent_days, o.created
        FROM orders o JOIN users u ON o.user_id = u.user_id
        WHERE o.status = 'pending' ORDER BY o.created ASC
    """).fetchall()
    
    if not orders:
        await callback.message.edit_text("❌ Нет ожидающих заказов")
        await callback.answer()
        return
    
    text = "📋 *Ожидающие заказы:*\n\n"
    for o in orders:
        text += f"#{o[0]} | {o[2]} | {o[3]} дней | {o[4][:16]}\n"
    
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_panel"))
    
    await callback.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
    await callback.answer()

# ============ КНОПКА "ПОПОЛНИТЬ БАЛАНС" ============
@dp.callback_query_handler(lambda c: c.data == "add_balance", user_id=ADMIN_ID)
async def add_balance_button(callback: types.CallbackQuery):
    await callback.message.edit_text(
        "💰 *Пополнение баланса*\n\n"
        "Введите команду:\n"
        "`/addbalance <user_id> <сумма>`\n\n"
        "Пример:\n"
        "`/addbalance 123456789 100`",
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.message_handler(commands=['addbalance'], user_id=ADMIN_ID)
async def add_balance(message: types.Message):
    args = message.text.split()
    if len(args) < 3:
        await message.answer("❌ /addbalance <user_id> <сумма>\nПример: /addbalance 123456789 100")
        return
    try:
        user_id = int(args[1])
        amount = float(args[2])
    except ValueError:
        await message.answer("❌ Неверный формат")
        return
    cursor.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    await message.answer(f"✅ Пользователю {user_id} начислено {amount}₽")

# ============ КАТАЛОГ ============
@dp.callback_query_handler(lambda c: c.data == "catalog")
async def show_catalog(callback: types.CallbackQuery):
    products = cursor.execute("SELECT id, name, description FROM products WHERE is_active=1").fetchall()
    if not products:
        await callback.message.answer("❌ Товаров пока нет")
        await callback.answer()
        return
    for p in products:
        text = f"📦 *{p[1]}*\n📝 {p[2]}\n\n💰 *Цены:*\n• 7 дней — 50₽\n• 14 дней — 80₽\n• 30 дней — 120₽"
        markup = InlineKeyboardMarkup(row_width=3)
        markup.add(InlineKeyboardButton("7д (50₽)", callback_data=f"order_{p[0]}_7_50"),
                   InlineKeyboardButton("14д (80₽)", callback_data=f"order_{p[0]}_14_80"),
                   InlineKeyboardButton("30д (120₽)", callback_data=f"order_{p[0]}_30_120"))
        await callback.message.answer(text, reply_markup=markup, parse_mode="Markdown")
    await callback.answer()

# ============ ПОКУПКА ============
@dp.callback_query_handler(lambda c: c.data.startswith("order_"))
async def order_proxy(callback: types.CallbackQuery):
    data = callback.data.split("_")
    product_id, rent_days, price = int(data[1]), int(data[2]), int(data[3])
    user_id = callback.from_user.id
    
    balance = cursor.execute("SELECT balance FROM users WHERE user_id=?", (user_id,)).fetchone()
    balance = balance[0] if balance else 0
    if balance < price:
        await callback.message.answer(f"❌ Недостаточно средств! Нужно: {price}₽, у вас: {balance}₽")
        await callback.answer()
        return
    
    proxy = cursor.execute("SELECT id FROM proxies WHERE product_id=? AND is_sold=0 AND is_active=0 LIMIT 1", (product_id,)).fetchone()
    if not proxy:
        await callback.message.answer("❌ Прокси закончились! Админ скоро добавит.")
        await callback.answer()
        return
    
    proxy_id = proxy[0]
    cursor.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (price, user_id))
    cursor.execute("INSERT INTO orders (user_id, product_id, proxy_id, status, rent_days) VALUES (?, ?, ?, 'pending', ?)",
                   (user_id, product_id, proxy_id, rent_days))
    conn.commit()
    order_id = cursor.lastrowid
    
    username = callback.from_user.username or "без username"
    admin_text = (f"🆕 *НОВЫЙ ЗАКАЗ!*\n"
                  f"📦 Заказ: #{order_id}\n"
                  f"👤 {callback.from_user.full_name} (@{username})\n"
                  f"🆔 ID: {user_id}\n"
                  f"💰 Сумма: {price}₽\n"
                  f"⏳ Срок: {rent_days} дней\n\n"
                  f"*👉 ПРОСТО ОТПРАВЬТЕ ПРОКСИ В ЭТОТ ЧАТ*")
    await bot.send_message(ADMIN_ID, admin_text, parse_mode="Markdown")
    
    await callback.message.answer(f"✅ *Заказ #{order_id} оплачен!*\n\n"
                                  f"⏳ Срок: {rent_days} дней\n"
                                  f"🔄 Ожидайте выдачи прокси...\n"
                                  f"Админ получил уведомление.")
    await callback.answer()

# ============ АДМИН: ВЫДАЧА ПРОКСИ ============
@dp.message_handler(user_id=ADMIN_ID)
async def admin_send_proxy(message: types.Message):
    proxy_pattern = r'\b(?:\d{1,3}\.){3}\d{1,3}:\d+\b'
    proxies = re.findall(proxy_pattern, message.text)
    if not proxies:
        return
    proxy_data = proxies[0]
    
    pending = cursor.execute("""
        SELECT o.id, o.user_id, o.rent_days
        FROM orders o WHERE o.status = 'pending' ORDER BY o.created ASC LIMIT 1
    """).fetchone()
    if not pending:
        await message.answer("❌ Нет ожидающих заказов")
        return
    
    order_id, user_id, rent_days = pending
    proxy = cursor.execute("SELECT id FROM proxies WHERE proxy_data=? AND is_sold=0 AND is_active=0 LIMIT 1", (proxy_data,)).fetchone()
    if not proxy:
        await message.answer(f"❌ Прокси `{proxy_data}` не найден в базе или уже использован", parse_mode="Markdown")
        return
    
    proxy_id = proxy[0]
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Подтвердить выдачу", callback_data=f"give_{order_id}_{proxy_id}"))
    await message.answer(f"🔍 Найден ожидающий заказ #{order_id}\n"
                         f"👤 ID пользователя: {user_id}\n"
                         f"⏳ Срок: {rent_days} дней\n"
                         f"📦 Прокси: `{proxy_data}`\n\n"
                         f"Нажмите кнопку для выдачи", reply_markup=markup, parse_mode="Markdown")

@dp.callback_query_handler(lambda c: c.data.startswith("give_"))
async def confirm_give(callback: types.CallbackQuery):
    data = callback.data.split("_")
    order_id, proxy_id = int(data[1]), int(data[2])
    
    order = cursor.execute("SELECT user_id, rent_days FROM orders WHERE id=?", (order_id,)).fetchone()
    if not order:
        await callback.answer("❌ Заказ не найден")
        return
    user_id, rent_days = order
    proxy_data = cursor.execute("SELECT proxy_data FROM proxies WHERE id=?", (proxy_id,)).fetchone()[0]
    
    expires = activate_proxy(proxy_id, user_id, rent_days * 24)
    cursor.execute("UPDATE orders SET status='completed' WHERE id=?", (order_id,))
    conn.commit()
    
    try:
        await bot.send_message(user_id,
            f"🟢 *Прокси выдан!*\n"
            f"✅ Ваш заказ #{order_id} выполнен!\n\n"
            f"```\n{proxy_data}\n```\n"
            f"⏳ Действует до: {expires.strftime('%d.%m.%Y %H:%M')}\n"
            f"📅 Срок аренды: {rent_days} дней\n"
            f"⚠️ По истечении времени доступ будет закрыт", parse_mode="Markdown")
    except:
        await callback.message.answer(f"❌ Не удалось отправить пользователю")
        await callback.answer()
        return
    
    await callback.message.edit_text(f"✅ Прокси выдан пользователю!\n"
                                     f"📦 Данные: `{proxy_data}`\n"
                                     f"⏳ Срок: {rent_days} дней\n"
                                     f"📅 До {expires.strftime('%d.%m.%Y %H:%M')}", parse_mode="Markdown")
    await callback.answer()

# ============ ПЕРЕСЫЛКА СООБЩЕНИЙ ОТ ПОЛЬЗОВАТЕЛЕЙ ============
@dp.message_handler()
async def forward_to_admin(message: types.Message):
    if message.from_user.id == ADMIN_ID:
        return
    pending = cursor.execute("SELECT id FROM orders WHERE user_id=? AND status='pending'", (message.from_user.id,)).fetchone()
    if pending:
        await bot.send_message(ADMIN_ID,
            f"💬 *Сообщение от пользователя:*\n"
            f"👤 {message.from_user.full_name}\n"
            f"🆔 {message.from_user.id}\n"
            f"📦 Заказ #{pending[0]}\n\n"
            f"📝 {message.text}", parse_mode="Markdown")
        await message.answer("✅ Сообщение отправлено админу. Ожидайте ответа.")

# ============ ПОЛЬЗОВАТЕЛЬ: МОИ ПРОКСИ ============
@dp.callback_query_handler(lambda c: c.data == "my_proxies")
async def my_proxies(callback: types.CallbackQuery):
    proxies = cursor.execute("""
        SELECT proxy_data, expires_at, is_active FROM proxies 
        WHERE user_id=? AND is_sold=1 ORDER BY expires_at DESC
    """, (callback.from_user.id,)).fetchall()
    if not proxies:
        await callback.message.answer("📋 У вас пока нет активных прокси")
        await callback.answer()
        return
    text = "📋 *Ваши прокси:*\n\n"
    for p in proxies[:5]:
        status = "🟢 Активен" if p[2] else "🔴 Истёк"
        expires = datetime.strptime(p[1], "%Y-%m-%d %H:%M:%S.%f")
        text += f"{status} | До {expires.strftime('%d.%m %H:%M')}\n`{p[0]}`\n\n"
    await callback.message.answer(text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query_handler(lambda c: c.data == "balance")
async def show_balance(callback: types.CallbackQuery):
    balance = cursor.execute("SELECT balance FROM users WHERE user_id=?", (callback.from_user.id,)).fetchone()
    await callback.message.answer(f"💰 *Ваш баланс:* {balance[0] if balance else 0}₽\n"
                                  f"Для пополнения обратитесь к админу", parse_mode="Markdown")
    await callback.answer()

# ============ АВТОМАТИЧЕСКАЯ ДЕАКТИВАЦИЯ ============
async def deactivate_expired_loop():
    while True:
        try:
            count = deactivate_expired()
            if count > 0:
                logging.info(f"Деактивировано {count} истекших прокси")
        except Exception as e:
            logging.error(f"Ошибка деактивации: {e}")
        await asyncio.sleep(300)

# ============ ЗАПУСК ============
async def main():
    logging.info("🚀 Бот запущен!")
    asyncio.create_task(deactivate_expired_loop())
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
