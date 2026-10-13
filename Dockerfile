# FRX в Docker. Код и данные лежат на хосте (том ./ -> /app), поэтому .update и бэкапы работают как обычно,
# пакеты, которых не хватает (например, после .update), keeper докачивает при старте сам (frx_deps.py);
# пересобрать образ (docker compose build) всё равно полезно — тогда они не скачиваются при каждом пересоздании контейнера.
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
COPY requirements.txt requirements-modules.txt /tmp/
RUN pip install -r /tmp/requirements.txt \
 && (pip install -r /tmp/requirements-modules.txt || echo "часть пакетов модулей не встала — FRX докачает их при старте")

EXPOSE 8878
CMD ["python", "-u", "frx_systemkeeper.py"]
