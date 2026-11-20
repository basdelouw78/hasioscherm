"""Backend van de setup-wizard op het scherm.

Werkwijze voor wijzigingen in Home Assistant:
1. De wizard vraagt eenmalig een admin-login. Het token blijft alleen in
   het geheugen en vervalt na de setup (of na 15 minuten).
2. /api/setup/plan geeft precies terug wat er zal gebeuren. Dit wordt op
   het scherm getoond.
3. Pas na 'Bevestigen' voert /api/setup/toepassen de stappen uit.
4. Elke uitgevoerde stap komt in het wijzigingslog op de boot-partitie
   (hasioscherm-wijzigingen.log) en in het systeemlog.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from . import __version__, system
from .config import TALEN, Config, config_write_path, dump_config, slugify
from .ha_client import HAClient, HAFout, LoginFlow, login, test_bereikbaar

log = logging.getLogger(__name__)

REPO_DIR = Path(os.environ.get("HASIOSCHERM_REPO", "/opt/hasioscherm"))
CHANGELOG = Path(os.environ.get("HASIOSCHERM_CHANGELOG", "/boot/firmware/hasioscherm-wijzigingen.log"))
SESSIE_GELDIG = 15 * 60
TIJD_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def schrijf_wijziging(regel: str) -> None:
    """Spoor van elke wijziging: logbestand op de boot-partitie + journal."""
    log.info("WIJZIGING: %s", regel)
    try:
        if not CHANGELOG.parent.is_dir():
            return
        with CHANGELOG.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')}  {regel}\n")
    except OSError as e:
        log.warning("Kon wijzigingslog niet schrijven: %s", e)


class SetupWizard:
    def __init__(self, cfg: Config, app):
        self.cfg = cfg
        self.app = app  # voor discovery opruimen en herstart
        self.lock = threading.Lock()
        self._flow: LoginFlow | None = None
        self._flow_url = ""
        self._admin: HAClient | None = None
        self._admin_tijd = 0.0
        self._admin_naam = ""

    # ------------------------------------------------------------------
    @property
    def nodig(self) -> bool:
        return not self.cfg.setup_klaar

    def _admin_client(self) -> HAClient:
        if self._admin and time.time() - self._admin_tijd < SESSIE_GELDIG:
            return self._admin
        self._admin = None
        raise HAFout("Admin-sessie verlopen of niet ingelogd. Log opnieuw in.")

    def _vergeet_admin(self) -> None:
        self._admin = None
        self._flow = None
        self._admin_naam = ""

    # ------------------------------------------------------------------
    # routes
    # ------------------------------------------------------------------
    def routes(self) -> dict:
        return {
            ("GET", "/api/setup/huidig"): self.r_huidig,
            ("GET", "/api/setup/wifi"): self.r_wifi_scan,
            ("POST", "/api/setup/wifi"): self.r_wifi_verbind,
            ("POST", "/api/setup/ha/test"): self.r_ha_test,
            ("POST", "/api/setup/ha/login"): self.r_ha_login,
            ("POST", "/api/setup/ha/mfa"): self.r_ha_mfa,
            ("GET", "/api/setup/ha/gegevens"): self.r_ha_gegevens,
            ("POST", "/api/setup/plan"): self.r_plan,
            ("POST", "/api/setup/toepassen"): self.r_toepassen,
            ("POST", "/api/setup/afbreken"): self.r_afbreken,
            ("POST", "/api/setup/fabrieksreset"): self.r_fabrieksreset,
        }

    def r_huidig(self, body, q):
        c = self.cfg
        return 200, {
            "versie": __version__,
            "setup_klaar": c.setup_klaar,
            "naam": c.naam,
            "rotatie": c.rotatie,
            "taal": c.taal,
            "stijl": c.stijl,
            "ha_url": c.ha_url,
            "dashboard_url": c.dashboard_url,
            "browser": c.browser,
            "koppeling": c.koppeling,
            "mqtt": {"host": c.mqtt_host, "port": c.mqtt_port, "gebruiker": c.mqtt_user,
                     "wachtwoord_ingesteld": bool(c.mqtt_password)},
            "ha_token_ingesteld": bool(c.ha_token),
            "gedrag": {
                "timeout_minuten": self.app.setting("timeout_minuten"),
                "eerste_tik_alleen_wekken": bool(self.app.setting("eerste_tik_alleen_wekken")),
                "helderheid_dag": self.app.setting("helderheid_dag"),
                "helderheid_nacht": self.app.setting("helderheid_nacht"),
                "dag_begint": c.dag_begint, "nacht_begint": c.nacht_begint,
                "uit_tijden": [{"van": u.van, "tot": u.tot} for u in c.uit_tijden],
            },
            "beweging": {"entiteit": c.beweging_entiteit, "uit_na_minuten": c.beweging_uit_na_minuten},
            "meldingen": {"notify_dienst": c.notify_dienst},
            "updates": {"modus": c.update_modus, "repo": c.update_repo, "token_ingesteld": bool(c.update_token)},
            "systeem": {"alleen_lezen": bool(self.app.alleen_lezen_gewenst())},
            "netwerk": netwerk_status(),
        }

    def r_wifi_scan(self, body, q):
        return 200, {"netwerken": wifi_scan(), "status": netwerk_status()}

    def r_wifi_verbind(self, body, q):
        ssid = str(body.get("ssid", "")).strip()
        if not ssid:
            return 400, {"fout": "Geen netwerknaam"}
        ok, melding = wifi_verbind(ssid, str(body.get("wachtwoord", "")))
        if ok:
            schrijf_wijziging(f"Wifi verbonden met '{ssid}'")
        else:
            log.warning("Wifi verbinden met '%s' mislukt: %s", ssid, melding)
        antwoord = {"ok": ok, "melding": melding, "status": netwerk_status()}
        if not ok:
            antwoord["fout"] = melding
        return (200 if ok else 400), antwoord

    def r_ha_test(self, body, q):
        url = normaliseer_url(str(body.get("url", "")))
        if not url:
            return 400, {"fout": "Geen adres"}
        try:
            r = test_bereikbaar(url)
        except HAFout as e:
            return 200, {"bereikbaar": False, "home_assistant": False, "melding": str(e), "url": url}
        r["url"] = url
        return 200, r

    def r_ha_login(self, body, q):
        url = normaliseer_url(str(body.get("url", "")))
        self._vergeet_admin()
        self._flow = LoginFlow(url)
        self._flow_url = url
        try:
            r = self._flow.start(str(body.get("gebruiker", "")), str(body.get("wachtwoord", "")))
        except HAFout as e:
            return 400, {"fout": str(e)}
        return self._na_login(r, str(body.get("gebruiker", "")))

    def r_ha_mfa(self, body, q):
        if not self._flow:
            return 400, {"fout": "Eerst inloggen"}
        try:
            r = self._flow.mfa(str(body.get("code", "")))
        except HAFout as e:
            return 400, {"fout": str(e)}
        return self._na_login(r, self._admin_naam)

    def _na_login(self, r: dict, naam: str):
        if not r.get("klaar"):
            self._admin_naam = naam
            return 200, {"ok": False, "mfa": True}
        client = HAClient(self._flow_url, r["access_token"])
        try:
            if not client.is_admin():
                return 400, {"fout": "Deze gebruiker is geen beheerder. Log in met een admin-account."}
        except HAFout as e:
            return 400, {"fout": str(e)}
        self._admin = client
        self._admin_tijd = time.time()
        self._admin_naam = naam
        log.info("Admin-login voor setup gelukt (%s); token alleen in geheugen", naam)
        return 200, {"ok": True}

    def r_ha_gegevens(self, body, q):
        try:
            c = self._admin_client()
            return 200, {"notify_diensten": c.notify_diensten(),
                         "bewegingssensoren": c.bewegingssensoren()}
        except HAFout as e:
            return 400, {"fout": str(e)}

    def r_fabrieksreset(self, body, q):
        if (body or {}).get("bevestig") != "RESET":
            return 400, {"fout": "Bevestiging ontbreekt"}
        self._vergeet_admin()
        self.app.fabrieksreset_starten("setup op het scherm")
        return 200, {"ok": True}

    def r_afbreken(self, body, q):
        self._vergeet_admin()
        return 200, {"ok": True}

    # ------------------------------------------------------------------
    # plan + uitvoeren
    # ------------------------------------------------------------------
    def r_plan(self, body, q):
        try:
            inst = valideer(body)
        except ValueError as e:
            return 400, {"fout": str(e)}
        return 200, {"stappen": [s["tekst"] for s in self.maak_plan(inst)]}

    def maak_plan(self, inst: dict) -> list[dict]:
        dev_id = slugify(inst["naam"])
        taal = inst.get("taal", "nl")

        def t(nl: str, en: str, de: str) -> str:
            return {"en": en, "de": de}.get(taal, nl)

        naam = inst["naam"]
        stappen: list[dict] = []
        if inst["koppeling"] == "automatisch":
            username = f"hasioscherm_{dev_id}"
            bestaat = None
            try:
                bestaat = self._admin_client().zoek_gebruiker(username)
            except HAFout:
                pass
            if bestaat and not bestaat.get("is_active", True):
                stappen.append({"actie": "activeren", "user_id": bestaat["id"],
                                "tekst": t(f"Home Assistant: uitgeschakelde gebruiker '{username}' weer activeren",
                                           f"Home Assistant: re-activate disabled user '{username}'",
                                           f"Home Assistant: deaktivierten Benutzer '{username}' wieder aktivieren")})
            if bestaat:
                stappen.append({"actie": "wachtwoord", "user_id": bestaat["id"], "username": username,
                                "tekst": t(f"Home Assistant: bestaande gebruiker '{username}' krijgt een nieuw wachtwoord",
                                           f"Home Assistant: existing user '{username}' gets a new password",
                                           f"Home Assistant: vorhandener Benutzer '{username}' erhält ein neues Passwort")})
            else:
                stappen.append({"actie": "gebruiker", "username": username,
                                "tekst": t(f"Home Assistant: nieuwe gebruiker '{naam}' (inlognaam '{username}'), "
                                           "groep Gebruikers, alleen lokaal, geen beheerder",
                                           f"Home Assistant: new user '{naam}' (login '{username}'), "
                                           "group Users, local only, not an administrator",
                                           f"Home Assistant: neuer Benutzer '{naam}' (Login '{username}'), "
                                           "Gruppe Benutzer, nur lokal, kein Administrator")})
            stappen.append({"actie": "token", "username": username,
                            "tekst": t(f"Home Assistant: token 'hasioscherm {naam}' voor die gebruiker (voor MQTT en het dashboard)",
                                       f"Home Assistant: token 'hasioscherm {naam}' for that user (for MQTT and the dashboard)",
                                       f"Home Assistant: Token 'hasioscherm {naam}' für diesen Benutzer (für MQTT und das Dashboard)")})
        # Automatiseringen kunnen bij elke koppeling, mits er een admin-login is.
        if self._admin is not None:
            if inst["meldingen"]["notify_dienst"]:
                dienst = inst["meldingen"]["notify_dienst"]
                stappen.append({"actie": "blueprint", "bestand": "hasioscherm_meldingen.yaml",
                                "tekst": t("Home Assistant: blueprint 'hasioscherm - meldingen' opslaan",
                                           "Home Assistant: save blueprint 'hasioscherm - meldingen'",
                                           "Home Assistant: Blueprint 'hasioscherm - meldingen' speichern")})
                stappen.append({"actie": "auto_meldingen",
                                "tekst": t(f"Home Assistant: automatisering 'hasioscherm {naam} - meldingen' (offline of te warm → {dienst})",
                                           f"Home Assistant: automation 'hasioscherm {naam} - meldingen' (offline or too hot → {dienst})",
                                           f"Home Assistant: Automatisierung 'hasioscherm {naam} - meldingen' (offline oder zu warm → {dienst})")})
            if inst["beweging"]["entiteit"]:
                ent = inst["beweging"]["entiteit"]
                uit = inst["beweging"]["uit_na_minuten"]
                stappen.append({"actie": "blueprint", "bestand": "hasioscherm_beweging.yaml",
                                "tekst": t("Home Assistant: blueprint 'hasioscherm - scherm aan/uit bij beweging' opslaan",
                                           "Home Assistant: save blueprint 'hasioscherm - scherm aan/uit bij beweging'",
                                           "Home Assistant: Blueprint 'hasioscherm - scherm aan/uit bij beweging' speichern")})
                stappen.append({"actie": "auto_beweging",
                                "tekst": t(f"Home Assistant: automatisering 'hasioscherm {naam} - beweging' ({ent} wekt het scherm"
                                           + (f", uit na {uit} min zonder beweging)" if uit else ")"),
                                           f"Home Assistant: automation 'hasioscherm {naam} - beweging' ({ent} wakes the screen"
                                           + (f", off after {uit} min without motion)" if uit else ")"),
                                           f"Home Assistant: Automatisierung 'hasioscherm {naam} - beweging' ({ent} weckt den Bildschirm"
                                           + (f", aus nach {uit} Min. ohne Bewegung)" if uit else ")"))})
        if dev_id != self.cfg.device_id:
            stappen.append({"actie": "discovery_weg",
                            "tekst": t(f"MQTT: oude apparaat '{self.cfg.naam}' uit Home Assistant verwijderen (nieuwe naam → nieuw apparaat)",
                                       f"MQTT: remove old device '{self.cfg.naam}' from Home Assistant (new name → new device)",
                                       f"MQTT: altes Gerät '{self.cfg.naam}' aus Home Assistant entfernen (neuer Name → neues Gerät)")})
        stappen.append({"actie": "config", "tekst": t(f"Scherm: instellingen opslaan in {config_write_path()}",
                                                      f"Screen: save settings to {config_write_path()}",
                                                      f"Bildschirm: Einstellungen speichern in {config_write_path()}")})
        stappen.append({"actie": "herstart", "tekst": t("Scherm: software herstarten met de nieuwe instellingen",
                                                        "Screen: restart the software with the new settings",
                                                        "Bildschirm: Software mit den neuen Einstellungen neu starten")})
        return stappen

    def r_toepassen(self, body, q):
        if not self.lock.acquire(blocking=False):
            return 409, {"fout": "Setup is al bezig"}
        try:
            inst = valideer(body)
            return 200, self.toepassen(inst)
        except (ValueError, HAFout) as e:
            schrijf_wijziging(f"Setup afgebroken: {e}")
            return 400, {"fout": str(e)}
        finally:
            self.lock.release()

    def toepassen(self, inst: dict) -> dict:
        dev_id = slugify(inst["naam"])
        gedaan: list[str] = []
        mqtt_user = inst["mqtt"]["gebruiker"]
        mqtt_pw = inst["mqtt"]["wachtwoord"] or self.cfg.mqtt_password
        ha_token = inst["ha_token"] or self.cfg.ha_token
        url = inst["ha_url"]
        schrijf_wijziging(f"=== Setup gestart voor '{inst['naam']}' (hasioscherm {__version__}) ===")

        for stap in self.maak_plan(inst):
            a = stap["actie"]
            if a in ("gebruiker", "wachtwoord"):
                admin = self._admin_client()
                wachtwoord = secrets.token_urlsafe(24)
                if a == "gebruiker":
                    admin.maak_gebruiker(inst["naam"], stap["username"], wachtwoord)
                else:
                    admin.zet_wachtwoord(stap["user_id"], wachtwoord)
                mqtt_user, mqtt_pw = stap["username"], wachtwoord
            elif a == "activeren":
                self._admin_client().activeer_gebruiker(stap["user_id"])
            elif a == "token":
                scherm = HAClient(url, login(url, mqtt_user, mqtt_pw))
                # Oude tokens van dit scherm opruimen (bijv. van een eerdere setup);
                # HA weigert ook een tweede token met precies dezelfde naam.
                try:
                    for tok in scherm.refresh_tokens():
                        if (tok.get("type") == "long_lived_access_token"
                                and str(tok.get("client_name", "")).startswith("hasioscherm ")):
                            scherm.verwijder_refresh_token(tok["id"])
                            schrijf_wijziging(f"HA: oude token '{tok.get('client_name')}' van {mqtt_user} verwijderd")
                except HAFout as e:
                    log.warning("Oude tokens opruimen mislukt: %s", e)
                naam_token = f"hasioscherm {inst['naam']} {datetime.now():%Y-%m-%d %H:%M:%S}"
                try:
                    ha_token = scherm.long_lived_token(naam_token)
                except HAFout as e:
                    raise HAFout(f"Token aanmaken mislukt ({e}). Is de gebruiker {mqtt_user} actief in "
                                 "Home Assistant (Instellingen → Personen → Gebruikers)?") from e
            elif a == "blueprint":
                tekst = (REPO_DIR / "blueprints" / stap["bestand"]).read_text(encoding="utf-8")
                self._admin_client().bewaar_blueprint(f"hasioscherm/{stap['bestand']}", tekst)
            elif a == "auto_meldingen":
                self._admin_client().maak_automatisering(f"hasioscherm_{dev_id}_meldingen", {
                    "alias": f"hasioscherm {inst['naam']} - meldingen",
                    "description": f"Aangemaakt door de setup-wizard van hasioscherm op {datetime.now():%d-%m-%Y %H:%M}.",
                    "use_blueprint": {"path": "hasioscherm/hasioscherm_meldingen.yaml", "input": {
                        "scherm_naam": inst["naam"],
                        "online_entiteit": f"sensor.{dev_id}_ip",
                        "te_warm_entiteit": f"binary_sensor.{dev_id}_te_warm",
                        "notify_dienst": inst["meldingen"]["notify_dienst"],
                        "offline_minuten": 5,
                    }},
                })
            elif a == "auto_beweging":
                self._admin_client().maak_automatisering(f"hasioscherm_{dev_id}_beweging", {
                    "alias": f"hasioscherm {inst['naam']} - beweging",
                    "description": f"Aangemaakt door de setup-wizard van hasioscherm op {datetime.now():%d-%m-%Y %H:%M}.",
                    "use_blueprint": {"path": "hasioscherm/hasioscherm_beweging.yaml", "input": {
                        "bewegingssensor": inst["beweging"]["entiteit"],
                        "wekken_knop": f"button.{dev_id}_wekken",
                        "backlight": f"light.{dev_id}_backlight",
                        "uit_na_minuten": inst["beweging"]["uit_na_minuten"],
                    }},
                })
            elif a == "discovery_weg":
                self.app.remove_discovery()
            elif a == "config":
                self.schrijf_config(inst, mqtt_user, mqtt_pw, ha_token)
            elif a == "herstart":
                self._vergeet_admin()
                threading.Thread(target=self.app.herstart_agent, args=(2.0,), daemon=True).start()
            schrijf_wijziging(stap["tekst"])
            gedaan.append(stap["tekst"])

        schrijf_wijziging("=== Setup klaar ===")
        return {"ok": True, "gedaan": gedaan}

    def schrijf_config(self, inst: dict, mqtt_user: str, mqtt_pw: str, ha_token: str) -> None:
        c = self.cfg
        g = inst["gedrag"]
        d = {
            "setup": {"klaar": True, "datum": datetime.now().isoformat(timespec="seconds")},
            "scherm": {"naam": inst["naam"], "id": "", "rotatie": inst["rotatie"],
                       "touch_draaiing": c.touch_draaiing, "taal": inst["taal"]},
            "homeassistant": {"url": inst["ha_url"], "koppeling": inst["koppeling"], "token": ha_token},
            "dashboard": {"url": inst["dashboard_url"], "browser": inst["browser"], "stijl": inst["stijl"]},
            "mqtt": {"host": inst["mqtt"]["host"], "port": inst["mqtt"]["port"],
                     "gebruiker": mqtt_user, "wachtwoord": mqtt_pw,
                     "discovery_prefix": c.discovery_prefix},
            "gedrag": {
                "timeout_minuten": g["timeout_minuten"],
                "eerste_tik_alleen_wekken": g["eerste_tik_alleen_wekken"],
                "helderheid_dag": g["helderheid_dag"], "helderheid_nacht": g["helderheid_nacht"],
                "dag_begint": g["dag_begint"], "nacht_begint": g["nacht_begint"],
                "uit_tijden": g["uit_tijden"],
                "popup_terug_seconden": c.popup_terug_seconden,
                "te_warm_graden": c.te_warm_graden,
            },
            "ha_automatiseringen": {
                "beweging_entiteit": inst["beweging"]["entiteit"],
                "beweging_uit_na_minuten": inst["beweging"]["uit_na_minuten"],
                "notify_dienst": inst["meldingen"]["notify_dienst"],
            },
            "updates": {"modus": inst["updates"]["modus"], "repo": inst["updates"]["repo"],
                        "token": inst["updates"]["token"] or c.update_token},
            "systeem": {"alleen_lezen": inst["systeem"]["alleen_lezen"]},
        }
        pad = config_write_path()
        pad.parent.mkdir(parents=True, exist_ok=True)
        tmp = pad.with_name(pad.name + ".nieuw")
        tmp.write_text(dump_config(d), encoding="utf-8")
        os.replace(tmp, pad)
        # Wizardwaarden gaan vóór eerder via HA gezette overschrijvingen.
        for k in ("timeout_minuten", "eerste_tik_alleen_wekken", "helderheid_dag",
                  "helderheid_nacht", "helderheid_handmatig"):
            if k in self.app.state.data:
                self.app.state.set(k, None if k == "helderheid_handmatig" else g.get(k), bron="setup")
        if "alleen_lezen" in self.app.state.data:
            self.app.state.set("alleen_lezen", inst["systeem"]["alleen_lezen"], bron="setup")


# ----------------------------------------------------------------------
# validatie
# ----------------------------------------------------------------------
def normaliseer_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url:
        return ""
    if not re.match(r"^https?://", url):
        url = "http://" + url
    u = urlparse(url)
    if not u.port and u.scheme == "http":
        url = f"{url}:8123"
    return url


def _tijd(v, naam: str) -> str:
    v = str(v).strip()
    if not TIJD_RE.match(v):
        raise ValueError(f"{naam}: '{v}' is geen geldige tijd (UU:MM)")
    return v


def valideer(b: dict) -> dict:
    naam = str(b.get("naam", "")).strip()
    if not naam:
        raise ValueError("Geef het scherm een naam")
    ha_url = normaliseer_url(str(b.get("ha_url", "")))
    if not ha_url:
        raise ValueError("Vul het adres van Home Assistant in")
    koppeling = b.get("koppeling", "automatisch")
    if koppeling not in ("automatisch", "handmatig"):
        raise ValueError("Onbekende koppeling")
    m = b.get("mqtt") or {}
    mqtt = {
        "host": str(m.get("host") or urlparse(ha_url).hostname),
        "port": int(m.get("port") or 1883),
        "gebruiker": str(m.get("gebruiker") or ""),
        "wachtwoord": str(m.get("wachtwoord") or ""),
    }
    if koppeling == "handmatig" and not mqtt["gebruiker"]:
        raise ValueError("Vul de MQTT-gebruiker in")
    g = b.get("gedrag") or {}
    gedrag = {
        "timeout_minuten": max(0.0, float(g.get("timeout_minuten", 5))),
        "eerste_tik_alleen_wekken": bool(g.get("eerste_tik_alleen_wekken", True)),
        "helderheid_dag": max(1, min(100, int(g.get("helderheid_dag", 80)))),
        "helderheid_nacht": max(1, min(100, int(g.get("helderheid_nacht", 20)))),
        "dag_begint": _tijd(g.get("dag_begint", "07:00"), "Dag begint"),
        "nacht_begint": _tijd(g.get("nacht_begint", "22:00"), "Nacht begint"),
        "uit_tijden": [{"van": _tijd(u.get("van"), "Uit van"), "tot": _tijd(u.get("tot"), "Uit tot")}
                       for u in (g.get("uit_tijden") or []) if u.get("van") and u.get("tot")],
    }
    bew = b.get("beweging") or {}
    meld = b.get("meldingen") or {}
    stijl = b.get("stijl", "auto")
    if stijl not in ("donker", "licht", "auto"):
        raise ValueError("Onbekende stijl")
    rotatie = int(b.get("rotatie", 90))
    if rotatie not in (0, 90, 180, 270):
        raise ValueError("Rotatie moet 0, 90, 180 of 270 zijn")
    modus = (b.get("updates") or {}).get("modus", "knop")
    if modus not in ("knop", "automatisch"):
        raise ValueError("Onbekende updatemodus")
    repo = str((b.get("updates") or {}).get("repo") or "basdelouw78/hasioscherm").strip()
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", repo):
        raise ValueError("Update-repo moet de vorm eigenaar/naam hebben")
    taal = b.get("taal", "nl")
    if taal not in TALEN:
        raise ValueError("Onbekende taal")
    browser = b.get("browser", "cog")
    if browser not in ("cog", "chromium"):
        raise ValueError("Onbekende browser")
    return {
        "naam": naam, "ha_url": ha_url, "koppeling": koppeling, "mqtt": mqtt,
        "ha_token": str(b.get("ha_token") or ""),
        "rotatie": rotatie, "stijl": stijl, "browser": browser, "taal": taal,
        "dashboard_url": str(b.get("dashboard_url") or "standaard").strip() or "standaard",
        "gedrag": gedrag,
        "beweging": {"entiteit": str(bew.get("entiteit") or ""),
                     "uit_na_minuten": max(0, int(bew.get("uit_na_minuten") or 0))},
        "meldingen": {"notify_dienst": str(meld.get("notify_dienst") or "")},
        "updates": {"modus": modus,
                    "repo": str((b.get("updates") or {}).get("repo") or "basdelouw78/hasioscherm").strip(),
                    "token": str((b.get("updates") or {}).get("token") or "").strip()},
        "systeem": {"alleen_lezen": bool((b.get("systeem") or {}).get("alleen_lezen", True))},
    }


# ----------------------------------------------------------------------
# netwerk (NetworkManager)
# ----------------------------------------------------------------------
def _nmcli(args: list[str], timeout: int = 20) -> tuple[int, str]:
    try:
        r = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def netwerk_status() -> dict:
    rc, out = _nmcli(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    apparaten = []
    if rc == 0:
        for regel in out.splitlines():
            parts = regel.split(":")
            if len(parts) >= 4 and parts[1] in ("ethernet", "wifi"):
                apparaten.append({"apparaat": parts[0], "soort": parts[1],
                                  "status": parts[2], "verbinding": parts[3]})
    return {"ip": system.ip_address(), "apparaten": apparaten}


def wifi_scan() -> list[dict]:
    rc, out = _nmcli(["-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"])
    if rc != 0:
        return []
    gezien: dict[str, dict] = {}
    for regel in out.splitlines():
        # SSID kan ':' bevatten (ge-escaped als '\:')
        parts = re.split(r"(?<!\\):", regel)
        if len(parts) < 3 or not parts[0]:
            continue
        ssid = parts[0].replace("\\:", ":")
        sig = int(parts[1] or 0)
        if ssid not in gezien or sig > gezien[ssid]["signaal"]:
            gezien[ssid] = {"ssid": ssid, "signaal": sig, "beveiligd": parts[2] not in ("", "--")}
    return sorted(gezien.values(), key=lambda n: -n["signaal"])


def wifi_verbind(ssid: str, wachtwoord: str) -> tuple[bool, str]:
    # Wifi kan geblokkeerd zijn (rfkill) of uit staan; eerst aanzetten.
    try:
        subprocess.run(["rfkill", "unblock", "wifi"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pass
    _nmcli(["radio", "wifi", "on"])
    # Een eerder mislukte poging laat een profiel achter dat volgende pogingen
    # (met het goede wachtwoord) kan laten mislukken.
    _nmcli(["connection", "delete", "id", ssid])
    args = ["device", "wifi", "connect", ssid]
    if wachtwoord:
        args += ["password", wachtwoord]
    rc, out = _nmcli(args, timeout=45)
    if rc == 0:
        return True, "Verbonden"
    if wachtwoord and _is_wachtwoordfout(out):
        # De wifi-chip van de Pi heeft soms moeite met WPA2/WPA3-mengmodus.
        # Probeer een vast WPA2-profiel (zonder verplichte PMF).
        log.info("Wifi: tweede poging met vast WPA2-profiel voor '%s'", ssid)
        _nmcli(["connection", "delete", "id", ssid])
        rc2, out2 = _nmcli(["connection", "add", "type", "wifi", "con-name", ssid, "ssid", ssid,
                            "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", wachtwoord,
                            "wifi-sec.pmf", "disable"])
        if rc2 == 0:
            rc3, out3 = _nmcli(["connection", "up", "id", ssid], timeout=45)
            if rc3 == 0:
                return True, "Verbonden"
            _nmcli(["connection", "delete", "id", ssid])
            out = out3 or out
    return False, wifi_fout_uitleg(out)


def _is_wachtwoordfout(out: str) -> bool:
    o = out.lower()
    return any(t in o for t in ("secrets were required", "passwords or encryption keys",
                                "802-11-wireless-security.psk", "property is invalid"))


def wifi_fout_uitleg(out: str) -> str:
    o = out.lower()
    if _is_wachtwoordfout(out):
        uitleg = ("Wachtwoord niet geaccepteerd. Controleer hoofdletters; minimaal 8 tekens. "
                  "Gebruikt het netwerk alleen WPA3 of een bedrijfslogin (802.1X), dan werkt het niet.")
    elif "no network with ssid" in o:
        uitleg = "Netwerk niet gevonden. Staat het scherm binnen bereik?"
    elif "no wi-fi device" in o or "not available" in o or "radio" in o:
        uitleg = "Wifi op het scherm is niet beschikbaar of staat uit."
    elif "timeout" in o or "timed out" in o:
        uitleg = "Geen antwoord van het netwerk (time-out)."
    else:
        uitleg = "Verbinden mislukt."
    return f"{uitleg} ({out.strip()[:200]})" if out.strip() else uitleg
