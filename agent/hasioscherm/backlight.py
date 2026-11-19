"""Aansturen van de backlight via /sys/class/backlight."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

log = logging.getLogger(__name__)

SYSFS = Path(os.environ.get("HASIOSCHERM_BACKLIGHT_ROOT", "/sys/class/backlight"))


class Backlight:
    """Zoekt de backlight; als die bij de start nog niet bestaat (driver laadt
    later), wordt hooguit elke 10 s opnieuw gezocht."""

    def __init__(self, root: Path = SYSFS):
        self.root = root
        self._dev: Path | None = None
        self._laatst_gezocht = 0.0
        self._zoek(eerste=True)

    def _zoek(self, eerste: bool = False) -> None:
        self._laatst_gezocht = time.monotonic()
        if self.root.is_dir():
            devs = sorted(p for p in self.root.iterdir() if (p / "brightness").exists())
            if devs:
                self._dev = devs[0]
                log.info("Backlight: %s (max %s)", self._dev.name, self.max)
                return
        if eerste:
            log.warning("Nog geen backlight in %s; blijf zoeken", self.root)

    @property
    def dev(self) -> Path | None:
        if self._dev is None and time.monotonic() - self._laatst_gezocht >= 10:
            self._zoek()
        return self._dev

    @property
    def available(self) -> bool:
        return self.dev is not None

    def _read(self, name: str, default: int) -> int:
        if not self.dev:
            return default
        try:
            return int((self.dev / name).read_text().strip())
        except (OSError, ValueError):
            return default

    def _write(self, name: str, value: int) -> None:
        if not self.dev:
            return
        try:
            (self.dev / name).write_text(str(value))
        except OSError as e:
            log.error("Schrijven naar %s/%s mislukt: %s", self.dev, name, e)

    @property
    def max(self) -> int:
        return self._read("max_brightness", 255) or 255

    def get_percent(self) -> int:
        return round(self._read("brightness", 0) * 100 / self.max)

    def set_percent(self, pct: int) -> None:
        pct = max(1, min(100, int(pct)))
        self._write("brightness", max(1, round(pct * self.max / 100)))

    def is_on(self) -> bool:
        if not self.dev:
            return True
        if (self.dev / "bl_power").exists():
            return self._read("bl_power", 0) == 0
        return self._read("brightness", 1) > 0

    def set_on(self, on: bool) -> None:
        if not self.dev:
            return
        if (self.dev / "bl_power").exists():
            # 0 = FB_BLANK_UNBLANK, 4 = FB_BLANK_POWERDOWN
            self._write("bl_power", 0 if on else 4)
        elif not on:
            self._write("brightness", 0)
