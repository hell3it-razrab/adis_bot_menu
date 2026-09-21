import os
import io
import time
import telebot
import requests
from threading import Thread
from flask import Flask
from PIL import Image

# ==================== НАСТРОЙКИ ====================
TELEGRAM_BOT_TOKEN = "8712152425:AAG94cYULxUTJ5lU67Reh9QDA29XZq4tZC4"

# Шлюз собственного сервера Beget
SITE_URL = "https://adis38.ru"
SITE_API_URL = "https://adis38.ru/api_menu.php?key=adis_secret_bot_key_2026"
SITE_MENU_URL = "https://adis38.ru/menu.json"

# Прямая выгрузка фото на собственный хостинг (v1.2.1)
SITE_UPLOAD_BOARD_URL = "https://adis38.ru/api_upload_board.php"
UPLOAD_SECRET = "AdisMenuSecretKey_2026"

ADMIN_LOGIN = "admin"
ADMIN_PASSWORD = "11111"

authenticated_admins = set()
user_states = {}  # {uid: {"state": ..., "data": ...}}

# Каталог товаров, сгруппированный по категориям
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

# Плоский список для быстрого поиска блюда по ID
ALL_ITEMS = {}
for cat in MENU_CATEGORIES.values():
    for item_id, name, def_price in cat["items"]:
        ALL_ITEMS[item_id] = (name, def_price)

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# ==================== ВЕБ-СЕРВЕР ДЛЯ RENDER ====================
app = Flask(__name__)

@app.route('/')
def home():
    return "Бот кафе Адис работает 24/7!"

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ==================== РАБОТА С МЕНЮ НА СЕРВЕРЕ ADIS38.RU ====================
def get_bin_data():
    try:
        url = f"{SITE_MENU_URL}?t={int(time.time())}"
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            return r.json()
        print(f"⚠️ Ошибка чтения меню: HTTP {r.status_code}")
    except Exception as e:
        print(f"❌ Исключение при чтении menu.json: {e}")
    return {"image_url": "", "out_of_stock": [], "prices": {}}

def update_bin_data(data):
    headers = {
        "Content-Type": "application/json"
    }
    try:
        r = requests.post(SITE_API_URL, headers=headers, json=data, timeout=15)
        print(f"📡 Ответ Beget ({r.status_code}): {r.text}")
        return r.status_code == 200
    except Exception as e:
        print(f"❌ Ошибка отправки на Beget: {e}")
        return False

# ==================== КЛАВИАТУРЫ ====================
def main_menu_kb():
    kb = telebot.types.InlineKeyboardMarkup()
    kb.add(telebot.types.InlineKeyboardButton("📸 Обновить фото доски", callback_data="hint_photo"))
    kb.add(telebot.types.InlineKeyboardButton("👁 Посмотреть текущее фото", callback_data="view_photo"))
    kb.add(telebot.types.InlineKeyboardButton("📦 Стоп-лист (Наличие)", callback_data="categories_stock"))
    kb.add(telebot.types.InlineKeyboardButton("💰 Редактор цен", callback_data="categories_price"))
    kb.add(telebot.types.InlineKeyboardButton("🌐 Открыть витрину сайта", url=SITE_URL))
    kb.add(telebot.types.InlineKeyboardButton("🚪 Выйти из системы", callback_data="menu_logout"))
    return kb

def categories_kb(mode):
    kb = telebot.types.InlineKeyboardMarkup()
    for cat_key, cat_val in MENU_CATEGORIES.items():
        kb.add(telebot.types.InlineKeyboardButton(cat_val["title"], callback_data=f"cat_{mode}_{cat_key}"))
    kb.add(telebot.types.InlineKeyboardButton("⬅️ В главное меню", callback_data="back_main"))
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

# ==================== АВТОРИЗАЦИЯ И СТАРТ ====================
@bot.message_handler(commands=['start'])
def start_cmd(message):
    uid = message.from_user.id
    if uid in authenticated_admins:
        show_dashboard(message.chat.id)
        return

    user_states[uid] = {"state": "LOGIN"}
    bot.send_message(
        message.chat.id,
        "🔒 <b>Панель управления кафе «Буузная Адис» v1.2.1</b>\n\n"
        "Для работы требуется авторизация.\n"
        "Введите <b>логин</b> сотрудника:",
        parse_mode="HTML"
    )

def show_dashboard(chat_id, message_id=None):
    bin_data = get_bin_data()
    out_count = len(bin_data.get("out_of_stock", []))
    status_icon = "🟢 Витрина активна"
    
    text = (
        f"<b>Панель администратора «Буузная Адис»</b>\n\n"
        f"Статус: {status_icon}\n"
        f"Позиций в стоп-листе: <b>{out_count}</b> шт.\n\n"
        f"<i>Выберите нужное действие:</i>"
    )
    
    if message_id:
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, parse_mode="HTML", reply_markup=main_menu_kb())
            return
        except Exception:
            pass
    bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=main_menu_kb())

# ==================== ВВОД ТЕКСТА (ЛОГИН/ПАРОЛЬ/ЦЕНА) ====================
@bot.message_handler(func=lambda msg: user_states.get(msg.from_user.id, {}).get("state") in ["LOGIN", "PASSWORD", "SET_PRICE"])
def handle_text_inputs(message):
    uid = message.from_user.id
    state_info = user_states.get(uid, {})
    state = state_info.get("state")
    text = message.text.strip()

    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

    if state == "LOGIN":
        user_states[uid] = {"state": "PASSWORD", "login": text}
        bot.send_message(message.chat.id, "🔑 Теперь введите <b>пароль</b>:", parse_mode="HTML")

    elif state == "PASSWORD":
        login = state_info.get("login")
        if login == ADMIN_LOGIN and text == ADMIN_PASSWORD:
            authenticated_admins.add(uid)
            user_states[uid] = {}
            show_dashboard(message.chat.id)
        else:
            user_states[uid] = {}
            bot.send_message(message.chat.id, "❌ Неверный логин или пароль. Нажмите /start для повторной попытки.")

    elif state == "SET_PRICE":
        clean = text.replace("₽", "").replace("руб", "").strip()
        if not clean.isdigit():
            bot.send_message(message.chat.id, "⚠️ Введите корректную сумму <b>целым числом</b> (например: <code>150</code>).", parse_mode="HTML")
            return

        new_price = int(clean)
        item_id = state_info.get("item_id")
        cat_key = state_info.get("cat_key")
        item_name = ALL_ITEMS.get(item_id, (item_id, 0))[0]

        bin_data = get_bin_data()
        prices = bin_data.get("prices", {})
        prices[item_id] = new_price
        bin_data["prices"] = prices
        
        if update_bin_data(bin_data):
            prompt_msg_id = state_info.get("prompt_msg_id")
            if prompt_msg_id:
                try:
                    bot.delete_message(message.chat.id, prompt_msg_id)
                except Exception:
                    pass

            user_states[uid] = {}
            
            bot.send_message(
                message.chat.id,
                f"✅ Стоимость <b>{item_name}</b> обновлена: <b>{new_price} ₽</b>",
                parse_mode="HTML",
                reply_markup=price_items_kb(cat_key, prices)
            )
        else:
            bot.send_message(message.chat.id, "❌ Ошибка сохранения на сервере. Попробуйте еще раз.")

# ==================== ЗАГРУЗКА ФОТО ДОСКИ МЕНЮ НА BEGET ====================
@bot.message_handler(content_types=['photo'])
def handle_photo(message):
    uid = message.from_user.id
    if uid not in authenticated_admins:
        bot.reply_to(message, "⚠️ Сначала авторизуйтесь через /start")
        return

    msg = bot.reply_to(message, "⏳ Сжимаю и отправляю фото на сервер adis38.ru...")
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_bytes = bot.download_file(file_info.file_path)

        # 1. Сжатие и оптимизация изображения
        img = Image.open(io.BytesIO(downloaded_bytes))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail((1800, 1800))

        output = io.BytesIO()
        img.save(output, format="JPEG", quality=85, optimize=True)
        compressed_bytes = output.getvalue()

        # 2. Прямая отправка на Beget
        files = {
            'photo': ('menu_board.jpg', compressed_bytes, 'image/jpeg')
        }
        data = {
            'secret': UPLOAD_SECRET
        }

        res = requests.post(SITE_UPLOAD_BOARD_URL, files=files, data=data, timeout=30)
        res_json = res.json()

        if res_json.get("ok"):
            image_url = res_json.get("url")

            # Обновляем URL в menu.json
            bin_data = get_bin_data()
            bin_data["image_url"] = image_url
            saved = update_bin_data(bin_data)

            if saved:
                bot.edit_message_text(
                    "✅ <b>Фотография меню успешно обновлена на сервере adis38.ru!</b>\n\n"
                    "Она моментально доступна гостям на сайте и в PWA-приложении.",
                    chat_id=message.chat.id,
                    message_id=msg.message_id,
                    parse_mode="HTML",
                    reply_markup=main_menu_kb()
                )
            else:
                bot.edit_message_text("❌ Фото загружено, но не удалось обновить menu.json.", chat_id=message.chat.id, message_id=msg.message_id)
        else:
            error_text = res_json.get("error", "Неизвестная ошибка")
            bot.edit_message_text(f"❌ Ошибка сервера Beget: {error_text}", chat_id=message.chat.id, message_id=msg.message_id)

    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка: {str(e)}", chat_id=message.chat.id, message_id=msg.message_id)

# ==================== ОБРАБОТКА ИНЛАЙН-КНОПОК ====================
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    uid = call.from_user.id
    if uid not in authenticated_admins:
        bot.answer_callback_query(call.id, "Требуется вход через /start", show_alert=True)
        return

    data = call.data

    if data == "back_main":
        user_states[uid] = {}
        show_dashboard(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id)

    elif data == "hint_photo":
        bot.send_message(call.message.chat.id, "📸 Просто отправьте фото доски с ценами в этот чат как обычную картинку.")
        bot.answer_callback_query(call.id)

    elif data == "view_photo":
        bin_data = get_bin_data()
        img_url = bin_data.get("image_url", "")
        if img_url:
            bot.send_photo(call.message.chat.id, img_url, caption="🖼 <b>Текущее фото меню на сайте</b>", parse_mode="HTML")
        else:
            bot.send_message(call.message.chat.id, "⚠️ Фотография меню еще не была загружена.")
        bot.answer_callback_query(call.id)

    elif data == "menu_logout":
        authenticated_admins.discard(uid)
        user_states[uid] = {}
        bot.edit_message_text("🚪 Вы вышли из системы. Нажмите /start для входа.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        bot.answer_callback_query(call.id)

    elif data == "categories_stock":
        bot.edit_message_text(
            "📦 <b>Управление стоп-листом</b>\nВыберите категорию блюд:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=categories_kb("stock")
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("cat_stock_"):
        cat_key = data.replace("cat_stock_", "")
        bin_data = get_bin_data()
        out_of_stock = bin_data.get("out_of_stock", [])
        cat_title = MENU_CATEGORIES[cat_key]["title"]
        bot.edit_message_text(
            f"📦 <b>Стоп-лист: {cat_title}</b>\n\n🟢 — есть в наличии\n🔴 — в стоп-листе\n<i>Нажмите для переключения:</i>",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=stock_items_kb(cat_key, out_of_stock)
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("toggle_stock_"):
        _, _, cat_key, item_id = data.split("_", 3)
        bin_data = get_bin_data()
        out_of_stock = bin_data.get("out_of_stock", [])

        if item_id in out_of_stock:
            out_of_stock.remove(item_id)
            alert_text = "🟢 Блюдо включено"
        else:
            out_of_stock.append(item_id)
            alert_text = "🔴 Блюдо выключено"

        bin_data["out_of_stock"] = out_of_stock
        update_bin_data(bin_data)

        bot.edit_message_reply_markup(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=stock_items_kb(cat_key, out_of_stock)
        )
        bot.answer_callback_query(call.id, alert_text)

    elif data == "categories_price":
        bot.edit_message_text(
            "💰 <b>Редактор цен</b>\nВыберите категорию:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=categories_kb("price")
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("cat_price_"):
        cat_key = data.replace("cat_price_", "")
        bin_data = get_bin_data()
        prices = bin_data.get("prices", {})
        cat_title = MENU_CATEGORIES[cat_key]["title"]
        bot.edit_message_text(
            f"💰 <b>Цены: {cat_title}</b>\nВыберите позицию для изменения:",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
            reply_markup=price_items_kb(cat_key, prices)
        )
        bot.answer_callback_query(call.id)

    elif data.startswith("edit_price_"):
        _, _, cat_key, item_id = data.split("_", 3)
        item_name = ALL_ITEMS.get(item_id, (item_id, 0))[0]

        prompt_msg = bot.send_message(
            call.message.chat.id,
            f"✏️ Введите новую стоимость для позиции:\n<b>{item_name}</b> (в рублях, только число):",
            parse_mode="HTML"
        )

        user_states[uid] = {
            "state": "SET_PRICE",
            "item_id": item_id,
            "cat_key": cat_key,
            "prompt_msg_id": prompt_msg.message_id
        }
        bot.answer_callback_query(call.id)

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    print("🚀 Запуск улучшенного бота «Буузная Адис» v1.2.1...")
    server_thread = Thread(target=run_web)
    server_thread.daemon = True
    server_thread.start()

    bot.infinity_polling(skip_pending=True)
