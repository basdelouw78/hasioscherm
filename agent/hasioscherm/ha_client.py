"""Client voor Home Assistant: inloggen, gebruiker/token aanmaken, blueprints.

Wordt alleen door de setup-wizard gebruikt. Het admin-token blijft in het
geheugen en wordt nooit opgeslagen.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

CLIENT_ID = "http://127.0.0.1:8099/"


class HAFout(Exception):
    pass


def _http(method: str, url: str, data=None, token: str | None = None,
          form: bool = False, timeout: float = 15):
    headers = {}
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"message": raw.decode(errors="ignore")}
    except (urllib.error.URLError, OSError) as e:
        raise HAFout(f"Home Assistant niet bereikbaar: {e}") from e


def test_bereikbaar(base: str) -> dict:
    """Controleer of er op dit adres een Home Assistant draait."""
    base = base.rstrip("/")
    status, data = _http("GET", base + "/manifest.json", timeout=5)
    is_ha = status == 200 and isinstance(data, dict) and "Home Assistant" in str(data.get("name", ""))
    return {"bereikbaar": True, "home_assistant": is_ha}


class LoginFlow:
    """Inloggen via de login-flow van HA (gebruikersnaam/wachtwoord, evt. MFA)."""

    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.flow_id: str | None = None

    def start(self, username: str, password: str) -> dict:
        status, data = _http("POST", self.base + "/auth/login_flow", {
            "client_id": CLIENT_ID, "handler": ["homeassistant", None], "redirect_uri": CLIENT_ID,
        })
        if status != 200 or not data or "flow_id" not in data:
            raise HAFout(f"Login starten mislukt ({status})")
        self.flow_id = data["flow_id"]
        return self._step({"username": username, "password": password, "client_id": CLIENT_ID})

    def mfa(self, code: str) -> dict:
        return self._step({"code": code, "client_id": CLIENT_ID})

    def _step(self, payload: dict) -> dict:
        status, data = _http("POST", f"{self.base}/auth/login_flow/{self.flow_id}", payload)
        data = data or {}
        if data.get("type") == "create_entry":
            code = data["result"]
            status, tok = _http("POST", self.base + "/auth/token", {
                "grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID,
            }, form=True)
            if status != 200 or not tok or "access_token" not in tok:
                raise HAFout("Token ophalen mislukt")
            return {"klaar": True, "access_token": tok["access_token"],
                    "refresh_token": tok.get("refresh_token")}
        if data.get("type") == "form" and data.get("step_id") == "mfa":
            return {"klaar": False, "mfa": True}
        fouten = data.get("errors") or {}
        if fouten.get("base") == "invalid_auth":
            raise HAFout("Gebruikersnaam of wachtwoord onjuist")
        if fouten.get("base") == "invalid_code":
            raise HAFout("Verificatiecode onjuist")
        raise HAFout(f"Inloggen mislukt: {fouten or data.get('message') or status}")


def login(base: str, username: str, password: str) -> str:
    """Eenvoudige login zonder MFA; geeft een access-token terug."""
    r = LoginFlow(base).start(username, password)
    if not r.get("klaar"):
        raise HAFout("Deze gebruiker vraagt om MFA")
    return r["access_token"]


class HAClient:
    """REST + websocket met een (admin- of scherm-)token."""

    def __init__(self, base: str, token: str):
        self.base = base.rstrip("/")
        self.token = token

    # --- REST --------------------------------------------------------
    def rest(self, method: str, path: str, data=None):
        status, body = _http(method, self.base + path, data, token=self.token)
        if status >= 400:
            raise HAFout(f"{method} {path} mislukt ({status}): {body}")
        return body

    # --- websocket -----------------------------------------------------
    def ws(self, commands: list[dict]) -> list:
        """Voer een reeks websocket-commando's uit en geef de resultaten terug."""
        from websockets.sync.client import connect

        u = urllib.parse.urlparse(self.base)
        scheme = "wss" if u.scheme == "https" else "ws"
        url = f"{scheme}://{u.netloc}/api/websocket"
        results = []
        with connect(url, open_timeout=10, close_timeout=5) as conn:
            msg = json.loads(conn.recv(timeout=10))
            if msg.get("type") != "auth_required":
                raise HAFout("Onverwacht antwoord van websocket")
            conn.send(json.dumps({"type": "auth", "access_token": self.token}))
            msg = json.loads(conn.recv(timeout=10))
            if msg.get("type") != "auth_ok":
                raise HAFout("Websocket-authenticatie mislukt")
            for i, cmd in enumerate(commands, start=1):
                conn.send(json.dumps({"id": i, **cmd}))
                while True:
                    msg = json.loads(conn.recv(timeout=30))
                    if msg.get("id") == i and msg.get("type") == "result":
                        break
                if not msg.get("success"):
                    err = msg.get("error", {})
                    raise HAFout(f"{cmd['type']} mislukt: {err.get('message', err)}")
                results.append(msg.get("result"))
        return results

    # --- gegevens voor de wizard ------------------------------------
    def notify_diensten(self) -> list[str]:
        services = self.rest("GET", "/api/services")
        for d in services or []:
            if d.get("domain") == "notify":
                return sorted(f"notify.{s}" for s in d.get("services", {})
                              if s not in ("send_message", "persistent_notification"))
        return []

    def bewegingssensoren(self) -> list[dict]:
        states = self.rest("GET", "/api/states")
        out = []
        for s in states or []:
            eid = s.get("entity_id", "")
            if not eid.startswith("binary_sensor."):
                continue
            dc = (s.get("attributes") or {}).get("device_class")
            if dc in ("motion", "occupancy", "presence"):
                out.append({"entity_id": eid,
                            "naam": (s.get("attributes") or {}).get("friendly_name", eid)})
        return sorted(out, key=lambda x: x["naam"].lower())

    def is_admin(self) -> bool:
        user = self.ws([{"type": "auth/current_user"}])[0]
        return bool(user.get("is_admin"))

    # --- wijzigingen (alleen na bevestiging) --------------------------
    def zoek_gebruiker(self, username: str) -> dict | None:
        users = self.ws([{"type": "config/auth/list"}])[0]
        for u in users:
            if username in (u.get("username"),) or any(
                    c.get("data", {}).get("username") == username for c in u.get("credentials", [])):
                return u
        return None

    def maak_gebruiker(self, naam: str, username: str, password: str) -> str:
        user = self.ws([{"type": "config/auth/create", "name": naam,
                         "group_ids": ["system-users"], "local_only": True}])[0]["user"]
        self.ws([{"type": "config/auth_provider/homeassistant/create",
                  "user_id": user["id"], "username": username, "password": password}])
        return user["id"]

    def zet_wachtwoord(self, user_id: str, password: str) -> None:
        self.ws([{"type": "config/auth_provider/homeassistant/admin_change_password",
                  "user_id": user_id, "password": password}])

    def long_lived_token(self, naam: str) -> str:
        return self.ws([{"type": "auth/long_lived_access_token",
                         "client_name": naam, "lifespan": 3650}])[0]

    def bewaar_blueprint(self, pad: str, yaml_tekst: str) -> None:
        self.ws([{"type": "blueprint/save", "domain": "automation", "path": pad,
                  "yaml": yaml_tekst, "allow_override": True}])

    def maak_automatisering(self, auto_id: str, config: dict) -> None:
        self.rest("POST", f"/api/config/automation/config/{auto_id}", config)
