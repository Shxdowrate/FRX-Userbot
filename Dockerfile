# FRX в Docker. Код и данные лежат на хосте (том ./ -> /app), поэтому .update и бэкапы работают как обычно,
# а пересобирать образ нужно, только если обновление поменяло requirements.txt (docker compose build).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FRX_WEB_BIND=0.0.0.0 \
    FRX_IN_DOCKER=1

# ffmpeg — кружки и голосовые в BotChat; openssh-client — запасной публичный туннель (localhost.run)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

EXPOSE 8878
CMD ["python", "-u", "frx_systemkeeper.py"]
