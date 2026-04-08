"""
topology_mapper.py — Topology business logic (v0.6.2+)

Extracted from server.py without any semantic change.
Callers: server.py route handlers (api_topology_snapshot, api_topology_graph, api_topology).
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import topology_state

TOPOLOGY_SCHEMA_VERSION = "1.0"
TOPOLOGY_SOURCE = "openwrt-runtime"


def _classify_interface(name: str, proto: str, device: str) -> str:
    """Derive interface_type from UCI name, proto, and device.

    Returns one of: uplink, lan, lan_port, wifi, vpn, unknown
    """
    # WAN uplinks
    if name in ("wan", "wan6") or proto == "dhcpv6":
        return "uplink"
    # VPN / WireGuard
    if proto == "wireguard" or name.startswith("wg"):
        return "vpn"
    # Physical LAN ports named lan1, lan2, lan3 …
    if re.match(r'^lan\d+$', name):
        return "lan_port"
    # WiFi AP virtual interfaces (phy0-ap0, phy1-ap1, …)
    if device and re.match(r'^phy\d+-ap\d+$', device):
        return "wifi"
    # VLAN-bridged LANs (device = br-lan.X)
    if device and re.match(r'^br-lan\.\d+$', device):
        return "lan"
    # Standard LAN bridge or static LAN (lan, iot, …)
    if proto == "static" and ("br-lan" in device or name in ("lan", "iot")):
        return "lan"
    return "unknown"


def _device_node_type(role: str) -> str:
    if role == "ap1":
        return "router"
    if role in ("node", "repeater"):
        return "ap"
    return "client"


def _build_topology_snapshot(
    db: sqlite3.Connection,
    include_wifi: bool = True,
    source_snapshot: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build canonical topology snapshot from openwrt runtime data."""
    devices = db.execute("""
        SELECT base_mac, hostname, role, project, status, last_ip, board_name, model, claimed, last_seen
        FROM devices
        ORDER BY project, CASE role WHEN 'ap1' THEN 0 ELSE 1 END, hostname
    """).fetchall()

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    interfaces: List[Dict[str, Any]] = []
    clients: List[Dict[str, Any]] = []

    for d in devices:
        dev_id = d["base_mac"]
        nodes.append({
            "id": dev_id,
            "type": _device_node_type(d["role"] or ""),
            "label": d["hostname"] or dev_id[:12],
            "source": "openwrt.devices",
            "inferred": False,
            "status": d["status"],
            "project": d["project"],
            "role": d["role"],
            "ip": d["last_ip"],
            "attributes": {
                "board_name": d["board_name"],
                "model": d["model"],
                "claimed": d["claimed"],
                "last_seen": d["last_seen"],
            },
        })

    if include_wifi:
        for ap_mac, iface_map in topology_state.wifi_iface_status.items():
            if not isinstance(iface_map, dict):
                continue
            for iface_name, iface_data in iface_map.items():
                iface_type = _classify_interface(iface_name, "", iface_name)
                iface_id = f"iface:{ap_mac}:{iface_name}"
                valid = iface_data.get("valid", True)
                status = iface_data.get("status", "unknown")
                warning = iface_data.get("warning")
                inferred = iface_type == "unknown"

                interfaces.append({
                    "id": iface_id,
                    "ap_mac": ap_mac,
                    "name": iface_name,
                    "interface_type": iface_type,
                    "rx_bytes": iface_data.get("rx_bytes"),
                    "tx_bytes": iface_data.get("tx_bytes"),
                    "valid": valid,
                    "status": status,
                    "warning": warning,
                    "source": "openwrt.wifi_iface_status",
                    "inferred": inferred,
                    "inference_reason": "interface_type_unknown" if inferred else None,
                })

                nodes.append({
                    "id": iface_id,
                    "type": "interface",
                    "label": iface_name,
                    "source": "openwrt.wifi_iface_status",
                    "inferred": inferred,
                    "inference_reason": "interface_type_unknown" if inferred else None,
                    "status": status,
                    "valid": valid,
                    "attributes": {
                        "ap_mac": ap_mac,
                        "interface_type": iface_type,
                        "rx_bytes": iface_data.get("rx_bytes"),
                        "tx_bytes": iface_data.get("tx_bytes"),
                        "warning": warning,
                    },
                })
                edges.append({
                    "id": f"{ap_mac}--{iface_id}",
                    "from": ap_mac,
                    "to": iface_id,
                    "relationship": "has_interface",
                    "source": "openwrt.wifi_iface_status",
                    "inferred": False,
                })

                ssid_id = f"ssid:{ap_mac}:{iface_name}:unknown"
                nodes.append({
                    "id": ssid_id,
                    "type": "ssid",
                    "label": f"SSID ? ({iface_name})",
                    "source": "openwrt.wifi_iface_status",
                    "inferred": True,
                    "inference_reason": "ssid_missing_from_runtime_data",
                    "status": status,
                    "attributes": {
                        "ap_mac": ap_mac,
                        "interface_name": iface_name,
                        "interface_type": iface_type,
                    },
                })
                edges.append({
                    "id": f"{iface_id}--{ssid_id}",
                    "from": iface_id,
                    "to": ssid_id,
                    "relationship": "broadcasts_ssid",
                    "source": "openwrt.wifi_iface_status",
                    "inferred": True,
                    "inference_reason": "ssid_missing_from_runtime_data",
                })

        for ap_mac, ap_clients in topology_state.wifi_clients.items():
            if not isinstance(ap_clients, dict):
                continue
            for client_mac, info in ap_clients.items():
                client_id = f"client:{client_mac.lower()}"
                signal = info.get("signal")
                bitrate = info.get("bitrate")
                clients.append({
                    "id": client_id,
                    "mac": client_mac.lower(),
                    "ap_mac": ap_mac,
                    "signal": signal,
                    "bitrate": bitrate,
                    "connected": info.get("connected"),
                    "last_seen": info.get("last_seen"),
                    "source": "openwrt.wifi_clients",
                    "inferred": True,
                    "inference_reason": "ssid_and_interface_unknown_for_wifi_client",
                })
                ssid_unknown = next(
                    (n["id"] for n in nodes if n["type"] == "ssid" and n["attributes"].get("ap_mac") == ap_mac),
                    ap_mac,
                )
                nodes.append({
                    "id": client_id,
                    "type": "client",
                    "label": client_mac.lower(),
                    "source": "openwrt.wifi_clients",
                    "inferred": True,
                    "inference_reason": "ssid_and_interface_unknown_for_wifi_client",
                    "status": "active",
                    "attributes": {
                        "ap_mac": ap_mac,
                        "signal": signal,
                        "bitrate": bitrate,
                        "connected": info.get("connected"),
                        "last_seen": info.get("last_seen"),
                    },
                })
                edges.append({
                    "id": f"{ssid_unknown}--{client_id}",
                    "from": ssid_unknown,
                    "to": client_id,
                    "relationship": "has_client",
                    "source": "openwrt.wifi_clients",
                    "inferred": True,
                    "inference_reason": "ssid_and_interface_unknown_for_wifi_client",
                })

    if isinstance(source_snapshot, dict):
        for n in source_snapshot.get("nodes", []):
            if isinstance(n, dict):
                nodes.append({**n, "helper_only": True})
        for e in source_snapshot.get("edges", []):
            if isinstance(e, dict):
                edges.append({**e, "helper_only": True})

    inference_used = any(bool(x.get("inferred")) for x in [*nodes, *edges, *interfaces, *clients])
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "nodes": nodes,
        "edges": edges,
        "interfaces": interfaces,
        "clients": clients,
        "meta": {
            "source": TOPOLOGY_SOURCE,
            "schema_version": TOPOLOGY_SCHEMA_VERSION,
            "inference_used": inference_used,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "interface_count": len(interfaces),
            "client_count": len(clients),
        },
    }
