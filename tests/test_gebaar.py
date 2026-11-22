"""Test het 3-vingergebaar (zonder echt touchscherm)."""
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "agent"))
from hasioscherm import touch  # noqa: E402

ec = touch.ecodes
if ec is None:
    print("python-evdev ontbreekt; test overgeslagen")
    sys.exit(0)

fails = []
def check(c, label):
    print(("OK   " if c else "FOUT ") + label)
    if not c: fails.append(label)

def ev(code, value):
    return SimpleNamespace(type=ec.EV_ABS, code=code, value=value)

def vinger(w, slot, neer):
    w.verwerk_mt(ev(ec.ABS_MT_SLOT, slot))
    w.verwerk_mt(ev(ec.ABS_MT_TRACKING_ID, 100 + slot if neer else -1))

touch.GEBAAR_SECONDEN = 1.5   # sneller testen

# 1. drie vingers, met een trillende vinger, lang genoeg vasthouden
gebaren = []
w = touch.TouchWatcher(lambda d: None, lambda: False, lambda: gebaren.append(1))
for s in (0, 1, 2):
    vinger(w, s, True)
for _ in range(4):                 # vinger 2 laat steeds heel even los
    time.sleep(0.4)
    vinger(w, 2, False); time.sleep(0.05); vinger(w, 2, True)
time.sleep(0.6)
check(gebaren == [1], "gebaar herkend ondanks een trillende vinger")
for s in (0, 1, 2):
    vinger(w, s, False)

# 2. te kort vastgehouden
gebaren.clear()
w = touch.TouchWatcher(lambda d: None, lambda: False, lambda: gebaren.append(1))
for s in (0, 1, 2):
    vinger(w, s, True)
time.sleep(0.6)
for s in (0, 1, 2):
    vinger(w, s, False)
time.sleep(2.0)
check(not gebaren, "te kort vastgehouden: geen gebaar")

# 3. twee vingers is geen gebaar
w = touch.TouchWatcher(lambda d: None, lambda: False, lambda: gebaren.append(1))
for s in (0, 1):
    vinger(w, s, True)
time.sleep(2.0)
check(not gebaren, "twee vingers: geen gebaar")

print("\nResultaat:", "ALLES OK" if not fails else f"{len(fails)} fout(en)")
sys.exit(1 if fails else 0)
