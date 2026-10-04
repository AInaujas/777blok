FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DB_PATH=/app/data/signalbot.db
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY signalbot ./signalbot
RUN mkdir -p /app/data

# No VOLUME line: hosts like Railway reject it and attach storage themselves.
# Runs as root so a mounted volume at /app/data is always writable.
CMD ["python", "-m", "signalbot"]
