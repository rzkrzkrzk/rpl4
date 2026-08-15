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

# Цены быстрой продажи карточек системно
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
    # Промокоды
    ADD_PROMO_CODE,
    ADD_PROMO_TYPE,
    ADD_PROMO_VAL,
    ADD_PROMO_LIMIT,
    WAITING_PROMO_INPUT,
    # Рынок и Трейд
    WAITING_TRADE_TARGET,
    WAITING_TRADE_MONEY,
    WAITING_MARKET_PRICE_INPUT,
    # Мини-игра КНБ ставка
    WAITING_RPS_BET,
    # Админ выставление паков на время
    ADMIN_SHOP_PACK_SELECT,
    ADMIN_SHOP_PACK_HOURS,
) = range(43)

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
            last_card_claim TIMESTAMP,
            last_daily_claim TIMESTAMP,
            daily_streak INTEGER DEFAULT 0,
            last_wheel_spin TIMESTAMP,
            free_card_cooldown_reset_until TIMESTAMP
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
            photo_id TEXT,
            available_until TIMESTAMP
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

    # Промокоды
    c.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            code TEXT PRIMARY KEY,
            reward_type TEXT NOT NULL,
            reward_value INTEGER NOT NULL,
            max_uses INTEGER DEFAULT 1,
            current_uses INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_promocodes (
            user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
            code TEXT REFERENCES promo_codes(code) ON DELETE CASCADE,
            used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(user_id, code)
        )
    ''')

    # Торговая площадка (Рынок)
    c.execute('''
        CREATE TABLE IF NOT EXISTS market (
            id SERIAL PRIMARY KEY,
            seller_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
            card_id INTEGER REFERENCES cards(id) ON DELETE CASCADE,
            price INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        ["🏠 Главное меню", "🃏 Бесплатная карта"],
        ["🎒 Инвентарь", "🛒 Торговая площадка"],
        ["🏒 Состав и Профиль", "⚔️ Искать игру"],
        ["🛒 Магазин Паков", "🏆 Топ MMR"],
        ["🤝 Трейд", "🎁 Промокод"],
        ["🎮 Мини-игры", "🎡 Колесо удачи"],
        ["🎁 Ежедневный бонус"]
    ], resize_keyboard=True)

def admin_menu_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Добавить каналы", "➕ Добавить чаты"],
        ["📩 Проверить поддержку", "⚙️ Настройки"],
        ["🎮 Настройки игры", "🃏 Карточки"],
        ["📦 Выставить пак в магазин", "🔍 Инвентарь игрока"],
        ["👥 Список игроков", "🚪 Выйти"]
    ], resize_keyboard=True)

def card_admin_keyboard():
    return ReplyKeyboardMarkup([
        ["📁 Создать коллекцию", "🛡 Создать команду"],
        ["❌ Удалить команду", "🃏 Добавить карточку"],
        ["❌ Удалить карточку", "📦 Добавить пак"],
        ["🎁 Выдать карточку игроку", "💰 Выдать деньги"],
        ["🎟 Создать промокод", "⬅️ Выйти из настройки карточек"]
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

# ---------- ЕЖЕДНЕВНЫЙ БОНУС (/daily) ----------
async def daily_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    user = update.effective_user
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT last_daily_claim, daily_streak FROM users WHERE user_id = %s", (user.id,))
    row = c.fetchone()
    conn.close()

    now = datetime.now()
    last_claim = row.get('last_daily_claim') if row else None
    streak = row.get('daily_streak', 0) if row else 0

    if last_claim:
        if isinstance(last_claim, str):
            last_claim = datetime.fromisoformat(last_claim)
        
        diff = now - last_claim
        if diff.total_seconds() < 86400:
            remaining = timedelta(seconds=86400) - diff
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            minutes = rem // 60
            await update.message.reply_text(
                f"⏳ Ежедневный бонус можно забирать раз в 24 часа!\nПодождите ещё: **{hours} ч {minutes} мин**\nВаш текущий стрик: **{streak} дней** 🔥",
                parse_mode="Markdown"
            )
            return
        elif diff.total_seconds() > 172800: # Прошло больше 48 часов — сброс стрика
            streak = 0

    streak = (streak % 7) + 1
    conn = get_db()
    c = conn.cursor()

    reward_text = ""
    if streak == 1:
        c.execute("UPDATE users SET balance = balance + 5000, daily_streak = %s, last_daily_claim = %s WHERE user_id = %s", (streak, now, user.id))
        reward_text = "5 000 RPLCoin 💳"
    elif streak == 2:
        c.execute("UPDATE users SET balance = balance + 10000, daily_streak = %s, last_daily_claim = %s WHERE user_id = %s", (streak, now, user.id))
        reward_text = "10 000 RPLCoin 💳"
    elif streak == 3:
        c.execute("UPDATE users SET balance = balance + 15000, daily_streak = %s, last_daily_claim = %s WHERE user_id = %s", (streak, now, user.id))
        reward_text = "15 000 RPLCoin 💳"
    elif streak == 4:
        c.execute("UPDATE users SET daily_streak = %s, last_daily_claim = %s WHERE user_id = %s", (streak, now, user.id))
        reward_text = "Скидку 15% в магазине на любую покупку 🏷"
    elif streak == 5:
        c.execute("UPDATE users SET free_card_cooldown_reset_until = %s, daily_streak = %s, last_daily_claim = %s WHERE user_id = %s", (datetime.max, streak, now, user.id))
        reward_text = "Обнуление КД на бесплатную карточку ✨"
    elif streak == 6:
        c.execute("SELECT * FROM cards WHERE rarity != 'Секретная'")
        all_cds = c.fetchall()
        card = choose_card_for_user(c, user.id, all_cds)
        if card:
            c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (user.id, card['id']))
            reward_text = f"Любая карточка: **{card['nickname']}** ({card['ovr']} OVR) [{card['rarity']}] 🃏"
        else:
            reward_text = "50 000 RPLCoin (нет карт в базе) 💳"
            c.execute("UPDATE users SET balance = balance + 50000 WHERE user_id = %s", (user.id,))
        c.execute("UPDATE users SET daily_streak = %s, last_daily_claim = %s WHERE user_id = %s", (streak, now, user.id))
    elif streak == 7:
        c.execute("SELECT * FROM cards WHERE rarity IN ('Эпическая', 'Мифическая')")
        epic_mythic = c.fetchall()
        card = choose_card_for_user(c, user.id, epic_mythic) if epic_mythic else None
        if card:
            c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (user.id, card['id']))
            reward_text = f"Эпическая/Мифическая карточка: **{card['nickname']}** ({card['ovr']} OVR) [{card['rarity']}] 🌟"
        else:
            c.execute("UPDATE users SET balance = balance + 100000 WHERE user_id = %s", (user.id,))
            reward_text = "100 000 RPLCoin 💳"
        c.execute("UPDATE users SET daily_streak = %s, last_daily_claim = %s WHERE user_id = %s", (streak, now, user.id))

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"🎁 **Ежедневный бонус за день {streak}/7 успешно получен!**\nВы получили: {reward_text}\n\nВозвращайтесь завтра за новым бонусом!",
        parse_mode="Markdown"
    )

# ---------- КОЛЕСО УДАЧИ (/wheel) ----------
async def wheel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    user = update.effective_user
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT balance, last_wheel_spin FROM users WHERE user_id = %s", (user.id,))
    u_row = c.fetchone()
    conn.close()

    if not u_row:
        return

    last_spin = u_row['last_wheel_spin']
    now = datetime.now()
    if last_spin:
        if isinstance(last_spin, str):
            last_spin = datetime.fromisoformat(last_spin)
        diff = now - last_spin
        if diff.total_seconds() < 129600: # 36 часов
            rem = timedelta(seconds=129600) - diff
            hours, rem_sec = divmod(int(rem.total_seconds()), 3600)
            minutes = rem_sec // 60
            await update.message.reply_text(f"⏳ Колесо удачи можно крутить раз в **36 часов**!\nПодождите ещё: **{hours} ч {minutes} мин**", parse_mode="Markdown")
            return

    cost = 10000
    if u_row['balance'] < cost:
        await update.message.reply_text(f"❌ Недостаточно средств! Прокрутка колеса стоит **{cost} RPLCoin**.", parse_mode="Markdown")
        return

    # Задержка 5 секунд и сообщение об открытии
    msg = await update.message.reply_text("🎡 **Идет открытие колеса удачи...** ⏳", parse_mode="Markdown")
    await asyncio.sleep(5)

    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=msg.message_id)
    except Exception:
        pass

    prizes = [
        "reset_cd",
        "money",
        "card_50_65",
        "card_70_80",
        "card_80_85",
        "discount",
        "custom_card",
        "rare",
        "very_rare",
        "epic",
        "mythic",
        "nothing"
    ]
    prize = random.choice(prizes)

    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance - %s, last_wheel_spin = %s WHERE user_id = %s", (cost, now, user.id))

    prize_text = ""
    if prize == "reset_cd":
        c.execute("UPDATE users SET free_card_cooldown_reset_until = %s WHERE user_id = %s", (datetime.max, user.id))
        prize_text = "✨ **Обнуление КД на выпадение бесплатной карты!**"
    elif prize == "money":
        amount = random.randint(1000, 150000)
        c.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (amount, user.id))
        prize_text = f"💵 **Денежный приз:** +{amount} RPLCoin!"
    elif prize == "card_50_65":
        c.execute("SELECT * FROM cards WHERE ovr BETWEEN 50 AND 65")
        cds = c.fetchall()
        card = choose_card_for_user(c, user.id, cds)
        if card:
            c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (user.id, card['id']))
            prize_text = f"🃏 **Карточка (50-65 OVR):** {card['nickname']} ({card['ovr']} OVR)"
        else:
            c.execute("UPDATE users SET balance = balance + 20000 WHERE user_id = %s", (user.id,))
            prize_text = "💵 Карточек 50-65 не найдено, зачислено 20 000 RPLCoin!"
    elif prize == "card_70_80":
        c.execute("SELECT * FROM cards WHERE ovr BETWEEN 70 AND 80")
        cds = c.fetchall()
        card = choose_card_for_user(c, user.id, cds)
        if card:
            c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (user.id, card['id']))
            prize_text = f"🃏 **Карточка (70-80 OVR):** {card['nickname']} ({card['ovr']} OVR)"
        else:
            c.execute("UPDATE users SET balance = balance + 40000 WHERE user_id = %s", (user.id,))
            prize_text = "💵 Карточек 70-80 не найдено, зачислено 40 000 RPLCoin!"
    elif prize == "card_80_85":
        c.execute("SELECT * FROM cards WHERE ovr BETWEEN 80 AND 85")
        cds = c.fetchall()
        card = choose_card_for_user(c, user.id, cds)
        if card:
            c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (user.id, card['id']))
            prize_text = f"🃏 **Карточка (80-85 OVR):** {card['nickname']} ({card['ovr']} OVR)"
        else:
            c.execute("UPDATE users SET balance = balance + 70000 WHERE user_id = %s", (user.id,))
            prize_text = "💵 Карточек 80-85 не найдено, зачислено 70 000 RPLCoin!"
    elif prize == "discount":
        disc = random.randint(1, 30)
        prize_text = f"🏷 **Скидка {disc}%** на любую покупку в магазине!"
    elif prize == "custom_card":
        prize_text = "🎨 **Создание своей карты с рейтингом до 82!** Свяжитесь с администратором @admin для создания."
    elif prize == "rare":
        c.execute("SELECT * FROM cards WHERE rarity = 'Редкая'")
        cds = c.fetchall()
        card = choose_card_for_user(c, user.id, cds)
        if card:
            c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (user.id, card['id']))
            prize_text = f"🌟 **Карта редкости Редкий:** {card['nickname']} ({card['ovr']} OVR)"
        else:
            prize_text = "🌟 Карта редкости Редкий (компенсация 10000 RPLCoin)"
            c.execute("UPDATE users SET balance = balance + 10000 WHERE user_id = %s", (user.id,))
    elif prize == "very_rare":
        c.execute("SELECT * FROM cards WHERE rarity = 'Очень редкая'")
        cds = c.fetchall()
        card = choose_card_for_user(c, user.id, cds)
        if card:
            c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (user.id, card['id']))
            prize_text = f"🌟 **Карта редкости Очень Редкий:** {card['nickname']} ({card['ovr']} OVR)"
        else:
            prize_text = "🌟 Очень Редкий (компенсация 20000 RPLCoin)"
            c.execute("UPDATE users SET balance = balance + 20000 WHERE user_id = %s", (user.id,))
    elif prize == "epic":
        c.execute("SELECT * FROM cards WHERE rarity = 'Эпическая'")
        cds = c.fetchall()
        card = choose_card_for_user(c, user.id, cds)
        if card:
            c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (user.id, card['id']))
            prize_text = f"🌟 **Карта редкости Эпический:** {card['nickname']} ({card['ovr']} OVR)"
        else:
            prize_text = "🌟 Эпический (компенсация 40000 RPLCoin)"
            c.execute("UPDATE users SET balance = balance + 40000 WHERE user_id = %s", (user.id,))
    elif prize == "mythic":
        c.execute("SELECT * FROM cards WHERE rarity = 'Мифическая'")
        cds = c.fetchall()
        card = choose_card_for_user(c, user.id, cds)
        if card:
            c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (user.id, card['id']))
            prize_text = f"🌟 **Карта редкости Мифический:** {card['nickname']} ({card['ovr']} OVR)"
        else:
            prize_text = "🌟 Мифический (компенсация 80000 RPLCoin)"
            c.execute("UPDATE users SET balance = balance + 80000 WHERE user_id = %s", (user.id,))
    else:
        prize_text = "💨 **Ничего!** Повезет в следующий раз."

    conn.commit()
    conn.close()

    await update.message.reply_text(
        f"🎡 **Колесо удачи прокручено!**\n\n{prize_text}",
        parse_mode="Markdown"
    )

# ---------- МИНИ-ИГРА КАМЕНЬ-НОЖНИЦЫ-БУМАГА (/rps) ----------
async def rps_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return
    await update.message.reply_text("🎮 **Камень - Ножницы - Бумага**\nВведите ставку в RPLCoin (целое число):", parse_mode="Markdown")
    return WAITING_RPS_BET

async def rps_receive_bet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        bet = int(update.message.text.strip())
        if bet <= 0:
            await update.message.reply_text("❌ Ставка должна быть больше 0!")
            return WAITING_RPS_BET

        user = update.effective_user
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id = %s", (user.id,))
        u_bal = c.fetchone()['balance']
        conn.close()

        if bet > u_bal:
            await update.message.reply_text(f"❌ Недостаточно средств! Ваш баланс: {u_bal} RPLCoin.")
            return WAITING_RPS_BET

        context.user_data["rps_bet"] = bet
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🪨 Камень", callback_data="rps_rock"),
             InlineKeyboardButton("✂️ Ножницы", callback_data="rps_scissors"),
             InlineKeyboardButton("📄 Бумага", callback_data="rps_paper")]
        ])
        await update.message.reply_text(f"✅ Ставка принята: **{bet} RPLCoin**.\nВыберите ваш ход:", reply_markup=kb, parse_mode="Markdown")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Введите ставку числом!")
        return WAITING_RPS_BET

async def rps_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data

    if not data.startswith("rps_"):
        return

    await query.answer()
    player_choice = data.replace("rps_", "")
    bet = context.user_data.get("rps_bet", 100)

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT balance FROM users WHERE user_id = %s", (user.id,))
    u_bal = c.fetchone()['balance']

    if bet > u_bal:
        conn.close()
        await query.message.edit_text("❌ Ошибка: недостаточно средств для выплаты ставки.")
        return

    choices = ["rock", "scissors", "paper"]
    # шансы 50% на победу игрока
    rand_val = random.random()
    if rand_val < 0.5:
        # Победа игрока
        if player_choice == "rock": bot_choice = "scissors"
        elif player_choice == "scissors": bot_choice = "paper"
        else: bot_choice = "rock"
        result = "win"
    elif rand_val < 0.75:
        # Ничья
        bot_choice = player_choice
        result = "draw"
    else:
        # Проигрыш
        if player_choice == "rock": bot_choice = "paper"
        elif player_choice == "scissors": bot_choice = "rock"
        else: bot_choice = "scissors"
        result = "lose"

    emojis = {"rock": "🪨 Камень", "scissors": "✂️ Ножницы", "paper": "📄 Бумага"}

    if result == "win":
        c.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (bet, user.id))
        res_str = f"🎉 **ПОБЕДА!** Вы выиграли **{bet} RPLCoin**!"
    elif result == "lose":
        c.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (bet, user.id))
        res_str = f"❌ **ПОРАЖЕНИЕ!** Вы потеряли **{bet} RPLCoin**."
    else:
        res_str = "🤝 **НИЧЬЯ!** Ставка возвращена на баланс."

    conn.commit()
    conn.close()

    text = (
        f"🎮 **Результат игры КНБ:**\n\n"
        f"👤 Ваш выбор: {emojis[player_choice]}\n"
        f"🤖 Выбор бота: {emojis[bot_choice]}\n\n"
        f"{res_str}"
    )
    await query.message.edit_text(text, parse_mode="Markdown")

# ---------- ПРОФИЛЬ И СОСТАВ С ДОП ПОЛЯМИ (/profile И /checkprofile) ----------
async def checkprofile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    args = context.args
    if not args:
        await update.message.reply_text("🔍 **Введите команду с указанием ID или username игрока:**\nПример: `/checkprofile @username` или `/checkprofile 123456789`", parse_mode="Markdown")
        return

    target_str = args[0].replace("@", "")
    conn = get_db()
    c = conn.cursor()

    if target_str.isdigit():
        c.execute("SELECT * FROM users WHERE user_id = %s", (int(target_str),))
    else:
        c.execute("SELECT * FROM users WHERE username = %s", (target_str,))

    target_user = c.fetchone()
    if not target_user:
        conn.close()
        await update.message.reply_text("❌ Пользователь не найден в базе данных!")
        return

    target_id = target_user['user_id']
    c.execute("SELECT * FROM user_rosters WHERE user_id = %s", (target_id,))
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

    # Лучший Skater и Goalie
    c.execute('''
        SELECT c.nickname, c.ovr, c.position 
        FROM user_cards uc JOIN cards c ON uc.card_id = c.id 
        WHERE uc.user_id = %s AND c.position = 'Skater' AND uc.count > 0 
        ORDER BY c.ovr DESC LIMIT 1
    ''', (target_id,))
    best_skater = c.fetchone()

    c.execute('''
        SELECT c.nickname, c.ovr, c.position 
        FROM user_cards uc JOIN cards c ON uc.card_id = c.id 
        WHERE uc.user_id = %s AND c.position = 'Goalie' AND uc.count > 0 
        ORDER BY c.ovr DESC LIMIT 1
    ''', (target_id,))
    best_goalie = c.fetchone()
    conn.close()

    avg_ovr = round(total_ovr / 5, 1) if count_filled == 5 else 0
    best_skater_str = f"**{best_skater['nickname']}** ({best_skater['ovr']} OVR)" if best_skater else "Отсутствует"
    best_goalie_str = f"**{best_goalie['nickname']}** ({best_goalie['ovr']} OVR)" if best_goalie else "Отсутствует"

    text = (
        f"🏒 **Профиль игрока {target_user['first_name'] or target_user['username']}:**\n\n"
        f"💳 Баланс: **{target_user['balance']} RPLCoin**\n"
        f"🏆 Рейтинг MMR: **{target_user['mmr']}**\n"
        f"⭐ Средний OVR Состава: **{avg_ovr if avg_ovr > 0 else 'Состав не собран'}**\n\n"
        f"🏒 Лучший Skater: {best_skater_str}\n"
        f"🧤 Лучший Goalie: {best_goalie_str}\n\n"
        f"📋 **Текущий Состав:**\n"
        f"🧤 Вратарь: {roster_info.get('goalie')}\n"
        f"🏒 Полевой 1: {roster_info.get('skater1')}\n"
        f"🏒 Полевой 2: {roster_info.get('skater2')}\n"
        f"🏒 Полевой 3: {roster_info.get('skater3')}\n"
        f"🏒 Полевой 4: {roster_info.get('skater4')}\n"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Написать в ЛС", url=f"https://t.me/{target_user['username']}" if target_user['username'] else f"tg://user?id={target_id}")],
        [InlineKeyboardButton("🔙 Назад", callback_data="refresh_profile")]
    ])

    await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

# ЛОГИКА КАРТОЧЕК И ВЫДАЧИ (/rplcards / Кнопка) С КОЛЛЕКЦИЕЙ
async def rplcards_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    user = update.effective_user
    u_data = get_or_create_user(user.id, user.username, user.first_name)
    now = datetime.now()
    
    last_claim = u_data.get('last_card_claim')
    cooldown_reset = u_data.get('free_card_cooldown_reset_until')
    bypassed = False
    if cooldown_reset:
        if isinstance(cooldown_reset, str):
            cooldown_reset = datetime.fromisoformat(cooldown_reset)
        if now < cooldown_reset:
            bypassed = True

    if not bypassed and last_claim:
        if isinstance(last_claim, str):
            last_claim = datetime.fromisoformat(last_claim)
        if now < last_claim + timedelta(hours=8):
            wait = (last_claim + timedelta(hours=8)) - now
            hours, rem = divmod(wait.seconds, 3600)
            minutes = rem // 60
            await update.message.reply_text(f"⏳ Бесплатную карточку можно получать раз в **8 часов**!\nПодожди ещё: **{hours} ч {minutes} мин**", parse_mode="Markdown")
            return

    temp_msg = await update.message.reply_text("⏳ **Идет открытие карточки...**", parse_mode="Markdown")
    await asyncio.sleep(3)

    try:
        await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=temp_msg.message_id)
    except Exception:
        pass

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
        await update.message.reply_text("📭 В базе пока нет карточек! Администратор скоро их добавит.")
        return

    card = choose_card_for_user(c, user.id, cards)
    card_id = card['id']

    c.execute('''
        INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1)
        ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1
    ''', (user.id, card_id))
    
    if bypassed:
        c.execute("UPDATE users SET free_card_cooldown_reset_until = NULL WHERE user_id = %s", (user.id,))
    else:
        c.execute("UPDATE users SET last_card_claim = %s WHERE user_id = %s", (now, user.id))
        
    conn.commit()
    conn.close()

    team_str = f"{card['team_emoji'] or '🏒'} {card['team_name']}" if card['team_name'] else "Без команды"
    
    caption = (
        f"🔥 **Вам выпала карточка!**\n\n"
        f"┏━━━━━━━━━━━━━━━━━━━━┓\n"
        f"┃ 👤 {card['nickname']}\n"
        f"┃ 📁 Коллекция: {card['collection_name']}\n"
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
        text += "У вас пока нет карточек! Получите бесплатную или купите пак в /shop."
    else:
        mythic_counts = {}
        for uc in user_cards:
            t_str = f"{uc['team_emoji']} {uc['team_name']}" if uc['team_name'] else ""
            text += f"ID `{uc['id']}` | **{uc['nickname']}** ({uc['position']}, {uc['ovr']} OVR) — `x{uc['count']}` [{uc['rarity']}] | 📁 {uc['col_name']} {t_str}\n"
            
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

        buttons.append([InlineKeyboardButton("🏷 Выставить на Рынок", callback_data="market_list_menu")])
        buttons.append([InlineKeyboardButton("💰 Продать карточки (системе)", callback_data="sell_menu")])

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

    text = "💰 **Продажа карточек системе:**\nНажмите на карточку, чтобы продать 1 шт.\n\n"
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

# ---------- ТОРГОВАЯ ПЛОЩАДКА (/cardshop) - ИСПРАВЛЕНА ----------
async def cardshop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)
    await show_market(update, context)

async def show_market(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user if query else update.effective_user

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT m.id as market_id, m.price, m.seller_id, c.id as card_id, c.nickname, c.position, c.ovr, c.rarity, u.username, u.first_name
        FROM market m
        JOIN cards c ON m.card_id = c.id
        JOIN users u ON m.seller_id = u.user_id
        ORDER BY m.id DESC
        LIMIT 25
    ''')
    items = c.fetchall()

    c.execute("SELECT COUNT(*) as cnt FROM market WHERE seller_id = %s", (user.id,))
    my_cnt = c.fetchone()['cnt']
    conn.close()

    text = "🛒 **ТОРГОВАЯ ПЛОЩАДКА (РЫНОК):**\nЗдесь игроки продают и покупают карточки друг у друга!\n\n"
    buttons = []

    if not items:
        text += "📭 На рынке сейчас нет выставленных лотов."
    else:
        for item in items:
            seller_name = f"@{item['username']}" if item['username'] else item['first_name']
            text += f"🏷 **#{item['market_id']}** | **{item['nickname']}** ({item['position']}, {item['ovr']} OVR) [{item['rarity']}] — **{item['price']} RPLCoin** (Продавец: {seller_name})\n"
            if item['seller_id'] != user.id:
                buttons.append([InlineKeyboardButton(f"Купить #{item['market_id']} ({item['nickname']}) - {item['price']} RPLCoin", callback_data=f"buy_market_{item['market_id']}")])

    nav_btns = []
    if my_cnt > 0:
        nav_btns.append(InlineKeyboardButton(f"📦 Мои лоты ({my_cnt})", callback_data="my_market_items"))
    nav_btns.append(InlineKeyboardButton("🔄 Обновить", callback_data="refresh_market"))
    
    buttons.append(nav_btns)

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

async def show_my_market_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT m.id as market_id, m.price, c.nickname, c.position, c.ovr, c.rarity
        FROM market m
        JOIN cards c ON m.card_id = c.id
        WHERE m.seller_id = %s
        ORDER BY m.id DESC
    ''', (user.id,))
    my_items = c.fetchall()
    conn.close()

    text = "📦 **Ваши выставленные карточки на рынке:**\n\n"
    buttons = []

    if not my_items:
        text += "У вас нет активных объявлений на рынке."
    else:
        for item in my_items:
            text += f"🏷 **#{item['market_id']}** | **{item['nickname']}** ({item['ovr']} OVR) — `{item['price']} RPLCoin`\n"
            buttons.append([InlineKeyboardButton(f"❌ Снять #{item['market_id']} ({item['nickname']})", callback_data=f"cancel_market_{item['market_id']}")])

    buttons.append([InlineKeyboardButton("🔙 Назад на рынок", callback_data="refresh_market")])

    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def market_start_list_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user

    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT uc.count, c.id, c.nickname, c.ovr, c.position, c.rarity
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        WHERE uc.user_id = %s AND uc.count > 0
        ORDER BY c.ovr DESC
    ''', (user.id,))
    user_cards = c.fetchall()
    conn.close()

    if not user_cards:
        await query.answer("У вас нет карточек в инвентаре!", show_alert=True)
        return

    text = "🏷 **Выставить карточку на Торговую площадку:**\nВыберите карточку, которую хотите выставить на продажу:"
    buttons = []
    for uc in user_cards:
        buttons.append([InlineKeyboardButton(f"{uc['nickname']} ({uc['ovr']} OVR) - x{uc['count']}", callback_data=f"select_mcard_{uc['id']}")])

    buttons.append([InlineKeyboardButton("🔙 Назад в инвентарь", callback_data="refresh_inv")])
    await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

async def market_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    query = update.callback_query
    user = query.from_user
    data = query.data

    if data == "refresh_market":
        await show_market(update, context)

    elif data == "my_market_items":
        await show_my_market_items(update, context)

    elif data == "market_list_menu":
        await market_start_list_card(update, context)

    elif data.startswith("select_mcard_"):
        card_id = int(data.split("_")[2])
        context.user_data["m_card_id"] = card_id
        await query.message.reply_text("💲 **Введите цену продажи (в RPLCoin, максимум 999 999):**\nНапример: `1500`", parse_mode="Markdown")
        return WAITING_MARKET_PRICE_INPUT

    elif data.startswith("cancel_market_"):
        market_id = int(data.split("_")[2])
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT * FROM market WHERE id = %s AND seller_id = %s", (market_id, user.id))
        item = c.fetchone()

        if not item:
            conn.close()
            await query.answer("❌ Лот не найден или уже продан!", show_alert=True)
            await show_my_market_items(update, context)
            return

        c.execute('''
            INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1)
            ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1
        ''', (user.id, item['card_id']))
        c.execute("DELETE FROM market WHERE id = %s", (market_id,))

        conn.commit()
        conn.close()

        await query.answer("✅ Карточка снята с продажи и возвращена в инвентарь!", show_alert=True)
        await show_my_market_items(update, context)

    elif data.startswith("buy_market_"):
        market_id = int(data.split("_")[2])
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT * FROM market WHERE id = %s", (market_id,))
        item = c.fetchone()

        if not item:
            conn.close()
            await query.answer("❌ Этот лот уже продан или снят!", show_alert=True)
            await show_market(update, context)
            return

        if item['seller_id'] == user.id:
            conn.close()
            await query.answer("❌ Вы не можете купить собственный лот!", show_alert=True)
            return

        c.execute("SELECT balance FROM users WHERE user_id = %s", (user.id,))
        buyer_bal = c.fetchone()['balance']

        if buyer_bal < item['price']:
            conn.close()
            await query.answer("❌ Недостаточно RPLCoin для покупки!", show_alert=True)
            return

        c.execute("UPDATE users SET balance = balance - %s WHERE user_id = %s", (item['price'], user.id))
        c.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (item['price'], item['seller_id']))

        c.execute('''
            INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1)
            ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1
        ''', (user.id, item['card_id']))

        c.execute("DELETE FROM market WHERE id = %s", (market_id,))

        conn.commit()
        conn.close()

        try:
            await context.bot.send_message(chat_id=item['seller_id'], text=f"🎉 Ваш лот на рынке куплен! Зачислено **{item['price']} RPLCoin**.", parse_mode="Markdown")
        except Exception:
            pass

        await query.answer("🎉 Вы успешно купили карточку с рынка!", show_alert=True)
        await show_market(update, context)

async def execute_market_list_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = int(update.message.text.strip())
        if price <= 0 or price > 999999:
            await update.message.reply_text("❌ Цена должна быть от 1 до 999 999 RPLCoin! Попробуйте снова:")
            return WAITING_MARKET_PRICE_INPUT

        card_id = context.user_data.get("m_card_id")
        user = update.effective_user

        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT count FROM user_cards WHERE user_id = %s AND card_id = %s AND count > 0", (user.id, card_id))
        row = c.fetchone()

        if not row:
            conn.close()
            await update.message.reply_text("❌ У вас больше нет этой карточки!")
            return ConversationHandler.END

        c.execute("UPDATE user_cards SET count = count - 1 WHERE user_id = %s AND card_id = %s", (user.id, card_id))
        c.execute("DELETE FROM user_cards WHERE user_id = %s AND card_id = %s AND count <= 0", (user.id, card_id))

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

        c.execute("INSERT INTO market (seller_id, card_id, price) VALUES (%s, %s, %s)", (user.id, card_id, price))

        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ **Карточка успешно выставляется за {price} RPLCoin на Торговую площадку!**", parse_mode="Markdown")
        await show_market(update, context)
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("❌ Введите цену целым числом (до 999 999):")
        return WAITING_MARKET_PRICE_INPUT

# ---------- СИСТЕМА ТРЕЙДА (/trade) ----------
active_trades = {}

async def trade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    user = update.effective_user
    args = context.args

    if not args:
        await update.message.reply_text("🤝 **Введи команду с указанием никнейма или ID:**\nПример: `/trade @username` или `/trade 123456789`", parse_mode="Markdown")
        return

    target_str = args[0].replace("@", "")
    conn = get_db()
    c = conn.cursor()

    if target_str.isdigit():
        c.execute("SELECT * FROM users WHERE user_id = %s", (int(target_str),))
    else:
        c.execute("SELECT * FROM users WHERE username = %s", (target_str,))

    target_user = c.fetchone()
    conn.close()

    if not target_user:
        await update.message.reply_text("❌ Игрок не найден в базе данных бота!")
        return

    if target_user['user_id'] == user.id:
        await update.message.reply_text("❌ Вы не можете отправить предложение трейда самому себе!")
        return

    target_id = target_user['user_id']

    for tid, tdata in active_trades.items():
        if user.id in (tdata['p1'], tdata['p2']) or target_id in (tdata['p1'], tdata['p2']):
            await update.message.reply_text("❌ Один из игроков уже находится в активном трейде!")
            return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Принять Трейд", callback_data=f"accept_trade_{user.id}_{target_id}")],
        [InlineKeyboardButton("❌ Отклонить", callback_data=f"decline_trade_{user.id}_{target_id}")]
    ])

    await update.message.reply_text(f"🤝 Вы отправили предложение обмена игроку **{target_user['first_name']}**! Ожидание ответа...", parse_mode="Markdown")

    try:
        await context.bot.send_message(
            chat_id=target_id,
            text=f"🤝 **Игрок {user.first_name} (@{user.username or user.id}) предлагает вам обмен (трейд)!**\nХотите принять предложение?",
            reply_markup=kb,
            parse_mode="Markdown"
        )
    except Exception:
        await update.message.reply_text("❌ Не удалось отправить уведомление игроку (возможно, бот заблокирован им).")

async def render_trade_text(tdata, for_user_id):
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT first_name, username FROM users WHERE user_id = %s", (tdata['p1'],))
    u1 = c.fetchone()
    c.execute("SELECT first_name, username FROM users WHERE user_id = %s", (tdata['p2'],))
    u2 = c.fetchone()

    name1 = u1['first_name'] if u1 else str(tdata['p1'])
    name2 = u2['first_name'] if u2 else str(tdata['p2'])

    p1_cards_str = ""
    if tdata['p1_cards']:
        c.execute("SELECT id, nickname, ovr, position FROM cards WHERE id IN %s", (tuple(tdata['p1_cards']),))
        cds = c.fetchall()
        for cd in cds:
            p1_cards_str += f"  • {cd['nickname']} ({cd['ovr']} OVR)\n"
    else:
        p1_cards_str = "  *(карточки не выбраны)*\n"

    p2_cards_str = ""
    if tdata['p2_cards']:
        c.execute("SELECT id, nickname, ovr, position FROM cards WHERE id IN %s", (tuple(tdata['p2_cards']),))
        cds = c.fetchall()
        for cd in cds:
            p2_cards_str += f"  • {cd['nickname']} ({cd['ovr']} OVR)\n"
    else:
        p2_cards_str = "  *(карточки не выбраны)*\n"

    conn.close()

    r1_status = "✅ ГОТОВ" if tdata['p1_ready'] else "⏳ Выбирает..."
    r2_status = "✅ ГОТОВ" if tdata['p2_ready'] else "⏳ Выбирает..."

    text = (
        f"🤝 **ОКНО ОБМЕНА (ТРЕЙД)**\n\n"
        f"🔴 **Предложение {name1}** [{r1_status}]:\n"
        f"💳 RPLCoin: **{tdata['p1_money']}**\n"
        f"🃏 Карточки:\n{p1_cards_str}\n"
        f"────────────────────\n"
        f"🔵 **Предложение {name2}** [{r2_status}]:\n"
        f"💳 RPLCoin: **{tdata['p2_money']}**\n"
        f"🃏 Карточки:\n{p2_cards_str}\n"
    )
    return text

async def update_trade_views(context, trade_id):
    if trade_id not in active_trades:
        return
    tdata = active_trades[trade_id]

    p1, p2 = tdata['p1'], tdata['p2']
    m1, m2 = tdata['msgs'].get(p1), tdata['msgs'].get(p2)

    txt = await render_trade_text(tdata, p1)

    p1_ready_btn = InlineKeyboardButton("❌ Снять готовность" if tdata['p1_ready'] else "✅ ПОДТВЕРДИТЬ ГОТОВНОСТЬ", callback_data=f"tr_ready_{trade_id}")
    kb1 = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить карту", callback_data=f"tr_addcard_{trade_id}"), InlineKeyboardButton("💵 Изменить RPLCoin", callback_data=f"tr_addmoney_{trade_id}")],
        [InlineKeyboardButton("🗑 Очистить предложение", callback_data=f"tr_clear_{trade_id}")],
        [p1_ready_btn],
        [InlineKeyboardButton("🚫 Отменить Трейд", callback_data=f"tr_cancel_{trade_id}")]
    ])

    p2_ready_btn = InlineKeyboardButton("❌ Снять готовность" if tdata['p2_ready'] else "✅ ПОДТВЕРДИТЬ ГОТОВНОСТЬ", callback_data=f"tr_ready_{trade_id}")
    kb2 = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Добавить карту", callback_data=f"tr_addcard_{trade_id}"), InlineKeyboardButton("💵 Изменить RPLCoin", callback_data=f"tr_addmoney_{trade_id}")],
        [InlineKeyboardButton("🗑 Очистить предложение", callback_data=f"tr_clear_{trade_id}")],
        [p2_ready_btn],
        [InlineKeyboardButton("🚫 Отменить Трейд", callback_data=f"tr_cancel_{trade_id}")]
    ])

    if m1:
        try:
            await context.bot.edit_message_text(chat_id=p1, message_id=m1, text=txt, reply_markup=kb1, parse_mode="Markdown")
        except Exception:
            pass
    if m2:
        try:
            await context.bot.edit_message_text(chat_id=p2, message_id=m2, text=txt, reply_markup=kb2, parse_mode="Markdown")
        except Exception:
            pass

async def trade_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = query.from_user
    data = query.data

    if data.startswith("accept_trade_"):
        parts = data.split("_")
        p1 = int(parts[2])
        p2 = int(parts[3])

        if user.id != p2:
            await query.answer("❌ Этот запрос отправлен не вам!", show_alert=True)
            return

        trade_id = f"{p1}_{p2}_{int(time.time())}"
        active_trades[trade_id] = {
            "p1": p1, "p2": p2,
            "p1_cards": [], "p2_cards": [],
            "p1_money": 0, "p2_money": 0,
            "p1_ready": False, "p2_ready": False,
            "msgs": {}
        }

        m1 = await context.bot.send_message(chat_id=p1, text="🤝 **Трейд принят! Загрузка...**", parse_mode="Markdown")
        m2 = await query.message.edit_text("🤝 **Трейд начат! Загрузка...**", parse_mode="Markdown")

        active_trades[trade_id]["msgs"][p1] = m1.message_id
        active_trades[trade_id]["msgs"][p2] = m2.message_id

        await update_trade_views(context, trade_id)

    elif data.startswith("decline_trade_"):
        parts = data.split("_")
        p1 = int(parts[2])
        await query.edit_message_text("❌ Предложение трейда отклонено.")
        try:
            await context.bot.send_message(chat_id=p1, text="❌ Игрок отклонил ваше предложение обмена.")
        except Exception:
            pass

    elif data.startswith("tr_"):
        parts = data.split("_")
        action = parts[1]
        trade_id = "_".join(parts[2:])

        if trade_id not in active_trades:
            await query.answer("❌ Этот трейд больше не активен!", show_alert=True)
            return

        tdata = active_trades[trade_id]
        if user.id not in (tdata['p1'], tdata['p2']):
            await query.answer("❌ Вы не участвуете в этом трейде!", show_alert=True)
            return

        is_p1 = (user.id == tdata['p1'])

        if action == "cancel":
            del active_trades[trade_id]
            for uid, mid in tdata['msgs'].items():
                try:
                    await context.bot.edit_message_text(chat_id=uid, message_id=mid, text="🚫 **Трейд отменен одной из сторон.**", parse_mode="Markdown")
                except Exception:
                    pass
            return

        elif action == "clear":
            if is_p1:
                tdata['p1_cards'] = []
                tdata['p1_money'] = 0
            else:
                tdata['p2_cards'] = []
                tdata['p2_money'] = 0
            
            tdata['p1_ready'] = False
            tdata['p2_ready'] = False
            await query.answer("Предложение очищено!")
            await update_trade_views(context, trade_id)

        elif action == "addmoney":
            context.user_data["active_trade_id"] = trade_id
            await query.message.reply_text("💵 **Введите сумму RPLCoin для трейда:**", parse_mode="Markdown")
            return WAITING_TRADE_MONEY

        elif action == "addcard":
            conn = get_db()
            c = conn.cursor()
            c.execute('''
                SELECT uc.card_id, uc.count, c.nickname, c.ovr, c.position
                FROM user_cards uc
                JOIN cards c ON uc.card_id = c.id
                WHERE uc.user_id = %s AND uc.count > 0
                ORDER BY c.ovr DESC
            ''', (user.id,))
            user_cards = c.fetchall()
            conn.close()

            if not user_cards:
                await query.answer("У вас нет доступных карточек для обмена!", show_alert=True)
                return

            buttons = []
            curr_cards = tdata['p1_cards'] if is_p1 else tdata['p2_cards']
            for uc in user_cards:
                cnt_in_tr = curr_cards.count(uc['card_id'])
                if uc['count'] - cnt_in_tr > 0:
                    buttons.append([InlineKeyboardButton(f"{uc['nickname']} ({uc['ovr']} OVR)", callback_data=f"tr_putcard_{trade_id}_{uc['card_id']}")])

            buttons.append([InlineKeyboardButton("🔙 Назад", callback_data=f"tr_back_{trade_id}")])
            await query.edit_message_text("📋 **Выберите карточку для добавления в предложении:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

        elif action == "putcard":
            card_id = int(parts[3])
            tr_id = "_".join(parts[2:-1])
            tdata = active_trades.get(tr_id)
            if not tdata:
                return

            if is_p1:
                tdata['p1_cards'].append(card_id)
            else:
                tdata['p2_cards'].append(card_id)

            tdata['p1_ready'] = False
            tdata['p2_ready'] = False
            await update_trade_views(context, tr_id)

        elif action == "back":
            await update_trade_views(context, trade_id)

        elif action == "ready":
            if is_p1:
                tdata['p1_ready'] = not tdata['p1_ready']
            else:
                tdata['p2_ready'] = not tdata['p2_ready']

            await update_trade_views(context, trade_id)

            if tdata['p1_ready'] and tdata['p2_ready']:
                await execute_trade_finish(context, trade_id)

async def execute_trade_finish(context, trade_id):
    if trade_id not in active_trades:
        return
    tdata = active_trades.pop(trade_id)

    p1, p2 = tdata['p1'], tdata['p2']
    c1, c2 = tdata['p1_cards'], tdata['p2_cards']
    m1, m2 = tdata['p1_money'], tdata['p2_money']

    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT balance FROM users WHERE user_id = %s", (p1,))
    b1 = c.fetchone()['balance']
    c.execute("SELECT balance FROM users WHERE user_id = %s", (p2,))
    b2 = c.fetchone()['balance']

    if b1 < m1 or b2 < m2:
        conn.close()
        for uid, mid in tdata['msgs'].items():
            try:
                await context.bot.edit_message_text(chat_id=uid, message_id=mid, text="❌ **Ошибка трейда!** У одного из игроков недостаточно средств.")
            except Exception:
                pass
        return

    c.execute("UPDATE users SET balance = balance - %s + %s WHERE user_id = %s", (m1, m2, p1))
    c.execute("UPDATE users SET balance = balance - %s + %s WHERE user_id = %s", (m2, m1, p2))

    for card_id in c1:
        c.execute("UPDATE user_cards SET count = count - 1 WHERE user_id = %s AND card_id = %s", (p1, card_id))
        c.execute("DELETE FROM user_cards WHERE user_id = %s AND card_id = %s AND count <= 0", (p1, card_id))
        c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (p2, card_id))

    for card_id in c2:
        c.execute("UPDATE user_cards SET count = count - 1 WHERE user_id = %s AND card_id = %s", (p2, card_id))
        c.execute("DELETE FROM user_cards WHERE user_id = %s AND card_id = %s AND count <= 0", (p2, card_id))
        c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (p1, card_id))

    conn.commit()
    conn.close()

    for uid, mid in tdata['msgs'].items():
        try:
            await context.bot.edit_message_text(chat_id=uid, message_id=mid, text="🎉 **ОБМЕН УСПЕШНО ЗАВЕРШЕН!**\nВсе предметы и балансы обновлены.", parse_mode="Markdown")
        except Exception:
            pass

async def execute_trade_money_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text.strip())
        if val < 0:
            val = 0

        user = update.effective_user
        trade_id = context.user_data.get("active_trade_id")

        if not trade_id or trade_id not in active_trades:
            await update.message.reply_text("❌ Трейд не активен!")
            return ConversationHandler.END

        tdata = active_trades[trade_id]
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id = %s", (user.id,))
        u_bal = c.fetchone()['balance']
        conn.close()

        if val > u_bal:
            await update.message.reply_text(f"❌ У вас нет столько RPLCoin! Ваш баланс: {u_bal}")
            return WAITING_TRADE_MONEY

        if user.id == tdata['p1']:
            tdata['p1_money'] = val
        else:
            tdata['p2_money'] = val

        tdata['p1_ready'] = False
        tdata['p2_ready'] = False

        await update.message.reply_text(f"✅ Установлена сумма {val} RPLCoin.")
        await update_trade_views(context, trade_id)
        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text("❌ Введите сумму целым числом!")
        return WAITING_TRADE_MONEY

# ---------- ПРОМОКОДЫ (/promo) ----------
async def promo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return

    user = update.effective_user
    get_or_create_user(user.id, user.username, user.first_name)

    if context.args:
        code = context.args[0].strip().upper()
        await process_promo_code(update, context, user.id, code)
        return

    await update.message.reply_text("🎟 **Введите ваш промокод:**", parse_mode="Markdown")
    return WAITING_PROMO_INPUT

async def promo_input_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code = update.message.text.strip().upper()
    user = update.effective_user
    await process_promo_code(update, context, user.id, code)
    return ConversationHandler.END

async def process_promo_code(update, context, user_id, code):
    conn = get_db()
    c = conn.cursor()

    c.execute("SELECT * FROM promo_codes WHERE code = %s", (code,))
    promo = c.fetchone()

    if not promo:
        conn.close()
        await update.message.reply_text("❌ Введен несуществующий или недействительный промокод!")
        return

    c.execute("SELECT * FROM user_promocodes WHERE user_id = %s AND code = %s", (user_id, code))
    already_used = c.fetchone()

    if already_used:
        conn.close()
        await update.message.reply_text("❌ Вы уже активировали этот промокод!")
        return

    if promo['current_uses'] >= promo['max_uses']:
        conn.close()
        await update.message.reply_text("❌ У этого промокода закончились использования!")
        return

    reward_msg = ""
    if promo['reward_type'] == 'money':
        c.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (promo['reward_value'], user_id))
        reward_msg = f"💳 **+{promo['reward_value']} RPLCoin**"
    elif promo['reward_type'] == 'card':
        c.execute('''
            INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1)
            ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1
        ''', (user_id, promo['reward_value']))
        c.execute("SELECT nickname, ovr FROM cards WHERE id = %s", (promo['reward_value'],))
        cd = c.fetchone()
        cd_name = f"{cd['nickname']} ({cd['ovr']} OVR)" if cd else f"Карточка ID {promo['reward_value']}"
        reward_msg = f"🃏 **{cd_name}**"

    c.execute("UPDATE promo_codes SET current_uses = current_uses + 1 WHERE code = %s", (code,))
    c.execute("INSERT INTO user_promocodes (user_id, code) VALUES (%s, %s)", (user_id, code))

    conn.commit()
    conn.close()

    await update.message.reply_text(f"🎉 **Промокод успешно активирован!**\nВы получили: {reward_msg}", parse_mode="Markdown")

async def admin_create_promo_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎟 **Введите текст нового промокода (например: `RPL2026`):**", parse_mode="Markdown")
    return ADD_PROMO_CODE

async def admin_promo_set_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["p_code"] = update.message.text.strip().upper()
    kb = [["💰 Деньги", "🃏 Карточка"]]
    await update.message.reply_text("🎁 Выберите тип награды:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
    return ADD_PROMO_TYPE

async def admin_promo_set_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t_text = update.message.text.strip()
    r_type = "money" if "Деньги" in t_text else "card"
    context.user_data["p_reward_type"] = r_type

    if r_type == "money":
        await update.message.reply_text("💰 Введите количество денег (RPLCoin):", reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text("🃏 Введите ID выдаваемой карточки:", reply_markup=ReplyKeyboardRemove())

    return ADD_PROMO_VAL

async def admin_promo_set_val(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text.strip())
        context.user_data["p_reward_val"] = val
        await update.message.reply_text("🔢 Введите общее количество активаций (лимит использования):")
        return ADD_PROMO_LIMIT
    except ValueError:
        await update.message.reply_text("❌ Введите число!")
        return ADD_PROMO_VAL

async def admin_promo_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        limit = int(update.message.text.strip())
        code = context.user_data.get("p_code")
        r_type = context.user_data.get("p_reward_type")
        r_val = context.user_data.get("p_reward_val")

        conn = get_db()
        c = conn.cursor()
        c.execute('''
            INSERT INTO promo_codes (code, reward_type, reward_value, max_uses, current_uses)
            VALUES (%s, %s, %s, %s, 0)
            ON CONFLICT (code) DO UPDATE SET reward_type = EXCLUDED.reward_type, reward_value = EXCLUDED.reward_value, max_uses = EXCLUDED.max_uses
        ''', (code, r_type, r_val, limit))
        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ Промокод `{code}` успешно создан на **{limit}** использований!", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
        return CARD_ADMIN_MENU

    except ValueError:
        await update.message.reply_text("❌ Введите число!")
        return ADD_PROMO_LIMIT

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

    # Лучший Skater и Goalie
    c.execute('''
        SELECT c.nickname, c.ovr, c.position 
        FROM user_cards uc JOIN cards c ON uc.card_id = c.id 
        WHERE uc.user_id = %s AND c.position = 'Skater' AND uc.count > 0 
        ORDER BY c.ovr DESC LIMIT 1
    ''', (user.id,))
    best_skater = c.fetchone()

    c.execute('''
        SELECT c.nickname, c.ovr, c.position 
        FROM user_cards uc JOIN cards c ON uc.card_id = c.id 
        WHERE uc.user_id = %s AND c.position = 'Goalie' AND uc.count > 0 
        ORDER BY c.ovr DESC LIMIT 1
    ''', (user.id,))
    best_goalie = c.fetchone()
    conn.close()

    avg_ovr = round(total_ovr / 5, 1) if count_filled == 5 else 0
    best_skater_str = f"**{best_skater['nickname']}** ({best_skater['ovr']} OVR)" if best_skater else "Отсутствует"
    best_goalie_str = f"**{best_goalie['nickname']}** ({best_goalie['ovr']} OVR)" if best_goalie else "Отсутствует"

    text = (
        f"🏒 **Профиль игрока {user.first_name}:**\n\n"
        f"💳 Баланс: **{u_data['balance']} RPLCoin**\n"
        f"🏆 Рейтинг MMR: **{u_data['mmr']}**\n"
        f"⭐ Средний OVR Состава: **{avg_ovr if avg_ovr > 0 else 'Состав не собран'}**\n\n"
        f"🏒 Лучший Skater: {best_skater_str}\n"
        f"🧤 Лучший Goalie: {best_goalie_str}\n\n"
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

# ---------- МАТЧИ И ПОИСК СОПЕРНИКА (/cardmatch) С НАЧИСЛЕНИЕМ 100 RPLCOIN ЗА ГОЛ ----------
active_searches = {}
active_games = set()

def calc_goal_probabilities(p1_cards, p2_cards):
    p1_skater_ovr = sum(p1_cards[f"skater{i}"]["ovr"] for i in range(1, 5)) / 4.0
    p2_skater_ovr = sum(p2_cards[f"skater{i}"]["ovr"] for i in range(1, 5)) / 4.0
    
    g1_ovr = p1_cards["goalie"]["ovr"]
    g2_ovr = p2_cards["goalie"]["ovr"]

    p1_tot_ovr = (p1_skater_ovr * 4.0 + g1_ovr) / 5.0
    p2_tot_ovr = (p2_skater_ovr * 4.0 + g2_ovr) / 5.0

    diff1 = p1_skater_ovr - g2_ovr
    diff2 = p2_skater_ovr - g1_ovr

    if diff1 >= 0:
        prob_p1 = 0.12 * (1.8 ** (diff1 / 7.0))
    else:
        prob_p1 = 0.12 * (0.5 ** (-diff1 / 7.0))

    if diff2 >= 0:
        prob_p2 = 0.12 * (1.8 ** (diff2 / 7.0))
    else:
        prob_p2 = 0.12 * (0.5 ** (-diff2 / 7.0))

    tot_diff = p1_tot_ovr - p2_tot_ovr
    if tot_diff > 10:
        prob_p1 *= 1.5
        prob_p2 *= 0.3
    elif tot_diff < -10:
        prob_p1 *= 0.3
        prob_p2 *= 1.5

    prob_p1 = max(0.005, min(0.45, prob_p1))
    prob_p2 = max(0.005, min(0.45, prob_p2))

    return prob_p1, prob_p2

def calc_shootout_prob(skater_ovr, goalie_ovr):
    diff = skater_ovr - goalie_ovr
    if diff >= 0:
        prob = 0.35 * (1.6 ** (diff / 8.0))
    else:
        prob = 0.35 * (0.5 ** (-diff / 8.0))
    return max(0.05, min(0.85, prob))

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
        f"Нажмите кнопку ниже или начните поиск `/cardmatch`!",
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

        conn_g = get_db()
        c_g = conn_g.cursor()

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
                    
                    # Начисление 100 RPLCoin за гол игроку P1
                    c_g.execute("UPDATE users SET balance = balance + 100 WHERE user_id = %s", (p1_id,))
                    conn_g.commit()

                    assist_str = f" (пас: {assist['nickname']})" if assist else ""
                    evt = f"⚡️ **{minute}' ГОЛ!** {scorer['nickname']}{assist_str} забивает за🔴 {name1}! (+100 RPLCoin) [{score1}:{score2}]"
                    all_events.append(evt)

                elif rand_val < prob_p1 + prob_p2:
                    scorer = random.choice([p2_cards['skater1'], p2_cards['skater2'], p2_cards['skater3'], p2_cards['skater4']])
                    assist_cand = [p for k, p in p2_cards.items() if k != 'goalie' and p['id'] != scorer['id']]
                    assist = random.choice(assist_cand) if assist_cand else None
                    score2 += 1

                    # Начисление 100 RPLCoin за гол игроку P2
                    c_g.execute("UPDATE users SET balance = balance + 100 WHERE user_id = %s", (p2_id,))
                    conn_g.commit()

                    assist_str = f" (пас: {assist['nickname']})" if assist else ""
                    evt = f"⚡️ **{minute}' ГОЛ!** {scorer['nickname']}{assist_str} забивает за🔵 {name2}! (+100 RPLCoin) [{score1}:{score2}]"
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

        conn_g.close()
        await asyncio.sleep(2)

        # ОВЕРТАЙМ
        if score1 == score2:
            conn_ot = get_db()
            c_ot = conn_ot.cursor()
            
            evt_ot_start = "⏳ **ОСНОВНОЕ ВРЕМЯ ЗАВЕРШЕНО СО СЧЕТОМ " + f"{score1}:{score2}! Начинается ОВЕРТАЙМ (5 минут, 3х3)!**"
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
                    c_ot.execute("UPDATE users SET balance = balance + 100 WHERE user_id = %s", (p1_id,))
                    conn_ot.commit()
                    evt = f"🔥 **{ot_min}' ЗОЛОТОЙ ГОЛ В ОВЕРТАЙМЕ!** {scorer['nickname']} приносит победу 🔴 {name1}! (+100 RPLCoin) [{score1}:{score2}]"
                    all_events.append(evt)
                    break
                elif rand_val < ot_prob1 + ot_prob2:
                    scorer = random.choice([p2_cards['skater1'], p2_cards['skater2']])
                    score2 += 1
                    c_ot.execute("UPDATE users SET balance = balance + 100 WHERE user_id = %s", (p2_id,))
                    conn_ot.commit()
                    evt = f"🔥 **{ot_min}' ЗОЛОТОЙ ГОЛ В ОВЕРТАЙМЕ!** {scorer['nickname']} приносит победу 🔵 {name2}! (+100 RPLCoin) [{score1}:{score2}]"
                    all_events.append(evt)
                    break
                else:
                    evt = f"⚡️ **{ot_min}' ОПАСНЕЙШИЙ МОМЕНТ В ОВЕРТАЙМЕ!**"
                    all_events.append(evt)

                recent_events = "\n".join(all_events[-6:])
                status_text = (
                    f"{header}\n"
                    f"📊 **Счет:** 🔴 {score1} — {score2} 🔵\n"
                    f"⏱ **ОВЕРТАЙМ (3х3)**\n"
                    f"📝 **Ход матча:**\n{recent_events}"
                )
                await broadcast_match_text(context, p1_chat_id, p1_msg_id, p2_chat_id, p2_msg_id, status_text)
                await asyncio.sleep(3.5)
            conn_ot.close()

        await asyncio.sleep(2)

        # СЕРИЯ БУЛЛИТОВ
        if score1 == score2:
            conn_so = get_db()
            c_so = conn_so.cursor()
            
            evt_so_start = "🏒 **СЕРИЯ ПОСЛЕМАТЧЕВЫХ БУЛЛИТОВ!**"
            all_events.append(evt_so_start)
            await broadcast_match_text(context, p1_chat_id, p1_msg_id, p2_chat_id, p2_msg_id, f"{header}\n📊 **Счет:** 🔴 {score1} — {score2} 🔵\n\n{evt_so_start}")
            await asyncio.sleep(4)

            so_score1 = 0
            so_score2 = 0

            for round_num in range(1, 4):
                sk1 = random.choice([p1_cards['skater1'], p1_cards['skater2'], p1_cards['skater3'], p1_cards['skater4']])
                p1_prob = calc_shootout_prob(sk1['ovr'], p2_cards['goalie']['ovr'])
                if random.random() < p1_prob:
                    so_score1 += 1
                    score1 += 1
                    c_so.execute("UPDATE users SET balance = balance + 100 WHERE user_id = %s", (p1_id,))
                    conn_so.commit()
                    evt1 = f"🎯 **Буллит #{round_num} 🔴 {name1}**: {sk1['nickname']} — **ГОЛ!** (+100 RPLCoin) [{score1}:{score2}]"
                else:
                    evt1 = f"🚫 **Буллит #{round_num} 🔴 {name1}**: {sk1['nickname']} — **СЕЙВ!** [{score1}:{score2}]"
                
                all_events.append(evt1)

                sk2 = random.choice([p2_cards['skater1'], p2_cards['skater2'], p2_cards['skater3'], p2_cards['skater4']])
                p2_prob = calc_shootout_prob(sk2['ovr'], p1_cards['goalie']['ovr'])
                if random.random() < p2_prob:
                    so_score2 += 1
                    score2 += 1
                    c_so.execute("UPDATE users SET balance = balance + 100 WHERE user_id = %s", (p2_id,))
                    conn_so.commit()
                    evt2 = f"🎯 **Буллит #{round_num} 🔵 {name2}**: {sk2['nickname']} — **ГОЛ!** (+100 RPLCoin) [{score1}:{score2}]"
                else:
                    evt2 = f"🚫 **Буллит #{round_num} 🔵 {name2}**: {sk2['nickname']} — **СЕЙВ!** [{score1}:{score2}]"

                all_events.append(evt2)
                recent_events = "\n".join(all_events[-6:])
                await broadcast_match_text(context, p1_chat_id, p1_msg_id, p2_chat_id, p2_msg_id, f"{header}\n🎯 **СЕРИЯ БУЛЛИТОВ (Раунд {round_num}/3)**\n📊 Счет: 🔴 {score1} — {score2} 🔵\n\n{recent_events}")
                await asyncio.sleep(3)
            conn_so.close()

        await asyncio.sleep(2)

        conn = get_db()
        c = conn.cursor()

        if score1 > score2:
            res_text = f"🎉 **ПОБЕДА 🔴 {name1}!**\nИтоговый счет: **{score1} - {score2}**"
            apply_match_rewards(c, p1_id, is_win=True)
            apply_match_rewards(c, p2_id, is_win=False)
        elif score2 > score1:
            res_text = f"🎉 **ПОБЕДА 🔵 {name2}!**\nИтоговый счет: **{score1} - {score2}**"
            apply_match_rewards(c, p2_id, is_win=True)
            apply_match_rewards(c, p1_id, is_win=False)
        else:
            res_text = f"🤝 **НИЧЬЯ!**\nИтоговый счет: **{score1} - {score2}**"
            apply_match_rewards(c, p1_id, is_win=None)
            apply_match_rewards(c, p2_id, is_win=None)

        conn.commit()
        conn.close()

        final_text = (
            f"🏁 **МАТЧ ЗАВЕРШЕН!**\n\n"
            f"{res_text}\n\n"
            f"🏆 Победитель получил: **+50 MMR** и **+2000 RPLCoin**\n"
            f"🥈 Проигравший получил: **-50 MMR** и **+500 RPLCoin**\n"
            f"🪙 **За каждый забитый гол игрокам начислено по 100 RPLCoin!**\n\n"
            f"📋 **Полный протокол игры:**\n" + "\n".join(all_events)
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

        conn_ai = get_db()
        c_ai = conn_ai.cursor()

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
                    c_ai.execute("UPDATE users SET balance = balance + 100 WHERE user_id = %s", (p1_id,))
                    conn_ai.commit()

                    evt = f"⚡️ **{minute}' ГОЛ!** {scorer['nickname']} забивает за🔴 {name1}! (+100 RPLCoin) [{score1}:{score2}]"
                    all_events.append(evt)

                elif rand_val < prob_p1 + prob_ai:
                    scorer = random.choice([ai_cards['skater1'], ai_cards['skater2'], ai_cards['skater3'], ai_cards['skater4']])
                    score2 += 1
                    evt = f"⚡️ **{minute}' ГОЛ!** {scorer['nickname']} (ИИ Бот) забивает за🤖 ИИ Бота! [{score1}:{score2}]"
                    all_events.append(evt)

                else:
                    event_type = random.choice(["save1", "save2", "post", "hit"])
                    if event_type == "save1":
                        evt = f"🧤 **{minute}' СЕЙВ!** {p1_cards['goalie']['nickname']} забирает шайбу!"
                    elif event_type == "save2":
                        evt = f"🧤 **{minute}' СЕЙВ!** ИИ Вратарь отражает опасный бросок!"
                    elif event_type == "post":
                        evt = f"🏒 **{minute}' ШТАНГА!** Мощный щелчок сотрясает ворота!"
                    else:
                        evt = f"💥 **{minute}' СИЛОВОЙ ПРИЕМ!** Игроки сошлись у борта!"

                    all_events.append(evt)

                recent_events = "\n".join(all_events[-6:])
                status_text = (
                    f"{header}\n"
                    f"📊 **Текущий Счет:** 🔴 {score1} — {score2} 🤖\n"
                    f"{period_header}\n"
                    f"📝 **Ход матча:**\n{recent_events}"
                )
                await broadcast_match_text(context, chat_id, msg_id, None, None, status_text)
                await asyncio.sleep(3.5)

        conn_ai.close()
        await asyncio.sleep(2)

        if score1 == score2:
            conn_ot_ai = get_db()
            c_ot_ai = conn_ot_ai.cursor()
            
            evt_ot_start = "⏳ **ОСНОВНОЕ ВРЕМЯ ЗАВЕРШЕНО СО СЧЕТОМ " + f"{score1}:{score2}! Начинается ОВЕРТАЙМ (5 минут, 3х3)!**"
            all_events.append(evt_ot_start)
            await broadcast_match_text(context, chat_id, msg_id, None, None, f"{header}\n📊 **Счет:** 🔴 {score1} — {score2} 🤖\n\n{evt_ot_start}")
            await asyncio.sleep(4)

            ot_prob1 = prob_p1 * 0.8
            ot_prob2 = prob_ai * 0.8

            for ot_min in range(61, 66):
                rand_val = random.random()

                if rand_val < ot_prob1:
                    scorer = random.choice([p1_cards['skater1'], p1_cards['skater2']])
                    score1 += 1
                    c_ot_ai.execute("UPDATE users SET balance = balance + 100 WHERE user_id = %s", (p1_id,))
                    conn_ot_ai.commit()
                    evt = f"🔥 **{ot_min}' ЗОЛОТОЙ ГОЛ В ОВЕРТАЙМЕ!** {scorer['nickname']} приносит победу 🔴 {name1}! (+100 RPLCoin) [{score1}:{score2}]"
                    all_events.append(evt)
                    break
                elif rand_val < ot_prob1 + ot_prob2:
                    scorer = random.choice([ai_cards['skater1'], ai_cards['skater2']])
                    score2 += 1
                    evt = f"🔥 **{ot_min}' ЗОЛОТОЙ ГОЛ В ОВЕРТАЙМЕ!** {scorer['nickname']} приносит победу 🤖 ИИ Боту! [{score1}:{score2}]"
                    all_events.append(evt)
                    break
                else:
                    evt = f"🧤 **{ot_min}' ОПАСНЫЙ МОМЕНТ В ОВЕРТАЙМЕ!**"
                    all_events.append(evt)

                recent_events = "\n".join(all_events[-6:])
                await broadcast_match_text(context, chat_id, msg_id, None, None, f"{header}\n📊 **Счет:** 🔴 {score1} — {score2} 🤖\n⏱ **ОВЕРТАЙМ**\n\n{recent_events}")
                await asyncio.sleep(3.5)
            conn_ot_ai.close()

        if score1 == score2:
            conn_so_ai = get_db()
            c_so_ai = conn_so_ai.cursor()
            
            evt_so_start = "🏒 **СЕРИЯ ПОСЛЕМАТЧЕВЫХ БУЛЛИТОВ ПРОТИВ ИИ БОТА!**"
            all_events.append(evt_so_start)
            await broadcast_match_text(context, chat_id, msg_id, None, None, f"{header}\n\n{evt_so_start}")
            await asyncio.sleep(4)

            so_score1, so_score2 = 0, 0
            for round_num in range(1, 4):
                sk1 = random.choice([p1_cards['skater1'], p1_cards['skater2'], p1_cards['skater3'], p1_cards['skater4']])
                p1_prob = calc_shootout_prob(sk1['ovr'], ai_cards['goalie']['ovr'])
                if random.random() < p1_prob:
                    so_score1 += 1
                    score1 += 1
                    c_so_ai.execute("UPDATE users SET balance = balance + 100 WHERE user_id = %s", (p1_id,))
                    conn_so_ai.commit()
                    evt1 = f"🎯 **Буллит #{round_num} 🔴 {name1}**: {sk1['nickname']} — **ГОЛ!** (+100 RPLCoin) [{score1}:{score2}]"
                else:
                    evt1 = f"🚫 **Буллит #{round_num} 🔴 {name1}**: {sk1['nickname']} — **СЕЙВ!** [{score1}:{score2}]"
                all_events.append(evt1)

                sk2 = random.choice([ai_cards['skater1'], ai_cards['skater2'], ai_cards['skater3'], ai_cards['skater4']])
                ai_prob = calc_shootout_prob(sk2['ovr'], p1_cards['goalie']['ovr'])
                if random.random() < ai_prob:
                    so_score2 += 1
                    score2 += 1
                    evt2 = f"🎯 **Буллит #{round_num} 🤖 ИИ Бот**: {sk2['nickname']} — **ГОЛ!** [{score1}:{score2}]"
                else:
                    evt2 = f"🚫 **Буллит #{round_num} 🤖 ИИ Бот**: {sk2['nickname']} — **СЕЙВ!** [{score1}:{score2}]"
                all_events.append(evt2)

                recent_events = "\n".join(all_events[-6:])
                await broadcast_match_text(context, chat_id, msg_id, None, None, f"{header}\n🎯 **СЕРИЯ БУЛЛИТОВ**\n📊 Счет: 🔴 {score1} — {score2} 🤖\n\n{recent_events}")
                await asyncio.sleep(3)
            conn_so_ai.close()

        await asyncio.sleep(2)

        conn = get_db()
        c = conn.cursor()

        if score1 > score2:
            res_text = f"🎉 **ПОБЕДА НАД ИИ БОТОМ!**\nИтоговый счет: **{score1} - {score2}**"
            apply_match_rewards(c, p1_id, is_win=True)
        elif score2 > score1:
            res_text = f"❌ **ПОРАЖЕНИЕ ОТ ИИ БОТА!**\nИтоговый счет: **{score1} - {score2}**"
            apply_match_rewards(c, p1_id, is_win=False)
        else:
            res_text = f"🤝 **НИЧЬЯ С ИИ БОТОМ!**\nИтоговый счет: **{score1} - {score2}**"
            apply_match_rewards(c, p1_id, is_win=None)

        conn.commit()
        conn.close()

        final_text = (
            f"🏁 **МАТЧ ЗАВЕРШЕН!**\n\n"
            f"{res_text}\n\n"
            f"🪙 **За каждый забитый гол вам начислено по 100 RPLCoin!**\n\n"
            f"📋 **Протокол игры:**\n" + "\n".join(all_events)
        )

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
    elif is_win is False:
        cursor.execute("UPDATE users SET mmr = GREATEST(0, mmr - 50), balance = balance + 500 WHERE user_id = %s", (user_id,))
    else:
        cursor.execute("UPDATE users SET balance = balance + 500 WHERE user_id = %s", (user_id,))

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

# ---------- МАГАЗИН И ПАКГИ С ОГРАНИЧЕНИЕМ ПО ВРЕМЕНИ ИЗ АДМИНКИ ----------
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
    # Удаляем просроченные временные паки или выводим только актуальные
    now = datetime.now()
    c.execute("SELECT * FROM packs WHERE available_until IS NULL OR available_until > %s", (now,))
    packs = c.fetchall()

    if not packs:
        conn.close()
        text = "🛒 **Магазин Паков пуст.** Администратор скоро добавит новые паки!"
        if query:
            await query.answer()
            await query.message.edit_text(text)
        else:
            await update.message.reply_text(text)
        return

    text = "🛒 **МАГАЗИН ПАКОВ КАРТОЧЕК:**\n\nВыберите пак для предварительного просмотра и покупки:\n\n"
    buttons = []

    for p in packs:
        c.execute("SELECT buy_count FROM user_pack_buys WHERE user_id = %s AND pack_id = %s", (user.id, p['id']))
        b_row = c.fetchone()
        b_count = b_row['buy_count'] if b_row else 0

        lim_str = f"{b_count}/{p['buy_limit']}" if p['buy_limit'] > 0 else "Безлимит"
        time_left = ""
        if p['available_until']:
            diff = p['available_until'] - now
            hours = int(diff.total_seconds() // 3600)
            time_left = f" (⏱ Осталось: {hours}ч)"

        text += f"📦 **{p['name']}** — `{p['price']} RPLCoin` (Куплено: {lim_str}){time_left}\n"
        buttons.append([InlineKeyboardButton(f"📦 {p['name']} ({p['price']} RPLCoin)", callback_data=f"preview_pack_{p['id']}")])

    conn.close()

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

    if data.startswith("preview_pack_"):
        pack_id = int(data.split("_")[2])
        conn = get_db()
        c = conn.cursor()

        c.execute("SELECT * FROM packs WHERE id = %s", (pack_id,))
        pack = c.fetchone()

        if not pack:
            conn.close()
            await query.answer("❌ Пак не найден!", show_alert=True)
            return

        c.execute("SELECT buy_count FROM user_pack_buys WHERE user_id = %s AND pack_id = %s", (user.id, pack_id))
        b_row = c.fetchone()
        b_count = b_row['buy_count'] if b_row else 0
        lim_str = f"{b_count}/{pack['buy_limit']}" if pack['buy_limit'] > 0 else "Безлимит"

        c.execute('''
            SELECT c.nickname, c.ovr, c.rarity, c.position
            FROM pack_cards pc
            JOIN cards c ON pc.card_id = c.id
            WHERE pc.pack_id = %s
        ''', (pack_id,))
        p_cards = c.fetchall()
        conn.close()

        cards_str = ""
        for pc in p_cards:
            cards_str += f"  • **{pc['nickname']}** ({pc['position']}, {pc['ovr']} OVR) [{pc['rarity']}]\n"

        caption = (
            f"📦 **ПРЕДПРОСМОТР ПАКА «{pack['name']}»**\n\n"
            f"💰 Цена: **{pack['price']} RPLCoin**\n"
            f"🔢 Лимит покупок: **{lim_str}**\n\n"
            f"🃏 **Возможные карточки в паке:**\n{cards_str or '  *(карточки не указаны)*'}\n"
            f"Подтвердите покупку ниже:"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Подтвердить покупку", callback_data=f"confirm_pack_{pack_id}")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_pack_buy")]
        ])

        await query.answer()

        if pack['photo_id']:
            try:
                await query.message.delete()
                await context.bot.send_photo(chat_id=user.id, photo=pack['photo_id'], caption=caption, reply_markup=kb, parse_mode="Markdown")
                return
            except Exception:
                pass

        await query.message.edit_text(caption, reply_markup=kb, parse_mode="Markdown")

    elif data == "cancel_pack_buy":
        await show_shop(update, context)

    elif data.startswith("confirm_pack_"):
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

        await query.answer("🎉 Пак успешно приобретен!", show_alert=True)

        temp_msg = await context.bot.send_message(chat_id=user.id, text="⏳ **Идет открытие пака...**", parse_mode="Markdown")
        await asyncio.sleep(3)

        try:
            await context.bot.delete_message(chat_id=user.id, message_id=temp_msg.message_id)
        except Exception:
            pass

        team_str = f"{card['team_emoji'] or '🏒'} {card['team_name']}" if card['team_name'] else "Без команды"
        caption = (
            f"📦 **Из пака «{pack['name']}» вам выпала карточка!**\n\n"
            f"┏━━━━━━━━━━━━━━━━━━━━┓\n"
            f"┃ 👤 {card['nickname']}\n"
            f"┃ 📁 Коллекция: {card['collection_name']}\n"
            f"┃ 🏒 {card['position']}\n"
            f"┃ ⭐ {card['ovr']} OVR\n"
            f"┃ {team_str}\n"
            f"┃ 🌍 {card['country']}\n"
            f"┃ ✨ {card['rarity']}\n"
            f"┗━━━━━━━━━━━━━━━━━━━━┛"
        )

        if card['image_id']:
            try:
                await context.bot.send_photo(chat_id=user.id, photo=card['image_id'], caption=caption, parse_mode="Markdown")
            except Exception:
                await context.bot.send_message(chat_id=user.id, text=caption, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user.id, text=caption, parse_mode="Markdown")

        await show_shop(update, context)

# ---------- АДМИНКАТОР И ВЫСТАВЛЕНИЕ ПАКОВ НА ВРЕМЯ ----------
async def admin_set_pack_time_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM packs")
    packs = c.fetchall()
    conn.close()

    if not packs:
        await update.message.reply_text("📭 В базе нет созданных паков.", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END

    buttons = [[InlineKeyboardButton(p['name'], callback_data=f"adm_pack_{p['id']}")] for p in packs]
    await update.message.reply_text("📦 Выберите пак, который хотите выставить на определенные часы:", reply_markup=InlineKeyboardMarkup(buttons))
    return ADMIN_SHOP_PACK_SELECT

async def admin_shop_pack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    pack_id = int(query.data.split("_")[2])
    context.user_data["admin_pack_id"] = pack_id
    await query.message.reply_text("⏳ Введите количество часов, на которые пак будет выставлен в магазин (например: `24` или `48`):", parse_mode="Markdown")
    return ADMIN_SHOP_PACK_HOURS

async def admin_shop_pack_hours_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        hours = int(update.message.text.strip())
        pack_id = context.user_data.get("admin_pack_id")
        until_time = datetime.now() + timedelta(hours=hours)

        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE packs SET available_until = %s WHERE id = %s", (until_time, pack_id))
        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ Пак успешно выставлен в магазин на **{hours} часов** (доступен до {until_time.strftime('%Y-%m-%d %H:%M')})!", reply_markup=admin_menu_keyboard(), parse_mode="Markdown")
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Введите количество часов целым числом!")
        return ADMIN_SHOP_PACK_HOURS

# ---------- АДМИН-ПАНЕЛЬ КАРТОЧЕК И ПАКОВ ----------
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
        await update.message.reply_text("🎁 Введите ID/@username пользователя и ID карточки через пробел (например: `@username 5` или `123456789 5`):", parse_mode="Markdown")
        return GRANT_CARD_DATA

    elif text == "💰 Выдать деньги":
        await update.message.reply_text("💰 Введите @username пользователя и сумму через пробел (например: `@username 5000`):", parse_mode="Markdown")
        return GIVE_MONEY_DATA

    elif text == "🎟 Создать промокод":
        await admin_create_promo_start(update, context)
        return ADD_PROMO_CODE

    elif text == "⬅️ Выйти из настройки карточек":
        await update.message.reply_text("⚙️ Админ-панель:", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END

    return CARD_ADMIN_MENU

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
    await update.message.reply_text("🏒 Введите один эмодзи/смайлик для команды (например 🟢 или 🏒):")
    return ADD_TEAM_EMOJI

async def save_team_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["team_emoji"] = update.message.text.strip()
    await update.message.reply_text("🖼 Отправьте логотип/фото графику команды (или введите `-` чтобы пропустить):")
    return ADD_TEAM_PHOTO

async def save_team_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = None
    if update.message.photo:
        photo_id = update.message.photo[-1].file_id

    name = context.user_data.get("team_name")
    emoji = context.user_data.get("team_emoji", "🏒")

    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO card_teams (name, emoji, photo_id) VALUES (%s, %s, %s)", (name, emoji, photo_id))
        conn.commit()
        await update.message.reply_text(f"✅ Команда {emoji} **{name}** успешно создана!", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
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
        await update.message.reply_text("❌ Сначала создайте коллекцию!", reply_markup=card_admin_keyboard())
        return CARD_ADMIN_MENU

    buttons = [[c_row['name']] for c_row in cols]
    await update.message.reply_text("📁 Выберите коллекцию:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
    return ADD_CARD_COLLECTION

async def card_set_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_collection"] = update.message.text.strip()
    
    kb = [COUNTRIES[i:i+3] for i in range(0, len(COUNTRIES), 3)]
    await update.message.reply_text("🌍 Выберите страну игрока:", reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True))
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

    await update.message.reply_text("🛡 Выберите команду игрока:", reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True))
    return ADD_CARD_TEAM

async def card_set_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t_text = update.message.text.strip()
    context.user_data["c_team"] = t_text

    await update.message.reply_text("🏷 Введите NickName игрока и номер (например: `miulio #11`):", reply_markup=ReplyKeyboardRemove(), parse_mode="Markdown")
    return ADD_CARD_NICK

async def card_set_nick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["c_nick"] = update.message.text.strip()
    await update.message.reply_text("⭐ Введите рейтинг OVR (число от 50 до 99):")
    return ADD_CARD_OVR

async def card_set_ovr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ovr = int(update.message.text.strip())
        context.user_data["c_ovr"] = ovr
        await update.message.reply_text("🖼 Отправьте фотографию или GIF карточки:")
        return ADD_CARD_PHOTO
    except ValueError:
        await update.message.reply_text("❌ Введите OVR числом!")
        return ADD_CARD_OVR

async def card_save_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = None
    if update.message.photo:
        photo_id = update.message.photo[-1].file_id
    elif update.message.animation:
        photo_id = update.message.animation.file_id

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

    await update.message.reply_text(f"✅ **Карточка создана!**\n🆔 ID карточки: `{new_card_id}`\n👤 Игрок: {nick} ({ovr} OVR)", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
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
        price = int(update.message.text.strip())
        context.user_data["p_price"] = price
        await update.message.reply_text("🔢 Введите лимит покупок пака на одного игрока (0 = безлимит):")
        return ADD_PACK_LIMIT
    except ValueError:
        await update.message.reply_text("❌ Введите цену числом!")
        return ADD_PACK_PRICE

async def pack_set_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lim = int(update.message.text.strip())
        context.user_data["p_limit"] = lim
        await update.message.reply_text("🆔 Введите ID карточек для этого пака через пробел (до 10 ID):\nПример: `1 2 5 12`", parse_mode="Markdown")
        return ADD_PACK_CARDS
    except ValueError:
        await update.message.reply_text("❌ Введите лимит числом!")
        return ADD_PACK_LIMIT

async def pack_set_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        card_ids = [int(x) for x in update.message.text.strip().split()]
        if len(card_ids) > 10:
            await update.message.reply_text("❌ В паке не может быть более 10 карточек!")
            return ADD_PACK_CARDS
        context.user_data["p_cards"] = card_ids
        await update.message.reply_text("🖼 Отправьте фото/анимацию для обложки пака:")
        return ADD_PACK_PHOTO
    except ValueError:
        await update.message.reply_text("❌ Введите ID карточек через пробел!")
        return ADD_PACK_CARDS

async def pack_save_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo_id = None
    if update.message.photo:
        photo_id = update.message.photo[-1].file_id

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

        if user_input.isdigit():
            target_id = int(user_input)
        else:
            c.execute("SELECT user_id FROM users WHERE username = %s", (user_input,))
            u_row = c.fetchone()
            if not u_row:
                conn.close()
                await update.message.reply_text("❌ Пользователь не найден!", reply_markup=card_admin_keyboard())
                return CARD_ADMIN_MENU
            target_id = u_row['user_id']

        c.execute("INSERT INTO user_cards (user_id, card_id, count) VALUES (%s, %s, 1) ON CONFLICT (user_id, card_id) DO UPDATE SET count = user_cards.count + 1", (target_id, card_id))
        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ Пользователю {target_id} выдана карточка ID {card_id}!", reply_markup=card_admin_keyboard())
    except Exception:
        await update.message.reply_text("❌ Неверный формат! Введите: `@username ID`", reply_markup=card_admin_keyboard())
    return CARD_ADMIN_MENU

async def give_money_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        parts = update.message.text.strip().split()
        username = parts[0].replace("@", "")
        amount = int(parts[1])

        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE users SET balance = balance + %s WHERE username = %s RETURNING user_id", (amount, username))
        row = c.fetchone()
        conn.commit()
        conn.close()

        if row:
            await update.message.reply_text(f"✅ Пользователю @{username} зачислено **{amount} RPLCoin**!", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
        else:
            await update.message.reply_text("❌ Пользователь не найден!", reply_markup=card_admin_keyboard())
    except Exception:
        await update.message.reply_text("❌ Неверный формат! Введите: `@username сумма`", reply_markup=card_admin_keyboard())
    return CARD_ADMIN_MENU

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
        await update.message.reply_text("❌ Игрок с таким username/ID не найден!", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END

    target_id = target_user['user_id']
    c.execute('''
        SELECT uc.count, c.*, col.name as col_name, t.name as team_name, t.emoji as team_emoji
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        JOIN collections col ON c.collection_id = col.id
        LEFT JOIN card_teams t ON c.team_id = t.id
        WHERE uc.user_id = %s AND uc.count > 0
        ORDER BY col.name, c.ovr DESC
    ''', (target_id,))
    user_cards = c.fetchall()
    conn.close()

    uname = f"@{target_user['username']}" if target_user['username'] else target_user['first_name']
    text = (
        f"🎒 **Инвентарь игрока {uname}** (`ID: {target_id}`):\n"
        f"💳 Баланс: **{target_user['balance']} RPLCoin** | 🏆 MMR: **{target_user['mmr']}**\n\n"
    )

    if not user_cards:
        text += "📭 У игрока нет карточек в инвентаре."
    else:
        for uc in user_cards:
            t_str = f"{uc['team_emoji']} {uc['team_name']}" if uc['team_name'] else ""
            text += f"ID `{uc['id']}` | **{uc['nickname']}** ({uc['position']}, {uc['ovr']} OVR) — `x{uc['count']}` [{uc['rarity']}] | 📁 {uc['col_name']} {t_str}\n"

    if len(text) > 4000:
        parts = [text[i:i+3800] for i in range(0, len(text), 3800)]
        for p in parts:
            await update.message.reply_text(p, parse_mode="Markdown")
        await update.message.reply_text("⚙️ Админ-панель:", reply_markup=admin_menu_keyboard())
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_menu_keyboard())

    return ConversationHandler.END

async def admin_show_players_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT user_id, username, first_name, balance, mmr FROM users ORDER BY user_id DESC")
    users = c.fetchall()
    conn.close()

    if not users:
        await update.message.reply_text("📭 В боте пока нет зарегистрированных игроков.", reply_markup=admin_menu_keyboard())
        return

    text = f"👥 **Список игроков, которые играли в бота (Всего: {len(users)}):**\n\n"
    lines = []
    for u in users:
        uname = f"@{u['username']}" if u['username'] else u['first_name'] or "Без имени"
        lines.append(f"• {uname} (`{u['user_id']}`) | 💳 `{u['balance']} RPLCoin` | 🏆 `{u['mmr']} MMR`")

    msg_chunk = text
    for line in lines:
        if len(msg_chunk) + len(line) + 1 > 3800:
            await update.message.reply_text(msg_chunk, parse_mode="Markdown")
            msg_chunk = ""
        msg_chunk += line + "\n"

    if msg_chunk:
        await update.message.reply_text(msg_chunk, parse_mode="Markdown", reply_markup=admin_menu_keyboard())

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

async def minigames_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_pm_registered(update, context):
        return
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 Камень-Ножницы-Бумага", callback_data="play_rps")]
    ])
    await update.message.reply_text("🕹 **Выберите мини-игру:**", reply_markup=kb, parse_mode="Markdown")

async def adminkarpl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Команда только в личных сообщениях.")
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
    login = context.user_data.get("login")
    password = update.message.text
    if check_credentials(login, password):
        add_admin(update.effective_user.id)
        context.user_data.clear()
        await update.message.reply_text("✅ Авторизован!", reply_markup=admin_menu_keyboard())
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Неверный логин или пароль!")
        return ConversationHandler.END

async def admin_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return

    update_admin_activity(user_id)
    text = update.message.text

    if text == "➕ Добавить каналы":
        await update.message.reply_text("Введите @username канала (бот должен быть админом):")
        return WAITING_CHANNEL_USERNAME
    elif text == "➕ Добавить чаты":
        await update.message.reply_text("Введите числовой ID чата или @username:")
        return WAITING_CHAT_LINK
    elif text == "📩 Проверить поддержку":
        await show_support_messages(update, context)
        return
    elif text == "⚙️ Настройки":
        await show_settings(update, context)
        return
    elif text == "🎮 Настройки игры":
        await show_game_settings(update, context)
        return ConversationHandler.END
    elif text == "🃏 Карточки":
        await update.message.reply_text("🃏 **Раздел управления карточками:**", reply_markup=card_admin_keyboard(), parse_mode="Markdown")
        return CARD_ADMIN_MENU
    elif text == "📦 Выставить пак в магазин":
        return await admin_set_pack_time_start(update, context)
    elif text == "🔍 Инвентарь игрока":
        await update.message.reply_text("🔍 Введите @username или ID игрока:")
        return WAITING_VIEW_USER_INV
    elif text == "👥 Список игроков":
        await admin_show_players_list(update, context)
        return ConversationHandler.END
    elif text == "🚪 Выйти":
        remove_admin(user_id)
        await update.message.reply_text("🚪 Вы вышли из админ-панели.", reply_markup=main_menu_keyboard())
        return
    return ConversationHandler.END

async def add_channel_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    if not username.startswith('@'):
        username = '@' + username
    try:
        chat = await context.bot.get_chat(username)
        add_source_channel(chat.id, username, update.effective_user.id)
        await update.message.reply_text(f"✅ Канал {username} добавлен.", reply_markup=admin_menu_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
    return ConversationHandler.END

async def add_chat_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    try:
        chat = await context.bot.get_chat(link)
        add_target_chat(chat.id, link, update.effective_user.id)
        await update.message.reply_text(f"✅ Чат {link} добавлен.", reply_markup=admin_menu_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
    return ConversationHandler.END

async def show_support_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    messages = get_unanswered_messages()
    if not messages:
        await update.message.reply_text("📭 Новых обращений нет.", reply_markup=admin_menu_keyboard())
        return
    msg = messages[0]
    display_text = f"📩 Обращение #{msg['id']}\n👤 {msg['username'] or msg['user_id']}\n🕒 {msg['timestamp']}\n\n{msg['text']}"
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Ответить", callback_data=f"reply_{msg['id']}")],
        [InlineKeyboardButton("✅ Закрыть", callback_data=f"close_{msg['id']}")]
    ])
    await update.message.reply_text(display_text, reply_markup=keyboard)

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sources = get_source_channels()
    targets = get_target_chats()
    text = "📋 **Настройки**\n\n📢 **Каналы-источники:**\n"
    for s in sources:
        text += f" - {s['username'] or s['chat_id']}\n"
    text += "\n📥 **Целевые чаты:**\n"
    for t in targets:
        text += f" - {t['link'] or t['chat_id']}\n"
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=admin_menu_keyboard())

async def show_game_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎮 Настройки игры настроены.", reply_markup=admin_menu_keyboard())

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
    elif data == "play_rps":
        await query.message.reply_text("🎮 **Камень - Ножницы - Бумага**\nВведите ставку в RPLCoin (целое число):", parse_mode="Markdown")
        return WAITING_RPS_BET

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

    conv_auth = ConversationHandler(
        entry_points=[CommandHandler("adminkarpl", adminkarpl)],
        states={
            WAITING_LOGIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, wait_login)],
            WAITING_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, wait_password)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    )
    app.add_handler(conv_auth)

    conv_channel = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить каналы$") & filters.ChatType.PRIVATE, admin_buttons)],
        states={WAITING_CHANNEL_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_channel_username)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    )
    app.add_handler(conv_channel)

    conv_chat = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить чаты$") & filters.ChatType.PRIVATE, admin_buttons)],
        states={WAITING_CHAT_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_chat_link)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    )
    app.add_handler(conv_chat)

    conv_user_inv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🔍 Инвентарь игрока$") & filters.ChatType.PRIVATE, admin_buttons)],
        states={WAITING_VIEW_USER_INV: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_view_inventory_execute)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    )
    app.add_handler(conv_user_inv)

    conv_support = ConversationHandler(
        entry_points=[CallbackQueryHandler(inline_callback, pattern="^support$")],
        states={WAITING_SUPPORT_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    )
    app.add_handler(conv_support)

    conv_duel = ConversationHandler(
        entry_points=[CallbackQueryHandler(inline_callback, pattern="^duel$")],
        states={WAITING_DUEL_SHOT: [CallbackQueryHandler(duel_shot, pattern="^shot_")]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    )
    app.add_handler(conv_duel)

    conv_promo_user = ConversationHandler(
        entry_points=[
            CommandHandler("promo", promo_command),
            MessageHandler(filters.Regex("^🎁 Промокод$"), promo_command)
        ],
        states={WAITING_PROMO_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, promo_input_receive)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    )
    app.add_handler(conv_promo_user)

    conv_market_price = ConversationHandler(
        entry_points=[CallbackQueryHandler(market_callback_handler, pattern="^select_mcard_")],
        states={WAITING_MARKET_PRICE_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, execute_market_list_price)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    )
    app.add_handler(conv_market_price)

    conv_trade_money = ConversationHandler(
        entry_points=[CallbackQueryHandler(trade_callback_handler, pattern="^tr_addmoney_")],
        states={WAITING_TRADE_MONEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, execute_trade_money_input)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    )
    app.add_handler(conv_trade_money)

    conv_rps = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(inline_callback, pattern="^play_rps$"),
            MessageHandler(filters.Regex("^🎮 Мини-игры$"), minigames_menu)
        ],
        states={WAITING_RPS_BET: [MessageHandler(filters.TEXT & ~filters.COMMAND, rps_receive_bet)]},
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    )
    app.add_handler(conv_rps)

    conv_admin_shop_pack = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📦 Выставить пак в магазин$") & filters.ChatType.PRIVATE, admin_buttons)
        ],
        states={
            ADMIN_SHOP_PACK_SELECT: [CallbackQueryHandler(admin_shop_pack_callback, pattern="^adm_pack_")],
            ADMIN_SHOP_PACK_HOURS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_shop_pack_hours_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
    )
    app.add_handler(conv_admin_shop_pack)

    conv_cards = ConversationHandler(
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
            ADD_PRO_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_promo_set_code)],
            ADD_PROMO_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_promo_set_code)],
            ADD_PROMO_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_promo_set_type)],
            ADD_PROMO_VAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_promo_set_val)],
            ADD_PROMO_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_promo_save)],
        },
        fallbacks=[CommandHandler("cancel", lambda u,c: u.message.reply_text("Отменено."))],
        allow_reentry=True,
    )
    app.add_handler(conv_cards)

    app.add_handler(MessageHandler(filters.Regex("^(📩 Проверить поддержку|⚙️ Настройки|🎮 Настройки игры|👥 Список игроков|🚪 Выйти)$") & filters.ChatType.PRIVATE, admin_buttons))

    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("rplcards", rplcards_command))
    app.add_handler(CommandHandler("inventory", inventory_command))
    app.add_handler(CommandHandler("profile", profile_command))
    app.add_handler(CommandHandler("checkprofile", checkprofile_command))
    app.add_handler(CommandHandler("cardmatch", cardmatch_command))
    app.add_handler(CommandHandler("cardmmr", cardmmr_command))
    app.add_handler(CommandHandler("shop", shop_command))
    app.add_handler(CommandHandler("cardshop", cardshop_command))
    app.add_handler(CommandHandler("trade", trade_command))
    app.add_handler(CommandHandler("daily", daily_command))
    app.add_handler(CommandHandler("wheel", wheel_command))
    app.add_handler(CommandHandler("rps", rps_command))

    # Текстовое меню
    app.add_handler(MessageHandler(filters.Regex("^🏠 Главное меню$"), main_menu))
    app.add_handler(MessageHandler(filters.Regex("^🃏 Бесплатная карта$"), rplcards_command))
    app.add_handler(MessageHandler(filters.Regex("^🎒 Инвентарь$"), inventory_command))
    app.add_handler(MessageHandler(filters.Regex("^🛒 Торговая площадка$"), cardshop_command))
    app.add_handler(MessageHandler(filters.Regex("^🏒 Состав и Профиль$"), profile_command))
    app.add_handler(MessageHandler(filters.Regex("^⚔️ Искать игру$"), cardmatch_command))
    app.add_handler(MessageHandler(filters.Regex("^🛒 Магазин Паков$"), shop_command))
    app.add_handler(MessageHandler(filters.Regex("^🏆 Топ MMR$"), cardmmr_command))
    app.add_handler(MessageHandler(filters.Regex("^🤝 Трейд$"), trade_command))
    app.add_handler(MessageHandler(filters.Regex("^🎡 Колесо удачи$"), wheel_command))
    app.add_handler(MessageHandler(filters.Regex("^🎁 Ежедневный бонус$"), daily_command))
    app.add_handler(MessageHandler(filters.Regex("^🎮 Мини-игры$"), minigames_menu))

    # Callback Handlers
    app.add_handler(CallbackQueryHandler(inventory_callback_handler, pattern="^(refresh_inv|craft_leg_|sell_menu|do_sell_)"))
    app.add_handler(CallbackQueryHandler(market_callback_handler, pattern="^(refresh_market|my_market_items|market_list_menu|cancel_market_|buy_market_)"))
    app.add_handler(CallbackQueryHandler(trade_callback_handler, pattern="^(accept_trade_|decline_trade_|tr_)"))
    app.add_handler(CallbackQueryHandler(profile_callback_handler, pattern="^(refresh_profile|edit_roster_menu|set_pos_|apply_card_)"))
    app.add_handler(CallbackQueryHandler(match_callback_handler, pattern="^(accept_match_|cancel_match_)"))
    app.add_handler(CallbackQueryHandler(shop_callback_handler, pattern="^(preview_pack_|confirm_pack_|cancel_pack_buy)"))
    app.add_handler(CallbackQueryHandler(rps_callback_handler, pattern="^rps_"))
    app.add_handler(CallbackQueryHandler(admin_shop_pack_callback, pattern="^adm_pack_"))
    app.add_handler(CallbackQueryHandler(inline_callback))

    logger.info("Бот RPL успешно запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
