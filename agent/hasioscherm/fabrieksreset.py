"""Fabrieksreset: het scherm terug naar de toestand van een vers geflasht image.

Wat er gewist wordt (op het scherm):
  - instellingen (hasioscherm.yaml wordt het voorbeeldbestand), de toestand
    (hasioscherm-state.json), het diagnoseverslag en een lopende update;
  - opgeslagen wifi-netwerken;
  - het apparaat in Home Assistant (MQTT-discovery leeg gemaakt, doet de agent
    vóór de reset).

Wat blijft: het wijzigingslog (het spoor) en alles wat de setup in Home
Assistant heeft aangemaakt (gebruiker, token, blueprints, automatiseringen).
Die worden in het wijzigingslog genoemd, zodat je ze zelf kunt opruimen.

In beveiligde modus (alleen-lezen) gaat het in twee stappen, net als een update:
markeerbestand op de boot-partitie -> overlay uit -> herstart -> reset -> herstart.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from . import alleen_lezen
from .config import config_write_path
from .setup_wizard import schrijf_wijziging

log = logging.getLogger(__name__)

BOOT = Path(os.environ.get("HASIOSCHERM_BOOT", "/boot/firmware"))
MARKER = Path(os.environ.get("HASIOSCHERM_RESET_MARKER", str(BOOT / "hasioscherm-reset.json")))
VOORBEELD = Path(os.environ.get("HASIOSCHERM_VOORBEELD", "/opt/hasioscherm/config/hasioscherm.example.yaml"))
MAX_POGINGEN = 3


def _lees() -> dict | None:
    try:
        return json.loads(MARKER.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {"pogingen": 0}


def _schrijf(d: dict) -> None:
    MARKER.write_text(json.dumps(d), encoding="utf-8")


def _wis(pad: Path) -> None:
    try:
        pad.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("Kon %s niet wissen: %s", pad, e)


def _wifi_vergeten() -> list[str]:
    weg = []
    try:
        r = subprocess.run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"],
                           capture_output=True, text=True, timeout=20)
        for regel in r.stdout.splitlines():
            naam, _, soort = regel.rpartition(":")
            if soort == "802-11-wireless" and naam:
                naam = naam.replace("\\:", ":")
                subprocess.run(["nmcli", "connection", "delete", "id", naam],
                               capture_output=True, timeout=20)
                weg.append(naam)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("Wifi-netwerken vergeten mislukt: %s", e)
    return weg


def ha_restanten(device_id: str) -> str:
    return (f"gebruiker 'hasioscherm_{device_id}' met token, automatiseringen "
            f"'hasioscherm_{device_id}_meldingen' en 'hasioscherm_{device_id}_beweging', "
            "blueprints 'hasioscherm_meldingen' en 'hasioscherm_beweging' (alleen als de setup ze had aangemaakt)")


def start(device_id: str, bron: str) -> str:
    """Begin de reset. Geeft 'herstart' of 'mislukt' terug."""
    schrijf_wijziging(f"Fabrieksreset gestart ({bron}). Blijft bestaan in Home Assistant: {ha_restanten(device_id)}")
    if alleen_lezen.actief():
        _schrijf({"stap": "uitschakelen", "pogingen": 0})
        schrijf_wijziging("Fabrieksreset: beveiligde modus tijdelijk uit, herstart")
        if not alleen_lezen.zet_uit():
            _wis(MARKER)
            schrijf_wijziging("Fabrieksreset afgebroken: beveiligde modus kon niet uit")
            return "mislukt"
        alleen_lezen.herstart()
        return "herstart"
    uitvoeren()
    alleen_lezen.herstart()
    return "herstart"


def uitvoeren() -> None:
    """De eigenlijke reset (alleen als het bestandssysteem schrijfbaar is)."""
    wifi = _wifi_vergeten()
    doel = config_write_path()
    try:
        if VOORBEELD.is_file():
            shutil.copyfile(VOORBEELD, doel)
        else:
            _wis(doel)
    except OSError as e:
        log.error("Instellingen terugzetten mislukt: %s", e)
    _wis(Path(os.environ.get("HASIOSCHERM_STATE", str(BOOT / "hasioscherm-state.json"))))
    _wis(BOOT / "hasioscherm-diagnose.txt")
    alleen_lezen.wis_marker()
    _wis(MARKER)
    schrijf_wijziging("Fabrieksreset uitgevoerd: instellingen en toestand gewist"
                      + (f", wifi vergeten: {', '.join(wifi)}" if wifi else "")
                      + "; de setup begint opnieuw")


def bij_opstart() -> str | None:
    """Vroeg bij het starten van de agent: een half afgemaakte reset afmaken."""
    m = _lees()
    if m is None:
        return None
    m["pogingen"] = int(m.get("pogingen", 0)) + 1
    if m["pogingen"] > MAX_POGINGEN:
        schrijf_wijziging("Fabrieksreset: te veel pogingen, gestopt (markeerbestand verwijderd)")
        _wis(MARKER)
        return "gestopt"
    if alleen_lezen.actief():
        _schrijf(m)
        if alleen_lezen.zet_uit():
            alleen_lezen.herstart()
        return "uitschakelen"
    uitvoeren()
    alleen_lezen.herstart()
    return "uitgevoerd"
