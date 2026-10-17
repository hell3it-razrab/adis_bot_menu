import os
import io
import time
import telebot
import requests
from threading import Thread
from flask import Flask
from PIL import Image

# ==================== НАСТРОЙКИ ====================
TELEGRAM_BOT_TOKEN = "8712152425:AAFrgtFyLexFis8K6rAxd5CDqXirctVCWp4"

SITE_URL = "https://adis38.ru"
SITE_API_URL = "https://adis38.ru/api_menu.php"
SITE_SETTINGS_URL = "https://dev.adis38.ru/api_settings.php"  # Для тестового сайта
SITE_UPLOAD_BOARD_URL = "https://adis38.ru/api_upload_board.php"
UPLOAD_SECRET = "AdisMenuSecretKey_2026"
BOT_API_KEY = "adis_secret_bot_key_2026"

# Учётные записи сотрудников и суперадмина
ADMIN_USERS = {
    # Главный администратор сети (доступ ко всем филиалам)
    "admin": {
        "password": "11111",
        "branch": "all",
        "role_title": "Главный администратор сети"
    },
    # Точка 1: Декабрьских Событий
    "decab": {
        "password": "11111",
        "branch": "dekabr",
        "role_title": "Кафе Декабрьских Событий, 78и"
    },
    # Точка 2: ТЦ Первомайский (Вампилова)
    "pervo": {
        "password": "22222",
        "branch": "vampilova",
        "role_title": "Кафе ТЦ «Первомайский»"
    },
    # Точка 3: мкр. Берёзовый
    "berez": {
        "password": "33333",
        "branch": "berezovy",
        "role_title": "Кафе мкр. Берёзовый, 131"
    }
}

# Реестр филиалов
BRANCHES = {
    "dekabr": {
        "name": "ТД «Антураж» (Декабрьских Событий, 78и)",
        "short": "Декабрьских Событий",
        "file": "menu_dekabr.json"
    },
    "vampilova": {
        "name": "ТЦ «Первомайский» (ул. Вампилова, 2/2)",
        "short": "Первомайский",
        "file": "menu_vampilova.json"
    },
    "berezovy": {
        "name": "мкр. Берёзовый, 131 (р. п. Маркова)",
        "short": "Берёзовый",
        "file": "menu_berezovy.json"
    }
}

# {uid: {"login": ..., "branch": ..., "role_title": ..., "is_super": bool}}
authenticated_admins = {}

# {uid: {"state": ..., "main_msg_id": ..., ...}}
user_states = {}

MENU_CATEGORIES = {
    "bakery": {
        "title": "🥟 Выпечка и чебуреки",
        "items": [
            ("belyash", "Беляш с мясом", 130),
            ("sosiska", "Сосиска в тесте", 100),
            ("cheb_mix", "Чебурек Mix", 200),
            ("cheb_meat", "Чебурек с мясом", 150),
            ("cheb_cheese", "Чебурек с сыром", 150),
            ("khush_meat", "Хушуур с мясом", 130),
            ("khush_potato", "Хушуур с картофелем", 100),
        ]
    },
    "pies": {
        "title": "🥧 Пирожки и ватрушки",
        "items": [
            ("pie_apple", "Пирожок с яблоком", 50),
            ("pie_egg", "Пирожок с яйцом", 50),
            ("pie_potato", "Пирожок с картошкой", 50),
            ("pie_cabbage", "Пирожок с капустой", 50),
            ("pie_liver", "Пирожок с печенью", 50),
            ("pie_meat", "Пирожок с мясом", 50),
            ("vat_jam", "Ватрушка с джемом", 50),
            ("vat_curd", "Ватрушка с творогом", 50),
        ]
    },
    "pancakes": {
        "title": "🥞 Блины",
        "items": [
            ("panc_milk", "Блин со сгущенкой", 80),
            ("panc_jam", "Блин с джемом", 80),
            ("panc_sour", "Блин со сметаной", 80),
            ("panc_curd", "Блин с творогом", 100),
            ("panc_ham", "Блин с ветчиной/сыром", 110),
            ("panc_honey", "Блин с мёдом", 130),
            ("panc_plain", "Один блин", 25),
        ]
    }
}

ALL_ITEMS = {}
for cat in MENU_CATEGORIES.values():
    for item_id, name, def_price in cat["items"]:
        ALL_ITEMS[item_id] = (name, def_price)

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# ==================== ВЕБ-СЕРВЕР ДЛЯ RENDER ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "Мультифилиальный бот кафе Адис работает 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ==================== УДАЛЕНИЕ СООБЩЕНИЙ ====================
def safe_delete_message(chat_id, message_id):
    if not message_id:
        return
    try:
        bot.delete_message(chat_id, message_id)
    except Exception:
        pass

# ==================== РАБОТА С JSON НА СЕРВЕРЕ ====================
def get_branch_data(branch_id):
    branch_cfg = BRANCHES.get(branch_id, BRANCHES["dekabr"])
    file_name = branch_cfg["file"]
    try:
        url = f"{SITE_URL}/data/{file_name}?t={int(time.time())}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "emergency" not in data or not isinstance(data["emergency"], dict):
                data["emergency"] = {
                    "is_blocked": False,
                    "block_reason": "Приём заказов временно приостановлен по техническим причинам.",
                    "cart_warning": ""
                }
            return data
    except Exception as e:
        print(f"❌ Ошибка чтения {file_name}: {e}")

    return {
        "image_url": "",
        "out_of_stock": [],
        "prices": {},
        "announcement": "",
        "emergency": {
            "is_blocked": False,
            "block_reason": "Приём заказов временно приостановлен по техническим причинам.",
            "cart_warning": ""
        }
    }

def update_branch_data(branch_id, data):
    headers = {"Content-Type": "application/json"}
    try:
        url = f"{SITE_API_URL}?key={BOT_API_KEY}&branch={branch_id}"
        r = requests.post(url, headers=headers, json=data, timeout=15)
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка отправки на Beget: {e}")
        return False

# ==================== КЛАВИАТУРЫ ====================
def branch_select_kb():
    kb = telebot.types.InlineKeyboardMarkup()
    for b_id, b_info in BRANCHES.items():
        kb.add(telebot.types.InlineKeyboardButton(f"📍 {b_info['name']}", callback_data=f"select_branch_{b_id}"))
    kb.add(telebot.types.InlineKeyboardButton("🚪 Выйти из системы", callback_data="menu_logout"))
    return kb

def main_menu_kb(branch_id, is_super=False, emergency_data=None):
    if emergency_data is None:
        emergency_data = get_branch_data(branch_id).get("emergency", {})
    
    is_blocked = emergency_data.get("is_blocked", False)
    em_btn_text = "🚨 Режим ЧП: ЗАКРЫТО" if is_blocked else "🚨 Режим ЧП / Стоп-заказ"

    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("📸 Обновить фото доски", callback_data="hint_photo"))
    kb.add(telebot.types.InlineKeyboardButton("📢 Объявление филиала", callback_data="manage_announcement"))
    kb.add(telebot.types.InlineKeyboardButton(em_btn_text, callback_data="manage_emergency"))
    kb.add(telebot.types.InlineKeyboardButton("📦 Стоп-лист (Наличие)", callback_data="categories_stock"))
    kb.add(telebot.types.InlineKeyboardButton("💰 Редактор цен", callback_data="categories_price"))
    
    # Кнопки суперадминистратора
    if is_super:
        kb.add(telebot.types.InlineKeyboardButton("🔄 Сменить филиал управления", callback_data="switch_branch"))
        kb.add(telebot.types.InlineKeyboardButton("🎨 Тема сайта (Оформление)", callback_data="menu_themes"))
        
    kb.add(telebot.types.InlineKeyboardButton("🌐 Открыть сайт кафе", url=SITE_URL))
    kb.add(telebot.types.InlineKeyboardButton("🚪 Выйти", callback_data="menu_logout"))
    return kb

def themes_kb():
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        telebot.types.InlineKeyboardButton("🎃 Хэллоуин", callback_data="set_theme_halloween"),
        telebot.types.InlineKeyboardButton("🍂 Осень", callback_data="set_theme_autumn")
    )
    kb.add(
        telebot.types.InlineKeyboardButton("❄️ Зима", callback_data="set_theme_winter"),
        telebot.types.InlineKeyboardButton("🎄 Новый Год", callback_data="set_theme_new_year")
    )
    kb.add(telebot.types.InlineKeyboardButton("🥟 Фирменная (Стандарт)", callback_data="set_theme_default"))
    kb.add(telebot.types.InlineKeyboardButton("⬅️ В главное меню", callback_data="back_main"))
    return kb

def cancel_input_kb(back_action="back_main"):
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("❌ Отмена", callback_data=back_action))
    return kb

def emergency_kb(is_blocked, has_warning):
    kb = telebot.types.InlineKeyboardMarkup()
    if is_blocked:
        kb.add(telebot.types.InlineKeyboardButton("🟢 Возобновить приём заказов", callback_data="toggle_block_orders"))
    else:
        kb.add(telebot.types.InlineKeyboardButton("🔴 Экстренно ОСТАНОВИТЬ заказы", callback_data="toggle_block_orders"))
    
    kb.add(telebot.types.InlineKeyboardButton("✏️ Изменить причину блокировки", callback_data="set_block_reason"))
    kb.add(telebot.types.InlineKeyboardButton("⚠️ Текст у корзины", callback_data="set_cart_warning"))
    if has_warning:
        kb.add(telebot.types.InlineKeyboardButton("🗑 Убрать плашку у корзины", callback_data="clear_cart_warning"))
    kb.add(telebot.types.InlineKeyboardButton("⬅️ В меню филиала", callback_data="back_main"))
    return kb

def categories_kb(mode):
    kb = telebot.types.InlineKeyboardMarkup()
    for cat_key, cat_val in MENU_CATEGORIES.items():
        kb.add(telebot.types.InlineKeyboardButton(cat_val["title"], callback_data=f"cat_{mode}_{cat_key}"))
    kb.add(telebot.types.InlineKeyboardButton("⬅️ В меню филиала", callback_data="back_main"))
    return kb

def stock_items_kb(cat_key, out_of_stock):
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for item_id, name, _ in MENU_CATEGORIES[cat_key]["items"]:
        icon = "🔴" if item_id in out_of_stock else "🟢"
        buttons.append(telebot.types.InlineKeyboardButton(f"{icon} {name}", callback_data=f"toggle_stock_{cat_key}_{item_id}"))
    kb.add(*buttons)
    kb.add(telebot.types.InlineKeyboardButton("⬅️ Назад к категориям", callback_data="categories_stock"))
    return kb

def price_items_kb(cat_key, current_prices):
    kb = telebot.types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for item_id, name, def_price in MENU_CATEGORIES[cat_key]["items"]:
        price = current_prices.get(item_id, def_price)
        buttons.append(telebot.types.InlineKeyboardButton(f"{name}: {price}₽", callback_data=f"edit_price_{cat_key}_{item_id}"))
    kb.add(*buttons)
    kb.add(telebot.types.InlineKeyboardButton("⬅️ Назад к категориям", callback_data="categories_price"))
    return kb

# ==================== ОТОБРАЖЕНИЕ ЭКРАНОВ ====================
def show_branch_selector(chat_id, user_id, message_id=None):
    text = (
        "🏢 <b>Панель главного администратора</b>\n\n"
        "Выберите филиал сети для управления меню, стоп-листом и режимом ЧП:"
    )
    kb = branch_select_kb()
    target_id = message_id or user_states.get(user_id, {}).get("main_msg_id")

    if target_id:
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=target_id, parse_mode="HTML", reply_markup=kb)
            user_states[user_id]["main_msg_id"] = target_id
            return
        except Exception:
            pass

    msg = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
    user_states[user_id] = {"main_msg_id": msg.message_id}

def render_dashboard_text(branch_id, role_title=""):
    branch_cfg = BRANCHES.get(branch_id, BRANCHES["dekabr"])
    bin_data = get_branch_data(branch_id)

    out_count = len(bin_data.get("out_of_stock", []))
    cur_ann = bin_data.get("announcement", "")
    ann_status = "📢 Активно" if cur_ann else "⚪ Нет"
    
    emergency = bin_data.get("emergency", {})
    is_blocked = emergency.get("is_blocked", False)
    order_status = "🔴 ЗАКАЗЫ ПРИОСТАНОВЛЕНЫ" if is_blocked else "🟢 Приём заказов открыт"
    cart_warn = emergency.get("cart_warning", "")
    cart_status = "⚠️ Установлено" if cart_warn else "⚪ Нет"
    
    text = (
        f"<b>Панель управления: {branch_cfg['name']}</b>\n"
        f"<i>Сотрудник: {role_title}</i>\n\n"
        f"Статус филиала: <b>{order_status}</b>\n"
        f"Позиций в стоп-листе: <b>{out_count}</b> шт.\n"
        f"Объявление филиала: <b>{ann_status}</b>\n"
        f"Предупреждение корзины: <b>{cart_status}</b>\n\n"
        f"<i>Выберите нужное действие:</i>"
    )
    return text, emergency

def show_dashboard(chat_id, user_id, message_id=None):
    admin_info = authenticated_admins.get(user_id, {})
    current_branch = user_states.get(user_id, {}).get("branch") or admin_info.get("branch", "dekabr")
    is_super = admin_info.get("is_super", False)

    text, emergency = render_dashboard_text(current_branch, admin_info.get("role_title", ""))
    kb = main_menu_kb(current_branch, is_super=is_super, emergency_data=emergency)

    target_id = message_id or user_states.get(user_id, {}).get("main_msg_id")

    if target_id:
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=target_id, parse_mode="HTML", reply_markup=kb)
            user_states[user_id]["main_msg_id"] = target_id
            return
        except Exception:
            pass

    msg = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
    user_states[user_id] = user_states.get(user_id, {})
    user_states[user_id]["main_msg_id"] = msg.message_id

def show_emergency_menu(chat_id, message_id, branch_id):
    bin_data = get_branch_data(branch_id)
    emergency = bin_data.get("emergency", {})
    is_blocked = emergency.get("is_blocked", False)
    reason = emergency.get("block_reason", "Не указана")
    cart_warn = emergency.get("cart_warning", "")
    b_name = BRANCHES.get(branch_id, {}).get("short", branch_id)

    status_text = "🔴 <b>ПРИЁМ ЗАКАЗОВ ЗАБЛОКИРОВАН</b>" if is_blocked else "🟢 <b>Приём заказов открыт (штатно)</b>"
    warn_display = f"«{cart_warn}»" if cart_warn else "<i>отключено</i>"

    text = (
        f"🚨 <b>Управление ЧП: филиал {b_name}</b>\n\n"
        f"Текущее состояние: {status_text}\n"
        f"Причина остановки: <i>«{reason}»</i>\n\n"
        f"Предупреждение над корзиной: {warn_display}\n\n"
        f"<i>Блокировка остановит оформление заказов на сайте именно для этого филиала.</i>"
    )
    bot.edit_message_text(
        text,
        chat_id=chat_id,
        message_id=message_id,
        parse_mode="HTML",
        reply_markup=emergency_kb(is_blocked, bool(cart_warn))
    )

# ==================== СТАРТ И АВТОРИЗАЦИЯ ====================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    safe_delete_message(chat_id, message.message_id)

    old_main = user_states.get(uid, {}).get("main_msg_id")

    if uid in authenticated_admins:
        admin_info = authenticated_admins[uid]
        if admin_info.get("is_super"):
            show_branch_selector(chat_id, uid, old_main)
        else:
            show_dashboard(chat_id, uid, old_main)
        return

    text = (
        "🔒 <b>Панель управления сетью «Буузная Адис» v1.3.1</b>\n\n"
        "Для работы требуется авторизация сотрудника.\n"
        "Введите ваш <b>логин</b>:"
    )

    if old_main:
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=old_main, parse_mode="HTML")
            user_states[uid] = {"state": "LOGIN", "main_msg_id": old_main}
            return
        except Exception:
            pass

    msg = bot.send_message(chat_id, text, parse_mode="HTML")
    user_states[uid] = {"state": "LOGIN", "main_msg_id": msg.message_id}

# ==================== ТЕКСТОВЫЙ ВВОД ====================
@bot.message_handler(func=lambda msg: user_states.get(msg.from_user.id, {}).get("state") in [
    "LOGIN", "PASSWORD", "SET_PRICE", "SET_ANNOUNCEMENT", "SET_BLOCK_REASON", "SET_CART_WARNING"
])
def handle_text_inputs(message):
    uid = message.from_user.id
    state_info = user_states.get(uid, {})
    state = state_info.get("state")
    branch_id = state_info.get("branch", "dekabr")
    text = message.text.strip()
    chat_id = message.chat.id
    main_msg_id = state_info.get("main_msg_id")

    safe_delete_message(chat_id, message.message_id)

    if state == "LOGIN":
        state_info["login"] = text.lower()
        state_info["state"] = "PASSWORD"
        bot.edit_message_text(
            f"🔑 Логин: <code>{state_info['login']}</code>\nВведите <b>пароль</b>:",
            chat_id=chat_id,
            message_id=main_msg_id,
            parse_mode="HTML"
        )

    elif state == "PASSWORD":
        login = state_info.get("login")
        account = ADMIN_USERS.get(login)

        if account and text == account["password"]:
            is_super = (account["branch"] == "all")
            assigned_branch = "dekabr" if is_super else account["branch"]

            authenticated_admins[uid] = {
                "login": login,
                "branch": assigned_branch,
                "role_title": account["role_title"],
                "is_super": is_super
            }

            user_states[uid] = {
                "branch": assigned_branch,
                "main_msg_id": main_msg_id
            }

            if is_super:
                show_branch_selector(chat_id, uid, main_msg_id)
            else:
                show_dashboard(chat_id, uid, main_msg_id)
        else:
            user_states[uid] = {"main_msg_id": main_msg_id}
            bot.edit_message_text(
                "❌ <b>Неверный логин или пароль.</b>\nНажмите /start для повторного входа.",
                chat_id=chat_id,
                message_id=main_msg_id,
                parse_mode="HTML"
            )

    elif state == "SET_PRICE":
        clean = text.replace("₽", "").replace("руб", "").strip()
        if not clean.isdigit():
            bot.edit_message_text(
                "⚠️ <b>Ошибка:</b> введите сумму целым числом (например: <code>150</code>):",
                chat_id=chat_id,
                message_id=main_msg_id,
                parse_mode="HTML",
                reply_markup=cancel_input_kb(f"cat_price_{state_info.get('cat_key')}")
            )
            return

        new_price = int(clean)
        item_id = state_info.get("item_id")
        cat_key = state_info.get("cat_key")

        bin_data = get_branch_data(branch_id)
        prices = bin_data.get("prices", {})
        prices[item_id] = new_price
        bin_data["prices"] = prices
        update_branch_data(branch_id, bin_data)

        cat_title = MENU_CATEGORIES[cat_key]["title"]
        user_states[uid]["state"] = None
        bot.edit_message_text(
            f"💰 <b>Цены ({BRANCHES[branch_id]['short']}): {cat_title}</b>\n(Обновлено: {new_price} ₽)\nВыберите позицию:",
            chat_id=chat_id,
            message_id=main_msg_id,
            parse_mode="HTML",
            reply_markup=price_items_kb(cat_key, prices)
        )

    elif state == "SET_ANNOUNCEMENT":
        bin_data = get_branch_data(branch_id)
        bin_data["announcement"] = text
        update_branch_data(branch_id, bin_data)
        user_states[uid]["state"] = None
        show_dashboard(chat_id, uid, main_msg_id)

    elif state == "SET_BLOCK_REASON":
        bin_data = get_branch_data(branch_id)
        emergency = bin_data.get("emergency", {})
        emergency["block_reason"] = text
        bin_data["emergency"] = emergency
        update_branch_data(branch_id, bin_data)
        user_states[uid]["state"] = None
        show_emergency_menu(chat_id, main_msg_id, branch_id)

    elif state == "SET_CART_WARNING":
        bin_data = get_branch_data(branch_id)
        emergency = bin_data.get("emergency", {})
        emergency["cart_warning"] = text
        bin_data["emergency"] = emergency
        update_branch_data(branch_id, bin_data)
        user_states[uid]["state"] = None
        show_emergency_menu(chat_id, main_msg_id, branch_id)

# ==================== ЗАГРУЗКА ФОТО ДОСКИ ====================
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.from_user.id
    chat_id = message.chat.id
    safe_delete_message(chat_id, message.message_id)

    if uid not in authenticated_admins:
        return

    branch_id = user_states.get(uid, {}).get("branch", "dekabr")
    main_msg_id = user_states.get(uid, {}).get("main_msg_id")

    if main_msg_id:
        try:
            bot.edit_message_text(
                f"⏳ Загружаю доску для филиала <b>{BRANCHES[branch_id]['short']}</b>...",
                chat_id=chat_id,
                message_id=main_msg_id,
                parse_mode="HTML"
            )
        except Exception:
            pass

    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_bytes = bot.download_file(file_info.file_path)

        img = Image.open(io.BytesIO(downloaded_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail((1800, 1800))

        output = io.BytesIO()
        img.save(output, format="JPEG", quality=85, optimize=True)
        compressed_bytes = output.getvalue()

        files = {'photo': (f'menu_board_{branch_id}.jpg', compressed_bytes, 'image/jpeg')}
        data = {
            'secret': UPLOAD_SECRET,
            'branch': branch_id
        }

        res = requests.post(SITE_UPLOAD_BOARD_URL, files=files, data=data, timeout=30)
        res_json = res.json()

        if res_json.get("ok"):
            image_url = res_json.get("url")
            bin_data = get_branch_data(branch_id)
            bin_data["image_url"] = image_url
            update_branch_data(branch_id, bin_data)
    except Exception as e:
        print(f"Ошибка загрузки фото: {e}")

    show_dashboard(chat_id, uid, main_msg_id)

# ==================== ОБРАБОТКА CALLBACK ====================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    uid = call.from_user.id
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    if uid not in authenticated_admins:
        bot.answer_callback_query(call.id, "Требуется вход через /start", show_alert=True)
        return

    data = call.data
    admin_info = authenticated_admins.get(uid, {})
    state_info = user_states.setdefault(uid, {})
    branch_id = state_info.get("branch") or admin_info.get("branch", "dekabr")

    # ---------- ВЫБОР ФИЛИАЛА ----------
    if data.startswith("select_branch_"):
        b_id = data.replace("select_branch_", "")
        if b_id in BRANCHES:
            state_info["branch"] = b_id
            state_info["main_msg_id"] = msg_id
            show_dashboard(chat_id, uid, msg_id)
            bot.answer_callback_query(call.id, f"Выбран филиал: {BRANCHES[b_id]['short']}")
        return

    elif data == "switch_branch":
        if admin_info.get("is_super"):
            show_branch_selector(chat_id, uid, msg_id)
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, "Доступ ограничен вашим филиалом", show_alert=True)
        return

    # ---------- УПРАВЛЕНИЕ ОФОРМЛЕНИЕМ САЙТА ----------
    elif data == "menu_themes":
        if admin_info.get("is_super"):
            bot.edit_message_text(
                "🎨 <b>Управление оформлением сайта сети</b>\n\n"
                "Выберите сезонную тему — она автоматически обновит бейдж в шапке и летающие частицы на сайте:",
                chat_id=chat_id,
                message_id=msg_id,
                parse_mode="HTML",
                reply_markup=themes_kb()
            )
            bot.answer_callback_query(call.id)
        else:
            bot.answer_callback_query(call.id, "Доступно только главному администратору", show_alert=True)
        return

    elif data.startswith("set_theme_"):
        theme_key = data.replace("set_theme_", "")
        theme_names = {
            "halloween": "🎃 Хэллоуин",
            "autumn": "🍂 Уютная осень",
            "winter": "❄️ Сибирская зима",
            "new_year": "🎄 Новый Год",
            "default": "🥟 Фирменная"
        }
        title = theme_names.get(theme_key, theme_key)

        try:
            r = requests.post(SITE_SETTINGS_URL, json={"theme": theme_key}, timeout=5)
            if r.status_code == 200:
                bot.answer_callback_query(call.id, f"Тема «{title}» успешно установлена!", show_alert=True)
            else:
                bot.answer_callback_query(call.id, f"Ошибка сервера: {r.status_code}", show_alert=True)
        except Exception as e:
            bot.answer_callback_query(call.id, f"Ошибка запроса: {e}", show_alert=True)

        show_dashboard(chat_id, uid, msg_id)
        return

    elif data == "back_main":
        state_info["main_msg_id"] = msg_id
        state_info["state"] = None
        show_dashboard(chat_id, uid, msg_id)
        bot.answer_callback_query(call.id)

    elif data == "hint_photo":
        b_name = BRANCHES[branch_id]["short"]
        bot.answer_callback_query(
            call.id,
            f"📸 Пришлите фото доски в чат — оно обновит меню филиала «{b_name}» на сайте!",
            show_alert=True
        )

    # ---------- ОБЪЯВЛЕНИЕ ТОЧКИ ----------
    elif data == "manage_announcement":
        bin_data = get_branch_data(branch_id)
        current = bin_data.get("announcement", "")
        current_display = f"<i>«{current}»</i>" if current else "<i>(сейчас отключено)</i>"

        kb = telebot.types.InlineKeyboardMarkup()
        kb.add(telebot.types.InlineKeyboardButton("✏️ Задать новый текст", callback_data="set_announcement"))
        if current:
            kb.add(telebot.types.InlineKeyboardButton("🗑 Отключить объявление", callback_data="clear_announcement"))
        kb.add(telebot.types.InlineKeyboardButton("⬅️ В меню филиала", callback_data="back_main"))

        bot.edit_message_text(
            f"📢 <b>Объявление: {BRANCHES[branch_id]['name']}</b>\n\n"
            f"Текущий текст:\n{current_display}\n\n"
            f"Отобразится на сайте, когда выбран этот филиал.",
            chat_id=chat_id,
            message_id=msg_id,
            parse_mode="HTML",
            reply_markup=kb
        )
        bot.answer_callback_query(call.id)

    elif data == "set_announcement":
        state_info["state"] = "SET_ANNOUNCEMENT"
        state_info["main_msg_id"] = msg_id
        bot.edit_message_text(
            f"✏️ <b>Напишите текст объявления для точки «{BRANCHES[branch_id]['short']}»:</b>",
            chat_id=chat_id,
            message_id=msg_id,
            parse_mode="HTML",
            reply_markup=cancel_input_kb("manage_announcement")
        )
        bot.answer_callback_query(call.id)

    elif data == "clear_announcement":
        bin_data = get_branch_data(branch_id)
        bin_data["announcement"] = ""
        update_branch_data(branch_id, bin_data)
        bot.answer_callback_query(call.id, "✅ Объявление убрано!", show_alert=True)
        show_dashboard(chat_id, uid, msg_id)

    # ---------- ЧП И СТОП-ЗАКАЗ ----------
    elif data == "manage_emergency":
        state_info["main_msg_id"] = msg_id
        show_emergency_menu(chat_id, msg_id, branch_id)
        bot.answer_callback_query(call.id)

    elif data == "toggle_block_orders":
        bin_data = get_branch_data(branch_id)
        emergency = bin_data.get("emergency", {})
        is_blocked = not emergency.get("is_blocked", False)
        emergency["is_blocked"] = is_blocked
        bin_data["emergency"] = emergency
        update_branch_data(branch_id, bin_data)

        b_name = BRANCHES[branch_id]["short"]
        alert = f"🔴 Приём заказов остановлен ({b_name})!" if is_blocked else f"🟢 Заказы открыты ({b_name})!"
        bot.answer_callback_query(call.id, alert, show_alert=True)
        show_emergency_menu(chat_id, msg_id, branch_id)

    elif data == "set_block_reason":
        state_info["state"] = "SET_BLOCK_REASON"
        state_info["main_msg_id"] = msg_id
        bot.edit_message_text(
            "✏️ <b>Напишите причину блокировки заказов:</b>",
            chat_id=chat_id,
            message_id=msg_id,
            parse_mode="HTML",
            reply_markup=cancel_input_kb("manage_emergency")
        )
        bot.answer_callback_query(call.id)

    elif data == "set_cart_warning":
        state_info["state"] = "SET_CART_WARNING"
        state_info["main_msg_id"] = msg_id
        bot.edit_message_text(
            "⚠️ <b>Напишите текст предупреждения над корзиной:</b>",
            chat_id=chat_id,
            message_id=msg_id,
            parse_mode="HTML",
            reply_markup=cancel_input_kb("manage_emergency")
        )
        bot.answer_callback_query(call.id)

    elif data == "clear_cart_warning":
        bin_data = get_branch_data(branch_id)
        emergency = bin_data.get("emergency", {})
        emergency["cart_warning"] = ""
        bin_data["emergency"] = emergency
        update_branch_data(branch_id, bin_data)
        bot.answer_callback_query(call.id, "✅ Предупреждение снято!", show_alert=True)
        show_emergency_menu(chat_id, msg_id, branch_id)

    # ---------- СТОП-ЛИСТ ----------
    elif data == "categories_stock":
        bot.edit_message_text(
            f"📦 <b>Стоп-лист филиала: {BRANCHES[branch_id]['short']}</b>\nВыберите категорию:",
            chat_id=chat_id,
            message_id=msg_id,
            parse_mode="HTML",
            reply_markup=categories_kb("stock")
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("cat_stock_"):
        cat_key = data.replace("cat_stock_", "")
        bin_data = get_branch_data(branch_id)
        out_of_stock = bin_data.get("out_of_stock", [])
        cat_title = MENU_CATEGORIES[cat_key]["title"]
        bot.edit_message_text(
            f"📦 <b>{cat_title} ({BRANCHES[branch_id]['short']})</b>\n\n🟢 — есть в наличии\n🔴 — в стоп-листе:",
            chat_id=chat_id,
            message_id=msg_id,
            parse_mode="HTML",
            reply_markup=stock_items_kb(cat_key, out_of_stock)
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("toggle_stock_"):
        _, _, cat_key, item_id = data.split("_", 3)
        bin_data = get_branch_data(branch_id)
        out_of_stock = bin_data.get("out_of_stock", [])

        if item_id in out_of_stock:
            out_of_stock.remove(item_id)
            alert_text = "🟢 Блюдо включено"
        else:
            out_of_stock.append(item_id)
            alert_text = "🔴 Блюдо выключено"

        bin_data["out_of_stock"] = out_of_stock
        update_branch_data(branch_id, bin_data)

        bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=msg_id,
            reply_markup=stock_items_kb(cat_key, out_of_stock)
        )
        bot.answer_callback_query(call.id, alert_text)

    # ---------- РЕДАКТОР ЦЕН ----------
    elif data == "categories_price":
        bot.edit_message_text(
            f"💰 <b>Редактор цен ({BRANCHES[branch_id]['short']})</b>\nВыберите категорию:",
            chat_id=chat_id,
            message_id=msg_id,
            parse_mode="HTML",
            reply_markup=categories_kb("price")
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("cat_price_"):
        cat_key = data.replace("cat_price_", "")
        bin_data = get_branch_data(branch_id)
        prices = bin_data.get("prices", {})
        cat_title = MENU_CATEGORIES[cat_key]["title"]
        bot.edit_message_text(
            f"💰 <b>Цены ({BRANCHES[branch_id]['short']}): {cat_title}</b>\nВыберите позицию:",
            chat_id=chat_id,
            message_id=msg_id,
            parse_mode="HTML",
            reply_markup=price_items_kb(cat_key, prices)
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("edit_price_"):
        _, _, cat_key, item_id = data.split("_", 3)
        item_name = ALL_ITEMS.get(item_id, (item_id, 0))[0]

        state_info["state"] = "SET_PRICE"
        state_info["item_id"] = item_id
        state_info["cat_key"] = cat_key
        state_info["main_msg_id"] = msg_id

        bot.edit_message_text(
            f"✏️ Введите цену для филиала <b>{BRANCHES[branch_id]['short']}</b>:\n<b>{item_name}</b> (числом, например: <code>150</code>):",
            chat_id=chat_id,
            message_id=msg_id,
            parse_mode="HTML",
            reply_markup=cancel_input_kb(f"cat_price_{cat_key}")
        )
        bot.answer_callback_query(call.id)

    # ---------- ВЫХОД ----------
    elif data == "menu_logout":
        authenticated_admins.pop(uid, None)
        user_states.pop(uid, None)
        bot.edit_message_text(
            "🚪 Вы вышли из системы. Нажмите /start для входа.",
            chat_id=chat_id,
            message_id=msg_id
        )
        bot.answer_callback_query(call.id)

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    print("🚀 Запуск мультифилиального бота «Буузная Адис» v1.3.1...")
    server_thread = Thread(target=run_web)
    server_thread.daemon = True
    server_thread.start()

    try:
        bot.remove_webhook()
        print("🔗 Вебхук сброшен, запуск polling...")
    except Exception as e:
        print(f"⚠️ Ошибка удаления вебхука: {e}")

    bot.infinity_polling(skip_pending=True)
