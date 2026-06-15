FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем ВСЕ файлы
COPY *.py .
COPY *.json .

RUN mkdir -p /app/sessions /app/logs /app/failed_exports

ENV PYTHONUNBUFFERED=1
ENV TZ=UTC

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Скрипт для проверки сессии
RUN echo '#!/bin/bash\n\
if [ ! -f /app/sessions/topic_logger_session.session ]; then\n\
    echo "No session found. Please run: python auth_userbot.py"\n\
    exit 1\n\
fi\n\
python topic_id_logging_bot.py\n\
' > /entrypoint.sh && chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]