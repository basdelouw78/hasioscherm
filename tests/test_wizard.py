"""End-to-end test van de setup-wizard: echte UI in een headless browser,
de agent, een lokale mosquitto en een nagebootste Home Assistant.

Draaien: mosquitto -p 18830 -d && python3 tests/test_wizard.py [screenshots-map]
"""
import json, os, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp())
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else tmp / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)

bl = tmp / "backlight" / "11-0045"; bl.mkdir(parents=True)
(bl / "max_brightness").write_text("31"); (bl / "brightness").write_text("31"); (bl / "bl_power").write_text("0")
cfgfile = tmp / "boot" / "hasioscherm.yaml"; cfgfile.parent.mkdir()
cfgfile.write_text("scherm: {naam: Nieuw scherm}\nmqtt: {host: 127.0.0.1, port: 18830}\n")
os.environ.update(HASIOSCHERM_CONFIG=str(cfgfile), HASIOSCHERM_STATE=str(tmp / "state.json"),
                  HASIOSCHERM_BACKLIGHT_ROOT=str(tmp / "backlight"), HASIOSCHERM_WEB=str(REPO / "kiosk"),
                  HASIOSCHERM_LOCAL_PORT="18842", HASIOSCHERM_RUN=str(tmp / "run"),
                  HASIOSCHERM_REPO=str(REPO), HASIOSCHERM_CHANGELOG=str(tmp / "boot" / "hasioscherm-wijzigingen.log"))
sys.path.insert(0, str(REPO / "agent"))

# ---------------- nep-Home Assistant ----------------
HA_PORT = 18843
calls = []

class FakeHA(BaseHTTPRequestHandler):
    def _j(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/manifest.json": return self._j(200, {"name": "Home Assistant"})
        if self.path == "/api/services":
            return self._j(200, [{"domain": "notify", "services": {"mobile_app_bas_telefoon": {}, "send_message": {}}}])
        if self.path == "/api/states":
            return self._j(200, [
                {"entity_id": "binary_sensor.pir_woonkamer", "attributes": {"device_class": "motion", "friendly_name": "PIR woonkamer (alarm)"}},
                {"entity_id": "binary_sensor.deur", "attributes": {"device_class": "door"}}])
        self._j(404, {})
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); raw = self.rfile.read(n).decode()
        if self.path == "/auth/login_flow": return self._j(200, {"flow_id": "f1"})
        if self.path == "/auth/login_flow/f1":
            d = json.loads(raw)
            if d.get("username") == "admin" and d.get("password") == "geheim":
                return self._j(200, {"type": "form", "step_id": "mfa", "errors": {}})
            if d.get("code") == "123456":
                return self._j(200, {"type": "create_entry", "result": "code-admin"})
            if d.get("username") == "hasioscherm_scherm_woonkamer":
                return self._j(200, {"type": "create_entry", "result": "code-scherm"})
            return self._j(200, {"type": "form", "errors": {"base": "invalid_auth"}})
        if self.path == "/auth/token":
            tok = "tok-admin" if "code-admin" in raw else "tok-scherm"
            return self._j(200, {"access_token": tok, "refresh_token": "r"})
        if self.path.startswith("/api/config/automation/config/"):
            calls.append(("automation", self.path.rsplit("/", 1)[1], json.loads(raw)))
            return self._j(200, {"result": "ok"})
        self._j(404, {})

srv = ThreadingHTTPServer(("127.0.0.1", HA_PORT), FakeHA)
threading.Thread(target=srv.serve_forever, daemon=True).start()

from hasioscherm import app as appmod, kiosk as kioskmod, ha_client  # noqa: E402

def fake_ws(self, commands):
    out = []
    for c in commands:
        calls.append(("ws", self.token, c["type"], c))
        t = c["type"]
        if t == "auth/current_user": out.append({"is_admin": self.token == "tok-admin"})
        elif t == "config/auth/list": out.append([])
        elif t == "config/auth/create": out.append({"user": {"id": "u1"}})
        elif t == "auth/long_lived_access_token": out.append("LLAT-scherm")
        elif t == "auth/refresh_tokens": out.append([
            {"id": "oud1", "type": "long_lived_access_token", "client_name": "hasioscherm Scherm woonkamer 2026-11-19 10:00"},
            {"id": "ander", "type": "long_lived_access_token", "client_name": "Mijn telefoon"},
            {"id": "huidig", "type": "normal", "client_name": None}])
        else: out.append(None)
    return out
ha_client.HAClient.ws = fake_ws
kioskmod.Kiosk.navigate = lambda self, url: setattr(self, "current_url", url)
kioskmod.Kiosk.restart = lambda self: None
kioskmod.Kiosk.is_running = lambda self: True
appmod.App.check_update = lambda self: None
herstart = []
appmod.App.herstart_agent = lambda self, v=0: herstart.append("software")
appmod.App.herstart_systeem_beveiligd = lambda self, v=0: herstart.append("systeem")

a = appmod.App(appmod.load_config())
threading.Thread(target=a.run, daemon=True).start()
time.sleep(1.5)

fails = []
def check(cond, label):
    print(("OK   " if cond else "FOUT ") + label)
    if not cond: fails.append(label)

check(a.wizard.nodig, "setup nodig bij nieuwe installatie")

# ---------------- wizard doorlopen in de browser ----------------
from playwright.sync_api import sync_playwright  # noqa: E402

def typ(page, selector, tekst):
    page.dispatch_event(selector, "pointerdown")
    for ch in tekst:
        key = "spatie" if ch == " " else ch
        if ch.isupper():
            page.click("#tb button[data-k=shift]")
            key = ch
        if ch in "0123456789":
            page.click(f'#tb button[data-k="{ch}"]')
        else:
            if ch in "#$€;,'\"<>!?&%+=*()[]{}~^":
                page.click("#tb button[data-k=sym]")
            page.click(f'#tb button[data-k="{key}"]' if ch != " " else "#tb button.spatie")
    page.click("#tb button[data-k=ok]")

def wis(page, selector):
    page.dispatch_event(selector, "pointerdown")
    n = len(page.input_value(selector))
    for _ in range(n): page.click("#tb button[data-k=bs]")

with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    page = br.new_page(viewport={"width": 1920, "height": 1200})
    page.goto("http://127.0.0.1:18842/offline.html")
    page.wait_for_url("**/setup/index.html", timeout=10000)
    check(True, "offline-pagina stuurt door naar setup")

    def knop_zichtbaar(stapnaam):
        bb = page.locator("#verder").bounding_box()
        check(bb is not None and bb["y"] + bb["height"] <= 1200, f"Volgende-knop zichtbaar ({stapnaam})")

    # taalstap eerst
    page.wait_for_selector("button[data-taal=en]")
    page.click("button[data-taal=en]")
    page.wait_for_selector("text=Choose your language")
    check(page.inner_text("#verder") == "Next", "taal wisselt direct (Engels)")
    page.click("button[data-taal=de]")
    check(page.inner_text("#verder") == "Weiter", "taal wisselt direct (Duits)")
    page.click("button[data-taal=nl]")
    check(page.locator("nav img.logo").count() == 1, "Bdlsoft-logo in de setup")
    page.click("#resetopen")
    check(page.locator("#resetvenster.open #resetja").count() == 1, "fabrieksreset vraagt bevestiging")
    page.screenshot(path=str(SHOTS / "0-reset.png"))
    page.click("#resetnee")
    check(page.locator("#resetvenster.open").count() == 0, "fabrieksreset annuleren")
    page.screenshot(path=str(SHOTS / "0-taal.png"))
    knop_zichtbaar("taal")
    page.click("#verder")                       # -> scherm
    page.wait_for_selector("input[data-pad=naam]")
    knop_zichtbaar("scherm")

    wis(page, "input[data-pad=naam]")
    typ(page, "input[data-pad=naam]", "Scherm woonkamer")
    page.screenshot(path=str(SHOTS / "1-scherm.png"))
    page.click("#verder")                       # -> netwerk
    page.wait_for_selector("text=Wifi-netwerken")
    page.click("#verder")                       # -> HA
    typ(page, "input[data-pad=ha_url]", "127.0.0.1:18843")
    page.click("#hatest")
    page.wait_for_selector("text=Home Assistant gevonden")
    page.screenshot(path=str(SHOTS / "3-ha.png"))
    page.click("#verder")                       # -> koppeling (automatisch)
    typ(page, "input[data-pad=_admin]", "admin")
    page.dispatch_event("input[data-pad=_adminww]", "pointerdown")
    page.screenshot(path=str(SHOTS / "4-login-toetsenbord.png"))
    typ(page, "input[data-pad=_adminww]", "geheim")
    page.click("#login")
    page.wait_for_selector("input[data-pad=_mfa]")
    typ(page, "input[data-pad=_mfa]", "123456")
    page.click("#mfa")
    page.wait_for_selector("text=Ingelogd als beheerder")
    page.click("#verder")                       # -> gedrag
    page.click("#uitbij")
    knop_zichtbaar("gedrag")
    inh = page.evaluate("(() => { const e = document.getElementById('inhoud'); return [e.scrollHeight, e.clientHeight]; })()")
    if inh[0] > inh[1]:
        # vegen met de muis moet de inhoud laten scrollen
        page.mouse.move(1100, 900); page.mouse.down(); page.mouse.move(1100, 500, steps=8); page.mouse.up()
        check(page.evaluate("document.getElementById('inhoud').scrollTop") > 100, "inhoud scrolt bij vegen")
    page.screenshot(path=str(SHOTS / "5-gedrag.png"))
    page.click("#verder")                       # -> beweging & meldingen
    page.wait_for_selector("text=PIR woonkamer (alarm)")
    page.click("text=PIR woonkamer (alarm)")
    page.click("text=notify.mobile_app_bas_telefoon")
    page.screenshot(path=str(SHOTS / "6-beweging.png"))
    page.click("#verder")                       # -> overzicht
    page.wait_for_selector("ol.plan li")
    plan = page.inner_text("ol.plan")
    page.screenshot(path=str(SHOTS / "7-overzicht.png"))
    check("nieuwe gebruiker" in plan and "blueprint" in plan and "beweging" in plan, "plan toont alle stappen")
    check(not any(c[0] == "automation" for c in calls), "niets aangemaakt vóór bevestigen")
    page.click("#verder")                       # bevestigen
    page.wait_for_selector("text=Klaar!", timeout=15000)
    check(page.locator("text=één keer opnieuw op").count() == 1, "melding: één herstart voor beveiligde modus")
    page.wait_for_timeout(4000)
    check("/setup/" in page.url, "setup blijft staan tijdens systeemherstart (geen 'niet bereikbaar')")
    page.screenshot(path=str(SHOTS / "8-klaar.png"))
    br.close()

# ---------------- resultaat controleren ----------------
ws_types = [c[2] for c in calls if c[0] == "ws"]
check("config/auth/create" in ws_types and "config/auth_provider/homeassistant/create" in ws_types, "HA-gebruiker aangemaakt")
check([c[3]["refresh_token_id"] for c in calls if c[0] == "ws" and c[2] == "auth/delete_refresh_token"] == ["oud1"],
      "alleen oude hasioscherm-token van het scherm opgeruimd")
check(any(c[0] == "ws" and c[2] == "auth/long_lived_access_token" and c[1] == "tok-scherm" for c in calls),
      "token aangemaakt als scherm-gebruiker (niet als admin)")
check(ws_types.count("blueprint/save") == 2, "2 blueprints opgeslagen")
autos = {c[1]: c[2] for c in calls if c[0] == "automation"}
check("hasioscherm_scherm_woonkamer_beweging" in autos and
      autos["hasioscherm_scherm_woonkamer_beweging"]["use_blueprint"]["input"]["bewegingssensor"] == "binary_sensor.pir_woonkamer",
      "automatisering beweging met PIR")
check(autos.get("hasioscherm_scherm_woonkamer_meldingen", {}).get("use_blueprint", {}).get("input", {}).get("notify_dienst")
      == "notify.mobile_app_bas_telefoon", "automatisering meldingen")

import yaml  # noqa: E402
cfg = yaml.safe_load(cfgfile.read_text())
check(cfg["setup"]["klaar"] is True and cfg["scherm"]["naam"] == "Scherm woonkamer", "config geschreven")
check(cfg["homeassistant"]["url"] == "http://127.0.0.1:18843" and cfg["homeassistant"]["token"] == "LLAT-scherm", "HA-url + token in config")
check(cfg["mqtt"]["gebruiker"] == "hasioscherm_scherm_woonkamer" and len(cfg["mqtt"]["wachtwoord"]) > 20, "MQTT-gebruiker in config")
check(cfg["gedrag"]["uit_tijden"] == [{"van": "23:30", "tot": "06:30"}], "uit-tijden in config")
check("geheim" not in cfgfile.read_text() and "tok-admin" not in cfgfile.read_text(), "admin-gegevens niet opgeslagen")
log = (tmp / "boot" / "hasioscherm-wijzigingen.log").read_text()
check("Setup klaar" in log and "nieuwe gebruiker" in log, "wijzigingslog geschreven")
check(a.wizard._admin is None, "admin-sessie vergeten na setup")
check(herstart == ["systeem"], "één systeemherstart (beveiligde modus), geen losse software-herstart")
check("beveiligde modus aanzetten en opnieuw opstarten" in log, "systeemherstart staat in plan en log")

# ---------------- snel aanpassen (setup is klaar) ----------------
a.cfg.__dict__.update(appmod.load_config().__dict__)   # zoals na de herstart
calls.clear(); herstart.clear()
appmod.alleen_lezen.actief = lambda: True              # scherm draait inmiddels beveiligd
mqtt_voor = yaml.safe_load(cfgfile.read_text())["mqtt"]
with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    page = br.new_page(viewport={"width": 1920, "height": 1200})
    page.goto("http://127.0.0.1:18842/setup/index.html")
    page.wait_for_selector("button[data-onderdeel=scherm]")
    check(page.locator("#verder").is_hidden(), "instellingenmenu zonder Volgende-knop")
    page.screenshot(path=str(SHOTS / "9-menu.png"))
    page.click("button[data-onderdeel=scherm]")
    page.wait_for_selector("input[data-pad=naam]")
    check(page.inner_text("#verder") == "Opslaan", "snel aanpassen: knop Opslaan")
    page.click("button[data-dash=eigen]")
    wis(page, "input[data-pad=dashboard_url]")
    typ(page, "input[data-pad=dashboard_url]", "/local/thuis/dash.html")
    page.click("#verder")                       # -> overzicht
    page.wait_for_selector("ol.plan li")
    plan2 = page.inner_text("ol.plan")
    check("gebruiker" not in plan2 and "token" not in plan2, "snel aanpassen: geen nieuwe gebruiker/token in het plan")
    page.click("#verder")                       # bevestigen
    page.wait_for_selector("text=Klaar!", timeout=15000)
    br.close()
cfg2 = yaml.safe_load(cfgfile.read_text())
check(cfg2["dashboard"]["url"] == "/local/thuis/dash.html", "snel aanpassen: pagina opgeslagen")
check(cfg2["mqtt"] == mqtt_voor and cfg2["homeassistant"]["token"] == "LLAT-scherm", "snel aanpassen: koppeling ongewijzigd")
check(not [c for c in calls if c[0] == "ws" and c[2].startswith(("config/auth", "auth/"))], "snel aanpassen: niets in HA gewijzigd")
check(herstart == ["software"], "snel aanpassen: alleen software herstart")

print(f"\nScreenshots: {SHOTS}")
print("Resultaat:", "ALLES OK" if not fails else f"{len(fails)} fout(en): {fails}")
sys.exit(1 if fails else 0)
