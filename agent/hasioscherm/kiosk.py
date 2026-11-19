"""Aansturen van de kiosk-browser (cog of chromium) die onder cage draait."""

from __future__ import annotations

import glob
import logging
import os
import pwd
import subprocess
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger(__name__)

KIOSK_USER = os.environ.get("HASIOSCHERM_KIOSK_USER", "kiosk")
KIOSK_SERVICE = "hasioscherm-kiosk.service"
RUN_DIR = Path(os.environ.get("HASIOSCHERM_RUN", "/run/hasioscherm"))
LOCAL = "http://127.0.0.1:" + os.environ.get("HASIOSCHERM_LOCAL_PORT", "8099")


def offline_url(target: str) -> str:
    """Offline-pagina (klok + 'verbinding zoeken'), keert zelf terug naar target."""
    return f"{LOCAL}/offline.html?target={quote(target, safe='')}"


class Kiosk:
    def __init__(self, browser: str = "cog"):
        self.browser = browser
        self.current_url = ""
        try:
            self.uid = pwd.getpwnam(KIOSK_USER).pw_uid
        except KeyError:
            self.uid = None
            log.warning("Gebruiker %s bestaat niet; kioskbediening beperkt", KIOSK_USER)

    # --- hulpfuncties -------------------------------------------------
    def _user_env(self) -> dict:
        env = dict(os.environ)
        if self.uid is not None:
            rt = f"/run/user/{self.uid}"
            env["XDG_RUNTIME_DIR"] = rt
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={rt}/bus"
            socks = sorted(glob.glob(f"{rt}/wayland-[0-9]"))
            if socks:
                env["WAYLAND_DISPLAY"] = os.path.basename(socks[0])
        return env

    def _as_kiosk(self, cmd: list[str], **kw) -> subprocess.CompletedProcess:
        full = cmd if os.geteuid() != 0 else ["runuser", "-u", KIOSK_USER, "--"] + cmd
        return subprocess.run(full, env=self._user_env(), timeout=kw.pop("timeout", 10), **kw)

    def _write_url(self, url: str) -> None:
        try:
            RUN_DIR.mkdir(parents=True, exist_ok=True)
            (RUN_DIR / "url").write_text(url + "\n", encoding="utf-8")
        except OSError as e:
            log.error("Kon URL niet wegschrijven: %s", e)

    # --- acties ---------------------------------------------------------
    def navigate(self, url: str) -> None:
        from .config import mask_url
        log.info("Navigeren naar %s", mask_url(url))
        self.current_url = url
        self._write_url(url)
        if self.browser == "cog":
            try:
                r = self._as_kiosk(["cogctl", "open", url], capture_output=True, text=True)
                if r.returncode == 0:
                    return
                log.warning("cogctl open mislukt (%s); browser herstarten", r.stderr.strip())
            except Exception as e:  # noqa: BLE001
                log.warning("cogctl niet bruikbaar (%s); browser herstarten", e)
        self.restart()

    def reload(self) -> None:
        log.info("Pagina herladen")
        if self.browser == "cog":
            try:
                r = self._as_kiosk(["cogctl", "reload"], capture_output=True, text=True)
                if r.returncode == 0:
                    return
            except Exception:  # noqa: BLE001
                pass
        self.restart()

    def restart(self) -> None:
        log.info("Kiosk-browser herstarten")
        subprocess.run(["systemctl", "restart", KIOSK_SERVICE], timeout=30)

    def is_running(self) -> bool:
        r = subprocess.run(["systemctl", "is-active", "--quiet", KIOSK_SERVICE])
        return r.returncode == 0

    def screenshot(self) -> bytes | None:
        try:
            r = self._as_kiosk(["grim", "-t", "jpeg", "-q", "70", "-"], capture_output=True)
            if r.returncode == 0 and r.stdout:
                return r.stdout
            log.warning("Screenshot mislukt: %s", r.stderr.decode(errors="ignore").strip())
        except Exception as e:  # noqa: BLE001
            log.warning("Screenshot mislukt: %s", e)
        return None
