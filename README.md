# hasioscherm

Een Raspberry Pi 5 met een Touch Display 2 (10") als ingebouwd wandscherm voor
Home Assistant, voor iedereen te gebruiken: alle instellingen doe je tijdens de
setup op het scherm zelf. Het scherm toont het meegeleverde **standaard
dashboard** of je **eigen dashboard**, en is volledig via MQTT in
Home Assistant te bedienen: backlight, helderheid, pagina's/pop-ups, herstarten,
systeemstatus en updates. Vergelijkbaar met TouchKio, maar zonder Chrome en
gebouwd om nooit SSH nodig te hebben.

> **Status: alle fasen gebouwd, nog niet getest op de echte hardware.** De
> software is getest tegen een echte MQTT-broker, een nagebootst scherm en een
> nagebootste Home Assistant (wizard en standaard dashboard zijn in een headless browser doorlopen).
> Het frame is ontworpen maar nog niet geprint. Zie [Fasering](#fasering).

## Hoe het werkt

```
┌────────────── Raspberry Pi 5 ──────────────┐        ┌──── Home Assistant ────┐
│ cage (Wayland-kiosk)                       │        │                        │
│  └─ cog (WPE WebKit)  ── dashboard-HTML ───┼──http──┤ /config/www/...        │
│                                            │        │                        │
│ hasioscherm-agent (Python)                 │        │ Mosquitto              │
│  ├─ backlight  (/sys/class/backlight)      │        │  └─ MQTT Discovery     │
│  ├─ touch      (evdev, eerste tik = wekken)├──mqtt──┤     → apparaat met     │
│  ├─ systeem    (temp, RAM, throttling…)    │        │       31 entiteiten    │
│  ├─ watchdog   (offline-pagina, herstel)   │        │                        │
│  └─ lokale web (127.0.0.1:8099)            │        │                        │
└────────────────────────────────────────────┘        └────────────────────────┘
```

- **cog** is een lichte browser (WPE WebKit). Mocht je dashboard daarin niet
  goed werken, zet dan `dashboard.browser: chromium`.
- Bij het opstarten toont het scherm het **Bdlsoft-logo met een laadbalk**,
  eerst als opstartscherm (Plymouth, draait mee met de stand uit de setup) en
  daarna in de browser tot het dashboard er is. Valt Home Assistant weg, dan
  verschijnt dezelfde pagina met een klok; die gaat vanzelf terug zodra HA
  bereikbaar is.
- Als het scherm donker is, **wekt de eerste tik alleen het scherm**. De tik gaat
  niet door naar het dashboard, zodat je niet per ongeluk een lamp schakelt.
- **Bewaking op drie niveaus:** systemd herstart de agent en de browser, de agent
  bewaakt de browser en Home Assistant, en de hardware-watchdog herstart de Pi
  als alles vastloopt.

## Setup-wizard

Bij de eerste start verschijnt een wizard op het scherm, met een eigen
schermtoetsenbord. Daarna is hij **altijd** weer te openen:

- **houd 3 vingers 4 seconden op het scherm.** Dit werkt altijd, ook met een
  eigen dashboard en ook als Home Assistant onbereikbaar is: de agent leest het
  rechtstreeks van het touchscherm;
- de knop **Setup openen** in Home Assistant;
- **⚙ Instellingen** op het offline-scherm en op de pagina *Scherm* van het
  standaard dashboard.

De wizard doorloopt:

1. **Taal**: Nederlands, English of Deutsch (setup, opstartscherm en
   standaard dashboard).
2. **Scherm**: naam, stand, stijl, dashboard (standaard of eigen) en browser.
3. **Netwerk**: status van de kabel (PoE), of kies een wifi-netwerk.
4. **Home Assistant**: adres + test. Kies dan *automatisch* of *handmatig*
   koppelen.
5. **Koppelen**:
   - *Automatisch*: eenmalige admin-login (MFA wordt ondersteund). Het
     admin-token blijft alleen in het geheugen en wordt na de setup vergeten.
   - *Handmatig*: zelf MQTT-gebruiker/wachtwoord (en eventueel een token)
     invullen.
6. **Gedrag**: timeout, eerste tik alleen wekken, helderheid dag/nacht,
   vaste uit-tijden, updates (modus, repo, eventueel token) en beveiligde modus.
7. **Beweging & meldingen**: laat Home Assistant het scherm aan/uit zetten
   met een bewegingssensor (bijvoorbeeld de PIR van het alarm) en kies een
   telefoon voor meldingen. Werkt bij beide koppelingen; bij *handmatig*
   vraagt deze stap om een eenmalige admin-login.
8. **Overzicht**: de wizard laat **precies zien wat er gaat gebeuren**. Pas na
   *Bevestigen* wordt het uitgevoerd.

Bij *automatisch* maakt de wizard in Home Assistant aan:
- een eigen gebruiker voor het scherm (groep Gebruikers, alleen lokaal, geen
  beheerder). De Mosquitto-add-on accepteert die gebruiker ook als
  MQTT-login;
- een token voor die gebruiker (voor het dashboard);
- optioneel de blueprints uit [`blueprints/`](blueprints) en de
  automatiseringen *meldingen* (offline of te warm → pushmelding) en
  *beweging* (PIR wekt het scherm, optioneel uit na X minuten zonder
  beweging).

Elke wijziging komt in `hasioscherm-wijzigingen.log` op de boot-partitie en in
het systeemlog. De automatiseringen hebben in hun omschrijving een notitie
*"Aangemaakt door de setup-wizard van hasioscherm op …"*.

## Dashboard

In de setup kies je wat het scherm toont.

### Standaard dashboard (meegeleverd)

Werkt direct na de setup, zonder iets in te stellen. Het draait op het scherm
zelf en haalt via de websocket van Home Assistant op wat er is:

- **Home:** grote klok en datum, het weer (eerste `weather`-entiteit), wie er
  thuis is, wat er nu aan staat (lampen, schakelaars, media) en een overzicht
  per ruimte (aantal lampen aan, temperatuur);
- **Ruimtes:** per ruimte (area in HA) tegels voor lampen, schakelaars,
  ventilatoren, rolluiken, klimaat (− / +), media (play/pauze, volume),
  temperatuur/vocht en deur/raam/beweging. Tik om te schakelen;
- **Scherm:** naam, versie, verbinding en een knop naar de setup.

Verborgen, uitgeschakelde en diagnose-/configuratie-entiteiten worden
overgeslagen. Licht of donker volgt de stijl uit de setup (bij *automatisch*
het dag/nacht-profiel). De token van het scherm haalt het dashboard bij de
agent op `127.0.0.1`; andere sites kunnen die niet uitlezen.

### Eigen dashboard

Vul een pad op Home Assistant in (bijvoorbeeld `/local/mijn/dash.html`) of een
volledig adres. Voor een pagina op Home Assistant zelf opent de agent
`…?kiosk=1&kt=<token van het scherm>`. Een dashboard dat die kioskmodus kent,
kan de token gebruiken om zonder inloggen te verbinden. In MQTT en in het log
staat de token als `kt=***`.

Voorbeeld: het Thuis-dashboard (Kinetic + eigen pagina's) op de branch
`bestaand-dashboard` heeft vanaf versie 1.6.0 zo'n kioskmodus. Die haalt de
token uit de adresbalk, bewaart hem alleen in de sessie, toont een
schermtoetsenbord en verbergt de muiscursor.

## Entiteiten in Home Assistant

Het apparaat verschijnt vanzelf via MQTT Discovery.

| Soort | Entiteiten |
|---|---|
| Bediening | Backlight (aan/uit + helderheid), Pagina (tekst/URL), Wekken, Terug naar dashboard, Pagina herladen, Screenshot maken |
| Instellingen | Timeout, Helderheid dag, Helderheid nacht, Dag/nacht-helderheid, Eerste tik alleen wekken, Beveiligde modus, Setup openen, Updates controleren, Fabrieksreset (2x drukken), Browser herstarten, Herstarten, Uitschakelen |
| Status | Laatste aanraking, In gebruik |
| Diagnose | Bestandssysteem, Updatecontrole, CPU-temperatuur, CPU-belasting, Geheugen, Schijf, Opgestart, IP-adres, Stroom/throttling, Ventilator, Te warm, Home Assistant bereikbaar |
| Overig | Screenshot (afbeelding), Software (update) |

### Pop-up vanuit een automatisering

Stuur het scherm naar een pagina. Het keert daarna vanzelf terug naar het
dashboard:

```yaml
action: mqtt.publish
data:
  topic: hasioscherm/scherm_woonkamer/popup
  payload: '{"url": "/local/deurbel.html", "seconden": 60}'
```

Een PIR-melder van het alarm kan het scherm wekken via de knop **Wekken**
(`button.press` op `button.scherm_woonkamer_wekken`).

## Hardware

- Raspberry Pi 5 (4 GB is genoeg)
- Raspberry Pi Touch Display 2, 10" (1200×1920, liggend gebruikt)
- USB-SSD voor het besturingssysteem. De Pi 5 start daar vanzelf van op als er
  geen SD-kaart in zit.
- PoE+-HAT voor de Pi 5, bijvoorbeeld Waveshare PoE+ HAT (J) of (H).
  **Let op:** het scherm krijgt zijn stroom via de 5V/GND-pinnen van de
  GPIO-header. Kies dus een HAT met doorgevoerde 40-pins header (of gebruik de
  meegeleverde riser), zodat de voedingskabel van het scherm erop past.

## Installeren

### Optie A: kant-en-klare image (aanbevolen)

1. Download `hasioscherm-*.img.xz` bij de laatste
   [release](https://github.com/basdelouw78/hasioscherm/releases).
2. Flash de image met Raspberry Pi Imager op de USB-SSD
   (*Use custom*; sla de aanpassingsvragen over).
3. Sluit de SSD aan op de Pi en start op. De setup-wizard verschijnt vanzelf.
   (Je kunt `hasioscherm.yaml` op de boot-partitie ook vooraf met de hand
   invullen; zet dan `setup: {klaar: true}`.)

De image wordt gebouwd door GitHub Actions (workflow *Image bouwen*) zodra het
versienummer verandert. Je kunt de workflow ook met de hand starten.

### Optie B: op een bestaande Raspberry Pi OS Lite (64-bit)

```bash
git clone https://github.com/basdelouw78/hasioscherm
cd hasioscherm
sudo ./install.sh
sudo reboot      # daarna verschijnt de setup-wizard op het scherm
```

## Configuratie

Alle instellingen per scherm staan in `/boot/firmware/hasioscherm.yaml`. Zie
[`config/hasioscherm.example.yaml`](config/hasioscherm.example.yaml). Wat je
vanuit HA wijzigt (timeout, helderheid enzovoort) wordt bewaard in
`/boot/firmware/hasioscherm-state.json`. Elke wijziging komt in het logboek
(`journalctl -u hasioscherm-agent`), zodat er een spoor is.

## Beveiligde modus (alleen-lezen)

Na de setup zet het scherm zichzelf in **beveiligde modus**, met één herstart.
Het systeem is dan alleen-lezen: alles wat tijdens het draaien verandert, staat
alleen in het werkgeheugen. Stroomuitval of een herstart via PoE kan de SSD dus
niet beschadigen. De boot-partitie blijft schrijfbaar; daar staan de config, de
instellingen en het wijzigingslog.

- In Home Assistant: schakelaar **Beveiligde modus** en sensor **Bestandssysteem**.
  Omschakelen kost één herstart en komt in het wijzigingslog.
- Gebruikt de standaardfunctie van Raspberry Pi OS (`raspi-config` overlayfs).
- Lukt het aanzetten twee keer niet, dan blijft het scherm schrijfbaar en meldt
  de sensor *aanzetten mislukt*. Zo komt het scherm nooit in een herstartlus.

## Instellingen later aanpassen

Is de setup al eens doorlopen, dan opent de setup (3 vingers 4 s, knop
**Setup openen** in HA of ⚙ op het offline-scherm) met een menu:
**Scherm & dashboard**, **Gedrag**, **Beweging & meldingen**, **Netwerk** en
**Taal**. Kies een onderdeel, pas aan en druk op **Opslaan**; je ziet eerst wat
er verandert. De koppeling met Home Assistant en MQTT blijft dan staan (geen
nieuwe gebruiker of token). Wil je toch alles opnieuw, kies dan **Volledige
setup**.

## Fabrieksreset

Zet het scherm terug naar de toestand van een vers geflasht image: alle
instellingen, de toestand en opgeslagen wifi-netwerken worden gewist, het
apparaat verdwijnt uit Home Assistant en de setup begint opnieuw.

- Op het scherm: in de setup linksonder **Fabrieksreset**, met bevestiging.
- Vanuit Home Assistant: knop **Fabrieksreset (2x drukken)**. De eerste druk
  toont de bevestiging op het scherm; een tweede druk binnen 60 seconden voert
  de reset uit.

Wat de setup in Home Assistant heeft aangemaakt (gebruiker, token,
automatiseringen, blueprints) blijft bestaan; het wijzigingslog op de
boot-partitie noemt die onderdelen, zodat je ze zelf kunt opruimen. In
beveiligde modus gaat de reset in twee stappen met een herstart ertussen.

## Updates

Het scherm controleert bij GitHub op een nieuwere release: kort na het
opstarten, na elke (her)verbinding met MQTT en daarna elke 6 uur (mislukt het,
dan na 10 minuten opnieuw). Met de knop **Updates controleren** gebeurt het
meteen. De entiteit **Software** toont of er een nieuwere release is. Druk je op
*Installeren*, dan haalt het scherm die tag op uit deze repo en installeert
die. In beveiligde modus gaat dat in stappen, die elk een spoor achterlaten in
het wijzigingslog:

1. de beveiliging gaat tijdelijk uit, gevolgd door een herstart;
2. de update wordt geïnstalleerd;
3. de beveiliging gaat weer aan, gevolgd door nog een herstart. Dat gebeurt ook
   als de update mislukt; dan blijft de oude versie staan.

Met `updates.modus: automatisch` gebeurt dit elke nacht één keer tussen 03:00 en 05:00.

**Een bepaalde versie** (ook een oudere, om terug te gaan): publiceer het
versienummer, bijvoorbeeld `0.6.2`, op `hasioscherm/<id>/update/set`
(Home Assistant → MQTT → Configureren → Publiceer). Het scherm controleert of
die release bestaat en installeert hem dan op dezelfde manier.

**Privé-repo?** Vul dan in de setup een GitHub-token in: een fine-grained token
met alleen *Contents: read* op de repo. De updatecontrole en `update.sh`
gebruiken die token; hij komt nooit in het log of in MQTT. De sensor
**Updatecontrole** laat zien of de controle lukt (bijvoorbeeld *geen toegang:
privé-repo? vul een token in bij de setup*), in plaats van stil "geen update"
te melden.

Een nieuwe versie uitbrengen: verhoog het versienummer in
`agent/hasioscherm/__init__.py` en push naar `main`. De workflow *Image bouwen*
maakt dan de image, de tag en de release.

## Fasering

| Fase | Inhoud | Status |
|---|---|---|
| 1 | Image, kiosk, MQTT-agent (backlight, touch-wake, status, pop-ups, watchdog) | ✅ gebouwd, nog testen op hardware |
| 2 | Setup-wizard op het scherm (naam, netwerk, HA, MQTT, stijl, tijden), HA-kant via eenmalige admin-login, blueprints voor meldingen en beweging | ✅ gebouwd, nog testen op hardware |
| 3 | Standaard dashboard (meegeleverd) + eigen dashboard met kioskmodus (token, schermtoetsenbord, geen cursor) | ✅ gebouwd, nog testen op hardware |
| 4 | Inbouwbak voor de stenen muur: glas op het stucwerk, alleen het glas zichtbaar (STL, PLA, bed 220 mm) + uitslijpmal, zie [`frame/`](frame) | ✅ ontworpen, nog meten, printen en passen |
| 5 | Beveiligde modus (alleen-lezen) en updates via HA, ook in beveiligde modus | ✅ gebouwd, nog testen op hardware |
| 6 | Generiek: alles via de setup, standaard dashboard, updates ook bij een privé-repo (token) | ✅ gebouwd, nog testen op hardware |

## Ontwikkelen

```bash
# integratietests (vereist mosquitto; de wizardtest ook playwright + chrome)
mosquitto -p 18830 -d
python3 tests/test_agent_mqtt.py
python3 tests/test_alleen_lezen.py
python3 tests/test_dashboard.py /tmp/screenshots
python3 tests/test_wizard.py /tmp/screenshots
```
