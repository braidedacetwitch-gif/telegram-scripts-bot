import csv
import io
import logging
import math
import os

import httpx
from fastapi import FastAPI, Request
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_USERNAME"]
SHEET_CSV_URL = os.environ["SHEET_CSV_URL"]
RENDER_URL = os.environ["RENDER_URL"]

GAMES_PER_PAGE = 8
SCRIPTS_PER_PAGE = 5

INJECTORS_NAME = "injectors"

app = FastAPI()
telegram_app = Application.builder().token(TOKEN).build()


def clean(value) -> str:
    return str(value or "").replace("\ufeff", "").strip().lower()


def clean_keys(row: dict) -> dict:
    return {
        clean(key): value
        for key, value in row.items()
    }


def yes_no(value: str) -> str:
    value = clean(value)
    return "Да" if value in ("yes", "да", "1", "true") else "Нет"


def injector_text(value: str) -> str:
    value = clean(value)

    if value in ("both", "оба", "solara+xeno", "solara xeno"):
        return "Solara / Xeno"

    if value == "solara":
        return "Solara"

    if value == "xeno":
        return "Xeno"

    if value in ("none", "нет", "-"):
        return "Не указан"

    return value.title() if value else "Не указан"


async def is_subscribed(user_id: int) -> bool:
    try:
        member = await telegram_app.bot.get_chat_member(CHANNEL, user_id)
        return member.status not in ("left", "kicked")
    except Exception:
        logger.exception("Ошибка проверки подписки")
        return False


async def load_scripts() -> list[dict]:
    async with httpx.AsyncClient(
        timeout=15,
        follow_redirects=True
    ) as client:
        response = await client.get(SHEET_CSV_URL)
        response.raise_for_status()

    csv_text = response.text
    reader = csv.DictReader(io.StringIO(csv_text))

    scripts = []

    for raw_row in reader:
        row = clean_keys(raw_row)

        script_id = clean(row.get("id"))
        game = clean(row.get("game"))
        title = clean(row.get("title"))
        link = clean(row.get("link"))
        description = clean(row.get("description"))
        key_required = clean(row.get("key"))
        injector = clean(row.get("injector"))
        mobile = clean(row.get("mobile"))
        active = clean(row.get("active"))

        if not game or not title or not link:
            continue

        scripts.append({
            "id": script_id,
            "game": game,
            "title": title,
            "link": link,
            "description": description,
            "key": key_required in ("yes", "да", "1", "true"),
            "injector": injector_text(injector),
            "mobile": mobile in ("yes", "да", "1", "true"),
            "active": active in ("yes", "да", "1", "true"),
        })

    logger.info("CSV колонки: %s", reader.fieldnames)
    logger.info("Загружено строк: %s", len(scripts))

    return scripts


async def get_active_games(include_injectors: bool = False) -> list[str]:
    scripts = await load_scripts()

    games = {
        item["game"]
        for item in scripts
        if item["active"] and item["game"]
    }

    if include_injectors and INJECTORS_NAME in games:
        games.remove(INJECTORS_NAME)

    return sorted(games)


async def get_active_injectors() -> list[str]:
    scripts = await load_scripts()

    return sorted({
        item["title"]
        for item in scripts
        if item["active"]
        and item["game"] == INJECTORS_NAME
        and item["title"]
    })


def games_keyboard(games: list[str], page: int = 0) -> InlineKeyboardMarkup:
    start = page * GAMES_PER_PAGE
    end = start + GAMES_PER_PAGE
    page_games = games[start:end]

    buttons = []

    for game in page_games:
        buttons.append([
            InlineKeyboardButton(
                game.title(),
                callback_data=f"game:{page}:{game}"
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=f"games_page:{page - 1}"
            )
        )

    if end < len(games):
        navigation.append(
            InlineKeyboardButton(
                "Вперёд ➡️",
                callback_data=f"games_page:{page + 1}"
            )
        )

    if navigation:
        buttons.append(navigation)

    return InlineKeyboardMarkup(buttons)


def injectors_keyboard(
    injectors: list[str],
    page: int = 0,
) -> InlineKeyboardMarkup:
    start = page * GAMES_PER_PAGE
    end = start + GAMES_PER_PAGE
    page_injectors = injectors[start:end]

    buttons = []

    for injector in page_injectors:
        buttons.append([
            InlineKeyboardButton(
                injector.title(),
                callback_data=f"injector:{page}:{injector}"
            )
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=f"injectors_page:{page - 1}"
            )
        )

    if end < len(injectors):
        navigation.append(
            InlineKeyboardButton(
                "Вперёд ➡️",
                callback_data=f"injectors_page:{page + 1}"
            )
        )

    if navigation:
        buttons.append(navigation)

    return InlineKeyboardMarkup(buttons)


async def send_games_page(
    update_or_query,
    page: int = 0,
    edit: bool = False,
):
    try:
        games = await get_active_games(include_injectors=True)
    except Exception:
        logger.exception("Ошибка чтения Google Таблицы")

        text = "Не удалось загрузить список игр. Попробуй позже."

        if edit:
            await update_or_query.edit_message_text(text)
        else:
            await update_or_query.message.reply_text(text)

        return

    if not games:
        text = "Пока нет доступных игр."
        keyboard = None
    else:
        total_pages = math.ceil(len(games) / GAMES_PER_PAGE)
        text = (
            f"Выбери игру:\n\n"
            f"Страница {page + 1} из {total_pages}"
        )
        keyboard = games_keyboard(games, page)

    keyboard = keyboard or InlineKeyboardMarkup([])

    if edit:
        await update_or_query.edit_message_text(
            text,
            reply_markup=keyboard,
        )
    else:
        await update_or_query.message.reply_text(
            text,
            reply_markup=keyboard,
        )


async def send_injectors_page(
    update_or_query,
    page: int = 0,
    edit: bool = False,
):
    try:
        injectors = await get_active_injectors()
    except Exception:
        logger.exception("Ошибка чтения Google Таблицы")

        text = "Не удалось загрузить список инжекторов. Попробуй позже."

        if edit:
            await update_or_query.edit_message_text(text)
        else:
            await update_or_query.message.reply_text(text)

        return

    if not injectors:
        text = "Пока нет доступных инжекторов."
        keyboard = None
    else:
        total_pages = math.ceil(len(injectors) / GAMES_PER_PAGE)
        text = (
            f"Выбери инжектор:\n\n"
            f"Страница {page + 1} из {total_pages}"
        )
        keyboard = injectors_keyboard(injectors, page)

    keyboard = keyboard or InlineKeyboardMarkup([])

    if edit:
        await update_or_query.edit_message_text(
            text,
            reply_markup=keyboard,
        )
    else:
        await update_or_query.message.reply_text(
            text,
            reply_markup=keyboard,
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await is_subscribed(user_id):
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Подписаться на канал",
                    url=f"https://t.me/{CHANNEL.lstrip('@')}"
                )
            ],
            [
                InlineKeyboardButton(
                    "Я подписался",
                    callback_data="check_sub"
                )
            ],
        ])

        await update.message.reply_text(
            "Чтобы получить скрипты, подпишись на канал "
            "и нажми кнопку ниже.",
            reply_markup=keyboard,
        )
        return

    await update.message.reply_text(
        "Привет!\n"
        "Выбери игру кнопкой или отправь команду:\n"
        "/script blox fruits\n\n"
        "Как пользоваться ботом —\n"
        "/help"
    )

    await send_games_page(update, page=0)

    await update.message.reply_text(
        "Инжекторы:",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "⚙️ Открыть инжекторы",
                    callback_data="injectors_page:0"
                )
            ]
        ])
    )


async def games(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_games_page(update, page=0)


async def injectors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_injectors_page(update, page=0)


async def games_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    page = int(query.data.replace("games_page:", "", 1))
    await send_games_page(query, page=page, edit=True)


async def injectors_page(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    page = int(query.data.replace("injectors_page:", "", 1))
    await send_injectors_page(query, page=page, edit=True)


async def check_subscription(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if await is_subscribed(query.from_user.id):
        await query.edit_message_text(
            "Спасибо за подписку!\n\n"
            "Теперь выбери игру или отправь команду:\n"
            "/script blox fruits"
        )
        await send_games_page(update, page=0)

        await update.message.reply_text(
            "Инжекторы:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⚙️ Открыть инжекторы",
                        callback_data="injectors_page:0"
                    )
                ]
            ])
        )
    else:
        await query.answer(
            "Пока не вижу подписку. Подпишись и попробуй снова.",
            show_alert=True,
        )


async def script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    logger.info(
        "Получена команда /script от %s: %s",
        user_id,
        context.args,
    )

    if not await is_subscribed(user_id):
        await update.message.reply_text(
            f"Сначала подпишись на канал: "
            f"https://t.me/{CHANNEL.lstrip('@')}"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Укажи игру: /script blox fruits"
        )
        return

    game_query = " ".join(context.args).strip().lower()

    if game_query == INJECTORS_NAME:
        await update.message.reply_text(
            "Инжекторы доступны в отдельном разделе.\n"
            "Отправь /injectors или нажми кнопку в главном меню."
        )
        return

    try:
        scripts = await load_scripts()
    except Exception:
        logger.exception("Ошибка чтения Google Таблицы")
        await update.message.reply_text(
            "Не удалось загрузить список скриптов. Попробуй позже."
        )
        return

    found = [
        item for item in scripts
        if item["game"] == game_query and item["active"]
    ]

    if not found:
        available = sorted({
            item["game"]
            for item in scripts
            if item["active"] and item["game"]
            and item["game"] != INJECTORS_NAME
        })

        similar = [
            game for game in available
            if game and (
                game in game_query
                or game_query in game
            )
        ]

        if similar:
            await update.message.reply_text(
                f"По запросу «{game_query}» ничего не найдено.\n"
                "Возможно, ты имел в виду:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            game.title(),
                            callback_data=f"game:0:{game}"
                        )
                    ]
                    for game in similar[:10]
                ])
            )
        else:
            await update.message.reply_text(
                f"По запросу «{game_query}» скрипты не найдены.\n"
                "Отправь /games, чтобы увидеть доступные игры."
            )

        return

    await send_scripts_page(
        update,
        game_query,
        found,
        page=0,
    )


async def game_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.replace("game:", "", 1)
    page_text, game = data.split(":", 1)
    page = int(page_text)
    user_id = query.from_user.id

    if not await is_subscribed(user_id):
        await query.message.reply_text(
            f"Сначала подпишись на канал: "
            f"https://t.me/{CHANNEL.lstrip('@')}"
        )
        return

    try:
        scripts = await load_scripts()
    except Exception:
        logger.exception("Ошибка чтения Google Таблицы")
        await query.message.reply_text(
            "Не удалось загрузить скрипты. Попробуй позже."
        )
        return

    found = [
        item for item in scripts
        if item["game"] == game and item["active"]
    ]

    if not found:
        await query.message.reply_text(
            f"Для игры «{game}» пока нет доступных скриптов."
        )
        return

    await send_scripts_page(
        query,
        game,
        found,
        page=0,
        edit=True,
    )


async def injector_button(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    data = query.data.replace("injector:", "", 1)
    page_text, injector = data.split(":", 1)
    page = int(page_text)
    user_id = query.from_user.id

    if not await is_subscribed(user_id):
        await query.message.reply_text(
            f"Сначала подпишись на канал: "
            f"https://t.me/{CHANNEL.lstrip('@')}"
        )
        return

    try:
        scripts = await load_scripts()
    except Exception:
        logger.exception("Ошибка чтения Google Таблицы")
        await query.message.reply_text(
            "Не удалось загрузить инжекторы. Попробуй позже."
        )
        return

    found = [
        item for item in scripts
        if item["game"] == INJECTORS_NAME
        and clean(item["title"]) == injector
        and item["active"]
    ]

    if not found:
        await query.message.reply_text(
            f"Для инжектора «{injector}» пока нет доступных файлов."
        )
        return

    await send_scripts_page(
        query,
        injector,
        found,
        page=0,
        edit=True,
    )


def scripts_keyboard(
    name: str,
    scripts: list[dict],
    total_pages: int,
    page: int,
) -> InlineKeyboardMarkup:
    buttons = []

    start = page * SCRIPTS_PER_PAGE
    page_scripts = scripts[start:start + SCRIPTS_PER_PAGE]

    for item in page_scripts:
        script_id = item["id"] or clean(item["title"])[:20]

        buttons.append([
            InlineKeyboardButton(
                f"📄 {item['title']}",
                callback_data=f"details:{script_id}"
            ),
            InlineKeyboardButton(
                "🔗 Открыть",
                url=item["link"]
            ),
        ])

    navigation = []

    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=f"scripts_page:{name}:{page - 1}"
            )
        )

    if page + 1 < total_pages:
        navigation.append(
            InlineKeyboardButton(
                "Вперёд ➡️",
                callback_data=f"scripts_page:{name}:{page + 1}"
            )
        )

    if navigation:
        buttons.append(navigation)

    buttons.append([
        InlineKeyboardButton(
            "🎮 К списку игр",
            callback_data="games_page:0"
        )
    ])

    return InlineKeyboardMarkup(buttons)


async def send_scripts_page(
    update_or_query,
    name: str,
    scripts: list[dict],
    page: int = 0,
    edit: bool = False,
):
    total_pages = max(1, math.ceil(len(scripts) / SCRIPTS_PER_PAGE))
    page = max(0, min(page, total_pages - 1))

    start = page * SCRIPTS_PER_PAGE
    end = start + SCRIPTS_PER_PAGE
    page_scripts = scripts[start:end]

    text = f"<b>Скрипты для {name}</b>\n\n"
    text += f"Страница {page + 1} из {total_pages}\n\n"

    for number, item in enumerate(page_scripts, start=start + 1):
        key_text = "Да" if item["key"] else "Нет"
        mobile_text = "Да" if item["mobile"] else "Нет"

        text += (
            f"<b>{number}. {item['title']}</b>\n"
            f"Ключ: {key_text}\n"
            f"Инжектор: {item['injector']}\n"
            f"Мобильные: {mobile_text}\n\n"
        )

    keyboard = scripts_keyboard(
        name,
        scripts,
        total_pages,
        page,
    )

    if edit:
        await update_or_query.edit_message_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    else:
        await update_or_query.message.reply_text(
            text,
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def scripts_page(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    data = query.data.replace("scripts_page:", "", 1)
    name, page_text = data.rsplit(":", 1)
    page = int(page_text)

    try:
        scripts = await load_scripts()
    except Exception:
        logger.exception("Ошибка чтения Google Таблицы")
        await query.edit_message_text(
            "Не удалось загрузить скрипты. Попробуй позже."
        )
        return

    found = [
        item for item in scripts
        if (
            item["game"] == name
            or item["title"] == name
        )
        and item["active"]
    ]

    if not found:
        await query.edit_message_text(
            f"Для «{name}» пока нет доступных скриптов."
        )
        return

    await send_scripts_page(
        query,
        name,
        found,
        page=page,
        edit=True,
    )


async def script_details(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    script_id = query.data.replace("details:", "", 1)

    try:
        scripts = await load_scripts()
    except Exception:
        logger.exception("Ошибка чтения Google Таблицы")
        await query.message.reply_text(
            "Не удалось загрузить описание скрипта."
        )
        return

    item = next(
        (
            s for s in scripts
            if s["active"] and (
                s["id"] == script_id
                or clean(s["title"])[:20] == script_id
            )
        ),
        None,
    )

    if not item:
        await query.message.reply_text("Скрипт не найден.")
        return

    key_text = "Да" if item["key"] else "Нет"
    mobile_text = "Да" if item["mobile"] else "Нет"

    text = (
        f"<b>{item['title']}</b>\n\n"
        f"<b>Игра:</b> {item['game']}\n"
        f"<b>Описание:</b> {item['description'] or 'Не указано'}\n"
        f"<b>Нужен ключ:</b> {key_text}\n"
        f"<b>Инжектор:</b> {item['injector']}\n"
        f"<b>Мобильные:</b> {mobile_text}\n\n"
        f"<a href='{item['link']}'>Открыть скрипт</a>"
    )

    await query.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    await update.message.reply_text(
        "Как пользоваться ботом:\n\n"
        "/start — главное меню и выбор игры\n"
        "/games — список доступных игр\n"
        "/injectors — список доступных инжекторов\n"
        "/script blox fruits — найти скрипты по названию\n"
        "/help — эта справка\n\n"
        "Нажми «Подробнее», чтобы увидеть функции скрипта, "
        "нужен ли ключ, поддержку Solara/Xeno и мобильных устройств."
    )


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("games", games))
telegram_app.add_handler(CommandHandler("injectors", injectors))
telegram_app.add_handler(CommandHandler("script", script))
telegram_app.add_handler(CommandHandler("help", help_command))

telegram_app.add_handler(
    CallbackQueryHandler(check_subscription, pattern="^check_sub$")
)
telegram_app.add_handler(
    CallbackQueryHandler(games_page, pattern="^games_page:")
)
telegram_app.add_handler(
    CallbackQueryHandler(injectors_page, pattern="^injectors_page:")
)
telegram_app.add_handler(
    CallbackQueryHandler(game_button, pattern="^game:")
)
telegram_app.add_handler(
    CallbackQueryHandler(injector_button, pattern="^injector:")
)
telegram_app.add_handler(
    CallbackQueryHandler(scripts_page, pattern="^scripts_page:")
)
telegram_app.add_handler(
    CallbackQueryHandler(script_details, pattern="^details:")
)


@app.get("/")
async def health():
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, telegram_app.bot)
    await telegram_app.process_update(update)
    return {"ok": True}


@app.on_event("startup")
async def on_startup():
    await telegram_app.initialize()

    await telegram_app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("games", "Список доступных игр"),
        BotCommand("injectors", "Список инжекторов"),
        BotCommand("script", "Найти скрипты: /script blox fruits"),
        BotCommand("help", "Как пользоваться ботом"),
    ])

    await telegram_app.bot.set_webhook(f"{RENDER_URL}/webhook")
    await telegram_app.start()
