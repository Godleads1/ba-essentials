#!/usr/bin/env bash
# Put the latest main branch live. Run as the deploy user in /opt/banda:  bash deploy/update.sh
# Takes a backup first, then builds, migrates and restarts. Takes a few minutes; the site is
# down only for the few seconds the web container restarts.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n==> %s\n' "$*"; }

say "Getting the latest code"
before="$(git rev-parse --short HEAD)"
git fetch --quiet origin main
git merge --ff-only origin/main
after="$(git rev-parse --short HEAD)"
echo "$before -> $after (to go back: git checkout $before, then run this script again)"

say "Backup before changing anything"
docker compose exec -T web python manage.py run_backups || {
    echo "The backup failed, so nothing was changed. Fix that first (see docs/BACKUPS.md)." >&2
    exit 1
}

say "Building"
docker compose build --pull web

say "Database changes"
docker compose run --rm migrate

say "Restarting"
docker compose up -d --remove-orphans
docker image prune -f >/dev/null

say "Checking"
for _ in $(seq 1 30); do
    status="$(docker inspect --format '{{.State.Health.Status}}' banda-web-1 2>/dev/null || echo starting)"
    [ "$status" = "healthy" ] && { echo "The site is up ($after)."; exit 0; }
    sleep 4
done
echo "The web container isn't healthy yet. Look at: docker compose logs --tail 100 web" >&2
exit 1
