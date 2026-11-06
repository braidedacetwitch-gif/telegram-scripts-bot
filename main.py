import csv
import io
import logging
import os

import httpx
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

app = FastAPI()
telegram_app = Application.builder().token(TOKEN).build()


def clean(value) -> str:
    return str(value or "").replace("\ufeff", "").strip().lower()


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

    for row in reader:
        game = clean(row.get("game"))
        title = clean(row.get("title"))
        link = clean(row.get("link"))
        active = clean(row.get("active"))

        scripts.append({
            "game": game,
            "title": title,
            "link": link,
            "active": active in ("yes", "да", "1", "true"),
        })

    logger.info("CSV колонки: %s", reader.fieldnames)
    logger.info("Загружено строк: %s", len(scripts))

    return scripts


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
        "Привет!\n\n"
        "Отправь команду в формате:\n"
        "/script blox fruits\n\n"
        "Я пришлю список доступных скриптов."
    )


async def check_subscription(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    if await is_subscribed(query.from_user.id):
        await query.edit_message_text(
            "Спасибо за подписку!\n\n"
            "Теперь отправь команду:\n"
            "/script blox fruits"
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

    game = " ".join(context.args).strip().lower()

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
        if item["game"] == game and item["active"]
    ]

    logger.info("Найдено скриптов: %s", len(found))

    if not found:
        await update.message.reply_text(
            f"По запросу «{game}» скрипты не найдены.\n"
            "Проверь название или попробуй позже."
        )
        return

    text = f"Скрипты для {game}:\n\n"

    for item in found:
        text += f"{item['title']}\n{item['link']}\n\n"

    await update.message.reply_text(
        text,
        disable_web_page_preview=True,
    )


async def debug(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with httpx.AsyncClient(
            timeout=15,
            follow_redirects=True
        ) as client:
            response = await client.get(SHEET_CSV_URL)
            response.raise_for_status()

        csv_text = response.text
        reader = csv.DictReader(io.StringIO(csv_text))
        rows = list(reader)

        columns = list(rows[0].keys()) if rows else []
        first_row = rows[0] if rows else {}

        await update.message.reply_text(
            "Первые 1500 символов CSV:\n\n"
            f"{csv_text[:1500]}\n\n"
            f"Колонки: {columns}\n\n"
            f"Всего строк: {len(rows)}\n\n"
            f"Первая строка: {first_row}"
        )

    except Exception as error:
        logger.exception("Ошибка команды /debug")
        await update.message.reply_text(f"Ошибка: {error}")


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("script", script))
telegram_app.add_handler(CommandHandler("debug", debug))
telegram_app.add_handler(
    CallbackQueryHandler(check_subscription, pattern="^check_sub$")
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
    await telegram_app.bot.set_webhook(f"{RENDER_URL}/webhook")
    await telegram_app.start()
