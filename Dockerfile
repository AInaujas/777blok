FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 DB_PATH=/app/data/signalbot.db
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY signalbot ./signalbot
RUN useradd --create-home bot && mkdir -p /app/data && chown bot /app/data
USER bot

VOLUME /app/data
CMD ["python", "-m", "signalbot"]
