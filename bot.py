"""
@operab2bbot — Telegram bot
Features: Currency conversion + plagiarism (web similarity) checking

Environment variables (set these in Railway's Variables tab):
    TELEGRAM_BOT_TOKEN   -> from @BotFather
    SERPER_API_KEY       -> optional, from https://serper.dev (free tier: 2500 searches/month)
                             Powers the plagiarism/similarity checker.
                             If not set, /plagiarism will tell users the
                             feature isn't configured yet — currency
                             conversion works fine without it.

Run locally:
    pip install -r requirements.txt
    export TELEGRAM_BOT_TOKEN="your_token"
    export SERPER_API_KEY="your_serper_key"   # optional
    python bot.py
"""

import os
import re
import logging
import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SERPER_API_KEY = os.getenv("SERPER_API_KEY")

# ---------------------------------------------------------------------------
# CURRENCY CONVERTER
# Uses Frankfurter API — fully free, no API key, no rate limits.
# Backed by European Central Bank reference rates.
# ---------------------------------------------------------------------------

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"

CURRENCY_PATTERN = re.compile(
    r"^\s*([\d.,]+)\s*([a-zA-Z]{3})\s*(?:to|in|->|=)\s*([a-zA-Z]{3})\s*$",
    re.IGNORECASE,
)


def convert_currency(amount: float, base: str, target: str) -> dict:
    params = {"base": base.upper(), "symbols": target.upper()}
    resp = requests.get(FRANKFURTER_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    rate = data["rates"].get(target.upper())
    if rate is None:
        raise ValueError(f"Unknown currency code: {target.upper()}")
    return {
        "amount": amount,
        "base": base.upper(),
        "target": target.upper(),
        "rate": rate,
        "result": round(amount * rate, 4),
        "date": data.get("date"),
    }


async def convert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) != 3:
        await update.message.reply_text(
            "Usage: /convert <amount> <from> <to>\nExample: /convert 100 USD EUR"
        )
        return

    amount_str, base, target = args
    try:
        amount = float(amount_str.replace(",", ""))
    except ValueError:
        await update.message.reply_text("Amount must be a number, e.g. 100 or 99.5")
        return

    try:
        result = convert_currency(amount, base, target)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return
    except requests.RequestException:
        await update.message.reply_text(
            "Currency service is temporarily unavailable. Try again shortly."
        )
        return

    await update.message.reply_text(
        f"💱 {result['amount']:,.2f} {result['base']} = "
        f"{result['result']:,.4f} {result['target']}\n"
        f"Rate: 1 {result['base']} = {result['rate']} {result['target']} "
        f"(as of {result['date']})"
    )


async def natural_language_convert(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    text = update.message.text or ""
    match = CURRENCY_PATTERN.match(text)
    if not match:
        return False

    amount_str, base, target = match.groups()
    try:
        amount = float(amount_str.replace(",", ""))
        result = convert_currency(amount, base, target)
    except (ValueError, requests.RequestException) as e:
        await update.message.reply_text(f"Couldn't convert that: {e}")
        return True

    await update.message.reply_text(
        f"💱 {result['amount']:,.2f} {result['base']} = "
        f"{result['result']:,.4f} {result['target']}\n"
        f"Rate: 1 {result['base']} = {result['rate']} {result['target']} "
        f"(as of {result['date']})"
    )
    return True


# ---------------------------------------------------------------------------
# PLAGIARISM / WEB SIMILARITY CHECKER
# ---------------------------------------------------------------------------

SERPER_URL = "https://google.serper.dev/search"


def split_into_sentences(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return [s.strip() for s in sentences if len(s.strip().split()) >= 6]


def search_snippet_match(sentence: str) -> dict | None:
    if not SERPER_API_KEY:
        return None

    query = f'"{sentence}"'
    headers = {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}
    payload = {"q": query, "num": 3}

    try:
        resp = requests.post(SERPER_URL, json=payload, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return None

    organic = data.get("organic", [])
    if not organic:
        return None

    top = organic[0]
    return {
        "sentence": sentence,
        "source_title": top.get("title"),
        "source_link": top.get("link"),
    }


async def plagiarism_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not SERPER_API_KEY:
        await update.message.reply_text(
            "Plagiarism checking isn't configured yet.\n\n"
            "This feature needs a free Serper.dev API key (2,500 free "
            "searches/month) set as the SERPER_API_KEY environment variable. "
            "Get one at https://serper.dev, then add it in Railway's "
            "Variables tab and redeploy."
        )
        return

    text = " ".join(context.args)
    if not text:
        await update.message.reply_text(
            "Usage: /plagiarism <text to check>\n\n"
            "Note: this checks each sentence against Google search results. "
            "It catches text copied verbatim from indexed web pages, but "
            "isn't a substitute for a full plagiarism-detection service."
        )
        return

    await run_plagiarism_check(update, text)


async def run_plagiarism_check(update: Update, text: str):
    sentences = split_into_sentences(text)
    if not sentences:
        await update.message.reply_text(
            "Text is too short to check meaningfully — try at least one full sentence."
        )
        return

    await update.message.reply_text(
        f"Checking {len(sentences)} sentence(s) against the web... this may take a moment."
    )

    matches = []
    checked = 0
    for sentence in sentences[:20]:
        result = search_snippet_match(sentence)
        checked += 1
        if result:
            matches.append(result)

    if not matches:
        await update.message.reply_text(
            f"✅ No exact matches found for {checked} sentence(s) checked.\n"
            "Note: this only detects text copied verbatim and indexed by "
            "Google — paraphrased plagiarism or unindexed sources won't be caught."
        )
        return

    score = round(100 * len(matches) / checked)
    lines = [f"⚠️ Possible matches found — {score}% of checked sentences flagged:\n"]
    for m in matches[:10]:
        lines.append(f"• \"{m['sentence'][:80]}...\"")
        lines.append(f"  ↳ {m['source_title']}\n  {m['source_link']}\n")

    await update.message.reply_text("\n".join(lines))


# ---------------------------------------------------------------------------
# GENERAL
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Hi, I'm @operab2bbot!\n\n"
        "💱 Currency conversion\n"
        "   /convert 100 USD EUR\n"
        "   or just type: 100 usd to eur\n\n"
        "📝 Plagiarism / similarity check\n"
        "   /plagiarism <your text>\n"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start_command(update, context)


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handled = await natural_language_convert(update, context)
    if not handled:
        await update.message.reply_text(
            "I didn't catch that. Try:\n"
            "• 100 usd to eur\n"
            "• /plagiarism <text>\n"
            "• /help"
        )


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN environment variable is not set. "
            "Set it in Railway's Variables tab (or locally before running)."
        )

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("convert", convert_command))
    app.add_handler(CommandHandler("plagiarism", plagiarism_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("operab2bbot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
