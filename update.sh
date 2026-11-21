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
DEST=/opt/hasioscherm
LOG=/var/log/hasioscherm-update.log
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

# Optioneel token (alleen nodig bij een privé-repo) uit de config; nooit in de
# remote-URL, het log of op de opdrachtregel zichtbaar.
CONFIG=/boot/firmware/hasioscherm.yaml
[ -f "$CONFIG" ] || CONFIG=/etc/hasioscherm/hasioscherm.yaml
TOKEN="$(python3 -c 'import sys,yaml; print(((yaml.safe_load(open(sys.argv[1])) or {}).get("updates") or {}).get("token") or "")' "$CONFIG" 2>/dev/null)"
if [ -n "$TOKEN" ]; then
  export GIT_CONFIG_COUNT=1
  export GIT_CONFIG_KEY_0="http.https://github.com/.extraHeader"
  export GIT_CONFIG_VALUE_0="Authorization: Basic $(printf 'x-access-token:%s' "$TOKEN" | base64 -w0)"
fi

# Repo uit de config; remote altijd zonder inloggegevens.
REPO="$(python3 -c 'import sys,yaml; print(((yaml.safe_load(open(sys.argv[1])) or {}).get("updates") or {}).get("repo") or "basdelouw78/hasioscherm")' "$CONFIG" 2>/dev/null)"
[ -n "$REPO" ] || REPO=basdelouw78/hasioscherm
git remote set-url origin "https://github.com/$REPO.git" 2>/dev/null || git remote add origin "https://github.com/$REPO.git"
# Images t/m 0.7.4 bevatten een (verlopen) inlogregel van de GitHub-build in
# .git/config; GitHub weigert dan ook een openbare repo. Weghalen.
git config --local --unset-all http.https://github.com/.extraheader 2>/dev/null || true
git config --local --unset-all includeIf.gitdir:/github/workspace/.git.path 2>/dev/null || true

# Na een herstart is het netwerk er soms nog niet: maximaal 3 minuten wachten.
for i in $(seq 1 36); do
  if git ls-remote --exit-code origin "refs/tags/$TAG" >/dev/null 2>&1; then
    echo "GitHub bereikbaar, tag $TAG gevonden (poging $i)"
    break
  fi
  [ "$i" -eq 1 ] && echo "wachten op netwerk/GitHub…"
  sleep 5
done

ophalen() {
  for poging in 1 2 3; do
    git fetch --depth 1 origin "refs/tags/$TAG:refs/tags/$TAG" && return 0
    echo "ophalen mislukt (poging $poging), opnieuw over 10 s"
    sleep 10
  done
  return 1
}

if ophalen && git -c advice.detachedHead=false checkout --force "$TAG"; then
  # markeren vóór install.sh: die herstart de agent, en de nieuwe agent leest dan "klaar"
  markeer klaar
  ./install.sh --update || { markeer mislukt; exit 1; }
  echo "=== update klaar ==="
else
  markeer mislukt
  echo "=== update mislukt ==="
  systemctl restart hasioscherm-agent.service || true
  exit 1
fi
