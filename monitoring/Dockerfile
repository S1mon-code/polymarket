FROM python:3.11-slim
WORKDIR /app
COPY ../shared /app/shared
COPY ../monitoring /app/monitoring
COPY ../.env /app/.env
RUN pip install --no-cache-dir python-dotenv httpx
CMD ["python", "-m", "monitoring.telegram_monitor"]
