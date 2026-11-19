#!/bin/bash
# Schrijft na het stoppen/crashen van de kiosk een diagnoseverslag naar de
# boot-partitie (FAT, leesbaar op elke pc). Zo is geen toetsenbord of SSH nodig.
# Hooguit eens per minuut schrijven om de kaart/SSD te sparen.
set -u
BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot
OUT="$BOOT/hasioscherm-diagnose.txt"

if [ -f "$OUT" ] && [ $(( $(date +%s) - $(stat -c %Y "$OUT") )) -lt 60 ]; then
  exit 0
fi

{
  echo "hasioscherm diagnose - $(date '+%Y-%m-%d %H:%M:%S')"
  echo "versie: $(grep -o '[0-9][0-9.]*' /opt/hasioscherm/agent/hasioscherm/__init__.py 2>/dev/null | head -n1)"
  echo "kernel: $(uname -r)"
  echo "kiosk-resultaat: ${SERVICE_RESULT:-?} / ${EXIT_CODE:-?} / ${EXIT_STATUS:-?}"
  echo
  echo "== /dev/dri =="
  ls -l /dev/dri 2>&1
  echo
  echo "== sessies =="
  loginctl list-sessions --no-pager 2>&1
  echo
  echo "== pakketten =="
  dpkg-query -W -f='${Package} ${Version}\n' cage cog libwpewebkit-2.0-1 'libwlroots*' 2>&1
  echo
  echo "== kiosk (laatste 120 regels) =="
  journalctl -u hasioscherm-kiosk.service -b --no-pager -n 120 2>&1
  echo
  echo "== agent (laatste 60 regels) =="
  journalctl -u hasioscherm-agent.service -b --no-pager -n 60 2>&1
} > "$OUT.tmp" 2>&1
mv -f "$OUT.tmp" "$OUT"
sync
