#!/bin/bash
# Installiert die gerber2lmd-Weboberfläche als Dienst auf einem Raspberry Pi (Raspberry Pi OS / Debian).
# Aufruf im geklonten Repo:   sudo ./deploy/install.sh
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(whoami)}"
[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo starten."; exit 1; }

echo "== Pakete installieren"
apt-get update
apt-get install -y python3-flask python3-shapely python3-matplotlib python3-waitress

echo "== Datenordner"
mkdir -p "$DIR/data/.mpl"
chown -R "$USER_NAME": "$DIR/data"

echo "== Dienst einrichten (Benutzer $USER_NAME, Ordner $DIR)"
sed -e "s#@DIR@#$DIR#g" -e "s#@USER@#$USER_NAME#g" "$DIR/deploy/lpkf-93s.service" > /etc/systemd/system/lpkf-93s.service
systemctl daemon-reload
systemctl enable --now lpkf-93s.service
sleep 2
systemctl --no-pager --lines=3 status lpkf-93s.service || true

IP=$(hostname -I | awk '{print $1}')
echo
echo "Fertig. Im Browser öffnen:  http://$IP:8093"
echo "Aktualisieren:  git pull && sudo systemctl restart lpkf-93s"
