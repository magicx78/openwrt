# 🛜 OpenWrt Provisioning Server v0.5.6

**Automatisierte Massenbereitstellung (Mass Provisioning) für OpenWrt Router-Netzwerke**

Ein produktionsreifer FastAPI-Server für schnelle und zuverlässige Bereitstellung identischer WLAN/Firewall-Konfigurationen auf mehreren OpenWrt-Routern – ohne USB-Stick-Jongliererei oder manuelle SSH-Befehle.

```
Hauptrouter (Config pullen)
         ↓
  Bearbeiten im Browser
         ↓
  Auf 10+ Client-Router pushen ← AUTOMATISCH ✅
         ↓
   SSH ohne Passwort ← SSH-Key auto-generiert + installiert
```

---

## ⚡ Features v0.5.6

### 🎯 Kernsystem
- **Bootstrap-Skript**: Vollständig deterministisch, fail-fast, 100% lokal auf Router
- **Template-System**: `{{HOSTNAME}}`, `{{SSID}}`, `{{SWITCH_BLOCK}}` etc.
- **Multi-WLAN**: N WLANs pro Projekt (2.4 GHz + 5 GHz, 802.11r Roaming)
- **VLAN/Switch-Config**: Automatische Generation (1x WAN-Trunk + N LAN-Ports)

### 🔐 Sicherheit & SSH
- **SSH-Key-Generator**: RSA 4096-bit on-the-fly generieren
- **Auto-Installer**: Public-Key automatisch auf 1 oder N Routern installieren
- **SSH ohne Passwort**: Einmalig Setup → Token-Auth nur optional
- **Paramiko-basiert**: Windows/Linux/Mac kompatibel

### 📥📤 Deployment
- **Config-Pull**: SSH-Pull Hauptrouter Config (WLAN, Network, System)
- **Config-Push**: Direkt auf Router oder als Template/Projekt speichern
- **Batch-Push**: Auf Projekt-Geräte mit 1 Click
- **Direct-Push**: Einzelner Router via IP + Passwort

### 🎛️ Management
- **Gerät-Vorregistrierung**: MAC + Hostname vorab anlegen
- **Projekt-System**: Geräte-Gruppen mit eigener SSID, IP-Bereich, Template
- **Export/Import**: Komplette Backup-Dateien (Templates + Projekte)

### 🔍 Observability
- **Live-Debug-Dashboard**: Geräte-Stats, Jobs, Activity-Log
- **Auto-Refresh**: Alle 2 Sekunden aktualisiert
- **Activity-Logging**: Claim-Events, SSH-Installationen, Fehler mit Timestamps

---

## 🚀 Quick Start

```bash
# 1. Dependencies
pip install fastapi uvicorn paramiko python-multipart

# 2. Server starten
cd filesV3 && uvicorn server:app --host 0.0.0.0 --port 8000

# 3. Browser: http://localhost:8000/ui/ (admin / changeme)

# 4. SSH-Key generieren
curl -X POST http://admin:changeme@localhost:8000/api/ssh/generate-keypair

# 5. Projekt erstellen + Bootstrap starten
# → /ui/setup oder /ui/config-pull
```

---

## 📊 Architektur

- **server.py** (~6600 Zeilen): FastAPI + SQLite
- **bootstrap-script** (`99-provision.sh`): Router-seitig, 100% lokal
- **Template-Engine**: UCI-Batch mit `{{VAR}}`-Ersetzung
- **SSH-Deployment**: Paramiko-basiert, Windows-compatible
- **Web-UIs**: Config-Pull/Push, SSH-Generator, Debug-Dashboard, Setup

---

## 🎯 Use-Cases

**Mesh-Netzwerk (10 APs)**: Pull Config → Template → Projekt → Batch-Push
**Kunden-Roll-Out**: Template-Pro-Kunde → 50+ Geräte provisionen → Activity-Log
**Migrations-Update**: Pull → Edit → Test-Push → Produktion-Push

---

## 📈 Performance

- SQLite: 100+ Geräte kein Problem
- Config-Pull: 5-10s (SSH)
- Batch-Push 50x: ~25 Minuten
- Async FastAPI: Mehrere Requests parallel

---

## 🐛 Known Limitations

| Issue | Workaround |
|---|---|
| Doppelte Routes (dead code) | FastAPI first-match, kein Funktionsproblem |
| save-template nur WLAN | Templates manuell ergänzen |
| Keine Token-Rotation | OK für LAN, env var ändern |
| HTTP only | Reverse-Proxy mit HTTPS |

---

## 🔐 Sicherheit

- HTTP Basic Auth für Server
- Token-Auth für Router-Claim
- SSH mit Paramiko (Key oder Passwort)
- Best Practice: `.env` für Secrets, provision.db im .gitignore

---

## 📞 Support

```bash
# Debug-Dashboard
http://localhost:8000/ui/debug

# Activity-Log
curl http://admin:changeme@localhost:8000/api/debug/status | jq .activity

# Database
sqlite3 provision.db "SELECT * FROM devices;"
```

---

## 📝 Mehr Info

- README_Provisioning.md: Bedienungsanleitung
- CHANGELOG.md: Version-History
- TODO.md: Geplante Features

Siehe GitHub: **magicx78/openwrt** (privat)
