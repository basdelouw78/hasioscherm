"""Systeeminformatie van de Pi."""

from __future__ import annotations

import glob
import socket
import subprocess
import time

import psutil

_THROTTLE_BITS = {
    0: "onderspanning",
    1: "frequentie begrensd",
    2: "afgeknepen",
    3: "temperatuurgrens",
}


def cpu_temp() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except (OSError, ValueError):
        return None


def fan_rpm() -> int | None:
    for p in glob.glob("/sys/devices/platform/cooling_fan/hwmon/*/fan1_input"):
        try:
            with open(p) as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            pass
    return None


def throttled() -> tuple[int | None, str]:
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                             text=True, timeout=3).stdout
        val = int(out.strip().split("=")[1], 16)
    except Exception:  # noqa: BLE001
        return None, "onbekend"
    nu = [txt for bit, txt in _THROTTLE_BITS.items() if val & (1 << bit)]
    ooit = [txt for bit, txt in _THROTTLE_BITS.items() if val & (1 << (bit + 16))]
    if nu:
        return val, "nu: " + ", ".join(nu)
    if ooit:
        return val, "eerder: " + ", ".join(ooit)
    return val, "ok"


def ip_address() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "onbekend"


def snapshot() -> dict:
    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    load1, _, _ = psutil.getloadavg()
    val, tekst = throttled()
    return {
        "cpu_temp": cpu_temp(),
        "cpu_load": round(psutil.cpu_percent(interval=None), 1),
        "load1": round(load1, 2),
        "ram": round(vm.percent, 1),
        "disk": round(du.percent, 1),
        "uptime": int(time.time() - psutil.boot_time()),
        "boot_time": psutil.boot_time(),
        "ip": ip_address(),
        "throttled": tekst,
        "throttled_raw": val,
        "fan": fan_rpm(),
    }


def reachable(url: str, timeout: float = 3.0) -> bool:
    """Controleer of host:poort van een http-URL bereikbaar is."""
    from urllib.parse import urlparse

    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        return True
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        with socket.create_connection((u.hostname, port), timeout=timeout):
            return True
    except OSError:
        return False


def tijd_van_server(url: str, timeout: float = 5.0) -> float | None:
    """Tijd volgens de HTTP-'Date'-kop van Home Assistant (UTC, seconden).
    Gebruikt als terugval wanneer NTP niet werkt (bijv. een afgeschermd
    IoT-netwerk zonder toegang tot internettijd)."""
    import email.utils
    import urllib.error
    import urllib.request

    if not url.startswith(("http://", "https://")):
        return None
    req = urllib.request.Request(url.rstrip("/") + "/manifest.json", method="HEAD",
                                 headers={"User-Agent": "hasioscherm"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            datum = r.headers.get("Date")
    except urllib.error.HTTPError as e:
        datum = e.headers.get("Date") if e.headers else None
    except OSError:
        return None
    if not datum:
        return None
    try:
        return email.utils.parsedate_to_datetime(datum).timestamp()
    except (TypeError, ValueError):
        return None


def ntp_gesynchroniseerd() -> bool | None:
    try:
        r = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "yes"
    except (OSError, subprocess.TimeoutExpired):
        return None


def zet_klok(epoch: float) -> bool:
    try:
        r = subprocess.run(["date", "-u", "-s", f"@{epoch:.0f}"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
