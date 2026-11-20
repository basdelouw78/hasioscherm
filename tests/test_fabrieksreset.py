"""Test fabrieksreset (zonder echte herstart, nmcli of raspi-config)."""
import json
import os
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
boot = tmp / "boot"; boot.mkdir()
cfg = boot / "hasioscherm.yaml"
cfg.write_text("scherm:\n  naam: Testscherm\nsetup_klaar: true\n")
state = boot / "hasioscherm-state.json"; state.write_text("{}")
(boot / "hasioscherm-diagnose.txt").write_text("x")
voorbeeld = tmp / "voorbeeld.yaml"; voorbeeld.write_text("scherm:\n  naam: Scherm\n")
os.environ.update(HASIOSCHERM_BOOT=str(boot), HASIOSCHERM_CONFIG=str(cfg), HASIOSCHERM_STATE=str(state),
                  HASIOSCHERM_VOORBEELD=str(voorbeeld), HASIOSCHERM_UPDATE_MARKER=str(boot / "hasioscherm-update.json"),
                  HASIOSCHERM_CHANGELOG=str(boot / "hasioscherm-wijzigingen.log"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))

from hasioscherm import alleen_lezen, fabrieksreset, setup_wizard  # noqa: E402

setup_wizard.CHANGELOG = boot / "hasioscherm-wijzigingen.log"
fabrieksreset.config_write_path = lambda: cfg
herstarts = []
alleen_lezen.herstart = lambda: herstarts.append(1)
overlay = {"aan": False, "uitgezet": 0}
alleen_lezen.actief = lambda: overlay["aan"]
alleen_lezen.zet_uit = lambda: overlay.__setitem__("uitgezet", overlay["uitgezet"] + 1) or True
fabrieksreset._wifi_vergeten = lambda: ["Thuis"]

fails = []
def check(c, label):
    print(("OK   " if c else "FOUT ") + label)
    if not c: fails.append(label)

# 1. zonder beveiligde modus: direct wissen + herstart
r = fabrieksreset.start("testscherm", "test")
check(r == "herstart" and herstarts, "reset zonder beveiligde modus herstart")
check(cfg.read_text() == voorbeeld.read_text(), "instellingen terug naar voorbeeld")
check(not state.exists() and not (boot / "hasioscherm-diagnose.txt").exists(), "toestand en diagnose gewist")
logtekst = (boot / "hasioscherm-wijzigingen.log").read_text()
check("Fabrieksreset gestart" in logtekst and "hasioscherm_testscherm" in logtekst, "spoor met HA-restanten in log")
check("wifi vergeten: Thuis" in logtekst, "wifi vergeten gelogd")

# 2. met beveiligde modus: marker, overlay uit, herstart; afmaken bij opstart
cfg.write_text("scherm:\n  naam: Weer\nsetup_klaar: true\n"); herstarts.clear()
overlay["aan"] = True
r = fabrieksreset.start("testscherm", "test")
check(r == "herstart" and overlay["uitgezet"] == 1 and fabrieksreset.MARKER.exists(), "beveiligde modus: eerst overlay uit")
check(cfg.read_text().startswith("scherm:\n  naam: Weer"), "nog niets gewist zolang overlay aan is")
overlay["aan"] = False
r = fabrieksreset.bij_opstart()
check(r == "uitgevoerd" and cfg.read_text() == voorbeeld.read_text() and not fabrieksreset.MARKER.exists(),
      "na herstart: reset afgemaakt, marker weg")

# 3. geen herstartlus
fabrieksreset.MARKER.write_text(json.dumps({"stap": "uitschakelen", "pogingen": 3}))
overlay["aan"] = True
check(fabrieksreset.bij_opstart() == "gestopt" and not fabrieksreset.MARKER.exists(), "stopt na te veel pogingen")
check(fabrieksreset.bij_opstart() is None, "zonder marker niets te doen")

print("\nResultaat:", "ALLES OK" if not fails else f"{len(fails)} fout(en)")
sys.exit(1 if fails else 0)
