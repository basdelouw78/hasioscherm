#!/bin/bash
# Draait binnen cage (Wayland-kiosk). Zet de rotatie en start de browser.
set -u

CONFIG="${HASIOSCHERM_CONFIG:-/boot/firmware/hasioscherm.yaml}"
[ -f "$CONFIG" ] || CONFIG=/etc/hasioscherm/hasioscherm.yaml
LOCAL="http://127.0.0.1:${HASIOSCHERM_LOCAL_PORT:-8099}"

cfg() {  # cfg <sectie> <sleutel> <standaard>
  python3 - "$CONFIG" "$1" "$2" "$3" <<'EOF'
import sys, yaml
path, sec, key, default = sys.argv[1:]
try:
    d = (yaml.safe_load(open(path)) or {}).get(sec, {}) or {}
    v = d.get(key, default)
    print(default if v is None else v)
except Exception:
    print(default)
EOF
}

ROTATIE="$(cfg scherm rotatie 90)"
BROWSER="$(cfg dashboard browser cog)"

case "$ROTATIE" in
  90) TRANSFORM=90 ;;
  180) TRANSFORM=180 ;;
  270) TRANSFORM=270 ;;
  *) TRANSFORM=normal ;;
esac

# Rotatie van beeld (cage draait touch automatisch mee met de output).
OUTPUT="$(wlr-randr 2>/dev/null | awk '/^[^ ]/{print $1; exit}')"
if [ -n "$OUTPUT" ]; then
  wlr-randr --output "$OUTPUT" --transform "$TRANSFORM" || true
fi

# Wacht tot de agent (lokale webserver) draait, maximaal 60 s.
for _ in $(seq 1 60); do
  curl -fs -o /dev/null "$LOCAL/api/status" && break
  sleep 1
done

# Altijd via de offline-pagina starten: die wacht tot HA bereikbaar is en
# gaat dan door naar het dashboard (of de laatst gekozen pagina).
TARGET=""
[ -f /run/hasioscherm/url ] && TARGET="$(head -n1 /run/hasioscherm/url)"
if [ -n "$TARGET" ]; then
  URL="$LOCAL/offline.html?target=$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$TARGET")"
else
  URL="$LOCAL/offline.html"
fi

if [ "$BROWSER" = "chromium" ]; then
  exec chromium --ozone-platform=wayland --kiosk --noerrdialogs --disable-infobars \
    --no-first-run --disable-session-crashed-bubble --overscroll-history-navigation=0 \
    --disable-pinch --check-for-update-interval=31536000 --touch-events=enabled "$URL"
fi

exec cog --platform=wl "$URL"
