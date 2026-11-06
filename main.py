import csv
import io
import os

import httpx
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler,
)

TOKEN = os.environ["BOT_TOKEN"]
CHANNEL = os.environ["CHANNEL_USERNAME"]
SHEET_CSV_URL = os.environ["SHEET_CSV_URL"]
RENDER_URL = os.environ["RENDER_URL"]

app = FastAPI()
telegram_app = Application.builder().token(TOKEN).build()


async def is_subscribed(user_id: int) -> bool:
    try:
        member = await telegram_app.bot.get_chat_member(CHANNEL, user_id)
        return member.status not in ("left", "kicked")
    except Exception:
        return False


async def load_scripts() -> list[dict]:
    async with httpx.AsyncClient(
    timeout=15,
    follow_redirects=True
) as client:
        response = await client.get(SHEET_CSV_URL)
        response.raise_for_status()

    reader = csv.DictReader(io.StringIO(response.text))

    return [
        {
            "game": (row.get("game") or "").strip().lower(),
            "title": (row.get("title") or "").strip(),
            "link": (row.get("link") or "").strip(),
            "active": (row.get("active") or "").strip().lower()
            in ("yes", "да", "1", "true"),
        }
        for row in reader
    ]


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
        "`/script blox fruits`\n\n"
        "Я пришлю список доступных скриптов.",
        parse_mode=ParseMode.MARKDOWN,
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
            "`/script blox fruits`",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await query.answer(
            "Пока не вижу подписку. Подпишись и попробуй снова.",
            show_alert=True,
        )


async def script(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await is_subscribed(user_id):
        await update.message.reply_text(
            f"Сначала подпишись на канал: "
            f"https://t.me/{CHANNEL.lstrip('@')}"
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Укажи игру: `/script blox fruits`"
        )
        return

    game = " ".join(context.args).strip().lower()
    scripts = await load_scripts()
    found = [
        s for s in scripts
        if s["game"] == game and s["active"]
    ]

    if not found:
        await update.message.reply_text(
            f"По запросу «{game}» скрипты не найдены.\n"
            "Проверь название или попробуй позже."
        )
        return

    lines = [
        f"**{s['title']}**\n{s['link']}"
        for s in found
    ]

    await update.message.reply_text(
        f"Скрипты для **{game}**:\n\n" + "\n\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


telegram_app.add_handler(CommandHandler("start", start))
telegram_app.add_handler(CommandHandler("script", script))
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
