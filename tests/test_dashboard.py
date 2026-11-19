"""Test van het meegeleverde standaarddashboard: agent + nep-Home Assistant (websocket) +
headless browser. Draaien: python3 tests/test_dashboard.py [screenshots-map]
"""
import json, os, sys, tempfile, threading, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
tmp = Path(tempfile.mkdtemp())
SHOTS = Path(sys.argv[1]) if len(sys.argv) > 1 else tmp / "shots"
SHOTS.mkdir(parents=True, exist_ok=True)
HA_PORT = 18861

cfg = tmp / "hasioscherm.yaml"
cfg.write_text(f"""
setup: {{klaar: true}}
scherm: {{naam: Scherm woonkamer}}
homeassistant: {{url: "http://127.0.0.1:{HA_PORT}", token: "SCHERMTOKEN"}}
dashboard: {{url: standaard, stijl: donker}}
mqtt: {{host: ""}}
systeem: {{alleen_lezen: false}}
""")
os.environ.update(HASIOSCHERM_CONFIG=str(cfg), HASIOSCHERM_STATE=str(tmp / "state.json"),
                  HASIOSCHERM_BACKLIGHT_ROOT=str(tmp / "geen"), HASIOSCHERM_WEB=str(REPO / "kiosk"),
                  HASIOSCHERM_LOCAL_PORT="18862", HASIOSCHERM_RUN=str(tmp / "run"),
                  HASIOSCHERM_UPDATE_MARKER=str(tmp / "upd.json"), HASIOSCHERM_CHANGELOG=str(tmp / "w.log"))
sys.path.insert(0, str(REPO / "agent"))

# ---------------- nep-Home Assistant (websocket) ----------------
from websockets.sync.server import serve  # noqa: E402

def st(eid, state, **attr):
    return {"entity_id": eid, "state": state, "attributes": attr, "last_changed": "", "last_updated": "", "context": {}}

STATES = {s["entity_id"]: s for s in [
    st("weather.thuis", "rainy", temperature=8.5, humidity=91, wind_speed=22, wind_speed_unit="km/h", friendly_name="Thuis"),
    st("person.bas", "home", friendly_name="Bas"), st("person.gast", "not_home", friendly_name="Gast"),
    st("light.spots_woonkamer", "on", brightness=200, friendly_name="Spots woonkamer"),
    st("light.leeslamp", "off", friendly_name="Leeslamp"),
    st("switch.woonkamer", "off", friendly_name="Stopcontact tv"),
    st("climate.vloer", "heat", current_temperature=20.5, temperature=21, target_temp_step=0.5, friendly_name="Vloerverwarming"),
    st("media_player.beneden", "playing", media_title="Radio Joe", friendly_name="Beneden"),
    st("sensor.temp_woonkamer", "21.3", device_class="temperature", unit_of_measurement="°C", friendly_name="Temperatuur woonkamer"),
    st("cover.rolluik_keuken", "closed", current_position=0, friendly_name="Rolluik keuken"),
    st("light.overloop", "off", friendly_name="Overloop"),
    st("sensor.cpu", "12", friendly_name="CPU (verborgen)"),
]}
AREAS = [{"area_id": "woonkamer", "name": "Woonkamer"}, {"area_id": "keuken", "name": "Keuken"}, {"area_id": "overloop", "name": "Overloop"}]
ENTS = [{"entity_id": e, "area_id": a, "device_id": None, "hidden_by": None, "disabled_by": None, "entity_category": None}
        for e, a in [("light.spots_woonkamer", "woonkamer"), ("light.leeslamp", "woonkamer"), ("switch.woonkamer", "woonkamer"),
                     ("climate.vloer", "woonkamer"), ("media_player.beneden", "woonkamer"), ("sensor.temp_woonkamer", "woonkamer"),
                     ("cover.rolluik_keuken", "keuken"), ("light.overloop", None)]]
ENTS.append({"entity_id": "light.overloop", "area_id": None, "device_id": "dev1", "hidden_by": None, "disabled_by": None, "entity_category": None})
DEVS = [{"id": "dev1", "area_id": "overloop"}]
calls = []
auth_tokens = []

def handler(conn):
    conn.send(json.dumps({"type": "auth_required"}))
    m = json.loads(conn.recv())
    auth_tokens.append(m.get("access_token"))
    if m.get("access_token") != "SCHERMTOKEN":
        conn.send(json.dumps({"type": "auth_invalid"})); return
    conn.send(json.dumps({"type": "auth_ok"}))
    sub = None
    for raw in conn:
        m = json.loads(raw); t = m["type"]; res = None
        if t == "get_states": res = list(STATES.values())
        elif t == "config/area_registry/list": res = AREAS
        elif t == "config/entity_registry/list": res = ENTS
        elif t == "config/device_registry/list": res = DEVS
        elif t == "subscribe_events": sub = m["id"]
        elif t == "call_service":
            calls.append(m)
            eid = m["target"]["entity_id"]
            if m["service"] == "toggle":
                s = dict(STATES[eid]); s["state"] = "off" if s["state"] in ("on", "open") else ("open" if eid.startswith("cover") else "on")
                STATES[eid] = s
                conn.send(json.dumps({"id": m["id"], "type": "result", "success": True, "result": None}))
                conn.send(json.dumps({"id": sub, "type": "event", "event": {"event_type": "state_changed",
                          "data": {"entity_id": eid, "new_state": s}}}))
                continue
        conn.send(json.dumps({"id": m["id"], "type": "result", "success": True, "result": res}))

srv = serve(handler, "127.0.0.1", HA_PORT)
threading.Thread(target=srv.serve_forever, daemon=True).start()

from hasioscherm import app as appmod, kiosk as kioskmod  # noqa: E402
kioskmod.Kiosk.navigate = lambda self, url: setattr(self, "current_url", url)
kioskmod.Kiosk.restart = lambda self: None
kioskmod.Kiosk.is_running = lambda self: True
appmod.App.check_update = lambda self: None
a = appmod.App(appmod.load_config())
threading.Thread(target=a.run, daemon=True).start()
time.sleep(1.5)

fails = []
def check(c, l):
    print(("OK   " if c else "FOUT ") + l)
    if not c: fails.append(l)

check(a.cfg.full_dashboard_url == "http://127.0.0.1:18862/dashboard/index.html", "standaard dashboard-URL")

import urllib.request, urllib.error  # noqa: E402
req = urllib.request.Request("http://127.0.0.1:18862/api/dashboard", headers={"Origin": "http://evil.example"})
try:
    urllib.request.urlopen(req); check(False, "vreemde Origin geweigerd")
except urllib.error.HTTPError as e:
    check(e.code == 403, "vreemde Origin geweigerd (token niet uit te lezen door andere sites)")

from playwright.sync_api import sync_playwright  # noqa: E402
with sync_playwright() as pw:
    br = pw.chromium.launch(executable_path="/usr/bin/google-chrome", args=["--no-sandbox"])
    pg = br.new_page(viewport={"width": 1920, "height": 1200})
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://127.0.0.1:18862/dashboard/index.html")
    pg.wait_for_selector("text=Nu aan", timeout=10000)
    check(auth_tokens and auth_tokens[-1] == "SCHERMTOKEN", "inloggen met token van het scherm")
    tekst = pg.inner_text("#inhoud")
    check("Regen" in tekst and "8.5°" in tekst, "weer getoond")
    check("Bas" in tekst and "Thuis" in tekst and "Gast" in tekst, "personen getoond")
    check("Spots woonkamer" in tekst and "Radio Joe" in tekst, "'Nu aan' toont lamp en media")
    check("Overloop" in tekst, "ruimte via apparaat (device) gevonden")
    pg.screenshot(path=str(SHOTS / "dashboard-home.png"))
    pg.click("[data-pagina=ruimtes]")
    pg.wait_for_selector(".chips")
    pg.click(".chips button[data-kies=woonkamer]")
    t2 = pg.inner_text("#inhoud")
    check("Vloerverwarming" in t2 and "Leeslamp" in t2 and "CPU" not in t2, "ruimte Woonkamer met juiste tegels")
    pg.click("text=Leeslamp")
    pg.wait_for_timeout(500)
    check(any(c["service"] == "toggle" and c["target"]["entity_id"] == "light.leeslamp" for c in calls), "tik schakelt lamp")
    check("aan" in pg.get_attribute(".tegel[data-id='light.leeslamp']", "class"), "lamp toont 'aan' na event")
    pg.click(".tegel button[data-id='climate.vloer'][data-wat='plus']")
    pg.wait_for_timeout(300)
    check(any(c["service"] == "set_temperature" and c["service_data"]["temperature"] == 21.5 for c in calls), "thermostaat + zet 21,5°")
    pg.screenshot(path=str(SHOTS / "dashboard-ruimtes.png"))
    pg.click("[data-pagina=scherm]")
    check("Scherm woonkamer" in pg.inner_text("#inhoud"), "schermpagina")
    check(not errs, "geen JS-fouten " + str(errs[:2]))
    br.close()

print("Resultaat:", "ALLES OK" if not fails else fails)
sys.exit(1 if fails else 0)
