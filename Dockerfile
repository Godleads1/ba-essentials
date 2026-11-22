# syntax=docker/dockerfile:1

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.build
WORKDIR /app

# clamdscan is the client (its own Debian package; "clamav" alone does not include it).
# The daemon runs as the private ClamAV service.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        clamdscan \
        gosu \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements ./requirements
# Exact versions with checked hashes: a tampered or swapped package fails the build.
RUN pip install --no-cache-dir --require-hashes -r requirements/prod.txt

COPY . .

RUN python manage.py collectstatic --noinput \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/var \
    && chown app:app /app/var \
    && chmod +x docker/entrypoint.sh docker/start.sh

# /app/var holds local files and backups when R2 isn't used; it is a volume on the server.
ENV DJANGO_SETTINGS_MODULE=config.settings.production
EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["/app/docker/start.sh"]
