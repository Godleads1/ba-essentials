#!/usr/bin/env bash
# One-time setup of a fresh Hostinger VPS (Ubuntu 24.04). Run as root:
#     bash harden.sh
# It is safe to run again. What it does (docs/HOSTINGER.md, step 3):
#   - updates the system and turns on automatic security updates (with a 04:00 reboot when needed);
#   - creates the "deploy" user with your SSH key, and installs Docker;
#   - adds 2 GB of swap if there is none;
#   - firewall (ufw): SSH open, web ports open to Cloudflare only (cloudflare-firewall.sh);
#   - fail2ban blocks addresses that keep failing SSH sign-in;
#   - SSH: keys only, no passwords, no root sign-in.
set -euo pipefail

DEPLOY_USER="${DEPLOY_USER:-deploy}"
HERE="$(cd "$(dirname "$0")" && pwd)"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nSTOPPED: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run this as root (sudo bash harden.sh)."
. /etc/os-release
[ "${ID:-}" = "ubuntu" ] || die "this script is for Ubuntu (found ${ID:-unknown})."
[ "${VERSION_ID:-}" = "24.04" ] || echo "Note: written for Ubuntu 24.04; found ${VERSION_ID:-unknown}."

say "Updating the system"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get -y -q upgrade
apt-get -y -q install ufw fail2ban unattended-upgrades ca-certificates curl git openssl

say "Automatic security updates"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
CONF
cat > /etc/apt/apt.conf.d/52banda-unattended <<'CONF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
Unattended-Upgrade::Remove-Unused-Kernel-Packages "true";
CONF

say "The ${DEPLOY_USER} user"
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
    adduser --disabled-password --gecos "" "$DEPLOY_USER"
fi
usermod -aG sudo "$DEPLOY_USER"
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
KEYS="/home/$DEPLOY_USER/.ssh/authorized_keys"
if [ ! -s "$KEYS" ] && [ -s /root/.ssh/authorized_keys ]; then
    cp /root/.ssh/authorized_keys "$KEYS"
fi
[ -s "$KEYS" ] || die "no SSH key found. Add your public key in Hostinger (VPS > Settings > SSH keys) or to $KEYS, then run this again. Nothing about SSH has been changed."
chown "$DEPLOY_USER:$DEPLOY_USER" "$KEYS"
chmod 600 "$KEYS"
# The deploy user needs a password for sudo (SSH sign-in stays key-only).
if passwd -S "$DEPLOY_USER" | awk '{print $2}' | grep -qE '^(L|NP)$'; then
    echo "Choose a password for ${DEPLOY_USER} (used only for sudo, never to sign in over SSH):"
    passwd "$DEPLOY_USER"
fi

say "Docker"
if ! command -v docker >/dev/null 2>&1; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get -y -q install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
cat > /etc/docker/daemon.json <<'CONF'
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" }, "live-restore": true }
CONF
systemctl restart docker
# Members of the docker group can control the server, so this user is the only one in it.
usermod -aG docker "$DEPLOY_USER"

say "Log folder for the scheduled jobs"
install -d -m 750 -o "$DEPLOY_USER" -g adm /var/log/banda
cat > /etc/logrotate.d/banda <<'CONF'
/var/log/banda/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
}
CONF

say "Swap"
if ! swapon --show | grep -q .; then
    fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl -q vm.swappiness=10 && echo 'vm.swappiness=10' > /etc/sysctl.d/99-banda-swap.conf
fi

say "fail2ban"
cat > /etc/fail2ban/jail.d/banda.local <<'CONF'
[sshd]
enabled = true
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
CONF
systemctl enable --now fail2ban >/dev/null
systemctl restart fail2ban

say "Firewall"
ufw --force reset >/dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
bash "$HERE/cloudflare-firewall.sh" --no-reload
ufw --force enable
install -m 0755 "$HERE/cloudflare-firewall.sh" /usr/local/sbin/banda-cloudflare-firewall
echo '17 5 * * 1 root /usr/local/sbin/banda-cloudflare-firewall >/var/log/banda-cloudflare-firewall.log 2>&1' \
    > /etc/cron.d/banda-cloudflare-firewall

say "SSH: keys only"
# Ubuntu reads sshd_config.d in name order and the FIRST value wins, so this file must sort
# before Hostinger's and cloud-init's (for example 50-cloud-init.conf).
cat > /etc/ssh/sshd_config.d/01-banda.conf <<CONF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowUsers ${DEPLOY_USER}
MaxAuthTries 3
X11Forwarding no
CONF
sshd -t || die "the SSH settings didn't pass the check; nothing was reloaded. Remove /etc/ssh/sshd_config.d/01-banda.conf and ask for help."
systemctl reload ssh

say "Done"
cat <<MSG
IMPORTANT: keep this window open. In a NEW PowerShell window, check you can still get in:

    ssh ${DEPLOY_USER}@$(hostname -I | awk '{print $1}')

Only close this window once that works. Root sign-in and passwords over SSH are now off.
Next: docs/HOSTINGER.md, step 4.
MSG
