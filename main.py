import os
import logging
import random
import time
import asyncio
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta

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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не задан!")

ADMIN_LOGIN = os.getenv("ADMIN_LOGIN", "rzk1488")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "rzksigma")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан! Создайте базу PostgreSQL в Railway.")

# ------------------- СОСТОЯНИЯ -------------------
(
    ADMIN_LOGIN_STATE,
    ADMIN_PASSWORD_STATE,
    ADD_MATCH_TEAM1,
    ADD_MATCH_TEAM2,
    ADD_MATCH_COEF1,
    ADD_MATCH_COEF2,
    BET_AMOUNT,
    GIVE_MONEY,
    BROADCAST_MSG,
    # Новые состояния для карточек (админ)
    ADMIN_CARDS_MENU,
    CREATE_COLLECTION_NAME,
    ADD_CARD_RARITY,
    ADD_CARD_COLLECTION,
    ADD_CARD_NAME,
    ADD_CARD_IMAGE,
    GIVE_CARD_USER,
    GIVE_CARD_SELECT,
    # Состояния для крафта (инвентарь)
    CRAFT_CONFIRM,
) = range(19)

# ------------------- БАЗА ДАННЫХ (PostgreSQL) -------------------
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # Существующие таблицы
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance INTEGER DEFAULT 5000,
            last_roulette INTEGER DEFAULT 0,
            last_card_time INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            match_id SERIAL PRIMARY KEY,
            team1 TEXT,
            team2 TEXT,
            coef1 REAL,
            coef2 REAL,
            status TEXT DEFAULT 'OPEN',
            winner INTEGER DEFAULT 0
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS bets (
            bet_id SERIAL PRIMARY KEY,
            user_id BIGINT,
            match_id INTEGER,
            team_choice INTEGER,
            amount INTEGER,
            coef REAL,
            status TEXT DEFAULT 'PENDING'
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY
        )
    ''')

    # Новые таблицы для карточек
    c.execute('''
        CREATE TABLE IF NOT EXISTS collections (
            id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            id SERIAL PRIMARY KEY,
            collection_id INTEGER REFERENCES collections(id) ON DELETE CASCADE,
            rarity TEXT NOT NULL,
            name TEXT NOT NULL,
            file_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_cards (
            id SERIAL PRIMARY KEY,
            user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
            card_id INTEGER REFERENCES cards(id) ON DELETE CASCADE,
            quantity INTEGER DEFAULT 1,
            acquired_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, card_id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ КАРТОЧЕК -------------------
def get_user(user_id, username="", first_name=""):
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute(
            "INSERT INTO users (user_id, username, first_name, balance, last_roulette, last_card_time) VALUES (%s, %s, %s, 5000, 0, 0)",
            (user_id, username, first_name)
        )
        conn.commit()
        c.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        row = c.fetchone()
    else:
        c.execute("UPDATE users SET username = %s, first_name = %s WHERE user_id = %s", (username, first_name, user_id))
        conn.commit()
    conn.close()
    return row

def update_balance(user_id, delta):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (delta, user_id))
    conn.commit()
    conn.close()

def is_admin(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT user_id FROM admins WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

def add_admin(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO admins (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    conn.commit()
    conn.close()

def remove_admin(user_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE user_id = %s", (user_id,))
    conn.commit()
    conn.close()

# ------------------- ФУНКЦИИ КАРТОЧЕК -------------------
def get_collections():
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT id, name FROM collections ORDER BY name")
    rows = c.fetchall()
    conn.close()
    return rows

def create_collection(name):
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO collections (name) VALUES (%s) RETURNING id", (name,))
        new_id = c.fetchone()[0]
        conn.commit()
        conn.close()
        return new_id
    except psycopg2.IntegrityError:
        conn.rollback()
        conn.close()
        return None

def add_card(collection_id, rarity, name, file_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO cards (collection_id, rarity, name, file_id) VALUES (%s, %s, %s, %s)",
        (collection_id, rarity, name, file_id)
    )
    conn.commit()
    conn.close()

def get_cards_by_rarity(rarity):
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("""
        SELECT c.id, c.name, c.file_id, col.name as collection_name
        FROM cards c
        JOIN collections col ON c.collection_id = col.id
        WHERE c.rarity = %s
    """, (rarity,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_card_by_id(card_id):
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("""
        SELECT c.*, col.name as collection_name
        FROM cards c
        JOIN collections col ON c.collection_id = col.id
        WHERE c.id = %s
    """, (card_id,))
    row = c.fetchone()
    conn.close()
    return row

def get_user_cards(user_id):
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("""
        SELECT c.id, c.name, c.rarity, col.name as collection_name, uc.quantity
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        JOIN collections col ON c.collection_id = col.id
        WHERE uc.user_id = %s
        ORDER BY col.name, c.rarity, c.name
    """, (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def add_user_card(user_id, card_id, quantity=1):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO user_cards (user_id, card_id, quantity) VALUES (%s, %s, %s) "
        "ON CONFLICT (user_id, card_id) DO UPDATE SET quantity = user_cards.quantity + %s",
        (user_id, card_id, quantity, quantity)
    )
    conn.commit()
    conn.close()

def remove_user_card(user_id, card_id, quantity=1):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "UPDATE user_cards SET quantity = quantity - %s WHERE user_id = %s AND card_id = %s AND quantity >= %s",
        (quantity, user_id, card_id, quantity)
    )
    # удаляем запись, если quantity стало 0
    c.execute("DELETE FROM user_cards WHERE user_id = %s AND card_id = %s AND quantity <= 0", (user_id, card_id))
    conn.commit()
    conn.close()

def get_user_card_quantity(user_id, card_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT quantity FROM user_cards WHERE user_id = %s AND card_id = %s", (user_id, card_id))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def get_mythical_cards_by_collection(user_id):
    """Возвращает словарь {collection_id: количество мифических карточек}"""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT c.collection_id, SUM(uc.quantity) as total
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        WHERE uc.user_id = %s AND c.rarity = 'Мифическая'
        GROUP BY c.collection_id
        HAVING SUM(uc.quantity) >= 5
    """, (user_id,))
    rows = c.fetchall()
    conn.close()
    result = {}
    for col_id, total in rows:
        # Проверим, что есть хотя бы 5 карт в этой коллекции (суммарно)
        result[col_id] = total
    return result

def get_mythical_cards_for_craft(user_id, collection_id):
    """Возвращает список card_id и quantity для мифических карт в указанной коллекции"""
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("""
        SELECT uc.card_id, uc.quantity
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        WHERE uc.user_id = %s AND c.collection_id = %s AND c.rarity = 'Мифическая' AND uc.quantity > 0
    """, (user_id, collection_id))
    rows = c.fetchall()
    conn.close()
    return rows

def craft_legendary(user_id, collection_id):
    """Крафт легендарной карты: удаляем 5 мифических (суммарно) и создаём легендарную."""
    # Проверим, что у пользователя есть легендарная карта в этой коллекции (если уже есть, то просто добавим ещё одну)
    # Но по логике обычно выдаётся новая. Мы создадим новую легендарную карту в той же коллекции (если нет, то создадим).
    # Сначала убедимся, что сумма мифических >=5
    conn = get_db_connection()
    c = conn.cursor()
    # Проверим сумму
    c.execute("""
        SELECT SUM(uc.quantity) as total
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        WHERE uc.user_id = %s AND c.collection_id = %s AND c.rarity = 'Мифическая'
    """, (user_id, collection_id))
    total = c.fetchone()[0]
    if total is None or total < 5:
        conn.close()
        return False, "Недостаточно мифических карточек (нужно минимум 5)."

    # Получаем все мифические карты пользователя в этой коллекции
    c.execute("""
        SELECT uc.card_id, uc.quantity
        FROM user_cards uc
        JOIN cards c ON uc.card_id = c.id
        WHERE uc.user_id = %s AND c.collection_id = %s AND c.rarity = 'Мифическая' AND uc.quantity > 0
    """, (user_id, collection_id))
    cards = c.fetchall()
    # Удаляем 5 штук, начиная с первых
    remaining = 5
    for card_id, qty in cards:
        if remaining <= 0:
            break
        take = min(qty, remaining)
        # Уменьшаем quantity
        c.execute("UPDATE user_cards SET quantity = quantity - %s WHERE user_id = %s AND card_id = %s", (take, user_id, card_id))
        # Удаляем если стало 0
        c.execute("DELETE FROM user_cards WHERE user_id = %s AND card_id = %s AND quantity <= 0", (user_id, card_id))
        remaining -= take

    # Теперь создаём легендарную карту. Проверим, есть ли уже легендарная в этой коллекции.
    c.execute("SELECT id FROM cards WHERE collection_id = %s AND rarity = 'Легендарная' LIMIT 1", (collection_id,))
    legendary_card = c.fetchone()
    if legendary_card:
        legendary_id = legendary_card[0]
    else:
        # Создаём легендарную карту с названием по умолчанию (админ может переименовать позже)
        # Возьмём имя коллекции для названия
        c.execute("SELECT name FROM collections WHERE id = %s", (collection_id,))
        col_name = c.fetchone()[0]
        # Создаём заглушку для легендарной карты (без файла) – позже админ сможет заменить
        # Но мы попросим админа создать легендарную карту отдельно. Вместо этого просто выдадим существующую, если есть.
        # Лучше: если нет легендарной в коллекции, то мы не можем выдать. Значит, админ должен создать легендарную карту заранее.
        # Поэтому проверяем наличие, если нет – выдаём ошибку.
        conn.close()
        return False, "В этой коллекции нет Легендарной карты. Обратитесь к администратору."

    # Добавляем пользователю легендарную карту
    add_user_card(user_id, legendary_id, 1)
    conn.commit()
    conn.close()
    return True, "Легендарная карта успешно скрафчена!"

# ------------------- КЛАВИАТУРЫ -------------------
def main_inline_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("🎰 Крутить рулетку", callback_data="roulette")],
        [InlineKeyboardButton("⚽️ Сделать ставку", callback_data="make_bet")],
    ])

def admin_reply_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Добавить матч", "❌ Удалить/Завершить матч"],
        ["💸 Выдать денег", "📢 Рассылка"],
        ["🃏 Карточки", "🚪 Выйти с админки"]
    ], resize_keyboard=True)

def admin_cards_keyboard():
    return ReplyKeyboardMarkup([
        ["📁 Создать коллекцию", "🃏 Добавить карточку"],
        ["🎁 Выдать карточку игроку", "🔙 Назад в админку"]
    ], resize_keyboard=True)

# Редкости с эмодзи
RARITY_EMOJI = {
    "Редкая": "🔵",
    "Очень редкая": "🟣",
    "Эпическая": "🟠",
    "Мифическая": "🔴",
    "Легендарная": "⭐",
    "Секретная": "💎"
}

# ------------------- ПОЛЬЗОВАТЕЛЬСКАЯ ЧАСТЬ -------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.username, user.first_name)
    text = "Здравствуйте! Бот Букмекерской Компании Grand Pari именно тут! Выберите что вы хотите сделать:"
    if update.message:
        await update.message.reply_text(text, reply_markup=main_inline_keyboard())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=main_inline_keyboard())

async def user_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    u_data = get_user(user.id, user.username, user.first_name)

    if data == "balance":
        await query.message.reply_text(f"💳 Ваш баланс: **{u_data['balance']} GCoin**", parse_mode="Markdown")
        await query.message.reply_text("Выберите следующее действие:", reply_markup=main_inline_keyboard())

    elif data == "roulette":
        now = int(time.time())
        cooldown = 24 * 3600
        elapsed = now - u_data["last_roulette"]
        if elapsed < cooldown:
            remaining = cooldown - elapsed
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            await query.message.reply_text(f"⏳ Рулетка доступна раз в 24 часа!\nПодождите ещё: **{hours} ч {minutes} мин**", parse_mode="Markdown")
            await query.message.reply_text("Выберите следующее действие:", reply_markup=main_inline_keyboard())
            return

        options = [
            (500, "🎉 Вам выпало +500 GCoin!"),
            (-500, "📉 Вам выпало -500 GCoin!"),
            (-2500, "💥 Вам выпало -2500 GCoin!"),
            (1500, "🚀 Вам выпало +1500 GCoin!")
        ]
        delta, msg = random.choice(options)
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE users SET balance = balance + %s, last_roulette = %s WHERE user_id = %s", (delta, now, user.id))
        conn.commit()
        conn.close()
        new_bal = u_data["balance"] + delta
        await query.message.reply_text(f"🎰 {msg}\n\nВаш новый баланс: **{new_bal} GCoin**", parse_mode="Markdown")
        await query.message.reply_text("Выберите следующее действие:", reply_markup=main_inline_keyboard())

    elif data == "make_bet":
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=RealDictCursor)
        c.execute("SELECT match_id, team1, team2, coef1, coef2 FROM matches WHERE status = 'OPEN'")
        matches = c.fetchall()
        conn.close()
        if not matches:
            await query.message.reply_text("📭 На данный момент нет доступных матчей для ставок.")
            await query.message.reply_text("Выберите следующее действие:", reply_markup=main_inline_keyboard())
            return
        buttons = []
        for m in matches:
            buttons.append([InlineKeyboardButton(f"⚽️ {m['team1']} ({m['coef1']}) vs {m['team2']} ({m['coef2']})", callback_data=f"select_match_{m['match_id']}")])
        await query.message.reply_text("🏆 **Выберите матч для ставки:**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")

    elif data.startswith("select_match_"):
        match_id = int(data.split("_")[2])
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=RealDictCursor)
        c.execute("SELECT team1, team2, coef1, coef2 FROM matches WHERE match_id = %s AND status = 'OPEN'", (match_id,))
        match = c.fetchone()
        conn.close()
        if not match:
            await query.message.reply_text("❌ Этот матч недоступен.")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"Победа {match['team1']} (кэф {match['coef1']})", callback_data=f"place_bet_{match_id}_1_{match['coef1']}")],
            [InlineKeyboardButton(f"Победа {match['team2']} (кэф {match['coef2']})", callback_data=f"place_bet_{match_id}_2_{match['coef2']}")],
        ])
        await query.message.reply_text(f"⚔️ **Матч:** {match['team1']} vs {match['team2']}\nВыберите исход:", reply_markup=kb, parse_mode="Markdown")

    elif data.startswith("place_bet_"):
        parts = data.split("_")
        match_id = int(parts[2])
        team_choice = int(parts[3])
        coef = float(parts[4])
        context.user_data["bet_match_id"] = match_id
        context.user_data["bet_team_choice"] = team_choice
        context.user_data["bet_coef"] = coef
        await query.message.reply_text(f"💰 Ваш баланс: **{u_data['balance']} GCoin**\nВведите сумму ставки текстом:", parse_mode="Markdown")
        return BET_AMOUNT

async def process_bet_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user = update.effective_user
    u_data = get_user(user.id)
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_text("❌ Введите корректную сумму числом!")
        return BET_AMOUNT
    amount = int(text)
    if amount > u_data["balance"]:
        await update.message.reply_text("❌ У вас недостаточно GCoin на балансе! Введите сумму меньше:")
        return BET_AMOUNT
    match_id = context.user_data.get("bet_match_id")
    team_choice = context.user_data.get("bet_team_choice")
    coef = context.user_data.get("bet_coef")
    update_balance(user.id, -amount)
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        "INSERT INTO bets (user_id, match_id, team_choice, amount, coef, status) VALUES (%s, %s, %s, %s, %s, 'PENDING')",
        (user.id, match_id, team_choice, amount, coef)
    )
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Ставка **{amount} GCoin** успешно принята!\nКоэффициент: **{coef}**", parse_mode="Markdown")
    await update.message.reply_text("Выберите следующее действие:", reply_markup=main_inline_keyboard())
    return ConversationHandler.END

# ------------------- КОМАНДА /freegoyda (выдача карточки) -------------------
async def freegoyda_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u_data = get_user(user.id, user.username, user.first_name)
    now = int(time.time())
    cooldown = 24 * 3600
    last_time = u_data.get("last_card_time", 0)
    if now - last_time < cooldown:
        remaining = cooldown - (now - last_time)
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await update.message.reply_text(f"⏳ Вы уже брали карточку сегодня. Следующая будет доступна через **{hours} ч {minutes} мин**.", parse_mode="Markdown")
        return

    # Определяем редкость: Редкая (50%), Очень редкая (25%), Эпическая (15%), Мифическая (10%)
    rand_val = random.random()
    if rand_val < 0.50:
        rarity = "Редкая"
    elif rand_val < 0.75:
        rarity = "Очень редкая"
    elif rand_val < 0.90:
        rarity = "Эпическая"
    else:
        rarity = "Мифическая"

    # Получаем все карты этой редкости
    cards = get_cards_by_rarity(rarity)
    if not cards:
        await update.message.reply_text("😅 В этой редкости пока нет карточек. Попробуйте позже или обратитесь к администратору.")
        return

    chosen = random.choice(cards)
    card_id = chosen['id']
    card_name = chosen['name']
    collection_name = chosen['collection_name']
    file_id = chosen['file_id']

    # Добавляем карточку пользователю
    add_user_card(user.id, card_id, 1)

    # Обновляем время последнего получения
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET last_card_time = %s WHERE user_id = %s", (now, user.id))
    conn.commit()
    conn.close()

    # Отправляем картинку и информацию
    emoji = RARITY_EMOJI.get(rarity, "🎴")
    caption = (
        f"🎉 **Новая карточка!**\n\n"
        f"📛 **Название:** {card_name}\n"
        f"⭐ **Редкость:** {emoji} {rarity}\n"
        f"📁 **Коллекция:** {collection_name}"
    )
    try:
        await update.message.reply_photo(photo=file_id, caption=caption, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка отправки фото: {e}")
        await update.message.reply_text(f"❌ Не удалось отправить картинку карточки. Обратитесь к администратору.\n\n{caption}", parse_mode="Markdown")

# ------------------- КОМАНДА /inventory (инвентарь и крафт) -------------------
async def inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    cards = get_user_cards(user.id)

    if not cards:
        await update.message.reply_text("📭 У вас пока нет карточек. Используйте /freegoyda, чтобы получить первую!")
        return

    # Группируем по коллекциям
    collections = {}
    for c in cards:
        col = c['collection_name']
        if col not in collections:
            collections[col] = []
        collections[col].append(c)

    text = "🎴 **Ваш инвентарь карточек:**\n\n"
    for col_name, items in collections.items():
        text += f"📁 **{col_name}**\n"
        # Группируем по редкости
        rarity_groups = {}
        for it in items:
            r = it['rarity']
            if r not in rarity_groups:
                rarity_groups[r] = []
            rarity_groups[r].append(it)
        for r, list_items in rarity_groups.items():
            emoji = RARITY_EMOJI.get(r, "▪️")
            text += f"  {emoji} *{r}*:\n"
            for item in list_items:
                text += f"    • {item['name']} (x{item['quantity']})\n"
        text += "\n"

    # Проверяем возможность крафта
    craftable = get_mythical_cards_by_collection(user.id)
    keyboard = []
    if craftable:
        text += "🛠 **Вы можете скрафтить Легендарную карту!**\n"
        for col_id, total in craftable.items():
            # Найдём название коллекции
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT name FROM collections WHERE id = %s", (col_id,))
            col_name = c.fetchone()[0]
            conn.close()
            text += f"   - Коллекция «{col_name}»: {total} мифических (нужно 5)\n"
            keyboard.append([InlineKeyboardButton(f"✨ Скрафтить из {col_name}", callback_data=f"craft_{col_id}")])
    else:
        text += "❌ У вас нет 5 мифических карточек одной коллекции для крафта Легендарной."

    # Отправляем сообщение с инвентарём и кнопками крафта
    if keyboard:
        reply_markup = InlineKeyboardMarkup(keyboard)
    else:
        reply_markup = None

    # Удаляем предыдущее сообщение, если оно было (для чистоты)
    if 'last_inv_msg' in context.user_data:
        try:
            await context.bot.delete_message(chat_id=user.id, message_id=context.user_data['last_inv_msg'])
        except:
            pass
    sent = await update.message.reply_text(text, parse_mode="Markdown", reply_markup=reply_markup)
    context.user_data['last_inv_msg'] = sent.message_id

async def inventory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user

    if data.startswith("craft_"):
        collection_id = int(data.split("_")[1])
        # Проверим ещё раз возможность крафта
        craftable = get_mythical_cards_by_collection(user.id)
        if collection_id not in craftable:
            await query.edit_message_text("❌ У вас недостаточно мифических карточек этой коллекции для крафта.")
            return
        # Покажем подтверждение
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, скрафтить", callback_data=f"confirm_craft_{collection_id}")],
            [InlineKeyboardButton("❌ Отмена", callback_data="cancel_craft")]
        ])
        await query.edit_message_text(
            "⚠️ **Внимание!** Вы собираетесь скрафтить Легендарную карту, потратив 5 мифических карточек этой коллекции.\n"
            "Подтвердите действие:",
            parse_mode="Markdown",
            reply_markup=keyboard
        )
        return

    elif data.startswith("confirm_craft_"):
        collection_id = int(data.split("_")[2])
        success, msg = craft_legendary(user.id, collection_id)
        if success:
            await query.edit_message_text(f"✅ {msg}", parse_mode="Markdown")
            # Обновляем инвентарь (вызовем /inventory заново)
            await inventory_command(update, context)
        else:
            await query.edit_message_text(f"❌ {msg}", parse_mode="Markdown")

    elif data == "cancel_craft":
        await query.edit_message_text("❌ Крафт отменён.")
        # Покажем инвентарь заново
        await inventory_command(update, context)

# ------------------- АДМИН-ПАНЕЛЬ (существующая) -------------------
async def adminka_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if is_admin(user_id):
        await update.message.reply_text("⚙️ Вы уже вошли в админ-панель!", reply_markup=admin_reply_keyboard())
        return ConversationHandler.END
    await update.message.reply_text("🔑 Введите логин админа:")
    return ADMIN_LOGIN_STATE

async def admin_login_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["admin_login"] = update.message.text.strip()
    await update.message.reply_text("🔒 Введите пароль:")
    return ADMIN_PASSWORD_STATE

async def admin_password_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    login = context.user_data.get("admin_login")
    password = update.message.text.strip()
    if login == ADMIN_LOGIN and password == ADMIN_PASSWORD:
        add_admin(update.effective_user.id)
        await update.message.reply_text("✅ Вход выполнен успешно!", reply_markup=admin_reply_keyboard())
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Неверный логин или пароль! Попробуйте снова через /adminka")
        return ConversationHandler.END

# ------------------- АДМИН-ФУНКЦИОНАЛ (существующий + карточки) -------------------
async def admin_buttons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        return
    text = update.message.text

    if text == "🚪 Выйти с админки":
        remove_admin(user_id)
        await update.message.reply_text("🚪 Вы вышли из админ-панели.", reply_markup=ReplyKeyboardRemove())
        await update.message.reply_text("Главное меню:", reply_markup=main_inline_keyboard())

    elif text == "➕ Добавить матч":
        await update.message.reply_text("Введите название **Команды 1**:", parse_mode="Markdown")
        return ADD_MATCH_TEAM1

    elif text == "❌ Удалить/Завершить матч":
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=RealDictCursor)
        c.execute("SELECT match_id, team1, team2 FROM matches WHERE status = 'OPEN'")
        matches = c.fetchall()
        conn.close()
        if not matches:
            await update.message.reply_text("📭 Нет активных матчей.")
            return
        buttons = []
        for m in matches:
            buttons.append([InlineKeyboardButton(f"🏁 Завершить: {m['team1']} vs {m['team2']}", callback_data=f"adm_end_{m['match_id']}")])
            buttons.append([InlineKeyboardButton(f"🗑 Удалить: {m['team1']} vs {m['team2']}", callback_data=f"adm_del_{m['match_id']}")])
        await update.message.reply_text("Выберите действие с матчем:", reply_markup=InlineKeyboardMarkup(buttons))

    elif text == "💸 Выдать денег":
        await update.message.reply_text("Введите username игрока и сумму через пробел.\nПример: `@username 1000`", parse_mode="Markdown")
        return GIVE_MONEY

    elif text == "📢 Рассылка":
        await update.message.reply_text("Введите текст сообщения для рассылки всем пользователям:")
        return BROADCAST_MSG

    elif text == "🃏 Карточки":
        context.user_data["admin_cards_mode"] = True
        await update.message.reply_text("🃏 **Раздел управления карточками**", reply_markup=admin_cards_keyboard(), parse_mode="Markdown")
        return ConversationHandler.END  # Переходим в состояние админ-карточек (будет обрабатываться отдельным хендлером)

    else:
        await update.message.reply_text("Используйте кнопки меню.", reply_markup=admin_reply_keyboard())

# ------------------- АДМИН: РАЗДЕЛ КАРТОЧЕК -------------------
async def admin_cards_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик кнопок в разделе карточек"""
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("⛔ Не авторизован.")
        return ConversationHandler.END

    text = update.message.text

    if text == "🔙 Назад в админку":
        context.user_data["admin_cards_mode"] = False
        await update.message.reply_text("🔙 Возврат в основную админ-панель.", reply_markup=admin_reply_keyboard())
        return ConversationHandler.END

    elif text == "📁 Создать коллекцию":
        await update.message.reply_text("Введите **название новой коллекции**:", parse_mode="Markdown")
        return CREATE_COLLECTION_NAME

    elif text == "🃏 Добавить карточку":
        # Сначала выберем редкость
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"{RARITY_EMOJI['Редкая']} Редкая", callback_data="addcard_rarity_Редкая")],
            [InlineKeyboardButton(f"{RARITY_EMOJI['Очень редкая']} Очень редкая", callback_data="addcard_rarity_Очень редкая")],
            [InlineKeyboardButton(f"{RARITY_EMOJI['Эпическая']} Эпическая", callback_data="addcard_rarity_Эпическая")],
            [InlineKeyboardButton(f"{RARITY_EMOJI['Мифическая']} Мифическая", callback_data="addcard_rarity_Мифическая")],
            [InlineKeyboardButton(f"{RARITY_EMOJI['Легендарная']} Легендарная", callback_data="addcard_rarity_Легендарная")],
            [InlineKeyboardButton(f"{RARITY_EMOJI['Секретная']} Секретная", callback_data="addcard_rarity_Секретная")],
        ])
        await update.message.reply_text("Выберите **редкость** карточки:", reply_markup=keyboard, parse_mode="Markdown")
        return ADD_CARD_RARITY

    elif text == "🎁 Выдать карточку игроку":
        # Покажем список карточек для выбора
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=RealDictCursor)
        c.execute("""
            SELECT c.id, c.name, c.rarity, col.name as collection_name
            FROM cards c
            JOIN collections col ON c.collection_id = col.id
            ORDER BY col.name, c.rarity, c.name
        """)
        cards = c.fetchall()
        conn.close()
        if not cards:
            await update.message.reply_text("❌ Нет карточек в базе. Сначала добавьте карточки.")
            return ConversationHandler.END
        # Создаём кнопки с ID карточки
        buttons = []
        for card in cards:
            emoji = RARITY_EMOJI.get(card['rarity'], "🎴")
            label = f"{emoji} {card['name']} ({card['collection_name']}, {card['rarity']})"
            buttons.append([InlineKeyboardButton(label, callback_data=f"givecard_{card['id']}")])
        # Кнопка отмены
        buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_give_card")])
        await update.message.reply_text("Выберите **карточку** для выдачи:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
        return GIVE_CARD_SELECT

    else:
        await update.message.reply_text("Используйте кнопки меню.", reply_markup=admin_cards_keyboard())
        return ConversationHandler.END

async def create_collection_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Название не может быть пустым. Попробуйте снова:")
        return CREATE_COLLECTION_NAME
    new_id = create_collection(name)
    if new_id:
        await update.message.reply_text(f"✅ Коллекция **{name}** создана! (ID: {new_id})", parse_mode="Markdown", reply_markup=admin_cards_keyboard())
        return ConversationHandler.END
    else:
        await update.message.reply_text("❌ Коллекция с таким названием уже существует. Введите другое название:")
        return CREATE_COLLECTION_NAME

async def add_card_rarity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rarity = query.data.split("_")[2]
    context.user_data["new_card_rarity"] = rarity
    # Теперь предложим выбрать коллекцию
    collections = get_collections()
    if not collections:
        await query.edit_message_text("❌ Нет коллекций. Сначала создайте коллекцию.")
        return ConversationHandler.END
    buttons = []
    for col in collections:
        buttons.append([InlineKeyboardButton(col['name'], callback_data=f"addcard_col_{col['id']}")])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="cancel_add_card")])
    await query.edit_message_text("Выберите **коллекцию** для карточки:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return ADD_CARD_COLLECTION

async def add_card_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    col_id = int(query.data.split("_")[2])
    context.user_data["new_card_collection"] = col_id
    await query.edit_message_text("Введите **название** карточки:", parse_mode="Markdown")
    return ADD_CARD_NAME

async def add_card_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Название не может быть пустым. Введите снова:")
        return ADD_CARD_NAME
    context.user_data["new_card_name"] = name
    await update.message.reply_text("📤 Теперь отправьте **изображение** карточки (фото):")
    return ADD_CARD_IMAGE

async def add_card_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ Пожалуйста, отправьте именно фотографию.")
        return ADD_CARD_IMAGE
    file_id = update.message.photo[-1].file_id
    rarity = context.user_data.get("new_card_rarity")
    collection_id = context.user_data.get("new_card_collection")
    name = context.user_data.get("new_card_name")
    if not all([rarity, collection_id, name]):
        await update.message.reply_text("❌ Ошибка: не все данные заполнены. Начните заново.")
        return ConversationHandler.END
    add_card(collection_id, rarity, name, file_id)
    await update.message.reply_text(f"✅ Карточка **{name}** добавлена!", parse_mode="Markdown", reply_markup=admin_cards_keyboard())
    context.user_data.clear()
    return ConversationHandler.END

async def give_card_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == "cancel_give_card":
        await query.edit_message_text("❌ Операция отменена.")
        return ConversationHandler.END
    card_id = int(data.split("_")[1])
    context.user_data["give_card_id"] = card_id
    await query.edit_message_text("Введите **username** игрока (без @) или его **ID** (число):")
    return GIVE_CARD_USER

async def give_card_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text.strip()
    user_id = None
    # Пробуем как ID
    if user_input.isdigit():
        user_id = int(user_input)
    else:
        # Ищем по username
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT user_id FROM users WHERE username = %s", (user_input,))
        row = c.fetchone()
        conn.close()
        if row:
            user_id = row[0]
    if user_id is None:
        await update.message.reply_text("❌ Пользователь не найден. Попробуйте снова или введите ID.")
        return GIVE_CARD_USER

    card_id = context.user_data.get("give_card_id")
    if not card_id:
        await update.message.reply_text("❌ Ошибка: карточка не выбрана. Начните заново.")
        return ConversationHandler.END

    # Добавляем карточку пользователю
    add_user_card(user_id, card_id, 1)
    # Получим информацию о карточке для ответа
    card = get_card_by_id(card_id)
    if card:
        await update.message.reply_text(
            f"✅ Карточка **{card['name']}** ({card['rarity']}) выдана пользователю.\n"
            f"Коллекция: {card['collection_name']}",
            parse_mode="Markdown",
            reply_markup=admin_cards_keyboard()
        )
    else:
        await update.message.reply_text("✅ Карточка выдана.", reply_markup=admin_cards_keyboard())
    context.user_data.clear()
    return ConversationHandler.END

async def cancel_add_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Добавление карточки отменено.")
    return ConversationHandler.END

# ------------------- ОСТАЛЬНЫЕ АДМИН-ФУНКЦИИ (существующие) -------------------
async def add_match_t1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["m_t1"] = update.message.text.strip()
    await update.message.reply_text("Введите название **Команды 2**:", parse_mode="Markdown")
    return ADD_MATCH_TEAM2

async def add_match_t2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["m_t2"] = update.message.text.strip()
    await update.message.reply_text("Введите коэффициент на **Победу Команды 1** (например 1.85):", parse_mode="Markdown")
    return ADD_MATCH_COEF1

async def add_match_c1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        c1 = float(update.message.text.replace(",", "."))
        context.user_data["m_c1"] = c1
        await update.message.reply_text("Введите коэффициент на **Победу Команды 2** (например 2.10):", parse_mode="Markdown")
        return ADD_MATCH_COEF2
    except ValueError:
        await update.message.reply_text("❌ Введите число (например 1.85):")
        return ADD_MATCH_COEF1

async def add_match_c2(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        c2 = float(update.message.text.replace(",", "."))
        t1 = context.user_data["m_t1"]
        t2 = context.user_data["m_t2"]
        c1 = context.user_data["m_c1"]
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO matches (team1, team2, coef1, coef2) VALUES (%s, %s, %s, %s)",
            (t1, t2, c1, c2)
        )
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Матч создан!\n⚽️ {t1} ({c1}) vs {t2} ({c2})", reply_markup=admin_reply_keyboard())
        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text("❌ Введите число (например 2.10):")
        return ADD_MATCH_COEF2

async def admin_match_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("adm_del_"):
        match_id = int(data.split("_")[2])
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("DELETE FROM matches WHERE match_id = %s", (match_id,))
        conn.commit()
        conn.close()
        await query.message.edit_text("🗑 Матч удален!")

    elif data.startswith("adm_end_"):
        match_id = int(data.split("_")[2])
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=RealDictCursor)
        c.execute("SELECT team1, team2 FROM matches WHERE match_id = %s", (match_id,))
        m = c.fetchone()
        conn.close()
        if not m:
            await query.message.edit_text("❌ Матч не найден.")
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🏆 Победил {m['team1']} (Команда 1)", callback_data=f"settle_{match_id}_1")],
            [InlineKeyboardButton(f"🏆 Победил {m['team2']} (Команда 2)", callback_data=f"settle_{match_id}_2")],
        ])
        await query.message.edit_text(f"Выберите победителя матча {m['team1']} vs {m['team2']}:", reply_markup=kb)

    elif data.startswith("settle_"):
        parts = data.split("_")
        match_id = int(parts[1])
        winner = int(parts[2])
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("UPDATE matches SET status = 'FINISHED', winner = %s WHERE match_id = %s", (winner, match_id))
        c.execute("SELECT bet_id, user_id, team_choice, amount, coef FROM bets WHERE match_id = %s AND status = 'PENDING'", (match_id,))
        bets = c.fetchall()
        for b in bets:
            bet_id, u_id, t_choice, amount, coef = b
            if t_choice == winner:
                win_amount = int(amount * coef)
                c.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (win_amount, u_id))
                c.execute("UPDATE bets SET status = 'WON' WHERE bet_id = %s", (bet_id,))
                try:
                    await context.bot.send_message(u_id, f"🎉 Ваша ставка на матч выиграла!\nЗачислено: **{win_amount} GCoin**", parse_mode="Markdown")
                except Exception:
                    pass
            else:
                c.execute("UPDATE bets SET status = 'LOST' WHERE bet_id = %s", (bet_id,))
                try:
                    await context.bot.send_message(u_id, f"❌ Ваша ставка на матч не сыграла.")
                except Exception:
                    pass
        conn.commit()
        conn.close()
        await query.message.edit_text("✅ Матч завершен, выигрыши выплачены!")

async def process_give_money(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().split()
    if len(text) != 2:
        await update.message.reply_text("❌ Неверный формат! Введите: `@username 1000`", parse_mode="Markdown")
        return GIVE_MONEY
    username = text[0].replace("@", "")
    try:
        amount = int(text[1])
    except ValueError:
        await update.message.reply_text("❌ Сумма должна быть целым числом!")
        return GIVE_MONEY
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT user_id, balance FROM users WHERE username = %s", (username,))
    row = c.fetchone()
    if not row:
        conn.close()
        await update.message.reply_text("❌ Пользователь с таким username не найден в базе бота.")
        return ConversationHandler.END
    target_id = row["user_id"]
    c.execute("UPDATE users SET balance = balance + %s WHERE user_id = %s", (amount, target_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ Пользователю @{username} выдано **{amount} GCoin**!", reply_markup=admin_reply_keyboard(), parse_mode="Markdown")
    try:
        await context.bot.send_message(target_id, f"🎁 Администратор зачислил вам **{amount} GCoin**!", parse_mode="Markdown")
    except Exception:
        pass
    return ConversationHandler.END

async def process_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    users = c.fetchall()
    conn.close()
    count = 0
    for u in users:
        try:
            await context.bot.send_message(u[0], text)
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await update.message.reply_text(f"📢 Рассылка завершена! Сообщение получили **{count}** пользователей.", reply_markup=admin_reply_keyboard(), parse_mode="Markdown")
    return ConversationHandler.END

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Действие отменено.", reply_markup=main_inline_keyboard())
    return ConversationHandler.END

# ------------------- MAIN -------------------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # ---- Существующие хендлеры ----
    admin_auth_handler = ConversationHandler(
        entry_points=[CommandHandler("adminka", adminka_start)],
        states={
            ADMIN_LOGIN_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_login_step)],
            ADMIN_PASSWORD_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_password_step)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)]
    )

    add_match_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить матч$"), admin_buttons_handler)],
        states={
            ADD_MATCH_TEAM1: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_match_t1)],
            ADD_MATCH_TEAM2: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_match_t2)],
            ADD_MATCH_COEF1: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_match_c1)],
            ADD_MATCH_COEF2: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_match_c2)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)]
    )

    bet_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(user_callback_handler, pattern="^place_bet_")],
        states={
            BET_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_bet_amount)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)]
    )

    give_money_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^💸 Выдать денег$"), admin_buttons_handler)],
        states={
            GIVE_MONEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_give_money)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)]
    )

    broadcast_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📢 Рассылка$"), admin_buttons_handler)],
        states={
            BROADCAST_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_broadcast)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)]
    )

    # ---- Новые хендлеры для карточек (админ) ----
    admin_cards_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🃏 Карточки$"), admin_buttons_handler)],
        states={
            CREATE_COLLECTION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, create_collection_name)],
            ADD_CARD_RARITY: [CallbackQueryHandler(add_card_rarity, pattern="^addcard_rarity_")],
            ADD_CARD_COLLECTION: [CallbackQueryHandler(add_card_collection, pattern="^addcard_col_")],
            ADD_CARD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_card_name)],
            ADD_CARD_IMAGE: [MessageHandler(filters.PHOTO, add_card_image)],
            GIVE_CARD_SELECT: [CallbackQueryHandler(give_card_select, pattern="^(givecard_|cancel_give_card)")],
            GIVE_CARD_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, give_card_user)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
        map_to_parent={
            # После завершения возвращаемся в основное админ-меню
            ConversationHandler.END: ConversationHandler.END
        }
    )

    # ---- Хендлер для кнопок внутри раздела карточек (не в диалогах) ----
    app.add_handler(MessageHandler(filters.Regex("^(📁 Создать коллекцию|🃏 Добавить карточку|🎁 Выдать карточку игроку|🔙 Назад в админку)$"), admin_cards_menu))

    # ---- Команды пользователя ----
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("freegoyda", freegoyda_command))
    app.add_handler(CommandHandler("inventory", inventory_command))

    # ---- Колбэки ----
    app.add_handler(CallbackQueryHandler(admin_match_callback, pattern="^(adm_|settle_)"))
    app.add_handler(CallbackQueryHandler(user_callback_handler, pattern="^(balance|roulette|make_bet|select_match_|place_bet_)"))
    app.add_handler(CallbackQueryHandler(inventory_callback, pattern="^(craft_|confirm_craft_|cancel_craft)"))

    # ---- Добавляем все диалоги ----
    app.add_handler(admin_auth_handler)
    app.add_handler(add_match_handler)
    app.add_handler(bet_handler)
    app.add_handler(give_money_handler)
    app.add_handler(broadcast_handler)
    app.add_handler(admin_cards_handler)  # новый

    # Обработчик для выхода из админки и удаления матчей (кнопки)
    app.add_handler(MessageHandler(filters.Regex("^(❌ Удалить/Завершить матч|🚪 Выйти с админки)$"), admin_buttons_handler))

    logger.info("Бот запущен с PostgreSQL и системой карточек...")
    app.run_polling()

if __name__ == "__main__":
    main()
