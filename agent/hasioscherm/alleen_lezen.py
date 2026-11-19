"""Beveiligde modus: het hoofdbestandssysteem alleen-lezen (overlay in het geheugen).

Gebruikt de standaardfunctie van Raspberry Pi OS (raspi-config overlayfs). De
boot-partitie blijft schrijfbaar; daar staan config, toestand en wijzigingslog.

Updates in beveiligde modus gaan in stappen, bijgehouden in een markeerbestand op
de boot-partitie, zodat het ook na herstarts klopt:

  1. "uitschakelen"  overlay uit + herstart
  2. "installeren"   (na de herstart) update.sh draait in een eigen systemd-unit
  3. "klaar"/"mislukt" (door update.sh) -> overlay weer aan + herstart, markeerbestand weg

Om een herstartlus te voorkomen telt het markeerbestand de pogingen.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from .setup_wizard import schrijf_wijziging

log = logging.getLogger(__name__)

MARKER = Path(os.environ.get("HASIOSCHERM_UPDATE_MARKER", "/boot/firmware/hasioscherm-update.json"))
MAX_POGINGEN = 3


def actief() -> bool:
    """Is het hoofdbestandssysteem nu een overlay (alleen-lezen)?"""
    try:
        with open("/proc/mounts") as f:
            for regel in f:
                d = regel.split()
                if len(d) > 2 and d[1] == "/" and d[2] == "overlay":
                    return True
    except OSError:
        pass
    return False


def _raspi_config(*args: str) -> bool:
    try:
        r = subprocess.run(["raspi-config", "nonint", *args], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("raspi-config %s mislukt: %s", " ".join(args), e)
        return False
    if r.returncode != 0:
        log.error("raspi-config %s gaf %s: %s", " ".join(args), r.returncode, (r.stderr or r.stdout).strip())
        return False
    return True


def zet_aan() -> bool:
    """Overlay aanzetten (actief na herstart)."""
    return _raspi_config("do_overlayfs", "0")


def zet_uit() -> bool:
    """Overlay uitzetten (actief na herstart)."""
    return _raspi_config("do_overlayfs", "1")


def herstart() -> None:
    subprocess.Popen(["systemctl", "reboot"])


def lees_marker() -> dict | None:
    try:
        return json.loads(MARKER.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning("Markeerbestand onleesbaar (%s); genegeerd", e)
        return None


def schrijf_marker(d: dict) -> None:
    tmp = MARKER.with_name(MARKER.name + ".nieuw")
    tmp.write_text(json.dumps(d), encoding="utf-8")
    os.replace(tmp, MARKER)


def wis_marker() -> None:
    try:
        MARKER.unlink()
    except FileNotFoundError:
        pass


def start_update(tag: str) -> str:
    """Begin een update. Geeft terug wat er gebeurt: 'direct' of 'herstart'."""
    if not actief():
        subprocess.Popen(["systemd-run", "--unit=hasioscherm-update", "--collect",
                          "/opt/hasioscherm/update.sh", tag])
        schrijf_wijziging(f"Update naar {tag} gestart")
        return "direct"
    schrijf_marker({"tag": tag, "stap": "uitschakelen", "pogingen": 0})
    schrijf_wijziging(f"Update naar {tag}: beveiligde modus tijdelijk uit, herstart")
    if not zet_uit():
        wis_marker()
        schrijf_wijziging("Update afgebroken: beveiligde modus kon niet uit")
        return "mislukt"
    herstart()
    return "herstart"


def bij_opstart(alleen_lezen_gewenst: bool, setup_klaar: bool) -> str | None:
    """Wordt bij elke start van de agent aangeroepen. Geeft een korte status terug."""
    m = lees_marker()
    if m:
        m["pogingen"] = int(m.get("pogingen", 0)) + 1
        if m["pogingen"] > MAX_POGINGEN:
            schrijf_wijziging(f"Update naar {m.get('tag')}: te veel pogingen, gestopt (markeerbestand verwijderd)")
            wis_marker()
            return "gestopt"
        stap = m.get("stap")
        if stap == "uitschakelen" and not actief():
            m["stap"] = "installeren"
            schrijf_marker(m)
            schrijf_wijziging(f"Update naar {m['tag']}: installeren")
            subprocess.Popen(["systemd-run", "--unit=hasioscherm-update", "--collect",
                              "/opt/hasioscherm/update.sh", m["tag"]])
            return "installeren"
        if stap == "installeren":
            schrijf_marker(m)   # update.sh loopt nog of agent is net herstart door install.sh
            return "installeren"
        if stap in ("klaar", "mislukt"):
            schrijf_wijziging(f"Update naar {m.get('tag')}: {stap}; beveiligde modus weer aan, herstart")
            wis_marker()
            if alleen_lezen_gewenst and not actief():
                if zet_aan():
                    herstart()
                    return "terugzetten"
            return stap
        schrijf_marker(m)
        return stap
    # geen update bezig: beveiligde modus afstemmen op de instelling
    if setup_klaar and alleen_lezen_gewenst and not actief():
        schrijf_wijziging("Beveiligde modus (alleen-lezen) aanzetten, herstart")
        if zet_aan():
            herstart()
            return "aanzetten"
    return None
