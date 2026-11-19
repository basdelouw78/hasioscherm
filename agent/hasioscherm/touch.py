"""Touch-bewaking via evdev.

Houdt bij wanneer er is aangeraakt. Als het scherm donker is, wordt het
touchapparaat 'gegrepen' (EVIOCGRAB): de browser krijgt de tik dan niet,
zodat de eerste tik alleen het scherm wekt en niet per ongeluk een knop
indrukt. Zodra de vinger loslaat, wordt de greep losgelaten.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

try:
    import evdev
    from evdev import ecodes
except ImportError:  # pragma: no cover - op de Pi altijd aanwezig
    evdev = None
    ecodes = None


def find_touch_device():
    if evdev is None:
        return None
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        caps = dev.capabilities()
        abs_caps = [c[0] if isinstance(c, tuple) else c for c in caps.get(ecodes.EV_ABS, [])]
        if ecodes.ABS_MT_POSITION_X in abs_caps:
            log.info("Touchscherm: %s (%s)", dev.name, path)
            return dev
        dev.close()
    return None


GEBAAR_VINGERS = 3       # setup openen: zoveel vingers ...
GEBAAR_SECONDEN = 4      # ... zolang vasthouden


class TouchWatcher(threading.Thread):
    def __init__(self, on_touch: Callable[[bool], None], should_swallow: Callable[[], bool],
                 on_gebaar: Callable[[], None] | None = None):
        """on_touch(was_dark) wordt bij elke nieuwe aanraking aangeroepen.

        should_swallow() geeft True als een tik op dit moment niet naar de
        browser mag (scherm donker en 'eerste tik alleen wekken' staat aan).
        """
        super().__init__(daemon=True, name="touch")
        self.on_touch = on_touch
        self.should_swallow = should_swallow
        self.on_gebaar = on_gebaar
        self._slots: set[int] = set()
        self._slot = 0
        self._gebaar_timer: threading.Timer | None = None
        self.last_touch = 0.0
        self._grabbed = False
        self._down = False
        self._dev = None
        self._lock = threading.Lock()

    def sync_grab(self) -> None:
        """Pak of laat de greep los, afhankelijk van de schermtoestand."""
        with self._lock:
            dev = self._dev
            if dev is None:
                return
            want = self.should_swallow()
            if want and not self._grabbed:
                try:
                    dev.grab()
                    self._grabbed = True
                except OSError as e:
                    log.debug("grab mislukt: %s", e)
            elif not want and self._grabbed and not self._down:
                # Niet loslaten zolang de vinger nog op het scherm ligt.
                try:
                    dev.ungrab()
                except OSError:
                    pass
                self._grabbed = False

    def run(self) -> None:
        if evdev is None:
            log.warning("python-evdev ontbreekt; touch-bewaking uit")
            return
        while True:
            self._dev = find_touch_device()
            if self._dev is None:
                log.warning("Geen touchscherm gevonden; opnieuw over 10 s")
                time.sleep(10)
                continue
            self.sync_grab()
            try:
                self._loop(self._dev)
            except OSError as e:
                log.warning("Touchapparaat weg (%s); opnieuw zoeken", e)
            finally:
                with self._lock:
                    self._grabbed = False
                    try:
                        self._dev.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self._dev = None
            time.sleep(2)

    # ---- gebaar: 3 vingers 4 seconden vasthouden -> setup openen ----
    def _vingers_gewijzigd(self) -> None:
        if len(self._slots) >= GEBAAR_VINGERS:
            if self._gebaar_timer is None and self.on_gebaar:
                self._gebaar_timer = threading.Timer(GEBAAR_SECONDEN, self._gebaar_klaar)
                self._gebaar_timer.daemon = True
                self._gebaar_timer.start()
        elif self._gebaar_timer is not None:
            self._gebaar_timer.cancel()
            self._gebaar_timer = None

    def _gebaar_klaar(self) -> None:
        self._gebaar_timer = None
        if len(self._slots) >= GEBAAR_VINGERS and self.on_gebaar:
            log.info("Gebaar herkend: %s vingers %s s vastgehouden", GEBAAR_VINGERS, GEBAAR_SECONDEN)
            try:
                self.on_gebaar()
            except Exception:  # noqa: BLE001
                log.exception("Fout bij gebaar")

    def verwerk_mt(self, ev) -> None:
        """Multitouch-slots bijhouden (ook los te testen)."""
        if ev.type != ecodes.EV_ABS:
            return
        if ev.code == ecodes.ABS_MT_SLOT:
            self._slot = ev.value
        elif ev.code == ecodes.ABS_MT_TRACKING_ID:
            if ev.value == -1:
                self._slots.discard(self._slot)
            else:
                self._slots.add(self._slot)
            self._vingers_gewijzigd()

    def _loop(self, dev) -> None:
        self._down = False
        self._slots.clear()
        for ev in dev.read_loop():
            self.verwerk_mt(ev)
            if ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_TOUCH:
                if ev.value == 1 and not self._down:
                    self._down = True
                    self.last_touch = time.time()
                    was_grabbed = self._grabbed
                    try:
                        self.on_touch(was_grabbed)
                    except Exception:  # noqa: BLE001
                        log.exception("Fout in touch-afhandeling")
                elif ev.value == 0:
                    self._down = False
                    self.last_touch = time.time()
                    # Pas na het loslaten de greep laten varen, anders krijgt
                    # de browser een half gebaar binnen.
                    time.sleep(0.15)
                    self.sync_grab()
            elif ev.type == ecodes.EV_ABS and self._down:
                self.last_touch = time.time()
