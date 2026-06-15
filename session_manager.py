"""
Менеджер сессий для Telegram UserBot
С поддержкой автоматического переподключения и обработки ошибок
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Callable

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
    AuthKeyUnregisteredError,
)
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class SessionManager:
    """Управляет сессией Telegram UserBot с автоматическим восстановлением"""
    
    def __init__(self, session_name: str, api_id: int, api_hash: str):
        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.client: Optional[TelegramClient] = None
        self._reconnect_callbacks: list[Callable] = []
        
    async def __aenter__(self):
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.disconnect()
    
    async def connect(self) -> TelegramClient:
        """Подключается или переподключается с восстановлением сессии"""
        if self.client and self.client.is_connected():
            return self.client
        
        self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)
        
        try:
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                logger.warning("Session expired or not found, need re-authentication")
                await self._reauthenticate()
            else:
                # Проверяем, что сессия жива
                try:
                    await self.client.get_me()
                    logger.info(f"✅ Session valid: {self.session_name}")
                except Exception as e:
                    logger.warning(f"Session check failed: {e}, re-authenticating...")
                    await self._reauthenticate()
            
            # Запускаем фоновую задачу для поддержания сессии
            asyncio.create_task(self._keep_alive())
            
            return self.client
            
        except Exception as e:
            logger.error(f"Failed to connect: {e}")
            raise
    
    async def _reauthenticate(self):
        """Переавторизация пользователя"""
        logger.info("Starting re-authentication...")
        
        # Запрашиваем номер телефона из окружения или через консоль
        phone = os.getenv('USERBOT_PHONE_NUMBER', '')
        if not phone:
            phone = input("📱 Enter your phone number (with country code, e.g., +1234567890): ")
        
        try:
            # Отправляем запрос на код
            await self.client.send_code_request(phone)
            logger.info("📨 Code request sent")
            
            # Получаем код
            code = input("🔐 Enter the code you received: ")
            
            try:
                # Пробуем войти с кодом
                await self.client.sign_in(phone, code)
                logger.info("✅ Successfully authenticated!")
                
            except SessionPasswordNeededError:
                # Если включена 2FA
                password = input("🔒 Enter your 2FA password: ")
                await self.client.sign_in(password=password)
                logger.info("✅ Successfully authenticated with 2FA!")
                
        except PhoneCodeInvalidError:
            logger.error("Invalid code, please try again")
            raise
        except PhoneCodeExpiredError:
            logger.error("Code expired, please request a new one")
            raise
        except FloodWaitError as e:
            logger.warning(f"Flood wait {e.seconds}s")
            await asyncio.sleep(e.seconds)
            await self._reauthenticate()
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            raise
        
        # Сохраняем сессию
        await self.client.disconnect()
        await self.client.connect()
    
    async def disconnect(self):
        """Безопасное отключение"""
        if self.client and self.client.is_connected():
            await self.client.disconnect()
            logger.info("Disconnected")
    
    async def _keep_alive(self):
        """Фоновая задача для поддержания сессии живой"""
        while self.client and self.client.is_connected():
            await asyncio.sleep(300)  # Каждые 5 минут
            try:
                await self.client.get_me()
            except Exception as e:
                logger.warning(f"Keep-alive failed: {e}, attempting reconnect...")
                await self._safe_reconnect()
                break
    
    async def _safe_reconnect(self):
        """Безопасное переподключение при разрыве"""
        try:
            await self.disconnect()
            await asyncio.sleep(5)
            await self.connect()
            # Уведомляем слушателей о переподключении
            for callback in self._reconnect_callbacks:
                try:
                    await callback(self.client)
                except Exception as e:
                    logger.error(f"Reconnect callback failed: {e}")
        except Exception as e:
            logger.error(f"Failed to reconnect: {e}")
    
    def on_reconnect(self, callback: Callable):
        """Регистрирует callback на переподключение"""
        self._reconnect_callbacks.append(callback)
    
    async def get_client(self) -> TelegramClient:
        """Возвращает активного клиента, переподключаясь при необходимости"""
        if not self.client or not self.client.is_connected():
            await self.connect()
        return self.client


class SessionHealthChecker:
    """Проверяет здоровье сессии и отправляет алерты"""
    
    def __init__(self, admin_chat_id: int, send_alert_func: Callable):
        self.admin_chat_id = admin_chat_id
        self.send_alert = send_alert_func
        self.last_check = datetime.now()
        
    async def check_and_alert(self, client: TelegramClient):
        """Проверяет сессию и отправляет алерт при проблемах"""
        try:
            me = await client.get_me()
            self.last_check = datetime.now()
            return True
        except Exception as e:
            await self.send_alert(
                f"⚠️ UserBot session issue!\n"
                f"Error: {str(e)[:100]}\n"
                f"Last successful check: {self.last_check}"
            )
            return False
