#!/bin/bash
# hasioscherm - installatie op Raspberry Pi OS Lite (64-bit, Pi 5).
#
# Gebruik:
#   sudo ./install.sh            volledige installatie (ook in de image-build)
#   sudo ./install.sh --update   alleen bestanden + services bijwerken
#
# Het script is idempotent: je kunt het zo vaak draaien als je wilt.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST=/opt/hasioscherm
BOOT=/boot/firmware
KIOSK_USER=kiosk
UPDATE_ONLY=0
[ "${1:-}" = "--update" ] && UPDATE_ONLY=1

log() { echo "[hasioscherm] $*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Draai dit script met sudo." >&2
  exit 1
fi

# In een chroot (image-build) mogen we geen services starten.
IN_CHROOT=0
if [ -n "${HASIOSCHERM_CHROOT:-}" ] || ! [ -d /run/systemd/system ]; then
  IN_CHROOT=1
fi

if [ "$UPDATE_ONLY" -eq 0 ]; then
  log "Pakketten installeren"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends \
    cage cog wlr-randr grim curl git ca-certificates \
    python3 python3-paho-mqtt python3-evdev python3-psutil python3-yaml python3-websockets network-manager \
    dbus-user-session fonts-dejavu-core libraspberrypi-bin || \
  apt-get install -y --no-install-recommends \
    cage cog wlr-randr grim curl git ca-certificates \
    python3 python3-paho-mqtt python3-evdev python3-psutil python3-yaml python3-websockets network-manager \
    dbus-user-session fonts-dejavu-core raspi-utils
  # Chromium als terugvaloptie (naam verschilt per Pi OS-versie).
  apt-get install -y --no-install-recommends chromium || \
    apt-get install -y --no-install-recommends chromium-browser || \
    log "Chromium niet geïnstalleerd (terugvaloptie niet beschikbaar)"

  log "Tijdzone Europe/Amsterdam"
  ln -sf /usr/share/zoneinfo/Europe/Amsterdam /etc/localtime
  echo "Europe/Amsterdam" > /etc/timezone

  log "Kiosk-gebruiker aanmaken"
  if ! id "$KIOSK_USER" >/dev/null 2>&1; then
    useradd -m -s /usr/sbin/nologin "$KIOSK_USER"
  fi
  usermod -aG video,input,render,audio "$KIOSK_USER"
fi

log "Bestanden kopiëren naar $DEST"
mkdir -p "$DEST"
if [ "$SRC" != "$DEST" ]; then
  # .git meenemen zodat updates via git kunnen.
  cp -a "$SRC/." "$DEST/"
fi
chmod +x "$DEST/install.sh" "$DEST/update.sh" "$DEST/kiosk/start-browser.sh" "$DEST/kiosk/diagnose.sh"

log "Configuratie"
if [ -d "$BOOT" ] && [ ! -f "$BOOT/hasioscherm.yaml" ]; then
  cp "$DEST/config/hasioscherm.example.yaml" "$BOOT/hasioscherm.yaml"
  log "Voorbeeldconfig gezet in $BOOT/hasioscherm.yaml - pas die aan!"
fi

log "Services installeren"
install -m 644 "$DEST/systemd/hasioscherm-agent.service" /etc/systemd/system/
install -m 644 "$DEST/systemd/hasioscherm-kiosk.service" /etc/systemd/system/
mkdir -p /etc/systemd/system.conf.d
install -m 644 "$DEST/systemd/watchdog.conf" /etc/systemd/system.conf.d/hasioscherm-watchdog.conf

if [ "$UPDATE_ONLY" -eq 0 ]; then
  systemctl enable hasioscherm-agent.service hasioscherm-kiosk.service
  systemctl set-default graphical.target
  systemctl disable getty@tty1.service || true

  if [ -f "$BOOT/config.txt" ]; then
    log "config.txt aanvullen"
    grep -q '^dtparam=watchdog=on' "$BOOT/config.txt" || echo 'dtparam=watchdog=on' >> "$BOOT/config.txt"
    # Touch Display 2 wordt automatisch herkend (display_auto_detect=1).
    grep -q '^display_auto_detect=1' "$BOOT/config.txt" || echo 'display_auto_detect=1' >> "$BOOT/config.txt"
  fi
  if [ -f "$BOOT/cmdline.txt" ] && ! grep -q 'vt.global_cursor_default=0' "$BOOT/cmdline.txt"; then
    log "Stille opstart (geen cursor/logo)"
    sed -i '1 s/$/ quiet loglevel=3 logo.nologo vt.global_cursor_default=0/' "$BOOT/cmdline.txt"
  fi
fi

if [ "$IN_CHROOT" -eq 0 ]; then
  systemctl daemon-reload
  systemctl restart hasioscherm-agent.service
  systemctl restart hasioscherm-kiosk.service || true
fi

log "Klaar. Versie: $(python3 -c "import sys; sys.path.insert(0,'$DEST/agent'); import hasioscherm; print(hasioscherm.__version__)")"
