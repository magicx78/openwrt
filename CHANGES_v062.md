# server.py — Change Log v0.6.1 → v0.6.2

**Scope:** `server.py` only. No new endpoints. No API-breaking changes.  
**Validated:** `python validate_fixes.py` — 37/37 checks, exit 0.  
**Runtime test:** Local uvicorn start + live `GET /api/topology?include_wifi=1` — response confirmed.

---

## Changed Functions

### `_extract_networks(parsed)` — Fix C: Interface Type Classification

Added `interface_type` field to every returned interface dict.

| Name / Proto / Device | interface_type |
|---|---|
| `wan`, `wan6`, or `proto=dhcpv6` | `uplink` |
| `proto=wireguard` or name starts `wg` | `vpn` |
| name matches `lan\d+` (lan1, lan2, lan3) | `lan_port` |
| device matches `br-lan.\d+` | `lan` |
| device matches `phy\d+-ap\d+` | `wifi` |
| `proto=static` + `br-lan` in device, or name in `lan`/`iot` | `lan` |
| anything else | `unknown` |

No existing fields removed. Non-breaking addition.

---

### `_parse_iwinfo_output(output)` — Fix B: No Invented Defaults

`signal` and `bitrate` start as `None`. Only overwritten when parsing succeeds.  
Previously both started at `-60` / `0` — a client that produced no data looked like
a weak client. Now it correctly looks like an unknown client.

---

### `_parse_iw_station_output(output)` — Fix B: No Invented Defaults

Same change. Initial client dict: `'signal': None, 'bitrate': None`.  
Previously: `'signal': -60, 'bitrate': 0`.

---

### `_poll_wifi_clients_from_ap(...)` — Fix A + Fix E

**Fix A — Dynamic interface discovery:**
- Removed hardcoded `wlan0`.
- First SSH call: `iw dev | awk '/Interface/{print $2}'` — discovers real interfaces.
- All subsequent iwinfo/iw commands iterate over discovered interfaces.
- Single compound shell command per method (semicolons + `__WIFIFACE__` sentinel)
  to avoid N separate SSH round-trips for N interfaces.

**Fix E — Interface activity (new optional parameter):**
```python
iface_stats_out: Optional[Dict[str, Any]] = None   # backwards-compatible
```
When provided:
- Reads `/proc/net/dev` via SSH.
- Calls `_validate_rx_tx()` per discovered interface.
- Writes `{iface: {rx_bytes, tx_bytes, valid, status, warning}}` into the dict.

**Return contract unchanged:**
- `{client_mac: {...}}` on success
- `{}` on connect-OK / no clients found
- `None` on SSH failure (caller keeps stale data)

---

## New Functions

### `_classify_interface(name, proto, device) → str` — Fix C helper
Pure function. No side effects. Used only by `_extract_networks`.

### `_validate_rx_tx(rx, tx) → Tuple[bool, Optional[str]]` — Fix D

| Input | Returns |
|---|---|
| `None, None` | `(True, None)` — no data, not invalid |
| either `< 0` | `(False, "invalid_negative")` |
| both `== 0` | `(True, "inactive")` |
| valid non-zero | `(True, None)` |

### `_parse_proc_net_dev(output) → Dict[str, Dict[str, int]]` — Fix D helper
Parses `/proc/net/dev` text output → `{iface: {rx_bytes, tx_bytes}}`.

---

## Changed Callers

### `_wifi_polling_task()` — Fix E wiring
Passes `iface_stats_out={}` to each `_poll_wifi_clients_from_ap()` call.  
Stores result in `_wifi_iface_status[ap_mac]` when non-empty.

### `api_topology()` — Fix E response field
`include_wifi=True` now adds a third key alongside the existing two:
```
wifi_clients       — unchanged
wifi_last_update   — unchanged
wifi_iface_status  — NEW: {ap_mac: {iface: {rx_bytes, tx_bytes, valid, status, warning}}}
```

---

## New Globals

```python
_wifi_iface_status: Dict[str, Dict[str, Any]] = {}
# {ap_mac: {iface_name: {rx_bytes, tx_bytes, valid, status, warning}}}
```

---

## Typing

`Tuple` added to `from typing import ...`. No other import changes.

---

## Confirmed Real Response — `GET /api/topology?include_wifi=1`

Tested against local uvicorn (port 18000) with 3 seeded devices.  
WiFi fields empty because no SSH key is present in local test DB (expected, no crash).

```json
{
  "devices": [
    {
      "id": "e4:95:6e:40:01:01",
      "label": "cudy-wr3000-gw",
      "type": "router",
      "role": "ap1",
      "status": "provisioned",
      "ip": "10.10.10.1",
      "color": "#4fc3f7",
      "borderColor": "#10b981"
    },
    {
      "id": "e4:95:6e:40:02:01",
      "label": "cudy-mesh-node1",
      "type": "ap",
      "role": "node",
      "status": "provisioned",
      "ip": "10.10.10.2",
      "color": "#81c784",
      "borderColor": "#10b981"
    },
    {
      "id": "e4:95:6e:40:03:01",
      "label": "cudy-pending",
      "type": "ap",
      "role": "node",
      "status": "pending",
      "ip": null,
      "color": "#81c784",
      "borderColor": "#f59e0b"
    }
  ],
  "edges": [
    { "id": "e4:95:6e:40:01:01--e4:95:6e:40:02:01", "from": "e4:95:6e:40:01:01", "to": "e4:95:6e:40:02:01" },
    { "id": "e4:95:6e:40:01:01--e4:95:6e:40:03:01", "from": "e4:95:6e:40:01:01", "to": "e4:95:6e:40:03:01" }
  ],
  "projects": ["home"],
  "timestamp": "2026-04-07T22:28:53.777309+00:00",
  "wifi_clients": {},
  "wifi_last_update": {},
  "wifi_iface_status": {}
}
```

### Expected production response (once polling reaches real AP)

```json
"wifi_iface_status": {
  "e4:95:6e:40:01:01": {
    "phy1-ap0":  { "rx_bytes": 2900000000, "tx_bytes": 7500000000, "valid": true,  "status": "active",   "warning": null },
    "phy0-ap2":  { "rx_bytes": 95000000,   "tx_bytes": 800000000,  "valid": true,  "status": "active",   "warning": null },
    "phy0-ap3":  { "rx_bytes": 0,          "tx_bytes": 256,        "valid": true,  "status": "active",   "warning": null },
    "br-lan.30": { "rx_bytes": 0,          "tx_bytes": 0,          "valid": true,  "status": "inactive", "warning": "inactive" }
  }
}
```

---

## Known Limitations

### `iw station dump` signal brackets
`int('-65, -68')` raises `ValueError` — multi-chain signal lines produce `signal=null`.
Fix B is correct: `null` (unknown) instead of the old `-60` (invented).
Future fix: `int(line.split('[')[1].split(',')[0])`. Out of scope for this change set.

### `inactive` rule is strict `0/0`
`phy0-ap3` with 0 RX / 256 TX is **not** inactive — 256 TX bytes exist (beacons/IGMP).
Only pure `0/0` triggers `"inactive"`. This is intentional.

### WiFi polling requires SSH key in DB
`_poll_wifi_clients_from_ap` uses `_get_saved_ssh_key()` from the DB settings table.
Without a key, SSH falls back to empty-password auth. Failed polls keep stale data.

### Config-pull path has no live RX/TX
`_extract_networks()` classifies interfaces from UCI config only.
No `/proc/net/dev` is read during config pull — `interface_type` is static, not live.

### Polling interval
Interface discovery and `/proc/net/dev` read happen every 15 s (default `_WIFI_POLLING_INTERVAL_SECONDS`).
Data is always one polling cycle behind real state.

---

## Frontend / HA Consumer Guidance

### Consuming `wifi_clients`
```
signal: null  → show as "?" / "--" — do NOT substitute -60
bitrate: null → show as "?" / "--" — do NOT substitute 0
```

### Consuming `wifi_iface_status`
```
status == "active"            → normal, show RX/TX counters
status == "inactive"          → 0/0 — mark interface grey / hidden, do NOT error
valid  == false               → invalid_negative — flag as data error
status == "active", rx == 0   → low-traffic (beacons only) — no special treatment
```

### Consuming `interface_type` (config-pull response)
```
"uplink"   → WAN — show as internet uplink icon
"lan"      → bridge VLAN — show as LAN segment
"lan_port" → physical port — show as switch port
"wifi"     → AP virtual interface — correlate with wifi_iface_status
"vpn"      → WireGuard tunnel
"unknown"  → no classification possible — show as generic interface
```

---

## Deliverables

| File | Purpose |
|------|---------|
| `server.py` | All 5 fixes applied |
| `validate_fixes.py` | 37 standalone checks — `python validate_fixes.py` |
| `CHANGES_v062.md` | This file — complete change documentation |
