#!/bin/bash
# hasioscherm - update naar een release-tag. Wordt (via systemd-run) gestart door de
# agent als je in Home Assistant op 'Installeren' drukt, of automatisch 's nachts.
# In beveiligde modus heeft de agent het systeem eerst schrijfbaar gemaakt; dit script
# meldt via het markeerbestand of het gelukt is, zodat de agent de beveiliging
# daarna weer aanzet.
#
# Gebruik: update.sh v0.2.0
set -uo pipefail

TAG="${1:?geef een tag op, bijv. v0.2.0}"
DEST="${HASIOSCHERM_DEST:-/opt/hasioscherm}"
LOG="${HASIOSCHERM_UPDATE_LOG:-/var/log/hasioscherm-update.log}"
MARKER="${HASIOSCHERM_UPDATE_MARKER:-/boot/firmware/hasioscherm-update.json}"
WLOG="${HASIOSCHERM_CHANGELOG:-/boot/firmware/hasioscherm-wijzigingen.log}"

exec >>"$LOG" 2>&1
echo "=== $(date -Is) update naar $TAG ==="

# De boot-partitie moet schrijfbaar zijn (markeerbestand, wijzigingslog);
# oudere installaties zetten hem in beveiligde modus op alleen-lezen.
if awk '$2=="/boot/firmware" && $4 ~ /(^|,)ro(,|$)/ {f=1} END {exit !f}' /proc/mounts; then
  mount -o remount,rw /boot/firmware && echo "boot-partitie weer schrijfbaar gemaakt"
fi

markeer() {   # markeer klaar|mislukt
  if [ -f "$MARKER" ]; then
    python3 - "$MARKER" "$1" <<'EOF'
import json, sys
p, stap = sys.argv[1], sys.argv[2]
d = json.load(open(p)); d["stap"] = stap
open(p, "w").write(json.dumps(d))
EOF
  fi
  echo "$(date -Iseconds)  Update naar $TAG: $1" >> "$WLOG" 2>/dev/null || true
}

cd "$DEST" || { markeer mislukt; exit 1; }

# Optioneel token (alleen nodig bij een privé-repo) uit de config; nooit in een
# adres, het log of op de opdrachtregel zichtbaar.
CONFIG="${HASIOSCHERM_CONFIG:-/boot/firmware/hasioscherm.yaml}"
[ -f "$CONFIG" ] || CONFIG=/etc/hasioscherm/hasioscherm.yaml
lees() { python3 -c 'import sys,yaml; print(((yaml.safe_load(open(sys.argv[1])) or {}).get("updates") or {}).get(sys.argv[2]) or "")' "$CONFIG" "$1" 2>/dev/null; }
TOKEN="$(lees token)"
REPO="$(lees repo)"; [ -n "$REPO" ] || REPO=basdelouw78/hasioscherm

# De release wordt als pakket (tar.gz) gedownload, niet met git: geen gedoe
# met eigenaarschap of oude inloggegevens in .git (oorzaak van mislukte
# updates t/m 0.7.5).
URL="https://api.github.com/repos/$REPO/tarball/$TAG"
WERK="$(mktemp -d /var/tmp/hasioscherm-update.XXXXXX)"
trap 'rm -rf "$WERK"' EXIT
CURL_ARGS=(-sSL --retry 3 --retry-delay 5 --connect-timeout 15 -H "Accept: application/vnd.github+json" -H "User-Agent: hasioscherm")
HDR="$WERK/hdr"
if [ -n "$TOKEN" ]; then
  printf 'Authorization: Bearer %s\n' "$TOKEN" > "$HDR"; chmod 600 "$HDR"
  CURL_ARGS+=(-H "@$HDR")
fi

# Na een herstart is het netwerk er soms nog niet: maximaal 3 minuten wachten.
OK=0
for i in $(seq 1 36); do
  CODE="$(curl "${CURL_ARGS[@]}" -o "$WERK/release.tar.gz" -w '%{http_code}' "$URL" 2>>"$LOG")" || true
  if [ "$CODE" = "200" ]; then OK=1; break; fi
  case "$CODE" in
    401|403|404) echo "release $TAG niet beschikbaar (HTTP $CODE): bestaat de versie, en is de repo/token goed?"; break ;;
  esac
  [ "$i" -eq 1 ] && echo "downloaden mislukt (HTTP ${CODE:-geen antwoord}); wachten op netwerk/GitHub…"
  sleep 5
done

NIEUW=""
if [ "$OK" -eq 1 ] && mkdir -p "$WERK/uit" && tar -xzf "$WERK/release.tar.gz" -C "$WERK/uit"; then
  NIEUW="$(find "$WERK/uit" -mindepth 1 -maxdepth 1 -type d | head -n1)"
fi

if [ -n "$NIEUW" ] && [ -x "$NIEUW/install.sh" ] && [ -f "$NIEUW/agent/hasioscherm/__init__.py" ]; then
  echo "release $TAG gedownload: $(grep -o '[0-9][0-9.]*' "$NIEUW/agent/hasioscherm/__init__.py" | head -n1)"
  # markeren vóór install.sh: die herstart de agent, en de nieuwe agent leest dan "klaar"
  markeer klaar
  chown -R root:root "$NIEUW"
  # install.sh kopieert zichzelf naar $DEST (bestaande .git e.d. blijven ongemoeid)
  "$NIEUW/install.sh" --update || { markeer mislukt; echo "=== install.sh mislukt ==="; exit 1; }
  echo "=== update klaar ==="
else
  markeer mislukt
  echo "=== update mislukt (download of uitpakken) ==="
  systemctl restart hasioscherm-agent.service || true
  exit 1
fi
