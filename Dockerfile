FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Копирование и установка Python зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY . .

# Создание необходимых директорий
RUN mkdir -p /app/sessions /app/logs /app/failed_exports

# Переменные окружения
ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python healthcheck.py || exit 1

# Скрипт для проверки сессии перед запуском
RUN echo '#!/bin/bash\n\
set -e\n\
echo "🔐 Checking UserBot session..."\n\
if [ ! -f /app/sessions/topic_logger_session.session ]; then\n\
    echo "⚠️ No session found. Please run auth_userbot.py first:"\n\
    echo "   docker run -it --rm -v $(pwd)/sessions:/app/sessions \\"\n\
    echo "      -v $(pwd)/.env:/app/.env \\"\n\
    echo "      --entrypoint python \\"\n\
    echo "      tg_accounts_daily_mail_ar_transfer-topic-logger \\"\n\
    echo "      auth_userbot.py"\n\
    exit 1\n\
fi\n\
echo "✅ Session found, starting bot..."\n\
exec python topic_id_logging_bot.py\n\
' > /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]