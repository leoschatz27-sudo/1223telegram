import asyncio
import base64
import datetime as dt
import email
import imaplib
import logging
import os
from collections import defaultdict
from email.header import decode_header
from pathlib import Path

import httpx
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from telegram import BotCommand, Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


BOT_DIR = Path(__file__).resolve().parent
GOOGLE_CREDENTIALS_FILE = BOT_DIR / "credentials.json"
GOOGLE_TOKEN_FILE = BOT_DIR / "google_token.json"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8861517323:AAGiZVzMYnIjyP9wb7Rsa3x_AzHYL8FqzPk")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
MAX_HISTORY_MESSAGES = 12
GMAIL_IMAP_EMAIL = os.getenv("GMAIL_IMAP_EMAIL") or os.getenv("GMAIL_ADDRESS")
GMAIL_IMAP_PASSWORD = os.getenv("GMAIL_IMAP_PASSWORD") or os.getenv("GMAIL_APP_PASSWORD")
LOCAL_DRIVE_PATH = Path(os.getenv("LOCAL_DRIVE_PATH", str(Path.home() / "Google Drive")))


if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError("Bitte TELEGRAM_BOT_TOKEN setzen.")


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.WARNING,
)
logger = logging.getLogger(__name__)

openai_client = None
if OPENAI_API_KEY:
    from openai import OpenAI

    openai_client = OpenAI(api_key=OPENAI_API_KEY)
chat_history = defaultdict(list)


SYSTEM_MESSAGE = {
    "role": "system",
    "content": (
        "Du bist CBC, ein hilfreicher, freundlicher und praktischer AI-Assistent in Telegram. "
        "Wenn dich jemand nach deinem Namen fragt, sag klar: Ich heisse CBC. "
        "Du erklaerst Dinge einfach, fuehrst Nutzer Schritt fuer Schritt und fragst nach, wenn wichtige Infos fehlen. "
        "Du hast nur Zugriff auf externe Apps, wenn sie ausdruecklich verbunden wurden. "
        "Du kannst Gmail per IMAP lesen, wenn GMAIL_ADDRESS und GMAIL_APP_PASSWORD gesetzt sind. "
        "Du kannst lokale Drive-Dateien lesen, wenn ein lokaler Drive-Ordner existiert. "
        "Wenn Nutzer nach E-Mails, Drive, Kalender oder Apps fragen, nenne die passenden Slash-Befehle. "
        "Antworte klar, direkt und in der Sprache des Nutzers."
    ),
}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Hi! Ich bin CBC. Schreib mir eine Frage oder nutze /apps fuer Verbindungen."
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_history[update.effective_chat.id].clear()
    await update.message.reply_text("Verlauf geloescht. Wir starten frisch.")


async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Ich heisse CBC. Ich bin dein Telegram-AI-Bot.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await apps(update, context)


async def apps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    google_status = "verbunden" if GOOGLE_TOKEN_FILE.exists() else "nicht verbunden"
    await update.message.reply_text(
        "App-Berechtigungen:\n"
        f"- Google/Gmail/Drive/Kalender: {google_status}\n"
        "- Instagram/Facebook: braucht Meta-Developer-App und Freigabe\n"
        "- Twitter/X: braucht X-Developer-Zugang/API\n\n"
        "Kostenlose Alternative ohne Kreditkarte:\n"
        "/gmail_free_setup - Gmail per App-Passwort einrichten\n"
        "/gmail_imap_recent - letzte Gmail-Mails per IMAP lesen\n"
        "/drive_local_recent - lokale Drive-Dateien anzeigen\n\n"
        "Google-Cloud-OAuth, falls du spaeter willst:\n"
        "/connect_google - Google verbinden\n"
        "/google_status - Verbindung pruefen\n"
        "/gmail_recent - letzte E-Mails anzeigen\n"
        "/calendar_today - heutige Termine anzeigen\n"
        "/drive_recent - neue Drive-Dateien anzeigen"
    )


async def smart_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Minimaler Platzhalter-Handler fuer /smart_setup.
    Ziel: NameError verhindern; keine Logik aendern.
    """
    await update.message.reply_text(
        "Smart-Setup ist noch nicht konfiguriert.\n" \
        "Nutze /apps, /connect_google oder setze Umgebungsvariablen fuer Gmail und Drive."
    )


def decode_mail_header(value: str | None) -> str:
    if not value:
        return ""
    parts = []
    for text, charset in decode_header(value):
        if isinstance(text, bytes):
            parts.append(text.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts)


async def gmail_free_setup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Kostenlose Gmail-Variante ohne Google Cloud und ohne Kreditkarte:\n\n"
        "1. In Google-Konto 2-Faktor-Anmeldung aktivieren\n"
        "2. Google App-Passwort erstellen fuer Mail\n"
        "3. Bot stoppen mit CTRL+C\n"
        "4. In PowerShell setzen:\n"
        "$env:GMAIL_ADDRESS=\"deine@gmail.com\"\n"
        "$env:GMAIL_APP_PASSWORD=\"dein app passwort\"\n"
        "5. Bot wieder starten\n\n"
        "Danach: /gmail_imap_recent"
    )


def gmail_imap_recent_sync() -> str:
    if not GMAIL_IMAP_EMAIL or not GMAIL_IMAP_PASSWORD:
        return (
            "Gmail IMAP ist noch nicht eingerichtet. Nutze /gmail_free_setup. "
            "Du brauchst nur ein kostenloses Google-App-Passwort, keine Kreditkarte."
        )

    with imaplib.IMAP4_SSL("imap.gmail.com") as mailbox:
        mailbox.login(GMAIL_IMAP_EMAIL, GMAIL_IMAP_PASSWORD)
        mailbox.select("INBOX")
        status, data = mailbox.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            return "Keine E-Mails gefunden."

        ids = data[0].split()[-5:]
        lines = ["Letzte Gmail-Mails per IMAP:"]
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


async def gmail_imap_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        text = await asyncio.to_thread(gmail_imap_recent_sync)
    except Exception as error:
        text = f"Gmail IMAP konnte nicht gelesen werden: {error}"
    await update.message.reply_text(text[:4000])


def drive_local_recent_sync() -> str:
    if not LOCAL_DRIVE_PATH.exists():
        return (
            "Lokaler Drive-Ordner nicht gefunden:\n"
            f"{LOCAL_DRIVE_PATH}\n\n"
            "Installiere Google Drive for Desktop oder setze in PowerShell z.B.:\n"
            "$env:LOCAL_DRIVE_PATH=\"C:\\Users\\leosc\\My Drive\""
        )

    files = []
    checked = 0
    for root, dirs, names in os.walk(LOCAL_DRIVE_PATH):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
        for name in names:
            file_path = Path(root) / name
            try:
                stat = file_path.stat()
            except OSError:
                continue
            files.append((stat.st_mtime, file_path))
            checked += 1
            if checked >= 5000:
                break
        if checked >= 5000:
            break

    if not files:
        return "Im lokalen Drive-Ordner wurden keine Dateien gefunden."

    files.sort(reverse=True)
    lines = [f"Neue lokale Drive-Dateien in {LOCAL_DRIVE_PATH}:"]
    for modified, file_path in files[:10]:
        changed = dt.datetime.fromtimestamp(modified).strftime("%Y-%m-%d %H:%M")
        lines.append(f"- {file_path.name}\n  Geaendert: {changed}\n  Pfad: {file_path}")
    return "\n".join(lines)


async def drive_local_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        text = await asyncio.to_thread(drive_local_recent_sync)
    except Exception as error:
        text = f"Lokaler Drive konnte nicht gelesen werden: {error}"
    await update.message.reply_text(text[:4000])

def load_google_credentials() -> Credentials | None:
    creds = None
    if GOOGLE_TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_FILE), GOOGLE_SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        GOOGLE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    if creds and creds.valid:
        return creds

    return None


def connect_google_sync() -> str:
    if not GOOGLE_CREDENTIALS_FILE.exists():
        return (
            "credentials.json fehlt. Lade sie aus Google Cloud herunter und lege sie hier ab:\n"
            f"{GOOGLE_CREDENTIALS_FILE}"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CREDENTIALS_FILE), GOOGLE_SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    GOOGLE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return "Google ist verbunden. CBC darf jetzt Gmail, Drive und Kalender lesen."


async def connect_google(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Ich starte gleich den Google-Login. Wenn sich ein Browserfenster oeffnet, "
        "melde dich an und erlaube den Zugriff."
    )
    result = await asyncio.to_thread(connect_google_sync)
    await update.message.reply_text(result)


async def google_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if load_google_credentials():
        await update.message.reply_text("Google ist verbunden: Gmail, Drive und Kalender sind lesbar.")
    elif GOOGLE_CREDENTIALS_FILE.exists():
        await update.message.reply_text("credentials.json ist da, aber Google ist noch nicht verbunden. Nutze /connect_google.")
    else:
        await update.message.reply_text(
            "Google ist noch nicht vorbereitet. Lege credentials.json in diesen Ordner:\n"
            f"{GOOGLE_CREDENTIALS_FILE}"
        )


def google_service(name: str, version: str):
    creds = load_google_credentials()
    if not creds:
        raise RuntimeError("Google ist nicht verbunden. Nutze zuerst /connect_google.")
    return build(name, version, credentials=creds)


def gmail_recent_sync() -> str:
    service = google_service("gmail", "v1")
    result = service.users().messages().list(userId="me", maxResults=5, labelIds=["INBOX"]).execute()
    messages = result.get("messages", [])
    if not messages:
        return "Keine E-Mails im Posteingang gefunden."

    lines = ["Letzte E-Mails:"]
    for item in messages:
        msg = service.users().messages().get(
            userId="me",
            id=item["id"],
            format="metadata",
            metadataHeaders=["From", "Subject", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        lines.append(
            f"- {headers.get('Subject', '(ohne Betreff)')}\n"
            f"  Von: {headers.get('From', 'unbekannt')}\n"
            f"  Datum: {headers.get('Date', 'unbekannt')}"
        )
    return "\n".join(lines)


async def gmail_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        text = await asyncio.to_thread(gmail_recent_sync)
    except Exception as error:
        text = f"Gmail konnte nicht gelesen werden: {error}"
    await update.message.reply_text(text[:4000])


def calendar_today_sync() -> str:
    service = google_service("calendar", "v3")
    now = dt.datetime.now(dt.timezone.utc)
    end = now + dt.timedelta(days=1)
    result = service.events().list(
        calendarId="primary",
        timeMin=now.isoformat(),
        timeMax=end.isoformat(),
        singleEvents=True,
        orderBy="startTime",
        maxResults=10,
    ).execute()
    events = result.get("items", [])
    if not events:
        return "Heute stehen keine weiteren Termine im Kalender."

    lines = ["Heutige Termine:"]
    for event in events:
        start = event.get("start", {}).get("dateTime") or event.get("start", {}).get("date")
        lines.append(f"- {start}: {event.get('summary', '(ohne Titel)')}")
    return "\n".join(lines)


async def calendar_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        text = await asyncio.to_thread(calendar_today_sync)
    except Exception as error:
        text = f"Kalender konnte nicht gelesen werden: {error}"
    await update.message.reply_text(text[:4000])


def drive_recent_sync() -> str:
    service = google_service("drive", "v3")
    result = service.files().list(
        pageSize=10,
        orderBy="modifiedTime desc",
        fields="files(name,mimeType,modifiedTime,webViewLink)",
    ).execute()
    files = result.get("files", [])
    if not files:
        return "Keine Drive-Dateien gefunden."

    lines = ["Neue Drive-Dateien:"]
    for file in files:
        lines.append(
            f"- {file.get('name', '(ohne Name)')}\n"
            f"  Geaendert: {file.get('modifiedTime', 'unbekannt')}\n"
            f"  Link: {file.get('webViewLink', 'kein Link')}"
        )
    return "\n".join(lines)


async def drive_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        text = await asyncio.to_thread(drive_recent_sync)
    except Exception as error:
        text = f"Drive konnte nicht gelesen werden: {error}"
    await update.message.reply_text(text[:4000])


async def connect_meta(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Instagram/Facebook geht nur ueber eine Meta-Developer-App. Viele Rechte "
        "brauchen App Review. Das ist machbar, aber aufwendiger als Google."
    )


async def connect_x(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Twitter/X braucht einen X-Developer-Zugang und API-Schluessel. Ohne diese "
        "Schluessel kann CBC dort nichts lesen oder posten."
    )


async def ask_openai(messages: list[dict[str, str]]) -> str:
    def create_response() -> str:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.7,
        )
        return response.choices[0].message.content or ""

    return await asyncio.to_thread(create_response)


async def ask_ollama(messages: list[dict[str, str]]) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.7},
    }

    async with httpx.AsyncClient(timeout=120) as http_client:
        response = await http_client.post(OLLAMA_URL, json=payload)
        response.raise_for_status()
        data = response.json()
        return data.get("message", {}).get("content", "")


async def ask_ai(chat_id: int, user_text: str) -> str:
    history = chat_history[chat_id]
    history.append({"role": "user", "content": user_text})
    del history[:-MAX_HISTORY_MESSAGES]

    messages = [SYSTEM_MESSAGE, *history]

    if openai_client:
        answer = await ask_openai(messages)
    else:
        try:
            answer = await ask_ollama(messages)
        except httpx.HTTPError as error:
            logger.exception("Ollama request failed")
            raise RuntimeError(
                "Keine lokale AI gefunden. Starte Ollama und lade ein Modell mit: "
                "ollama pull llama3.2"
            ) from error

    history.append({"role": "assistant", "content": answer})
    del history[:-MAX_HISTORY_MESSAGES]
    return answer


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_text = update.message.text.strip()

    if not user_text:
        return

    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    try:
        answer = await ask_ai(chat_id, user_text)
    except Exception as error:
        logger.exception("AI request failed")
        await update.message.reply_text(f"Sorry, die AI laeuft gerade nicht: {error}")
        return

    for chunk_start in range(0, len(answer), 4000):
        await update.message.reply_text(answer[chunk_start : chunk_start + 4000])


async def setup_bot_commands(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "CBC starten"),
            BotCommand("help", "Befehle anzeigen"),
            BotCommand("apps", "App-Verbindungen anzeigen"),
            BotCommand("app", "App-Verbindungen anzeigen"),
            BotCommand("whoami", "Name von CBC anzeigen"),
            BotCommand("smart_setup", "CBC schlauer machen"),
            BotCommand("gmail_free_setup", "Gmail kostenlos einrichten"),
            BotCommand("gmail_imap_recent", "Gmail per IMAP lesen"),
            BotCommand("drive_local_recent", "Lokale Drive-Dateien"),
            BotCommand("connect_google", "Google verbinden"),
            BotCommand("google_status", "Google-Verbindung pruefen"),
            BotCommand("gmail_recent", "Letzte E-Mails anzeigen"),
            BotCommand("calendar_today", "Heutige Termine anzeigen"),
            BotCommand("drive_recent", "Neue Drive-Dateien anzeigen"),
        ]
    )


def main() -> None:
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(setup_bot_commands).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset", reset))
    application.add_handler(CommandHandler("whoami", whoami))
    application.add_handler(CommandHandler("apps", apps))
    application.add_handler(CommandHandler("app", apps))
    application.add_handler(CommandHandler("smart_setup", smart_setup))
    application.add_handler(CommandHandler("gmail_free_setup", gmail_free_setup))
    application.add_handler(CommandHandler("gmail_imap_recent", gmail_imap_recent))
    application.add_handler(CommandHandler("drive_local_recent", drive_local_recent))
    application.add_handler(CommandHandler("connect_google", connect_google))
    application.add_handler(CommandHandler("google_status", google_status))
    application.add_handler(CommandHandler("gmail_recent", gmail_recent))
    application.add_handler(CommandHandler("calendar_today", calendar_today))
    application.add_handler(CommandHandler("drive_recent", drive_recent))
    application.add_handler(CommandHandler("connect_meta", connect_meta))
    application.add_handler(CommandHandler("connect_x", connect_x))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()




