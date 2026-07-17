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
# Uses open.er-api.com — free, no API key, no rate limits, covers ~160
# currencies including ones the ECB-backed Frankfurter API doesn't (e.g. NGN).
# ---------------------------------------------------------------------------

EXCHANGE_RATE_URL = "https://open.er-api.com/v6/latest/{base}"

CURRENCY_PATTERN = re.compile(
    r"^\s*([\d.,]+)\s*([a-zA-Z]{3})\s*(?:to|in|->|=)\s*([a-zA-Z]{3})\s*$",
    re.IGNORECASE,
)


def convert_currency(amount: float, base: str, target: str) -> dict:
    url = EXCHANGE_RATE_URL.format(base=base.upper())
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()

    if data.get("result") != "success":
        raise ValueError(f"Unknown currency code: {base.upper()}")

    rate = data["rates"].get(target.upper())
    if rate is None:
        raise ValueError(f"Unknown currency code: {target.upper()}")

    return {
        "amount": amount,
        "base": base.upper(),
        "target": target.upper(),
        "rate": rate,
        "result": round(amount * rate, 4),
        "date": data.get("time_last_update_utc", "").split(" GMT")[0],
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
#
# HONESTY NOTE: no free API gives true, comprehensive plagiarism detection
# (that needs a massive proprietary indexed corpus, like Turnitin has). This
# checker approximates it: it breaks submitted text into sentences, searches
# the web for each one via Serper.dev (free tier: 2500 searches/month), and
# flags sentences that appear to match indexed web pages.
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
