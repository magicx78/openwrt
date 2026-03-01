# CONTINUE_PROMPT – OpenWrt Provisioning Server v0.5.3

> Stand: 2026-03-01 | Datei: server.py (~6200 Zeilen) | DB: provision.db (SQLite)

---

## Aktueller Status

Der Server ist **stabil und produktionsbereit**. Version 0.5.0 wurde vollstaendig implementiert.

### Was laeuft

| Feature | Status | Details |
|---------|--------|---------|
| FastAPI-Server (HTTP Basic Auth) | OK | uvicorn, alle Routen aktiv |
| SQLite-Datenbank | OK | devices, projects, templates, roles, settings |
| Geraete-Enrollment | OK | POST /api/claim mit Token |
| Template-Rendering | OK | {{VAR}}-Ersetzung, Master + Private Template |
| Multi-WLAN-Projekte | OK | N WLANs pro Projekt, Tab-basierter Editor |
| VLAN-Dropdown | OK | select + „Andere…" mit Freitext-Handler (NEU v0.4.0) |
| Netzwerk-Config-Editor | OK | IPs/VLANs/Gateways je Interface |
| Netzwerk → Template-Variablen | OK | NEU v0.4.0: {{NET_LAN_IP}} etc. in build_vars() |
| WLAN_BLOCK Master-Template | OK | kein hardcoded wlan0/wlan1 mehr |
| SSH-Deploy | OK | /api/deploy/{mac}/ssh-push, precheck, jobs |
| Config-Pull -> Edit -> Push | OK | /ui/config-pull, UCI-Parse, Batch-Push |
| Config-Push (Projekt → Router) | OK | NEU v0.4.0: /ui/config-push |
| SSH-Key-Verwaltung | OK | NEU v0.4.0: Settings-UI, Key-Install-API |
| SSH-Key-Auth | OK | NEU v0.4.0: Leeres Passwort = gespeicherter Key |
| Geraete-Discovery | OK | /ui/discover, asyncio Scan, LuCI-Erkennung |
| Diagnose (Server + SSH) | OK | /ui/diagnose/{mac} |
| Rollen-Overrides | OK | ap1, node, repeater mit UCI-Overrides |
| IP-Tracking | OK | last_ip in DB, SSH-Vorausfuellung |
| 99-provision.sh dynamisch | OK | NEU v0.4.1: _generate_provision_sh(), kein static file |
| /api/config/{mac} | OK | NEU v0.4.1: UCI-Config fuer Router nach Claim |
| Download-Buttons (Setup-UI) | OK | NEU v0.4.2: Content-Disposition, Browser-Download |
| provision.conf inline | OK | NEU v0.4.2: Inhalt im Browser sichtbar + Copy-Button |
| /api/claim JSON+Form-Data | OK | NEU v0.4.3: BusyBox-wget kompatibel (beides akzeptiert) |
| Server-URL-Feld SSH-Installer | OK | NEU v0.4.3: Router-Subnet-Problem lösen |
| Image-Pakete-Card Setup-UI | OK | NEU v0.4.3: wpad-wolfssl, kmod-batman-adv etc. |
| 99-provision.sh Claim JSON | OK | NEU v0.4.4: base_mac + Content-Type: application/json |
| 99-provision.sh BusyBox-Fix | OK | NEU v0.4.5: --header='...' Syntax, kein touch bei Fehler, TOKEN single-quote |
| /api/config 409 + provision.conf Quotes | OK | NEU v0.4.6: 404→409, TOKEN='...' in provision.conf, cfg-wget ohne 2>/dev/null |
| Bootstrap final stabilisiert | OK | NEU v0.4.7: BusyBox --header Check, BATCH_RC+COMMIT_RC, kein -q |
| Bootstrap deterministisch | OK | NEU v0.4.8: curl-Fallback statt Form-Data, CLAIM_RC exit 1, HTTP_CLIENT einmalig |
| Bootstrap final korrekt | OK | NEU v0.4.9: curl -sS, leere claim.json exit 1, FAIL-Meldung mit Handlungshinweis |
| Bootstrap v0.5.0 deterministisch | OK | NEU v0.5.0: Logging+Timestamps, HTTP-Status, Fehlerseiten-Check, UCI-Pattern-Check, provision.conf |
| Bootstrap v0.5.1 fail-fast | OK | NEU v0.5.1: json_escape(), wget-Claim Body-Check, curl -fsS Config, CFG_SIZE<10 exit 1 |
| Router-Push 500-Fix | OK | NEU v0.5.2: uci_cmds Array→String, globaler Exception-Handler, JS r.ok-Check |
| Switch-Config + network restart | OK | NEU v0.5.3: SWITCH_BLOCK in build_vars, Projekt-Switch-Felder, network restart kein Fehler mehr, Bootstrap-Version dynamisch |

---

## Dateistruktur

```
filesV3/
├── server.py          # Hauptserver (~6100 Zeilen, FastAPI+SQLite)
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
settings   (key PK, value)                    -- MGMT_NET, GW, DNS, SSID, SSH_PRIVKEY, ...
roles      (name PK, description, overrides)  -- ap1, node, repeater
templates  (id, name UNIQUE, content, updated_at)
projects   (name PK, description, created_at, settings JSON)
devices    (base_mac PK, hostname, role, board_name, model,
            last_seen, last_ip, claimed, project, notes,
            override, status, last_log)
```

### projects.settings JSON-Struktur (v0.4.0)

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
| `{{NETWORKS_BLOCK}}` | NEU v0.4.0: UCI set network.* fuer alle statischen Interfaces |
| `{{NET_LAN_IP}}` | NEU v0.4.0: ipaddr von Interface "lan" (X → MGMT_SUFFIX) |
| `{{NET_LAN_VLAN}}` | NEU v0.4.0: VLAN-ID von Interface "lan" |
| `{{NET_{NAME}_IP}}` | NEU v0.4.0: IP fuer jedes konfigurierte Interface |
| `{{NET_{NAME}_VLAN}}` | NEU v0.4.0: VLAN fuer jedes Interface |
| `{{NET_{NAME}_PROTO}}` | NEU v0.4.0: Protokoll |
| `{{NET_{NAME}_MASK}}` | NEU v0.4.0: Netzmaske |
| `{{NET_{NAME}_GW}}` | NEU v0.4.0: Gateway |

---

## Download-Endpoints (v0.4.2)

Alle drei Endpoints generieren Dateien **dynamisch** (kein statisches File noetig).
Alle liefern `Content-Disposition: attachment` → Browser startet Download-Dialog.

| Method | Path | Datei | Inhalt |
|--------|------|-------|--------|
| GET | `/download/99-provision.sh` | Bootstrap-Script | _generate_provision_sh() |
| GET | `/download/provision.conf` | Server-Config | SERVER=URL + TOKEN |
| GET | `/download/start.bat` | Windows-Starter | Env-Vars aus Server |

`provision.conf` auto-erkennt Server-URL aus `request.base_url`.
Optionaler Override: `?server=192.168.x.x` (Rueckwaertskompatibilitaet).

---

## Bekannte Einschraenkungen / TODOs

### Mittel-Prioritaet
1. **Config-Pull History**: Kein Versionsverlauf fuer gepullte Konfigurationen.
2. **Geraete-Discovery -> Auto-Import**: Discovery-Seite zeigt Geraete, aber
   echter Import (POST /api/devices/import) fehlt noch.
3. **2FA / Token-Auth**: Aktuell nur HTTP Basic Auth.

### Niedrig-Prioritaet
4. **network.Worls umbenennen**: Nur moeglich mit vollstaendigem Re-Flash aller
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
| GET | `/api/config/{mac}?token=TOKEN` | UCI-Config fuer Geraet (Router-Auth) |
| POST | `/api/deploy/{mac}/ssh-push` | SSH-Push (Background-Job) |
| GET | `/api/deploy/job/{job_id}` | Job-Status pollen |
| POST | `/api/discover` | Netzwerk-Scan |
| POST | `/api/config-pull` | Config von Router holen |
| POST | `/api/batch-push` | UCI-Batch an mehrere Router |
| GET | `/api/devices` | Alle Geraete als JSON |
| GET | `/api/projects` | Alle Projekte als JSON |
| POST | `/api/config-push/preview` | UCI aus Projekt rendern |
| POST | `/api/settings/ssh-key` | SSH-Key speichern |
| GET | `/api/settings/ssh-key/status` | Key-Status |
| POST | `/api/settings/ssh-key/install` | Key auf Router installieren |
| GET | `/download/99-provision.sh` | Bootstrap-Script (Download) |
| GET | `/download/provision.conf` | Server-Config-Datei (Download) |
| GET | `/download/start.bat` | Windows-Startskript (Download) |

---

## Git / GitHub

- Repo: `magicx78/openwrt` (privat)
- Branch: `main`
- **Nicht in Git**: `provision.db`, `start.bat`, `.claude/`, `__pycache__/`
