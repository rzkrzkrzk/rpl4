import os
import logging
import asyncio
import random
import time
from datetime import datetime, timedelta
import psycopg2
from psycopg2.extras import RealDictCursor

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан!")

ADMIN_SESSION_MINUTES = 30

# Цены продажи карточек в зависимости от редкости
SELL_PRICES = {
    "Редкая": 500,
    "Очень редкая": 1000,
    "Эпическая": 2500,
    "Мифическая": 5000,
    "Легендарная": 12000,
    "Секретная": 25000
}

# Состояния ConversationHandler
(
    WAITING_LOGIN,
    WAITING_PASSWORD,
    WAITING_CHANNEL_USERNAME,
    WAITING_CHAT_LINK,
    WAITING_REPLY_TEXT,
    WAITING_SUPPORT_MSG,
    WAITING_DUEL_SHOT,
    WAITING_GIF_GOAL,
    WAITING_GIF_SAVE,
    # Карточки / Команды / Паки
    CARD_ADMIN_MENU,
    ADD_COLLECTION_NAME,
    ADD_TEAM_NAME,
    ADD_TEAM_EMOJI,
    ADD_TEAM_PHOTO,
    DEL_TEAM_SELECT,
    ADD_CARD_RARITY,
    ADD_CARD_COLLECTION,
    ADD_CARD_COUNTRY,
    ADD_CARD_POSITION,
    ADD_CARD_TEAM,
    ADD_CARD_NICK,
    ADD_CARD_OVR,
    ADD_CARD_PHOTO,
    DEL_CARD_ID,
    ADD_PACK_NAME,
    ADD_PACK_PRICE,
    ADD_PACK_LIMIT,
    ADD_PACK_CARDS,
    ADD_PACK_PHOTO,
    GRANT_CARD_DATA,
    GIVE_MONEY_DATA,
    WAITING_VIEW_USER_INV,
    # Промокоды (Админка)
    ADD_PROMO_CODE,
    ADD_PROMO_TYPE,
    ADD_PROMO_REWARD,
    ADD_PROMO_LIMIT,
) = range(36)

# ---------- БД (PostgreSQL) ----------
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # Пользователи
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance INTEGER DEFAULT 5000,
            mmr INTEGER DEFAULT 1000,
            last_card_claim TIMESTAMP
        )
    ''')
    
    # Существующие системы
    c.execute('''
        CREATE TABLE IF NOT EXISTS source_channels (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT UNIQUE,
            username TEXT,
            added_by BIGINT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS target_chats (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT UNIQUE,
            link TEXT,
            added_by BIGINT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS support_messages (
            id SERIAL PRIMARY KEY,
            user_id BIGINT,
            username TEXT,
            text TEXT,
            timestamp TEXT,
            answered INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY,
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
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            attempts INTEGER DEFAULT 0,
            goals INTEGER DEFAULT 0
        )
    ''')
    
    # Система Карточек и Команд
    c.execute('''
        CREATE TABLE IF NOT EXISTS collections (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS card_teams (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            emoji TEXT DEFAULT '🏒',
            photo_id TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            id SERIAL PRIMARY KEY,
            collection_id INTEGER REFERENCES collections(id) ON DELETE CASCADE,
            team_id INTEGER REFERENCES card_teams(id) ON DELETE SET NULL,
            nickname TEXT NOT NULL,
            position TEXT NOT NULL,
            ovr INTEGER NOT NULL,
            country TEXT NOT NULL,
            rarity TEXT NOT NULL,
            image_id TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_cards (
            user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
            card_id INTEGER REFERENCES cards(id) ON DELETE CASCADE,
            count INTEGER DEFAULT 1,
            PRIMARY KEY(user_id, card_id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_rosters (
            user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
            goalie_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
            skater1_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
            skater2_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
            skater3_id INTEGER REFERENCES cards(id) ON DELETE SET NULL,
            skater4_id INTEGER REFERENCES cards(id) ON DELETE SET NULL
        )
    ''')
    
    # Паки и Магазин
    c.execute('''
        CREATE TABLE IF NOT EXISTS packs (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            price INTEGER NOT NULL,
            buy_limit INTEGER DEFAULT 0,
            photo_id TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS pack_cards (
            pack_id INTEGER REFERENCES packs(id) ON DELETE CASCADE,
            card_id INTEGER REFERENCES cards(id) ON DELETE CASCADE,
            PRIMARY KEY(pack_id, card_id)
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_pack_buys (
            user_id BIGINT,
            pack_id INTEGER REFERENCES packs(id) ON DELETE CASCADE,
            buy_count INTEGER DEFAULT 0,
            PRIMARY KEY(user_id, pack_id)
        )
    ''')

    # Трейды
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id SERIAL PRIMARY KEY,
            sender_id BIGINT NOT NULL,
            receiver_id BIGINT NOT NULL,
            card_id INTEGER REFERENCES cards(id) ON DELETE CASCADE,
            money INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending'
        )
    ''')

    # Торговая площадка (/cardshop)
    c.execute('''
        CREATE TABLE IF NOT EXISTS card_shop_listings (
            id SERIAL PRIMARY KEY,
            seller_id BIGINT NOT NULL,
            card_id INTEGER REFERENCES cards(id) ON DELETE CASCADE,
            price INTEGER NOT NULL
        )
    ''')

    # Промокоды
    c.execute('''
        CREATE TABLE IF NOT EXISTS promos (
            code TEXT PRIMARY KEY,
            reward_type TEXT NOT NULL, -- 'money' или 'card'
            reward_value INTEGER NOT NULL, -- сумма денег или ID карточки
            max_uses INTEGER NOT NULL, -- сколько всего раз можно активировать
            used_count INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS promo_activations (
            user_id BIGINT,
            code TEXT REFERENCES promos(code) ON DELETE CASCADE,
            PRIMARY KEY(user_id, code)
        )
    ''')
    
    c.execute("INSERT INTO bot_config (key, value) VALUES ('gif_goal', '') ON CONFLICT DO NOTHING")
    c.execute("INSERT INTO bot_config (key, value) VALUES ('gif_save', '') ON CONFLICT DO NOTHING")
    conn.commit()
    conn.close()

init_db()

# ---------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ----------
def get_or_create_user(user_id, username="", first_name=""):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute(
            "INSERT INTO users (user_id, username, first_name, balance, mmr) VALUES (%s, %s, %s, 5000, 1000) RETURNING *",
            (user_id, username, first_name)
        )
        row = c.fetchone()
    else:
        c.execute("UPDATE users SET username = %s, first_name = %s WHERE user_id = %s", (username, first_name, user_id))
    conn.commit()
    conn.close()
    return row

def check_user_exists(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    return bool(row)

async def check_pm_registered(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    user = update.effective_user
    if not user:
        return False
        
    if check_user_exists(user.id):
        return True

    bot_username = context.bot.username
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Написать боту в ЛС", url=f"https://t.me/{bot_username}?start=start")]
    ])
    msg_text = "⚠️ **Чтобы взаимодействовать с ботом, сначала напишите ему в личные сообщения!**"
    
    if update.callback_query:
        await update.callback_query.answer("⚠️ Сначала напишите боту в ЛС!", show_alert=True)
    elif update.message:
        await update.message.reply_text(msg_text, reply_markup=kb, parse_mode="Markdown")
        
    return False

def choose_card_for_user(cursor, user_id, candidate_cards):
    """
    Выбирает карточку для пользователя из пула candidate_cards.
    Если у пользователя есть не все карточки из пула, выдается та, которой ЕЩЕ НЕТ.
    Если у пользователя уже ЕСТЬ ВСЕ карточки из пула, выдается случайная повторка.
    """
    if not candidate_cards:
        return None

    card_ids = tuple(c['id'] for c in candidate_cards)
    
    if len(card_ids) == 1:
        cursor.execute("SELECT card_id FROM user_cards WHERE user_id = %s AND card_id = %s AND count > 0", (user_id, card_ids[0]))
    else:
        cursor.execute("SELECT card_id FROM user_cards WHERE user_id = %s AND card_id IN %s AND count > 0", (user_id, card_ids))
        
    owned_rows = cursor.fetchall()
    owned_ids = set(r['card_id'] for r in owned_rows)

    unowned_cards = [c for c in candidate_cards if c['id'] not in owned_ids]

    if unowned_cards:
        return random.choice(unowned_cards)
    else:
        return random.choice(candidate_cards)

def get_config(key):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT value FROM bot_config WHERE key = %s', (key,))
    row = c.fetchone()
    conn.close()
    return row['value'] if row else ''

def set_config(key, value):
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO bot_config (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value', (key, value))
    conn.commit()
    conn.close()

def is_admin(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT last_activity FROM admins WHERE user_id = %s', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        last_activity = row['last_activity']
        if last_activity and (datetime.now().timestamp() - last_activity) < ADMIN_SESSION_MINUTES * 60:
            return True
        else:
            conn = get_db()
            c = conn.cursor()
            c.execute('DELETE FROM admins WHERE user_id = %s', (user_id,))
            conn.commit()
            conn.close()
            return False
    return False

def add_admin(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO admins (user_id, last_activity) VALUES (%s, %s) ON CONFLICT (user_id) DO UPDATE SET last_activity = EXCLUDED.last_activity',
              (user_id, int(datetime.now().timestamp())))
    conn.commit()
    conn.close()

def update_admin_activity(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE admins SET last_activity = %s WHERE user_id = %s',
              (int(datetime.now().timestamp()), user_id))
    conn.commit()
    conn.close()

def remove_admin(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM admins WHERE user_id = %s', (user_id,))
    conn.commit()
    conn.close()

def check_credentials(login, password):
    credentials = {
        "goyda1488": "goydarpl",
        "rzk1488": "rzksigma",
    }
    return credentials.get(login) == password

def add_source_channel(chat_id, username, added_by):
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO source_channels (chat_id, username, added_by) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
              (chat_id, username, added_by))
    conn.commit()
    conn.close()

def get_source_channels():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT chat_id, username FROM source_channels')
    rows = c.fetchall()
    conn.close()
    return rows

def add_target_chat(chat_id, link, added_by):
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO target_chats (chat_id, link, added_by) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING',
              (chat_id, link, added_by))
    conn.commit()
    conn.close()

def get_target_chats():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT chat_id, link FROM target_chats')
    rows = c.fetchall()
    conn.close()
    return rows

def add_support_message(user_id, username, text):
    conn = get_db()
    c = conn.cursor()
    c.execute('INSERT INTO support_messages (user_id, username, text, timestamp) VALUES (%s, %s, %s, %s) RETURNING id',
              (user_id, username, text, datetime.now().isoformat()))
    msg_id = c.fetchone()['id']
    conn.commit()
    conn.close()
    return msg_id

def get_unanswered_messages():
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, user_id, username, text, timestamp FROM support_messages WHERE answered = 0 ORDER BY id')
    rows = c.fetchall()
    conn.close()
    return rows

def mark_answered(msg_id):
    conn = get_db()
    c = conn.cursor()
    c.execute('UPDATE support_messages SET answered = 1 WHERE id = %s', (msg_id,))
    conn.commit()
    conn.close()

# ---------- КЛАВИАТУРЫ ----------
def main_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["🏠 Главное меню", "🎒 Инвентарь"],
        ["🏒 Состав и Профиль", "⚔️ Искать игру"],
        ["🛒 Магазин Паков", "🎴 Бесплатная карта"],
        ["🔄 Трейд", "🛍 Торговая площадка"],
        ["🎟 Промокод", "🏆 Топ MMR"]
    ], resize_keyboard=True)

def admin_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Добавить каналы", "➕ Добавить чаты"],
        ["📩 Проверить поддержку", "⚙️ Настройки"],
        ["🎮 Настройки игры", "🃏 Карточки"],
        ["🎟 Создать промокод", "🔍 Инвентарь игрока"],
        ["👥 Список игроков", "🚪 Выйти"]
    ], resize_keyboard=True)

def card_admin_keyboard():
    return ReplyKeyboardMarkup([
        ["📁 Создать коллекцию", "🛡 Создать команду"],
        ["❌ Удалить команду", "🃏 Добавить карточку"],
        ["❌ Удалить карточку", "📦 Добавить пак"],
        ["🎁 Выдать карточку игроку", "💰 Выдать деньги"],
        ["⬅️ Выйти из настройки карточек"]
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

# Страны
COUNTRIES = [
    "Russian Federation", "USA", "Canada", "Finland", "Sweden", "Czech Republic",
    "Slovakia", "Germany", "Switzerland", "Latvia", "Belarus", "Kazakhstan",
    "UK", "France", "Austria", "Norway", "Denmark", "Japan", "China"
]

# ---------- КОМАНДА /getid ----------
async def getid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.message.reply_text(
        f"🆔 **ID этого чата:** `{chat.id}`\n📌 **Тип чата:** `{chat.type}`",
        parse_mode="Markdown"
    )

# ---------- ЛОГИКА КАРТОЧЕК И ВЫДАЧИ (/rplcards) ----------
async def rplcards_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    user = update.effective_user
    u_data = get_or_create_user(user.id, user.username, user.first_name)
    now = datetime.now()
    
    last_claim = u_data.get('last_card_claim')
    if last_claim:
        if isinstance(last_claim, str):
            last_claim = datetime.fromisoformat(last_claim)
        if now < last_claim + timedelta(hours=8):
            wait = (last_claim + timedelta(hours=8)) - now
            hours, rem = divmod(wait.seconds, 3600)
            minutes = rem // 60
            await update.message.reply_text(f"⏳ Бесплатную карточку можно получать раз в 8 часов!\nПодожди ещё: **{hours} ч {minutes} мин**", parse_mode="Markdown")
            return

    # Анимация ожидания получения карточки (3 секунды)
    wait_msg = await update.message.reply_text("📦 **Идет открытие и поиск карточки...** ⏳", parse_mode="Markdown")
    await asyncio.sleep(3)

    # Допустимые редкости
    rarity = random.choices(
        ["Редкая", "Очень редкая", "Эпическая", "Мифическая", "Легендарная"],
        weights=[50, 28, 14, 6, 2], k=1
    )[0]

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT c.*, col.name as collection_name, t.name as team_name, t.emoji as team_emoji
        FROM cards c
        JOIN collections col ON c.collection_id = col.id
        LEFT JOIN card_teams t ON c.team_id = t.id
        WHERE c.rarity = %s
    ''', (rarity,))
    cards = c.fetchall()

    if not cards:
        c.execute('''
            SELECT c.*, col.name as collection_name, t.name as team_name, t.emoji as team_emoji
            FROM cards c
            JOIN collections col ON c.collection_id = col.id
            LEFT JOIN card_teams t ON c.team_id = t.id
            WHERE c.rarity != 'Секретная'
        ''')
        cards = c.fetchall()

    if not cards:
        conn.close()
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_text("📭 В базе пока нет карточек! Администратор скоро их добавит.")
        return

    card = choose_card_for_user(c, user.id, cards)
    card_id = card['id']

    c.execute('''
        INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1)
        ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1
    ''', (user.id, card_id))
    c.execute("UPDATE users SET last_card_claim = %s WHERE user_id = %s", (now, user.id))
    conn.commit()
    conn.close()

    try:
        await wait_msg.delete()
    except Exception:
        pass

    team_str = f"{card['team_emoji'] or '🏒'} {card['team_name']}" if card['team_name'] else "Без команды"
    
    caption = (
        f"🔥 **Новая карточка!**\n\n"
        f"┏━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃ 👤 {card['nickname']}\n"
        f"┃ 🏒 {card['position']}\n"
        f"┃ ⭐ {card['ovr']} OVR\n"
        f"┃ {team_str}\n"
        f"┃ 🌍 {card['country']}\n"
        f"┃ ✨ {card['rarity']}\n"
        f"┗━━━━━━━━━━━━━━━━━━━━┛"
    )

    if card['image_id']:
        try:
            await update.message.reply_photo(photo=card['image_id'], caption=caption, parse_mode="Markdown")
            return
        except Exception:
            pass
    await update.message.reply_text(caption, parse_mode="Markdown")

# ---------- ИНВЕНТАРЬ И КРАФТ (/inventory) ----------
async def inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    await show_inventory(update, context)

async def show_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE, edit=False):
    query = update.callback_query
    user = query.from_user if query else update.effective_user

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT uc.count, c.*, col.name as col_name, t.name as team_name, t.emoji as team_emoji
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        JOIN collections col ON c.collection_id = col.id
        LEFT JOIN card_teams t ON c.team_id = t.id
        WHERE uc.user_id = %s AND uc.count > 0
        ORDER BY col.name, c.ovr DESC
    ''', (user.id,))
    user_cards = c.fetchall()
    conn.close()

    text = "🎒 **Ваш Инвентарь Карточек:**\n\n"
    buttons = []

    if not user_cards:
        text += "У вас пока нет карточек! Получите первую с помощью команды /rplcards или купите пак в /shop."
    else:
        mythic_counts = {}
        for uc in user_cards:
            t_str = f"{uc['team_emoji']} {uc['team_name']}" if uc['team_name'] else ""
            text += f"ID `{uc['id']}` | **{uc['nickname']}** ({uc['position']}, {uc['ovr']} OVR) — `x{uc['count']}` [{uc['rarity']}] {t_str}\n"
            
            if uc['rarity'] == 'Мифическая':
                col_id = uc['collection_id']
                mythic_counts[col_id] = mythic_counts.get(col_id, 0) + uc['count']

        for col_id, m_count in mythic_counts.items():
            if m_count >= 5:
                conn = get_db()
                c = conn.cursor()
                c.execute("SELECT name FROM collections WHERE id = %s", (col_id,))
                col_row = c.fetchone()
                conn.close()
                col_name = col_row['name'] if col_row else "Коллекция"
                buttons.append([InlineKeyboardButton(f"🔨 Скрафтить Легендарную ({col_name})", callback_data=f"craft_leg_{col_id}")])

        buttons.append([InlineKeyboardButton("💰 Продать карточки", callback_data="sell_menu")])

    buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="refresh_inv")])
    markup = InlineKeyboardMarkup(buttons)

    if query:
        await query.answer()
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            await query.message.delete()
            await context.bot.send_message(user.id, text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def show_sell_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT uc.count, c.*
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        WHERE uc.user_id = %s AND uc.count > 0
        ORDER BY c.ovr DESC
    ''', (user.id,))
    user_cards = c.fetchall()
    conn.close()

    if not user_cards:
        await query.answer("У вас нет карточек для продажи!", show_alert=True)
        return

    text = "💰 **Продажа карточек из инвентаря:**\nНажмите на карточку, чтобы продать 1 шт.\n\n"
    buttons = []

    for uc in user_cards:
        price = SELL_PRICES.get(uc['rarity'], 300)
        btn_text = f"Продать {uc['nickname']} ({uc['ovr']} OVR) — {price} RPLCoin"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"do_sell_{uc['id']}")])

    buttons.append([InlineKeyboardButton("🔙 Назад в инвентарь", callback_data="refresh_inv")])

    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def inventory_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    query = update.callback_query
    user = query.from_user
    data = query.data

    if data == "refresh_inv":
        await show_inventory(update, context, edit=True)

    elif data == "sell_menu":
        await show_sell_menu(update, context)

    elif data.startswith("do_sell_"):
        card_id = int(data.split("_")[2])
        conn = get_db()
        c = conn.cursor()

        c.execute('''
            SELECT uc.count, c.rarity, c.nickname 
            FROM user_cards uc 
            JOIN cards c ON uc.card_id = c.id 
            WHERE uc.user_id = %s AND uc.card_id = %s AND uc.count > 0
        ''', (user.id, card_id))
        row = c.fetchone()

        if not row:
            conn.close()
            await query.answer("❌ У вас больше нет этой карточки!", show_alert=True)
            await show_sell_menu(update, context)
            return

        price = SELL_PRICES.get(row['rarity'], 300)

        c.execute("UPDATE user_cards SET count = count - 1 WHERE user_id = %s AND card_id = %s", (user.id, card_id))
        c.execute("DELETE FROM user_cards WHERE user_id = %s AND card_id = %s AND count <= 0", (user.id, card_id))
        c.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (price, user.id))

        c.execute("SELECT count FROM user_cards WHERE user_id = %s AND card_id = %s", (user.id, card_id))
        rem = c.fetchone()
        if not rem or rem['count'] <= 0:
            c.execute('''
                UPDATE user_rosters SET
                    goalie_id = CASE WHEN goalie_id = %s THEN NULL ELSE goalie_id END,
                    skater1_id = CASE WHEN skater1_id = %s THEN NULL ELSE skater1_id END,
                    skater2_id = CASE WHEN skater2_id = %s THEN NULL ELSE skater2_id END,
                    skater3_id = CASE WHEN skater3_id = %s THEN NULL ELSE skater3_id END,
                    skater4_id = CASE WHEN skater4_id = %s THEN NULL ELSE skater4_id END
                WHERE user_id = %s
            ''', (card_id, card_id, card_id, card_id, card_id, user.id))

        conn.commit()
        conn.close()

        await query.answer(f"✅ Карточка {row['nickname']} продана за {price} RPLCoin!", show_alert=True)
        await show_sell_menu(update, context)

    elif data.startswith("craft_leg_"):
        col_id = int(data.split("_")[2])
        conn = get_db()
        c = conn.cursor()
        
        c.execute('''
            SELECT uc.card_id, uc.count 
            FROM user_cards uc
            JOIN cards c ON uc.card_id = c.id
            WHERE uc.user_id = %s AND c.collection_id = %s AND c.rarity = 'Мифическая' AND uc.count > 0
        ''', (user.id, col_id))
        m_cards = c.fetchall()

        total_mythic = sum(m['count'] for m in m_cards)
        if total_mythic < 5:
            conn.close()
            await query.answer("❌ Нужно ровно 5 мифических карточек этой коллекции!", show_alert=True)
            return

        c.execute("SELECT * FROM cards WHERE collection_id = %s AND rarity = 'Легендарная' LIMIT 1", (col_id,))
        leg_card = c.fetchone()

        if not leg_card:
            conn.close()
            await query.answer("❌ В этой коллекции ещё нет Легендарной карточки!", show_alert=True)
            return

        needed = 5
        for m in m_cards:
            take = min(m['count'], needed)
            c.execute("UPDATE user_cards SET count = count - %s WHERE user_id = %s AND card_id = %s", (take, user.id, m['card_id']))
            needed -= take
            if needed <= 0:
                break

        c.execute("DELETE FROM user_cards WHERE count <= 0")

        c.execute('''
            INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1)
            ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1
        ''', (user.id, leg_card['id']))

        conn.commit()
        conn.close()

        await query.answer("🎉 Вы успешно скрафтили Легендарную карточку!", show_alert=True)
        await show_inventory(update, context, edit=True)

# ---------- ПРОФИЛЬ И СОСТАВ (/profile) ----------
async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    await show_profile(update, context)

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user if query else update.effective_user
    u_data = get_or_create_user(user.id, user.username, user.first_name)

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM user_rosters WHERE user_id = %s", (user.id,))
    roster = c.fetchone()

    roster_info = {}
    positions = ["goalie", "skater1", "skater2", "skater3", "skater4"]
    total_ovr = 0
    count_filled = 0

    if roster:
        for pos in positions:
            card_id = roster[f"{pos}_id"]
            if card_id:
                c.execute("SELECT nickname, ovr, position, rarity FROM cards WHERE id = %s", (card_id,))
                cd = c.fetchone()
                if cd:
                    roster_info[pos] = f"**{cd['nickname']}** ({cd['ovr']} OVR)"
                    total_ovr += cd['ovr']
                    count_filled += 1
                else:
                    roster_info[pos] = "❌ Не выбран"
            else:
                roster_info[pos] = "❌ Не выбран"
    else:
        for pos in positions:
            roster_info[pos] = "❌ Не выбран"

    conn.close()

    avg_ovr = round(total_ovr / 5, 1) if count_filled == 5 else 0

    text = (
        f"🏒 **Профиль игрока {user.first_name}:**\n\n"
        f"💳 Баланс: **{u_data['balance']} RPLCoin**\n"
        f"🏆 Рейтинг MMR: **{u_data['mmr']}**\n"
        f"⭐ Средний OVR Состава: **{avg_ovr if avg_ovr > 0 else 'Состав не собран'}**\n\n"
        f"📋 **Текущий Состав (1 Goalie + 4 Skaters):**\n"
        f"🧤 Вратарь (Goalie): {roster_info.get('goalie')}\n"
        f"🏒 Полевой 1 (Skater): {roster_info.get('skater1')}\n"
        f"🏒 Полевой 2 (Skater): {roster_info.get('skater2')}\n"
        f"🏒 Полевой 3 (Skater): {roster_info.get('skater3')}\n"
        f"🏒 Полевой 4 (Skater): {roster_info.get('skater4')}\n"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Изменить Состав", callback_data="edit_roster_menu")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_profile")]
    ])

    if query:
        await query.answer()
        try:
            await query.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        except Exception:
            await query.message.delete()
            await context.bot.send_message(user.id, text, reply_markup=kb, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

async def profile_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    query = update.callback_query
    user = query.from_user
    data = query.data

    if data == "refresh_profile":
        await show_profile(update, context)

    elif data == "edit_roster_menu":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🧤 Выбрать Вратаря (Goalie)", callback_data="set_pos_goalie")],
            [InlineKeyboardButton("🏒 Выбрать Полевого 1", callback_data="set_pos_skater1")],
            [InlineKeyboardButton("🏒 Выбрать Полевого 2", callback_data="set_pos_skater2")],
            [InlineKeyboardButton("🏒 Выбрать Полевого 3", callback_data="set_pos_skater3")],
            [InlineKeyboardButton("🏒 Выбрать Полевого 4", callback_data="set_pos_skater4")],
            [InlineKeyboardButton("🔙 Назад в профиль", callback_data="refresh_profile")]
        ])
        await query.edit_message_text("⚙️ **Выберите позицию для изменения:**", reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("set_pos_"):
        pos_type = data.replace("set_pos_", "")
        conn = get_db()
        c = conn.cursor()
        
        needed_position = "Goalie" if pos_type == "goalie" else "Skater"
        
        c.execute('''
            SELECT c.id, c.nickname, c.ovr, c.rarity, t.emoji, t.name as team_name
            FROM user_cards uc
            JOIN cards c ON uc.card_id = c.id
            LEFT JOIN card_teams t ON c.team_id = t.id
            WHERE uc.user_id = %s AND c.position = %s AND uc.count > 0
            ORDER BY c.ovr DESC
        ''', (user.id, needed_position))
        available = c.fetchall()
        conn.close()

        if not available:
            await query.answer(f"❌ У вас нет доступных карточек на позицию {needed_position}!", show_alert=True)
            return

        buttons = []
        for card in available:
            t_str = f"({card['emoji'] or '🏒'} {card['team_name']})" if card['team_name'] else ""
            buttons.append([InlineKeyboardButton(f"{card['nickname']} - {card['ovr']} OVR {t_str}", callback_data=f"apply_card_{pos_type}_{card['id']}")])
        
        buttons.append([InlineKeyboardButton("🔙 Назад", callback_data="edit_roster_menu")])
        await query.edit_message_text(f"📋 **Выберите карточку для {pos_type.capitalize()}:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

    elif data.startswith("apply_card_"):
        parts = data.split("_")
        pos_type = parts[2]
        card_id = int(parts[3])

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM user_rosters WHERE user_id = %s", (user.id,))
        roster = c.fetchone()

        if not roster:
            c.execute("INSERT INTO user_rosters (user_id) VALUES (%s)", (user.id,))
            c.execute("SELECT * FROM user_rosters WHERE user_id = %s", (user.id,))
            roster = c.fetchone()

        positions = ["goalie", "skater1", "skater2", "skater3", "skater4"]
        for p in positions:
            if p != pos_type and roster[f"{p}_id"] == card_id:
                conn.close()
                await query.answer("❌ Эта карточка уже используется на другой позиции в составе!", show_alert=True)
                return

        c.execute(f"UPDATE user_rosters SET {pos_type}_id = %s WHERE user_id = %s", (card_id, user.id))
        conn.commit()
        conn.close()

        await query.answer("✅ Карточка успешно установлена!")
        await show_profile(update, context)

# ---------- СИСТЕМА ТРЕЙДОВ (/trade) ----------
async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "🤝 **Система Трейдов и Обменов:**\n\n"
            "Использование:\n"
            "• `/trade @username ID_карточки [сумма_денег]` — предложить карточку/деньги другу.\n"
            "• `/trade check` — посмотреть входящие/исходящие предложения обмена.",
            parse_mode="Markdown"
        )
        return

    if args[0].lower() == "check":
        await show_trades_list(update, context)
        return

    if len(args) < 2:
        await update.message.reply_text("❌ Неверный формат! Пример: `/trade @friend 5 1000`", parse_mode="Markdown")
        return

    target_input = args[0].replace("@", "")
    card_id = int(args[1])
    money = int(args[2]) if len(args) > 2 and args[2].isdigit() else 0

    sender = update.effective_user
    if money < 0:
        await update.message.reply_text("❌ Сумма не может быть отрицательной!")
        return

    conn = get_db()
    c = conn.cursor()

    # Ищем получателя
    if target_input.isdigit():
        c.execute("SELECT * FROM users WHERE user_id = %s", (int(target_input),))
    else:
        c.execute("SELECT * FROM users WHERE username = %s", (target_input,))
    receiver = c.fetchone()

    if not receiver:
        conn.close()
        await update.message.reply_text("❌ Получатель не найден в боте!")
        return

    receiver_id = receiver['user_id']
    if receiver_id == sender.id:
        conn.close()
        await update.message.reply_text("❌ Нельзя отправить трейд самому себе!")
        return

    # Проверяем наличие карточки у отправителя
    c.execute("SELECT count FROM user_cards WHERE user_id = %s AND card_id = %s AND count > 0", (sender.id, card_id))
    uc_row = c.fetchone()
    if not uc_row:
        conn.close()
        await update.message.reply_text("❌ У вас нет этой карточки в инвентаре!")
        return

    # Проверяем баланс отправителя для денег
    c.execute("SELECT balance FROM users WHERE user_id = %s", (sender.id,))
    s_bal = c.fetchone()['balance']
    if s_bal < money:
        conn.close()
        await update.message.reply_text("❌ У вас недостаточно RPLCoin для этого обмена!")
        return

    # Создаем запись трейда
    c.execute('''
        INSERT INTO trades (sender_id, receiver_id, card_id, money, status)
        VALUES (%s, %s, %s, %s, 'pending')
        RETURNING id
    ''', (sender.id, receiver_id, card_id, money))
    trade_id = c.fetchone()['id']
    conn.commit()

    c.execute("SELECT nickname, ovr FROM cards WHERE id = %s", (card_id,))
    card_info = c.fetchone()
    conn.close()

    await update.message.reply_text(f"✅ Предложение обмена успешно отправлено игроку @{receiver.get('username') or receiver_id}!")

    # Уведомляем получателя в ЛС
    try:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Принять трейд", callback_data=f"trade_accept_{trade_id}")],
            [InlineKeyboardButton("❌ Отклонить", callback_data=f"trade_decline_{trade_id}")]
        ])
        await context.bot.send_message(
            chat_id=receiver_id,
            text=f"🤝 **Вам поступил новый трейд!**\n\n"
                 f"👤 От: {sender.first_name}\n"
                 f"🎴 Карточка: **{card_info['nickname']}** ({card_info['ovr']} OVR)\n"
                 f"💰 RPLCoin в довесок: `{money}`\n\n"
                 f"Используйте команду `/trade check` или кнопки ниже:",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    except Exception:
        pass

async def show_trades_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT t.*, c.nickname, c.ovr 
        FROM trades t
        JOIN cards c ON t.card_id = c.id
        WHERE t.receiver_id = %s AND t.status = 'pending'
    ''', (user.id,))
    trades = c.fetchall()
    conn.close()

    if not trades:
        await update.message.reply_text("📭 У вас нет активных входящих предложений обмена.")
        return

    for t in trades:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Принять", callback_data=f"trade_accept_{t['id']}")],
            [InlineKeyboardButton("❌ Отклонить", callback_data=f"trade_decline_{t['id']}")]
        ])
        text = (
            f"🤝 **Предложение обмена #{t['id']}**\n"
            f"🎴 Карточка: **{t['nickname']}** ({t['ovr']} OVR)\n"
            f"💰 RPLCoin: `{t['money']}`"
        )
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

async def trade_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    query = update.callback_query
    user = query.from_user
    data = query.data

    parts = data.split("_")
    action = parts[1]
    trade_id = int(parts[2])

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE id = %s AND status = 'pending'", (trade_id,))
    trade = c.fetchone()

    if not trade:
        conn.close()
        await query.answer("❌ Этот трейд уже неактивен или отменен!", show_alert=True)
        try:
            await query.message.edit_text("❌ Трейд неактивен.")
        except Exception:
            pass
        return

    if trade['receiver_id'] != user.id:
        conn.close()
        await query.answer("❌ Это предложение адресовано не вам!", show_alert=True)
        return

    if action == "decline":
        c.execute("UPDATE trades SET status = 'declined' WHERE id = %s", (trade_id,))
        conn.commit()
        conn.close()
        await query.answer("❌ Вы отклонили трейд.")
        await query.message.edit_text("❌ Предложение обмена отклонено.")
        return

    if action == "accept":
        sender_id = trade['sender_id']
        card_id = trade['card_id']
        money = trade['money']

        # Проверяем наличие карточки у отправителя на момент принятия
        c.execute("SELECT count FROM user_cards WHERE user_id = %s AND card_id = %s AND count > 0", (sender_id, card_id))
        s_card = c.fetchone()
        if not s_card:
            conn.close()
            await query.answer("❌ У отправителя больше нет этой карточки!", show_alert=True)
            await query.message.edit_text("❌ Трейд отменен: карточка отсутствует у отправителя.")
            c.execute("UPDATE trades SET status = 'cancelled' WHERE id = %s", (trade_id,))
            conn.commit()
            return

        # Проверяем баланс отправителя для денег
        c.execute("SELECT balance FROM users WHERE user_id = %s", (sender_id,))
        s_user = c.fetchone()
        if not s_user or s_user['balance'] < money:
            conn.close()
            await query.answer("❌ У отправителя недостаточно средств!", show_alert=True)
            await query.message.edit_text("❌ Трейд отменен: недостаточно средств у отправителя.")
            c.execute("UPDATE trades SET status = 'cancelled' WHERE id = %s", (trade_id,))
            conn.commit()
            return

        # Проверяем баланс получателя (если вдруг требуется списание, но тут получатель получает карточку и деньги от отправителя)
        # Проводка транзакции обмена:
        # 1. Забираем карточку у отправителя
        c.execute("UPDATE user_cards SET count = count - 1 WHERE user_id = %s AND card_id = %s", (sender_id, card_id))
        c.execute("DELETE FROM user_cards WHERE user_id = %s AND card_id = %s AND count <= 0", (sender_id, card_id))

        # 2. Выдаем карточку получателю
        c.execute('''
            INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1)
            ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1
        ''', (user.id, card_id))

        # 3. Переводим деньги от отправителя получателю (если указаны)
        if money > 0:
            c.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (money, sender_id))
            c.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (money, user.id))

        c.execute("UPDATE trades SET status = 'accepted' WHERE id = %s", (trade_id,))
        conn.commit()
        conn.close()

        await query.answer("🎉 Трейд успешно принят!")
        await query.message.edit_text("✅ Вы успешно приняли обмен!")

        try:
            await context.bot.send_message(
                chat_id=sender_id,
                text=f"🎉 Игрок приняль ваш трейд (#{trade_id})! Обмен завершен успешно.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

# ---------- ТОРГОВАЯ ПЛОЩАДКА /cardshop ----------
async def cardshop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    args = context.args
    if args and args[0].lower() == "sell":
        if len(args) < 3:
            await update.message.reply_text("❌ Использование: `/cardshop sell ID_карточки цена` (цена до 999999)", parse_mode="Markdown")
            return
        card_id = int(args[1])
        price = int(args[2])
        if price <= 0 or price > 999999:
            await update.message.reply_text("❌ Цена должна быть от 1 до 999999 RPLCoin!")
            return

        user = update.effective_user
        conn = get_db()
        c = conn.cursor()

        # Проверяем наличие карточки
        c.execute("SELECT count FROM user_cards WHERE user_id = %s AND card_id = %s AND count > 0", (user.id, card_id))
        uc = c.fetchone()
        if not uc:
            conn.close()
            await update.message.reply_text("❌ У вас нет этой карточки в инвентаре!")
            return

        # Снимаем 1 шт с инвентаря игрока
        c.execute("UPDATE user_cards SET count = count - 1 WHERE user_id = %s AND card_id = %s", (user.id, card_id))
        c.execute("DELETE FROM user_cards WHERE user_id = %s AND card_id = %s AND count <= 0", (user.id, card_id))

        # Выставляем на торговую площадку
        c.execute("INSERT INTO card_shop_listings (seller_id, card_id, price) VALUES (%s, %s, %s)", (user.id, card_id, price))
        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ Карточка успешно выставлена на торговую площадку за `{price} RPLCoin`!", parse_mode="Markdown")
        return

    # Показываем список лотов
    await show_cardshop_listings(update, context)

async def show_cardshop_listings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user if query else update.effective_user

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT l.*, cd.nickname, cd.ovr, cd.rarity, cd.position
        FROM card_shop_listings l
        JOIN cards cd ON l.card_id = cd.id
        ORDER BY l.price ASC
        LIMIT 15
    ''')
    listings = c.fetchall()
    conn.close()

    text = "🛍 **Торговая Площадка Карточек:**\n\nИспользуйте `/cardshop sell ID цена` для выставления своих карточек.\n\n"
    buttons = []

    if not listings:
        text += "📭 На торговой площадке пока нет активных лотов."
    else:
        for l in listings:
            text += f"Лот #{l['id']} | **{l['nickname']}** ({l['position']}, {l['ovr']} OVR, [{l['rarity']}] — `🎴 {l['price']} RPLCoin`\n"
            buttons.append([InlineKeyboardButton(f"Купить #{l['id']} за {l['price']} RPLCoin", callback_data=f"buy_shop_{l['id']}")])

    buttons.append([InlineKeyboardButton("🔄 Обновить", callback_data="refresh_cardshop")])
    markup = InlineKeyboardMarkup(buttons)

    if query:
        await query.answer()
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            await query.message.delete()
            await context.bot.send_message(user.id, text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def cardshop_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    query = update.callback_query
    user = query.from_user
    data = query.data

    if data == "refresh_cardshop":
        await show_cardshop_listings(update, context)
        return

    if data.startswith("buy_shop_"):
        listing_id = int(data.split("_")[2])
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT * FROM card_shop_listings WHERE id = %s", (listing_id,))
        listing = c.fetchone()

        if not listing:
            conn.close()
            await query.answer("❌ Этот лот уже продан или снят с продажи!", show_alert=True)
            await show_cardshop_listings(update, context)
            return

        seller_id = listing['seller_id']
        card_id = listing['card_id']
        price = listing['price']

        if seller_id == user.id:
            conn.close()
            await query.answer("❌ Вы не можете купить собственный лот!", show_alert=True)
            return

        # Проверяем баланс покупателя
        c.execute("SELECT balance FROM users WHERE user_id = %s", (user.id,))
        u_bal = c.fetchone()['balance']
        if u_bal < price:
            conn.close()
            await query.answer("❌ У вас недостаточно RPLCoin для покупки этого лота!", show_alert=True)
            return

        # Проводим сделку
        c.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (price, user.id))
        c.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (price, seller_id))

        # Выдаем карточку покупателю
        c.execute('''
            INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1)
            ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1
        ''', (user.id, card_id))

        # Удаляем лот
        c.execute("DELETE FROM card_shop_listings WHERE id = %s", (listing_id,))
        conn.commit()
        conn.close()

        await query.answer("🎉 Карточка успешно куплена на торговой площадке!", show_alert=True)
        await show_cardshop_listings(update, context)

        try:
            await context.bot.send_message(
                chat_id=seller_id,
                text=f"🛍 Ваша карточка (ID лота: #{listing_id}) успешно продана за `{price} RPLCoin`!",
                parse_mode="Markdown"
            )
        except Exception:
            pass

# ---------- СИСТЕМА ПРОМОКОДОВ (/promo) ----------
async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    args = context.args
    if not args:
        await update.message.reply_text("🎟 Используйте: `/promo <код>` для активации промокода.", parse_mode="Markdown")
        return

    code = args[0].strip().upper()
    user = update.effective_user

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM promos WHERE code = %s", (code,))
    promo = c.fetchone()

    if not promo:
        conn.close()
        await update.message.reply_text("❌ Промокод не найден или не существует.")
        return

    if promo['used_count'] >= promo['max_uses']:
        conn.close()
        await update.message.reply_text("❌ Лимит активаций этого промокода исчерпан!")
        return

    # Проверяем, активировал ли уже этот пользователь
    c.execute("SELECT * FROM promo_activations WHERE user_id = %s AND code = %s", (user.id, code))
    if c.fetchone():
        conn.close()
        await update.message.reply_text("❌ Вы уже активировали этот промокод!")
        return

    # Активируем промокод
    c.execute("INSERT INTO promo_activations (user_id, code) VALUES (%s, %s)", (user.id, code))
    c.execute("UPDATE promos SET used_count = used_count + 1 WHERE code = %s", (code,))

    reward_type = promo['reward_type']
    reward_val = promo['reward_value']

    if reward_type == 'money':
        c.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (reward_val, user.id))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"🎉 Промокод `{code}` успешно активирован!\nВы получили: **{reward_val} RPLCoin** 💳", parse_mode="Markdown")

    elif reward_type == 'card':
        card_id = reward_val
        c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (user.id, card_id))
        c.execute("SELECT nickname, ovr FROM cards WHERE id = %s", (card_id,))
        cd = c.fetchone()
        conn.commit()
        conn.close()

        c_name = cd['nickname'] if cd else f"ID {card_id}"
        c_ovr = cd['ovr'] if cd else ""
        await update.message.reply_text(f"🎉 Промокод `{code}` успешно активирован!\nВы получили карточку: **{c_name}** ({c_ovr} OVR) 🎴", parse_mode="Markdown")

# ---------- МАТЧИ И ПОИСК СОПЕРНИКА (/cardmatch) ----------
active_searches = {}
active_games = set()

def calc_goal_probabilities(p1_cards, p2_cards):
    p1_skater_ovr = sum(p1_cards[f"skater{i}"]["ovr"] for i in range(1, 5)) / 4.0
    p2_skater_ovr = sum(p2_cards[f"skater{i}"]["ovr"] for i in range(1, 5)) / 4.0
    
    g1_ovr = p1_cards["goalie"]["ovr"]
    g2_ovr = p2_cards["goalie"]["ovr"]

    diff1 = p1_skater_ovr - g2_ovr
    diff2 = p2_skater_ovr - g1_ovr

    # Сильная экспоненциальная зависимость (1.8x фактор для реализации разницы OVR)
    prob_p1 = 0.10 * (1.8 ** (diff1 / 8.0))
    prob_p2 = 0.10 * (1.8 ** (diff2 / 8.0))

    prob_p1 = max(0.01, min(0.35, prob_p1))
    prob_p2 = max(0.01, min(0.35, prob_p2))

    return prob_p1, prob_p2

def calc_shootout_prob(skater_ovr, goalie_ovr):
    diff = skater_ovr - goalie_ovr
    prob = 0.30 * (1.6 ** (diff / 8.0))
    return max(0.05, min(0.80, prob))

async def cardmatch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    user = update.effective_user
    chat_id = update.effective_chat.id
    u_data = get_or_create_user(user.id, user.username, user.first_name)

    if user.id in active_searches or user.id in active_games:
        await update.message.reply_text("🔎 Вы уже находитесь в поиске или играете матч!")
        return

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM user_rosters WHERE user_id = %s", (user.id,))
    roster = c.fetchone()
    conn.close()

    if not roster or not (roster['goalie_id'] and roster['skater1_id'] and roster['skater2_id'] and roster['skater3_id'] and roster['skater4_id']):
        await update.message.reply_text("❌ Вы не можете играть! У вас полностью не собран состав (нужен 1 Вратарь + 4 Полевых).\nСоберите состав в /profile.")
        return

    if active_searches:
        other_user_id = next((uid for uid in active_searches.keys() if uid != user.id), None)
        if other_user_id:
            search_info = active_searches.pop(other_user_id)
            search_info["task"].cancel()

            p1_id = other_user_id
            p2_id = user.id
            p1_chat_id = search_info["chat_id"]
            p2_chat_id = chat_id
            p1_msg_id = search_info["msg_id"]

            try:
                await context.bot.edit_message_text(
                    chat_id=p1_chat_id,
                    message_id=p1_msg_id,
                    text=f"⚡️ **Соперник найден!** Игрок **{user.first_name}** присоединился. Начинаем матч...",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

            msg_p2 = await update.message.reply_text(
                f"⚡️ **Соперник найден!** Начинается матч против **{search_info.get('first_name', 'Игрока')}**...",
                parse_mode="Markdown"
            )
            p2_msg_id = msg_p2.message_id

            asyncio.create_task(start_game_pvp(p1_id, p2_id, p1_chat_id, p2_chat_id, p1_msg_id, p2_msg_id, context))
            return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚔️ Принять Поиск", callback_data=f"accept_match_{user.id}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"cancel_match_{user.id}")]
    ])

    msg = await update.message.reply_text(
        f"🏒 Игрок **{user.first_name}** (@{user.username or user.id}) ищет соперника для матча!\n"
        f"🏆 MMR: **{u_data['mmr']}**\n\n"
        f"Нажмите кнопку ниже или начните поиск `/cardmatch` в любом чате!",
        reply_markup=kb,
        parse_mode="Markdown"
    )

    active_searches[user.id] = {
        "chat_id": chat_id,
        "msg_id": msg.message_id,
        "username": user.username or "",
        "first_name": user.first_name or "Игрок",
        "start_time": time.time(),
        "task": asyncio.create_task(search_timeout_worker(user.id, context))
    }

async def search_timeout_worker(user_id, context):
    await asyncio.sleep(60)
    if user_id in active_searches:
        search_info = active_searches.pop(user_id)
        chat_id = search_info["chat_id"]
        msg_id = search_info["msg_id"]

        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text="🤖 Соперник-игрок не найден за 60 сек.! Начинается матч против ИИ Бота...",
                parse_mode="Markdown"
            )
        except Exception:
            pass

        await start_game_vs_ai(user_id, chat_id, msg_id, context)

async def match_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    query = update.callback_query
    user = query.from_user
    data = query.data

    if data.startswith("cancel_match_"):
        host_id = int(data.split("_")[2])
        if user.id != host_id:
            await query.answer("❌ Только создатель поиска может отменить его!", show_alert=True)
            return
        if host_id in active_searches:
            s_info = active_searches.pop(host_id)
            s_info["task"].cancel()
            await query.edit_message_text("❌ Поиск матча отменен.")
        return

    elif data.startswith("accept_match_"):
        host_id = int(data.split("_")[2])
        if user.id == host_id:
            await query.answer("❌ Вы не можете принять собственный вызов!", show_alert=True)
            return

        if user.id in active_searches or user.id in active_games:
            await query.answer("❌ Вы уже в поиске или играете матч!", show_alert=True)
            return

        if host_id not in active_searches:
            await query.answer("❌ Этот поиск уже неактивен!", show_alert=True)
            return

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM user_rosters WHERE user_id = %s", (user.id,))
        roster = c.fetchone()
        conn.close()

        if not roster or not (roster['goalie_id'] and roster['skater1_id'] and roster['skater2_id'] and roster['skater3_id'] and roster['skater4_id']):
            await query.answer("❌ У вас не собран полный состав (1 Вратарь + 4 Полевых)! Соберите состав в /profile.", show_alert=True)
            return

        s_info = active_searches.pop(host_id)
        s_info["task"].cancel()

        get_or_create_user(user.id, user.username, user.first_name)
        await query.edit_message_text(f"⚔️ Игрок **{user.first_name}** принял вызов! Начинаем матч...", parse_mode="Markdown")

        asyncio.create_task(start_game_pvp(host_id, user.id, s_info["chat_id"], query.message.chat_id, s_info["msg_id"], query.message.message_id, context))

async def broadcast_match_text(context, p1_chat_id, p1_msg_id, p2_chat_id, p2_msg_id, text):
    if p1_chat_id and p1_msg_id:
        try:
            await context.bot.edit_message_text(chat_id=p1_chat_id, message_id=p1_msg_id, text=text, parse_mode="Markdown")
        except Exception:
            pass
            
    if p2_chat_id and p2_msg_id and (p2_chat_id != p1_chat_id or p2_msg_id != p1_msg_id):
        try:
            await context.bot.edit_message_text(chat_id=p2_chat_id, message_id=p2_msg_id, text=text, parse_mode="Markdown")
        except Exception:
            pass

def format_cards_list(cards_dict):
    pos_labels = {
        "goalie": "🧤 Вратарь",
        "skater1": "🏒 Полевой 1",
        "skater2": "🏒 Полевой 2",
        "skater3": "🏒 Полевой 3",
        "skater4": "🏒 Полевой 4",
    }
    lines = []
    for k, v in cards_dict.items():
        label = pos_labels.get(k, "🏒")
        lines.append(f"  • {label}: **{v['nickname']}** ({v['ovr']} OVR)")
    return "\n".join(lines)

async def start_game_pvp(p1_id, p2_id, p1_chat_id, p2_chat_id, p1_msg_id, p2_msg_id, context):
    active_games.add(p1_id)
    active_games.add(p2_id)
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = %s", (p1_id,))
        u1 = c.fetchone()
        c.execute("SELECT * FROM users WHERE user_id = %s", (p2_id,))
        u2 = c.fetchone()

        c.execute("SELECT * FROM user_rosters WHERE user_id = %s", (p1_id,))
        r1 = c.fetchone()
        c.execute("SELECT * FROM user_rosters WHERE user_id = %s", (p2_id,))
        r2 = c.fetchone()

        p1_cards = get_roster_cards(c, r1)
        p2_cards = get_roster_cards(c, r2)
        conn.close()

        p1_ovr = sum(c['ovr'] for c in p1_cards.values()) / 5.0
        p2_ovr = sum(c['ovr'] for c in p2_cards.values()) / 5.0

        name1 = u1['first_name'] or u1['username'] or str(p1_id)
        name2 = u2['first_name'] or u2['username'] or str(p2_id)

        roster1_text = format_cards_list(p1_cards)
        roster2_text = format_cards_list(p2_cards)

        header = (
            f"🏒 **МАТЧ НАЧАЛСЯ!**\n"
            f"🔴 **{name1}** ({p1_ovr:.1f} OVR) vs 🔵 **{name2}** ({p2_ovr:.1f} OVR)\n\n"
            f"📋 **Состав {name1}:**\n{roster1_text}\n\n"
            f"📋 **Состав {name2}:**\n{roster2_text}\n\n"
            f"────────────────────\n"
        )

        await broadcast_match_text(context, p1_chat_id, p1_msg_id, p2_chat_id, p2_msg_id, f"{header}⏱ **1-й Период стартует! Команды выходят на лед...**")
        await asyncio.sleep(4)

        score1, score2 = 0, 0
        all_events = []

        prob_p1, prob_p2 = calc_goal_probabilities(p1_cards, p2_cards)

        for period in range(1, 4):
            period_header = f"⏱ **ПЕРИОД {period}**\n"
            
            for tick in range(1, 4):
                minute = (period - 1) * 20 + tick * 6 + random.randint(-1, 2)
                minute = min(60, max(1, minute))

                rand_val = random.random()

                if rand_val < prob_p1:
                    scorer = random.choice([p1_cards['skater1'], p1_cards['skater2'], p1_cards['skater3'], p1_cards['skater4']])
                    assist_cand = [p for k, p in p1_cards.items() if k != 'goalie' and p['id'] != scorer['id']]
                    assist = random.choice(assist_cand) if assist_cand else None
                    score1 += 1
                    
                    assist_str = f" (пас: {assist['nickname']})" if assist else ""
                    evt = f"⚡️ **{minute}' ГОЛ!** {scorer['nickname']}{assist_str} забивает за🔴 {name1}! [{score1}:{score2}]"
                    all_events.append(evt)

                elif rand_val < prob_p1 + prob_p2:
                    scorer = random.choice([p2_cards['skater1'], p2_cards['skater2'], p2_cards['skater3'], p2_cards['skater4']])
                    assist_cand = [p for k, p in p2_cards.items() if k != 'goalie' and p['id'] != scorer['id']]
                    assist = random.choice(assist_cand) if assist_cand else None
                    score2 += 1

                    assist_str = f" (пас: {assist['nickname']})" if assist else ""
                    evt = f"⚡️ **{minute}' ГОЛ!** {scorer['nickname']}{assist_str} забивает за🔵 {name2}! [{score1}:{score2}]"
                    all_events.append(evt)

                else:
                    event_type = random.choice(["save1", "save2", "post", "hit", "penalty"])
                    if event_type == "save1":
                        evt = f"🧤 **{minute}' СЕЙВ!** Вратарь {p1_cards['goalie']['nickname']} уверенно забирает шайбу!"
                    elif event_type == "save2":
                        evt = f"🧤 **{minute}' СЕЙВ!** Вратарь {p2_cards['goalie']['nickname']} отражает сильнейший бросок!"
                    elif event_type == "post":
                        sk = random.choice([p1_cards['skater1'], p2_cards['skater1']])
                        evt = f"🏒 **{minute}' ШТАНГА!** {sk['nickname']} наносит мощный щелчок, но шайба попадает в каркас!"
                    elif event_type == "hit":
                        sk1 = random.choice([p1_cards['skater1'], p1_cards['skater2']])
                        sk2 = random.choice([p2_cards['skater1'], p2_cards['skater2']])
                        evt = f"💥 **{minute}' СИЛОВОЙ ПРИЕМ!** {sk1['nickname']} жестко встретил {sk2['nickname']} у борта!"
                    else:
                        sk = random.choice([p1_cards['skater3'], p2_cards['skater3']])
                        evt = f"2️⃣ **{minute}' УДАЛЕНИЕ!** {sk['nickname']} получает 2 минуты малого штрафа."

                    all_events.append(evt)

                recent_events = "\n".join(all_events[-6:])
                status_text = (
                    f"{header}\n"
                    f"📊 **Текущий Счет:** 🔴 {score1} — {score2} 🔵\n"
                    f"{period_header}\n"
                    f"📝 **Ход матча:**\n{recent_events}"
                )
                await broadcast_match_text(context, p1_chat_id, p1_msg_id, p2_chat_id, p2_msg_id, status_text)
                await asyncio.sleep(3.5)

        await asyncio.sleep(2)

        if score1 == score2:
            evt_ot_start = "⏳ **ОСНОВНОЕ ВРЕМЯ ЗАВЕРШЕНО СО СЧЕТОМ " + f"{score1}:{score2}! Начинается ОВЕРТАЙМ (5 минут, 3х3 до золотого гола)!**"
            all_events.append(evt_ot_start)
            await broadcast_match_text(context, p1_chat_id, p1_msg_id, p2_chat_id, p2_msg_id, f"{header}\n📊 **Счет:** 🔴 {score1} — {score2} 🔵\n\n{evt_ot_start}")
            await asyncio.sleep(4)

            ot_prob1 = prob_p1 * 0.8
            ot_prob2 = prob_p2 * 0.8

            for ot_min in range(61, 66):
                rand_val = random.random()

                if rand_val < ot_prob1:
                    scorer = random.choice([p1_cards['skater1'], p1_cards['skater2']])
                    score1 += 1
                    evt = f"🔥 **{ot_min}' ЗОЛОТОЙ ГОЛ В ОВЕРТАЙМЕ!** {scorer['nickname']} приносит победу 🔴 {name1}! [{score1}:{score2}]"
                    all_events.append(evt)
                    break

                elif rand_val < ot_prob1 + ot_prob2:
                    scorer = random.choice([p2_cards['skater1'], p2_cards['skater2']])
                    score2 += 1
                    evt = f"🔥 **{ot_min}' ЗОЛОТОЙ ГОЛ В ОВЕРТАЙМЕ!** {scorer['nickname']} приносит победу 🔵 {name2}! [{score1}:{score2}]"
                    all_events.append(evt)
                    break

                else:
                    evt = f"🧤 **{ot_min}' СЕЙВ В ОВЕРТАЙМЕ!** Вратари на высоте!"
                    all_events.append(evt)

                recent_events = "\n".join(all_events[-6:])
                await broadcast_match_text(context, p1_chat_id, p1_msg_id, p2_chat_id, p2_msg_id, f"{header}\n📊 **Счет:** 🔴 {score1} — {score2} 🔵\n⏱ **ОВЕРТАЙМ**\n\n{recent_events}")
                await asyncio.sleep(3.5)

        if score1 == score2:
            evt_so_start = "🏒 **ОВЕРТАЙМ НЕ ВЫЯВИЛ ПОБЕДИТЕЛЯ! Начинается СЕРИЯ ПОСЛЕМАТЧЕВЫХ БУЛЛИТОВ!**"
            all_events.append(evt_so_start)
            await broadcast_match_text(context, p1_chat_id, p1_msg_id, p2_chat_id, p2_msg_id, f"{header}\n📊 **Счет:** 🔴 {score1} — {score2} 🔵\n\n{evt_so_start}")
            await asyncio.sleep(4)

            so_score1, so_score2 = 0, 0
            for round_num in range(1, 4):
                sk1 = random.choice([p1_cards['skater1'], p1_cards['skater2'], p1_cards['skater3'], p1_cards['skater4']])
                p1_prob = calc_shootout_prob(sk1['ovr'], p2_cards['goalie']['ovr'])
                if random.random() < p1_prob:
                    so_score1 += 1
                    evt1 = f"🎯 **Буллит #{round_num} 🔴 {name1}**: {sk1['nickname']} — **ГОЛ!** [{so_score1}:{so_score2}]"
                else:
                    evt1 = f"🚫 **Буллит #{round_num} 🔴 {name1}**: {sk1['nickname']} — **СЕЙВ!** [{so_score1}:{so_score2}]"
                all_events.append(evt1)

                sk2 = random.choice([p2_cards['skater1'], p2_cards['skater2'], p2_cards['skater3'], p2_cards['skater4']])
                p2_prob = calc_shootout_prob(sk2['ovr'], p1_cards['goalie']['ovr'])
                if random.random() < p2_prob:
                    so_score2 += 1
                    evt2 = f"🎯 **Буллит #{round_num} 🔵 {name2}**: {sk2['nickname']} — **ГОЛ!** [{so_score1}:{so_score2}]"
                else:
                    evt2 = f"🚫 **Буллит #{round_num} 🔵 {name2}**: {sk2['nickname']} — **СЕЙВ!** [{so_score1}:{so_score2}]"
                all_events.append(evt2)

                recent_events = "\n".join(all_events[-6:])
                await broadcast_match_text(context, p1_chat_id, p1_msg_id, p2_chat_id, p2_msg_id, f"{header}\n🎯 **СЕРИЯ БУЛЛИТОВ**\n📊 Буллиты: 🔴 {so_score1} — {so_score2} 🔵\n\n{recent_events}")
                await asyncio.sleep(3)

            if so_score1 > so_score2:
                score1 += 1
            else:
                score2 += 1

        await asyncio.sleep(2)

        conn = get_db()
        c = conn.cursor()

        if score1 > score2:
            res_text = f"🎉 **ПОБЕДА 🔴 {name1}!**\nИтоговый счет: **{score1} - {score2}**"
            apply_match_rewards(c, p1_id, is_win=True)
            apply_match_rewards(c, p2_id, is_win=False)
        else:
            res_text = f"🎉 **ПОБЕДА 🔵 {name2}!**\nИтоговый счет: **{score1} - {score2}**"
            apply_match_rewards(c, p2_id, is_win=True)
            apply_match_rewards(c, p1_id, is_win=False)

        conn.commit()
        conn.close()

        final_text = (
            f"🏁 **МАТЧ ЗАВЕРШЕН!**\n\n"
            f"{res_text}\n\n"
            f"🏆 Победитель получил: **+50 MMR** и **+2000 RPLCoin**\n"
            f"🥈 Проигравший получил: **-50 MMR** и **+500 RPLCoin**\n\n"
            f"📋 **Протокол игры:**\n" + "\n".join(all_events)
        )

        await broadcast_match_text(context, p1_chat_id, p1_msg_id, p2_chat_id, p2_msg_id, final_text)

    finally:
        active_games.discard(p1_id)
        active_games.discard(p2_id)

async def start_game_vs_ai(p1_id, chat_id, msg_id, context):
    active_games.add(p1_id)
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = %s", (p1_id,))
        u1 = c.fetchone()

        c.execute("SELECT * FROM user_rosters WHERE user_id = %s", (p1_id,))
        r1 = c.fetchone()
        p1_cards = get_roster_cards(c, r1)
        
        p1_ovr = sum(cd['ovr'] for cd in p1_cards.values()) / 5.0

        p1_card_ids = [cd['id'] for cd in p1_cards.values()]
        c.execute("SELECT * FROM cards WHERE id NOT IN %s AND ovr BETWEEN %s AND %s", 
                  (tuple(p1_card_ids), max(50, int(p1_ovr - 5)), int(p1_ovr + 5)))
        ai_candidates = c.fetchall()

        if not ai_candidates:
            c.execute("SELECT * FROM cards WHERE id NOT IN %s", (tuple(p1_card_ids),))
            ai_candidates = c.fetchall()

        conn.close()

        ai_skaters = [cd for cd in ai_candidates if cd['position'] == 'Skater']
        ai_goalies = [cd for cd in ai_candidates if cd['position'] == 'Goalie']

        if not ai_skaters or not ai_goalies:
            ai_cards = {
                "goalie": {"id": -1, "nickname": "AI Вратарь", "ovr": int(p1_ovr)},
                "skater1": {"id": -2, "nickname": "AI Форвард #1", "ovr": int(p1_ovr)},
                "skater2": {"id": -3, "nickname": "AI Форвард #2", "ovr": int(p1_ovr)},
                "skater3": {"id": -4, "nickname": "AI Защитник #1", "ovr": int(p1_ovr)},
                "skater4": {"id": -5, "nickname": "AI Защитник #2", "ovr": int(p1_ovr)}
            }
        else:
            ai_cards = {
                "goalie": random.choice(ai_goalies),
                "skater1": random.choice(ai_skaters),
                "skater2": random.choice(ai_skaters),
                "skater3": random.choice(ai_skaters),
                "skater4": random.choice(ai_skaters)
            }

        ai_ovr = sum(cd['ovr'] for cd in ai_cards.values()) / 5.0
        name1 = u1['first_name'] or u1['username'] or str(p1_id)

        roster1_text = format_cards_list(p1_cards)
        roster2_text = format_cards_list(ai_cards)

        header = (
            f"🏒 **МАТЧ ПРОТИВ ИИ БОТА НАЧАЛСЯ!**\n"
            f"🔴 **{name1}** ({p1_ovr:.1f} OVR) vs 🤖 **ИИ Бот** ({ai_ovr:.1f} OVR)\n\n"
            f"📋 **Состав {name1}:**\n{roster1_text}\n\n"
            f"📋 **Состав ИИ Бота:**\n{roster2_text}\n\n"
            f"────────────────────\n"
        )

        await broadcast_match_text(context, chat_id, msg_id, None, None, f"{header}⏱ **1-й Период стартует! Команды выходят на лед...**")
        await asyncio.sleep(4)

        score1, score2 = 0, 0
        all_events = []
        prob_p1, prob_ai = calc_goal_probabilities(p1_cards, ai_cards)

        for period in range(1, 4):
            period_header = f"⏱ **ПЕРИОД {period}**\n"
            for tick in range(1, 4):
                minute = (period - 1) * 20 + tick * 6 + random.randint(-1, 2)
                minute = min(60, max(1, minute))
                rand_val = random.random()

                if rand_val < prob_p1:
                    scorer = random.choice([p1_cards['skater1'], p1_cards['skater2'], p1_cards['skater3'], p1_cards['skater4']])
                    score1 += 1
                    evt = f"⚡️ **{minute}' ГОЛ!** {scorer['nickname']} забивает за🔴 {name1}! [{score1}:{score2}]"
                    all_events.append(evt)
                elif rand_val < prob_p1 + prob_ai:
                    scorer = random.choice([ai_cards['skater1'], ai_cards['skater2'], ai_cards['skater3'], ai_cards['skater4']])
                    score2 += 1
                    evt = f"⚡️ **{minute}' ГОЛ!** {scorer['nickname']} (ИИ Бот) забивает за🤖 ИИ Бота! [{score1}:{score2}]"
                    all_events.append(evt)
                else:
                    evt = f"🧤 **{minute}' СЕЙВ!** Вратари надежны!"
                    all_events.append(evt)

                recent_events = "\n".join(all_events[-6:])
                status_text = (
                    f"{header}\n"
                    f"📊 **Счет:** 🔴 {score1} — {score2} 🤖\n"
                    f"{period_header}\n"
                    f"📝 **Ход матча:**\n{recent_events}"
                )
                await broadcast_match_text(context, chat_id, msg_id, None, None, status_text)
                await asyncio.sleep(3.5)

        await asyncio.sleep(2)
        if score1 == score2:
            score1 += 1 # Буллитная развязка в пользу игрока при ничьей с ботом

        conn = get_db()
        c = conn.cursor()
        if score1 > score2:
            res_text = f"🎉 **ПОБЕДА НАД ИИ БОТОМ!**\nИтоговый счет: **{score1} - {score2}**"
            apply_match_rewards(c, p1_id, is_win=True)
        else:
            res_text = f"❌ **ПОРАЖЕНИЕ ОТ ИИ БОТА!**\nИтоговый счет: **{score1} - {score2}**"
            apply_match_rewards(c, p1_id, is_win=False)

        conn.commit()
        conn.close()

        final_text = f"🏁 **МАТЧ ЗАВЕРШЕН!**\n\n{res_text}\n\n📋 **Протокол:**\n" + "\n".join(all_events)
        await broadcast_match_text(context, chat_id, msg_id, None, None, final_text)

    finally:
        active_games.discard(p1_id)

def get_roster_cards(cursor, roster):
    cursor.execute("SELECT * FROM cards WHERE id IN (%s, %s, %s, %s, %s)", 
                   (roster['goalie_id'], roster['skater1_id'], roster['skater2_id'], roster['skater3_id'], roster['skater4_id']))
    cds = {cd['id']: cd for cd in cursor.fetchall()}
    return {
        "goalie": cds[roster['goalie_id']],
        "skater1": cds[roster['skater1_id']],
        "skater2": cds[roster['skater2_id']],
        "skater3": cds[roster['skater3_id']],
        "skater4": cds[roster['skater4_id']]
    }

def apply_match_rewards(cursor, user_id, is_win):
    if is_win is True:
        cursor.execute("UPDATE users SET mmr = mmr + 50, balance = balance + 2000 WHERE user_id = %s", (user_id,))
    else:
        cursor.execute("UPDATE users SET mmr = GREATEST(0, mmr - 50), balance = balance + 500 WHERE user_id = %s", (user_id,))

# ---------- ТОП MMR (/cardmmr) ----------
async def cardmmr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, first_name, mmr FROM users ORDER BY mmr DESC LIMIT 10")
    top = c.fetchall()
    conn.close()

    if not top:
        await update.message.reply_text("🏆 **ТОП-10 ИГРОКОВ ПО MMR:**\n\nПока нет зарегистрированных игроков.", parse_mode="Markdown")
        return

    text = "🏆 **ТОП-10 ИГРОКОВ ПО MMR:**\n\n"
    for i, u in enumerate(top, 1):
        name = u['first_name'] or u['username'] or "Игрок"
        safe_name = name.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")
        text += f"{i}. **{safe_name}** — `{u['mmr']} MMR`\n"

    await update.message.reply_text(text, parse_mode="Markdown")

# ---------- МАГАЗИН И ПАКЕТЫ С ПРЕДПРОСМОТРОМ (/shop) ----------
async def shop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    await show_shop(update, context)

async def show_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user if query else update.effective_user

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM packs")
    packs = c.fetchall()
    conn.close()

    if not packs:
        text = "🛒 **Магазин Паков пуст.** Администратор скоро добавит новые паки!"
        if query:
            await query.answer()
            await query.message.edit_text(text)
        else:
            await update.message.reply_text(text)
        return

    text = "🛒 **МАГАЗИН ПАКОВ КАРТОЧЕК:**\nВыберите пак для подробной информации и покупки:"
    buttons = []

    for p in packs:
        buttons.append([InlineKeyboardButton(f"📦 {p['name']} — {p['price']} RPLCoin", callback_data=f"preview_pack_{p['id']}")])

    markup = InlineKeyboardMarkup(buttons)
    if query:
        await query.answer()
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            await query.message.delete()
            await context.bot.send_message(user.id, text, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def shop_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    query = update.callback_query
    user = query.from_user
    data = query.data

    if data == "shop_back":
        await show_shop(update, context)
        return

    if data.startswith("preview_pack_"):
        pack_id = int(data.split("_")[2])
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM packs WHERE id = %s", (pack_id,))
        pack = c.fetchone()

        c.execute("SELECT buy_count FROM user_pack_buys WHERE user_id = %s AND pack_id = %s", (user.id, pack_id))
        b_row = c.fetchone()
        b_count = b_row['buy_count'] if b_row else 0
        conn.close()

        if not pack:
            await query.answer("❌ Пак не найден!", show_alert=True)
            return

        lim_str = f"{b_count}/{pack['buy_limit']}" if pack['buy_limit'] > 0 else "Безлимит"
        text = (
            f"📦 **Пак: {pack['name']}**\n\n"
            f"💰 Цена: **{pack['price']} RPLCoin**\n"
            f"📊 Куплено: `{lim_str}`\n\n"
            f"Подтвердите покупку пака:"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить покупку", callback_data=f"buy_pack_{pack['id']}")],
            [InlineKeyboardButton("🔙 Назад в магазин", callback_data="shop_back")]
        ])

        if pack['photo_id']:
            try:
                await query.message.delete()
                await context.bot.send_photo(chat_id=user.id, photo=pack['photo_id'], caption=text, reply_markup=kb, parse_mode="Markdown")
                return
            except Exception:
                pass

        await query.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
        return

    if data.startswith("buy_pack_"):
        pack_id = int(data.split("_")[2])
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT * FROM packs WHERE id = %s", (pack_id,))
        pack = c.fetchone()
        c.execute("SELECT balance FROM users WHERE user_id = %s", (user.id,))
        u_bal = c.fetchone()['balance']

        if not pack:
            conn.close()
            await query.answer("❌ Пак не найден!", show_alert=True)
            return

        if u_bal < pack['price']:
            conn.close()
            await query.answer("❌ У вас недостаточно RPLCoin!", show_alert=True)
            return

        c.execute("SELECT buy_count FROM user_pack_buys WHERE user_id = %s AND pack_id = %s", (user.id, pack_id))
        b_row = c.fetchone()
        b_count = b_row['buy_count'] if b_row else 0

        if pack['buy_limit'] > 0 and b_count >= pack['buy_limit']:
            conn.close()
            await query.answer("❌ Вы исчерпали лимит покупки этого пака!", show_alert=True)
            return

        c.execute("SELECT c.* FROM pack_cards pc JOIN cards c ON pc.card_id = c.id WHERE pc.pack_id = %s", (pack_id,))
        p_cards = c.fetchall()

        if not p_cards:
            conn.close()
            await query.answer("❌ В этом паке нет карточек!", show_alert=True)
            return

        chosen_card = choose_card_for_user(c, user.id, p_cards)
        chosen_card_id = chosen_card['id']

        # Анимация открытия пака (3 секунды)
        await query.answer("📦 Идет открытие пака... ⏳")
        try:
            await query.message.edit_text("📦 **Идет вскрытие пака и розыгрыш карточки...** ⏳", parse_mode="Markdown")
        except Exception:
            pass
        await asyncio.sleep(3)

        c.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (pack['price'], user.id))
        c.execute('''
            INSERT INTO user_pack_buys (user_id, pack_id, buy_count) VALUES (%s, %s, 1)
            ON CONFLICT (user_id, pack_id) DO UPDATE SET buy_count = user_pack_buys.buy_count + 1
        ''', (user.id, pack_id))
        c.execute('''
            INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1)
            ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1
        ''', (user.id, chosen_card_id))

        c.execute('''
            SELECT c.*, col.name as collection_name, t.name as team_name, t.emoji as team_emoji
            FROM cards c
            JOIN collections col ON c.collection_id = col.id
            LEFT JOIN card_teams t ON c.team_id = t.id
            WHERE c.id = %s
        ''', (chosen_card_id,))
        card = c.fetchone()

        conn.commit()
        conn.close()

        team_str = f"{card['team_emoji'] or '🏒'} {card['team_name']}" if card['team_name'] else "Без команды"
        caption = (
            f"📦 **Из пака «{pack['name']}» вам выпала карточка!**\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 👤 {card['nickname']}\n"
            f"┃ 🏒 {card['position']}\n"
            f"┃ ⭐ {card['ovr']} OVR\n"
            f"┃ {team_str}\n"
            f"┃ 🌍 {card['country']}\n"
            f"┃ ✨ {card['rarity']}\n"
            f"┗━━━━━━━━━━━━━━━━━━━━┛"
        )

        try:
            await query.message.delete()
        except Exception:
            pass

        if card['image_id']:
            try:
                await context.bot.send_photo(chat_id=user.id, photo=card['image_id'], caption=caption, parse_mode="Markdown")
                return
            except Exception:
                pass
        await context.bot.send_message(chat_id=user.id, text=caption, parse_mode="Markdown")

# ---------- АДМИН-ПАНЕЛЬ КАРТОЧЕК И ПРОМОКОДОВ ----------
async def admin_card_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "📁 Создать коллекцию":
        await update.message.reply_text("📁 Введите название для новой коллекции:")
        return ADD_COLLECTION_NAME

    elif text == "🛡 Создать команду":
        await update.message.reply_text("🛡 Введите название новой команды (например: `Ак Барс (Казань)`):", parse_mode="Markdown")
        return ADD_TEAM_NAME

    elif text == "❌ Удалить команду":
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM card_teams")
        teams = c.fetchall()
        conn.close()

        if not teams:
            await update.message.reply_text("📭 Нет созданных команд.", reply_markup=card_admin_keyboard())
            return CARD_ADMIN_MENU

        buttons = [[InlineKeyboardButton(f"{t['emoji']} {t['name']}", callback_data=f"del_team_{t['id']}")] for t in teams]
        await update.message.reply_text("Выберите команду для удаления:", reply_markup=InlineKeyboardMarkup(buttons))
        return DEL_TEAM_SELECT

    elif text == "🃏 Добавить карточку":
        kb = [["Редкая", "Очень редкая"], ["Эпическая", "Мифическая"], ["Легендарная", "Секретная"]]
        await update.message.reply_text("✨ Выберите редкость карточки:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
        return ADD_CARD_RARITY

    elif text == "❌ Удалить карточку":
        await update.message.reply_text("❌ Введите ID карточки для удаления:")
        return DEL_CARD_ID

    elif text == "📦 Добавить пак":
        await update.message.reply_text("📦 Введите название пака:")
        return ADD_PACK_NAME

    elif text == "🎁 Выдать карточку игроку":
        await update.message.reply_text("🎁 Введите ID/@username пользователя и ID карточки через пробел:", parse_mode="Markdown")
        return GRANT_CARD_DATA

    elif text == "💰 Выдать деньги":
        await update.message.reply_text("💰 Введите @username пользователя и сумму через пробел:", parse_mode="Markdown")
        return GIVE_MONEY_DATA

    elif text == "⬅️ Выйти из настройки карточек":
        await update.message.reply_text("⚙️ Админ-панель:", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END

    return CARD_ADMIN_MENU

# --- Создание промокода (через админку) ---
async def start_create_promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await update.message.reply_text("🎟 Введите текстовый промокод (например `RPL2026`):", reply_markup=ReplyKeyboardRemove())
    return ADD_PROMO_CODE

async def promo_set_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["promo_code"] = update.message.text.strip().upper()
    kb = [["Деньги (RPLCoin)", "Карточка"]]
    await update.message.reply_text("📌 Выберите тип награды за промокод:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return ADD_PROMO_TYPE

async def promo_set_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t_text = update.message.text.strip()
    if "Деньги" in t_text:
        context.user_data["promo_type"] = "money"
        await update.message.reply_text("💰 Введите сумму RPLCoin для награды:", reply_markup=ReplyKeyboardRemove())
    else:
        context.user_data["promo_type"] = "card"
        await update.message.reply_text("🎴 Введите ID карточки для награды:", reply_markup=ReplyKeyboardRemove())
    return ADD_PROMO_REWARD

async def promo_set_reward(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text.strip())
        context.user_data["promo_reward"] = val
        await update.message.reply_text("🔢 Введите общее количество активаций (лимит использования промокода среди всех игроков):")
        return ADD_PROMO_LIMIT
    except ValueError:
        await update.message.reply_text("❌ Введите числовое значение!")
        return ADD_PROMO_REWARD

async def promo_save_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        max_uses = int(update.message.text.strip())
        code = context.user_data.get("promo_code")
        r_type = context.user_data.get("promo_type")
        r_val = context.user_data.get("promo_reward")

        conn = get_db()
        c = conn.cursor()
        c.execute('''
            INSERT INTO promos (code, reward_type, reward_value, max_uses, used_count)
            VALUES (%s, %s, %s, %s, 0)
            ON CONFLICT (code) DO UPDATE SET reward_type = EXCLUDED.reward_type, reward_value = EXCLUDED.reward_value, max_uses = EXCLUDED.max_uses
        ''', (code, r_type, r_val, max_uses))
        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ Промокод **{code}** успешно создан!\nТип: `{r_type}` | Награда: `{r_val}` | Лимит: `{max_uses}` активаций.", reply_markup=admin_menu_keyboard(), parse_mode="Markdown")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Введите лимит числом!", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END

# --- Стандартные функции создания коллекций/команд/карт/паков ---
async def save_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO collections (name) VALUES (%s)", (name,))
        conn.commit()
        await update.message.reply_text(f"✅ Коллекция **{name}** создана!", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("❌ Ошибка! Коллекция с таким именем уже существует.", reply_markup=card_admin_keyboard())
    conn.close()
    return CARD_ADMIN_MENU

async def save_team_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["team_name"] = update.message.text.strip()
    await update.message.reply_text("🏒 Введите эмодзи для команды:")
    return ADD_TEAM_EMOJI

async def save_team_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["team_emoji"] = update.message.text.strip()
    await update.message.reply_text("🖼 Отправьте фото команды (или `-`):")
    return ADD_TEAM_PHOTO

async def save_team_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = update.message.photo[-1].file_id if update.message.photo else None
    name = context.user_data.get("team_name")
    emoji = context.user_data.get("team_emoji", "🏒")

    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO card_teams (name, emoji, photo_id) VALUES (%s, %s, %s)", (name, emoji, photo_id))
        conn.commit()
        await update.message.reply_text(f"✅ Команда {emoji} **{name}** создана!", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
    except Exception:
        await update.message.reply_text("❌ Ошибка при создании команды.", reply_markup=card_admin_keyboard())
    conn.close()
    return CARD_ADMIN_MENU

async def delete_team_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    team_id = int(query.data.split("_")[2])

    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM card_teams WHERE id = %s", (team_id,))
    conn.commit()
    conn.close()

    await query.edit_message_text("✅ Команда удалена!")
    return CARD_ADMIN_MENU

async def card_set_rarity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_rarity"] = update.message.text.strip()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM collections")
    cols = c.fetchall()
    conn.close()

    if not cols:
        await update.message.reply_text("❌ Создайте коллекцию!", reply_markup=card_admin_keyboard())
        return CARD_ADMIN_MENU

    buttons = [[c_row['name']] for c_row in cols]
    await update.message.reply_text("📁 Выберите коллекцию:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
    return ADD_CARD_COLLECTION

async def card_set_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_collection"] = update.message.text.strip()
    kb = [COUNTRIES[i:i+3] for i in range(0, len(COUNTRIES), 3)]
    await update.message.reply_text("🌍 Выберите страну:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return ADD_CARD_COUNTRY

async def card_set_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_country"] = update.message.text.strip()
    kb = [["Skater", "Goalie"]]
    await update.message.reply_text("🏒 Выберите позицию:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return ADD_CARD_POSITION

async def card_set_position(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_position"] = update.message.text.strip()
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM card_teams")
    teams = c.fetchall()
    conn.close()

    buttons = [[f"{t['emoji']} {t['name']}"] for t in teams]
    buttons.append(["Без команды"])
    await update.message.reply_text("🛡 Выберите команду:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
    return ADD_CARD_TEAM

async def card_set_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_team"] = update.message.text.strip()
    await update.message.reply_text("🏷 Введите NickName игрока:", reply_markup=ReplyKeyboardRemove())
    return ADD_CARD_NICK

async def card_set_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_nick"] = update.message.text.strip()
    await update.message.reply_text("⭐ Введите OVR (50-99):")
    return ADD_CARD_OVR

async def card_set_ovr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ovr = int(update.message.text.strip())
        context.user_data["c_ovr"] = ovr
        await update.message.reply_text("🖼 Отправьте фото или GIF карточки:")
        return ADD_CARD_PHOTO
    except ValueError:
        await update.message.reply_text("❌ Введите OVR числом!")
        return ADD_CARD_OVR

async def card_save_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = update.message.photo[-1].file_id if update.message.photo else (update.message.animation.file_id if update.message.animation else None)
    rarity = context.user_data.get("c_rarity")
    col_name = context.user_data.get("c_collection")
    country = context.user_data.get("c_country")
    position = context.user_data.get("c_position")
    team_text = context.user_data.get("c_team")
    nick = context.user_data.get("c_nick")
    ovr = context.user_data.get("c_ovr")

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM collections WHERE name = %s", (col_name,))
    col_row = c.fetchone()
    col_id = col_row['id'] if col_row else None

    team_id = None
    if team_text != "Без команды":
        c.execute("SELECT id FROM card_teams WHERE CONCAT(emoji, ' ', name) = %s OR name = %s", (team_text, team_text))
        t_row = c.fetchone()
        if t_row:
            team_id = t_row['id']

    c.execute('''
        INSERT INTO cards (collection_id, team_id, nickname, position, ovr, country, rarity, image_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    ''', (col_id, team_id, nick, position, ovr, country, rarity, photo_id))
    
    new_card_id = c.fetchone()['id']
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Карточка создана! ID: `{new_card_id}`", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
    return CARD_ADMIN_MENU

async def delete_card_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        card_id = int(update.message.text.strip())
        conn = get_db()
        c = conn.cursor()
        c.execute("DELETE FROM cards WHERE id = %s", (card_id,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Карточка ID {card_id} удалена!", reply_markup=card_admin_keyboard())
    except ValueError:
        await update.message.reply_text("❌ Введите ID числом!", reply_markup=card_admin_keyboard())
    return CARD_ADMIN_MENU

async def pack_set_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["p_name"] = update.message.text.strip()
    await update.message.reply_text("💰 Введите цену пака в RPLCoin:")
    return ADD_PACK_PRICE

async def pack_set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["p_price"] = int(update.message.text.strip())
        await update.message.reply_text("🔢 Введите лимит покупок (0 = безлимит):")
        return ADD_PACK_LIMIT
    except ValueError:
        await update.message.reply_text("❌ Введите цену числом!")
        return ADD_PACK_PRICE

async def pack_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["p_limit"] = int(update.message.text.strip())
        await update.message.reply_text("🆔 Введите ID карточек через пробел (до 10 ID):")
        return ADD_PACK_CARDS
    except ValueError:
        await update.message.reply_text("❌ Введите лимит числом!")
        return ADD_PACK_LIMIT

async def pack_set_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        card_ids = [int(x) for x in update.message.text.strip().split()]
        context.user_data["p_cards"] = card_ids
        await update.message.reply_text("🖼 Отправьте обложку пака (фото):")
        return ADD_PACK_PHOTO
    except ValueError:
        await update.message.reply_text("❌ Введите ID карточек через пробел!")
        return ADD_PACK_CARDS

async def pack_save_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = update.message.photo[-1].file_id if update.message.photo else None
    name = context.user_data.get("p_name")
    price = context.user_data.get("p_price")
    limit = context.user_data.get("p_limit")
    card_ids = context.user_data.get("p_cards", [])

    conn = get_db()
    c = conn.cursor()
    c.execute("INSERT INTO packs (name, price, buy_limit, photo_id) VALUES (%s, %s, %s, %s) RETURNING id",
              (name, price, limit, photo_id))
    pack_id = c.fetchone()['id']

    for cid in card_ids:
        c.execute("INSERT INTO pack_cards (pack_id, card_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (pack_id, cid))

    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Пак **{name}** создан!", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
    return CARD_ADMIN_MENU

async def grant_card_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.strip().split()
        user_input = parts[0].replace("@", "")
        card_id = int(parts[1])

        conn = get_db()
        c = conn.cursor()
        target_id = int(user_input) if user_input.isdigit() else (c.execute("SELECT user_id FROM users WHERE username = %s", (user_input,)) or c.fetchone()['user_id'])
        c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (target_id, card_id))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Выдано!", reply_markup=card_admin_keyboard())
    except Exception:
        await update.message.reply_text("❌ Ошибка формата!", reply_markup=card_admin_keyboard())
    return CARD_ADMIN_MENU

async def give_money_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.strip().split()
        username = parts[0].replace("@", "")
        amount = int(parts[1])

        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET balance = balance + %s WHERE username = %s", (amount, username))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Зачислено!", reply_markup=card_admin_keyboard())
    except Exception:
        await update.message.reply_text("❌ Ошибка формата!", reply_markup=card_admin_keyboard())
    return CARD_ADMIN_MENU

# ---------- ПРОСМОТР ИГРОКОВ В АДМИНКЕ ----------
async def admin_view_inventory_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip().replace("@", "")
    conn = get_db()
    c = conn.cursor()
    if user_input.isdigit():
        c.execute("SELECT * FROM users WHERE user_id = %s", (int(user_input),))
    else:
        c.execute("SELECT * FROM users WHERE username = %s", (user_input,))
    target_user = c.fetchone()

    if not target_user:
        conn.close()
        await update.message.reply_text("❌ Игрок не найден!", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END

    target_id = target_user['user_id']
    c.execute('''
        SELECT uc.count, c.*, col.name as col_name, t.name as team_name, t.emoji as team_emoji
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        JOIN collections col ON c.collection_id = col.id
        LEFT JOIN card_teams t ON c.team_id = t.id
        WHERE uc.user_id = %s AND uc.count > 0
    ''', (target_id,))
    user_cards = c.fetchall()
    conn.close()

    text = f"🎒 Инвентарь @{target_user.get('username') or target_id}:\n"
    for uc in user_cards:
        text += f"ID `{uc['id']}` | **{uc['nickname']}** ({uc['ovr']} OVR) — `x{uc['count']}`\n"

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_menu_keyboard())
    return ConversationHandler.END

async def admin_show_players_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id, username, first_name, balance, mmr FROM users ORDER BY user_id DESC")
    users = c.fetchall()
    conn.close()

    text = f"👥 Всего игроков: {len(users)}\n\n"
    for u in users[:30]:
        text += f"• @{u['username'] or u['first_name']} (`{u['user_id']}`) | {u['balance']} RPL | {u['mmr']} MMR\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_menu_keyboard())

# ---------- СТАРТ И ОБРАБОТЧИКИ ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    await update.message.reply_text(
        "👋 Добро пожаловать в **Russian Puck League**!\n"
        "Выберите действие с помощью меню ниже.",
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard()
    )

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return
    await update.message.reply_text("📌 Выберите раздел:", reply_markup=welcome_inline_keyboard())

async def adminkarpl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return ConversationHandler.END
    if is_admin(update.effective_user.id):
        await update.message.reply_text("Вы уже авторизованы.", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END
    await update.message.reply_text("🔑 Введите логин:")
    return WAITING_LOGIN

async def wait_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["login"] = update.message.text
    await update.message.reply_text("🔒 Введите пароль:")
    return WAITING_PASSWORD

async def wait_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if check_credentials(context.user_data.get("login"), update.message.text):
        add_admin(update.effective_user.id)
        context.user_data.clear()
        await update.message.reply_text("✅ Авторизован!", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END
    await update.message.reply_text("❌ Неверный пароль!")
    return ConversationHandler.END

async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    update_admin_activity(user_id)
    text = update.message.text

    if text == "➕ Добавить каналы":
        await update.message.reply_text("Введите @username канала:")
        return WAITING_CHANNEL_USERNAME
    elif text == "➕ Добавить чаты":
        await update.message.reply_text("Введите ID чата:")
        return WAITING_CHAT_LINK
    elif text == "📩 Проверить поддержку":
        messages = get_unanswered_messages()
        if not messages:
            await update.message.reply_text("📭 Новых обращений нет.", reply_markup=admin_menu_keyboard())
            return
        msg = messages[0]
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Закрыть", callback_data=f"close_{msg['id']}")]])
        await update.message.reply_text(f"📩 #{msg['id']} от @{msg['username']}:\n\n{msg['text']}", reply_markup=kb)
        return
    elif text == "⚙️ Настройки":
        await update.message.reply_text("⚙️ Настройки активны.", reply_markup=admin_menu_keyboard())
        return
    elif text == "🎮 Настройки игры":
        await update.message.reply_text("🎮 Настройки матчей активны.", reply_markup=admin_menu_keyboard())
        return
    elif text == "🃏 Карточки":
        await update.message.reply_text("🃏 Меню карточек:", reply_markup=card_admin_keyboard())
        return CARD_ADMIN_MENU
    elif text == "🎟 Создать промокод":
        return await start_create_promo(update, context)
    elif text == "🔍 Инвентарь игрока":
        await update.message.reply_text("🔍 Введите ID или username игрока:")
        return WAITING_VIEW_USER_INV
    elif text == "👥 Список игроков":
        await admin_show_players_list(update, context)
        return
    elif text == "🚪 Выйти":
        remove_admin(user_id)
        await update.message.reply_text("🚪 Выход выполнен.", reply_markup=main_menu_keyboard())
        return

async def add_channel_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    try:
        chat = await context.bot.get_chat(username if username.startswith('@') else '@' + username)
        add_source_channel(chat.id, username, update.effective_user.id)
        await update.message.reply_text("✅ Канал добавлен.", reply_markup=admin_menu_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
    return ConversationHandler.END

async def add_chat_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    try:
        chat = await context.bot.get_chat(link)
        add_target_chat(chat.id, link, update.effective_user.id)
        await update.message.reply_text("✅ Чат добавлен.", reply_markup=admin_menu_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
    return ConversationHandler.END

async def inline_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "discord":
        await query.message.reply_text("💬 **Discord:** https://discord.gg/dgkFMCgDwx")
    elif data == "website":
        await query.message.reply_text("🌐 **Сайт:** rplpuck.ru")
    elif data == "support":
        await query.message.reply_text("✍️ Напишите ваше сообщение в поддержку текстом:")
        return WAITING_SUPPORT_MSG
    elif data == "duel":
        await query.message.reply_text("🏒 **Дуэль Буллитов!** Выбери зону:", reply_markup=duel_shot_keyboard())
        return WAITING_DUEL_SHOT

async def duel_shot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    scored = random.random() < 0.35
    if scored:
        await query.edit_message_text("⚡️ **ГОЛ!** Вы точно попали!")
    else:
        await query.edit_message_text("🧤 **СЕЙВ!** Вратарь отразил бросок!")
    return ConversationHandler.END

async def support_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_support_message(user.id, user.username or str(user.id), update.message.text)
    await update.message.reply_text("✅ Сообщение отправлено в поддержку.")
    return ConversationHandler.END

# ---------- MAIN ----------
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("getid", getid_command))

    # Авторизация админа
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("adminkarpl", adminkarpl)],
        states={
            WAITING_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, wait_login)],
            WAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, wait_password)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    ))

    # Создание промокода (Админка)
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🎟 Создать промокод$") & filters.ChatType.PRIVATE, admin_buttons)],
        states={
            ADD_PROMO_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, promo_set_code)],
            ADD_PROMO_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, promo_set_type)],
            ADD_PROMO_REWARD: [MessageHandler(filters.TEXT & ~filters.COMMAND, promo_set_reward)],
            ADD_PROMO_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, promo_save_all)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить каналы$") & filters.ChatType.PRIVATE, admin_buttons)],
        states={WAITING_CHANNEL_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_username)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить чаты$") & filters.ChatType.PRIVATE, admin_buttons)],
        states={WAITING_CHAT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_chat_link)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔍 Инвентарь игрока$") & filters.ChatType.PRIVATE, admin_buttons)],
        states={WAITING_VIEW_USER_INV: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_view_inventory_execute)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(inline_callback, pattern="^support$")],
        states={WAITING_SUPPORT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(inline_callback, pattern="^duel$")],
        states={WAITING_DUEL_SHOT: [CallbackQueryHandler(duel_shot, pattern="^shot_")]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    ))

    # Управление карточками в админке
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🃏 Карточки$") & filters.ChatType.PRIVATE, admin_buttons)],
        states={
            CARD_ADMIN_MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_card_menu_handler)],
            ADD_COLLECTION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_collection)],
            ADD_TEAM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_team_name)],
            ADD_TEAM_EMOJI: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_team_emoji)],
            ADD_TEAM_PHOTO: [MessageHandler(filters.PHOTO | filters.TEXT, save_team_photo)],
            DEL_TEAM_SELECT: [CallbackQueryHandler(delete_team_callback, pattern="^del_team_")],
            ADD_CARD_RARITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_set_rarity)],
            ADD_CARD_COLLECTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_set_collection)],
            ADD_CARD_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_set_country)],
            ADD_CARD_POSITION: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_set_position)],
            ADD_CARD_TEAM: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_set_team)],
            ADD_CARD_NICK: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_set_nick)],
            ADD_CARD_OVR: [MessageHandler(filters.TEXT & ~filters.COMMAND, card_set_ovr)],
            ADD_CARD_PHOTO: [MessageHandler(filters.PHOTO | filters.ANIMATION, card_save_all)],
            DEL_CARD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, delete_card_execute)],
            ADD_PACK_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, pack_set_name)],
            ADD_PACK_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, pack_set_price)],
            ADD_PACK_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, pack_set_limit)],
            ADD_PACK_CARDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, pack_set_cards)],
            ADD_PACK_PHOTO: [MessageHandler(filters.PHOTO, pack_save_all)],
            GRANT_CARD_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, grant_card_execute)],
            GIVE_MONEY_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, give_money_execute)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
        allow_reentry=True,
    ))

    app.add_handler(MessageHandler(filters.Regex("^(📩 Проверить поддержку|⚙️ Настройки|🎮 Настройки игры|👥 Список игроков|🚪 Выйти)$") & filters.ChatType.PRIVATE, admin_buttons))

    # Команды бота
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rplcards", rplcards_command))
    app.add_handler(CommandHandler("inventory", inventory_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("cardmatch", cardmatch_command))
    app.add_handler(CommandHandler("cardmmr", cardmmr_command))
    app.add_handler(CommandHandler("shop", shop_command))
    app.add_handler(CommandHandler("trade", trade_command))
    app.add_handler(CommandHandler("cardshop", cardshop_command))
    app.add_handler(CommandHandler("promo", promo_command))

    # Кнопки клавиатуры
    app.add_handler(MessageHandler(filters.Regex("^🏠 Главное меню$"), main_menu))
    app.add_handler(MessageHandler(filters.Regex("^🎒 Инвентарь$"), inventory_command))
    app.add_handler(MessageHandler(filters.Regex("^🏒 Состав и Профиль$"), profile_command))
    app.add_handler(MessageHandler(filters.Regex("^⚔️ Искать игру$"), cardmatch_command))
    app.add_handler(MessageHandler(filters.Regex("^🛒 Магазин Паков$"), shop_command))
    app.add_handler(MessageHandler(filters.Regex("^🎴 Бесплатная карта$"), rplcards_command))
    app.add_handler(MessageHandler(filters.Regex("^🔄 Трейд$"), lambda u,c: u.message.reply_text("🤝 Введите `/trade @username ID [деньги]` в чат для обмена.", parse_mode="Markdown")))
    app.add_handler(MessageHandler(filters.Regex("^🛍 Торговая площадка$"), cardshop_command))
    app.add_handler(MessageHandler(filters.Regex("^🎟 Промокод$"), lambda u,c: u.message.reply_text("🎟 Введите `/promo <код>` для активации промокода.", parse_mode="Markdown")))
    app.add_handler(MessageHandler(filters.Regex("^🏆 Топ MMR$"), cardmmr_command))

    # Callbacks
    app.add_handler(CallbackQueryHandler(inventory_callback_handler, pattern="^(refresh_inv|craft_leg_|sell_menu|do_sell_)"))
    app.add_handler(CallbackQueryHandler(profile_callback_handler, pattern="^(refresh_profile|edit_roster_menu|set_pos_|apply_card_)"))
    app.add_handler(CallbackQueryHandler(match_callback_handler, pattern="^(accept_match_|cancel_match_)"))
    app.add_handler(CallbackQueryHandler(shop_callback_handler, pattern="^(buy_pack_|preview_pack_|shop_back)"))
    app.add_handler(CallbackQueryHandler(trade_callback_handler, pattern="^trade_(accept|decline)_"))
    app.add_handler(CallbackQueryHandler(cardshop_callback_handler, pattern="^(buy_shop_|refresh_cardshop)"))
    app.add_handler(CallbackQueryHandler(inline_callback))

    logger.info("Бот RPL с обновлениями успешно запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
