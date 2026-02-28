# CONTINUE_PROMPT – OpenWrt Provisioning Server v0.3.0

> Stand: 2026-02-28 | Datei: server.py (~5619 Zeilen) | DB: provision.db (SQLite)

---

## Aktueller Status

Der Server ist **stabil und produktionsbereit**. Version 0.3.0 wurde vollstaendig implementiert.

### Was laeuft

| Feature | Status | Details |
|---------|--------|---------|
| FastAPI-Server (HTTP Basic Auth) | OK | uvicorn, alle Routen aktiv |
| SQLite-Datenbank | OK | devices, projects, templates, roles, settings |
| Geraete-Enrollment | OK | POST /api/claim mit Token |
| Template-Rendering | OK | {{VAR}}-Ersetzung, Master + Private Template |
| Multi-WLAN-Projekte | OK | N WLANs pro Projekt, Tab-basierter Editor |
| VLAN-Dropdown | OK | NEU v0.3.0: select statt text-input |
| Netzwerk-Config-Editor | OK | NEU v0.3.0: IPs/VLANs/Gateways je Interface |
| WLAN_BLOCK Master-Template | OK | NEU v0.3.0: kein hardcoded wlan0/wlan1 mehr |
| SSH-Deploy | OK | /api/deploy/{mac}/ssh-push, precheck, jobs |
| Script-Push (MAC-basiert) | OK | NEU v0.3.0: korrekt via /api/deploy/{mac}/ssh-push |
| Config-Pull -> Edit -> Push | OK | /ui/config-pull, UCI-Parse, Batch-Push |
| Geraete-Discovery | OK | NEU v0.3.0: /ui/discover, asyncio Scan, LuCI-Erkennung |
| Diagnose (Server + SSH) | OK | /ui/diagnose/{mac} |
| Rollen-Overrides | OK | ap1, node, repeater mit UCI-Overrides |
| IP-Tracking | OK | last_ip in DB, SSH-Vorausfuellung |
| network.Worls dokumentiert | OK | NEU v0.3.0: Breaking-Change-Warnung in UI + Code |
| paramiko in requirements.txt | OK | NEU v0.3.0 |

---

## Dateistruktur

```
filesV3/
├── server.py          # Hauptserver (~5619 Zeilen, FastAPI+SQLite)
├── provision.db       # SQLite-Datenbank (NICHT in Git!)
├── requirements.txt   # fastapi, uvicorn, python-multipart, paramiko
├── CHANGELOG.md       # Versionshistorie
├── CONTINUE_PROMPT.md # Diese Datei
├── DEPLOY.md          # Deployment-Anleitung
├── STATUS.md          # Feature-Status-Uebersicht
├── start.bat          # Windows-Starter mit Env-Vars (NICHT in Git!)
└── .gitignore         # provision.db, start.bat, __pycache__, .claude/
```

---

## Datenbank-Schema

```sql
settings   (key PK, value)                    -- MGMT_NET, GW, DNS, SSID, ...
roles      (name PK, description, overrides)  -- ap1, node, repeater
templates  (id, name UNIQUE, content, updated_at)
projects   (name PK, description, created_at, settings JSON)
devices    (base_mac PK, hostname, role, board_name, model,
            last_seen, last_ip, claimed, project, notes,
            override, status, last_log)
```

### projects.settings JSON-Struktur (v0.3.0)

```json
{
  "MGMT_NET": "192.168.10",
  "GW": "192.168.10.1",
  "DNS": "192.168.10.89",
  "ENABLE_MESH": "0",
  "template": "master",
  "SSID": "...",
  "WPA_PSK": "...",
  "ENABLE_11R": "1",
  "wlans": [
    {
      "label": "Haupt-WLAN",
      "ssid": "MeinNetz",
      "psk": "...",
      "band": "2g+5g",
      "encryption": "sae-mixed",
      "vlan": "lan",
      "r80211": "1",
      "enabled": "1"
    }
  ],
  "networks": {
    "lan":   {"proto": "static", "ipaddr": "192.168.10.X", "netmask": "255.255.255.0", "gateway": "", "vlan": "10"},
    "Media": {"proto": "static", "ipaddr": "192.168.20.1", "netmask": "255.255.255.0", "gateway": "", "vlan": "20"},
    "Worls": {"proto": "static", "ipaddr": "192.168.30.1", "netmask": "255.255.255.0", "gateway": "", "vlan": "30"},
    "Guest": {"proto": "static", "ipaddr": "192.168.40.1", "netmask": "255.255.255.0", "gateway": "", "vlan": "40"}
  }
}
```

---

## Template-Variablen

| Variable | Quelle |
|----------|--------|
| `{{HOSTNAME}}` | MAC-basiert (letztes Oktett) |
| `{{MGMT_NET}}` | settings["MGMT_NET"] |
| `{{MGMT_SUFFIX}}` | aus MAC berechnet |
| `{{GW}}` | settings["GW"] |
| `{{DNS}}` | settings["DNS"] |
| `{{SSID}}` | wlans[0].ssid |
| `{{WPA_PSK}}` | wlans[0].psk |
| `{{ENABLE_11R}}` | wlans[0].r80211 |
| `{{MOBILITY_DOMAIN}}` | SSID-Hash (4 Hex-Zeichen) |
| `{{MESH_BLOCK}}` | UCI fuer Mesh-Radio (wenn ENABLE_MESH=1) |
| `{{WLAN_BLOCK}}` | UCI fuer alle WLANs aus wlans[] |

---

## Bekannte Einschraenkungen / TODOs

### Hoch-Prioritaet
1. **Netzwerk-Editor -> Template-Rendering verbinden**: Die `networks{}`-Daten aus dem
   Projekt-Editor werden noch nicht in `build_vars()` eingespeist. IPs aus dem
   Netzwerk-Editor koennten zukuenftig direkt im Template-Rendering genutzt werden.

2. **VLAN-Dropdown "Andere..." Option**: Wenn der Nutzer "Andere..." waehlt, erscheint
   kein Freitext-Input. Fuer benutzerdefinierte VLAN-Namen fehlt noch ein JS-Handler.

3. **SSH-Key-Upload in UI**: Aktuell nur Passwort-Auth ueber UI. SSH-Key kann nur
   manuell per ~/.ssh/authorized_keys auf dem Router gesetzt werden.

### Mittel-Prioritaet
4. **Config-Pull History**: Kein Versionsverlauf fuer gepullte Konfigurationen.

5. **Geraete-Discovery -> Auto-Import**: Discovery-Seite zeigt Geraete, aber
   echter Import (POST /api/devices/import) fehlt noch.

6. **2FA / Token-Auth**: Aktuell nur HTTP Basic Auth.

### Niedrig-Prioritaet
7. **network.Worls umbenennen**: Nur moeglich mit vollstaendigem Re-Flash aller
   Geraete. Dokumentiert als known issue (CHANGELOG v0.3.0).

---

## Server starten (Entwicklung)

```bat
:: Windows (start.bat - lokal, NICHT in Git!)
set ENROLLMENT_TOKEN=geheim
set ADMIN_PASS=admin
set HMAC_SECRET=supersecret
python server.py
```

Server laeuft auf http://0.0.0.0:8000 - Admin-UI unter http://localhost:8000/ui/

---

## Wichtige API-Endpoints (Kurzreferenz)

| Method | Path | Funktion |
|--------|------|---------|
| POST | `/api/claim` | Geraet registrieren |
| GET | `/api/config/{mac}` | UCI-Script abrufen |
| POST | `/api/deploy/{mac}/ssh-push` | SSH-Push (Background-Job) |
| GET | `/api/deploy/job/{job_id}` | Job-Status pollen |
| POST | `/api/discover` | Netzwerk-Scan |
| POST | `/api/config-pull` | Config von Router holen |
| POST | `/api/batch-push` | UCI-Batch an mehrere Router |
| GET | `/api/devices` | Alle Geraete als JSON |

---

## Git / GitHub

- Repo: `magicx78/openwrt` (privat)
- Branch: `main`
- **Nicht in Git**: `provision.db`, `start.bat`, `.claude/`, `__pycache__/`
