FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer caching)
COPY funding-arb/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared utilities
COPY shared/ /app/shared/

# Copy bot source and config
COPY funding-arb/src/ /app/src/
COPY funding-arb/config/ /app/config/

ENV PYTHONPATH=/app

CMD ["python", "-m", "src.main"]
