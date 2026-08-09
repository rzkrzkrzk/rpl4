import os
import logging
import sqlite3
import asyncio
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
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

WAITING_LOGIN, WAITING_PASSWORD, WAITING_CHANNEL_USERNAME, WAITING_CHAT_LINK, WAITING_REPLY_TEXT, WAITING_SUPPORT_MSG = range(6)
WAITING_DUEL_SHOT = 10
WAITING_GIF_GOAL = 11
WAITING_GIF_SAVE = 12
WAITING_CARD_RARITY = 13
WAITING_CARD_COLLECTION = 14
WAITING_CARD_NAME = 15
WAITING_CARD_IMAGE = 16
WAITING_CARD_ID_TO_GIVE = 17
WAITING_USER_ID_TO_GIVE = 18
WAITING_NEW_COLLECTION_NAME = 19

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
    c.execute('INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)', ('gif_goal', ''))
    c.execute('INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)', ('gif_save', ''))

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
            rarity TEXT,
            name TEXT,
            image_file_id TEXT,
            FOREIGN KEY (collection_id) REFERENCES collections(id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_cards (
            user_id INTEGER,
            card_id INTEGER,
            quantity INTEGER DEFAULT 1,
            PRIMARY KEY (user_id, card_id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_last_free (
            user_id INTEGER PRIMARY KEY,
            last_time INTEGER
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ---------- Функции БД (существующие) ----------
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

# ---------- Функции для карточек ----------
def create_collection(name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('INSERT INTO collections (name) VALUES (?)', (name,))
        conn.commit()
        return c.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()

def get_collections():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, name FROM collections')
    rows = c.fetchall()
    conn.close()
    return rows

def get_collection_id(name):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id FROM collections WHERE name = ?', (name,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None

def add_card(collection_id, rarity, name, image_file_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO cards (collection_id, rarity, name, image_file_id) VALUES (?, ?, ?, ?)',
              (collection_id, rarity, name, image_file_id))
    conn.commit()
    card_id = c.lastrowid
    conn.close()
    return card_id

def get_card(card_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, collection_id, rarity, name, image_file_id FROM cards WHERE id = ?', (card_id,))
    row = c.fetchone()
    conn.close()
    return row

def get_all_cards():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, collection_id, rarity, name, image_file_id FROM cards')
    rows = c.fetchall()
    conn.close()
    return rows

def get_cards_by_rarity(rarity):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, collection_id, name, image_file_id FROM cards WHERE rarity = ?', (rarity,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_cards_by_collection_and_rarity(collection_id, rarity):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT id, name, image_file_id FROM cards WHERE collection_id = ? AND rarity = ?', (collection_id, rarity))
    rows = c.fetchall()
    conn.close()
    return rows

def give_card_to_user(user_id, card_id, quantity=1):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO user_cards (user_id, card_id, quantity)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id, card_id) DO UPDATE SET
            quantity = quantity + ?
    ''', (user_id, card_id, quantity, quantity))
    conn.commit()
    conn.close()

def get_user_cards(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT c.id, c.collection_id, col.name, c.rarity, c.name, c.image_file_id, uc.quantity
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        JOIN collections col ON c.collection_id = col.id
        WHERE uc.user_id = ?
    ''', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_user_cards_by_collection(user_id):
    rows = get_user_cards(user_id)
    result = {}
    for row in rows:
        card_id, coll_id, coll_name, rarity, card_name, image_file_id, quantity = row
        if coll_name not in result:
            result[coll_name] = []
        result[coll_name].append((card_id, rarity, card_name, quantity, image_file_id))
    return result

def get_user_last_free(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT last_time FROM user_last_free WHERE user_id = ?', (user_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def set_user_last_free(user_id, timestamp):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO user_last_free (user_id, last_time) VALUES (?, ?)', (user_id, timestamp))
    conn.commit()
    conn.close()

def get_mythical_count_by_collection(user_id, collection_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT SUM(uc.quantity)
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        WHERE uc.user_id = ? AND c.collection_id = ? AND c.rarity = 'Мифическая'
    ''', (user_id, collection_id))
    row = c.fetchone()
    conn.close()
    return row[0] if row[0] else 0

def get_legendary_card_for_collection(collection_id):
    cards = get_cards_by_collection_and_rarity(collection_id, 'Легендарная')
    if cards:
        return random.choice(cards)
    return None

def deduct_user_cards(user_id, card_id, quantity):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE user_cards SET quantity = quantity - ? WHERE user_id = ? AND card_id = ?', (quantity, user_id, card_id))
    c.execute('DELETE FROM user_cards WHERE user_id = ? AND card_id = ? AND quantity <= 0', (user_id, card_id))
    conn.commit()
    conn.close()

def craft_legendary(user_id, collection_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT uc.card_id, uc.quantity
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        WHERE uc.user_id = ? AND c.collection_id = ? AND c.rarity = 'Мифическая'
    ''', (user_id, collection_id))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return False, "Нет мифических карт в этой коллекции."

    total = sum(q for _, q in rows)
    if total < 5:
        return False, f"Недостаточно мифических карт. Есть {total}, нужно 5."

    to_deduct = 5
    for card_id, qty in rows:
        if to_deduct <= 0:
            break
        deduct_now = min(qty, to_deduct)
        deduct_user_cards(user_id, card_id, deduct_now)
        to_deduct -= deduct_now

    legendary = get_legendary_card_for_collection(collection_id)
    if not legendary:
        return False, "В этой коллекции нет легендарной карты. Обратитесь к администратору."

    leg_card_id, leg_name, leg_image = legendary
    give_card_to_user(user_id, leg_card_id, 1)
    return True, f"Вы скрафтили легендарную карту «{leg_name}»!"

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

def cards_admin_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Создать коллекцию", "🆕 Добавить карточку"],
        ["🎁 Выдать карточку игроку", "🔙 Назад в админ-панель"]
    ], resize_keyboard=True)

def welcome_inline_keyboard():
    keyboard = [
        [InlineKeyboardButton("💬 Наш Discord", callback_data="discord")],
        [InlineKeyboardButton("🌐 Наш Сайт", callback_data="website")],
        [InlineKeyboardButton("🆘 Обратиться в поддержку", callback_data="support")],
        [InlineKeyboardButton("🏒 Дуэль Буллитов", callback_data="duel")],
        [InlineKeyboardButton("🃏 Получить карточку", callback_data="free_card")]
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

async def free_card_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    last_time = get_user_last_free(user_id)
    now = int(datetime.now().timestamp())
    if last_time and (now - last_time) < 86400:
        remaining = 86400 - (now - last_time)
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await update.message.reply_text(f"⏳ Вы уже получали карточку сегодня. Подождите {hours} ч {minutes} мин.")
        return

    rarity_pool = [
        ('Редкая', 0.50),
        ('Очень редкая', 0.25),
        ('Эпическая', 0.15),
        ('Мифическая', 0.10)
    ]
    rarities = [r for r, _ in rarity_pool]
    weights = [w for _, w in rarity_pool]
    chosen_rarity = random.choices(rarities, weights=weights, k=1)[0]

    cards = get_cards_by_rarity(chosen_rarity)
    if not cards:
        await update.message.reply_text("😕 К сожалению, карточек этой редкости пока нет. Попробуйте позже.")
        return

    card = random.choice(cards)
    card_id, collection_id, card_name, image_file_id = card

    give_card_to_user(user_id, card_id, 1)
    set_user_last_free(user_id, now)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT name FROM collections WHERE id = ?', (collection_id,))
    coll_row = c.fetchone()
    conn.close()
    collection_name = coll_row[0] if coll_row else "Неизвестная"

    caption = (
        f"🃏 **Новая карточка!**\n\n"
        f"📛 **Название:** {card_name}\n"
        f"⭐ **Редкость:** {chosen_rarity}\n"
        f"📚 **Коллекция:** {collection_name}"
    )
    try:
        await update.message.reply_animation(image_file_id, caption=caption, parse_mode="Markdown")
    except Exception:
        try:
            await update.message.reply_photo(image_file_id, caption=caption, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось отправить изображение карточки. Ошибка: {e}")
            logger.error(f"Ошибка отправки карточки: {e}")

async def inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_cards = get_user_cards_by_collection(user_id)

    if not user_cards:
        await update.message.reply_text("📭 У вас пока нет карточек. Используйте /freegoyda, чтобы получить первую!")
        return

    text = "🎒 **Ваш инвентарь карточек:**\n\n"
    keyboard_buttons = []

    for collection, cards in user_cards.items():
        text += f"📚 **{collection}**\n"
        rarity_groups = {}
        for card_id, rarity, card_name, qty, img in cards:
            if rarity not in rarity_groups:
                rarity_groups[rarity] = []
            rarity_groups[rarity].append((card_name, qty))
        for rarity, items in rarity_groups.items():
            text += f"  {rarity}:\n"
            for name, qty in items:
                text += f"    • {name} ×{qty}\n"
        text += "\n"

        coll_id = get_collection_id(collection)
        if coll_id:
            mythical_count = get_mythical_count_by_collection(user_id, coll_id)
            if mythical_count >= 5:
                leg = get_legendary_card_for_collection(coll_id)
                if leg:
                    keyboard_buttons.append([InlineKeyboardButton(
                        f"⚒️ Скрафтить легендарную из {collection} (5 мифических)",
                        callback_data=f"craft_{coll_id}"
                    )])

    if not keyboard_buttons:
        text += "❌ Нет доступных крафтов (нужно 5 мифических одной коллекции и наличие легендарной карты в ней)."

    if 'inventory_msg_id' in context.user_data:
        try:
            await context.bot.delete_message(chat_id=update.effective_chat.id,
                                             message_id=context.user_data['inventory_msg_id'])
        except Exception:
            pass

    reply_markup = InlineKeyboardMarkup(keyboard_buttons) if keyboard_buttons else None
    sent = await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    context.user_data['inventory_msg_id'] = sent.message_id

    try:
        await update.message.delete()
    except Exception:
        pass

async def inventory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("craft_"):
        collection_id = int(data.split("_")[1])
        user_id = query.from_user.id

        mythical_count = get_mythical_count_by_collection(user_id, collection_id)
        if mythical_count < 5:
            await query.edit_message_text("❌ У вас недостаточно мифических карт в этой коллекции.")
            return

        leg = get_legendary_card_for_collection(collection_id)
        if not leg:
            await query.edit_message_text("❌ В этой коллекции нет легендарной карты. Обратитесь к администратору.")
            return

        success, msg = craft_legendary(user_id, collection_id)
        if success:
            if 'inventory_msg_id' in context.user_data:
                try:
                    await context.bot.delete_message(chat_id=query.message.chat.id,
                                                     message_id=context.user_data['inventory_msg_id'])
                except Exception:
                    pass
            await inventory_command(update, context)
            await query.message.reply_text(f"✅ {msg}")
        else:
            await query.edit_message_text(f"❌ {msg}")

# ---------- Inline-колбэки ----------
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
    elif data == "free_card":
        await free_card_command(update, context)
        return
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
        await update.message.reply_text("⛔️ Сессия истекла. Авторизуйтесь через /adminkarpl.")
        await update.message.reply_text("📌 Выберите раздел:", reply_markup=welcome_inline_keyboard())
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
        context.user_data.clear()
        await show_cards_admin_menu(update, context)
        return ConversationHandler.END
    elif text == "🚪 Выйти":
        remove_admin(user_id)
        await update.message.reply_text("🚪 Вы вышли из админ-панели.", reply_markup=main_menu_keyboard())
        await update.message.reply_text("📌 Выберите раздел:", reply_markup=welcome_inline_keyboard())
        return
    else:
        await update.message.reply_text("Используйте кнопки меню.", reply_markup=admin_menu_keyboard())
        return
    return ConversationHandler.END

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

# ---------- Админ-раздел карточек ----------
async def show_cards_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🃏 **Управление карточками**\n"
        "Выберите действие:",
        parse_mode="Markdown",
        reply_markup=cards_admin_menu_keyboard()
    )

async def cards_admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔️ Сессия истекла. Авторизуйтесь через /adminkarpl.")
        return

    update_admin_activity(user_id)
    text = update.message.text

    if text == "➕ Создать коллекцию":
        context.user_data["in_conversation"] = True
        await update.message.reply_text("📝 Введите название новой коллекции:")
        return WAITING_NEW_COLLECTION_NAME
    elif text == "🆕 Добавить карточку":
        context.user_data["in_conversation"] = True
        await update.message.reply_text(
            "Выберите редкость карточки:\n"
            "Напишите одно из: Редкая, Очень редкая, Эпическая, Мифическая, Легендарная, Секретная"
        )
        return WAITING_CARD_RARITY
    elif text == "🎁 Выдать карточку игроку":
        context.user_data["in_conversation"] = True
        cards = get_all_cards()
        if not cards:
            await update.message.reply_text("❌ Нет карточек. Сначала добавьте карточку.")
            return
        msg = "📋 Список карточек (ID - Название):\n"
        for cid, coll_id, rarity, name, img in cards:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT name FROM collections WHERE id = ?', (coll_id,))
            coll_row = c.fetchone()
            conn.close()
            coll_name = coll_row[0] if coll_row else "?"
            msg += f"{cid} - {name} ({rarity}, {coll_name})\n"
        await update.message.reply_text(msg)
        await update.message.reply_text("✏️ Введите ID карточки, которую хотите выдать:")
        return WAITING_CARD_ID_TO_GIVE
    elif text == "🔙 Назад в админ-панель":
        context.user_data.clear()
        await update.message.reply_text("🔙 Возврат в админ-панель.", reply_markup=admin_menu_keyboard())
        return
    else:
        await update.message.reply_text("Используйте кнопки меню.", reply_markup=cards_admin_menu_keyboard())
        return

# Обработчики состояний для админ-карточек
async def create_collection_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Название не может быть пустым. Попробуйте снова.")
        return WAITING_NEW_COLLECTION_NAME
    coll_id = create_collection(name)
    if coll_id:
        await update.message.reply_text(f"✅ Коллекция «{name}» создана (ID: {coll_id}).", reply_markup=cards_admin_menu_keyboard())
    else:
        await update.message.reply_text("❌ Коллекция с таким названием уже существует.", reply_markup=cards_admin_menu_keyboard())
    context.user_data["in_conversation"] = False
    return ConversationHandler.END

async def card_rarity_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rarity = update.message.text.strip()
    valid_rarities = ["Редкая", "Очень редкая", "Эпическая", "Мифическая", "Легендарная", "Секретная"]
    if rarity not in valid_rarities:
        await update.message.reply_text("❌ Неверная редкость. Выберите из: " + ", ".join(valid_rarities))
        return WAITING_CARD_RARITY
    context.user_data["card_rarity"] = rarity
    collections = get_collections()
    if not collections:
        await update.message.reply_text("❌ Нет коллекций. Сначала создайте коллекцию.")
        context.user_data["in_conversation"] = False
        return ConversationHandler.END
    msg = "📚 Выберите коллекцию, отправив её ID:\n"
    for cid, name in collections:
        msg += f"{cid} - {name}\n"
    await update.message.reply_text(msg)
    return WAITING_CARD_COLLECTION

async def card_collection_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        coll_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введите числовой ID коллекции.")
        return WAITING_CARD_COLLECTION
    collections = get_collections()
    if coll_id not in [c[0] for c in collections]:
        await update.message.reply_text("❌ Коллекция с таким ID не найдена.")
        return WAITING_CARD_COLLECTION
    context.user_data["card_collection_id"] = coll_id
    await update.message.reply_text("📝 Введите название карточки:")
    return WAITING_CARD_NAME

async def card_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Название не может быть пустым.")
        return WAITING_CARD_NAME
    context.user_data["card_name"] = name
    await update.message.reply_text("🖼 Теперь отправьте изображение карточки (фото или GIF).")
    return WAITING_CARD_IMAGE

async def card_image_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    file_id = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.animation:
        file_id = message.animation.file_id
    elif message.document and message.document.mime_type and 'image' in message.document.mime_type:
        file_id = message.document.file_id
    else:
        await update.message.reply_text("❌ Пожалуйста, отправьте фото, GIF или изображение в документе.")
        return WAITING_CARD_IMAGE

    if file_id:
        rarity = context.user_data.get("card_rarity")
        coll_id = context.user_data.get("card_collection_id")
        name = context.user_data.get("card_name")
        card_id = add_card(coll_id, rarity, name, file_id)
        await update.message.reply_text(f"✅ Карточка «{name}» добавлена (ID: {card_id}).", reply_markup=cards_admin_menu_keyboard())
        context.user_data.clear()
        context.user_data["in_conversation"] = False
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Не удалось получить изображение. Попробуйте ещё раз.")
        return WAITING_CARD_IMAGE

async def card_give_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        card_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Введите числовой ID карточки.")
        return WAITING_CARD_ID_TO_GIVE
    card = get_card(card_id)
    if not card:
        await update.message.reply_text("❌ Карточка с таким ID не найдена.")
        return WAITING_CARD_ID_TO_GIVE
    context.user_data["give_card_id"] = card_id
    await update.message.reply_text("👤 Введите ID пользователя (число) или @username, которому выдать карточку:")
    return WAITING_USER_ID_TO_GIVE

async def card_give_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    user_id = None
    if user_input.startswith('@'):
        try:
            chat = await context.bot.get_chat(user_input)
            user_id = chat.id
        except Exception as e:
            await update.message.reply_text(f"❌ Не удалось найти пользователя: {e}")
            return WAITING_USER_ID_TO_GIVE
    else:
        try:
            user_id = int(user_input)
        except ValueError:
            await update.message.reply_text("❌ Введите числовой ID или @username.")
            return WAITING_USER_ID_TO_GIVE

    card_id = context.user_data.get("give_card_id")
    if not card_id:
        await update.message.reply_text("❌ Ошибка: ID карточки не найден.")
        return ConversationHandler.END

    give_card_to_user(user_id, card_id, 1)
    await update.message.reply_text(f"✅ Карточка (ID {card_id}) выдана пользователю {user_id}.", reply_markup=cards_admin_menu_keyboard())
    context.user_data.clear()
    context.user_data["in_conversation"] = False
    return ConversationHandler.END

# ---------- Просмотр поддержки и настроек ----------
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
            await query.message.reply_text("❌ Не удалось отправить GIF. Возможно, файл устарел. Попробуйте обновить его через 'Изменить'.")
            logger.error(f"Ошибка отправки GIF при просмотре: {e}")
    else:
        await query.message.reply_text("❌ GIF не установлен. Установите его через 'Изменить'.")

async def change_gif_start(update: Update, context: ContextTypes.DEFAULT_TYPE, gif_type):
    query = update.callback_query
    await query.answer()
    context.user_data["gif_type"] = gif_type
    context.user_data["in_conversation"] = True
    await query.edit_message_text(
        f"📤 Отправьте **GIF-файл** для {'гола' if gif_type == 'goal' else 'сейва'}.\n"
        "Просто перешлите GIF или загрузите файл.\n"
        "Для отмены отправьте /cancel"
    )
    if gif_type == 'goal':
        return WAITING_GIF_GOAL
    else:
        return WAITING_GIF_SAVE

async def receive_gif(update: Update, context: ContextTypes.DEFAULT_TYPE, gif_type):
    message = update.message
    file_id = None
    if message.animation:
        file_id = message.animation.file_id
    elif message.document and message.document.mime_type and 'gif' in message.document.mime_type:
        file_id = message.document.file_id
    else:
        await update.message.reply_text("❌ Пожалуйста, отправьте GIF-файл (анимацию или документ).")
        return WAITING_GIF_GOAL if gif_type == 'goal' else WAITING_GIF_SAVE

    if file_id:
        key = 'gif_goal' if gif_type == 'goal' else 'gif_save'
        set_config(key, file_id)
        await update.message.reply_text("✅ GIF сохранён!", reply_markup=admin_menu_keyboard())
        context.user_data.clear()
        await show_game_settings(update, context)
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Не удалось получить file_id. Попробуйте ещё раз.")
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
        await query.edit_message_text("⛔️ Сессия истекла. Авторизуйтесь заново.")
        return

    update_admin_activity(user_id)

    if data.startswith("reply_"):
        msg_id = int(data.split("_")[1])
        context.user_data["reply_to"] = msg_id
        context.user_data["in_conversation"] = True
        await query.edit_message_text("✏️ Введите текст ответа (в личку боту):")
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

# ---------- Пересылка из каналов ----------
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
            logger.info(f"Переслано из {chat_id} в {target_id}")
        except Exception as e:
            logger.error(f"Ошибка пересылки в {target_id}: {e}")

# ---------- Автоудаление неизвестных сообщений ----------
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
                "📩 Проверить поддержку", "⚙️ Настройки", "🎮 Настройки игры", "🚪 Выйти",
                "🃏 Карточки", "➕ Создать коллекцию", "🆕 Добавить карточку",
                "🎁 Выдать карточку игроку", "🔙 Назад в админ-панель"]:
        return

    try:
        user_msg = update.message
        error_msg = await update.message.reply_text("❌ Ошибка! Не выбран модуль запроса. Попробуйте снова.")
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

    conv_new_collection = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Создать коллекцию$") & filters.ChatType.PRIVATE, cards_admin_buttons)],
        states={
            WAITING_NEW_COLLECTION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_collection_name)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
        allow_reentry=True,
    )
    app.add_handler(conv_new_collection)

    conv_add_card = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🆕 Добавить карточку$") & filters.ChatType.PRIVATE, cards_admin_buttons)],
        states={
            WAITING_CARD_RARITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_rarity_input)],
            WAITING_CARD_COLLECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_collection_input)],
            WAITING_CARD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_name_input)],
            WAITING_CARD_IMAGE: [MessageHandler(filters.PHOTO | filters.ANIMATION | filters.Document.ALL, card_image_input)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
        allow_reentry=True,
    )
    app.add_handler(conv_add_card)

    conv_give_card = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🎁 Выдать карточку игроку$") & filters.ChatType.PRIVATE, cards_admin_buttons)],
        states={
            WAITING_CARD_ID_TO_GIVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_give_id_input)],
            WAITING_USER_ID_TO_GIVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_give_user_input)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
        allow_reentry=True,
    )
    app.add_handler(conv_give_card)

    app.add_handler(MessageHandler(filters.Regex("^📩 Проверить поддержку$") & filters.ChatType.PRIVATE, admin_buttons))
    app.add_handler(MessageHandler(filters.Regex("^⚙️ Настройки$") & filters.ChatType.PRIVATE, admin_buttons))
    app.add_handler(MessageHandler(filters.Regex("^🎮 Настройки игры$") & filters.ChatType.PRIVATE, admin_buttons))
    app.add_handler(MessageHandler(filters.Regex("^🃏 Карточки$") & filters.ChatType.PRIVATE, admin_buttons))
    app.add_handler(MessageHandler(filters.Regex("^🚪 Выйти$") & filters.ChatType.PRIVATE, admin_buttons))
    app.add_handler(MessageHandler(filters.Regex("^🏠 Главное меню$") & filters.ChatType.PRIVATE, main_menu))
    app.add_handler(MessageHandler(filters.Regex("^🔙 Назад в админ-панель$") & filters.ChatType.PRIVATE, cards_admin_buttons))

    app.add_handler(CallbackQueryHandler(inline_callback, pattern="^(discord|website|free_card)$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(close_|next_support|back_to_admin|view_goal_gif|view_save_gif)$"))
    app.add_handler(CallbackQueryHandler(inventory_callback, pattern="^craft_"))

    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, forward_from_channels))
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, handle_unknown_message), group=999)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rating", rating))
    app.add_handler(CommandHandler("getid", getid))
    app.add_handler(CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено.")))
    app.add_handler(CommandHandler("freegoyda", free_card_command))
    app.add_handler(CommandHandler("inventory", inventory_command))

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
