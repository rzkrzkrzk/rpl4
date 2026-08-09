import os
import logging
import sqlite3
import asyncio
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

DB_PATH = "bot_data.db"
ADMIN_SESSION_MINUTES = 30

(WAITING_LOGIN, WAITING_PASSWORD, WAITING_CHANNEL_USERNAME, WAITING_CHAT_LINK, 
 WAITING_REPLY_TEXT, WAITING_SUPPORT_MSG, WAITING_DUEL_SHOT, WAITING_GIF_GOAL, 
 WAITING_GIF_SAVE, CARD_ADMIN_MENU, ADD_COLLECTION_NAME, ADD_CARD_RARITY, 
 ADD_CARD_COLLECTION, ADD_CARD_NAME, ADD_CARD_PHOTO, GRANT_CARD_PLAYER) = range(16)

# ---------- БД ----------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS source_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER UNIQUE,
            username TEXT,
            added_by INTEGER
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS target_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER UNIQUE,
            link TEXT,
            added_by INTEGER
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            text TEXT,
            timestamp TEXT,
            answered INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            last_activity INTEGER
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS player_stats (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            attempts INTEGER DEFAULT 0,
            goals INTEGER DEFAULT 0
        )
    ''')
    # Системы карточек
    c.execute('''
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_id INTEGER,
            name TEXT,
            rarity TEXT,
            image_id TEXT,
            FOREIGN KEY(collection_id) REFERENCES collections(id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_cards (
            user_id INTEGER,
            card_id INTEGER,
            count INTEGER DEFAULT 1,
            PRIMARY KEY(user_id, card_id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS card_claims (
            user_id INTEGER PRIMARY KEY,
            last_claim TIMESTAMP
        )
    ''')
    c.execute('INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)', ('gif_goal', ''))
    c.execute('INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)', ('gif_save', ''))
    conn.commit()
    conn.close()

init_db()

# ---------- Функции БД ----------
def get_config(key):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT value FROM bot_config WHERE key = ?', (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else ''

def set_config(key, value):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)', (key, value))
    conn.commit()
    conn.close()

def add_source_channel(chat_id, username, added_by):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO source_channels (chat_id, username, added_by) VALUES (?, ?, ?)',
              (chat_id, username, added_by))
    conn.commit()
    conn.close()

def get_source_channels():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT chat_id, username FROM source_channels')
    rows = c.fetchall()
    conn.close()
    return rows

def add_target_chat(chat_id, link, added_by):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO target_chats (chat_id, link, added_by) VALUES (?, ?, ?)',
              (chat_id, link, added_by))
    conn.commit()
    conn.close()

def get_target_chats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT chat_id, link FROM target_chats')
    rows = c.fetchall()
    conn.close()
    return rows

def add_support_message(user_id, username, text):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO support_messages (user_id, username, text, timestamp) VALUES (?, ?, ?, ?)',
              (user_id, username, text, datetime.now().isoformat()))
    conn.commit()
    return c.lastrowid

def get_unanswered_messages():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, user_id, username, text, timestamp FROM support_messages WHERE answered = 0 ORDER BY id')
    rows = c.fetchall()
    conn.close()
    return rows

def mark_answered(msg_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE support_messages SET answered = 1 WHERE id = ?', (msg_id,))
    conn.commit()
    conn.close()

def is_admin(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT last_activity FROM admins WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        last_activity = row[0]
        if last_activity and (datetime.now().timestamp() - last_activity) < ADMIN_SESSION_MINUTES * 60:
            return True
        else:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
            conn.commit()
            conn.close()
            return False
    return False

def add_admin(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO admins (user_id, last_activity) VALUES (?, ?)',
              (user_id, int(datetime.now().timestamp())))
    conn.commit()
    conn.close()

def update_admin_activity(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE admins SET last_activity = ? WHERE user_id = ?',
              (int(datetime.now().timestamp()), user_id))
    conn.commit()
    conn.close()

def remove_admin(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def check_credentials(login, password):
    credentials = {
        "goyda1488": "goydarpl",
        "rzk1488": "rzksigma",
    }
    return credentials.get(login) == password

# ---------- Статистика игроков ----------
def update_player_stats(user_id, username, scored):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO player_stats (user_id, username, attempts, goals)
        VALUES (?, ?, 1, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username = COALESCE(?, username),
            attempts = attempts + 1,
            goals = goals + ?
    ''', (user_id, username, 1 if scored else 0, username, 1 if scored else 0))
    conn.commit()
    conn.close()

def get_top_players(limit=10, min_attempts=3):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT username, attempts, goals,
               ROUND(CAST(goals AS FLOAT) / attempts * 100, 1) as percent
        FROM player_stats
        WHERE attempts >= ?
        ORDER BY percent DESC, goals DESC
        LIMIT ?
    ''', (min_attempts, limit))
    rows = c.fetchall()
    conn.close()
    return rows

# ---------- Логика карточек ----------
async def freegoyda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Проверка КД (24 часа)
    c.execute("SELECT last_claim FROM card_claims WHERE user_id = ?", (user.id,))
    row = c.fetchone()
    now = datetime.now()
    if row:
        last_claim = datetime.fromisoformat(row[0])
        if now < last_claim + timedelta(hours=24):
            wait_time = (last_claim + timedelta(hours=24)) - now
            hours, remainder = divmod(wait_time.seconds, 3600)
            minutes = remainder // 60
            await update.message.reply_text(f"⏳ Слишком рано! Новую карточку можно будет получить через {hours} ч. {minutes} мин.")
            conn.close()
            return

    # Выпадение по редкостям: Редкая, Очень редкая, Эпическая, Мифическая
    rarity = random.choices(
        ["Редкая", "Очень редкая", "Эпическая", "Мифическая"],
        weights=[55, 30, 12, 3], k=1
    )[0]
    
    c.execute('''
        SELECT cards.id, cards.name, collections.name, cards.image_id 
        FROM cards 
        JOIN collections ON cards.collection_id = collections.id 
        WHERE cards.rarity = ?
    ''', (rarity,))
    available = c.fetchall()
    
    if not available:
        await update.message.reply_text("📭 В игре пока нет карточек для выпадения. Администратор скоро их добавит!")
        conn.close()
        return

    card = random.choice(available)
    card_id, c_name, col_name, img_id = card
    
    # Сохраняем клейм и выдаем карточку
    c.execute("INSERT OR REPLACE INTO card_claims (user_id, last_claim) VALUES (?, ?)", (user.id, now.isoformat()))
    c.execute('''
        INSERT INTO user_cards (user_id, card_id, count) VALUES (?, ?, 1)
        ON CONFLICT(user_id, card_id) DO UPDATE SET count = count + 1
    ''', (user.id, card_id))
    conn.commit()
    conn.close()

    text = f"✨ **Новая карточка!**\n\n🏷 Название: {c_name}\n🌟 Редкость: {rarity}\n📁 Коллекция: {col_name}"
    if img_id:
        await update.message.reply_photo(img_id, caption=text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")

async def inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Если вызов из инлайн-кнопки крафта, подчищаем старое сообщение
    if update.callback_query:
        try:
            await update.callback_query.message.delete()
        except Exception:
            pass

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT cards.name, cards.rarity, collections.name, user_cards.count, cards.id, collections.id 
        FROM user_cards 
        JOIN cards ON user_cards.card_id = cards.id 
        JOIN collections ON cards.collection_id = collections.id 
        WHERE user_id = ?
        ORDER BY collections.name, cards.rarity
    ''', (user.id,))
    cards = c.fetchall()
    conn.close()

    if not cards:
        msg = "🎒 Твой инвентарь пуст. Используй команду /freegoyda, чтобы получить первую карточку!"
        if update.callback_query:
            await update.callback_query.message.reply_text(msg)
        else:
            await update.message.reply_text(msg)
        return

    text = "🎒 **Твой инвентарь карточек:**\n\n"
    buttons = []
    
    for name, rarity, col, count, cid, col_id in cards:
        text += f"▪️ [{rarity}] **{name}** ({col}) — `{count} шт.`\n"
        # Кнопка крафта Легендарной (требуется 5 мифических одной коллекции)
        if rarity == "Мифическая" and count >= 5:
            buttons.append([InlineKeyboardButton(f"🔨 Скрафтить Легендарную ({col})", callback_data=f"craft_{cid}_{col_id}")])

    buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="refresh_inventory")])
    markup = InlineKeyboardMarkup(buttons)

    if update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=markup)

async def inventory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "refresh_inventory":
        try:
            await query.message.delete()
        except Exception:
            pass
        await inventory(update, context)
        return

    if data.startswith("craft_"):
        _, card_id, col_id = data.split("_")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Проверяем количество мифических
        c.execute("SELECT count FROM user_cards WHERE user_id = ? AND card_id = ?", (user_id, card_id))
        res = c.fetchone()
        if not res or res[0] < 5:
            await query.answer("❌ У тебя недостаточно мифических карточек для крафта (нужно 5 штук)!", show_alert=True)
            conn.close()
            return

        # Ищем Легендарную карточку в этой же коллекции
        c.execute("SELECT id, name FROM cards WHERE collection_id = ? AND rarity = 'Легендарная' LIMIT 1", (col_id,))
        leg = c.fetchone()
        if not leg:
            await query.answer("❌ В этой коллекции еще не добавлена легендарная карточка!", show_alert=True)
            conn.close()
            return

        # Списание 5 мифических и выдача 1 легендарной
        c.execute("UPDATE user_cards SET count = count - 5 WHERE user_id = ? AND card_id = ?", (user_id, card_id))
        c.execute("DELETE FROM user_cards WHERE count <= 0")
        c.execute('''
            INSERT INTO user_cards (user_id, card_id, count) VALUES (?, ?, 1)
            ON CONFLICT(user_id, card_id) DO UPDATE SET count = count + 1
        ''', (user_id, leg[0]))
        conn.commit()
        conn.close()

        await query.answer("🎉 Успешный крафт легендарной карточки!", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
        await update.effective_message.reply_text(f"👑 Поздравляем! Ты успешно скрафтил легендарную карточку: **{leg[1]}**!", parse_mode="Markdown")
        await inventory(update, context)

# ---------- Клавиатуры ----------
def main_menu_keyboard():
    return ReplyKeyboardMarkup([["🏠 Главное меню"]], resize_keyboard=True)

def admin_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Добавить каналы", "➕ Добавить чаты"],
        ["📩 Проверить поддержку", "⚙️ Настройки"],
        ["🎮 Настройки игры", "🃏 Карточки"],
        ["🚪 Выйти"]
    ], resize_keyboard=True)

def card_admin_keyboard():
    return ReplyKeyboardMarkup([
        ["📁 Создать коллекцию", "🃏 Добавить карточку"],
        ["🎁 Выдать карточку игроку", "🔙 Назад в админку"]
    ], resize_keyboard=True)

def welcome_inline_keyboard():
    keyboard = [
        [InlineKeyboardButton("💬 Наш Discord", callback_data="discord")],
        [InlineKeyboardButton("🌐 Наш Сайт", callback_data="website")],
        [InlineKeyboardButton("🆘 Обратиться в поддержку", callback_data="support")],
        [InlineKeyboardButton("🏒 Дуэль Буллитов", callback_data="duel")]
    ]
    return InlineKeyboardMarkup(keyboard)

def duel_shot_keyboard():
    keyboard = [
        [InlineKeyboardButton("🥅 Левая девятка", callback_data="shot_left")],
        [InlineKeyboardButton("🥅 Правая девятка", callback_data="shot_right")],
        [InlineKeyboardButton("🧤 Домик (между щитков)", callback_data="shot_five")],
        [InlineKeyboardButton("🥅 Низ в угол", callback_data="shot_low")]
    ]
    return InlineKeyboardMarkup(keyboard)

# ---------- Основные обработчики ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Добро пожаловать в **Russian Puck League**!\n"
        "Выберите действие с помощью кнопок ниже.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )
    await update.message.reply_text(
        "📌 Выберите раздел:",
        reply_markup=welcome_inline_keyboard()
    )

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 Выберите раздел:",
        reply_markup=welcome_inline_keyboard()
    )

async def rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    top = get_top_players(limit=10, min_attempts=3)
    if not top:
        await update.message.reply_text("📊 Пока нет статистики. Сыграйте в «Дуэль Буллитов»!")
        return
    text = "🏆 **Топ-10 игроков по проценту голов**\n\n"
    for i, (username, attempts, goals, percent) in enumerate(top, 1):
        display_name = username or f"Игрок {i}"
        text += f"{i}. {display_name} — {goals}/{attempts} ({percent}%)\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def duel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🏒 **Дуэль Буллитов!**\n"
        "Выбери зону для броска:",
        parse_mode="Markdown",
        reply_markup=duel_shot_keyboard()
    )
    context.user_data["in_conversation"] = True
    return WAITING_DUEL_SHOT

async def inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "discord":
        await query.edit_message_text(
            "💬 **Discord Server RPL:** https://discord.gg/dgkFMCgDwx",
            parse_mode="Markdown"
        )
        await query.message.reply_text(
            "📌 Выберите другой раздел:",
            reply_markup=welcome_inline_keyboard()
        )
    elif data == "website":
        await query.edit_message_text(
            "🌐 **Сайт Russian Puck League:** rplpuck.ru",
            parse_mode="Markdown"
        )
        await query.message.reply_text(
            "📌 Выберите другой раздел:",
            reply_markup=welcome_inline_keyboard()
        )
    elif data == "support":
        context.user_data["in_conversation"] = True
        await query.edit_message_text(
            "✍️ Напишите ваше сообщение для поддержки.\n"
            "Мы ответим вам как можно скорее.\n\n"
            "Для отмены отправьте /cancel"
        )
        return WAITING_SUPPORT_MSG
    elif data == "duel":
        context.user_data["in_conversation"] = True
        await query.edit_message_text(
            "🏒 **Дуэль Буллитов!**\n"
            "Выбери зону для броска:",
            parse_mode="Markdown",
            reply_markup=duel_shot_keyboard()
        )
        return WAITING_DUEL_SHOT
    return ConversationHandler.END

async def duel_shot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    shot_zone = query.data

    goalie_zones = ["shot_left", "shot_right", "shot_five", "shot_low"]
    goalie_choice = random.choice(goalie_zones)
    scored = random.random() < 0.35

    if shot_zone == goalie_choice:
        scored = False

    user = update.effective_user
    username = user.username or user.full_name or str(user.id)
    update_player_stats(user.id, username, scored)

    if scored:
        gif = get_config('gif_goal')
        result_text = "⚡️ **ГОЛ!** Вы точно попали в девятку!"
        if not gif:
            gif = "https://media.giphy.com/media/3o7aTskHEUdgCQAXde/giphy.gif"
    else:
        gif = get_config('gif_save')
        result_text = "🧤 **СЕЙВ!** Вратарь отразил бросок!"
        if not gif:
            gif = "https://media.giphy.com/media/3o6Ztq5cG6GZj5F9uo/giphy.gif"

    await query.edit_message_text(
        f"{result_text}\n\n"
        f"Ваш бросок: **{shot_zone.replace('shot_', '').capitalize()}**\n"
        f"Вратарь выбрал: **{goalie_choice.replace('shot_', '').capitalize()}**",
        parse_mode="Markdown"
    )
    try:
        await query.message.reply_animation(gif)
    except Exception as e:
        await query.message.reply_text("❌ Не удалось отправить GIF. Проверьте настройки.")
        logger.error(f"Ошибка отправки GIF: {e}")

    await query.message.reply_text(
        "📌 Сыграйте ещё раз, написав /duelrpl или выберите другой раздел:",
        reply_markup=welcome_inline_keyboard() if update.effective_chat.type == "private" else None
    )
    context.user_data["in_conversation"] = False
    return ConversationHandler.END

# ---------- Поддержка ----------
async def support_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    if not text:
        await update.message.reply_text("Пожалуйста, напишите текст сообщения.")
        return WAITING_SUPPORT_MSG

    msg_id = add_support_message(user.id, user.username or str(user.id), text)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id FROM admins')
    admins = [row[0] for row in c.fetchall()]
    conn.close()

    if admins:
        for admin_id in admins:
            try:
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✏️ Ответить", callback_data=f"reply_{msg_id}")],
                    [InlineKeyboardButton("❌ Закрыть", callback_data=f"close_{msg_id}")]
                ])
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=f"📩 Новое обращение #{msg_id} от {user.username or user.id}:\n\n{text}",
                    reply_markup=keyboard
                )
            except Exception as e:
                logger.error(f"Не удалось отправить админу {admin_id}: {e}")
    else:
        await update.message.reply_text("⚠️ Нет активных администраторов. Сообщение сохранено.")

    await update.message.reply_text("✅ Сообщение отправлено в поддержку.")
    context.user_data["in_conversation"] = False
    await update.message.reply_text(
        "📌 Выберите раздел:",
        reply_markup=welcome_inline_keyboard()
    )
    return ConversationHandler.END

async def support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["in_conversation"] = False
    await update.message.reply_text("❌ Отправка отменена.")
    await update.message.reply_text(
        "📌 Выберите раздел:",
        reply_markup=welcome_inline_keyboard()
    )
    return ConversationHandler.END

# ---------- Админ-панель ----------
async def adminkarpl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Команда только в личных сообщениях.")
        return ConversationHandler.END
    if is_admin(update.effective_user.id):
        await update.message.reply_text("Вы уже авторизованы.", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END
    context.user_data["in_conversation"] = True
    await update.message.reply_text("🔑 Введите логин:")
    return WAITING_LOGIN

async def wait_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["in_conversation"] = True
    context.user_data["login"] = update.message.text
    await update.message.reply_text("🔒 Введите пароль:")
    return WAITING_PASSWORD

async def wait_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["in_conversation"] = True
    login = context.user_data.get("login")
    password = update.message.text
    if check_credentials(login, password):
        add_admin(update.effective_user.id)
        context.user_data.clear()
        await update.message.reply_text("✅ Авторизован!", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Неверный логин или пароль. Попробуйте /adminkarpl")
        return WAITING_PASSWORD

async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    update_admin_activity(user_id)
    text = update.message.text

    if text == "➕ Добавить каналы":
        context.user_data["in_conversation"] = True
        await update.message.reply_text("Введите @username канала (бот должен быть админом):")
        return WAITING_CHANNEL_USERNAME
    elif text == "➕ Добавить чаты":
        context.user_data["in_conversation"] = True
        await update.message.reply_text(
            "Введите числовой ID чата или @username.\n"
            "Бот должен состоять в чате.\n"
            "Узнать ID можно через /getid в нужном чате."
        )
        return WAITING_CHAT_LINK
    elif text == "📩 Проверить поддержку":
        await show_support_messages(update, context)
        return
    elif text == "⚙️ Настройки":
        await show_settings(update, context)
        return
    elif text == "🎮 Настройки игры":
        context.user_data.clear()
        await show_game_settings(update, context)
        return ConversationHandler.END
    elif text == "🃏 Карточки":
        await update.message.reply_text("🃏 Раздел управления карточками:", reply_markup=card_admin_keyboard())
        return CARD_ADMIN_MENU
    elif text == "🚪 Выйти":
        remove_admin(user_id)
        await update.message.reply_text("🚪 Вы вышли из админ-панели.", reply_markup=main_menu_keyboard())
        await update.message.reply_text("📌 Выберите раздел:", reply_markup=welcome_inline_keyboard())
        return
    return ConversationHandler.END

# ---------- Управление карточками через Админку ----------
async def admin_card_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "📁 Создать коллекцию":
        await update.message.reply_text("📁 Введите название новой коллекции:")
        return ADD_COLLECTION_NAME
    elif text == "🃏 Добавить карточку":
        kb = [["Редкая", "Очень редкая"], ["Эпическая", "Мифическая"], ["Легендарная", "Секретная"]]
        await update.message.reply_text("✨ Выберите редкость карточки:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return ADD_CARD_RARITY
    elif text == "🎁 Выдать карточку игроку":
        await update.message.reply_text("🎁 Введите ID игрока и ID карточки через пробел (например: `123456789 1`):", parse_mode="Markdown")
        return GRANT_CARD_PLAYER
    elif text == "🔙 Назад в админку":
        await update.message.reply_text("⚙️ Админ-панель:", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END
    return CARD_ADMIN_MENU

async def save_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO collections (name) VALUES (?)", (name,))
        conn.commit()
        await update.message.reply_text(f"✅ Коллекция **{name}** успешно создана!", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("❌ Такая коллекция уже существует или произошла ошибка.", reply_markup=card_admin_keyboard())
    conn.close()
    return CARD_ADMIN_MENU

async def card_set_rarity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["card_rarity"] = update.message.text.strip()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT name FROM collections")
    cols = [r[0] for r in c.fetchall()]
    conn.close()
    if not cols:
        await update.message.reply_text("❌ Сначала создайте хотя бы одну коллекцию!", reply_markup=card_admin_keyboard())
        return CARD_ADMIN_MENU
    await update.message.reply_text("📁 Выберите коллекцию:", reply_markup=ReplyKeyboardMarkup([cols], resize_keyboard=True))
    return ADD_CARD_COLLECTION

async def card_set_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["card_collection"] = update.message.text.strip()
    await update.message.reply_text("🏷 Введите название карточки:", reply_markup=ReplyKeyboardRemove())
    return ADD_CARD_NAME

async def card_set_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["card_name"] = update.message.text.strip()
    await update.message.reply_text("📸 Отправьте картинку (фотографию) карточки:")
    return ADD_CARD_PHOTO

async def card_save_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ Пожалуйста, отправьте именно фотографию!")
        return ADD_CARD_PHOTO

    photo_id = update.message.photo[-1].file_id
    rarity = context.user_data.get("card_rarity")
    col_name = context.user_data.get("card_collection")
    name = context.user_data.get("card_name")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id FROM collections WHERE name = ?", (col_name,))
    row = c.fetchone()
    if not row:
        await update.message.reply_text("❌ Ошибка: коллекция не найдена.", reply_markup=card_admin_keyboard())
        conn.close()
        return CARD_ADMIN_MENU

    col_id = row[0]
    c.execute("INSERT INTO cards (collection_id, name, rarity, image_id) VALUES (?, ?, ?, ?)", (col_id, name, rarity, photo_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Карточка **{name}** ({rarity}) успешно добавлена!", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
    return CARD_ADMIN_MENU

async def grant_card_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.strip().split()
        if len(parts) != 2:
            raise ValueError()
        user_id, card_id = int(parts[0]), int(parts[1])

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT name FROM cards WHERE id = ?", (card_id,))
        card = c.fetchone()
        if not card:
            await update.message.reply_text("❌ Карточка с таким ID не найдена.", reply_markup=card_admin_keyboard())
            conn.close()
            return CARD_ADMIN_MENU

        c.execute('''
            INSERT INTO user_cards (user_id, card_id, count) VALUES (?, ?, 1)
            ON CONFLICT(user_id, card_id) DO UPDATE SET count = count + 1
        ''', (user_id, card_id))
        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ Игроку {user_id} успешно выдана карточка **{card[0]}** (ID: {card_id})!", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
        try:
            await context.bot.send_message(user_id, f"🎁 Администратор выдал вам карточку: **{card[0]}**!", parse_mode="Markdown")
        except Exception:
            pass
    except Exception:
        await update.message.reply_text("❌ Ошибка формата! Введите `ID_игрока ID_карточки` цифрами.", parse_mode="Markdown", reply_markup=card_admin_keyboard())
    return CARD_ADMIN_MENU

# ---------- Добавление каналов и чатов ----------
async def add_channel_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    if not username.startswith('@'):
        username = '@' + username
    try:
        chat = await context.bot.get_chat(username)
        chat_id = chat.id
        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if bot_member.status not in ['administrator', 'creator']:
            await update.message.reply_text("❌ Бот не администратор.")
            context.user_data["in_conversation"] = False
            return ConversationHandler.END
        add_source_channel(chat_id, username, update.effective_user.id)
        await update.message.reply_text(f"✅ Канал {username} добавлен.", reply_markup=admin_menu_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
    context.user_data["in_conversation"] = False
    return ConversationHandler.END

async def add_chat_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    chat_id = None
    username = None

    if link.startswith('@'):
        username = link
    elif link.startswith('https://t.me/'):
        parts = link.split('/')
        if len(parts) >= 4:
            candidate = parts[-1]
            if candidate and not candidate.startswith('joinchat') and not candidate.startswith('+'):
                username = '@' + candidate
            else:
                await update.message.reply_text("❌ Приватная ссылка не поддерживается. Используйте ID.")
                context.user_data["in_conversation"] = False
                return ConversationHandler.END
    else:
        try:
            chat_id = int(link)
        except ValueError:
            username = '@' + link

    try:
        if username:
            chat = await context.bot.get_chat(username)
            chat_id = chat.id
        elif chat_id is not None:
            chat = await context.bot.get_chat(chat_id)
        else:
            await update.message.reply_text("❌ Неверный формат.")
            context.user_data["in_conversation"] = False
            return ConversationHandler.END

        bot_member = await context.bot.get_chat_member(chat_id, context.bot.id)
        if bot_member.status not in ['member', 'administrator', 'creator']:
            await update.message.reply_text("❌ Бот не состоит в чате.")
            context.user_data["in_conversation"] = False
            return ConversationHandler.END

        add_target_chat(chat_id, link, update.effective_user.id)
        await update.message.reply_text(f"✅ Чат {link} добавлен.", reply_markup=admin_menu_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
    context.user_data["in_conversation"] = False
    return ConversationHandler.END

async def show_support_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    messages = get_unanswered_messages()
    if not messages:
        await update.message.reply_text("📭 Новых обращений нет.", reply_markup=admin_menu_keyboard())
        return
    msg = messages[0]
    msg_id, user_id, username, text, timestamp = msg
    display_text = (
        f"📩 Обращение #{msg_id}\n"
        f"👤 {username or user_id}\n"
        f"🕒 {timestamp}\n\n"
        f"{text}"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Ответить", callback_data=f"reply_{msg_id}")],
        [InlineKeyboardButton("✅ Закрыть", callback_data=f"close_{msg_id}")],
        [InlineKeyboardButton("⏩ Следующее", callback_data="next_support")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_admin")]
    ])
    await update.message.reply_text(display_text, reply_markup=keyboard)

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sources = get_source_channels()
    targets = get_target_chats()
    text = "📋 **Настройки**\n\n📢 **Каналы-источники:**\n"
    if sources:
        for chat_id, username in sources:
            text += f"  - {username or chat_id} (ID: {chat_id})\n"
    else:
        text += "  (нет)\n"
    text += "\n📥 **Целевые чаты:**\n"
    if targets:
        for chat_id, link in targets:
            text += f"  - {link or chat_id} (ID: {chat_id})\n"
    else:
        text += "  (нет)\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_menu_keyboard())

async def show_game_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    goal_gif = get_config('gif_goal') or 'не установлен'
    save_gif = get_config('gif_save') or 'не установлен'
    text = (
        "🎮 **Настройки игры «Дуэль Буллитов»**\n\n"
        f"⚡️ **GIF гола:** {goal_gif}\n"
        f"🧤 **GIF сейва:** {save_gif}\n\n"
        "Выберите действие:"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Посмотреть GIF гола", callback_data="view_goal_gif"),
         InlineKeyboardButton("👁 Посмотреть GIF сейва", callback_data="view_save_gif")],
        [InlineKeyboardButton("🔄 Изменить GIF гола", callback_data="change_goal_gif"),
         InlineKeyboardButton("🔄 Изменить GIF сейва", callback_data="change_save_gif")],
        [InlineKeyboardButton("🔙 Назад", callback_data="back_to_admin")]
    ])
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

async def view_gif(update: Update, context: ContextTypes.DEFAULT_TYPE, gif_type):
    query = update.callback_query
    await query.answer()
    gif = get_config('gif_goal' if gif_type == 'goal' else 'gif_save')
    if gif:
        try:
            await query.message.reply_animation(gif)
        except Exception as e:
            await query.message.reply_text("❌ Не удалось отправить GIF.")
            logger.error(f"Ошибка отправки GIF: {e}")
    else:
        await query.message.reply_text("❌ GIF не установлен.")

async def change_gif_start(update: Update, context: ContextTypes.DEFAULT_TYPE, gif_type):
    query = update.callback_query
    await query.answer()
    context.user_data["gif_type"] = gif_type
    context.user_data["in_conversation"] = True
    await query.edit_message_text(
        f"📤 Отправьте **GIF-файл** для {'гола' if gif_type == 'goal' else 'сейва'}.\n"
        "Для отмены отправьте /cancel"
    )
    return WAITING_GIF_GOAL if gif_type == 'goal' else WAITING_GIF_SAVE

async def receive_gif(update: Update, context: ContextTypes.DEFAULT_TYPE, gif_type):
    message = update.message
    file_id = None
    if message.animation:
        file_id = message.animation.file_id
    elif message.document and message.document.mime_type and 'gif' in message.document.mime_type:
        file_id = message.document.file_id
    else:
        await update.message.reply_text("❌ Пожалуйста, отправьте GIF-файл.")
        return WAITING_GIF_GOAL if gif_type == 'goal' else WAITING_GIF_SAVE

    if file_id:
        key = 'gif_goal' if gif_type == 'goal' else 'gif_save'
        set_config(key, file_id)
        await update.message.reply_text("✅ GIF сохранён!", reply_markup=admin_menu_keyboard())
        context.user_data.clear()
        await show_game_settings(update, context)
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Не удалось получить file_id.")
        context.user_data["in_conversation"] = False
        return ConversationHandler.END

async def cancel_gif_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Диалог отменён.", reply_markup=admin_menu_keyboard())
    return ConversationHandler.END

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if not is_admin(user_id):
        await query.edit_message_text("⛔️ Сессия истекла.")
        return

    update_admin_activity(user_id)

    if data.startswith("reply_"):
        msg_id = int(data.split("_")[1])
        context.user_data["reply_to"] = msg_id
        context.user_data["in_conversation"] = True
        await query.edit_message_text("✏️ Введите текст ответа:")
        return WAITING_REPLY_TEXT
    elif data.startswith("close_"):
        msg_id = int(data.split("_")[1])
        mark_answered(msg_id)
        await query.edit_message_text("✅ Обращение закрыто.")
        messages = get_unanswered_messages()
        if messages:
            await show_support_messages(update, context)
        else:
            await query.message.reply_text("📭 Больше нет обращений.", reply_markup=admin_menu_keyboard())
    elif data == "next_support":
        await query.message.delete()
        messages = get_unanswered_messages()
        if messages:
            msg = messages[0]
            msg_id, user_id, username, text, timestamp = msg
            display_text = f"📩 Обращение #{msg_id}\n👤 {username or user_id}\n🕒 {timestamp}\n\n{text}"
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Ответить", callback_data=f"reply_{msg_id}")],
                [InlineKeyboardButton("✅ Закрыть", callback_data=f"close_{msg_id}")],
                [InlineKeyboardButton("⏩ Следующее", callback_data="next_support")],
                [InlineKeyboardButton("🔙 Назад", callback_data="back_to_admin")]
            ])
            await query.message.reply_text(display_text, reply_markup=keyboard)
        else:
            await query.message.reply_text("📭 Больше нет обращений.", reply_markup=admin_menu_keyboard())
    elif data == "back_to_admin":
        await query.message.delete()
        await query.message.reply_text("🔙 Возврат в админ-панель.", reply_markup=admin_menu_keyboard())
    elif data == "change_goal_gif":
        return await change_gif_start(update, context, 'goal')
    elif data == "change_save_gif":
        return await change_gif_start(update, context, 'save')
    elif data == "view_goal_gif":
        await view_gif(update, context, 'goal')
    elif data == "view_save_gif":
        await view_gif(update, context, 'save')
    return ConversationHandler.END

async def reply_to_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply_text = update.message.text
    msg_id = context.user_data.get("reply_to")
    if not msg_id:
        await update.message.reply_text("❌ Нет обращения для ответа.")
        context.user_data["in_conversation"] = False
        return ConversationHandler.END

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id FROM support_messages WHERE id = ?', (msg_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        await update.message.reply_text("❌ Обращение не найдено.")
        context.user_data["in_conversation"] = False
        return ConversationHandler.END

    user_id = row[0]
    try:
        await context.bot.send_message(chat_id=user_id, text=f"📨 Ответ поддержки:\n{reply_text}")
        mark_answered(msg_id)
        await update.message.reply_text("✅ Ответ отправлен.", reply_markup=admin_menu_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка отправки: {e}")
    context.user_data["in_conversation"] = False
    return ConversationHandler.END

async def forward_from_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel_post = update.channel_post
    if not channel_post:
        return
    chat_id = channel_post.chat_id
    sources = get_source_channels()
    source_ids = [s[0] for s in sources]
    if chat_id not in source_ids:
        return

    text = channel_post.text or channel_post.caption or ""
    if not any(tag in text for tag in ["#MatchDay", "#Results", "#rplpuck"]):
        return

    targets = get_target_chats()
    for target_id, _ in targets:
        try:
            await channel_post.copy(chat_id=target_id)
        except Exception as e:
            logger.error(f"Ошибка пересылки: {e}")

async def handle_unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    if update.message.text and update.message.text.startswith('/'):
        return
    if context.user_data.get("in_conversation", False):
        return
    if context.user_data.get("login"):
        return

    text = update.message.text
    if text in ["🏠 Главное меню", "➕ Добавить каналы", "➕ Добавить чаты", 
                "📩 Проверить поддержку", "⚙️ Настройки", "🎮 Настройки игры", "🃏 Карточки", "🚪 Выйти"]:
        return

    try:
        user_msg = update.message
        error_msg = await update.message.reply_text("❌ Ошибка! Не выбран модуль запроса.")
        await asyncio.sleep(3)
        await user_msg.delete()
        await error_msg.delete()
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")

async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(f"🆔 ID этого чата: `{chat.id}`", parse_mode="Markdown")

# ---------- MAIN ----------
def main():
    app = Application.builder().token(TOKEN).build()

    # Диалоги авторизации и админки
    conv_auth = ConversationHandler(
        entry_points=[CommandHandler("adminkarpl", adminkarpl)],
        states={
            WAITING_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, wait_login)],
            WAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, wait_password)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
        allow_reentry=True,
    )
    app.add_handler(conv_auth)

    conv_channel = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить каналы$") & filters.ChatType.PRIVATE, admin_buttons)],
        states={
            WAITING_CHANNEL_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_username)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
        allow_reentry=True,
    )
    app.add_handler(conv_channel)

    conv_chat = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить чаты$") & filters.ChatType.PRIVATE, admin_buttons)],
        states={
            WAITING_CHAT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_chat_link)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
        allow_reentry=True,
    )
    app.add_handler(conv_chat)

    conv_reply = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^reply_")],
        states={
            WAITING_REPLY_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, reply_to_support)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
        allow_reentry=True,
    )
    app.add_handler(conv_reply)

    conv_support = ConversationHandler(
        entry_points=[CallbackQueryHandler(inline_callback, pattern="^support$")],
        states={
            WAITING_SUPPORT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive)],
        },
        fallbacks=[CommandHandler("cancel", support_cancel)],
        allow_reentry=True,
    )
    app.add_handler(conv_support)

    conv_duel = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(inline_callback, pattern="^duel$"),
            CommandHandler("duelrpl", duel_command)
        ],
        states={
            WAITING_DUEL_SHOT: [CallbackQueryHandler(duel_shot, pattern="^shot_")],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Игра отменена."))],
        allow_reentry=True,
    )
    app.add_handler(conv_duel)

    conv_gif_goal = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^change_goal_gif$")],
        states={
            WAITING_GIF_GOAL: [
                MessageHandler(filters.ANIMATION | filters.Document.ALL, lambda u,c: receive_gif(u,c,'goal')),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cancel_gif_dialog)
            ],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
        allow_reentry=True,
    )
    app.add_handler(conv_gif_goal)

    conv_gif_save = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^change_save_gif$")],
        states={
            WAITING_GIF_SAVE: [
                MessageHandler(filters.ANIMATION | filters.Document.ALL, lambda u,c: receive_gif(u,c,'save')),
                MessageHandler(filters.TEXT & ~filters.COMMAND, cancel_gif_dialog)
            ],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
        allow_reentry=True,
    )
    app.add_handler(conv_gif_save)

    # --- ДИАЛОГ УПРАВЛЕНИЯ КАРТОЧКАМИ В АДМИНКЕ ---
    conv_cards = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🃏 Карточки$") & filters.ChatType.PRIVATE, admin_buttons)],
        states={
            CARD_ADMIN_MENU: [
                MessageHandler(filters.Regex("^📁 Создать коллекцию$"), add_col_start),
                MessageHandler(filters.Regex("^🃏 Добавить карточку$"), add_card_start),
                MessageHandler(filters.Regex("^🎁 Выдать карточку игроку$"), grant_card_start),
            ],
            ADD_COLLECTION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_collection)],
            ADD_CARD_RARITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_set_rarity)],
            ADD_CARD_COLLECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_set_collection)],
            ADD_CARD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_set_name)],
            ADD_CARD_PHOTO: [MessageHandler(filters.PHOTO, card_save_all)],
            GRANT_CARD_PLAYER: [MessageHandler(filters.TEXT & ~filters.COMMAND, grant_card_execute)],
        },
        fallbacks=[
            MessageHandler(filters.Regex("^🔙 Назад в админку$"), lambda u,c: u.message.reply_text("⚙️ Админ-панель:", reply_markup=admin_menu_keyboard())),
            CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))
        ],
        allow_reentry=True,
    )
    app.add_handler(conv_cards)

    # Обработчики общих кнопок админ-меню
    app.add_handler(MessageHandler(filters.Regex("^(📩 Проверить поддержку|⚙️ Настройки|🎮 Настройки игры|🚪 Выйти)$") & filters.ChatType.PRIVATE, admin_buttons))
    app.add_handler(MessageHandler(filters.Regex("^🏠 Главное меню$") & filters.ChatType.PRIVATE, main_menu))

    # Inline колбэки
    app.add_handler(CallbackQueryHandler(inline_callback, pattern="^(discord|website)$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(close_|next_support|back_to_admin|view_goal_gif|view_save_gif)$"))
    app.add_handler(CallbackQueryHandler(inventory_callback, pattern="^(craft_|refresh_inventory)"))

    # Пересылка из каналов
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, forward_from_channels))

    # Автоудаление неизвестных сообщений
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_unknown_message), group=999)

    # Команды пользователей
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rating", rating))
    app.add_handler(CommandHandler("getid", getid))
    app.add_handler(CommandHandler("freegoyda", freegoyda))
    app.add_handler(CommandHandler("inventory", inventory))
    app.add_handler(CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено.")))

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
