"""Test van de stappen van beveiligde modus + update (zonder echte raspi-config/herstart).

Draaien: python3 tests/test_alleen_lezen.py
"""
import os
import sys
import tempfile
from pathlib import Path

tmp = Path(tempfile.mkdtemp())
os.environ["HASIOSCHERM_UPDATE_MARKER"] = str(tmp / "update.json")
os.environ["HASIOSCHERM_CHANGELOG"] = str(tmp / "wijzigingen.log")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agent"))

from hasioscherm import alleen_lezen as al  # noqa: E402

acties = []
toestand = {"overlay": True}
al.actief = lambda: toestand["overlay"]
al.zet_aan = lambda: (acties.append("aan"), True)[1]
al.zet_uit = lambda: (acties.append("uit"), True)[1]
al.herstart = lambda: acties.append("herstart")
al.subprocess.Popen = lambda cmd, **kw: acties.append("run:" + " ".join(cmd[-2:]))

fails = []
def check(c, l):
    print(("OK   " if c else "FOUT ") + l)
    if not c:
        fails.append(l)

# 1. update in beveiligde modus
r = al.start_update("v0.5.0")
check(r == "herstart" and acties == ["uit", "herstart"], "update: beveiliging uit + herstart")
check(al.lees_marker()["stap"] == "uitschakelen", "marker: uitschakelen")

# 2. na herstart: schrijfbaar -> installeren
toestand["overlay"] = False; acties.clear()
r = al.bij_opstart(True, True)
check(r == "installeren" and acties == ["run:/opt/hasioscherm/update.sh v0.5.0"], "na herstart: update.sh gestart")

# 3. agent herstart door install.sh terwijl update nog loopt
acties.clear()
check(al.bij_opstart(True, True) == "installeren" and acties == [], "tussentijdse herstart: niets dubbel")

# 4. update.sh meldt klaar -> beveiliging weer aan + herstart, marker weg
m = al.lees_marker(); m["stap"] = "klaar"; al.schrijf_marker(m); acties.clear()
r = al.bij_opstart(True, True)
check(r == "terugzetten" and acties == ["aan", "herstart"] and al.lees_marker() is None, "klaar: beveiliging aan + herstart")

# 5. mislukt -> ook beveiliging terug
al.schrijf_marker({"tag": "v0.6.0", "stap": "mislukt", "pogingen": 0}); acties.clear()
check(al.bij_opstart(True, True) == "terugzetten" and acties == ["aan", "herstart"], "mislukt: beveiliging toch terug")

# 6. herstartlus voorkomen
al.schrijf_marker({"tag": "v0.7.0", "stap": "installeren", "pogingen": 3}); acties.clear()
check(al.bij_opstart(True, True) == "gestopt" and al.lees_marker() is None, "te veel pogingen: gestopt")

# 7. zonder beveiliging: update direct
toestand["overlay"] = False; acties.clear()
check(al.start_update("v0.8.0") == "direct" and acties == ["run:/opt/hasioscherm/update.sh v0.8.0"], "schrijfbaar: update direct")

# 8. eerste keer na setup: aanzetten; zonder setup niet
acties.clear()
check(al.bij_opstart(True, False) is None and acties == [], "vóór setup: niets doen")
check(al.bij_opstart(True, True) == "aanzetten" and acties == ["aan", "herstart"], "na setup: beveiliging aanzetten")
acties.clear()
check(al.bij_opstart(False, True) is None and acties == [], "uitgezet in instellingen: niets doen")

log = (tmp / "wijzigingen.log").read_text()
check("beveiligde modus tijdelijk uit" in log and "weer aan" in log, "alles in het wijzigingslog")
print("Resultaat:", "ALLES OK" if not fails else fails)
sys.exit(1 if fails else 0)
