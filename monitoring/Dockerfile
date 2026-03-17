FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir python-dotenv httpx python-telegram-bot

# Copy shared utilities and monitoring code
COPY shared/ /app/shared/
COPY monitoring/ /app/monitoring/

ENV PYTHONPATH=/app

CMD ["python", "-m", "monitoring.telegram_monitor"]
