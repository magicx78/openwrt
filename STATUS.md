# 📋 OpenWrt Provisioning Server – Projektstatus
**Stand:** 01.03.2026 | **server.py:** ~6200 Zeilen | **FastAPI + SQLite** | **v0.4.3**

---

## ✅ Was funktioniert (komplett implementiert)

### 🏗️ Kern-Infrastruktur
- FastAPI-Server mit SQLite-Datenbank (`provision.db`), HTTP Basic Auth
- Enrollment-Token-basiertes Geräteanmeldung via `POST /api/claim`
  - **v0.4.3**: akzeptiert JSON *und* `x-www-form-urlencoded` (BusyBox-wget kompatibel)
  - Feldname `mac` als Alias für `base_mac` (für ältere Provision-Scripts)
- `GET /api/config/{mac}?token=...` – gerenderte UCI-Config für Router (v0.4.1)
- HMAC-signierte Config-Ausgabe
- Lifespan-basierte DB-Initialisierung mit Defaults

### 📡 Multi-WLAN-System
- **Projekt-Editor** mit dynamischen Tabs – ein Tab pro WLAN
- Jedes WLAN: SSID, Passwort, Band, Verschlüsselung, VLAN, 802.11r, aktiv/deaktiviert
- Gespeichert als `wlans: [...]` Array im Projekt-JSON
- **VLAN-Dropdown**: fest + „Andere…" Freitext-Option (v0.4.0)

### 🌐 Netzwerk-Editor → Template-Variablen (v0.4.0)
- `networks: {...}` aus Projekt-Settings → `build_vars()`
- Template-Variablen: `{{NET_LAN_IP}}`, `{{NET_LAN_VLAN}}`, `{{NETWORKS_BLOCK}}` etc.
- X-Platzhalter in IPs (`192.168.10.X`) → MGMT_SUFFIX aus MAC berechnet

### ⚡ SSH-Installer & Deploy
- Pro Gerät: `/ui/deploy/{mac}/ssh` + Schnellinstaller auf `/ui/setup`
- **v0.4.3**: Neues „Server-URL"-Feld im Schnellinstaller
  (Router im anderen Subnet kann abweichende URL nutzen)
- **Precheck** (7 read-only Checks) + **Precheck-only** Modus
- SSH-Auth-Priorität: Key-Auth (gespeicherter Key) → paramiko → sshpass
- Fatal-Output-Erkennung (`: not found`, `No such file`, etc.)
- UTC-Timestamps in allen Logs

### 🗝️ SSH-Key-Verwaltung (v0.4.0)
- Settings-UI: SSH-Private-Key eintragen (PEM: RSA, Ed25519, ECDSA)
- Automatische Key-Auth wenn Passwort-Feld leer
- „Key auf Router installieren"-Funktion (fügt Public Key in authorized_keys ein)
- Gespeichert als `SSH_PRIVKEY` in Settings-DB

### 📤 Config-Push (v0.4.0)
- `/ui/config-push` – 3-Schritt-Workflow: Projekt auswählen → UCI rendern → SSH-Push
- Vorschau der generierten UCI-Config im Browser

### 📥 Config-Pull → Edit → Direct-Push
- `/ui/config-pull` – 5-Schritt-Oberfläche
- Pull-Methode wählbar (`uci export` / `uci show`)
- SSH-Pull: liest wireless + network read-only vom Quell-Router
- WLAN-Editor: Tab pro Interface
- UCI-Vorschau + Raw-Config-Ansicht
- Batch-Push auf mehrere Router parallel

### 🔬 Diagnose & Discovery
- `/ui/diagnose/{mac}` – Server + Router-Diagnose via SSH
- `/ui/discover` – Netzwerk-Scan mit asyncio, LuCI-Erkennung

### 🚀 Setup-Assistent (v0.4.2 / v0.4.3)
- Download-Buttons (`99-provision.sh`, `provision.conf`, `start.bat`) alle dynamisch generiert
- `Content-Disposition: attachment` → Browser-Download statt Inline-Anzeige
- `provision.conf` Inhalt direkt im Browser mit 📋 Copy-Button
- **v0.4.3**: „📦 Benötigte Image-Pakete"-Card mit Copy-Button:
  `wpad-wolfssl kmod-batman-adv batctl-full openssh-sftp-server -wpad-basic-mbedtls`

---

## 🖥️ UI-Seiten (komplett)

| Route | Funktion |
|---|---|
| `/ui/` | Dashboard (Geräte nach Projekt gruppiert) |
| `/ui/projects` | Projekt-Übersicht |
| `/ui/projects/{name}` | Projekt-Editor mit Multi-WLAN-Tabs + Netzwerk-Editor |
| `/ui/devices` | Geräteliste |
| `/ui/devices/{mac}` | Gerät bearbeiten |
| `/ui/deploy/{mac}` | Config-Vorschau + Validierung |
| `/ui/deploy/{mac}/ssh` | SSH-Installer |
| `/ui/diagnose/{mac}` | Diagnose-Seite |
| `/ui/config-pull` | Config Pull → Edit → Push |
| `/ui/config-push` | Config-Push (Projekt → Router) – NEU v0.4.0 |
| `/ui/discover` | Netzwerk-Discovery / Scan |
| `/ui/templates` | Template-Liste + Editor |
| `/ui/roles` | Rollen-Override-Editor |
| `/ui/settings` | Globale Einstellungen + SSH-Key-Verwaltung |
| `/ui/setup` | Setup-Assistent + SSH-Schnellinstaller |

---

## 📦 Benötigte OpenWrt Image-Pakete

```
# Hinzufügen:
wpad-wolfssl kmod-batman-adv batctl-full openssh-sftp-server

# Entfernen (Konflikt):
-wpad-basic-mbedtls

# ImageBuilder:
make image PACKAGES="wpad-wolfssl kmod-batman-adv batctl-full openssh-sftp-server -wpad-basic-mbedtls"
```

---

## ⚠️ Bekannte Einschränkungen / TODO

- `network.Worls` (Tippfehler für „Works") – historisch konsistent, Änderung wäre breaking change für alle deployed Router
- Config-Pull History: kein Versionsverlauf für gepullte Konfigurationen
- Geräte-Discovery → Auto-Import: Discovery-Seite zeigt Geräte, echter Import fehlt noch
- Keine 2FA (aktuell nur HTTP Basic Auth)

---

## 🚀 Starten

```bash
# Linux
pip install fastapi uvicorn python-multipart paramiko
uvicorn server:app --host 0.0.0.0 --port 8000

# Windows
start.bat  # Token + Passwort anpassen!
```

Admin-UI: `http://localhost:8000/ui/`
Setup-Assistent: `http://localhost:8000/ui/setup`
GitHub: `https://github.com/magicx78/openwrt` (privat, Branch: `main`)
