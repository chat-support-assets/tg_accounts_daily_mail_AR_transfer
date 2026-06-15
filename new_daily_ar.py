import asyncio
import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build

load_dotenv()

# ============================================================
# Конфигурация
# ============================================================
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
CONTROLLER_TOKEN = os.getenv('CONTROLLER_TOKEN', '')
AUTHORIZED_USER_IDS = {int(x.strip()) for x in os.getenv('AUTHORIZED_USER_IDS', '').split(',') if x.strip()}

# Google Sheets
SPREADSHEET_ID = os.getenv('SPREADSHEET_ID', '')
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv('GOOGLE_SERVICE_ACCOUNT_FILE', '')

# Пути
SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)


def get_google_sheets_service():
    """Получение сервиса Google Sheets"""
    if not GOOGLE_SERVICE_ACCOUNT_FILE or not Path(GOOGLE_SERVICE_ACCOUNT_FILE).exists():
        print(f"Service account not found: {GOOGLE_SERVICE_ACCOUNT_FILE}")
        return None
    
    try:
        credentials = service_account.Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_FILE,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )
        return build('sheets', 'v4', credentials=credentials)
    except Exception as e:
        print(f"Google Sheets error: {e}")
        return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return
    await update.message.reply_text(
        "🤖 GEO Controller Bot is running!\n\n"
        "Available commands:\n"
        "/INRfirst90rows - India rows 1–90\n"
        "/INRsecond90rows - India rows 91–180\n"
        "/INRlast - India rows 181+\n"
        "/nepal - Nepal all rows\n"
        "/egypt - Egypt all rows\n"
        "/srilanka - Sri Lanka all rows"
    )


async def handle_command(update: Update, context: ContextTypes.DEFAULT_TYPE, command: str, message: str):
    """Обработчик команд с подтверждением"""
    if update.effective_user.id not in AUTHORIZED_USER_IDS:
        await update.message.reply_text("⛔ You are not authorized.")
        return
    
    await update.message.reply_text(f"📋 {message}\nЗапустить? (да/нет)")
    # TODO: Добавить логику подтверждения и отправки
    # Пока просто заглушка
    await update.message.reply_text("⏳ В разработке...")


async def inr_first(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_command(update, context, "inr_first", "Файл Approval_speed обновлен с актуальной информацией?")


async def inr_second(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_command(update, context, "inr_second", "Файл Approval_speed обновлен с актуальной информацией?")


async def inr_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_command(update, context, "inr_last", "Файл Approval_speed обновлен с актуальной информацией?")


async def nepal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_command(update, context, "nepal", "Файл Approval_speed обновлен с актуальной информацией?")


async def egypt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_command(update, context, "egypt", "Файл Approval_speed обновлен с актуальной информацией?")


async def srilanka(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_command(update, context, "srilanka", "Файл Approval_speed обновлен с актуальной информацией?")


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.")


def main():
    if not CONTROLLER_TOKEN:
        print("❌ CONTROLLER_TOKEN not set in .env")
        return
    
    print("🤖 Starting GEO Controller Bot...")
    print(f"✅ Authorized users: {AUTHORIZED_USER_IDS}")
    
    # Создаем приложение
    app = Application.builder().token(CONTROLLER_TOKEN).build()
    
    # Регистрируем команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("INRfirst90rows", inr_first))
    app.add_handler(CommandHandler("INRsecond90rows", inr_second))
    app.add_handler(CommandHandler("INRlast", inr_last))
    app.add_handler(CommandHandler("nepal", nepal))
    app.add_handler(CommandHandler("egypt", egypt))
    app.add_handler(CommandHandler("srilanka", srilanka))
    app.add_handler(CommandHandler("cancel", cancel_command))
    
    print("📡 Bot is polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
