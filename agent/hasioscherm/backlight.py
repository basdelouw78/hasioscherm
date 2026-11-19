"""Aansturen van de backlight via /sys/class/backlight."""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

SYSFS = Path(os.environ.get("HASIOSCHERM_BACKLIGHT_ROOT", "/sys/class/backlight"))


class Backlight:
    def __init__(self, root: Path = SYSFS):
        self.dev: Path | None = None
        if root.is_dir():
            devs = sorted(p for p in root.iterdir() if (p / "brightness").exists())
            if devs:
                self.dev = devs[0]
        if self.dev is None:
            log.warning("Geen backlight gevonden in %s; aan/uit en helderheid werken niet", root)
        else:
            log.info("Backlight: %s (max %s)", self.dev.name, self.max)

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
