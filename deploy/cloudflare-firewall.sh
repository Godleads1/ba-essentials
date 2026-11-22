#!/usr/bin/env bash
# Let only Cloudflare reach ports 80 and 443 (security checklist 30: no bypassing Cloudflare).
# Reads Cloudflare's published address ranges, adds a ufw rule for each new one and removes
# rules for ranges Cloudflare no longer lists. Run weekly by cron (harden.sh sets that up).
set -euo pipefail

STATE=/var/lib/banda/cloudflare-ranges
mkdir -p "$(dirname "$STATE")"
touch "$STATE"

fetch() { curl -fsS --max-time 20 "$1"; }
ranges="$( { fetch https://www.cloudflare.com/ips-v4; echo; fetch https://www.cloudflare.com/ips-v6; echo; } \
           | tr -d '\r' | grep -E '^[0-9a-fA-F:.]+/[0-9]{1,3}$' | sort -u )" || true
count="$(printf '%s\n' "$ranges" | grep -c . || true)"
if [ "$count" -lt 10 ]; then
    echo "Got only $count ranges from Cloudflare; leaving the firewall as it is." >&2
    exit 1
fi

while read -r old; do
    [ -n "$old" ] || continue
    if ! printf '%s\n' "$ranges" | grep -qxF "$old"; then
        ufw --force delete allow proto tcp from "$old" to any port 80,443 >/dev/null || true
        echo "removed $old"
    fi
done < "$STATE"

while read -r cidr; do
    ufw allow proto tcp from "$cidr" to any port 80,443 comment cloudflare >/dev/null
done <<< "$ranges"

printf '%s\n' "$ranges" > "$STATE"
[ "${1:-}" = "--no-reload" ] || ufw reload >/dev/null
echo "Ports 80 and 443 are open to $count Cloudflare ranges only."
