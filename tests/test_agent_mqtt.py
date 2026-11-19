"""Integratietest van de agent tegen een lokale mosquitto, met nep-backlight.

Draaien: python3 tests/test_agent_mqtt.py   (mosquitto moet op 127.0.0.1:18830 draaien)
"""
import json, os, sys, tempfile, threading, time
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
bl = tmp / "backlight" / "11-0045"
bl.mkdir(parents=True)
(bl / "max_brightness").write_text("31")
(bl / "brightness").write_text("31")
(bl / "bl_power").write_text("0")
(tmp / "web").mkdir()
(tmp / "web" / "offline.html").write_text("<html>offline</html>")
cfgfile = tmp / "cfg.yaml"
cfgfile.write_text("""
scherm: {naam: "Scherm woonkamer"}
homeassistant: {url: "http://127.0.0.1:18831"}
mqtt: {host: 127.0.0.1, port: 18830}
gedrag: {timeout_minuten: 0.05, helderheid_dag: 60, helderheid_nacht: 60, uit_tijden: []}
""")
os.environ.update(HASIOSCHERM_CONFIG=str(cfgfile), HASIOSCHERM_STATE=str(tmp / "state.json"),
                  HASIOSCHERM_BACKLIGHT_ROOT=str(tmp / "backlight"), HASIOSCHERM_WEB=str(tmp / "web"),
                  HASIOSCHERM_LOCAL_PORT="18832", HASIOSCHERM_RUN=str(tmp / "run"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from hasioscherm import app as appmod, kiosk as kioskmod  # noqa: E402
import paho.mqtt.client as mqtt  # noqa: E402

nav = []
kioskmod.Kiosk.navigate = lambda self, url: (nav.append(url), setattr(self, "current_url", url))
kioskmod.Kiosk.restart = lambda self: nav.append("RESTART")
kioskmod.Kiosk.reload = lambda self: nav.append("RELOAD")
kioskmod.Kiosk.is_running = lambda self: True
kioskmod.Kiosk.screenshot = lambda self: b"\xff\xd8JPEG"
appmod.App.check_update = lambda self: None

a = appmod.App(appmod.load_config())
threading.Thread(target=a.run, daemon=True).start()

msgs = {}
c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
c.on_message = lambda cl, u, m: msgs.__setitem__(m.topic, m.payload)
c.connect("127.0.0.1", 18830); c.subscribe("#"); c.loop_start()
time.sleep(3)

def j(t): return json.loads(msgs[t])
fails = []
def check(cond, label):
    print(("OK   " if cond else "FOUT ") + label)
    if not cond: fails.append(label)

configs = [t for t in msgs if t.startswith("homeassistant/") and t.endswith("/config")]
check(len(configs) >= 25, f"discovery: {len(configs)} entiteiten")
check(msgs.get("hasioscherm/scherm_woonkamer/status") == b"online", "status online")
check(j("hasioscherm/scherm_woonkamer/backlight")["state"] == "ON", "backlight aan")
check((bl / "brightness").read_text() == str(round(60 * 31 / 100)), "helderheid 60% gezet")

c.publish("hasioscherm/scherm_woonkamer/backlight/set", json.dumps({"state": "ON", "brightness": 40})); time.sleep(1)
check((bl / "brightness").read_text() == str(round(40 * 31 / 100)), "helderheid via HA naar 40%")

time.sleep(4)  # timeout 0.05 min = 3 s
check((bl / "bl_power").read_text() == "4", "scherm uit na timeout")
check(j("hasioscherm/scherm_woonkamer/backlight")["state"] == "OFF", "state OFF gepubliceerd")
check(a._should_swallow(), "eerste tik wordt ingeslikt als scherm donker")

c.publish("hasioscherm/scherm_woonkamer/wekken/set", "PRESS"); time.sleep(1)
check((bl / "bl_power").read_text() == "0", "wekken via HA")

c.publish("hasioscherm/scherm_woonkamer/popup", json.dumps({"url": "/local/deurbel.html", "seconden": 2})); time.sleep(1)
check(nav and nav[-1] == "http://127.0.0.1:18831/local/deurbel.html", "pop-up geopend")
time.sleep(3)
check(nav[-1].endswith("/dashboard/index.html"), "pop-up keert terug naar het (standaard)dashboard")

c.publish("hasioscherm/scherm_woonkamer/screenshot/set", "PRESS"); time.sleep(1)
check(msgs.get("hasioscherm/scherm_woonkamer/screenshot") == b"\xff\xd8JPEG", "screenshot gepubliceerd")

c.publish("hasioscherm/scherm_woonkamer/timeout/set", "10"); time.sleep(1)
check(j("hasioscherm/scherm_woonkamer/state")["timeout"] == 10.0, "timeout via HA aangepast")
check(json.loads((tmp / "state.json").read_text())["timeout_minuten"] == 10.0, "instelling bewaard")

st = j("hasioscherm/scherm_woonkamer/stats")
check("cpu_temp" in st and "ram" in st, "systeemsensoren gepubliceerd")

# HA onbereikbaar (niets luistert op 18831) -> offline-pagina
check(any("offline.html" in u for u in nav), "offline-pagina getoond als HA onbereikbaar is")
import urllib.request
s = json.loads(urllib.request.urlopen("http://127.0.0.1:18832/api/status").read())
check(s["ha_bereikbaar"] is False and s["dashboard"].endswith("/dashboard/index.html"), "lokale /api/status werkt")
check(b"offline" in urllib.request.urlopen("http://127.0.0.1:18832/offline.html").read(), "offline.html geserveerd")

print("\nResultaat:", "ALLES OK" if not fails else f"{len(fails)} fout(en)")
sys.exit(1 if fails else 0)
