#!/usr/bin/env python3
"""
Интерактивная авторизация для UserBot
Запускается один раз для создания сессии
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError

# Добавляем текущую директорию в путь
sys.path.insert(0, str(Path(__file__).parent))

load_dotenv()

API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
SESSION_DIR = Path(__file__).parent / 'sessions'
SESSION_DIR.mkdir(exist_ok=True)
SESSION_PATH = str(SESSION_DIR / 'topic_logger_session')


async def authenticate():
    """Авторизация пользователя"""
    print("=" * 60)
    print("🤖 Telegram UserBot Authentication")
    print("=" * 60)
    
    if not API_ID or not API_HASH:
        print("❌ Error: API_ID or API_HASH not found in .env")
        print("Please add them to your .env file")
        return False
    
    print(f"✅ API_ID: {API_ID}")
    print(f"✅ API_HASH: {API_HASH[:10]}...")
    print()
    
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    
    try:
        print("🔐 Starting authentication...")
        print("📱 You'll be asked to enter:")
        print("   1. Phone number (e.g., +1234567890)")
        print("   2. Code from Telegram")
        print("   3. 2FA password (if enabled)")
        print()
        
        await client.start()
        
        me = await client.get_me()
        print()
        print("=" * 60)
        print("✅ AUTHENTICATION SUCCESSFUL!")
        print("=" * 60)
        print(f"📱 Name: {me.first_name} {me.last_name or ''}")
        print(f"👤 Username: @{me.username}")
        print(f"🆔 User ID: {me.id}")
        print(f"📁 Session saved: {SESSION_PATH}.session")
        print("=" * 60)
        
        # Сохраняем USER_ID в .env для справки
        env_path = Path(__file__).parent / '.env'
        if env_path.exists():
            with open(env_path, 'a') as f:
                f.write(f"\n# UserBot info (auto-generated)\n")
                f.write(f"USERBOT_USER_ID={me.id}\n")
        
        return True
        
    except FloodWaitError as e:
        print(f"❌ Flood wait: need to wait {e.seconds} seconds")
        print("Please try again later")
        return False
    except Exception as e:
        print(f"❌ Authentication failed: {e}")
        return False
    finally:
        await client.disconnect()


async def test_session():
    """Тестирование существующей сессии"""
    print("Testing existing session...")
    client = TelegramClient(SESSION_PATH, API_ID, API_HASH)
    
    try:
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"✅ Session valid! Logged in as: @{me.username}")
            return True
        else:
            print("❌ Session exists but not authorized")
            return False
    except Exception as e:
        print(f"❌ Session test failed: {e}")
        return False
    finally:
        await client.disconnect()


async def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--test':
        await test_session()
    else:
        await authenticate()


if __name__ == "__main__":
    asyncio.run(main())
