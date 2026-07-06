import email
import imaplib
import os
from email.header import decode_header
from urllib.parse import quote

import requests
from flask import Flask, abort, request


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8861517323:AAGiZVzMYnIjyP9wb7Rsa3x_AzHYL8FqzPk")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "cbc-webhook")
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")
POLLINATIONS_MODEL = os.getenv("POLLINATIONS_MODEL", "openai")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = Flask(__name__)
chat_history = {}

SYSTEM_PROMPT = (
    "Du bist CBC, ein freundlicher und praktischer Telegram-AI-Assistent. "
    "Du antwortest kurz, klar und in der Sprache des Nutzers. "
    "Wenn dich jemand nach deinem Namen fragt, sag: Ich heisse CBC. "
    "Du laeufst auf PythonAnywhere, damit du auch antwortest, wenn der PC des Nutzers aus ist."
)


def telegram_send(chat_id, text):
    if not text:
        text = "Ich habe gerade keine Antwort bekommen."
    for start in range(0, len(text), 3900):
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text[start : start + 3900]},
            timeout=20,
        )


def decode_mail_header(value):
    if not value:
        return ""
    parts = []
    for text, charset in decode_header(value):
        if isinstance(text, bytes):
            parts.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts)


def gmail_recent():
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        return (
            "Gmail ist noch nicht eingerichtet. Setze auf PythonAnywhere unter Web -> Environment variables:\n"
            "GMAIL_ADDRESS = deine@gmail.com\n"
            "GMAIL_APP_PASSWORD = dein Google App-Passwort"
        )

    with imaplib.IMAP4_SSL("imap.gmail.com") as mailbox:
        mailbox.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        mailbox.select("INBOX")
        status, data = mailbox.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return "Keine E-Mails gefunden."

        ids = data[0].split()[-5:]
        lines = ["Letzte Gmail-Mails:"]
        for message_id in reversed(ids):
            status, msg_data = mailbox.fetch(message_id, "(RFC822.HEADER)")
            if status != "OK" or not msg_data:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            subject = decode_mail_header(msg.get("Subject")) or "(ohne Betreff)"
            sender = decode_mail_header(msg.get("From")) or "unbekannt"
            date = decode_mail_header(msg.get("Date")) or "unbekannt"
            lines.append(f"- {subject}\n  Von: {sender}\n  Datum: {date}")

    return "\n".join(lines)


def ask_free_ai(chat_id, user_text):
    history = chat_history.setdefault(chat_id, [])
    history.append(f"User: {user_text}")
    del history[:-8]

    prompt = SYSTEM_PROMPT + "\n\n" + "\n".join(history) + "\nCBC:"
    url = f"https://text.pollinations.ai/{quote(prompt)}"
    response = requests.get(url, params={"model": POLLINATIONS_MODEL}, timeout=60)
    response.raise_for_status()
    answer = response.text.strip()

    history.append(f"CBC: {answer}")
    del history[:-8]
    return answer


def handle_text(chat_id, text):
    command = text.strip().split()[0].lower() if text.strip() else ""

    if command in {"/start", "/help", "/app", "/apps"}:
        return (
            "Hi, ich bin CBC. Ich laufe auf PythonAnywhere und kann antworten, auch wenn dein PC aus ist.\n\n"
            "Befehle:\n"
            "/gmail_recent - letzte Gmail-Mails lesen\n"
            "/status - Status anzeigen\n"
            "/help - Hilfe anzeigen\n\n"
            "Normale Nachrichten beantworte ich mit kostenloser Web-AI."
        )

    if command == "/status":
        gmail = "eingerichtet" if GMAIL_ADDRESS and GMAIL_APP_PASSWORD else "nicht eingerichtet"
        return f"CBC online auf PythonAnywhere. Gmail: {gmail}."

    if command in {"/gmail_recent", "/gmail_imap_recent"}:
        return gmail_recent()

    return ask_free_ai(chat_id, text)


@app.get("/")
def index():
    return "CBC Telegram bot is online."


@app.post(f"/webhook/{WEBHOOK_SECRET}")
def webhook():
    update = request.get_json(silent=True) or {}
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    text = message.get("text")

    if not chat_id or not text:
        return {"ok": True}

    try:
        answer = handle_text(chat_id, text)
    except Exception as error:
        answer = f"Sorry, CBC hatte einen Fehler: {error}"

    telegram_send(chat_id, answer)
    return {"ok": True}


@app.post("/webhook/<wrong_secret>")
def wrong_webhook(wrong_secret):
    abort(404)
