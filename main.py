import os
import logging
import random
import time
import asyncio
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime

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

# Получаем DATABASE_URL из окружения
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL не задан! Создайте базу PostgreSQL в Railway.")

# Состояния ConversationHandler
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
    CREATE_COLLECTION_STATE,
    ADD_CARD_RARITY,
    ADD_CARD_COLLECTION,
    ADD_CARD_NAME,
    ADD_CARD_IMAGE,
    GRANT_CARD_INPUT,
) = range(15)

# ---------- БАЗА ДАННЫХ (PostgreSQL) ----------
def get_db_connection():
    return psycopg2.connect(DATABASE_URL)

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance INTEGER DEFAULT 5000,
            last_roulette INTEGER DEFAULT 0,
            last_card_claim INTEGER DEFAULT 0
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
    c.execute('''
        CREATE TABLE IF NOT EXISTS collections (
            collection_id SERIAL PRIMARY KEY,
            name TEXT UNIQUE NOT NULL
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            card_id SERIAL PRIMARY KEY,
            collection_id INTEGER REFERENCES collections(collection_id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            rarity TEXT NOT NULL,
            image_file_id TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_cards (
            user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
            card_id INTEGER REFERENCES cards(card_id) ON DELETE CASCADE,
            quantity INTEGER DEFAULT 1,
            PRIMARY KEY (user_id, card_id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Вспомогательные функции
def get_user(user_id, username="", first_name=""):
    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    if not row:
        c.execute(
            "INSERT INTO users (user_id, username, first_name, balance, last_roulette, last_card_claim) VALUES (%s, %s, %s, 5000, 0, 0)",
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

# ---------- КЛАВИАТУРЫ ----------
def main_inline_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Баланс", callback_data="balance")],
        [InlineKeyboardButton("🎰 Крутить рулетку", callback_data="roulette")],
        [InlineKeyboardButton("⚽️ Сделать ставку", callback_data="make_bet")],
        [InlineKeyboardButton("🎒 Инвентарь карточек", callback_data="inventory")],
    ])

def admin_reply_keyboard():
    return ReplyKeyboardMarkup([
        ["➕ Добавить матч", "❌ Удалить/Завершить матч"],
        ["💸 Выдать денег", "📢 Рассылка"],
        ["🃏 Карточки", "🚪 Выйти с админки"]
    ], resize_keyboard=True)

def card_admin_reply_keyboard():
    return ReplyKeyboardMarkup([
        ["📁 Создать коллекцию", "➕ Добавить карточку"],
        ["🎁 Выдать карточку игроку", "⬅️ Выйти в меню админки"]
    ], resize_keyboard=True)

def inventory_inline_keyboard(cards):
    buttons = []
    # Проверяем карточки для кнопки крафта
    for c in cards:
        if c['rarity'] == 'Мифическая' and c['quantity'] >= 5:
            buttons.append([InlineKeyboardButton(f"🔨 Скрафтить Легендарную ({c['name']})", callback_data=f"craft_mythic_{c['card_id']}")])
    buttons.append([InlineKeyboardButton("🔄 Обновить инвентарь", callback_data="refresh_inventory")])
    return InlineKeyboardMarkup(buttons)

# ---------- ПОЛЬЗОВАТЕЛЬСКАЯ ЧАСТЬ ----------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.username, user.first_name)
    text = "Здравствуйте! Бот Букмекерской Компании Grand Pari именно тут! Выберите что вы хотите сделать:"
    if update.message:
        await update.message.reply_text(text, reply_markup=main_inline_keyboard())
    else:
        await update.callback_query.message.reply_text(text, reply_markup=main_inline_keyboard())

# --- Система карточек: /freegoyda ---
async def freegoyda_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    u_data = get_user(user.id, user.username, user.first_name)
    now = int(time.time())
    cooldown = 24 * 3600
    elapsed = now - u_data.get("last_card_claim", 0)

    if elapsed < cooldown:
        remaining = cooldown - elapsed
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await update.message.reply_text(f"⏳ Карточку можно получать раз в 24 часа!\nПодождите ещё: **{hours} ч {minutes} мин**", parse_mode="Markdown")
        return

    # Выпадают только Редкая, Очень редкая, Эпическая, Мифическая
    rarities = ["Редкая", "Очень редкая", "Эпическая", "Мифическая"]
    weights = [55, 30, 12, 3] # Вероятности выпадения
    chosen_rarity = random.choices(rarities, weights=weights, k=1)[0]

    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT c.card_id, c.name, c.rarity, c.image_file_id, col.name as col_name FROM cards c JOIN collections col ON c.collection_id = col.collection_id WHERE c.rarity = %s", (chosen_rarity,))
    available_cards = c.fetchall()

    if not available_cards:
        conn.close()
        await update.message.reply_text("📭 В данный момент в игре нет доступных карточек для выпадения. Администратор скоро их добавит!")
        return

    card = random.choice(available_cards)

    # Выдаем карточку игроку и обновляем КД
    c_write = conn.cursor()
    c_write.execute("""
        INSERT INTO user_cards (user_id, card_id, quantity) VALUES (%s, %s, 1)
        ON CONFLICT (user_id, card_id) DO UPDATE SET quantity = user_cards.quantity + 1
    """, (user.id, card['card_id']))
    c_write.execute("UPDATE users SET last_card_claim = %s WHERE user_id = %s", (now, user.id))
    conn.commit()
    conn.close()

    caption = (
        "✨ **Новая карточка!**\n\n"
        f"🏷 Название: {card['name']}\n"
        f"🌟 Редкость: {card['rarity']}\n"
        f"📁 Коллекция: {card['col_name']}"
    )

    if card['image_file_id']:
        await update.message.reply_photo(photo=card['image_file_id'], caption=caption, parse_mode="Markdown")
    else:
        await update.message.reply_text(caption, parse_mode="Markdown")

# --- Система карточек: /inventory ---
async def inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user.id, user.username, user.first_name)
    await show_or_update_inventory(update, context, is_new=True)

async def show_or_update_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE, is_new=False):
    query = update.callback_query
    user = query.from_user if query else update.effective_user

    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("""
        SELECT uc.card_id, c.name, c.rarity, c.image_file_id, col.name as col_name, uc.quantity 
        FROM user_cards uc 
        JOIN cards c ON uc.card_id = c.card_id 
        JOIN collections col ON c.collection_id = col.collection_id 
        WHERE uc.user_id = %s 
        ORDER BY col.name, c.rarity
    """, (user.id,))
    cards = c.fetchall()
    conn.close()

    text = "🎒 **Ваш инвентарь карточек:**\n\n"
    if not cards:
        text += "У вас пока нет карточек. Получите первую по команде /freegoyda!"
    else:
        for card in cards:
            text += f"▪️ [{card['rarity']}] **{card['name']}** ({card['col_name']}) — `x{card['quantity']}`\n"

    markup = inventory_inline_keyboard(cards)

    if query:
        await query.answer()
        try:
            await query.message.edit_text(text, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            # Если сообщение содержало фото или не изменилось текст
            await query.message.delete()
            await context.bot.send_message(chat_id=user.id, text=text, reply_markup=markup, parse_mode="Markdown")
    else:
        if is_new and update.message:
            await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")

async def user_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = query.from_user
    u_data = get_user(user.id, user.username, user.first_name)

    if data == "balance":
        await query.answer()
        await query.message.reply_text(f"💳 Ваш баланс: **{u_data['balance']} GCoin**", parse_mode="Markdown")
        await query.message.reply_text("Выберите следующее действие:", reply_markup=main_inline_keyboard())

    elif data == "roulette":
        await query.answer()
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
        await query.answer()
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

    elif data == "inventory":
        await show_or_update_inventory(update, context)

    elif data == "refresh_inventory":
        await show_or_update_inventory(update, context)

    elif data.startswith("craft_mythic_"):
        card_id = int(data.split("_")[2])
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=RealDictCursor)
        # Проверяем количество мифической карточки
        c.execute("SELECT quantity, c.collection_id FROM user_cards uc JOIN cards c ON uc.card_id = c.card_id WHERE uc.user_id = %s AND uc.card_id = %s", (user.id, card_id))
        res = c.fetchone()
        
        if not res or res['quantity'] < 5:
            conn.close()
            await query.answer("❌ У вас недостаточно таких карточек (нужно 5 штук)!", show_alert=True)
            return

        col_id = res['collection_id']
        # Ищем легендарную карточку в этой же коллекции
        c.execute("SELECT card_id, name, image_file_id FROM cards WHERE collection_id = %s AND rarity = 'Легендарная' LIMIT 1", (col_id,))
        leg_card = c.fetchone()

        if not leg_card:
            conn.close()
            await query.answer("❌ В этой коллекции еще не создана легендарная карточка!", show_alert=True)
            return

        # Списываем 5 мифических и добавляем 1 легендарную
        c_write = conn.cursor()
        c_write.execute("UPDATE user_cards SET quantity = quantity - 5 WHERE user_id = %s AND card_id = %s", (user.id, card_id))
        c_write.execute("""
            INSERT INTO user_cards (user_id, card_id, quantity) VALUES (%s, %s, 1)
            ON CONFLICT (user_id, card_id) DO UPDATE SET quantity = user_cards.quantity + 1
        """, (user.id, leg_card['card_id']))
        # Удаляем запись если количество стало 0
        c_write.execute("DELETE FROM user_cards WHERE user_id = %s AND card_id = %s AND quantity <= 0", (user.id, card_id))
        conn.commit()
        conn.close()

        await query.answer("🎉 Успешный крафт легендарной карточки!", show_alert=True)
        
        # Удаляем старое инвентарное сообщение и шлем новую карточку
        try:
            await query.message.delete()
        except Exception:
            pass

        congrats_text = f"👑 **Поздравляем с успешным крафтом!**\n\nВы получили легендарную карточку: **{leg_card['name']}**"
        if leg_card['image_file_id']:
            await context.bot.send_photo(chat_id=user.id, photo=leg_card['image_file_id'], caption=congrats_text, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=user.id, text=congrats_text, parse_mode="Markdown")

        # Открываем обновленный инвентарь новым сообщением
        await show_or_update_inventory(update, context, is_new=True)

    elif data.startswith("select_match_"):
        await query.answer()
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
        await query.answer()
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

# ---------- АДМИН-ПАНЕЛЬ ----------
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

# ---------- АДМИН-ФУНКЦИОНАЛ ----------
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
        await update.message.reply_text("🃏 **Управление карточками:**\nВыберите нужный пункт меню:", reply_markup=card_admin_reply_keyboard(), parse_mode="Markdown")

    elif text == "📁 Создать коллекцию":
        await update.message.reply_text("📁 Введите название новой коллекции:")
        return CREATE_COLLECTION_STATE

    elif text == "➕ Добавить карточку":
        conn = get_db_connection()
        c = conn.cursor(cursor_factory=RealDictCursor)
        c.execute("SELECT * FROM collections")
        cols = c.fetchall()
        conn.close()

        if not cols:
            await update.message.reply_text("❌ Сначала создайте хотя бы одну коллекцию!", reply_markup=card_admin_reply_keyboard())
            return

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⚪ Редкая", callback_data="rarity_Редкая")],
            [InlineKeyboardButton("🔵 Очень редкая", callback_data="rarity_Очень редкая")],
            [InlineKeyboardButton("🟣 Эпическая", callback_data="rarity_Эпическая")],
            [InlineKeyboardButton("🟠 Мифическая", callback_data="rarity_Мифическая")],
            [InlineKeyboardButton("🟡 Легендарная", callback_data="rarity_Легендарная")],
            [InlineKeyboardButton("🔴 Секретная", callback_data="rarity_Секретная")],
        ])
        await update.message.reply_text("✨ Выберите редкость карточки:", reply_markup=kb)
        return ADD_CARD_RARITY

    elif text == "🎁 Выдать карточку игроку":
        await update.message.reply_text("🎁 Введите `@username ID_карточки` (например: `@player 5`):", parse_mode="Markdown")
        return GRANT_CARD_INPUT

    elif text == "⬅️ Выйти в меню админки":
        await update.message.reply_text("⚙️ Админ-панель:", reply_markup=admin_reply_keyboard())

# --- Управление карточками (Админка) ---
async def process_create_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    col_name = update.message.text.strip()
    conn = get_db_connection()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO collections (name) VALUES (%s)", (col_name,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Коллекция **{col_name}** успешно создана!", reply_markup=card_admin_reply_keyboard(), parse_mode="Markdown")
    except Exception:
        conn.close()
        await update.message.reply_text("❌ Такая коллекция уже существует или произошла ошибка.", reply_markup=card_admin_reply_keyboard())
    return ConversationHandler.END

async def process_card_rarity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    rarity = query.data.split("_")[1]
    context.user_data["new_card_rarity"] = rarity

    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT * FROM collections")
    cols = c.fetchall()
    conn.close()

    buttons = []
    for col in cols:
        buttons.append([InlineKeyboardButton(f"📁 {col['name']}", callback_data=f"cardcol_{col['collection_id']}")])

    await query.message.edit_text(f"Вы выбрали редкость: **{rarity}**\n\n📁 Выберите коллекцию:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
    return ADD_CARD_COLLECTION

async def process_card_collection_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    col_id = int(query.data.split("_")[1])
    context.user_data["new_card_col_id"] = col_id

    await query.message.edit_text("🏷 Введите название карточки:")
    return ADD_CARD_NAME

async def process_card_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    context.user_data["new_card_name"] = name
    await update.message.reply_text("🖼 Отправьте картинку (фотографию) для этой карточки:")
    return ADD_CARD_IMAGE

async def process_card_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ Пожалуйста, отправьте именно фотографию!")
        return ADD_CARD_IMAGE

    file_id = update.message.photo[-1].file_id
    rarity = context.user_data.get("new_card_rarity")
    col_id = context.user_data.get("new_card_col_id")
    name = context.user_data.get("new_card_name")

    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT INTO cards (collection_id, name, rarity, image_file_id) VALUES (%s, %s, %s, %s)", (col_id, name, rarity, file_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Карточка **{name}** ({rarity}) успешно добавлена!", reply_markup=card_admin_reply_keyboard(), parse_mode="Markdown")
    return ConversationHandler.END

async def process_grant_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().split()
    if len(text) != 2:
        await update.message.reply_text("❌ Неверный формат! Введите: `@username ID`", parse_mode="Markdown")
        return GRANT_CARD_INPUT

    username = text[0].replace("@", "")
    try:
        card_id = int(text[1])
    except ValueError:
        await update.message.reply_text("❌ ID карточки должен быть числом!")
        return GRANT_CARD_INPUT

    conn = get_db_connection()
    c = conn.cursor(cursor_factory=RealDictCursor)
    c.execute("SELECT user_id FROM users WHERE username = %s", (username,))
    user_row = c.fetchone()
    if not user_row:
        conn.close()
        await update.message.reply_text("❌ Пользователь с таким username не найден.")
        return card_admin_reply_keyboard()

    target_id = user_row["user_id"]

    # Проверяем существование карточки
    c.execute("SELECT name FROM cards WHERE card_id = %s", (card_id,))
    card_row = c.fetchone()
    if not card_row:
        conn.close()
        await update.message.reply_text("❌ Карточка с таким ID не найдена.")
        return card_admin_reply_keyboard()

    c_write = conn.cursor()
    c_write.execute("""
        INSERT INTO user_cards (user_id, card_id, quantity) VALUES (%s, %s, 1)
        ON CONFLICT (user_id, card_id) DO UPDATE SET quantity = user_cards.quantity + 1
    """, (target_id, card_id))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Пользователю @{username} выдана карточка **{card_row['name']}** (ID: {card_id})!", reply_markup=card_admin_reply_keyboard(), parse_mode="Markdown")
    try:
        await context.bot.send_message(target_id, f"🎁 Администратор выдал вам карточку: **{card_row['name']}**!", parse_mode="Markdown")
    except Exception:
        pass
    return ConversationHandler.END

# --- Добавление матча ---
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

# --- Управление матчами ---
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

# --- Выдача денег ---
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

# --- Рассылка ---
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

# ---------- MAIN ----------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

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

    # Диалог создания коллекции
    create_col_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📁 Создать коллекцию$"), admin_buttons_handler)],
        states={
            CREATE_COLLECTION_STATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_create_collection)]
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)]
    )

    # Диалог добавления карточки
    add_card_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^➕ Добавить карточку$"), admin_buttons_handler)],
        states={
            ADD_CARD_RARITY: [CallbackQueryHandler(process_card_rarity_callback, pattern="^rarity_")],
            ADD_CARD_COLLECTION: [CallbackQueryHandler(process_card_collection_callback, pattern="^cardcol_")],
            ADD_CARD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_card_name)],
            ADD_CARD_IMAGE: [MessageHandler(filters.PHOTO, process_card_image)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)]
    )

    # Диалог выдачи карточки игроку
    grant_card_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🎁 Выдать карточку игроку$"), admin_buttons_handler)],
        states={
            GRANT_CARD_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_grant_card)]
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)]
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("freegoyda", freegoyda_command))
    app.add_handler(CommandHandler("inventory", inventory_command))

    app.add_handler(admin_auth_handler)
    app.add_handler(add_match_handler)
    app.add_handler(bet_handler)
    app.add_handler(give_money_handler)
    app.add_handler(broadcast_handler)
    app.add_handler(create_col_handler)
    app.add_handler(add_card_handler)
    app.add_handler(grant_card_handler)

    app.add_handler(CallbackQueryHandler(admin_match_callback, pattern="^(adm_|settle_)"))
    app.add_handler(CallbackQueryHandler(user_callback_handler))

    app.add_handler(MessageHandler(filters.Regex("^(❌ Удалить/Завершить матч|🚪 Выйти с админки|🃏 Карточки|⬅️ Выйти в меню админки)$"), admin_buttons_handler))

    logger.info("Бот запущен с PostgreSQL и системой карточек...")
    app.run_polling()

if __name__ == "__main__":
    main()
