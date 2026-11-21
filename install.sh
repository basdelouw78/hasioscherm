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
    dbus-user-session fonts-dejavu-core fonts-noto-color-emoji plymouth raspi-utils || \
  apt-get install -y --no-install-recommends \
    cage cog wlr-randr grim curl git ca-certificates \
    python3 python3-paho-mqtt python3-evdev python3-psutil python3-yaml python3-websockets network-manager \
    dbus-user-session fonts-dejavu-core fonts-noto-color-emoji plymouth libraspberrypi-bin
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
  cp -a "$SRC/." "$DEST/"
fi
# Altijd van root: bij het bouwen hielden de bestanden de eigenaar van de
# bouwomgeving, waardoor 'git' als root weigerde ("dubious ownership").
chown -R root:root "$DEST" 2>/dev/null || true
chmod +x "$DEST/install.sh" "$DEST/update.sh" "$DEST/kiosk/start-browser.sh" "$DEST/kiosk/diagnose.sh" "$DEST/kiosk/touch-kalibratie.sh"

log "Configuratie"
# De boot-partitie moet schrijfbaar blijven (config, toestand, wijzigingslog,
# markeerbestanden). Oudere versies lieten raspi-config hem alleen-lezen maken.
if [ -f /etc/fstab ] && grep -qE '/boot/firmware.*defaults,ro' /etc/fstab; then
  log "Boot-partitie weer schrijfbaar in /etc/fstab"
  sed -i -E 's#(.*/boot/firmware.*)defaults,ro(.*)#\1defaults\2#' /etc/fstab
fi

if [ -d "$BOOT" ] && [ ! -f "$BOOT/hasioscherm.yaml" ]; then
  cp "$DEST/config/hasioscherm.example.yaml" "$BOOT/hasioscherm.yaml"
  log "Voorbeeldconfig gezet in $BOOT/hasioscherm.yaml - pas die aan!"
fi

log "Services installeren"
install -m 644 "$DEST/systemd/hasioscherm-agent.service" /etc/systemd/system/
install -m 644 "$DEST/systemd/hasioscherm-kiosk.service" /etc/systemd/system/
install -m 644 "$DEST/systemd/hasioscherm-diagnose.service" "$DEST/systemd/hasioscherm-diagnose.timer" /etc/systemd/system/
systemctl enable hasioscherm-diagnose.timer || true
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
  if [ -f "$BOOT/cmdline.txt" ] && ! grep -qw 'splash' "$BOOT/cmdline.txt"; then
    log "Opstartscherm aanzetten (Plymouth)"
    sed -i '1 s/$/ splash plymouth.ignore-serial-consoles/' "$BOOT/cmdline.txt"
  fi
fi

# Bdlsoft-opstartscherm (Plymouth). Ook bij --update bijwerken.
if command -v plymouth-set-default-theme >/dev/null; then
  log "Opstartscherm Bdlsoft installeren"
  THEMA=/usr/share/plymouth/themes/bdlsoft
  mkdir -p "$THEMA"
  cp "$DEST"/plymouth/bdlsoft/* "$THEMA"/
  "$DEST/kiosk/touch-kalibratie.sh" --alleen-thema --geen-initramfs || true
  plymouth-set-default-theme bdlsoft || true
  if command -v update-initramfs >/dev/null; then
    update-initramfs -u -k all >/dev/null 2>&1 || log "Let op: initramfs bijwerken mislukt (opstartscherm verschijnt dan later)"
  fi
fi

if [ "$IN_CHROOT" -eq 0 ]; then
  systemctl daemon-reload
  systemctl restart hasioscherm-agent.service
  systemctl restart hasioscherm-kiosk.service || true
fi

log "Klaar. Versie: $(python3 -c "import sys; sys.path.insert(0,'$DEST/agent'); import hasioscherm; print(hasioscherm.__version__)")"
