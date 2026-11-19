#!/bin/bash
# Draait als root vlak voordat de kiosk start. Zet een libinput-
# kalibratiematrix zodat de touch dezelfde kant op draait als het beeld.
# De regel komt in /run (werkt ook met de beveiligde, alleen-lezen modus).
set -u
CONFIG="${HASIOSCHERM_CONFIG:-/boot/firmware/hasioscherm.yaml}"
[ -f "$CONFIG" ] || CONFIG=/etc/hasioscherm/hasioscherm.yaml
REGEL=/run/udev/rules.d/99-hasioscherm-touch.rules

lees() {  # lees <sleutel> <standaard>
  python3 - "$CONFIG" "$1" "$2" <<'EOF'
import sys, yaml
path, key, default = sys.argv[1:]
try:
    v = ((yaml.safe_load(open(path)) or {}).get("scherm") or {}).get(key, default)
    print(default if v in (None, "") else v)
except Exception:
    print(default)
EOF
}

ROTATIE="$(lees rotatie 90)"
TOUCH="$(lees touch_draaiing auto)"
[ "$TOUCH" = "auto" ] && TOUCH="$ROTATIE"

# libinput: matrix draait de genormaliseerde touch-coördinaten.
case "$TOUCH" in
  90)  MATRIX="0 1 0 -1 0 1" ;;
  180) MATRIX="-1 0 1 0 -1 1" ;;
  270) MATRIX="0 -1 1 1 0 0" ;;
  *)   MATRIX="" ;;
esac

mkdir -p /run/udev/rules.d
if [ -n "$MATRIX" ]; then
  cat > "$REGEL" <<EOF
# Gemaakt door hasioscherm (touch_draaiing=$TOUCH)
ENV{ID_INPUT_TOUCHSCREEN}=="1", ENV{LIBINPUT_CALIBRATION_MATRIX}="$MATRIX"
EOF
else
  rm -f "$REGEL"
fi
echo "touch-kalibratie: draaiing=$TOUCH matrix='${MATRIX:-geen}'"

udevadm control --reload 2>/dev/null || true
udevadm trigger --subsystem-match=input --action=change 2>/dev/null || true
udevadm settle --timeout=5 2>/dev/null || true
exit 0
