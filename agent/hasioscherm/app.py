"""Hoofdprogramma: koppelt scherm, touch, kiosk en MQTT aan elkaar."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import paho.mqtt.client as mqtt

from . import __version__, alleen_lezen, localweb, system
from .backlight import Backlight
from .config import Config, State, load_config, mask_url
from .kiosk import LOCAL, Kiosk, offline_url
from .setup_wizard import SetupWizard
from .touch import TouchWatcher

log = logging.getLogger("hasioscherm")

STATS_INTERVAL = 30
WATCHDOG_INTERVAL = 15
UPDATE_INTERVAL = 6 * 3600
IN_GEBRUIK_SECONDEN = 60


def _hm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _in_window(now_min: int, van: str, tot: str) -> bool:
    a, b = _hm(van), _hm(tot)
    if a == b:
        return False
    if a < b:
        return a <= now_min < b
    return now_min >= a or now_min < b  # over middernacht


def _iso(ts: float) -> str | None:
    if not ts:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.state = State()
        self.dev_id = cfg.device_id
        self.base = f"hasioscherm/{self.dev_id}"
        self.backlight = Backlight()
        self.kiosk = Kiosk(cfg.browser)
        self.lock = threading.RLock()

        self.screen_on = True
        self.last_activity = time.time()
        self.popup_until = 0.0
        self.offline_shown = False
        self.ha_ok = True
        self.latest_version: str | None = None
        self._last_period: str | None = None
        self._last_in_window: bool | None = None
        self.ro_status = ""
        self.update_status = "nog niet gecontroleerd"

        self.touch = TouchWatcher(self._on_touch, self._should_swallow, self.open_setup_gebaar)
        self.wizard = SetupWizard(cfg, self)
        self._disc_topics: list[str] = []

        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                      client_id=f"hasioscherm_{self.dev_id}")
        except AttributeError:  # paho-mqtt 1.x
            self.client = mqtt.Client(client_id=f"hasioscherm_{self.dev_id}")
        if cfg.mqtt_user:
            self.client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
        self.client.will_set(f"{self.base}/status", "offline", retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 60)

    # ------------------------------------------------------------------
    # instellingen (config met overschrijving vanuit HA)
    # ------------------------------------------------------------------
    def setting(self, key: str):
        return self.state.get(key, getattr(self.cfg, key))

    def auto_brightness(self) -> bool:
        return bool(self.state.get("auto_helderheid", True))

    def period(self) -> str:
        now = datetime.now()
        m = now.hour * 60 + now.minute
        return "dag" if _in_window(m, self.cfg.dag_begint, self.cfg.nacht_begint) else "nacht"

    def in_off_window(self) -> bool:
        now = datetime.now()
        m = now.hour * 60 + now.minute
        return any(_in_window(m, u.van, u.tot) for u in self.cfg.uit_tijden)

    def target_brightness(self) -> int:
        override = self.state.get("helderheid_handmatig")
        if override is not None:
            return int(override)
        return int(self.setting(f"helderheid_{self.period()}"))

    # ------------------------------------------------------------------
    # scherm aan/uit
    # ------------------------------------------------------------------
    def set_screen(self, on: bool, reden: str) -> None:
        with self.lock:
            if on == self.screen_on and self.backlight.is_on() == on:
                return
            log.info("Scherm %s (%s)", "aan" if on else "uit", reden)
            self.screen_on = on
            if on:
                self.backlight.set_percent(self.target_brightness())
                self.backlight.set_on(True)
                self.last_activity = time.time()
            else:
                self.backlight.set_on(False)
        self.touch.sync_grab()
        self.publish_state()

    def open_setup_gebaar(self) -> None:
        """3 vingers 4 s vasthouden: de setup is altijd bereikbaar, ook zonder HA of knop."""
        from .setup_wizard import schrijf_wijziging
        schrijf_wijziging("Setup geopend via gebaar op het scherm")
        self.set_screen(True, "gebaar")
        self.popup_until = 0
        self.kiosk.navigate(f"{LOCAL}/setup/index.html")

    def _should_swallow(self) -> bool:
        return (not self.screen_on) and bool(self.setting("eerste_tik_alleen_wekken"))

    def _on_touch(self, was_swallowed: bool) -> None:
        self.last_activity = time.time()
        if not self.screen_on:
            self.set_screen(True, "aanraking")
        self.publish_state()

    # ------------------------------------------------------------------
    # MQTT
    # ------------------------------------------------------------------
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        log.info("Verbonden met MQTT (%s)", rc)
        client.subscribe(f"{self.base}/+/set")
        client.subscribe(f"{self.base}/popup")
        client.subscribe(f"{self.cfg.discovery_prefix}/status")
        self.publish_discovery()
        client.publish(f"{self.base}/status", "online", retain=True)
        self.publish_state()
        self.publish_stats()

    def _on_message(self, client, userdata, msg):
        payload = msg.payload.decode("utf-8", errors="ignore").strip()
        topic = msg.topic
        try:
            if topic == f"{self.cfg.discovery_prefix}/status":
                if payload == "online":  # HA opnieuw gestart
                    self.publish_discovery()
                    client.publish(f"{self.base}/status", "online", retain=True)
                    self.publish_state()
                return
            if topic == f"{self.base}/popup":
                self.handle_popup(payload)
                return
            name = topic[len(self.base) + 1:].rsplit("/", 1)[0]
            self.handle_command(name, payload)
        except Exception:  # noqa: BLE001
            log.exception("Fout bij verwerken van %s: %s", topic, payload)

    def handle_command(self, name: str, payload: str) -> None:
        log.info("Opdracht van HA: %s = %s", name, payload)
        if name == "backlight":
            data = json.loads(payload)
            if "brightness" in data:
                pct = int(data["brightness"])
                self.state.set("helderheid_handmatig", pct)
                if self.screen_on:
                    self.backlight.set_percent(pct)
            if data.get("state") == "ON":
                self.set_screen(True, "HA")
            elif data.get("state") == "OFF":
                self.set_screen(False, "HA")
        elif name == "timeout":
            self.state.set("timeout_minuten", float(payload))
        elif name in ("helderheid_dag", "helderheid_nacht"):
            self.state.set(name, int(float(payload)))
            self.state.set("helderheid_handmatig", None)
            if self.screen_on:
                self.backlight.set_percent(self.target_brightness())
        elif name == "auto_helderheid":
            self.state.set("auto_helderheid", payload == "ON")
            if payload == "ON":
                self.state.set("helderheid_handmatig", None)
                if self.screen_on:
                    self.backlight.set_percent(self.target_brightness())
        elif name == "eerste_tik_alleen_wekken":
            self.state.set("eerste_tik_alleen_wekken", payload == "ON")
            self.touch.sync_grab()
        elif name == "url":
            self.popup_until = 0
            self.kiosk.navigate(self._expand(payload))
        elif name == "wekken":
            self.set_screen(True, "HA wekken")
            self.last_activity = time.time()
        elif name == "herladen":
            self.kiosk.reload()
        elif name == "dashboard":
            self.popup_until = 0
            self.kiosk.navigate(self.cfg.full_dashboard_url)
        elif name == "browser_herstarten":
            self.kiosk.restart()
        elif name == "screenshot":
            self.publish_screenshot()
        elif name == "setup":
            self.set_screen(True, "setup openen")
            self.popup_until = 0
            self.kiosk.navigate(f"{LOCAL}/setup/index.html")
        elif name == "herstarten":
            log.warning("Pi wordt herstart op verzoek van HA")
            self.client.publish(f"{self.base}/status", "offline", retain=True)
            subprocess.Popen(["systemctl", "reboot"])
        elif name == "uitschakelen":
            log.warning("Pi wordt uitgeschakeld op verzoek van HA")
            self.client.publish(f"{self.base}/status", "offline", retain=True)
            subprocess.Popen(["systemctl", "poweroff"])
        elif name == "update":
            self.install_update()
        elif name == "beveiligde_modus":
            self.zet_beveiligde_modus(payload == "ON")
        self.publish_state()

    def _expand(self, url: str) -> str:
        if url.startswith(("http://", "https://", "file://")):
            return url
        return self.cfg.ha_url.rstrip("/") + "/" + url.lstrip("/")

    def handle_popup(self, payload: str) -> None:
        """Pop-up vanuit HA. Payload: een URL, of JSON {"url": ..., "seconden": 60}."""
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            data = {"url": payload}
        url = self._expand(str(data["url"]))
        sec = int(data.get("seconden", self.cfg.popup_terug_seconden))
        log.info("Pop-up: %s (%s s)", url, sec)
        self.set_screen(True, "pop-up")
        self.last_activity = time.time()
        self.kiosk.navigate(url)
        self.popup_until = time.time() + sec if sec > 0 else 0

    # ------------------------------------------------------------------
    # publiceren
    # ------------------------------------------------------------------
    def _device(self) -> dict:
        return {
            "identifiers": [f"hasioscherm_{self.dev_id}"],
            "name": self.cfg.naam,
            "manufacturer": "hasioscherm",
            "model": "Raspberry Pi 5 + Touch Display 2",
            "sw_version": __version__,
            "configuration_url": f"https://github.com/{self.cfg.update_repo}",
        }

    def publish_discovery(self) -> None:
        p = self.cfg.discovery_prefix
        b = self.base
        st = f"{b}/state"
        stats = f"{b}/stats"
        avail = [{"topic": f"{b}/status"}]
        dev = self._device()

        def disc(component: str, key: str, cfg: dict) -> None:
            cfg.setdefault("availability", avail)
            cfg["unique_id"] = f"hasioscherm_{self.dev_id}_{key}"
            cfg["default_entity_id"] = f"{component}.{self.dev_id}_{key}"
            cfg["device"] = dev
            topic = f"{p}/{component}/{self.dev_id}/{key}/config"
            if topic not in self._disc_topics:
                self._disc_topics.append(topic)
            self.client.publish(topic, json.dumps(cfg), retain=True)

        disc("light", "backlight", {
            "name": "Backlight", "schema": "json", "brightness": True,
            "brightness_scale": 100, "supported_color_modes": ["brightness"],
            "command_topic": f"{b}/backlight/set", "state_topic": f"{b}/backlight",
            "icon": "mdi:monitor",
        })
        disc("number", "timeout", {
            "name": "Timeout", "command_topic": f"{b}/timeout/set", "state_topic": st,
            "value_template": "{{ value_json.timeout }}", "min": 0, "max": 120, "step": 0.5,
            "unit_of_measurement": "min", "icon": "mdi:timer-outline", "entity_category": "config",
        })
        for dn, label in (("dag", "Helderheid dag"), ("nacht", "Helderheid nacht")):
            disc("number", f"helderheid_{dn}", {
                "name": label, "command_topic": f"{b}/helderheid_{dn}/set", "state_topic": st,
                "value_template": f"{{{{ value_json.helderheid_{dn} }}}}", "min": 1, "max": 100,
                "unit_of_measurement": "%", "icon": "mdi:brightness-6", "entity_category": "config",
            })
        disc("switch", "auto_helderheid", {
            "name": "Dag/nacht-helderheid", "command_topic": f"{b}/auto_helderheid/set",
            "state_topic": st, "value_template": "{{ value_json.auto_helderheid }}",
            "icon": "mdi:theme-light-dark", "entity_category": "config",
        })
        disc("switch", "eerste_tik_alleen_wekken", {
            "name": "Eerste tik alleen wekken", "command_topic": f"{b}/eerste_tik_alleen_wekken/set",
            "state_topic": st, "value_template": "{{ value_json.eerste_tik_alleen_wekken }}",
            "icon": "mdi:gesture-tap", "entity_category": "config",
        })
        disc("text", "url", {
            "name": "Pagina", "command_topic": f"{b}/url/set", "state_topic": st,
            "value_template": "{{ value_json.url }}", "max": 255, "icon": "mdi:web",
        })
        for key, name, icon, cat in (
            ("wekken", "Wekken", "mdi:gesture-tap-button", None),
            ("dashboard", "Terug naar dashboard", "mdi:view-dashboard", None),
            ("herladen", "Pagina herladen", "mdi:refresh", None),
            ("screenshot", "Screenshot maken", "mdi:camera", None),
            ("setup", "Setup openen", "mdi:cog", "config"),
            ("browser_herstarten", "Browser herstarten", "mdi:restart", "config"),
            ("herstarten", "Herstarten", "mdi:restart-alert", "config"),
            ("uitschakelen", "Uitschakelen", "mdi:power", "config"),
        ):
            c = {"name": name, "command_topic": f"{b}/{key}/set", "icon": icon}
            if key == "herstarten":
                c["device_class"] = "restart"
            if cat:
                c["entity_category"] = cat
            disc("button", key, c)

        sensors = [
            ("cpu_temp", "CPU-temperatuur", {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement"}),
            ("cpu_load", "CPU-belasting", {"unit_of_measurement": "%", "state_class": "measurement", "icon": "mdi:cpu-64-bit"}),
            ("ram", "Geheugen in gebruik", {"unit_of_measurement": "%", "state_class": "measurement", "icon": "mdi:memory"}),
            ("disk", "Schijf in gebruik", {"unit_of_measurement": "%", "state_class": "measurement", "icon": "mdi:harddisk"}),
            ("boot", "Opgestart", {"device_class": "timestamp"}),
            ("ip", "IP-adres", {"icon": "mdi:ip-network"}),
            ("throttled", "Stroom/throttling", {"icon": "mdi:flash-alert"}),
            ("fan", "Ventilator", {"unit_of_measurement": "rpm", "state_class": "measurement", "icon": "mdi:fan"}),
        ]
        for key, name, extra in sensors:
            c = {"name": name, "state_topic": stats,
                 "value_template": f"{{{{ value_json.{key} }}}}", "entity_category": "diagnostic"}
            c.update(extra)
            disc("sensor", key, c)
        disc("sensor", "laatste_aanraking", {
            "name": "Laatste aanraking", "state_topic": st, "device_class": "timestamp",
            "value_template": "{{ value_json.laatste_aanraking }}", "icon": "mdi:gesture-tap",
        })
        disc("binary_sensor", "in_gebruik", {
            "name": "In gebruik", "state_topic": st, "value_template": "{{ value_json.in_gebruik }}",
            "device_class": "occupancy",
        })
        disc("binary_sensor", "te_warm", {
            "name": "Te warm", "state_topic": stats, "value_template": "{{ value_json.te_warm }}",
            "device_class": "heat", "entity_category": "diagnostic",
        })
        disc("binary_sensor", "ha_bereikbaar", {
            "name": "Home Assistant bereikbaar", "state_topic": st,
            "value_template": "{{ value_json.ha_bereikbaar }}", "device_class": "connectivity",
            "entity_category": "diagnostic",
        })
        disc("switch", "beveiligde_modus", {
            "name": "Beveiligde modus", "command_topic": f"{b}/beveiligde_modus/set",
            "state_topic": st, "value_template": "{{ value_json.beveiligde_modus }}",
            "icon": "mdi:shield-lock", "entity_category": "config",
        })
        disc("sensor", "bestandssysteem", {
            "name": "Bestandssysteem", "state_topic": st, "value_template": "{{ value_json.bestandssysteem }}",
            "icon": "mdi:harddisk-plus", "entity_category": "diagnostic",
        })
        disc("sensor", "updatecontrole", {
            "name": "Updatecontrole", "state_topic": st, "value_template": "{{ value_json.updatecontrole }}",
            "icon": "mdi:update", "entity_category": "diagnostic",
        })
        disc("image", "screenshot", {
            "name": "Screenshot", "image_topic": f"{b}/screenshot", "content_type": "image/jpeg",
        })
        disc("update", "software", {
            "name": "Software", "state_topic": f"{b}/update", "command_topic": f"{b}/update/set",
            "payload_install": "install", "release_url": f"https://github.com/{self.cfg.update_repo}/releases",
            "entity_category": "config",
        })

    def remove_discovery(self) -> None:
        """Haal dit apparaat uit HA (bijv. bij een nieuwe naam via de wizard)."""
        if not self._disc_topics:
            self.publish_discovery()
        for t in self._disc_topics:
            self.client.publish(t, "", retain=True)
        for t in ("state", "stats", "backlight", "update", "screenshot", "status"):
            self.client.publish(f"{self.base}/{t}", "", retain=True)
        log.info("Discovery van '%s' verwijderd", self.dev_id)

    def herstart_agent(self, vertraging: float = 0) -> None:
        """Stop het proces; systemd start de agent opnieuw met de nieuwe config."""
        time.sleep(vertraging)
        log.warning("Agent herstart om nieuwe instellingen te laden")
        try:
            self.client.publish(f"{self.base}/status", "offline", retain=True)
            self.client.loop_stop()
        finally:
            os._exit(0)

    def publish_state(self) -> None:
        last_touch = self.touch.last_touch
        data = {
            "timeout": self.setting("timeout_minuten"),
            "helderheid_dag": self.setting("helderheid_dag"),
            "helderheid_nacht": self.setting("helderheid_nacht"),
            "auto_helderheid": "ON" if self.auto_brightness() else "OFF",
            "eerste_tik_alleen_wekken": "ON" if self.setting("eerste_tik_alleen_wekken") else "OFF",
            "url": mask_url(self.kiosk.current_url)[:255],
            "laatste_aanraking": _iso(last_touch),
            "in_gebruik": "ON" if last_touch and time.time() - last_touch < IN_GEBRUIK_SECONDEN else "OFF",
            "ha_bereikbaar": "ON" if self.ha_ok else "OFF",
            "beveiligde_modus": "ON" if self.alleen_lezen_gewenst() else "OFF",
            "bestandssysteem": self.bestandssysteem_status(),
            "updatecontrole": self.update_status,
        }
        self.client.publish(f"{self.base}/state", json.dumps(data), retain=True)
        self.client.publish(f"{self.base}/backlight", json.dumps({
            "state": "ON" if self.screen_on else "OFF",
            "brightness": self.backlight.get_percent() if self.backlight.available else self.target_brightness(),
            "color_mode": "brightness",
        }), retain=True)

    def publish_stats(self) -> None:
        s = system.snapshot()
        temp = s["cpu_temp"]
        out = {
            "cpu_temp": temp, "cpu_load": s["cpu_load"], "ram": s["ram"], "disk": s["disk"],
            "boot": _iso(s["boot_time"]), "ip": s["ip"], "throttled": s["throttled"],
            "fan": s["fan"] if s["fan"] is not None else 0,
            "te_warm": "ON" if temp is not None and temp >= self.cfg.te_warm_graden else "OFF",
        }
        self.client.publish(f"{self.base}/stats", json.dumps(out), retain=True)

    def publish_screenshot(self) -> None:
        img = self.kiosk.screenshot()
        if img:
            self.client.publish(f"{self.base}/screenshot", img, retain=True)

    # ------------------------------------------------------------------
    # updates
    # ------------------------------------------------------------------
    def check_update(self) -> None:
        url = f"https://api.github.com/repos/{self.cfg.update_repo}/releases/latest"
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "hasioscherm"}
        if self.cfg.update_token:
            headers["Authorization"] = f"Bearer {self.cfg.update_token}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
                tag = json.loads(r.read())["tag_name"]
            self.latest_version = tag.lstrip("v")
            self.update_status = "ok"
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404):
                self.update_status = ("geen toegang: token ongeldig" if self.cfg.update_token
                                      else "geen toegang: privé-repo? vul een token in bij de setup")
            else:
                self.update_status = f"mislukt (HTTP {e.code})"
            log.warning("Updatecontrole: %s", self.update_status)
        except Exception as e:  # noqa: BLE001
            self.update_status = "mislukt: geen verbinding met GitHub"
            log.warning("Updatecontrole mislukt: %s", e)
        self.client.publish(f"{self.base}/update", json.dumps({
            "installed_version": __version__,
            "latest_version": self.latest_version or __version__,
            "release_summary": None if self.update_status == "ok" else f"Updatecontrole: {self.update_status}",
        }), retain=True)
        self.publish_state()

    def install_update(self) -> None:
        if not self.latest_version or self.latest_version == __version__:
            log.info("Geen update beschikbaar")
            return
        log.warning("Update naar %s wordt geïnstalleerd", self.latest_version)
        self.ro_status = "update: " + alleen_lezen.start_update("v" + self.latest_version)
        self.publish_state()

    # ------------------------------------------------------------------
    # beveiligde modus (alleen-lezen)
    # ------------------------------------------------------------------
    def alleen_lezen_gewenst(self) -> bool:
        return bool(self.state.get("alleen_lezen", self.cfg.alleen_lezen))

    def bestandssysteem_status(self) -> str:
        if self.ro_status:
            return self.ro_status
        return "alleen-lezen" if alleen_lezen.actief() else "schrijfbaar"

    def zet_beveiligde_modus(self, aan: bool) -> None:
        self.state.set("alleen_lezen", aan)
        self.state.set("ro_pogingen", 0, bron="intern")
        if aan == alleen_lezen.actief():
            return
        from .setup_wizard import schrijf_wijziging
        schrijf_wijziging(f"Beveiligde modus {'aan' if aan else 'uit'} via Home Assistant, herstart")
        ok = alleen_lezen.zet_aan() if aan else alleen_lezen.zet_uit()
        if ok:
            self.ro_status = "herstart nodig"
            self.publish_state()
            self.client.publish(f"{self.base}/status", "offline", retain=True)
            alleen_lezen.herstart()
        else:
            self.ro_status = "omzetten mislukt"

    def beveiliging_bij_opstart(self) -> None:
        if alleen_lezen.actief():
            self.state.set("ro_pogingen", 0, bron="intern")
        gewenst = self.alleen_lezen_gewenst()
        if gewenst and not alleen_lezen.actief() and alleen_lezen.lees_marker() is None:
            pogingen = int(self.state.get("ro_pogingen", 0))
            if pogingen >= 2:
                log.error("Beveiligde modus lukt niet na %s pogingen; blijft schrijfbaar", pogingen)
                self.ro_status = "aanzetten mislukt"
                return
            if self.cfg.setup_klaar:
                self.state.set("ro_pogingen", pogingen + 1, bron="intern")
        r = alleen_lezen.bij_opstart(gewenst, self.cfg.setup_klaar)
        if r:
            log.info("Beveiligde modus bij opstart: %s", r)
            if r in ("installeren", "terugzetten", "aanzetten"):
                self.ro_status = {"installeren": "update wordt geïnstalleerd",
                                  "terugzetten": "herstart na update",
                                  "aanzetten": "herstart naar alleen-lezen"}[r]

    # ------------------------------------------------------------------
    # hoofdlus
    # ------------------------------------------------------------------
    def tick(self) -> None:
        now = time.time()

        # Dag/nacht-wissel: handmatige helderheid vervalt bij de wissel.
        period = self.period()
        if period != self._last_period:
            if self._last_period is not None and self.auto_brightness():
                self.state.set("helderheid_handmatig", None, bron="dag/nacht")
                if self.screen_on:
                    self.backlight.set_percent(self.target_brightness())
                self.publish_state()
            self._last_period = period

        # Vaste uit-tijden: bij het begin van een venster gaat het scherm uit.
        in_window = self.in_off_window()
        if in_window and self._last_in_window is False and self.screen_on:
            self.set_screen(False, "vaste uit-tijd")
        self._last_in_window = in_window

        # Timeout (binnen een uit-venster minstens 1 minuut).
        timeout = float(self.setting("timeout_minuten") or 0)
        if in_window and timeout <= 0:
            timeout = 1
        idle = now - max(self.last_activity, self.touch.last_touch)
        if self.screen_on and timeout > 0 and idle > timeout * 60 and not self.popup_until:
            self.set_screen(False, f"{timeout:g} min geen aanraking")

        # Pop-up verlopen: terug naar het dashboard.
        if self.popup_until and now >= self.popup_until:
            self.popup_until = 0
            self.kiosk.navigate(self.cfg.full_dashboard_url)
            self.publish_state()

    def watchdog(self) -> None:
        ok = system.reachable(self.cfg.ha_url)
        if ok != self.ha_ok:
            log.warning("Home Assistant %s", "weer bereikbaar" if ok else "NIET bereikbaar")
            self.ha_ok = ok
            if not ok and not self.offline_shown:
                # De offline-pagina toont een klok en keert zelf terug
                # zodra het dashboard weer bereikbaar is.
                target = self.kiosk.current_url or self.cfg.full_dashboard_url
                if target.startswith("http://127.0.0.1"):
                    target = self.cfg.full_dashboard_url
                self.kiosk.navigate(offline_url(target))
                self.kiosk.current_url = target  # daar keert de offline-pagina naar terug
                self.offline_shown = True
            if ok:
                self.offline_shown = False
            self.publish_state()
        if not self.kiosk.is_running():
            log.warning("Kiosk draait niet; herstarten")
            self.kiosk.restart()

    def run(self) -> None:
        log.info("hasioscherm %s start als '%s' (config: %s)", __version__, self.cfg.naam, self.cfg.pad)
        self.kiosk.current_url = self.cfg.full_dashboard_url
        self.backlight.set_percent(self.target_brightness())
        self.backlight.set_on(True)
        self.touch.start()
        routes = {("GET", "/api/status"): lambda b, q: (200, {
            "ha_bereikbaar": system.reachable(self.cfg.ha_url, timeout=2),
            "dashboard": self.cfg.full_dashboard_url,   # alleen 127.0.0.1 kan dit lezen
            "naam": self.cfg.naam,
            "versie": __version__,
            "setup_nodig": self.wizard.nodig,
            "taal": self.cfg.taal,
        })}
        # Gegevens voor het meegeleverde standaarddashboard. Alleen bereikbaar
        # vanaf het scherm zelf (127.0.0.1) en zonder CORS, dus andere pagina's
        # kunnen de token niet uitlezen.
        routes[("GET", "/api/dashboard")] = lambda b, q: (200, {
            "naam": self.cfg.naam,
            "versie": __version__,
            "ha_url": self.cfg.ha_url,
            "token": self.cfg.ha_token,
            "stijl": self.cfg.stijl,
            "taal": self.cfg.taal,
            "periode": self.period(),
        })
        routes.update(self.wizard.routes())
        localweb.start(routes)
        try:
            self.beveiliging_bij_opstart()
        except Exception:  # noqa: BLE001
            log.exception("Fout bij controleren beveiligde modus")
        if self.cfg.mqtt_host:
            self.client.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=30)
            self.client.loop_start()
        else:
            log.warning("Nog geen MQTT-server ingesteld; eerst de setup doorlopen")

        next_stats = next_wd = 0.0
        next_update = time.time() + 120
        last_in_gebruik = None
        while True:
            now = time.time()
            try:
                self.tick()
                in_gebruik = bool(self.touch.last_touch and now - self.touch.last_touch < IN_GEBRUIK_SECONDEN)
                if in_gebruik != last_in_gebruik:
                    last_in_gebruik = in_gebruik
                    self.publish_state()
                if now >= next_stats:
                    next_stats = now + STATS_INTERVAL
                    self.publish_stats()
                if now >= next_wd:
                    next_wd = now + WATCHDOG_INTERVAL
                    self.watchdog()
                if now >= next_update:
                    next_update = now + UPDATE_INTERVAL
                    self.check_update()
                    if self.cfg.update_modus == "automatisch" and 2 <= datetime.now().hour < 5:
                        self.install_update()
                _notify_watchdog()
            except Exception:  # noqa: BLE001
                log.exception("Fout in hoofdlus")
            time.sleep(1)


def _notify_watchdog() -> None:
    """Meld aan systemd dat de agent nog leeft (WatchdogSec in de service)."""
    import os
    import socket

    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(b"WATCHDOG=1")
    except OSError:
        pass


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    App(load_config()).run()
