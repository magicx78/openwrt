# 🤖 Continuation Prompt – OpenWrt Provisioning Server v0.2.0

## Kontext

Du arbeitest an einem **OpenWrt Minimal Provisioning Server** (`server.py`).
FastAPI + SQLite, ~5400 Zeilen, aktuell **v0.2.0**.

---

## Vollständiger Feature-Stand (v0.2.0)

### ✅ Kern-Infrastruktur
- FastAPI + SQLite (`provision.db`), HTTP Basic Auth, HMAC-Config, Enrollment-Token
- Lifespan-basierte DB-Initialisierung mit Defaults

### ✅ Multi-WLAN-System (Projekt-Editor)
- `wlans: [...]` Array pro Projekt
- Tab-basierter Editor im Browser
- `build_wlan_block()` → UCI aus wlans[] Array
- Rückwärtskompatibel: erstes WLAN → `SSID`/`WPA_PSK`/`ENABLE_11R`

### ✅ SSH-Installer
- Precheck (7 read-only Checks) + Precheck-only Modus
- SSH-Auth: sshpass → paramiko → SSH-Key (plattformübergreifend)
- Fatal-Output-Erkennung (`: not found`, `No such file`, etc.)
- UTC-Timestamps überall: `now_utc()`, `_ts()`

### ✅ Diagnose-System
- `/ui/diagnose/{mac}` + `/api/diagnose/{mac}`

### ✅ Config-Pull → Edit → Direct-Push (NEU in v0.2.0)

#### `/ui/config-pull` – 5-Schritt-Workflow
1. Pull-Methode wählbar mit Erklärung (`uci export` empfohlen, `uci show` möglich)
2. SSH-Pull: liest `wireless` + `network` read-only vom Quell-Router
3. WLAN-Editor: Tab pro wifi-iface, SSID/Key/Enc/Netz(VLAN)/802.11r/k/v/MFP/WDS
4. UCI-Vorschau + Raw-Config-Ansicht
5. Optional als Projekt oder Template speichern
6. Batch-Push auf mehrere Client-Router parallel (UCI-direct oder Script)

#### Neue Backend-Funktionen
```python
_parse_uci_export(raw)           # UCI export → Dict
_uci_show_to_export(raw)         # uci show → uci export Format
_extract_wlans(parsed)           # wifi-iface → WLAN-Dicts
_extract_radios(parsed)          # wifi-device → Radio-Dicts
_extract_networks(parsed)        # interfaces → Netz-Dict
_wlans_to_uci_set(wlans)        # WLANs → UCI set-Befehle
_wlans_to_uci_template(wlans)   # WLANs → UCI-Template mit {{VAR}}
_ssh_pull_job(...)               # Thread: Pull
_direct_push_job(...)            # Thread: Direct-Push (uci batch + commit + reload)
```

#### Alle API-Endpunkte (komplett)
```
# Claim & Config
POST /api/claim
GET  /api/config/{mac}

# Deploy
POST /api/deploy/{mac}/ssh-push
GET  /api/deploy/job/{job_id}
POST /ui/deploy/{mac}/push
GET  /ui/deploy/{mac}
GET  /ui/deploy/{mac}/ssh

# Diagnose
GET  /api/diagnose/{mac}
POST /api/diagnose/{mac}/ssh
GET  /api/diagnose/report/{id}.json
GET  /api/diagnose/report/{id}.txt
GET  /api/diagnose/report/{id}.config

# Config-Pull (NEU v0.2.0)
GET  /api/devices                              # Geräteliste als JSON
POST /api/config-pull                          # Pull-Job starten
GET  /api/config-pull/{id}                     # Status/Ergebnis
GET  /api/config-pull/{id}/raw/{sub}           # Roh-UCI-Output
POST /api/config-pull/{id}/save-project        # Als Projekt speichern
POST /api/config-pull/{id}/save-template       # Als Template speichern
POST /api/direct-push                          # UCI-batch → 1 Router
POST /api/batch-push                           # UCI-batch → N Router

# Setup
POST /api/setup/quick-ssh
GET  /api/status

# UI-Seiten
GET  /ui/
GET  /ui/devices
GET  /ui/devices/{mac}
POST /ui/devices/{mac}
POST /ui/devices/{mac}/delete
GET  /ui/projects
POST /ui/projects/new
GET  /ui/projects/{name}
POST /ui/projects/{name}/save
POST /ui/projects/{name}/delete
GET  /ui/templates
GET  /ui/templates/{name}
POST /ui/templates/{name}
GET  /ui/roles
POST /ui/roles/{name}
GET  /ui/settings
POST /ui/settings
GET  /ui/setup
GET  /ui/config-pull   ← NEU v0.2.0
GET  /ui/diagnose/{mac}

# Downloads
GET  /download/99-provision.sh
GET  /download/provision.conf
GET  /download/start.bat
GET  /provision.sh
```

---

## Datenbankschema
```sql
settings  (key PK, value)
roles     (name PK, description, overrides)
templates (id, name UNIQUE, content, updated_at)
projects  (name PK, description, created_at, settings JSON)
devices   (base_mac PK, hostname, role, board_name, model,
           last_seen, claimed, project, notes, override,
           status, last_log)
```

## Wichtige Globals / Konstanten
```python
__version__       = "0.2.0"
_ssh_jobs         # dict: job_id → {status, log, done, success, precheck_only, ip}
_pulled_configs   # dict: pull_id → {done, success, log, ip, wlans[], radios[], networks{}, ...}
_diag_reports     # dict: report_id → {report, config}
_DEPLOY_FATAL_PATTERNS = [": not found", "No such file", "Permission denied",
                           "uci: Usage:", "ash: can't open", "provision script not found"]
_PRECHECK_CMDS    # 7 read-only SSH-Checks
_MAX_CMD_OUTPUT   = 8192
```

## SSH-Funktions-Stack
```python
_build_base_ssh(ip, user, pw, logline)              # Auth: sshpass > paramiko > key
_ssh_exec(base, cmd, stdin_data, timeout)           # Ausführen (subprocess oder paramiko)
_run_precheck(base, logline) → bool                 # 7 read-only Checks
_ssh_push_job(...)                                  # Precheck + Script-Upload + Exec
_direct_push_job(...)                               # UCI-batch + commit + reload/reboot
_ssh_pull_job(...)                                  # uci export lesen + parsen
```

## Starten
```bash
# Linux
pip install fastapi uvicorn python-multipart paramiko
uvicorn server:app --host 0.0.0.0 --port 8000

# Windows
start.bat   (Token + Passwort anpassen!)
```
Admin-UI: `http://localhost:8000/ui/`
Config-Pull: `http://localhost:8000/ui/config-pull`

## ⚠️ Bekannte Einschränkungen / offene TODOs
- **Script-Push-Methode** im Config-Pull UI ruft aktuell ebenfalls `/api/direct-push` auf
  (UCI-batch). Echter Script-Upload via `/api/deploy/{mac}/ssh-push` erfordert bekannte MAC.
- `network.Worls` (historischer Tippfehler für "Works") im Private-Template – breaking change, dokumentiert
- Kein SSH-Key-Upload in der UI
- Kein Batch-Deploy über klassische Script-Methode ohne MAC
- Kein Gerät-Discovery / Netzwerk-Scan
- Keine 2FA
- Tests (pytest) nicht im Lieferumfang

## Nächste sinnvolle Aufgaben
1. **Script-Push-Methode** korrekt implementieren (MAC-basiert via `/api/deploy/{mac}/ssh-push`)
2. **Netzwerk-Config-Editor** – IPs, VLANs, Gateways aus `networks{}` editierbar machen
3. **Pull-History** – mehrere Pulls vergleichen, versionieren
4. **Geräte-Discovery** – Netzwerk-Scan nach OpenWrt-Routern
5. **WLAN-Template `{{WLAN_BLOCK}}`** im Master-Template aktivieren
6. **paramiko** in `requirements.txt` aufnehmen
