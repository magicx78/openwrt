# Changelog

Alle nennenswerten Änderungen werden hier dokumentiert.
Format angelehnt an [Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

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
