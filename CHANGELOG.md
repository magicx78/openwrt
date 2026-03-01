# Changelog

Alle nennenswerten Änderungen werden hier dokumentiert.
Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

---

## [0.4.8] – 2026-03-01

### Behoben / Verbessert

- **Form-Data-Fallback entfernt**: `--post-data "base_mac=..."` als Fallback entfernt.
  Kein Content-Type-Kompromiss: falsches Format → stilles 422. Nicht akzeptabel.

- **curl als sauberer Fallback** (kein Form-Data): Wenn `wget --header` nicht verfuegbar:
  - `command -v curl` → JSON via `curl -X POST -H 'Content-Type: application/json'`
  - Sonst: `echo "FAIL: Kein JSON-POST moeglich ..." && exit 1`

- **`CLAIM_RC` als Variable + sofortiger `exit 1`**: `CLAIM_RC=$?` gespeichert,
  bei Fehler bricht das Script sofort ab – kein Config-Download ohne Claim.

- **Config-Download konsistent**: Nutzt `HTTP_CLIENT` (wget oder curl) konsequent
  fuer beide Requests. `CFG_RC=$?` und `SIZE=$(...)` als Variablen vor dem echo.

- **`HTTP_CLIENT` einmalig bestimmt**: `wget --help`-Check laeuft einmal am Anfang,
  nicht separat fuer Claim und Config.

---

## [0.4.7] – 2026-03-01

### Behoben / Verbessert

- **BusyBox-Kompatibilitätscheck für `--header`** (neu):
  Das Script prüft jetzt vor dem Claim, ob `wget --header` unterstützt wird:
  ```sh
  if wget --help 2>&1 | grep -q -- '--header'; then
  ```
  - **Wenn ja**: JSON-Claim mit `--header='Content-Type: application/json'` (wie bisher)
  - **Wenn nein** (minimales BusyBox ohne `--header`): Form-Data-Fallback:
    `--post-data "base_mac=$MAC&board_name=$BOARD&model=$MODEL&token=$TOKEN"`
    Der Server akzeptiert beide Formate seit v0.4.3.

- **`-q` entfernt** von Claim- und Config-wget: HTTP-Fehler (z. B. 409, 403, Verbindungsfehler)
  sind jetzt direkt im SSH-Installer-Log sichtbar. Kein stilles Verschlucken mehr.

- **`BATCH_RC` und `COMMIT_RC` explizit geprüft**:
  `uci batch` und `uci commit` werden einzeln ausgewertet. Bei Fehler:
  - `exit 1` → Script-Job im Dashboard als FAIL markiert
  - kein `touch /etc/provisioned` → nächster Boot versucht erneut
  Nur wenn beide RCs `0` sind, wird `/etc/provisioned` gesetzt.

- **`CFG_RC` und `CFG_SIZE` als separate Zeilen**:
  ```sh
  echo "CFG_RC:$?"
  echo "CFG_SIZE:$(wc -c < /tmp/provision.uci 2>/dev/null || echo 0)"
  ```

---

## [0.4.6] – 2026-03-01

### Behoben

- **`/api/config/{mac}` – HTTP-Status 404 → 409**: Gerät nicht geclaimt lieferte bisher
  404 (Not Found). Nun 409 (Conflict) – semantisch korrekt: Gerät ist bekannt aber noch
  nicht registriert. Body bleibt `{"error": "device_not_claimed", "mac": "...", "hint": "..."}`.

- **`provision.conf` TOKEN in Single-Quotes**: `TOKEN='<wert>'` verhindert
  Shell-Expansion von Sonderzeichen (z. B. `$`) beim Sourcen der Datei.
  Gilt für: `/download/provision.conf` (Download), Setup-UI HTML-Vorschau,
  und `/provision.sh` Legacy-Endpoint.

- **Config-wget ohne `2>/dev/null`**: Der wget-Aufruf für `/api/config` gibt nun
  HTTP-Fehler direkt aus (z. B. „409 Conflict"), statt sie stillschweigend zu
  verschlucken. Erleichtert Debugging im SSH-Installer-Log erheblich.

---

## [0.4.5] – 2026-03-01

### Behoben

- **`_generate_provision_sh()` – drei kritische Bugs im generierten Script**:

  1. **BusyBox `--header`-Syntax**: BusyBox wget erfordert `--header='KEY: VALUE'`
     (Gleichzeichen-Syntax), nicht `--header "KEY: VALUE"` (Leerzeichen). Das alte
     Format wurde von BusyBox stillschweigend ignoriert → JSON-Body ohne Content-Type
     → FastAPI parsete ihn als Form-Data → Claim-Fehler.
     **Fix**: `--header='Content-Type: application/json'` (korrekte BusyBox-Syntax).

  2. **`CLAIM_JSON` als separate Variable**: Statt Inline-Post-Data mit Escape-Hölle
     wird der JSON-Body jetzt als Variable gebaut und per `--post-data "$CLAIM_JSON"`
     übergeben. Saubererer Code, einfacher zu debuggen.

  3. **Selbstsabotage: `touch /etc/provisioned` im Fehlerfall**: Das Script setzte
     `/etc/provisioned` auch dann, wenn **keine Config gefunden** wurde. Beim nächsten
     Boot übersprang das Script dann komplett mit „Bereits provisioned – skip" – das
     Gerät blieb für immer ohne Config.
     **Fix**: `touch /etc/provisioned` **nur** bei erfolgreich angewendeter UCI-Config.
     Im Fehlerfall: `exit 1` (kein provisioned-Flag) → Script läuft beim nächsten Boot erneut.

  4. **TOKEN in Single-Quotes**: `TOKEN='{token}'` verhindert Shell-Interpretation von
     Sonderzeichen (z. B. `$`) im Token-Wert.

  5. **Diagnose-Output**: Neue Ausgaben `CLAIM_RC:$?`, `CFG_RC:$? SIZE:...` und
     `head -n 20 /tmp/claim.json` für einfacheres Debugging im SSH-Installer-Log.

  6. **`HOSTNAME`-Variable entfernt**: War nie Teil des Claim-Requests (API ignoriert es),
     wurde nur in einem `echo` genutzt.

---

## [0.4.4] – 2026-03-01

### Behoben

- **`_generate_provision_sh()` – wget-Claim sendet falsches Format**:
  Das generierte `99-provision.sh` schickte Form-Data mit Feldname `mac=...` statt
  JSON mit `base_mac`. Obwohl v0.4.3 den Server robust gemacht hat (beides akzeptiert),
  war das Script selbst weiterhin semantisch falsch.

  **Fix**: wget-Claim-Aufruf auf JSON umgestellt:
  - `--header "Content-Type: application/json"` hinzugefügt
  - Feldname `base_mac` (korrekt, passend zu `/api/claim`)
  - `hostname` aus dem Body entfernt (war nie in ClaimReq/API verarbeitet)
  - Erzeugt: `{"base_mac":"aa-bb-cc-dd-ee-ff","board_name":"...","model":"...","token":"..."}`

  Der Server (v0.4.3) akzeptiert weiterhin beide Formate als Rückwärtskompatibilität
  für bereits deployed Router mit dem alten Script.

---

## [0.4.3] – 2026-03-01

### Behoben

- **Claim schlägt still fehl – Router erscheint nie im Dashboard** (`WARN Claim fehlgeschlagen – weiter`):
  Doppelter Bug in `/api/claim`:
  - **Field-Name-Mismatch**: BusyBox-wget sendet `mac=...`, API erwartete `base_mac` (422).
  - **Content-Type-Mismatch**: BusyBox-wget sendet `application/x-www-form-urlencoded`,
    Pydantic-Body erwartet `application/json` (422).

  **Fix (server-seitig, robuster)**: `/api/claim` akzeptiert jetzt **beide Formate**:
  - `application/json`: `base_mac` **und** `mac` (Alias) werden akzeptiert.
  - `application/x-www-form-urlencoded`: `mac` oder `base_mac`, form-encoded (BusyBox-wget).

  MAC wird normalisiert: `aa:bb:cc:dd:ee:ff` → `aa-bb-cc-dd-ee-ff`.
  Response vereinfacht: `{"status": "claimed", "mac": "...", "hostname": "...", ...}`.
  Provision-Script (`_generate_provision_sh`) bleibt **unverändert** – kein Router-Update nötig.

- **`/api/config/{mac}` – 404 schwer zu debuggen**:
  Bei nicht gefundenem Gerät jetzt JSON-Response statt Plain-Text-404:
  `{"error": "device_not_claimed", "mac": "...", "hint": "..."}`

### Hinzugefügt

- **Setup-Assistent: „🖥️ Server-URL"-Feld** im SSH-Schnellinstaller.
  Frischer Router (192.168.1.1) hat keine Route ins Admin-Netz (192.168.10.x).
  Admin kann jetzt die URL angeben, die der Router tatsächlich erreichen kann
  (z.B. `http://192.168.1.100:8000` wenn PC im 192.168.1.x-Netz ist).
  Default: aktuelle Admin-URL aus `request.base_url`. Wird als `server_url` an
  `/api/setup/quick-ssh` übergeben → in Provision-Script eingebettet.

- **Setup-Assistent: „📦 Benötigte Image-Pakete"-Card** mit Copy-Button.
  Erforderliche OpenWrt Image-Builder-Pakete:
  `wpad-wolfssl kmod-batman-adv batctl-full openssh-sftp-server -wpad-basic-mbedtls`
  Hinweis: `-wpad-basic-mbedtls` entfernen (Konflikt mit wpad-wolfssl).

---

## [0.4.2] – 2026-02-28

### Behoben

- **Download-Buttons im Setup-Assistenten funktionierten nicht** (Browser zeigte Inhalt
  inline statt Download zu starten): Alle 3 Endpoints geben jetzt `Content-Disposition: attachment`
  zurück → Browser startet immer einen Download-Dialog.
  Betrifft: `GET /download/99-provision.sh`, `GET /download/provision.conf`,
  `GET /download/start.bat`.

- **`provision.conf`-Endpoint benötigte Query-Params** (`?server=...&token=...`):
  Endpoint ermittelt Server-URL jetzt automatisch aus `request.base_url` (wie `99-provision.sh`).
  Query-Param `?server=` bleibt als optionaler Override erhalten (Rückwärtskompatibilität).

### Verbessert

- **Setup-Assistent „Schritt 1"**: `provision.conf`-Inhalt mit echten Werten (Server-URL + Token)
  direkt auf der Seite angezeigt. „📋 Kopieren"-Button und „⬇️ Download"-Link inline.
  Kein manuelles Notepad mehr nötig – einfach kopieren und auf den Router übertragen.

- **Download-Card**: Alle 3 Download-Links sauber aufgelistet mit Kurzbeschreibungen.
  `provision.conf`-Link braucht keine Query-Params mehr.

---

## [0.4.1] – 2026-02-28

### Behoben

- **„Router direkt provisionieren" schlägt fehl** (`provision script not found`):
  `99-provision.sh` wurde als statische Datei erwartet – existierte nie.
  Fix: `_generate_provision_sh()` generiert das Bootstrap-Script jetzt **dynamisch**
  mit Server-URL und Enrollment-Token eingebettet.
  - `GET /download/99-provision.sh` liefert jetzt ein vollständiges Shell-Script
  - `POST /api/setup/quick-ssh` nutzt dasselbe generierte Script (kein Dateisystem-Zugriff mehr)

- **`GET /api/config/{mac}` fehlte** (war in CONTINUE_PROMPT dokumentiert, aber nicht implementiert):
  Neuer Endpoint – Router ruft ihn nach dem Claim auf um seine UCI-Config herunterzuladen.
  Auth via `?token=ENROLLMENT_TOKEN`. Rendert Projekt-Template mit `build_vars()` + `render_template()`.

### Hinzugefügt

- `_generate_provision_sh(server_url, token)` – DRY-Hilfsfunktion für Bootstrap-Script-Generierung
- `GET /api/config/{mac}?token=...` – UCI-Config-Endpoint für enrolled Geräte

---

## [0.4.0] – 2026-02-28

### Hinzugefügt

- **F1 – Config-Push UI** (`/ui/config-push`): Gegenstück zu Config-Pull.
  Workflow: Projekt wählen → UCI-Config im Browser rendern (editierbar) →
  per SSH direkt auf einen Router pushen. Nutzt vorhandene `build_vars()` +
  `render_template()` + `/api/direct-push`-Infrastruktur.
  - Optionale Geräteauswahl: Hostname + IP werden automatisch befüllt
  - Alle Proj.-Variablen inkl. neuer Netzwerk-Variablen sichtbar
  - Neuer Navbar-Link `📤 Config-Push`

- **F2 – Netzwerk-Editor → Template-Rendering** (`build_vars()`):
  `settings["networks"]`-Daten werden jetzt als Template-Variablen eingespeist:
  - `{{NET_{NAME}_IP}}`, `{{NET_{NAME}_VLAN}}`, `{{NET_{NAME}_PROTO}}`,
    `{{NET_{NAME}_MASK}}`, `{{NET_{NAME}_GW}}` pro Interface
  - `{{NETWORKS_BLOCK}}` → UCI `set network.*`-Befehle für alle statischen Interfaces
  - `X` in IP-Adressen wird durch berechneten `MGMT_SUFFIX` ersetzt
  - Beispiele: `{{NET_LAN_IP}}`, `{{NET_MEDIA_VLAN}}`, `{{NET_GUEST_IP}}`
  - Template-Kopf in `_MASTER_TEMPLATE` dokumentiert neue Variablen

- **F3 – VLAN-Dropdown „Andere…" Handler** (Projekt-Editor):
  - Neue JS-Funktion `onVlanChange()` zeigt/versteckt Freitext-Input
  - `renderVlanSelect()` gibt jetzt `<select>` + `<input>` zurück
  - Form-Submit-Handler ersetzt `__custom__`-Wert durch eingegebenen Namen
  - Custom-VLAN-Namen werden korrekt gespeichert und beim Reload angezeigt

- **F4 – SSH-Key-Verwaltung** (Settings-UI + Backend):
  - Einstellungen-Seite: Neuer Abschnitt „🗝️ SSH-Schlüssel-Verwaltung"
    - Private Key einfügen (RSA/Ed25519/ECDSA) + Fingerprint-Anzeige
    - „📤 Public Key auf Router installieren" – verbindet per Passwort, schreibt
      Public Key in `~/.ssh/authorized_keys`
  - Leeres Passwort-Feld in SSH-Formularen → gespeicherter Key wird genutzt
  - Neuer Namedtuple `_ParamikoKeyAuth(ip, user, key_content)`
  - Neue Funktion `_ssh_exec_paramiko_key()` – RSA → Ed25519 → ECDSA Fallback
  - `_build_base_ssh()`: Neuer Parameter `key_content=`, Key-Auth hat höchste Prio
  - Neue API-Endpoints:
    - `POST /api/settings/ssh-key` – Key speichern/löschen
    - `GET /api/settings/ssh-key/status` – Konfigurationsstatus
    - `POST /api/settings/ssh-key/install` – Key auf Router installieren

- **F5 – Neue API-Endpoints**:
  - `GET /api/projects` – Alle Projekte als JSON
  - `POST /api/config-push/preview` – UCI-Config aus Projekt rendern

### Technische Details

- `_get_saved_ssh_key()` – Hilfsfunktion liest `SSH_PRIVKEY` aus DB
- `SSH_PRIVKEY`-Eintrag in `init_db()`-Defaults ergänzt
- Alle `_build_base_ssh()`-Aufrufstellen übergeben jetzt `key_content=_get_saved_ssh_key()`

---

## [0.3.0] – 2026-02-28

### Hinzugefügt
- **F1 – VLAN/Netz als Dropdown** im Projekt-Editor: Das VLAN-Feld in der
  WLAN-Konfiguration ist jetzt ein `<select>`-Dropdown (wie z.B. SSID).
  Die verfügbaren Optionen werden aus `settings["networks"]` des Projekts
  gelesen; Fallback auf Standardliste `["lan", "Media", "Worls", "Guest"]`.
  Neue WLANs (per JS hinzugefügt) nutzen dasselbe Dropdown via `renderVlanSelect()`.
- **F2 – Netzwerk-Config-Editor**: Neuer Tab „🌐 Netzwerk-Interfaces" im Projekt-Editor.
  Zeigt alle konfigurierten Interfaces (Name, Protokoll, IP, Netmask, Gateway, VLAN-ID)
  als editierbare Tabelle. Interfaces können hinzugefügt und entfernt werden.
  Gespeichert als `settings["networks"]` in der Projekt-DB. Speist F1-Dropdown.
- **F3 – Geräte-Discovery**: Neuer Menüpunkt „🔍 Discovery" in der Navigation.
  Seite `/ui/discover` erlaubt Netzwerk-Scan nach erreichbaren Hosts.
  Neuer API-Endpoint `POST /api/discover` mit `{subnet, timeout}`-Parameter.
  Erkennt SSH (Port 22), HTTP (Port 80) und OpenWrt LuCI automatisch.
  Scan läuft parallel via `asyncio.gather()` – keine zusätzlichen Abhängigkeiten.
- **F4 – `{{WLAN_BLOCK}}` im Master-Template aktiviert**: Die hardcodierten
  `wlan0`/`wlan1`-Stanzas wurden durch `{{WLAN_BLOCK}}` ersetzt. Das Master-Template
  wird jetzt genau wie das Private-Template vollständig aus den Projekt-WLANs generiert.
  Variable auch im Kopfkommentar des Templates dokumentiert.
- **F7 – paramiko in requirements.txt**: `paramiko>=3.4.0` ergänzt.
  War bereits im Code genutzt, fehlte aber als deklarierte Abhängigkeit.

### Geändert
- **F6 – Script-Push-Methode korrigiert**: Im Config-Pull-UI ruft der „Script"-Modus
  jetzt korrekt `/api/deploy/{mac}/ssh-push` auf (MAC-basiert) statt `/api/direct-push`.
  Fallback auf `/api/direct-push` bleibt erhalten wenn keine gültige MAC bekannt ist.

### Dokumentiert (Breaking Change)
- **F5 – `network.Worls` (historischer Tippfehler)**: Der UCI-Schnittstellenname
  „Worls" (statt „Works") im Private-Template ist ein historischer Tippfehler.
  **Eine Umbenennung würde alle bereits provisionierten Geräte brechen** (UCI-Name
  ist im Flash gespeichert). Bleibt absichtlich erhalten.
  - Erweiterter Kommentar im `_PRIVATE_TEMPLATE` (Zeile ~363)
  - UI-Warnung im Template-Editor wenn Template `network.Worls` enthält

---

## [0.1.0] – 2026-02-28

### Hinzugefügt
- `__version__ = "0.1.0"` in `server.py`
- Helper `now_utc()` – liefert immer `datetime.now(timezone.utc)`
- Helper `parse_dt_utc(s)` – parst ISO-8601-Strings zu UTC-aware datetimes;
  naive Strings (Legacy-DB-Einträge ohne Timezone-Info) werden als UTC interpretiert
- Optionaler SSH-Precheck-Modus vor Deploy (`precheck: bool`-Flag in API + UI-Checkbox):
  - 7 read-only Checks (uname, os-release, busybox, id, uci, df, ip)
  - Fail bei SSH-Timeout/Exception oder ≥3× exit 127
  - Warn (kein Fail) bei fehlendem OpenWrt oder fehlendem uci
  - Ausgaben auf 8 KB/Command + 300 Zeichen/Log-Zeile begrenzt
  - Kein Passwort im Log
- `_ssh_exec()` – zentraler SSH-Runner (kein doppelter subprocess-Code)
- `_build_base_ssh()` – SSH-Befehlsprefix-Builder (sshpass/key-auth)
- `validate_template()` bugfix: `""` in `valid_cmds` führte dazu, dass
  ungültige Befehle nie erkannt wurden
- Vollständige pytest-Testsuite (`tests/`): 136 Tests

### Geändert
- Alle `datetime.utcnow()` ersetzt durch `now_utc()` (9 Stellen)
- `time.strftime("%Y-%m-%dT%H:%M:%S")` im SSH-Job DB-Update ersetzt durch
  `now_utc().isoformat()` (war lokale Zeit ohne Timezone-Info)
- Dashboard-Zeitberechnung: `datetime.fromisoformat` + `datetime.utcnow()`
  ersetzt durch `parse_dt_utc` + `now_utc()` (aware/naive-Mix behoben)
- DB-Timestamps haben jetzt immer `+00:00`-Offset (ISO 8601 mit Offset)

### Migration
- **Alte DB-Einträge** (`provision.db`) ohne Timezone-Info in `last_seen` /
  `updated_at` werden beim Einlesen automatisch als UTC interpretiert
  (`parse_dt_utc` → `replace(tzinfo=timezone.utc)`). Kein manuelles DB-Update nötig.

---

## [0.1.3] – 2026-02-28

### Hinzugefügt
- **Precheck-only Modus** (`precheck_only: true` in API + UI-Checkbox):
  - Führt SSH-Verbindung + `_run_precheck()` aus, dann sofortiger Stopp
  - Kein Upload, kein Script-Exec, kein DB-Status-Update (niemals `provisioned`)
  - Job endet mit `success=true` + Log: `"Precheck-only: beendet ohne Änderungen am Gerät"`
  - UI zeigt differenzierte Erfolgsmeldung: `"Precheck erfolgreich – keine Änderungen"`
  - Verfügbar auf `/ui/deploy/{mac}/ssh` und `/ui/setup`

### Behoben
- **Deploy meldet Erfolg trotz Fehler-Output** (`"provision script not found"` bei Exit 0):
  - Neues `_DEPLOY_FATAL_PATTERNS`-Array – bekannte Fehlerstrings werden geprüft:
    `not found`, `No such file`, `Permission denied`, `uci: Usage:`, `ash: can't open`
  - Non-zero Exitcode in Schritt 3 (Exec) → `RuntimeError` → `job.success = False`
  - `"Provisioning abgeschlossen"` wird NICHT geloggt wenn Fehler erkannt
- **Precheck uci-Command**: `uci -V` ersetzt durch `uci --help 2>&1 | head -n 1`
  (`uci -V` ist kein valider Read-only-Befehl auf allen OpenWrt-Versionen)
- **Precheck busybox-Command**: `busybox | head -n 1 || true` ersetzt durch
  `busybox --help 2>&1 | head -n 1 || true` (stderr-Umleitung, robuster)

### Tests
- 159 Tests (+23 neu): `TestPrecheckOnly` (5), `TestDeployFatalOutput` (8),
  `TestParamikoExitStatus` (3), `TestPrecheckCommands` (5), `TestPrecheckApiEndpoint` +2

---

## [0.1.2] – 2026-02-28

### Behoben
- **Windows SSH-Precheck Timeout** (`❌ Precheck Timeout bei 'uname'`): Ohne `sshpass`
  versuchte der Code SSH über einen subprocess mit Key-Auth. Auf Windows ohne
  hinterlegten SSH-Key hängt der subprocess und wartet auf interaktive Passwort-Eingabe
  → Timeout nach 12 s. Behoben durch optionale `paramiko`-Integration:
  - Neues Modul-Flag `_HAS_PARAMIKO` – gesetzt wenn `pip install paramiko` vorhanden
  - Neue Funktion `_ssh_exec_paramiko()` – SSH + Passwort-Auth ohne sshpass,
    plattformübergreifend (Windows, Linux, macOS)
  - `_build_base_ssh()` wählt jetzt: sshpass → paramiko → SSH-Key-Auth (subprocess)
  - `_ssh_exec()` dispatcht transparent zu subprocess oder paramiko
  - `paramiko.AutoAddPolicy()` – kein manuelles Host-Key-Management nötig
  - Timeout in paramiko wird als `subprocess.TimeoutExpired` weitergereicht
    (einheitliche Exception-Behandlung in `_run_precheck` und `_ssh_push_job`)

### Hinzugefügt
- Abhängigkeit: `paramiko` (optional – bei fehlender Installation weiterhin key-auth)

---

## [0.1.1] – 2026-02-28

### Behoben
- **Windows-Crash im SSH-Installer** (`[WinError 2] Das System kann die angegebene
  Datei nicht finden`): `subprocess.run(["which", "sshpass"])` schlägt auf Windows
  fehl, weil `which` kein Windows-Befehl ist. Ersetzt durch `shutil.which("sshpass")`
  (Python-Stdlib, plattformübergreifend). Auf Windows ohne sshpass greift der
  Installer korrekt auf SSH-Key-Auth zurück.

---

## [0.1.0] – 2026-02-28

---

## [0.1.3-fix1] – 2026-02-28

### Behoben (Fehleranalyse & Fixes nach Code-Review)

- **`__version__`**: War `0.1.4` im Code, aber `0.1.3` im CHANGELOG → auf `0.1.3` zurückgesetzt
- **UTC-Timestamps in Loglines**: Alle `time.strftime('%H:%M:%S')` ersetzt durch `_ts()` (neue Hilfsfunktion),
  die `datetime.now(timezone.utc).strftime(...)` verwendet. Log-Timestamps waren bisher **lokale Zeit** statt UTC –
  inkonsistent mit den DB-Timestamps aus `now_utc()`.
- **`_DEPLOY_FATAL_PATTERNS` False-Positives** (kritischer Bug):
  - `"not found"` war zu weit gefasst – OpenWrt-`uci commit` gibt bei nicht-existenten Pfaden
    `"commit: Not found ..."` aus, auch bei erfolgreichem Provisioning → False-Positive, Deploy als Fehler markiert
  - Gefixt: `"not found"` → `": not found"` (ash-Syntax für fehlende Commands/Scripts)
  - Neu: `"provision script not found"` explizit ergänzt (Fallback-Script-Platzhalter)
- **`_run_precheck` Typ-Annotation**: `base_ssh: list` war falsch – Funktion akzeptiert
  auch `_ParamikoAuth` (namedtuple). Korrigiert zu `base_ssh` ohne Typ-Annotation.
- **Template-Kommentar**: Hinweis zu `network.Worls` (historischer Tippfehler, konsistent im Template –
  Änderung würde bestehende Geräte brechen) dokumentiert.

### Technische Details

Neue Hilfsfunktion `_ts()` in `server.py`:
```python
def _ts() -> str:
    """Aktueller UTC-Zeitstempel für Loglines (HH:MM:SS UTC)."""
    return datetime.now(timezone.utc).strftime("%H:%M:%S")
```

Überarbeitete `_DEPLOY_FATAL_PATTERNS`:
```python
_DEPLOY_FATAL_PATTERNS = [
    ": not found",       # ash: <cmd>: not found (zu spezifisch statt "not found")
    "No such file",
    "Permission denied",
    "uci: Usage:",
    "ash: can't open",
    "provision script not found",
]
```

---

## [0.2.0] – 2026-02-28

### Hinzugefügt – Config Pull → Bearbeiten → Direct Push

#### 🆕 Neue UI-Seite: `/ui/config-pull`

Kompletter 5-Schritt-Workflow in einer Seite:

**① Config ziehen** – SSH-Verbindung zum Hauptrouter, `uci export wireless` + `uci export network` lesen.
Pull-Methode wählbar mit Beschreibung:
- **`uci export`** (empfohlen) – vollständiges Sections-Format, inkl. Listenfelder
- **`uci show`** – flaches Format, wird server-seitig via `_uci_show_to_export()` konvertiert
- Nur Lesezugriff – kein `uci set` auf dem Quell-Router

**② WLAN-Editor** – Tab-basiert (ein Tab pro wifi-iface):
- SSID, Passwort, Verschlüsselung (6 Optionen mit Erklärung)
- **Netz/VLAN** – Dropdown aus allen UCI-Interfaces des Quell-Routers (lan, Guest, Worls …) + Freitext
- 802.11r Roaming mit Mobility-Domain + NAS-ID Inline-Felder
- 802.11k (RRM) + 802.11v (BTM) getrennt wählbar
- MFP/ieee80211w: 0=Aus / 1=Optional / 2=Pflicht
- WDS (Bridge-Modus), WLAN aktiv/deaktiviert
- Tabs aktualisieren sich live (Farbe + Label)

**③ Als Projekt / Template speichern** (optional):
- Projekt: WLANs + Netz-Infos → neues Projekt im Server (Rückwärtskompatibel: erstes WLAN als `SSID`/`WPA_PSK`)
- Template: UCI-Template mit `{{ENABLE_11R}}` / `{{MOBILITY_DOMAIN}}` Variablen

**④ UCI-Vorschau** – zeigt genau die Befehle die gepusht werden

**⑤ Batch-Push auf N Client-Router parallel**:
- Push-Methode wählbar: **UCI direct** (`uci batch`) oder **Script** (99-provision.sh)
- Optionen: `uci commit wireless` + `wifi reload` (kein Reboot) | `reboot`
- Live-Log + Zeitanzeige pro Router, alle parallel

#### 🆕 Neue Backend-Funktionen

| Funktion | Beschreibung |
|---|---|
| `_parse_uci_export(raw)` | UCI export → Dict {section → {_type, _opt, _list}} |
| `_uci_show_to_export(raw)` | uci show Flat-Format → Sections-Format |
| `_extract_wlans(parsed)` | wifi-iface → WLAN-Dict-Liste |
| `_extract_radios(parsed)` | wifi-device → Radio-Liste |
| `_extract_networks(parsed)` | UCI-Interfaces → Netz-Dict für VLAN-Dropdown |
| `_wlans_to_uci_set(wlans)` | WLANs → UCI set-Befehle |
| `_wlans_to_uci_template(wlans)` | WLANs → UCI-Template mit {{VAR}} |
| `_ssh_pull_job(...)` | Thread: Pull vom Quell-Router |
| `_direct_push_job(...)` | Thread: UCI-batch + commit + reload/reboot |

#### 🆕 Neue API-Endpunkte

| Endpunkt | Methode | Beschreibung |
|---|---|---|
| `/api/devices` | GET | Alle Geräte als JSON (für "Aus Geräteliste laden") |
| `/api/config-pull` | POST | Pull-Job starten |
| `/api/config-pull/{id}` | GET | Pull-Status + Ergebnis |
| `/api/config-pull/{id}/raw/{subsystem}` | GET | Roh-UCI-Output |
| `/api/config-pull/{id}/save-project` | POST | Als Projekt speichern |
| `/api/config-pull/{id}/save-template` | POST | Als Template speichern |
| `/api/direct-push` | POST | UCI-batch auf einen Router |
| `/api/batch-push` | POST | UCI-batch auf mehrere Router parallel |
| `/ui/config-pull` | GET | UI-Seite |

#### 🔧 Verbesserungen

- **Navigation**: `📥 Config-Pull` Link in der Nav-Bar, gruppiert nach Funktion (Trennzeichen)
- **Aktive Seite** in der Nav wird hervorgehoben (`_page(..., active=...)`)
- **Dashboard**: Schnellzugriff-Bar mit Config-Pull, Setup, Projekte, Templates
- **CSS**: `.card-teal`, `.badge-teal`, `.btn-teal`, `.grid3` ergänzt
- **`max-width`**: 1100px → 1200px (mehr Platz für Tabellen)

---

## [0.2.1] – 2026-02-28

### Hinzugefügt – IP-Tracking & Workflow-Integration

#### 🆕 `last_ip` – IP-Adresse wird jetzt automatisch gespeichert

- **DB-Schema**: Neues Feld `last_ip TEXT` in Tabelle `devices`
- **Migration**: Beim Start automatisch `ALTER TABLE devices ADD COLUMN last_ip` für bestehende DBs
- **Claim**: `/api/claim` speichert die IP des anfragenden Geräts als `last_ip`
  (ermöglicht "ein Gerät claimt → IP sofort bekannt")

#### 🔧 SSH-Installer: `last_ip` als Standard-IP

- `/ui/deploy/{mac}/ssh` befüllt das IP-Feld jetzt bevorzugt aus `last_ip`
  (Fallback: berechnete MGMT_NET.SUFFIX-IP wie bisher)
- Kleiner Hinweis im Formular: „🔴 aus Datenbank" vs. „📐 aus MGMT_NET berechnet"

#### 🔧 Config-Pull: „Aus Geräteliste laden" funktioniert vollständig

- `GET /api/devices` gibt `last_ip` zurück
- `loadFromDevices()` trägt die gespeicherte IP direkt ins IP-Feld des jeweiligen Routers ein
- Hostname als Placeholder im IP-Feld wenn keine IP bekannt
- Meldung: Anzahl geladener Geräte + Hinweis falls einige ohne IP

#### 🔧 Dashboard: IP-Spalte + SSH-Button

- Gerätetabelle zeigt `last_ip` als eigene Spalte (Monospace, gedimmt)
- Neuer `📡`-Button pro Zeile → direkt zum SSH-Installer

#### 🔧 Gerät-Detailseite: IP anzeigen

- `/ui/devices/{mac}` zeigt `last_ip` in der blauen Info-Box (grün hervorgehoben)

#### Neue `addTarget()` Signatur (intern)

```javascript
addTarget(ip='', mac='', user='root', label='')
// label wird als Placeholder im IP-Feld verwendet wenn ip leer ist
```

---

## [0.2.0] – 2026-02-28

### Hinzugefügt – Config Pull → Edit → Direct Push

#### Neue UI: `/ui/config-pull` (Nav-Link `📥 Config-Pull`)
5-Schritt-Workflow: Pull → Editor → UCI-Vorschau → Speichern → Push

#### ① Pull-Methode wählbar (mit Beschreibung)
- **`uci export`** *(empfohlen)* – vollständige Sections-Config inkl. Listenfelder (DNS, Ports). Beste Kompatibilität.
- **`uci show`** – flaches Key=Value-Format; wird server-seitig via `_uci_show_to_export()` konvertiert.
- Beide Methoden: nur read-only, kein Schreibzugriff auf Quell-Router.
- Live-Fortschrittslog beim Pull (Polling).

#### ② Vollständiger WLAN-Editor
- **Tab-basiert** – ein Tab pro UCI wifi-iface (wifinet0, wifinet1 …)
- Pro WLAN editierbar:
  - SSID, Passwort / Key, Verschlüsselung (6 Optionen mit Erklärung)
  - **Netz / VLAN** – Dropdown aus echten UCI-Interfaces des Quell-Routers + Freitext-Fallback
  - 802.11r Roaming (Mobility-Domain, NAS-ID, FT over DS inline)
  - 802.11k (RRM) und 802.11v (BTM / BSS Transition) getrennt
  - Management Frame Protection (MFP / ieee80211w): 3 Stufen
  - WDS Bridge-Modus
  - WLAN aktivieren / deaktivieren (disabled)
- Tab-Farbe + Label aktualisieren sich live beim Bearbeiten

#### ③ UCI-Vorschau + Raw-Config Download
- `🔄 UCI-Preview` – zeigt exakt die Befehle die gepusht werden
- `📄 Raw wireless` / `📄 Raw network` – Roh-UCI direkt anzeigen

#### ④ Als Projekt oder Template speichern (optional)
- **Projekt**: Erstes aktives WLAN als `SSID`/`WPA_PSK`/`ENABLE_11R` (Rückwärtskompatibilität), alle WLANs in `wlans: [...]`
- **Template**: UCI-Format mit `{{ENABLE_11R}}` / `{{MOBILITY_DOMAIN}}` Platzhaltern, Direct-Link auf Template-Editor

#### ⑤ Push auf Client-Router
- Push-Methode wählbar: **UCI direct** (`uci batch` via SSH) oder **Script** (99-provision.sh)
- Optionen: `uci commit wireless` + `wifi reload` (kein Reboot) oder `reboot`
- **Batch-Push**: mehrere Router **parallel** ansteuern
- Live-Status-Log pro Router gleichzeitig sichtbar

#### Neue API-Endpunkte
| Endpunkt | Methode | Beschreibung |
|---|---|---|
| `/api/devices` | GET | Geräteliste als JSON (für "Aus Geräteliste laden") |
| `/api/config-pull` | POST | SSH-Pull-Job starten |
| `/api/config-pull/{id}` | GET | Pull-Status / Ergebnis abrufen |
| `/api/config-pull/{id}/raw/{sub}` | GET | Roh-UCI-Output (wireless / network) |
| `/api/config-pull/{id}/save-project` | POST | Als Projekt speichern |
| `/api/config-pull/{id}/save-template` | POST | Als UCI-Template speichern |
| `/api/direct-push` | POST | UCI-batch direkt auf einen Router |
| `/api/batch-push` | POST | UCI-batch parallel auf mehrere Router |
| `/ui/config-pull` | GET | UI-Seite (Pull → Edit → Push) |

#### Neue Backend-Funktionen
| Funktion | Beschreibung |
|---|---|
| `_parse_uci_export(raw)` | UCI export → Dict {section → {_type, _opt, _list}} |
| `_uci_show_to_export(raw)` | uci show Flat-Format → uci export Sections-Format |
| `_extract_wlans(parsed)` | wifi-iface Sektionen → WLAN-Dict-Liste |
| `_extract_radios(parsed)` | wifi-device Sektionen → Radio-Liste |
| `_extract_networks(parsed)` | UCI-Interfaces → Netz-Dict (für VLAN-Dropdown) |
| `_wlans_to_uci_set(wlans)` | WLAN-Dicts → UCI set-Befehle (Direct-Push) |
| `_wlans_to_uci_template(wlans)` | WLAN-Dicts → UCI-Template (mit {{VAR}} Platzhaltern) |
| `_ssh_pull_job(...)` | Thread: Pull vom Quell-Router |
| `_direct_push_job(...)` | Thread: UCI-batch + commit + reload/reboot |

#### UI-Verbesserungen (global)
- Aktive Nav-Links werden hervorgehoben (`active-nav` Klasse)
- `btn-teal` + `card-teal` Farbe für Config-Pull-Aktionen
- `_nav()` Hilfsfunktion in `_page()` für einheitliche Navigation
