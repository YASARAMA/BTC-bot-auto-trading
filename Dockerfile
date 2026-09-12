FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN adduser --disabled-password --gecos "" bot

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY config.yaml ./config.yaml
COPY data/samples ./data/samples

RUN mkdir -p /app/data && chown -R bot:bot /app
USER bot

# State (sqlite, kill switch) lives in /app/data: mount it as a volume.
VOLUME ["/app/data"]

CMD ["python", "-m", "bot.main"]
