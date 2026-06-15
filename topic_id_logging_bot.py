import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from telethon import events
from telethon.errors import UserAlreadyParticipantError, FloodWaitError, UserPrivacyRestrictedError
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.messages import GetForumTopicsRequest
from telethon.tl.types import Channel
from tenacity import retry, stop_after_attempt, wait_exponential

from session_manager import SessionManager, SessionHealthChecker

# Загрузка .env
load_dotenv()

# ============================================================
# Настройка логирования
# ============================================================
LOG_DIR = Path(__file__).parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'topic_fetcher.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================
# Конфигурация
# ============================================================
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
TRACKED_BOT_ID = int(os.getenv('TRACKED_BOT_ID', 0))
LOG_CHAT_ID = int(os.getenv('LOG_CHAT_ID', 0))

SESSION_DIR = Path(__file__).parent / 'sessions'
SESSION_DIR.mkdir(exist_ok=True)
SESSION_PATH = str(SESSION_DIR / 'topic_logger_session')

# Google Sheets
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '')
TOPIC_SHEET_NAME = os.getenv('TOPIC_SHEET_NAME', 'test_sheet')
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE', '')

# Пользователи для добавления
ONBOARD_USER_IDS = [
    int(x.strip()) for x in os.getenv('ONBOARD_USER_IDS', '').split(',') if x.strip()
]

# ============================================================
# Google Sheets (с retry)
# ============================================================
SHEET_HEADER = ['Group name', 'Group ID', 'Topic title', 'Topic ID']
SHEET_SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_sheets_service():
    """Получение сервиса Google Sheets с retry"""
    if not GOOGLE_SERVICE_ACCOUNT_FILE or not Path(GOOGLE_SERVICE_ACCOUNT_FILE).exists():
        logger.error(f"Service account file not found: {GOOGLE_SERVICE_ACCOUNT_FILE}")
        return None
    
    try:
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_FILE,
            scopes=SHEET_SCOPES
        )
        return build('sheets', 'v4', credentials=credentials)
    except Exception as e:
        logger.error(f"Failed to build Sheets service: {e}")
        raise


def get_existing_group_ids() -> set:
    """Возвращает set Group IDs уже в таблице"""
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
        logger.error(f"Failed to read sheet: {e}")
        return set()


def append_topics_to_sheet(group_name, chat_id, topics):
    """Добавляет топики в Google Sheet"""
    service = get_sheets_service()
    if not service:
        return
    
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
        logger.info(f"Sheet updated for {group_name} ({chat_id}): {len(rows)} row(s)")
    except Exception as e:
        logger.error(f"Failed to append to sheet: {e}")


# ============================================================
# Добавление пользователей
# ============================================================
_onboard_lock = asyncio.Semaphore(1)


async def add_onboard_members(client, chat_id, group_name):
    """Добавляет пользователей в группу"""
    if not ONBOARD_USER_IDS:
        return
    
    async with _onboard_lock:
        await _do_add_onboard_members(client, chat_id, group_name)
        await asyncio.sleep(30)


async def _do_add_onboard_members(client, chat_id, group_name):
    added, already_in, failed = [], [], []
    
    for user_id in ONBOARD_USER_IDS:
        try:
            await client(InviteToChannelRequest(chat_id, [user_id]))
            logger.info(f"Added {user_id} to {group_name}")
            added.append(user_id)
            await asyncio.sleep(3)
        except UserAlreadyParticipantError:
            already_in.append(user_id)
        except FloodWaitError as e:
            logger.warning(f"FloodWait {e.seconds}s")
            await asyncio.sleep(e.seconds + 5)
            try:
                await client(InviteToChannelRequest(chat_id, [user_id]))
                added.append(user_id)
            except Exception as retry_err:
                failed.append((user_id, str(retry_err)))
        except Exception as e:
            failed.append((user_id, str(e)))
    
    logger.info(f"Onboarding for {group_name}: added={len(added)} already={len(already_in)} failed={len(failed)}")


# ============================================================
# Основная логика
# ============================================================
async def fetch_and_send_topics(client, chat_id, group_name):
    """Получает топики и отправляет алерт"""
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
        if topics:
            lines = "\n".join(f"  • <b>{t.title}</b> — <code>{t.id}</code>" for t in topics)
            msg = (
                f"📋 <b>Bot added to:</b> {group_name}\n"
                f"🔖 <b>Group ID:</b> <code>{chat_id}</code>\n\n"
                f"🗂 <b>Topics ({len(topics)}):</b>\n{lines}"
            )
        else:
            msg = (
                f"📋 <b>Bot added to:</b> {group_name}\n"
                f"🔖 <b>Group ID:</b> <code>{chat_id}</code>\n\n"
                f"ℹ️ No forum topics found"
            )
        
        await client.send_message(LOG_CHAT_ID, msg, parse_mode='html')
        logger.info(f"Alert sent for {group_name}")
        
        # Запись в Google Sheets
        existing = get_existing_group_ids()
        if str(chat_id) not in existing:
            append_topics_to_sheet(group_name, chat_id, topics)
            
    except Exception as e:
        logger.error(f"Failed to fetch topics: {e}")
        try:
            await client.send_message(
                LOG_CHAT_ID,
                f"⚠️ Bot added to <b>{group_name}</b> (<code>{chat_id}</code>)\n"
                f"Error: <code>{e}</code>",
                parse_mode='html'
            )
        except Exception:
            pass


# ============================================================
# Main
# ============================================================
async def send_alert(message: str):
    """Отправляет алерт админу"""
    try:
        # Временно создаем клиента для отправки
        async with SessionManager(SESSION_PATH, API_ID, API_HASH) as sm:
            client = await sm.get_client()
            await client.send_message(LOG_CHAT_ID, message)
    except Exception as e:
        logger.error(f"Failed to send alert: {e}")


async def main():
    # Проверка конфигурации
    required = [('API_ID', API_ID), ('API_HASH', API_HASH), 
                ('TRACKED_BOT_ID', TRACKED_BOT_ID), ('LOG_CHAT_ID', LOG_CHAT_ID)]
    missing = [name for name, value in required if not value]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")
    
    # Создаем менеджер сессий
    session_manager = SessionManager(SESSION_PATH, API_ID, API_HASH)
    
    # Health checker
    health_checker = SessionHealthChecker(LOG_CHAT_ID, send_alert)
    
    async with session_manager:
        client = await session_manager.get_client()
        
        # Регистрируем callback для переподключения
        session_manager.on_reconnect(health_checker.check_and_alert)
        
        me = await client.get_me()
        logger.info(f"✅ UserBot logged in as: {me.first_name} (@{me.username})")
        logger.info(f"👀 Watching for bot ID: {TRACKED_BOT_ID}")
        
        # Обработчик добавления в чат
        @client.on(events.ChatAction)
        async def handler(event):
            if not isinstance(event.chat, Channel) or not event.user_added:
                return
            
            try:
                added_user = await event.get_user()
                if added_user and added_user.id == TRACKED_BOT_ID:
                    logger.info(f"Bot added to: {event.chat.title} ({event.chat_id})")
                    await asyncio.gather(
                        fetch_and_send_topics(client, event.chat_id, event.chat.title),
                        add_onboard_members(client, event.chat_id, event.chat.title),
                    )
            except Exception as e:
                logger.error(f"Handler error: {e}")
        
        logger.info("🚀 UserBot is running...")
        await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
