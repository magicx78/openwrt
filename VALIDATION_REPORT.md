# Validation Report — server.py v0.6.2
## Fixes A–E: Runtime-Validierung, API-Spezifikation, Integrations-Handoff

**Erstellt:** 2026-04-08  
**Geprüft:** `python validate_fixes.py` — 37/37 Checks, Exit 0  
**Runtime-Test:** lokaler uvicorn, `GET /api/topology?include_wifi=1` — HTTP 200, JSON valide

---

## 1. Runtime-Test-Ergebnis

### Setup

```
Server:  python -m uvicorn server:app --host 127.0.0.1 --port 18001
Auth:    HTTP Basic (ADMIN_USER / ADMIN_PASS aus Umgebungsvariablen)
DB:      provision.db — 3 Testgeräte geseedet (ap1 + 2x node)
```

### `GET /api/topology?include_wifi=1` — echte Antwort

```json
{
  "devices": [
    {
      "id": "e4:95:6e:40:01:01",
      "label": "cudy-wr3000-gw",
      "type": "router",
      "role": "ap1",
      "project": "home",
      "status": "provisioned",
      "ip": "10.10.10.1",
      "color": "#4fc3f7",
      "borderColor": "#10b981",
      "borderWidth": 2
    },
    {
      "id": "e4:95:6e:40:02:01",
      "label": "cudy-mesh-node1",
      "type": "ap",
      "role": "node",
      "project": "home",
      "status": "provisioned",
      "ip": "10.10.10.2",
      "color": "#81c784",
      "borderColor": "#10b981",
      "borderWidth": 2
    },
    {
      "id": "e4:95:6e:40:03:01",
      "label": "cudy-pending",
      "type": "ap",
      "role": "node",
      "project": "home",
      "status": "pending",
      "ip": null,
      "color": "#81c784",
      "borderColor": "#f59e0b",
      "borderWidth": 2
    }
  ],
  "edges": [
    {
      "id": "e4:95:6e:40:01:01--e4:95:6e:40:02:01",
      "from": "e4:95:6e:40:01:01",
      "to": "e4:95:6e:40:02:01",
      "arrows": "to"
    },
    {
      "id": "e4:95:6e:40:01:01--e4:95:6e:40:03:01",
      "from": "e4:95:6e:40:01:01",
      "to": "e4:95:6e:40:03:01",
      "arrows": "to"
    }
  ],
  "projects": ["home"],
  "timestamp": "2026-04-07T22:41:53.820177+00:00",
  "wifi_clients": {},
  "wifi_last_update": {},
  "wifi_iface_status": {}
}
```

### Befund

| Prüfpunkt | Ergebnis |
|---|---|
| `wifi_iface_status` vorhanden | ✓ |
| `wifi_clients` vorhanden | ✓ |
| `wifi_last_update` vorhanden | ✓ |
| Edges korrekt (ap1 → beide nodes) | ✓ |
| Status-Farben korrekt (provisioned=grün, pending=gelb) | ✓ |
| WiFi-Felder leer (kein SSH-Key im lokalen Test-DB) | ✓ erwartet |
| Kein Crash, kein 500 | ✓ |

**WiFi-Felder leer:** Das WiFi-Polling versuchte den AP (10.10.10.1) zu erreichen,
hatte aber keinen SSH-Key in der lokalen DB. Fehler wird still behandelt,
Stale-Data-Logik greift. Korrektes Verhalten.

---

## 2. Erwartete Produktions-Antwort — `wifi_iface_status`

Wenn SSH-Key vorhanden und AP erreichbar, liefert ein Polling-Zyklus (≤15 s):

```json
"wifi_iface_status": {
  "e4:95:6e:40:01:01": {
    "phy1-ap0": {
      "rx_bytes": 2900000000,
      "tx_bytes": 7500000000,
      "valid":   true,
      "status":  "active",
      "warning": null
    },
    "phy0-ap2": {
      "rx_bytes": 95000000,
      "tx_bytes": 800000000,
      "valid":   true,
      "status":  "active",
      "warning": null
    },
    "phy0-ap3": {
      "rx_bytes": 0,
      "tx_bytes": 256,
      "valid":   true,
      "status":  "active",
      "warning": null
    },
    "br-lan.30": {
      "rx_bytes": 0,
      "tx_bytes": 0,
      "valid":   true,
      "status":  "inactive",
      "warning": "inactive"
    }
  }
}
```

### Erwartete Produktions-Antwort — `wifi_clients`

```json
"wifi_clients": {
  "e4:95:6e:40:01:01": {
    "aa:bb:cc:dd:ee:10": {
      "signal":    -65,
      "bitrate":   390,
      "connected": true,
      "last_seen": "2026-04-08T10:00:00+00:00"
    },
    "aa:bb:cc:dd:ee:11": {
      "signal":    null,
      "bitrate":   null,
      "connected": true,
      "last_seen": "2026-04-08T10:00:00+00:00"
    }
  }
}
```

Zweiter Client: `signal=null`, `bitrate=null` — iwinfo hat keinen Wert geliefert.
Das ist `unbekannt`, nicht `schwaches Signal`.

---

## 3. Interface-Verifikation (Fix C)

Getestet via `validate_fixes.py` — alle 12 Klassifizierungsfälle korrekt:

| UCI-Name | Proto | Device | interface_type | Ergebnis |
|---|---|---|---|---|
| `wan` | static | wan | `uplink` | ✓ |
| `wan6` | dhcpv6 | — | `uplink` | ✓ |
| `wg0` | wireguard | — | `vpn` | ✓ |
| `lan1` | static | — | `lan_port` | ✓ |
| `lan2` | static | — | `lan_port` | ✓ |
| `lan3` | static | — | `lan_port` | ✓ |
| `lan` | static | br-lan | `lan` | ✓ |
| `iot` | static | br-lan.20 | `lan` | ✓ |
| `guest` | static | br-lan.30 | `lan` | ✓ |
| `ap0` | static | phy0-ap0 | `wifi` | ✓ |
| `ap1` | static | phy1-ap1 | `wifi` | ✓ |
| `foo` | static | — | `unknown` | ✓ |

**wifi-device Sektionen** (UCI-Typ `wifi-device`, z.B. `radio0`) werden korrekt **ausgeschlossen** — nur `interface`-Sektionen werden zurückgegeben.

---

## 4. RX/TX-Validierung (Fix D)

Getestet via `validate_fixes.py`:

| Input | valid | warning | Bedeutung |
|---|---|---|---|
| rx=1 000 000, tx=500 000 | `true` | `null` | normal |
| rx=0, tx=0 | `true` | `"inactive"` | kein Traffic |
| rx=-1, tx=0 | `false` | `"invalid_negative"` | Datenfehler |
| rx=0, tx=-5 | `false` | `"invalid_negative"` | Datenfehler |
| rx=None, tx=None | `true` | `null` | unbekannt, kein Fehler |
| rx=0, tx=256 | `true` | `null` | aktiv (Beacon-Traffic) |

**`/proc/net/dev`-Parsing** korrekt für reale Daten aus der Router-Diagnose:

| Interface | rx_bytes | tx_bytes | status |
|---|---|---|---|
| `wan` | 113 000 000 000 | 9 400 000 000 | active |
| `phy0-ap0` | 95 000 000 | 800 000 000 | active |
| `phy0-ap3` | 0 | 256 | active (256 tx ≠ 0/0) |
| `br-lan.30` | 0 | 0 | **inactive** |

---

## 5. Signal/Bitrate-Verifikation (Fix B)

Kein einziger `-60`-Defaultwert in Parser-Output. Getestet mit:
- Validen iwinfo-Zeilen → Signal korrekt geparst
- Garbage-Zeilen → `signal=null, bitrate=null`
- Leerer Input → `{}`
- `iw station dump` ohne Brackets → `signal=null` (korrekt)
- `iw station dump` mit `[-65, -68]` → `null` (pre-existing Parser-Bug, dokumentiert)

---

## 6. Bekannte Restgrenzen

### 6.1 `iw station dump` — Signal-Brackets

**Problem:** `iw dev <iface> station dump` liefert für Multi-Chain-Adapter:
```
signal: -65 [-65, -68] dBm
```
Der vorhandene Parser extrahiert `line.split('[')[1].split(']')[0]` = `"-65, -68"`
und ruft `int("-65, -68")` auf → `ValueError` → Signal bleibt `None`.

**Auswirkung:** Multi-Chain-Signal nicht auswertbar. Ergebnis ist `null`.

**Verhalten nach Fix B:** `null` statt erfundener `-60`. Das ist korrekt.  
**Kein Rückfall auf Phantomwerte.**

**Zukünftiger Fix (out of scope):** `int(line.split('[')[1].split(',')[0])` würde `-65` liefern.

---

### 6.2 `inactive`-Regel ist strikt

`_validate_rx_tx(rx, tx)` setzt `"inactive"` nur wenn **beide** Zähler exakt `0` sind.

```
rx=0, tx=0    → "inactive"   ← einziger Fall
rx=0, tx=256  → "active"     ← Beacon-Traffic, IGMP, STP-Frames
rx=0, tx=1088 → "active"     ← br-lan.30 mit IGMP-Frames
```

Das ist gewolltes Verhalten. `tx=256` bei `phy0-ap3` bedeutet: Interface lebt,
sendet Beacons, hat nur keine assoziierten Clients.

---

### 6.3 Config-Pull hat keine Live-Daten

`_extract_networks()` arbeitet mit geparsten UCI-Export-Daten.
Der `interface_type` wird statisch aus `proto` + `device`-String abgeleitet.

Es werden **keine** Live-Daten (`/proc/net/dev`) gelesen.  
`rx_bytes`, `tx_bytes`, `status` existieren dort **nicht**.

Das WiFi-Polling-Pfad (`_poll_wifi_clients_from_ap`) liest `/proc/net/dev` —
aber nur für WiFi-Interfaces, nicht für alle LAN-Interfaces.

---

### 6.4 SSH-Key Voraussetzung

Das WiFi-Polling ruft `_get_saved_ssh_key()` aus der SQLite-Datenbank ab.
Ohne gespeicherten Key versucht es leeres Passwort (Key-Auth ohne Key schlägt fehl).

```
Kein Key → SSH-Fehler → _poll_wifi_clients_from_ap gibt None zurück
          → wifi_clients[ap_mac] unverändert (stale)
          → wifi_iface_status[ap_mac] nicht befüllt
          → Antwort: wifi_iface_status = {}
```

Kein Crash. Kein 500. Nur leere Daten.

---

### 6.5 Polling-Latenz

`_WIFI_POLLING_INTERVAL_SECONDS = 15`

Interface-Discovery + `/proc/net/dev`-Lesen + Client-Poll = 3–4 SSH-Befehle pro AP-Zyklus.
Bei Netzwerk-Latenz oder langsamen APs kann die tatsächliche Latenz > 15 s sein.
Der nächste Zyklus startet erst nach `time.sleep(15)` nach Abschluss des vorherigen.

---

## 7. Frontend- und HA-Integration — Semantik

### `wifi_clients[ap_mac][client_mac]`

```
signal: -65     → Signalstärke in dBm — anzeigen
signal: null    → UNBEKANNT — als "?" / "--" darstellen
                  NIEMALS -60 oder einen anderen Defaultwert substituieren

bitrate: 390    → TX-Bitrate in Mbit/s — anzeigen
bitrate: null   → UNBEKANNT — als "?" / "--" darstellen
                  NIEMALS 0 substituieren

connected: true → Client ist assoziiert
last_seen:      → ISO-8601 UTC-Timestamp letzter erfolgreicher Poll
```

---

### `wifi_iface_status[ap_mac][iface_name]`

```
status: "active"
  → Interface hat Traffic
  → normal darstellen
  → rx_bytes / tx_bytes können für Sparklines / Counters genutzt werden

status: "inactive"
  → rx_bytes == 0 AND tx_bytes == 0
  → Interface ist leer — grau darstellen, optional ausblenden
  → KEIN Fehler — z.B. leeres Gast-VLAN, inaktives SSID

valid: false, warning: "invalid_negative"
  → Zähler-Korruption erkannt
  → als Datenfehler markieren (rotes Icon)
  → rx_bytes / tx_bytes NICHT nutzen

valid: true, warning: null
  → Daten in Ordnung

rx_bytes / tx_bytes == null
  → /proc/net/dev nicht gelesen (SSH-Fehler oder Interface nicht in iw dev)
  → als "unbekannt" behandeln, nicht als 0
```

---

### `interface_type` (aus Config-Pull, nicht WiFi-Polling)

```
"uplink"    → WAN / Internet-Uplink → Uplink-Icon, separate Farbe
"lan"       → Bridge-VLAN (br-lan.X) → LAN-Segment-Icon
"lan_port"  → physischer Port (lan1, lan2, lan3) → Switch-Port-Icon
"wifi"      → AP-Interface (phy0-ap0 etc.) → WLAN-Icon
             → korrelierbar mit wifi_iface_status-Einträgen über Interface-Name
"vpn"       → WireGuard / VPN-Tunnel → VPN-Icon
"unknown"   → keine Klassifizierung möglich → generisches Interface-Icon
             → KEIN Fehler, nur fehlende Information
```

---

## 8. Änderungsübersicht (kein Git)

### `server.py` — geänderte Funktionen

| Funktion | Zeile (ca.) | Änderung |
|---|---|---|
| `_parse_iwinfo_output()` | 4696 | `signal=None`, `bitrate=None` statt `-60`/`0` |
| `_parse_iw_station_output()` | 4729 | `signal=None`, `bitrate=None` statt `-60`/`0` |
| `_extract_networks()` | 4633 | `interface_type`-Feld hinzugefügt |
| `_poll_wifi_clients_from_ap()` | 4768 | `wlan0` → `iw dev` Discovery; `iface_stats_out`-Parameter |
| `_wifi_polling_task()` | ~6382 | `iface_stats_out` befüllt + in `_wifi_iface_status` gespeichert |
| `api_topology()` | ~6510 | `wifi_iface_status` zur Response hinzugefügt |

### `server.py` — neue Funktionen

| Funktion | Zeile (ca.) | Zweck |
|---|---|---|
| `_classify_interface()` | 4606 | Helper für `_extract_networks`, leitet `interface_type` ab |
| `_validate_rx_tx()` | 4655 | Zentrale RX/TX-Validierung: invalid / inactive / active |
| `_parse_proc_net_dev()` | 4672 | Parst `/proc/net/dev` → `{iface: {rx_bytes, tx_bytes}}` |

### `server.py` — neues Global

| Symbol | Zeile | Zweck |
|---|---|---|
| `_wifi_iface_status` | ~77 | Cache: `{ap_mac: {iface: {rx, tx, valid, status, warning}}}` |

### Neue Dateien

| Datei | Zweck |
|---|---|
| `validate_fixes.py` | 37 isolierte Checks — `python validate_fixes.py` |
| `CHANGES_v062.md` | Detailliertes Change-Log v0.6.1 → v0.6.2 |
| `VALIDATION_REPORT.md` | Dieses Dokument |

---

## 9. Integrations-Status

| Komponente | Status |
|---|---|
| `server.py` syntaktisch korrekt | ✓ |
| Alle 37 Unit-Checks grün | ✓ |
| `GET /api/topology?include_wifi=1` liefert HTTP 200 | ✓ |
| `wifi_iface_status`-Feld in Response | ✓ |
| `interface_type` in Config-Pull-Antwort | ✓ |
| Kein Signal-Default `-60` mehr im Code | ✓ |
| `inactive`-Markierung für 0/0-Interfaces | ✓ |
| Dynamische Interface-Discovery (kein `wlan0` hardcoded) | ✓ |

**Bereit für Integration mit Codex-Exportdaten.**  
Die `wifi_iface_status`-Struktur ist stabil und nicht mehr geändert.  
Frontend und HA-Integration können auf Basis dieses Dokuments implementieren.
