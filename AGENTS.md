# hasioscherm - notities voor ontwikkeling

- Taal: alle teksten, logs, entiteitsnamen en documentatie in het Nederlands.
- Doel: Pi 5 + Touch Display 2 (10", liggend) als kiosk voor Home Assistant, volledig via MQTT Discovery. Generiek: geen persoonlijke standaarden in de code; alles via de setup-wizard.
- Standaard dashboard: `kiosk/dashboard/index.html` (websocket naar HA, token via `/api/dashboard` op 127.0.0.1, vreemde Origin geweigerd).
- Wijzigingen in Home Assistant nooit ongemerkt: altijd eerst tonen/bevestigen en loggen.
- Agent: `agent/hasioscherm` (Python, alleen Debian-pakketten: paho-mqtt, evdev, psutil, yaml).
- Kiosk: cage + cog (`kiosk/start-browser.sh`), chromium als terugvaloptie.
- Tests: `mosquitto -p 18830 -d && python3 tests/test_agent_mqtt.py && python3 tests/test_alleen_lezen.py && python3 tests/test_fabrieksreset.py && python3 tests/test_gebaar.py && python3 tests/test_dashboard.py && python3 tests/test_wizard.py` (wizardtest gebruikt playwright + chrome en een nep-HA).
- Setup-wizard: `kiosk/setup/index.html` (eigen schermtoetsenbord, geen native select) + `agent/hasioscherm/setup_wizard.py`. HA-wijzigingen altijd via plan → bevestigen → uitvoeren → wijzigingslog.
- Image: pi-gen stage in `image/stage-hasioscherm`, gebouwd door `.github/workflows/build-image.yml`; die stage draait `install.sh` in de chroot.
- Beveiligde modus: `agent/hasioscherm/alleen_lezen.py` (raspi-config overlayfs, updates via markeerbestand op de boot-partitie: uitschakelen → installeren → klaar/mislukt → terug aan). Nooit een herstartlus: pogingen worden geteld.
- Talen: NL (bron), EN, DE. Setup en dashboard gebruiken `t("Nederlandse tekst")` met vertaaltabellen in het bestand; de backend vertaalt alleen de plan-teksten. Nieuwe tekst = ook EN/DE toevoegen (de wizardtest controleert taalwissel).
- Merk: `kiosk/merk/` (Bdlsoft-logo, SVG-bron + PNG). Opstartscherm: Plymouth-thema `plymouth/bdlsoft/` (script; draait mee met `scherm.rotatie`, bijgewerkt door `kiosk/touch-kalibratie.sh`).
- Layout: schermen zijn 1920x1200; kolom-layouts altijd met `min-height: 0` zodat de knoppenbalk zichtbaar blijft. Scrollen gaat via eigen veeg-code (cog scrolt niet altijd zelf).
- Fabrieksreset: `agent/hasioscherm/fabrieksreset.py` (markeerbestand op de boot-partitie, zoals updates; HA-onderdelen blijven bestaan en worden gelogd).
- Frame: `frame/` (inbouwbak, glas op het stucwerk).
