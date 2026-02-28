# 📋 OpenWrt Provisioning Server – Projektstatus
**Stand:** 28.02.2026 | **server.py:** ~5400 Zeilen | **FastAPI + SQLite** | **v0.2.0**

---

## ✅ Was funktioniert (komplett implementiert)

### 🏗️ Kern-Infrastruktur
- FastAPI-Server mit SQLite-Datenbank (`provision.db`)
- HTTP Basic Auth für alle Admin-UI-Seiten
- Enrollment-Token-basiertes Geräteanmeldung via `/api/claim`
- HMAC-signierte Config-Ausgabe (`/api/config/{mac}`)
- Lifespan-basierte DB-Initialisierung mit Defaults

### 📡 Multi-WLAN-System
- **Projekt-Editor** mit dynamischen Tabs – ein Tab pro WLAN
- Jedes WLAN: SSID, Passwort, Band, Verschlüsselung, VLAN, 802.11r, aktiv/deaktiviert
- Gespeichert als `wlans: [...]` Array im Projekt-JSON
- Rückwärtskompatibel: erstes WLAN → `SSID`/`WPA_PSK`/`ENABLE_11R`

### ⚡ SSH-Installer
- Pro Gerät: `/ui/deploy/{mac}/ssh` + Schnellinstaller ohne DB auf `/ui/setup`
- **Precheck** (7 read-only Checks) + **Precheck-only** Modus
- SSH-Auth-Priorität: sshpass → paramiko → SSH-Key-Auth
- Fatal-Output-Erkennung (`: not found`, `No such file`, etc.)
- UTC-Timestamps in allen Logs

### 🔬 Diagnose-System
- `/ui/diagnose/{mac}` – Server + Router-Diagnose via SSH

### 📥 Config-Pull → Edit → Direct-Push (NEU v0.2.0)
- **`/ui/config-pull`** – komplette 5-Schritt-Oberfläche:
  1. Pull-Methode wählbar (`uci export` / `uci show`) mit Erklärung
  2. SSH-Pull: liest wireless + network read-only vom Quell-Router
  3. WLAN-Editor: Tab pro Interface, SSID/Key/Enc/VLAN/802.11r/k/v/MFP/WDS
  4. UCI-Vorschau + Raw-Config-Ansicht
  5. Optional als Projekt oder Template speichern
  6. Batch-Push auf mehrere Client-Router **parallel** (UCI-direct oder Script)

---

## 🖥️ UI-Seiten (komplett)

| Route | Funktion |
|---|---|
| `/ui/` | Dashboard (Geräte nach Projekt gruppiert) |
| `/ui/projects` | Projekt-Übersicht |
| `/ui/projects/{name}` | Projekt-Editor mit Multi-WLAN-Tabs |
| `/ui/devices` | Geräteliste |
| `/ui/devices/{mac}` | Gerät bearbeiten |
| `/ui/deploy/{mac}` | Config-Vorschau + Validierung |
| `/ui/deploy/{mac}/ssh` | SSH-Installer |
| `/ui/diagnose/{mac}` | Diagnose-Seite |
| `/ui/config-pull` | **NEU: Config Pull → Edit → Push** |
| `/ui/templates` | Template-Liste + Editor |
| `/ui/roles` | Rollen-Override-Editor |
| `/ui/settings` | Globale Einstellungen |
| `/ui/setup` | Setup-Assistent + SSH-Schnellinstaller |

---

## ⚠️ Bekannte Einschränkungen / TODO

- Script-Push-Methode im Config-Pull UI: ruft aktuell UCI-batch auf (nicht echten Script-Upload)
- `network.Worls` (Tippfehler) historisch konsistent – Änderung wäre breaking change
- Kein SSH-Key-Upload in der UI
- Kein Batch-Deploy über klassische Script-Methode (ohne bekannte MAC)
- Kein Gerät-Discovery / Netzwerk-Scan
- Keine 2FA

---

## 🚀 Starten

```bash
# Linux
pip install fastapi uvicorn python-multipart
pip install paramiko  # empfohlen für Windows/Passwort-Auth
uvicorn server:app --host 0.0.0.0 --port 8000

# Windows
start.bat  # Token + Passwort anpassen!
```

Admin-UI: `http://localhost:8000/ui/`
Config-Pull: `http://localhost:8000/ui/config-pull`
