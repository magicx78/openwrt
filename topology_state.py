"""
topology_state.py — Shared mutable WiFi state (v0.6.2+)

Single authoritative location for the two live-polling dicts.
Both dicts are imported by server.py (alias) and topology_mapper.py (direct).
Mutating a key works cross-module because all references point to the same object.
"""
from __future__ import annotations

from typing import Any, Dict

# {ap_mac: {client_mac: {signal, bitrate, connected, last_seen}}}
wifi_clients: Dict[str, Dict[str, Dict[str, Any]]] = {}

# {ap_mac: {iface_name: {rx_bytes, tx_bytes, valid, status, warning}}}
wifi_iface_status: Dict[str, Dict[str, Any]] = {}
