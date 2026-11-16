# syntax=docker/dockerfile:1

FROM node:22-alpine AS assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci
COPY static ./static
COPY templates ./templates
COPY apps ./apps
RUN npm run css:build

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.build
WORKDIR /app

# clamdscan is the client; the daemon runs as the private ClamAV service.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        clamav \
        gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements ./requirements
RUN pip install --no-cache-dir -r requirements/prod.txt

COPY . .
COPY --from=assets /build/static/css/app.css ./static/css/app.css

RUN python manage.py collectstatic --noinput --ignore "src/*" \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
    && chmod +x docker/entrypoint.sh docker/start.sh

ENV DJANGO_SETTINGS_MODULE=config.settings.staging
EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["/app/docker/start.sh"]
