FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Устанавливаем системные зависимости (если нужны)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копируем requirements.txt и устанавливаем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код
COPY . .

# Создаем необходимые директории
RUN mkdir -p /app/sessions /app/logs /app/failed_exports

# Переменные окружения для Python (отключаем буферизацию логов)
ENV PYTHONUNBUFFERED=1

# Команда по умолчанию (будет переопределена в docker-compose)
CMD ["python", "new_daily_ar.py"]
