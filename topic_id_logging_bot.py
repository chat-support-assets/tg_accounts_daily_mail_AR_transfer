import asyncio
import logging
import os
import pickle
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from googleapiclient.discovery import build
from telethon import TelegramClient, events
from telethon.errors import (
    UserAlreadyParticipantError,
    FloodWaitError,
    UserPrivacyRestrictedError
)
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import Channel

# ============================================================
# Загрузка .env файла
# ============================================================
# Ищем .env в текущей директории или на уровень выше
env_path = Path(__file__).parent / '.env'
if not env_path.exists():
    env_path = Path(__file__).parent.parent / '.env'
    
load_dotenv(env_path)

# ============================================================
# Настройка логирования
# ============================================================
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'topic_fetcher.log'),
        logging.StreamHandler()
    ]
)

# ============================================================
# Конфигурация из переменных окружения
# ============================================================
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
LOG_CHAT_ID = int(os.getenv('LOG_CHAT_ID', 0))

# Сессии храним в папке sessions
SESSION_DIR = Path(__file__).parent / 'sessions'
SESSION_DIR.mkdir(exist_ok=True)
SESSION_NAME = str(SESSION_DIR / os.getenv('SESSION_NAME', 'topic_logger_session'))

# Google Sheets
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '')
TOPIC_SHEET_NAME = os.getenv('TOPIC_SHEET_NAME', 'test_sheet')
SERVICE_ACCOUNT_FILE = Path(__file__).parent / 'service-account-key.json'
GOOGLE_SERVICE_ACCOUNT_FILE = str(SERVICE_ACCOUNT_FILE)

# ============================================================
# Google Sheets с Service Account (impersonation)
# ============================================================
SHEET_HEADER = ['Group name', 'Group ID', 'Topic title', 'Topic ID']
SHEET_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


def get_sheets_service():
    """Получение сервиса Google Sheets через Service Account"""
    if not GOOGLE_SERVICE_ACCOUNT_FILE:
        logging.error("GOOGLE_SERVICE_ACCOUNT_FILE not set in .env")
        return None
    
    service_account_path = Path(GOOGLE_SERVICE_ACCOUNT_FILE)
    if not service_account_path.exists():
        logging.error(f"Service account file not found: {service_account_path}")
        return None
    
    try:
        # Используем service account напрямую (impersonation не нужен для sheets)
        credentials = service_account.Credentials.from_service_account_file(
            service_account_path,
            scopes=SHEET_SCOPES
        )
        return build('sheets', 'v4', credentials=credentials)
    except Exception as e:
        logging.error(f"Failed to build Sheets service: {e}")
        return None


def get_existing_group_ids() -> set:
    """Возвращает set Group IDs, которые уже есть в таблице"""
    service = get_sheets_service()
    if not service:
        return set()
    
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{TOPIC_SHEET_NAME}!B:B",
        ).execute()
        rows = result.get('values', [])
        return {row[0].strip() for row in rows[1:] if row}
    except Exception as e:
        logging.error(f"Failed to read sheet: {e}")
        return set()


def ensure_header(service):
    """Создает заголовок, если таблица пустая"""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{TOPIC_SHEET_NAME}!A1:D1",
        ).execute()
        if not result.get('values'):
            service.spreadsheets().values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{TOPIC_SHEET_NAME}!A1",
                valueInputOption='RAW',
                body={'values': [SHEET_HEADER]},
            ).execute()
    except Exception as e:
        logging.error(f"Failed to ensure header: {e}")


def append_topics_to_sheet(group_name, chat_id, topics):
    """Добавляет строки с топиками в Google Sheet"""
    service = get_sheets_service()
    if not service:
        return
    
    ensure_header(service)
    
    if topics:
        rows = [[group_name, str(chat_id), t.title, str(t.id)] for t in topics]
    else:
        rows = [[group_name, str(chat_id), '', '']]
    
    try:
        service.spreadsheets().values().append(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{TOPIC_SHEET_NAME}!A:D",
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': rows},
        ).execute()
        logging.info(f"Sheet updated for {group_name} ({chat_id}): {len(rows)} row(s)")
    except Exception as e:
        logging.error(f"Failed to append to sheet: {e}")



# ============================================================
# Основная логика
# ============================================================
async def fetch_and_send_topics(client, chat_id, group_name):
    """Получает топики форума и отправляет в лог-чат и Google Sheets"""
    try:
        result = await client(GetForumTopicsRequest(
            peer=chat_id,
            offset_date=None,
            offset_id=0,
            offset_topic=0,
            limit=100
        ))
        topics = result.topics if result.topics else []

        # Формируем сообщение
        if not topics:
            msg = (
                f"📋 <b>Bot added to:</b> {group_name}\n"
                f"🔖 <b>Group ID:</b> <code>{chat_id}</code>\n\n"
                f"ℹ️ No forum topics found (topics may not be enabled)."
            )
        else:
            lines = "\n".join(
                f"  • <b>{t.title}</b> — <code>{t.id}</code>"
                for t in topics
            )
            msg = (
                f"📋 <b>Bot added to:</b> {group_name}\n"
                f"🔖 <b>Group ID:</b> <code>{chat_id}</code>\n\n"
                f"🗂 <b>Topics ({len(topics)}):</b>\n{lines}"
            )
        
        await client.send_message(LOG_CHAT_ID, msg, parse_mode='html')
        logging.info(f"Alert sent for {group_name} ({chat_id})")

        # Запись в Google Sheets
        existing = get_existing_group_ids()
        if str(chat_id) in existing:
            logging.info(f"Group {group_name} ({chat_id}) already in sheet — skipping")
        else:
            append_topics_to_sheet(group_name, chat_id, topics)

    except Exception as e:
        logging.error(f"Failed to fetch topics for {chat_id}: {e}")
        try:
            await client.send_message(
                LOG_CHAT_ID,
                f"⚠️ Bot added to <b>{group_name}</b> (<code>{chat_id}</code>)\n"
                f"Could not fetch topics: <code>{e}</code>",
                parse_mode='html'
            )
        except Exception:
            pass


# ============================================================
# Main
# ============================================================
async def main():
    # Проверка обязательных переменных
    required_vars = [
        ('API_ID', API_ID), ('API_HASH', API_HASH), ('LOG_CHAT_ID', LOG_CHAT_ID)
    ]
    
    missing = [name for name, value in required_vars if not value]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
    
    # Создаем клиента
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()
    
    me = await client.get_me()
    logging.info(f"Logged in as {me.first_name} (@{me.username})")
    
    logging.info("Running. Waiting for bot to be added to chats...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())