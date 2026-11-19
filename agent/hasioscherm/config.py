"""Inlezen van de configuratie en de bewaarde toestand."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

CONFIG_PATHS = [
    Path(os.environ.get("HASIOSCHERM_CONFIG", "/boot/firmware/hasioscherm.yaml")),
    Path("/etc/hasioscherm/hasioscherm.yaml"),
]
STATE_PATH = Path(os.environ.get("HASIOSCHERM_STATE", "/var/lib/hasioscherm/state.json"))
LOCAL_URL = "http://127.0.0.1:" + os.environ.get("HASIOSCHERM_LOCAL_PORT", "8099")
STANDAARD_DASHBOARD = LOCAL_URL + "/dashboard/index.html"


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_") or "hasioscherm"


@dataclass
class UitTijd:
    van: str
    tot: str


@dataclass
class Config:
    naam: str = "Scherm woonkamer"
    id: str = ""
    rotatie: int = 90
    touch_draaiing: str = "auto"
    ha_url: str = ""
    dashboard_url: str = "standaard"   # "standaard" = meegeleverd dashboard op het scherm zelf
    browser: str = "cog"
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    discovery_prefix: str = "homeassistant"
    timeout_minuten: float = 5
    eerste_tik_alleen_wekken: bool = True
    helderheid_dag: int = 80
    helderheid_nacht: int = 20
    dag_begint: str = "07:00"
    nacht_begint: str = "22:00"
    uit_tijden: list[UitTijd] = field(default_factory=list)
    popup_terug_seconden: int = 60
    te_warm_graden: float = 75
    update_modus: str = "knop"
    update_repo: str = "basdelouw78/hasioscherm"
    update_token: str = ""       # alleen nodig als de repo privé is (alleen-lezen token)
    ha_token: str = ""
    stijl: str = "auto"
    setup_klaar: bool = False
    koppeling: str = "automatisch"
    beweging_entiteit: str = ""
    beweging_uit_na_minuten: int = 0
    notify_dienst: str = ""
    alleen_lezen: bool = True
    pad: Path | None = None

    @property
    def device_id(self) -> str:
        return slugify(self.id or self.naam)

    @property
    def full_dashboard_url(self) -> str:
        """Dashboard-adres in kioskmodus, met de token van het scherm (?kiosk=1&kt=...).

        Het dashboard haalt kt meteen uit de adresbalk en bewaart hem alleen in
        de sessie. Gebruik mask_url() voor alles wat naar buiten gaat (MQTT, log).
        """
        url = self.dashboard_url.strip()
        if url in ("", "standaard"):
            return STANDAARD_DASHBOARD
        if not url.startswith(("http://", "https://", "file://")):
            url = self.ha_url.rstrip("/") + "/" + url.lstrip("/")
        return kiosk_url(url, self.ha_url, self.ha_token)


def kiosk_url(url: str, ha_url: str, token: str) -> str:
    """Voeg kiosk=1 (en de token) toe aan een adres op Home Assistant zelf."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    u, h = urlsplit(url), urlsplit(ha_url)
    if u.netloc != h.netloc:
        return url
    q = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if k not in ("kiosk", "kt")]
    q.append(("kiosk", "1"))
    if token:
        q.append(("kt", token))
    return urlunsplit((u.scheme, u.netloc, u.path, urlencode(q), u.fragment))


def mask_url(url: str) -> str:
    """Token uit een adres halen voordat het naar MQTT of het log gaat."""
    return re.sub(r"([?&](?:kt|token)=)[^&#]+", r"\1***", url or "")


def is_standaard_dashboard(url: str) -> bool:
    return url.strip() in ("", "standaard")


def _get(d: dict, *keys, default=None):
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d if d is not None else default


def load_config() -> Config:
    raw: dict[str, Any] = {}
    used: Path | None = None
    for p in CONFIG_PATHS:
        if p.is_file():
            with p.open(encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            used = p
            break
    if used is None:
        log.warning("Geen configbestand gevonden in %s; standaardwaarden gebruikt",
                    ", ".join(str(p) for p in CONFIG_PATHS))
    c = Config()
    c.pad = used
    c.naam = str(_get(raw, "scherm", "naam", default=c.naam))
    c.id = str(_get(raw, "scherm", "id", default="") or "")
    c.rotatie = int(_get(raw, "scherm", "rotatie", default=c.rotatie))
    c.touch_draaiing = str(_get(raw, "scherm", "touch_draaiing", default=c.touch_draaiing))
    c.ha_url = str(_get(raw, "homeassistant", "url", default=c.ha_url))
    c.dashboard_url = str(_get(raw, "dashboard", "url", default=c.dashboard_url))
    c.browser = str(_get(raw, "dashboard", "browser", default=c.browser))
    c.mqtt_host = str(_get(raw, "mqtt", "host", default=c.mqtt_host))
    c.mqtt_port = int(_get(raw, "mqtt", "port", default=c.mqtt_port))
    c.mqtt_user = str(_get(raw, "mqtt", "gebruiker", default="") or "")
    c.mqtt_password = str(_get(raw, "mqtt", "wachtwoord", default="") or "")
    c.discovery_prefix = str(_get(raw, "mqtt", "discovery_prefix", default=c.discovery_prefix))
    g = _get(raw, "gedrag", default={}) or {}
    c.timeout_minuten = float(g.get("timeout_minuten", c.timeout_minuten))
    c.eerste_tik_alleen_wekken = bool(g.get("eerste_tik_alleen_wekken", c.eerste_tik_alleen_wekken))
    c.helderheid_dag = int(g.get("helderheid_dag", c.helderheid_dag))
    c.helderheid_nacht = int(g.get("helderheid_nacht", c.helderheid_nacht))
    c.dag_begint = str(g.get("dag_begint", c.dag_begint))
    c.nacht_begint = str(g.get("nacht_begint", c.nacht_begint))
    c.uit_tijden = [UitTijd(str(u["van"]), str(u["tot"])) for u in (g.get("uit_tijden") or [])
                    if isinstance(u, dict) and "van" in u and "tot" in u]
    c.popup_terug_seconden = int(g.get("popup_terug_seconden", c.popup_terug_seconden))
    c.te_warm_graden = float(g.get("te_warm_graden", c.te_warm_graden))
    c.update_modus = str(_get(raw, "updates", "modus", default=c.update_modus))
    c.update_repo = str(_get(raw, "updates", "repo", default=c.update_repo))
    c.ha_token = str(_get(raw, "homeassistant", "token", default="") or "")
    c.update_token = str(_get(raw, "updates", "token", default="") or "")
    c.koppeling = str(_get(raw, "homeassistant", "koppeling", default=c.koppeling))
    c.stijl = str(_get(raw, "dashboard", "stijl", default=c.stijl))
    c.setup_klaar = bool(_get(raw, "setup", "klaar", default=False))
    c.beweging_entiteit = str(_get(raw, "ha_automatiseringen", "beweging_entiteit", default="") or "")
    c.beweging_uit_na_minuten = int(_get(raw, "ha_automatiseringen", "beweging_uit_na_minuten", default=0) or 0)
    c.notify_dienst = str(_get(raw, "ha_automatiseringen", "notify_dienst", default="") or "")
    c.alleen_lezen = bool(_get(raw, "systeem", "alleen_lezen", default=True))
    return c


def config_write_path() -> Path:
    """Waar de wizard het configbestand naartoe schrijft."""
    p = CONFIG_PATHS[0]
    if p.parent.is_dir():
        return p
    return CONFIG_PATHS[1]


def dump_config(d: dict) -> str:
    """Schrijf een configbestand met uitleg bovenaan."""
    kop = (
        "# hasioscherm - configuratie per scherm\n"
        "# Geschreven door de setup-wizard. Je kunt dit bestand ook met de hand\n"
        "# aanpassen (boot-partitie, leesbaar op Windows/Mac). De wizard opnieuw\n"
        "# openen kan via de knop 'Setup openen' in Home Assistant of op het\n"
        "# offline-scherm.\n"
        "# LET OP: dit bestand bevat het MQTT-wachtwoord en HA-token van het scherm.\n\n"
    )
    return kop + yaml.safe_dump(d, allow_unicode=True, sort_keys=False, default_flow_style=False)


class State:
    """Kleine persistente toestand (instellingen die HA wijzigt).

    Elke wijziging wordt ook in het logboek geschreven, zodat er een spoor is.
    """

    def __init__(self, path: Path = STATE_PATH):
        self.path = path
        self.data: dict[str, Any] = {}
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except Exception as e:  # noqa: BLE001
            log.warning("Toestand %s onleesbaar (%s); opnieuw begonnen", path, e)

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value, bron: str = "ha") -> None:
        oud = self.data.get(key)
        if oud == value:
            return
        self.data[key] = value
        log.info("Instelling gewijzigd: %s %r -> %r (bron: %s)", key, oud, value, bron)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            log.warning("Kon toestand niet opslaan: %s", e)
