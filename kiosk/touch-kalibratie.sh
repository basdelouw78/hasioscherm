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
ALLEEN_THEMA=0; GEEN_INITRAMFS=0
for a in "$@"; do
  case "$a" in --alleen-thema) ALLEEN_THEMA=1 ;; --geen-initramfs) GEEN_INITRAMFS=1 ;; esac
done

# Opstartscherm: rotatie in het Plymouth-thema gelijk houden met de setup.
THEMA=/usr/share/plymouth/themes/bdlsoft/bdlsoft.script
case "$ROTATIE" in 0|90|180|270) ;; *) ROTATIE=90 ;; esac
if [ -f "$THEMA" ] && ! grep -q "^rotatie = $ROTATIE;" "$THEMA"; then
  if grep -q ' / overlay ' /proc/mounts; then
    echo "opstartscherm: rotatie wijkt af, maar beveiligde modus staat aan; later bijwerken"
  else
    sed -i "s/^rotatie = .*/rotatie = $ROTATIE; # HASIOSCHERM_ROTATIE/" "$THEMA"
    echo "opstartscherm: rotatie $ROTATIE"
    if [ "$GEEN_INITRAMFS" -eq 0 ] && command -v update-initramfs >/dev/null; then
      systemd-run --no-block --unit=hasioscherm-initramfs update-initramfs -u -k all >/dev/null 2>&1 || true
    fi
  fi
fi
[ "$ALLEEN_THEMA" -eq 1 ] && exit 0
TOUCH="$(lees touch_draaiing auto)"
# "auto": de touch draait precies andersom dan de rotatie-waarde van cage
# (op het echte Touch Display 2 vastgesteld: rotatie 90 vraagt touch 270).
if [ "$TOUCH" = "auto" ]; then
  case "$ROTATIE" in 90) TOUCH=270 ;; 270) TOUCH=90 ;; *) TOUCH="$ROTATIE" ;; esac
fi

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

# Opstartscherm laten staan tot de browser tekent (geen zwarte flits).
command -v plymouth >/dev/null && plymouth quit --retain-splash 2>/dev/null || true
exit 0
