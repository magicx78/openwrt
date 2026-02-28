# OpenWrt Minimal Provisioning – Deploy-Anleitung

## Überblick

```
┌──────────────────────────┐        HTTP POST /api/claim
│  Provisioning Server     │◄───────────────────────────────┐
│  FastAPI + SQLite        │                                │
│  http://192.168.1.100:8000│       JSON: role+template     │
└──────────────────────────┘───────────────────────────────►│
                                                            │
                                                  OpenWrt Firstboot
                                                  /etc/uci-defaults/99-provision
                                                  → uci batch
                                                  → /etc/provisioned
```

---

## 1. Server aufsetzen

### Voraussetzungen
- Python 3.9+, pip
- Erreichbar aus dem Netz der OpenWrt-Geräte (Management-VLAN oder direktes LAN)

### Installation

```bash
git clone <repo> provision-server
cd provision-server
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Konfiguration (Umgebungsvariablen)

```bash
export ENROLLMENT_TOKEN="MeinSicheresToken42"   # ← ÄNDERN!
export ADMIN_USER="admin"
export ADMIN_PASS="MeinAdminPasswort"            # ← ÄNDERN!
export HMAC_SECRET="NochEinGeheimnis"            # ← ÄNDERN!
export DB_PATH="/var/lib/provision/provision.db"

mkdir -p /var/lib/provision
```

### Starten (Entwicklung)

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

### Starten (Produktion mit systemd)

```ini
# /etc/systemd/system/provision.service
[Unit]
Description=OpenWrt Provisioning Server
After=network.target

[Service]
Type=simple
User=provision
WorkingDirectory=/opt/provision-server
Environment=ENROLLMENT_TOKEN=MeinSicheresToken42
Environment=ADMIN_USER=admin
Environment=ADMIN_PASS=MeinAdminPasswort
Environment=HMAC_SECRET=NochEinGeheimnis
Environment=DB_PATH=/var/lib/provision/provision.db
ExecStart=/opt/provision-server/venv/bin/uvicorn server:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now provision
# Admin-UI: http://SERVER:8000/ui/
# Login: admin / MeinAdminPasswort
```

---

## 2. Server konfigurieren (UI)

Öffne `http://SERVER:8000/ui/` im Browser.

### Einstellungen setzen (`/ui/settings`)

| Key           | Beispiel          | Bedeutung                     |
|---------------|-------------------|-------------------------------|
| MGMT_NET      | 192.168.50        | Erstes Oktet des Mgmt-Netzes  |
| GW            | 192.168.50.1      | Gateway                       |
| DNS           | 192.168.50.1      | DNS                           |
| SSID          | MeinWLAN          | WLAN-Name (alle APs gleich)   |
| WPA_PSK       | GeheimesWLAN      | WLAN-Passwort                 |
| ENABLE_11R    | 1                 | 802.11r Fast Roaming an/aus   |
| ENABLE_MESH   | 0                 | 802.11s+batman-adv an/aus     |
| MESH_ID       | mymesh            | Mesh-Netzname                 |
| MESH_PSK      | MeshSecret        | Mesh-Passwort                 |

### Rollen (`/ui/roles`)

- **ap1**: Root-AP, DHCP-Server aktiv → `set dhcp.lan.ignore='0'`
- **node**: Normaler AP, kein DHCP → `set dhcp.lan.ignore='1'`
- **repeater**: Mesh-Leaf, kein DHCP → `set dhcp.lan.ignore='1'`

### Template (`/ui/template`)

Das Standard-Template ist nach `init_db()` bereits gesetzt. Anpassen nach Bedarf.
Wichtig: `ifname` an dein Gerät anpassen (`eth0`, `eth0.1`, `lan`, etc.).

---

## 3. OpenWrt Image bauen (ImageBuilder)

### Paketauswahl

```bash
# ImageBuilder für dein Target runterladen
# Beispiel: mediatek/filogic für Cudy WR3000

PACKAGES="-wpad-basic-mbedtls wpad-wolfssl kmod-batman-adv batctl -iw iw-full"

# Optional: python3-light für robustes JSON-Parsing im Client
PACKAGES="$PACKAGES python3-light"
```

> **Achtung Paketkonflikte:**
> - Nur **eine** wpad-Variante! `wpad-wolfssl` ersetzt `wpad-basic-mbedtls` und `wpad-mbedtls`.
> - `-iw` entfernt das Standard-`iw`, dann `iw-full` hinzufügen.
> - `kmod-batman-adv` braucht passendes Kernel-Modul – muss aus gleichem Build stammen.

### Provisioning-Script ins Image integrieren

```bash
mkdir -p files/etc/uci-defaults
cp 99-provision.sh files/etc/uci-defaults/99-provision
chmod +x files/etc/uci-defaults/99-provision

# provision.conf mit Server-Konfiguration
mkdir -p files/etc
cat > files/etc/provision.conf <<EOF
SERVER=http://192.168.1.100:8000
TOKEN=MeinSicheresToken42
EOF
```

### Build ausführen

```bash
make image \
  PROFILE="cudy_wr3000-v1" \
  PACKAGES="$PACKAGES" \
  FILES="files/" \
  BIN_DIR="output/"

# Für andere Geräte: PROFILE anpassen
# Profile auflisten: make info
```

### Alternatives: Script nachträglich aufspielen

Wenn Image schon geflasht und Gerät erreichbar:

```bash
scp 99-provision.sh root@192.168.1.1:/etc/uci-defaults/99-provision
ssh root@192.168.1.1 "chmod +x /etc/uci-defaults/99-provision"

# provision.conf setzen
ssh root@192.168.1.1 'cat > /etc/provision.conf <<EOF
SERVER=http://192.168.1.100:8000
TOKEN=MeinSicheresToken42
EOF'

# Manuell ausführen (simuliert Firstboot)
ssh root@192.168.1.1 "sh /etc/uci-defaults/99-provision"
```

---

## 4. Firstboot-Ablauf

```
Flash → Firstboot
  └─ /etc/uci-defaults/99-provision
      ├─ /etc/provisioned vorhanden? → exit 0 (schon provisioniert)
      ├─ Base-MAC ermitteln (eth0 > br-lan > erster iface)
      ├─ board.json lesen
      ├─ POST /api/claim  {mac, board, model, token}
      │   └─ Server: Gerät registrieren, Template rendern, antworten
      ├─ Template als uci-batch ausführen
      ├─ SSH Hostkeys regenerieren (dropbear oder openssh)
      ├─ uci commit (network, wireless, system, firewall, dhcp)
      ├─ Dienste neu starten
      └─ /etc/provisioned schreiben → fertig
```

---

## 5. Gerät dem Server zuweisen

Nach dem ersten Claim erscheint das Gerät unter `/ui/devices/`.

```
Dashboard → Gerät anklicken → Rolle setzen → Speichern
```

Beim nächsten Boot (oder manuell) zieht das Gerät seine neue Rolle.
Um erneut zu provisionieren:

```bash
# Auf dem OpenWrt-Gerät:
rm /etc/provisioned
reboot
# ODER manuell:
sh /etc/uci-defaults/99-provision
```

---

## 6. Fallstricke & Lösungen

### DHCP-Konflikte

Problem: Mehrere APs mit DHCP-Server im gleichen Segment.

Lösung: Nur `ap1`-Rolle hat `dhcp.lan.ignore='0'`. Alle anderen Rollen setzen `ignore='1'`.
Prüfen: `uci show dhcp.lan.ignore` auf jedem Gerät.

### wpad-Paketkonflikte

Problem: `wpad-basic-mbedtls` oder `wpad-mbedtls` blockiert `wpad-wolfssl`.

Lösung im ImageBuilder:
```
PACKAGES="-wpad-basic-mbedtls -wpad-mbedtls wpad-wolfssl"
```
Prüfen: `opkg list-installed | grep wpad` → nur eine Variante.

### ULA / IPv6 Duplikate

Problem: Jeder OpenWrt-Router generiert beim ersten Boot eine zufällige ULA (fd.../8).
Bei mehreren APs im gleichen Netz entstehen unterschiedliche ULA-Präfixe.

Lösung im Template:
```
set network.globals.ula_prefix='fd12:3456:789a::/48'
```
Einheitliches Präfix aus dem Template → alle Geräte nutzen dasselbe Präfix.
Oder IPv6 ganz deaktivieren:
```
set network.lan.ipv6='0'
delete network.wan6
```

### SSH Hostkey-Duplikate

Problem: Alle geclonten Images haben denselben SSH-Hostkey → MITM-Warnung beim Verbinden.

Lösung: `99-provision` löscht und regeneriert Hostkeys beim Firstboot automatisch.
Manuell: `dropbearkey -t ed25519 -f /etc/dropbear/dropbear_ed25519_host_key`

### `iw` Konflikt

Problem: ImageBuilder meckert über Paketkonflikt zwischen `iw` und `iw-full`.

Lösung: `-iw iw-full` in PACKAGES (Minus = entfernen, kein Präfix = hinzufügen).

### python3 nicht verfügbar

Problem: JSON-Parsing im Client schlägt fehl ohne python3.

Lösung A: `python3-light` ins Image aufnehmen (empfohlen).
Lösung B: Script nutzt dann awk-Fallback (weniger robust bei Sonderzeichen in Werten).

### Server nicht erreichbar beim Firstboot

Problem: Netzwerk noch nicht verfügbar wenn uci-defaults läuft.

Lösung: Das Script wartet nicht aktiv – bei Fehler wird Fallback-Modus genutzt.
Für robusteres Behavior: Script in `/etc/rc.local` zusätzlich einbinden mit `sleep 10` und Check auf `/etc/provisioned`.

```sh
# /etc/rc.local (zusätzlich)
if [ ! -f /etc/provisioned ]; then
    sleep 15
    sh /etc/uci-defaults/99-provision
fi
```

---

## 7. Sicherheitshinweise

- **Enrollment-Token** schützt `/api/claim` vor unautorisierten Geräten.
- **Admin-UI** ist per HTTP Basic Auth geschützt – TLS (nginx reverse proxy) dringend empfohlen für Produktivbetrieb.
- **MAC-Allowlist**: Geräte ohne Claim können nicht provisioniert werden – Server erstellt Eintrag erst beim ersten Claim.
- **HMAC**: Jedes Template-Response ist mit HMAC-SHA256 signiert. Client-seitige Verifikation kann ergänzt werden wenn gewünscht.
- **Enrollment-Token im Image**: Wer das Image hat, kennt den Token → physischer Geräteschutz wichtig. Token rotation: neuen Token setzen + neues Image bauen.

---

## 8. Verzeichnisstruktur

```
provision-server/
├── server.py              # FastAPI Server
├── requirements.txt       # Python-Abhängigkeiten
├── 99-provision.sh        # OpenWrt Client-Script
├── example-template.uci   # Beispiel-Template (Referenz)
├── DEPLOY.md              # Diese Datei
└── provision.db           # SQLite DB (wird automatisch erstellt)

ImageBuilder/
└── files/
    ├── etc/
    │   ├── provision.conf           # SERVER + TOKEN
    │   └── uci-defaults/
    │       └── 99-provision         # Client-Script
    └── ...
```
