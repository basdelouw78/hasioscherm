# hasioscherm - notities voor ontwikkeling

- Taal: alle teksten, logs, entiteitsnamen en documentatie in het Nederlands.
- Doel: Pi 5 + Touch Display 2 (10", liggend) als kiosk voor Home Assistant, volledig via MQTT Discovery. Generiek: geen persoonlijke standaarden in de code; alles via de setup-wizard.
- Standaard dashboard: `kiosk/dashboard/index.html` (websocket naar HA, token via `/api/dashboard` op 127.0.0.1, vreemde Origin geweigerd).
- Wijzigingen in Home Assistant nooit ongemerkt: altijd eerst tonen/bevestigen en loggen.
- Agent: `agent/hasioscherm` (Python, alleen Debian-pakketten: paho-mqtt, evdev, psutil, yaml).
- Kiosk: cage + cog (`kiosk/start-browser.sh`), chromium als terugvaloptie.
- Tests: `mosquitto -p 18830 -d && python3 tests/test_agent_mqtt.py && python3 tests/test_alleen_lezen.py && python3 tests/test_dashboard.py && python3 tests/test_wizard.py` (wizardtest gebruikt playwright + chrome en een nep-HA).
- Setup-wizard: `kiosk/setup/index.html` (eigen schermtoetsenbord, geen native select) + `agent/hasioscherm/setup_wizard.py`. HA-wijzigingen altijd via plan → bevestigen → uitvoeren → wijzigingslog.
- Image: pi-gen stage in `image/stage-hasioscherm`, gebouwd door `.github/workflows/build-image.yml`; die stage draait `install.sh` in de chroot.
- Beveiligde modus: `agent/hasioscherm/alleen_lezen.py` (raspi-config overlayfs, updates via markeerbestand op de boot-partitie: uitschakelen → installeren → klaar/mislukt → terug aan). Nooit een herstartlus: pogingen worden geteld.
- Frame: `frame/` (inbouwbak, glas op het stucwerk).
