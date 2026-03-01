#!/usr/bin/env python3
"""
OpenWrt Minimal Provisioning Server – FINAL VERSION
FastAPI + SQLite | Projekte | Validierung | Push-Deploy | Script-Generator
"""

import asyncio, hashlib, hmac as _hmac, json, os, re, shutil, sqlite3, subprocess, secrets, threading, time
from collections import namedtuple
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

try:
    import paramiko as _paramiko
    _HAS_PARAMIKO = True
except ImportError:
    _HAS_PARAMIKO = False

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

__version__ = "0.4.5"

# ─────────────────────────────────────────────────────────────────────────────
# Provisioning Diagnose (Server + optional Router read-only)
# ─────────────────────────────────────────────────────────────────────────────

class DiagnoseCheck(BaseModel):
    id: str
    status: str  # OK/WARN/FAIL
    summary: str
    details: Optional[str] = None


class DiagnoseSection(BaseModel):
    id: str
    title: str
    status: str  # OK/WARN/FAIL
    checks: List[DiagnoseCheck]


class DiagnoseReport(BaseModel):
    report_id: str
    mac: str
    created_at: str  # ISO UTC
    overall_status: str  # OK/WARN/FAIL
    sections: List[DiagnoseSection]
    config_sha256: Optional[str] = None
    config_hmac_ok: Optional[bool] = None
    downloads: Dict[str, str] = {}


_diag_reports: Dict[str, Dict[str, Any]] = {}  # report_id -> {report, config}

# ─────────────────────────────────────────────────────────────────────────────
# Datetime-Helpers: intern immer UTC timezone-aware
# ─────────────────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    """Aktueller Zeitpunkt als timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def parse_dt_utc(s: str) -> datetime:
    """Parst einen ISO-8601-String zu einem UTC-aware datetime.
    Strings ohne Timezone-Info (Legacy-DB-Einträge) werden als UTC interpretiert."""
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ─────────────────────────────────────────────────────────────────────────────
# Konfiguration – per Umgebungsvariable überschreibbar
# ─────────────────────────────────────────────────────────────────────────────
DB_PATH          = os.getenv("DB_PATH",          "provision.db")
ENROLLMENT_TOKEN = os.getenv("ENROLLMENT_TOKEN", "CHANGE_ME_TOKEN_1234")
ADMIN_USER       = os.getenv("ADMIN_USER",       "admin")
ADMIN_PASS       = os.getenv("ADMIN_PASS",       "changeme")
HMAC_SECRET      = os.getenv("HMAC_SECRET",      "CHANGE_ME_HMAC_SECRET")

security = HTTPBasic()

# ─────────────────────────────────────────────────────────────────────────────
# Datenbank
# ─────────────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY, value TEXT
    );
    CREATE TABLE IF NOT EXISTS roles (
        name TEXT PRIMARY KEY, description TEXT, overrides TEXT
    );
    CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE, content TEXT, updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS projects (
        name TEXT PRIMARY KEY, description TEXT,
        created_at TEXT, settings TEXT DEFAULT '{}'
    );
    CREATE TABLE IF NOT EXISTS devices (
        base_mac   TEXT PRIMARY KEY,
        hostname   TEXT,
        role       TEXT DEFAULT 'node',
        board_name TEXT,
        model      TEXT,
        last_seen  TEXT,
        claimed    INTEGER DEFAULT 0,
        project    TEXT DEFAULT 'default',
        notes      TEXT,
        override   TEXT,
        status     TEXT DEFAULT 'pending',
        last_log   TEXT,
        last_ip    TEXT
    );
    """)

    # Globale Defaults
    for k,v in {
        "MGMT_NET":"192.168.10","GW":"192.168.10.1","DNS":"192.168.10.89",
        "SSID":"WWW.PC2HELP.DE","WPA_PSK":"Luna20152015",
        "ENABLE_11R":"1","ENABLE_MESH":"0",
        "MESH_ID":"securemesh","MESH_PSK":"MeshSecret",
        "MGMT_VLAN":"10",
        "SSH_PRIVKEY":"",
    }.items():
        c.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))

    # Rollen
    for name,desc,ov in [
        ("ap1","🏠 Master-AP – DHCP-Server, Gateway, DNS aktiv",
         "# ap1: DHCP-Server einschalten\nset dhcp.lan.ignore='0'\nset dhcp.lan.start='11'\nset dhcp.lan.limit='249'\n"),
        ("node","📡 Client-AP – kein DHCP, hängt am Master",
         "# node: DHCP-Server aus\nset dhcp.lan.ignore='1'\n"),
        ("repeater","🔁 Repeater / Mesh-Leaf – kein DHCP, Uplink per Mesh",
         "# repeater: DHCP aus, kein WAN\nset dhcp.lan.ignore='1'\ndelete network.wan\ndelete network.wan6\n"),
    ]:
        c.execute("INSERT OR IGNORE INTO roles(name,description,overrides) VALUES(?,?,?)",(name,desc,ov))

    # Master-Template (generisch)
    master = _MASTER_TEMPLATE
    c.execute("INSERT OR IGNORE INTO templates(name,content,updated_at) VALUES(?,?,?)",
              ("master", master, now_utc().isoformat()))

    # Privat-Template (vollständig mit allen 6 WLANs)
    c.execute("INSERT OR IGNORE INTO templates(name,content,updated_at) VALUES(?,?,?)",
              ("sECUREaP-privat", _PRIVATE_TEMPLATE, now_utc().isoformat()))

    # Projekte
    now = now_utc().isoformat()
    c.execute("INSERT OR IGNORE INTO projects(name,description,created_at,settings) VALUES(?,?,?,?)",
        ("default","📦 Standard-Projekt",now,json.dumps({
            "MGMT_NET":"192.168.50","GW":"192.168.50.1","DNS":"192.168.50.1",
            "SSID":"MyNetwork","WPA_PSK":"SuperSecret123",
            "ENABLE_11R":"1","ENABLE_MESH":"0","template":"master"
        })))
    c.execute("INSERT OR IGNORE INTO projects(name,description,created_at,settings) VALUES(?,?,?,?)",
        ("sECUREaP-privat","🏠 Privat – sECUREaP | 4x VLAN | 6x WLAN | WireGuard",now,json.dumps({
            "MGMT_NET":"192.168.10","GW":"192.168.10.1","DNS":"192.168.10.89",
            "SSID":"WWW.PC2HELP.DE","WPA_PSK":"Luna20152015",
            "ENABLE_11R":"1","ENABLE_MESH":"0",
            "MESH_ID":"securemesh","MESH_PSK":"MeshSecret",
            "template":"sECUREaP-privat"
        })))
    # Migration: last_ip ggf. nachträglich hinzufügen (bestehende DBs ohne Spalte)
    try:
        conn.execute("ALTER TABLE devices ADD COLUMN last_ip TEXT")
    except Exception:
        pass  # Spalte existiert bereits – kein Problem
    conn.commit(); conn.close()

# ─────────────────────────────────────────────────────────────────────────────
# Templates als Konstanten
# ─────────────────────────────────────────────────────────────────────────────
_MASTER_TEMPLATE = """\
# ═══════════════════════════════════════════════════
# 📋 MASTER TEMPLATE – generisch für alle Projekte
# Variablen: {{HOSTNAME}} {{MGMT_NET}} {{MGMT_SUFFIX}}
#            {{GW}} {{DNS}} {{SSID}} {{WPA_PSK}}
#            {{ENABLE_11R}} {{MOBILITY_DOMAIN}}
#            {{MESH_BLOCK}} {{WLAN_BLOCK}}
#            {{NETWORKS_BLOCK}}
#            {{NET_LAN_IP}} {{NET_LAN_VLAN}} {{NET_LAN_MASK}}
#            {{NET_MEDIA_IP}} {{NET_GUEST_IP}} etc.
# ═══════════════════════════════════════════════════

# 🖥️ System
set system.@system[0].hostname='{{HOSTNAME}}'
set system.@system[0].timezone='CET-1CEST,M3.5.0,M10.5.0/3'
set system.@system[0].zonename='Europe/Berlin'
set system.@system[0].log_size='64'

# 🌐 Netzwerk
set network.loopback=interface
set network.loopback.device='lo'
set network.loopback.proto='static'
set network.loopback.ipaddr='127.0.0.1'
set network.loopback.netmask='255.0.0.0'

set network.lan=interface
set network.lan.device='br-lan'
set network.lan.proto='static'
set network.lan.ipaddr='{{MGMT_NET}}.{{MGMT_SUFFIX}}'
set network.lan.netmask='255.255.255.0'
set network.lan.gateway='{{GW}}'
add_list network.lan.dns='{{DNS}}'

# 📡 WLAN-Konfiguration (aus Projekt-WLANs generiert)
{{WLAN_BLOCK}}

# 🕸️ Mesh (nur wenn ENABLE_MESH=1)
{{MESH_BLOCK}}

# 🔥 Firewall
set firewall.@defaults[0].input='ACCEPT'
set firewall.@defaults[0].output='ACCEPT'
set firewall.@defaults[0].forward='REJECT'
set firewall.@defaults[0].synflood_protect='1'

# 📦 DHCP (Rolle überschreibt ignore)
set dhcp.lan=dhcp
set dhcp.lan.interface='lan'
set dhcp.lan.start='100'
set dhcp.lan.limit='150'
set dhcp.lan.leasetime='12h'
set dhcp.lan.ignore='1'
"""

_PRIVATE_TEMPLATE = """\
# ╔══════════════════════════════════════════════════════════════════╗
# ║  PROJEKT: sECUREaP-gATEWAy  –  Privat-Setup                   ║
# ║                                                                 ║
# ║  AUTOMATISCHE VARIABLEN (pro Gerät):                           ║
# ║    {{HOSTNAME}}        → z.B. ap-042 (aus MAC berechnet)       ║
# ║    {{MGMT_SUFFIX}}     → letztes IP-Oktett z.B. 42             ║
# ║    {{MOBILITY_DOMAIN}} → für 802.11r (aus SSID berechnet)      ║
# ║    {{MESH_BLOCK}}      → Mesh-Config wenn ENABLE_MESH=1        ║
# ║                                                                 ║
# ║  ⚠️  Script läuft nur EINMAL – /etc/provisioned schützt davor  ║
# ╚══════════════════════════════════════════════════════════════════╝

# ════════════════════════════════════════════════════════════════
# 🖥️ SYSTEM
# ════════════════════════════════════════════════════════════════
set system.@system[0].hostname='{{HOSTNAME}}'
set system.@system[0].timezone='CET-1CEST,M3.5.0,M10.5.0/3'
set system.@system[0].zonename='Europe/Berlin'
set system.@system[0].ttylogin='0'
set system.@system[0].log_size='64'
set system.@system[0].description='sECUREaP Node'
set system.@system[0].log_proto='udp'
set system.@system[0].conloglevel='8'
set system.@system[0].cronloglevel='5'

# 🕐 NTP
add_list system.ntp.server='0.openwrt.pool.ntp.org'
add_list system.ntp.server='1.openwrt.pool.ntp.org'
add_list system.ntp.server='2.openwrt.pool.ntp.org'
add_list system.ntp.server='3.openwrt.pool.ntp.org'

# ════════════════════════════════════════════════════════════════
# 🌐 NETZWERK – Bridge + 4x VLANs
# ════════════════════════════════════════════════════════════════
set network.loopback=interface
set network.loopback.device='lo'
set network.loopback.proto='static'
set network.loopback.ipaddr='127.0.0.1'
set network.loopback.netmask='255.0.0.0'

# IPv6 ULA – einheitlich für alle APs (kein random!)
set network.globals=globals
set network.globals.ula_prefix='fd0e:4105:48ed::/48'

# br-lan Bridge mit allen Ports
# lan1/lan2/lan3 = physisch | br-lan.X = VLAN-Subinterfaces
set network.br_lan=device
set network.br_lan.name='br-lan'
set network.br_lan.type='bridge'
set network.br_lan.bridge_empty='1'
add_list network.br_lan.ports='lan1'
add_list network.br_lan.ports='lan2'
add_list network.br_lan.ports='lan3'
add_list network.br_lan.ports='br-lan.1'
add_list network.br_lan.ports='br-lan.10'
add_list network.br_lan.ports='br-lan.20'
add_list network.br_lan.ports='br-lan.30'
add_list network.br_lan.ports='br-lan.40'

# ── VLAN 10: LAN/Management ──────────────────────────────────────
# IP: 192.168.10.{{MGMT_SUFFIX}} (pro Gerät aus MAC)
# DNS: AdGuard auf 192.168.10.89
set network.lan=interface
set network.lan.device='br-lan.10'
set network.lan.proto='static'
set network.lan.ipaddr='192.168.10.{{MGMT_SUFFIX}}'
set network.lan.netmask='255.255.255.0'
set network.lan.ip6assign='60'
set network.lan.ip4table='1'
set network.lan.defaultroute='0'
add_list network.lan.dns='192.168.10.89'

# ── VLAN 20: Media ───────────────────────────────────────────────
# Chromecast, TV, Streaming → darf ins LAN (MediaFW)
set network.Media=interface
set network.Media.proto='static'
set network.Media.device='br-lan.20'
set network.Media.ipaddr='192.168.20.1'
set network.Media.netmask='255.255.255.0'
set network.Media.delegate='0'

# ── VLAN 30: Works/VPN ───────────────────────────────────────────
# ⚠️  BREAKING CHANGE NOTICE (v0.3.0):
#     UCI-Schnittstellenname "Worls" ist ein historischer Tippfehler für "Works".
#     Eine Umbenennung würde alle bereits provisionierten Geräte brechen,
#     da der UCI-Name in deren Flash gespeichert ist.
#     Dokumentiert als known issue. Nicht umbenennen ohne vollständiges Re-Flash.
# Arbeits-Netz + WireGuard VPN
set network.Worls=interface
set network.Worls.proto='static'
set network.Worls.device='br-lan.30'
set network.Worls.ipaddr='192.168.30.1'
set network.Worls.netmask='255.255.255.0'
set network.Worls.delegate='0'

# ── VLAN 40: Guest ───────────────────────────────────────────────
# Gäste: Internet ja, LAN nein (GuestFW forward=REJECT)
set network.Guest=interface
set network.Guest.proto='static'
set network.Guest.device='br-lan.40'
set network.Guest.ipaddr='192.168.40.1'
set network.Guest.netmask='255.255.255.0'
set network.Guest.delegate='0'
set network.Guest.defaultroute='0'
add_list network.Guest.dns='192.168.10.89'

# Bridge-VLAN Trunk-Zuweisungen (:t=tagged, ohne=untagged)
set network.brvlan10=bridge-vlan
set network.brvlan10.device='br-lan'
set network.brvlan10.vlan='10'
add_list network.brvlan10.ports='br-lan.10:t'
add_list network.brvlan10.ports='lan1'
add_list network.brvlan10.ports='lan2'
add_list network.brvlan10.ports='lan3:t'

set network.brvlan20=bridge-vlan
set network.brvlan20.device='br-lan'
set network.brvlan20.vlan='20'
add_list network.brvlan20.ports='br-lan.20:t'
add_list network.brvlan20.ports='lan3:t'

set network.brvlan30=bridge-vlan
set network.brvlan30.device='br-lan'
set network.brvlan30.vlan='30'
add_list network.brvlan30.ports='br-lan.30:t'
add_list network.brvlan30.ports='lan3:t'

set network.brvlan40=bridge-vlan
set network.brvlan40.device='br-lan'
set network.brvlan40.vlan='40'
add_list network.brvlan40.ports='br-lan.40:t'
add_list network.brvlan40.ports='lan3:t'

# WAN: IP per DHCP vom Provider/Fritzbox
set network.wan=interface
set network.wan.device='wan'
set network.wan.proto='dhcp'
set network.wan.ip4table='default'
set network.wan.ip6assign='64'

set network.wan6=interface
set network.wan6.device='wan'
set network.wan6.proto='dhcpv6'

# ════════════════════════════════════════════════════════════════
# 📡 WLAN 2.4 GHz – radio0
# Chip: platform/18000000.wifi | HT40 | DE | auto-channel
# ════════════════════════════════════════════════════════════════
set wireless.radio0=wifi-device
set wireless.radio0.type='mac80211'
set wireless.radio0.path='platform/18000000.wifi'
set wireless.radio0.band='2g'
set wireless.radio0.htmode='HT40'
set wireless.radio0.country='DE'
set wireless.radio0.cell_density='1'
set wireless.radio0.channel='auto'
set wireless.radio0.disabled='0'

# ── wifinet0: 🌐 Works/VPN-WLAN (2.4G → VLAN30) ─────────────────
# Arbeits-Netz | psk-mixed (WPA2/3) | kein Roaming nötig
set wireless.wifinet0=wifi-iface
set wireless.wifinet0.device='radio0'
set wireless.wifinet0.mode='ap'
set wireless.wifinet0.ssid='<#>--<sECURe>--<#> -[- _ - ]-'
set wireless.wifinet0.encryption='psk-mixed'
set wireless.wifinet0.key='Cs16051979$'
set wireless.wifinet0.network='Worls'
set wireless.wifinet0.ieee80211w='0'
set wireless.wifinet0.wpa_disable_eapol_key_retries='1'
set wireless.wifinet0.disabled='0'

# ── wifinet1: 🏠 Haupt-WLAN (2.4G → VLAN10/LAN) ─────────────────
# Primäres Heimnetz | SAE-mixed (WPA2+WPA3) | WDS | 802.11r+k+v
set wireless.wifinet1=wifi-iface
set wireless.wifinet1.device='radio0'
set wireless.wifinet1.mode='ap'
set wireless.wifinet1.ssid='WWW.PC2HELP.DE'
set wireless.wifinet1.encryption='sae-mixed'
set wireless.wifinet1.key='Luna20152015'
set wireless.wifinet1.network='lan'
set wireless.wifinet1.wds='1'
set wireless.wifinet1.ieee80211r='{{ENABLE_11R}}'
set wireless.wifinet1.mobility_domain='{{MOBILITY_DOMAIN}}'
set wireless.wifinet1.ft_over_ds='0'
set wireless.wifinet1.reassociation_deadline='1000'
set wireless.wifinet1.ieee80211k='1'
set wireless.wifinet1.ieee80211v='1'
set wireless.wifinet1.bss_transition='1'
set wireless.wifinet1.disabled='0'

# ── wifinet2: 🔌 IoT-WLAN (2.4G → LAN) ──────────────────────────
# Shelly, ESPHome, Sensoren | psk-mixed für alte ESP8266 | kein Roaming
set wireless.wifinet2=wifi-iface
set wireless.wifinet2.device='radio0'
set wireless.wifinet2.mode='ap'
set wireless.wifinet2.ssid='secure-IoT'
set wireless.wifinet2.encryption='psk-mixed'
set wireless.wifinet2.key='Cs16051979$'
set wireless.wifinet2.network='lan'
set wireless.wifinet2.disabled='0'

# ── wifinet4: 👥 Gäste-WLAN (2.4G → VLAN40) ─────────────────────
# ⚠️ DEAKTIVIERT – bei Bedarf disabled auf '0' setzen
# Gäste: Internet ja | LAN/IoT/Media/Works: NEIN (GuestFW)
set wireless.wifinet4=wifi-iface
set wireless.wifinet4.device='radio0'
set wireless.wifinet4.mode='ap'
set wireless.wifinet4.ssid='<#>--<sECURe>--<#>--<gUESt>--<#>'
set wireless.wifinet4.encryption='sae-mixed'
set wireless.wifinet4.key='20252025'
set wireless.wifinet4.network='Guest'
set wireless.wifinet4.disabled='1'

# ── wifinet5: 🔒 Sicheres IoT WPA3 (2.4G → LAN) ─────────────────
# Neuere Geräte mit WPA3-Support | MFP required (ieee80211w=2)
set wireless.wifinet5=wifi-iface
set wireless.wifinet5.device='radio0'
set wireless.wifinet5.mode='ap'
set wireless.wifinet5.ssid='<#>--<sECURe>--<#>--<iOt>--<#>'
set wireless.wifinet5.encryption='sae-mixed'
set wireless.wifinet5.key='Cs16051979$'
set wireless.wifinet5.network='lan'
set wireless.wifinet5.ieee80211w='2'
set wireless.wifinet5.wpa_disable_eapol_key_retries='1'
set wireless.wifinet5.disabled='0'

# ════════════════════════════════════════════════════════════════
# 📡 WLAN 5 GHz – radio1
# Chip: platform/18000000.wifi+1 | HE160 (WiFi6) | DE | auto
# ════════════════════════════════════════════════════════════════
set wireless.radio1=wifi-device
set wireless.radio1.type='mac80211'
set wireless.radio1.path='platform/18000000.wifi+1'
set wireless.radio1.band='5g'
set wireless.radio1.htmode='HE160'
set wireless.radio1.country='DE'
set wireless.radio1.cell_density='1'
set wireless.radio1.channel='auto'
set wireless.radio1.disabled='0'

# ── wifinet3: 🎬 Media-WLAN (5G → LAN) ──────────────────────────
# Chromecast, TV, Apple TV | 5GHz für Bandbreite | 802.11r Roaming
set wireless.wifinet3=wifi-iface
set wireless.wifinet3.device='radio1'
set wireless.wifinet3.mode='ap'
set wireless.wifinet3.ssid='<#>--<sECURe>--<#>--<mEDIa>--<#>'
set wireless.wifinet3.encryption='sae-mixed'
set wireless.wifinet3.key='16051979Cs$'
set wireless.wifinet3.network='lan'
set wireless.wifinet3.ieee80211r='{{ENABLE_11R}}'
set wireless.wifinet3.mobility_domain='{{MOBILITY_DOMAIN}}'
set wireless.wifinet3.nasid='A002'
set wireless.wifinet3.ft_over_ds='0'
set wireless.wifinet3.reassociation_deadline='1000'
set wireless.wifinet3.ieee80211k='1'
set wireless.wifinet3.ieee80211v='1'
set wireless.wifinet3.bss_transition='1'
set wireless.wifinet3.disabled='0'

# ════════════════════════════════════════════════════════════════
# 🕸️ MESH (802.11s + batman-adv)
# Wird durch {{MESH_BLOCK}} ersetzt wenn ENABLE_MESH=1
# Benötigt: kmod-batman-adv batctl wpad-wolfssl im Image
# ════════════════════════════════════════════════════════════════
{{MESH_BLOCK}}

# ════════════════════════════════════════════════════════════════
# 🔥 FIREWALL
# lan=VLAN10 | wan | WorksFW=VLAN30 | GuestFW=VLAN40 | MediaFW=VLAN20
# ════════════════════════════════════════════════════════════════
set firewall.@defaults[0].input='ACCEPT'
set firewall.@defaults[0].output='ACCEPT'
set firewall.@defaults[0].forward='ACCEPT'
set firewall.@defaults[0].synflood_protect='1'

# LAN: volles Vertrauen
set firewall.lan=zone
set firewall.lan.name='lan'
set firewall.lan.input='ACCEPT'
set firewall.lan.output='ACCEPT'
set firewall.lan.forward='ACCEPT'
add_list firewall.lan.network='lan'
add_list firewall.lan.network='default'

# WAN: kein unerwünschter Eingang
set firewall.wan=zone
set firewall.wan.name='wan'
set firewall.wan.input='REJECT'
set firewall.wan.output='ACCEPT'
set firewall.wan.forward='ACCEPT'
set firewall.wan.masq='1'
set firewall.wan.mtu_fix='1'
add_list firewall.wan.network='wan'
add_list firewall.wan.network='wan6'

# Works: darf ins LAN + WAN + Media (für VPN)
set firewall.WorksFW=zone
set firewall.WorksFW.name='WorksFW'
set firewall.WorksFW.input='ACCEPT'
set firewall.WorksFW.output='ACCEPT'
set firewall.WorksFW.forward='ACCEPT'
add_list firewall.WorksFW.network='Worls'
add_list firewall.WorksFW.network='VPN'

# Guest: nur WAN – kein LAN/IoT
set firewall.GuestFW=zone
set firewall.GuestFW.name='GuestFW'
set firewall.GuestFW.input='ACCEPT'
set firewall.GuestFW.output='ACCEPT'
set firewall.GuestFW.forward='REJECT'
add_list firewall.GuestFW.network='Guest'

# Media: darf ins LAN (Chromecast braucht LAN)
set firewall.MediaFW=zone
set firewall.MediaFW.name='MediaFW'
set firewall.MediaFW.input='ACCEPT'
set firewall.MediaFW.output='ACCEPT'
set firewall.MediaFW.forward='REJECT'
add_list firewall.MediaFW.network='Media'

# ════════════════════════════════════════════════════════════════
# 📦 DHCP
# ap1-Rolle: ignore=0 (DHCP an) | node/repeater: ignore=1 (aus)
# Rollen-Override überschreibt diese Werte automatisch
# ════════════════════════════════════════════════════════════════
set dhcp.lan=dhcp
set dhcp.lan.interface='lan'
set dhcp.lan.start='11'
set dhcp.lan.limit='249'
set dhcp.lan.leasetime='12h'
set dhcp.lan.dhcpv4='server'
set dhcp.lan.dhcpv6='server'
set dhcp.lan.ra='server'
add_list dhcp.lan.ra_flags='managed-config'
add_list dhcp.lan.ra_flags='other-config'
set dhcp.lan.ignore='1'

set dhcp.Media=dhcp
set dhcp.Media.interface='Media'
set dhcp.Media.start='11'
set dhcp.Media.limit='250'
set dhcp.Media.leasetime='12h'

set dhcp.Guest=dhcp
set dhcp.Guest.interface='Guest'
set dhcp.Guest.start='11'
set dhcp.Guest.limit='111'
set dhcp.Guest.leasetime='12h'

set dhcp.Worls=dhcp
set dhcp.Worls.interface='Worls'
set dhcp.Worls.start='100'
set dhcp.Worls.limit='150'
set dhcp.Worls.leasetime='12h'

set dhcp.wan=dhcp
set dhcp.wan.interface='wan'
set dhcp.wan.ignore='1'
"""

# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen
# ─────────────────────────────────────────────────────────────────────────────
def check_admin(credentials: HTTPBasicCredentials = Depends(security)):
    ok = (secrets.compare_digest(credentials.username.encode(), ADMIN_USER.encode()) and
          secrets.compare_digest(credentials.password.encode(), ADMIN_PASS.encode()))
    if not ok:
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username

def get_settings(db) -> dict:
    return {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings").fetchall()}

def mobility_domain(ssid: str) -> str:
    return hashlib.md5(ssid.encode()).hexdigest()[:4].upper()

def mac_suffix(mac: str) -> int:
    clean = mac.replace(":","").replace("-","")
    return (int(clean[-4:], 16) % 253) + 2

def sign_payload(payload: str) -> str:
    return _hmac.new(HMAC_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()

def build_wlan_block(wlans: list, mob_domain: str, enable_11r: str) -> str:
    """Generiert UCI-Befehle fuer alle WLANs aus dem wlans-Array."""
    lines = []
    iface_idx = 0
    for w in wlans:
        ssid = w.get("ssid","")
        if not ssid:
            continue
        psk        = w.get("psk","")
        band       = w.get("band","2g+5g")
        enc        = w.get("encryption","sae-mixed")
        vlan       = w.get("vlan","lan")
        r11        = w.get("r80211", enable_11r)
        enabled    = w.get("enabled","1")
        bands = []
        if band in ("2g","2g+5g"): bands.append(("radio0","2.4GHz"))
        if band in ("5g","2g+5g"): bands.append(("radio1","5GHz"))
        for radio, freq in bands:
            name = f"wifinet{iface_idx}"
            lines.append(f"# WLAN: {w.get('label',ssid)} ({freq})")
            lines.append(f"set wireless.{name}=wifi-iface")
            lines.append(f"set wireless.{name}.device=\'{radio}\'")
            lines.append(f"set wireless.{name}.mode=\'ap\'")
            lines.append(f"set wireless.{name}.ssid=\'{ssid}\'")
            lines.append(f"set wireless.{name}.encryption=\'{enc}\'")
            if psk and enc != "none":
                lines.append(f"set wireless.{name}.key=\'{psk}\'")
            lines.append(f"set wireless.{name}.network=\'{vlan}\'")
            if r11 == "1":
                lines.append(f"set wireless.{name}.ieee80211r=\'1\'")
                lines.append(f"set wireless.{name}.mobility_domain=\'{mob_domain}\'")
                lines.append(f"set wireless.{name}.ft_over_ds=\'0\'")
                lines.append(f"set wireless.{name}.reassociation_deadline=\'1000\'")
                lines.append(f"set wireless.{name}.ieee80211k=\'1\'")
                lines.append(f"set wireless.{name}.ieee80211v=\'1\'")
            lines.append(f"set wireless.{name}.disabled=\'{0 if enabled=='1' else 1}\'")
            iface_idx += 1
    return "\n".join(lines) if lines else "# Keine WLANs konfiguriert"

def build_vars(settings: dict, mac: str, hostname: str) -> dict:
    suffix = mac_suffix(mac)
    ssid   = settings.get("SSID","network")
    mob    = mobility_domain(ssid)
    enable_11r = settings.get("ENABLE_11R","1")
    if settings.get("ENABLE_MESH","0") == "1":
        mesh_id  = settings.get("MESH_ID","mymesh")
        mesh_psk = settings.get("MESH_PSK","secret")
        mesh_lines = [
            "set wireless.mesh0=wifi-iface",
            "set wireless.mesh0.device='radio1'",
            "set wireless.mesh0.mode='mesh'",
            f"set wireless.mesh0.mesh_id='{mesh_id}'",
            "set wireless.mesh0.mesh_fwding='0'",
            "set wireless.mesh0.encryption='sae'",
            f"set wireless.mesh0.key='{mesh_psk}'",
            "set wireless.mesh0.network='bat0_if'",
            "set network.bat0_if=interface",
            "set network.bat0_if.proto='batadv'",
            "set network.bat0_if.mesh='bat0'"
        ]
        mesh = "\n".join(mesh_lines)
    else:
        mesh = "# Mesh deaktiviert (ENABLE_MESH=0)"
    wlans = settings.get("wlans", [])
    wlan_block = build_wlan_block(wlans, mob, enable_11r) if wlans else ""
    # Netzwerk-Variablen aus settings["networks"] ableiten
    networks = settings.get("networks", {})
    net_vars: dict = {}
    net_lines: list = []
    for nname, net in networks.items():
        pfx = f"NET_{nname.upper()}"
        ip = net.get("ipaddr", "").replace("X", str(suffix))
        net_vars[f"{pfx}_IP"]    = ip
        net_vars[f"{pfx}_VLAN"]  = net.get("vlan", "")
        net_vars[f"{pfx}_PROTO"] = net.get("proto", "static")
        net_vars[f"{pfx}_MASK"]  = net.get("netmask", "255.255.255.0")
        net_vars[f"{pfx}_GW"]    = net.get("gateway", "")
        if net.get("proto", "static") == "static" and ip:
            net_lines += [
                f"set network.{nname}=interface",
                f"set network.{nname}.proto='static'",
                f"set network.{nname}.ipaddr='{ip}'",
                f"set network.{nname}.netmask='{net.get('netmask','255.255.255.0')}'",
            ]
            if net.get("gateway"):
                net_lines.append(f"set network.{nname}.gateway='{net['gateway']}'")
    networks_block = "\n".join(net_lines) if net_lines else "# Keine statischen Interfaces konfiguriert"
    return {**settings,
            "MGMT_SUFFIX":     str(suffix),
            "HOSTNAME":        hostname,
            "MOBILITY_DOMAIN": mob,
            "MESH_BLOCK":      mesh,
            "WLAN_BLOCK":      wlan_block,
            "NETWORKS_BLOCK":  networks_block,
            **net_vars}

def render_template(content: str, vars_: dict, role_override: str,
                    device_override: Optional[str]) -> str:
    result = content
    for k, v in vars_.items():
        result = result.replace("{{"+k+"}}", str(v))
    result = re.sub(r"\{\{[^}]+\}\}", "", result)
    result += "\n# ── Rollen-Override ──\n" + (role_override or "")
    if device_override:
        result += "\n# ── Geräte-Override ──\n" + device_override
    return result

def validate_template(content: str) -> list:
    """Gibt Liste von Warnungen/Fehlern zurück."""
    issues = []
    lines = content.strip().splitlines()
    valid_cmds = ("set ", "add_list ", "delete ", "#")
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped and not any(stripped.startswith(c) for c in valid_cmds):
            issues.append(f"⚠️ Zeile {i}: Unbekannter Befehl: <code>{stripped[:60]}</code>")
    unresolved = re.findall(r"\{\{[^}]+\}\}", content)
    if unresolved:
        issues.append(f"⚠️ Ungelöste Variablen: {', '.join(set(unresolved))}")
    if "set wireless." in content and "wifi-iface" not in content:
        issues.append("⚠️ WLAN: wifi-device ohne wifi-iface?")
    if content.count("set dhcp.lan.ignore='0'") == 0 and "role_override" not in content:
        issues.append("ℹ️ DHCP: ignore wird durch Rollen-Override gesetzt – ok wenn Rolle ap1/node/repeater genutzt.")
    return issues


def _status_rank(s: str) -> int:
    return {"OK": 0, "WARN": 1, "FAIL": 2}.get(s, 2)


def _worst_status(statuses: List[str]) -> str:
    if not statuses:
        return "OK"
    worst = max(statuses, key=_status_rank)
    return worst


def _short(s: str, limit: int = 900) -> str:
    s = (s or "").strip()
    return s if len(s) <= limit else (s[:limit] + "\n…(gekürzt)…")


def _render_template_diag(content: str, vars_: dict, role_override: str,
                          device_override: Optional[str]) -> tuple:
    """Diagnose-Render: gibt (rendered, unresolved_placeholders_set) zurück.
    Anders als render_template() werden ungelöste {{VAR}} NICHT still gelöscht."""
    result = content
    for k, v in vars_.items():
        result = result.replace("{{" + k + "}}", str(v))
    unresolved = set(re.findall(r"\{\{[^}]+\}\}", result))
    result += "\n# ── Rollen-Override ──\n" + (role_override or "")
    if device_override:
        result += "\n# ── Geräte-Override ──\n" + device_override
    return result, unresolved


def _parse_json_if_jsonish(raw: Optional[str]) -> tuple:
    """(ok, parsed_or_none, err_or_none). Nur wenn es nach JSON aussieht."""
    if raw is None:
        return True, None, None
    s = str(raw).strip()
    if not s:
        return True, None, None
    if not (s.startswith("{") or s.startswith("[")):
        return True, None, "not_json"
    try:
        return True, json.loads(s), None
    except Exception as e:
        return False, None, f"{type(e).__name__}: {e}"


def _validate_wlans(settings: dict) -> List[DiagnoseCheck]:
    checks: List[DiagnoseCheck] = []
    wlans = settings.get("wlans", []) or []
    if not isinstance(wlans, list):
        return [DiagnoseCheck(id="server.wlan.schema", status="FAIL",
                              summary="wlans ist kein Array", details=_short(repr(wlans)))]

    allowed_band = {"2g", "5g", "2g+5g"}
    allowed_enc = {"sae-mixed", "psk-mixed", "sae", "none"}

    bad = []
    for i, w in enumerate(wlans):
        if not isinstance(w, dict):
            bad.append(f"wlans[{i}] ist kein Objekt")
            continue
        band = str(w.get("band", "2g+5g"))
        enc = str(w.get("encryption", "sae-mixed"))
        enabled = str(w.get("enabled", "1"))
        if band not in allowed_band:
            bad.append(f"wlans[{i}].band='{band}' ungültig")
        if enc not in allowed_enc:
            bad.append(f"wlans[{i}].encryption='{enc}' ungültig")
        if enabled not in {"0", "1"}:
            bad.append(f"wlans[{i}].enabled='{enabled}' muss 0/1 sein")
        if not str(w.get("ssid", "")).strip():
            bad.append(f"wlans[{i}].ssid fehlt")

    if bad:
        checks.append(DiagnoseCheck(
            id="server.wlan.consistency",
            status="FAIL",
            summary="WLAN-Definitionen sind inkonsistent",
            details=_short("\n".join(bad)),
        ))
    else:
        checks.append(DiagnoseCheck(
            id="server.wlan.consistency",
            status="OK",
            summary=f"wlans[] konsistent ({len(wlans)} WLAN(s))" if wlans else "Keine wlans[] definiert (Legacy/Template-hardcoded möglich)",
        ))

    # Rückwärtskompatibilität: SSID/WPA_PSK/ENABLE_11R sollten aus wlans[0] passen
    if wlans:
        first = wlans[0]
        mism = []
        if settings.get("SSID", "") != first.get("ssid", ""):
            mism.append("SSID")
        if settings.get("WPA_PSK", "") != first.get("psk", ""):
            mism.append("WPA_PSK")
        if settings.get("ENABLE_11R", "") != first.get("r80211", settings.get("ENABLE_11R", "")):
            mism.append("ENABLE_11R")
        if mism:
            checks.append(DiagnoseCheck(
                id="server.wlan.legacy",
                status="WARN",
                summary="Legacy-Felder weichen von wlans[0] ab",
                details=_short("Abweichungen: " + ", ".join(mism)),
            ))
        else:
            checks.append(DiagnoseCheck(
                id="server.wlan.legacy",
                status="OK",
                summary="Legacy-Felder (SSID/WPA_PSK/ENABLE_11R) sind mit wlans[0] synchron",
            ))
    return checks


def build_server_diagnose(mac: str, db: sqlite3.Connection) -> tuple:
    """Erzeugt (report, rendered_config)."""
    mac = mac.lower().strip()
    created = now_utc().isoformat()
    checks_cfg: List[DiagnoseCheck] = []
    checks_sec: List[DiagnoseCheck] = []

    d = db.execute("SELECT * FROM devices WHERE base_mac=?", (mac,)).fetchone()
    if not d:
        sec = DiagnoseSection(
            id="server_config",
            title="Server: Konfiguration",
            status="FAIL",
            checks=[DiagnoseCheck(id="server.device.exists", status="FAIL",
                                 summary="Device nicht in DB", details=mac)],
        )
        report_id = secrets.token_hex(8)
        report = DiagnoseReport(report_id=report_id, mac=mac, created_at=created,
                                overall_status="FAIL", sections=[sec])
        return report, ""

    # claimed? project? role?
    checks_cfg.append(DiagnoseCheck(
        id="server.device.claimed",
        status="OK" if int(d["claimed"] or 0) == 1 else "WARN",
        summary="Device ist claimed" if int(d["claimed"] or 0) == 1 else "Device ist nicht claimed (ok für Pre-Deploy)",
    ))
    if not (d["project"] or "").strip():
        checks_cfg.append(DiagnoseCheck(id="server.device.project", status="FAIL",
                                        summary="Kein Projekt am Device gesetzt"))
        project = ""
    else:
        project = d["project"]
        checks_cfg.append(DiagnoseCheck(id="server.device.project", status="OK",
                                        summary=f"Projekt: {project}"))
    role = d["role"] or ""
    if not role:
        checks_cfg.append(DiagnoseCheck(id="server.device.role", status="FAIL",
                                        summary="Keine Rolle am Device gesetzt"))
    else:
        checks_cfg.append(DiagnoseCheck(id="server.device.role", status="OK",
                                        summary=f"Rolle: {role}"))

    # JSON-Felder
    proj_row = db.execute("SELECT settings FROM projects WHERE name=?", (project,)).fetchone() if project else None
    ok, proj_s, err = (False, None, "Projekt fehlt")
    if proj_row:
        ok, proj_s, err = _parse_json_if_jsonish(proj_row["settings"])
        if err == "not_json":
            # projects.settings ist immer JSON, also ist das hier ein FAIL
            ok = False
            err = "projects.settings ist kein JSON"
    if not ok:
        checks_cfg.append(DiagnoseCheck(id="server.db.projects.settings", status="FAIL",
                                        summary="projects.settings kaputt", details=_short(str(err))))
        proj_s = {}
    else:
        checks_cfg.append(DiagnoseCheck(id="server.db.projects.settings", status="OK",
                                        summary="projects.settings JSON ok"))

    ok_r, _, err_r = _parse_json_if_jsonish(db.execute(
        "SELECT overrides FROM roles WHERE name=?", (role,)).fetchone()["overrides"]
    ) if role else (True, None, None)
    if not ok_r:
        checks_cfg.append(DiagnoseCheck(id="server.db.roles.overrides", status="FAIL",
                                        summary="roles.overrides JSON kaputt", details=_short(str(err_r))))
    else:
        checks_cfg.append(DiagnoseCheck(id="server.db.roles.overrides", status="OK",
                                        summary="roles.overrides ok (script oder JSON)"))

    ok_d, _, err_d = _parse_json_if_jsonish(d["override"])
    if not ok_d:
        checks_cfg.append(DiagnoseCheck(id="server.db.devices.override", status="FAIL",
                                        summary="devices.override JSON kaputt", details=_short(str(err_d))))
    else:
        checks_cfg.append(DiagnoseCheck(id="server.db.devices.override", status="OK",
                                        summary="devices.override ok (script oder JSON)"))

    # Template + Rendering
    glob_s = get_settings(db)
    merged = {**glob_s, **(proj_s or {})}
    tmpl_name = (proj_s or {}).get("template", "master")
    tmpl_row = db.execute("SELECT content FROM templates WHERE name=?", (tmpl_name,)).fetchone()
    if not tmpl_row:
        checks_cfg.append(DiagnoseCheck(id="server.template.exists", status="FAIL",
                                        summary=f"Template fehlt: {tmpl_name}"))
        tmpl_content = ""
    else:
        checks_cfg.append(DiagnoseCheck(id="server.template.exists", status="OK",
                                        summary=f"Template gefunden: {tmpl_name}"))
        tmpl_content = tmpl_row["content"] or ""

    role_row = db.execute("SELECT overrides FROM roles WHERE name=?", (role,)).fetchone() if role else None
    role_override = role_row["overrides"] if role_row else ""

    vars_ = build_vars(merged, mac, d["hostname"])
    rendered, unresolved = _render_template_diag(tmpl_content, vars_, role_override, d["override"])

    if unresolved:
        checks_cfg.append(DiagnoseCheck(id="server.template.vars", status="FAIL",
                                        summary="Ungelöste Template-Variablen",
                                        details=_short(", ".join(sorted(unresolved)))))
    else:
        checks_cfg.append(DiagnoseCheck(id="server.template.vars", status="OK",
                                        summary="Alle Template-Variablen ersetzt"))

    issues = validate_template(rendered)
    bad_lines = [x for x in issues if "⚠️ Zeile" in x or "⚠️ Ungelöste" in x]
    info_lines = [x for x in issues if x not in bad_lines]
    if bad_lines:
        checks_cfg.append(DiagnoseCheck(id="server.template.validate", status="FAIL",
                                        summary="validate_template: Fehler",
                                        details=_short("\n".join(bad_lines + info_lines))))
    elif info_lines:
        checks_cfg.append(DiagnoseCheck(id="server.template.validate", status="WARN",
                                        summary="validate_template: Hinweise",
                                        details=_short("\n".join(info_lines))))
    else:
        checks_cfg.append(DiagnoseCheck(id="server.template.validate", status="OK",
                                        summary="validate_template: OK"))

    # WLAN checks
    checks_cfg.extend(_validate_wlans(merged))

    # MOBILITY_DOMAIN + Mesh
    mob = vars_.get("MOBILITY_DOMAIN", "")
    if re.fullmatch(r"[0-9A-F]{4}", str(mob or "")):
        checks_cfg.append(DiagnoseCheck(id="server.mobility_domain", status="OK",
                                        summary=f"MOBILITY_DOMAIN berechnet: {mob}"))
    else:
        checks_cfg.append(DiagnoseCheck(id="server.mobility_domain", status="FAIL",
                                        summary="MOBILITY_DOMAIN ungültig", details=_short(str(mob))))

    mesh_enabled = str(merged.get("ENABLE_MESH", "0")) == "1"
    mesh_block = vars_.get("MESH_BLOCK", "") or ""
    if mesh_enabled and "mesh" not in mesh_block.lower():
        checks_cfg.append(DiagnoseCheck(id="server.mesh_block", status="FAIL",
                                        summary="ENABLE_MESH=1 aber MESH_BLOCK wirkt leer"))
    elif (not mesh_enabled) and "set wireless.mesh" in mesh_block:
        checks_cfg.append(DiagnoseCheck(id="server.mesh_block", status="FAIL",
                                        summary="MESH_BLOCK enthält Mesh obwohl ENABLE_MESH=0"))
    else:
        checks_cfg.append(DiagnoseCheck(id="server.mesh_block", status="OK",
                                        summary="MESH_BLOCK konsistent"))

    # Security: HMAC Roundtrip
    sig = sign_payload(rendered)
    sig2 = sign_payload(rendered)
    hmac_ok = secrets.compare_digest(sig, sig2)
    checks_sec.append(DiagnoseCheck(
        id="server.hmac.roundtrip",
        status="OK" if hmac_ok else "FAIL",
        summary="HMAC sign+verify Roundtrip OK" if hmac_ok else "HMAC Roundtrip fehlgeschlagen",
    ))

    cfg_hash = hashlib.sha256(rendered.encode("utf-8", errors="replace")).hexdigest()
    checks_sec.append(DiagnoseCheck(id="server.config.hash", status="OK",
                                    summary="Config SHA256 berechnet", details=cfg_hash))

    sec_cfg = DiagnoseSection(
        id="server_config",
        title="Server: Konfiguration",
        status=_worst_status([c.status for c in checks_cfg]),
        checks=checks_cfg,
    )
    sec_sec = DiagnoseSection(
        id="server_security",
        title="Server: Security",
        status=_worst_status([c.status for c in checks_sec]),
        checks=checks_sec,
    )

    report_id = secrets.token_hex(8)
    overall = _worst_status([sec_cfg.status, sec_sec.status])
    report = DiagnoseReport(
        report_id=report_id,
        mac=mac,
        created_at=created,
        overall_status=overall,
        sections=[sec_cfg, sec_sec],
        config_sha256=cfg_hash,
        config_hmac_ok=hmac_ok,
        downloads={
            "json": f"/api/diagnose/report/{report_id}.json",
            "text": f"/api/diagnose/report/{report_id}.txt",
            "config": f"/api/diagnose/report/{report_id}.config",
        },
    )
    return report, rendered


def _parse_df_overlay_free_mb(df_out: str) -> tuple:
    """Parst 'df -h' und liefert (found, free_mb, line)."""
    line = ""
    for l in (df_out or "").splitlines():
        if l.strip().endswith(" /overlay") or " /overlay" in l:
            line = l
    if not line:
        return False, None, ""
    parts = [p for p in line.split() if p]
    if len(parts) < 4:
        return True, None, line
    free = parts[3]
    m = re.fullmatch(r"([0-9.]+)([KMGTP])", free)
    if not m:
        return True, None, line
    val = float(m.group(1))
    unit = m.group(2)
    mult = {"K": 1/1024, "M": 1.0, "G": 1024.0, "T": 1024.0*1024, "P": 1024.0*1024*1024}[unit]
    return True, val * mult, line


def _ssh_build_base_diag(ip: str, user: str, password: str, logline):
    """Diagnose-SSH: strict host key checks, damit 'REMOTE HOST IDENTIFICATION HAS CHANGED' sichtbar wird."""
    has_sshpass = shutil.which("sshpass") is not None
    opts = [
        "-o", "StrictHostKeyChecking=yes",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
    ]
    if has_sshpass and password:
        logline(f"[{_ts()}] Nutze sshpass (Diagnose, StrictHostKeyChecking=yes)...")
        return ["sshpass", f"-p{password}", "ssh"] + opts + [f"{user}@{ip}"]

    # paramiko: enforce known_hosts, detect changed key as FAIL
    if _HAS_PARAMIKO and password:
        logline(f"[{_ts()}] Nutze paramiko (Diagnose, Strict HostKey Policy)...")
        return _ParamikoAuth(ip, user, password)

    logline(f"[{_ts()}] ⚠️  sshpass nicht gefunden – Diagnose via SSH-Key-Auth...")
    return ["ssh"] + opts + [f"{user}@{ip}"]


def _ssh_exec_diag(base_ssh, remote_cmd: str, timeout: int = 15) -> tuple:
    """Wie _ssh_exec(), aber paramiko mit RejectPolicy + system host keys."""
    if isinstance(base_ssh, _ParamikoAuth):
        client = _paramiko.SSHClient()
        try:
            client.load_system_host_keys()
        except Exception:
            pass
        client.set_missing_host_key_policy(_paramiko.RejectPolicy())
        try:
            client.connect(base_ssh.ip, username=base_ssh.user, password=base_ssh.password,
                           timeout=timeout, allow_agent=False, look_for_keys=False)
            _, stdout_ch, stderr_ch = client.exec_command(remote_cmd, timeout=timeout)
            stdout = stdout_ch.read().decode("utf-8", errors="replace")[:_MAX_CMD_OUTPUT]
            stderr = stderr_ch.read().decode("utf-8", errors="replace")[:_MAX_CMD_OUTPUT]
            rc = stdout_ch.channel.recv_exit_status()
            return rc, stdout, stderr
        except _paramiko.BadHostKeyException as e:
            # simulate typical ssh message
            return 255, "", f"REMOTE HOST IDENTIFICATION HAS CHANGED: {e}"
        except TimeoutError:
            raise subprocess.TimeoutExpired(remote_cmd, timeout)
        finally:
            client.close()
    return _ssh_exec(base_ssh, remote_cmd, timeout=timeout)


def build_router_diagnose(ip: str, user: str, password: str) -> List[DiagnoseSection]:
    conn_checks: List[DiagnoseCheck] = []
    cap_checks: List[DiagnoseCheck] = []
    log = []
    def logline(x):
        log.append(x)

    base = _ssh_build_base_diag(ip, user, password, logline)

    def run_check(target: List[DiagnoseCheck], cid: str, title: str, cmd: str, ok_if_missing: bool = False) -> tuple:
        try:
            rc, out, err = _ssh_exec_diag(base, cmd, timeout=12)
            combined = (out + err).strip()
            # hostkey mismatch detection
            if rc == 255 and "REMOTE HOST IDENTIFICATION HAS CHANGED" in combined:
                conn_checks.append(DiagnoseCheck(
                    id="router.ssh.hostkey",
                    status="FAIL",
                    summary="SSH Host-Key mismatch",
                    details=_short(
                        "Der Host-Key in known_hosts passt nicht zum Router. "
                        "Fix: alten Key entfernen (z.B. 'ssh-keygen -R IP') und neu verbinden.\n\n" + combined
                    ),
                ))
                return False, combined
            if rc != 0 and not ok_if_missing:
                target.append(DiagnoseCheck(id=cid, status="WARN",
                                            summary=f"{title}: non-zero exit ({rc})",
                                            details=_short(combined)))
            else:
                target.append(DiagnoseCheck(id=cid, status="OK",
                                            summary=f"{title}: OK",
                                            details=_short(combined)))
            return True, combined
        except subprocess.TimeoutExpired:
            target.append(DiagnoseCheck(id=cid, status="FAIL",
                                        summary=f"{title}: Timeout",
                                        details="SSH-Command Timeout"))
            return False, ""
        except Exception as e:
            target.append(DiagnoseCheck(id=cid, status="FAIL",
                                        summary=f"{title}: Fehler",
                                        details=_short(f"{type(e).__name__}: {e}")))
            return False, ""

    # connectivity: minimal command
    ok, _ = run_check(conn_checks, "router.ssh.connect", "SSH Verbindung", "echo DIAG_OK")
    conn_sec = DiagnoseSection(
        id="router_connectivity",
        title="Router: Connectivity",
        status=_worst_status([c.status for c in conn_checks]) if conn_checks else "FAIL",
        checks=conn_checks,
    )
    if not ok:
        return [conn_sec]

    # capability commands
    run_check(cap_checks, "router.uname", "uname", "uname -a")
    _, osrel = run_check(cap_checks, "router.os_release", "os-release", "cat /etc/openwrt_release || cat /etc/os-release")
    run_check(cap_checks, "router.busybox", "busybox", "busybox --help 2>&1 | head -n 1 || true", ok_if_missing=True)
    run_check(cap_checks, "router.id", "id", "id")
    run_check(cap_checks, "router.uci", "uci", "which uci && uci -h 2>&1 | head -n 1")
    run_check(cap_checks, "router.ubus", "ubus", "ubus -V 2>&1 | head -n 1 || true", ok_if_missing=True)
    run_check(cap_checks, "router.wifi", "wifi status", "wifi status 2>&1 | head -n 50 || true", ok_if_missing=True)
    _, dfout = run_check(cap_checks, "router.df", "df", "df -h")
    run_check(cap_checks, "router.logread", "logread", "logread -e hostapd -e wpa_supplicant -e netifd 2>/dev/null | tail -n 50 || true", ok_if_missing=True)

    # OpenWrt detection
    if "openwrt" in (osrel or "").lower():
        cap_checks.append(DiagnoseCheck(id="router.openwrt", status="OK", summary="OpenWrt erkannt"))
    else:
        cap_checks.append(DiagnoseCheck(id="router.openwrt", status="WARN", summary="OpenWrt nicht eindeutig erkannt"))

    # free space /overlay
    found, free_mb, line = _parse_df_overlay_free_mb(dfout)
    if not found:
        cap_checks.append(DiagnoseCheck(id="router.overlay.space", status="WARN",
                                        summary="/overlay nicht in df -h gefunden"))
    elif free_mb is None:
        cap_checks.append(DiagnoseCheck(id="router.overlay.space", status="WARN",
                                        summary="/overlay free space nicht parsebar",
                                        details=_short(line)))
    else:
        if free_mb < 0.8:
            st = "FAIL"
            summ = f"/overlay zu voll: {free_mb:.2f} MB frei (<0.8 MB)"
        elif free_mb < 5.0:
            st = "WARN"
            summ = f"/overlay knapp: {free_mb:.2f} MB frei"
        else:
            st = "OK"
            summ = f"/overlay free: {free_mb:.2f} MB"
        cap_checks.append(DiagnoseCheck(id="router.overlay.space", status=st, summary=summ, details=_short(line)))

    cap_sec = DiagnoseSection(
        id="router_capabilities",
        title="Router: Capabilities",
        status=_worst_status([c.status for c in cap_checks]),
        checks=cap_checks,
    )
    return [conn_sec, cap_sec]

# ─────────────────────────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(); yield

app = FastAPI(title="OpenWrt Provisioning Server", lifespan=lifespan)

# ─────────────────────────────────────────────────────────────────────────────
# SSH-Push: Hintergrund-Jobs (subprocess+sshpass, paramiko oder SSH-Key-Auth)
# ─────────────────────────────────────────────────────────────────────────────

# Auth-Deskriptoren für paramiko-basierte Verbindungen (kein sshpass/subprocess)
_ParamikoAuth    = namedtuple("_ParamikoAuth",    ["ip", "user", "password"])
_ParamikoKeyAuth = namedtuple("_ParamikoKeyAuth", ["ip", "user", "key_content"])
_ssh_jobs: dict = {}  # job_id -> {status, log, done}

def _ts() -> str:
    """Aktueller UTC-Zeitstempel für Loglines (HH:MM:SS UTC)."""
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

# Read-only Checks, die vor einem Deploy ausgeführt werden können.
# Alle Befehle sind idempotent und schreiben NICHTS auf das Gerät.
_PRECHECK_CMDS = [
    ("uname",      "uname -a"),
    ("os_release", "cat /etc/openwrt_release || cat /etc/os-release || true"),
    # busybox ohne Argumente gibt Usage + RC 1; --help 2>&1 ist robuster
    ("busybox",    "busybox --help 2>&1 | head -n 1 || true"),
    ("id",         "id"),
    # uci -V ist bei manchen Versionen kein gültiger Befehl;
    # --help 2>&1 ist read-only und zeigt, ob uci vorhanden ist
    ("uci",        "which uci && uci --help 2>&1 | head -n 1 || echo 'uci missing'"),
    ("df",         "df -h | head"),
    ("ip",         "ip a | head -n 50 || ifconfig | head -n 50 || true"),
]

_MAX_CMD_OUTPUT = 8192  # Bytes – maximale Ausgabe pro SSH-Command im Log

# Strings im Router-Output des Deploy-Exec-Schritts, die auf einen fatalen Fehler
# hinweisen – auch wenn der Exitcode 0 ist (z.B. wenn das Script selbst abbricht
# und der Fehler über stdout kommt). Nur für Schritt 3 (Exec), NICHT für Precheck.
_DEPLOY_FATAL_PATTERNS = [
    # "not found" ist zu weit – OpenWrt uci meldet z.B. "commit: Not found" auch bei
    # OK-Runs. Deshalb nur spezifische Varianten:
    ": not found",       # ash: <cmd>: not found – Befehl/Script nicht gefunden
    "No such file",      # Datei nicht gefunden
    "Permission denied", # Rechteproblem
    "uci: Usage:",       # uci mit ungültigen Argumenten aufgerufen
    "ash: can't open",   # Shell kann Script nicht öffnen
    "provision script not found",  # Fallback-Script-Platzhalter wurde übertragen
]


def _generate_provision_sh(server_url: str, token: str) -> str:
    """Generiert das 99-provision.sh Bootstrap-Script dynamisch.
    Wird von /download/99-provision.sh und /api/setup/quick-ssh genutzt."""
    return f"""#!/bin/sh
# OpenWrt Provisioning Bootstrap – auto-generiert

SERVER="{server_url}"
TOKEN='{token}'

[ -f /etc/provisioned ] && {{ echo "Bereits provisioned – skip"; exit 0; }}

# MAC-Adresse ermitteln (br-lan bevorzugt, dann eth0, dann ip-Befehl)
MAC=$(cat /sys/class/net/br-lan/address 2>/dev/null \\
   || cat /sys/class/net/eth0/address 2>/dev/null \\
   || ip link show | awk '/ether/{{print $2; exit}}')
MAC=$(echo "$MAC" | tr ':' '-' | tr '[:upper:]' '[:lower:]')

BOARD=$(cat /tmp/sysinfo/board_name 2>/dev/null || echo "unknown")
MODEL=$(cat /tmp/sysinfo/model 2>/dev/null || echo "unknown")

echo "MAC: $MAC | Board: $BOARD | Model: $MODEL"
echo "Server: $SERVER"

echo "Claim..."
CLAIM_JSON="{{\\"base_mac\\":\\"$MAC\\",\\"board_name\\":\\"$BOARD\\",\\"model\\":\\"$MODEL\\",\\"token\\":\\"$TOKEN\\"}}"
wget -q -O /tmp/claim.json --header='Content-Type: application/json' --post-data "$CLAIM_JSON" "$SERVER/api/claim"
echo "CLAIM_RC:$?"
[ -s /tmp/claim.json ] && head -n 20 /tmp/claim.json

echo "Config..."
wget -q -O /tmp/provision.uci "$SERVER/api/config/$MAC?token=$TOKEN" 2>/dev/null
echo "CFG_RC:$? SIZE:$(wc -c </tmp/provision.uci 2>/dev/null || echo 0)"

if [ -s /tmp/provision.uci ]; then
  echo "Apply..."
  uci batch < /tmp/provision.uci
  uci commit
  touch /etc/provisioned
  /etc/init.d/network restart 2>/dev/null || true
  echo "OK"
else
  echo "FAIL: Keine Config (nicht provisioned gesetzt!)"
  echo "     Dashboard: $SERVER/ui/ – Projekt zuweisen, dann erneut booten"
  exit 1
fi
"""


def _get_saved_ssh_key() -> str:
    """Liest gespeicherten SSH-Private-Key aus der DB. Gibt '' zurück wenn nicht konfiguriert."""
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        row = conn.execute("SELECT value FROM settings WHERE key='SSH_PRIVKEY'").fetchone()
        conn.close()
        return (row[0] or "").strip() if row else ""
    except Exception:
        return ""


def _build_base_ssh(ip: str, user: str, password: str, logline, key_content: str = ""):
    """Wählt die beste SSH-Auth-Methode.
    Gibt eine Subprocess-Befehlsliste, _ParamikoAuth oder _ParamikoKeyAuth zurück.
    Priorität: gespeicherter SSH-Key (wenn kein Passwort) → sshpass → paramiko → Key-Auth subprocess."""
    if key_content and not password and _HAS_PARAMIKO:
        logline(f"[{_ts()}] Nutze gespeicherten SSH-Key (paramiko Key-Auth)...")
        return _ParamikoKeyAuth(ip, user, key_content)
    has_sshpass = shutil.which("sshpass") is not None
    if has_sshpass and password:
        logline(f"[{_ts()}] Nutze sshpass für Passwort-Auth...")
        return ["sshpass", f"-p{password}", "ssh",
                "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                "-o", "BatchMode=no", f"{user}@{ip}"]
    if _HAS_PARAMIKO and password:
        logline(f"[{_ts()}] Nutze paramiko für Passwort-Auth...")
        return _ParamikoAuth(ip, user, password)
    logline(f"[{_ts()}] ⚠️  sshpass nicht gefunden – versuche SSH-Key-Auth...")
    return ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
            "-o", "BatchMode=no", f"{user}@{ip}"]


def _ssh_exec(base_ssh, remote_cmd: str,
              stdin_data: Optional[bytes] = None, timeout: int = 15) -> tuple:
    """Zentraler SSH-Runner. Gibt (returncode, stdout, stderr) zurück.
    Akzeptiert eine Subprocess-Befehlsliste, _ParamikoAuth oder _ParamikoKeyAuth.
    Ausgaben werden auf _MAX_CMD_OUTPUT Bytes gecappt. Passwort/Key erscheint NICHT im Log."""
    if isinstance(base_ssh, _ParamikoKeyAuth):
        return _ssh_exec_paramiko_key(base_ssh.ip, base_ssh.user, base_ssh.key_content,
                                      remote_cmd, stdin_data, timeout)
    if isinstance(base_ssh, _ParamikoAuth):
        return _ssh_exec_paramiko(base_ssh.ip, base_ssh.user, base_ssh.password,
                                  remote_cmd, stdin_data, timeout)
    result = subprocess.run(
        base_ssh + [remote_cmd],
        input=stdin_data, capture_output=True, timeout=timeout)
    stdout = (result.stdout or b"").decode("utf-8", errors="replace")[:_MAX_CMD_OUTPUT]
    stderr = (result.stderr or b"").decode("utf-8", errors="replace")[:_MAX_CMD_OUTPUT]
    return result.returncode, stdout, stderr


def _ssh_exec_paramiko(ip: str, user: str, password: str, remote_cmd: str,
                       stdin_data: Optional[bytes] = None, timeout: int = 15) -> tuple:
    """SSH-Befehl via paramiko (kein sshpass/subprocess – plattformübergreifend).
    Gibt (returncode, stdout, stderr) zurück. Wirft subprocess.TimeoutExpired bei Timeout."""
    client = _paramiko.SSHClient()
    client.set_missing_host_key_policy(_paramiko.AutoAddPolicy())
    try:
        client.connect(ip, username=user, password=password, timeout=timeout,
                       allow_agent=False, look_for_keys=False)
        stdin_ch, stdout_ch, stderr_ch = client.exec_command(remote_cmd, timeout=timeout)
        if stdin_data:
            stdin_ch.write(stdin_data)
            stdin_ch.flush()
            stdin_ch.channel.shutdown_write()
        stdout = stdout_ch.read().decode("utf-8", errors="replace")[:_MAX_CMD_OUTPUT]
        stderr = stderr_ch.read().decode("utf-8", errors="replace")[:_MAX_CMD_OUTPUT]
        rc = stdout_ch.channel.recv_exit_status()
        return rc, stdout, stderr
    except TimeoutError:
        raise subprocess.TimeoutExpired(remote_cmd, timeout)
    finally:
        client.close()


def _ssh_exec_paramiko_key(ip: str, user: str, key_content: str, remote_cmd: str,
                            stdin_data: Optional[bytes] = None, timeout: int = 15) -> tuple:
    """SSH-Befehl via paramiko mit Private-Key-Auth (kein Passwort).
    Versucht RSA → Ed25519 → ECDSA. Gibt (returncode, stdout, stderr) zurück."""
    if not _HAS_PARAMIKO:
        raise RuntimeError("paramiko nicht installiert – SSH-Key-Auth nicht verfügbar")
    import io as _io
    pkey = None
    for key_class in (_paramiko.RSAKey, _paramiko.Ed25519Key, _paramiko.ECDSAKey):
        try:
            pkey = key_class.from_private_key(_io.StringIO(key_content))
            break
        except Exception:
            continue
    if pkey is None:
        raise ValueError("SSH-Private-Key konnte nicht geladen werden (kein RSA/Ed25519/ECDSA)")
    client = _paramiko.SSHClient()
    client.set_missing_host_key_policy(_paramiko.AutoAddPolicy())
    try:
        client.connect(ip, username=user, pkey=pkey, timeout=timeout,
                       allow_agent=False, look_for_keys=False)
        stdin_ch, stdout_ch, stderr_ch = client.exec_command(remote_cmd, timeout=timeout)
        if stdin_data:
            stdin_ch.write(stdin_data)
            stdin_ch.flush()
            stdin_ch.channel.shutdown_write()
        stdout = stdout_ch.read().decode("utf-8", errors="replace")[:_MAX_CMD_OUTPUT]
        stderr = stderr_ch.read().decode("utf-8", errors="replace")[:_MAX_CMD_OUTPUT]
        rc = stdout_ch.channel.recv_exit_status()
        return rc, stdout, stderr
    except TimeoutError:
        raise subprocess.TimeoutExpired(remote_cmd, timeout)
    finally:
        client.close()


def _run_precheck(base_ssh, logline) -> bool:
    """Führt read-only Precheck-Commands gegen den Router aus.
    Schreibt NICHTS auf das Gerät. Gibt True zurück wenn Deploy starten darf.
    Bei SSH-Fehler oder >2× exit-127 → False (fataler Fehler).
    Fehlendes OpenWrt / fehlendes uci → nur Warnung, kein Abbruch."""
    logline(f"[{_ts()}] 🔍 Starte Precheck (read-only, kein Schreibzugriff)...")
    exit127_count = 0
    openwrt_found = False

    for name, cmd in _PRECHECK_CMDS:
        try:
            rc, stdout, stderr = _ssh_exec(base_ssh, cmd, timeout=12)
            combined = (stdout + stderr).strip()
            short = combined[:300]
            logline(f"[{_ts()}] [{name}] exit={rc} → {short or '(keine Ausgabe)'}")

            if name == "os_release" and "openwrt" in combined.lower():
                openwrt_found = True
            if name == "uci" and "uci missing" in combined.lower():
                logline(f"[{_ts()}] ⚠️  Precheck WARN: uci nicht gefunden (kein Abbruch)")
            if rc == 127:
                exit127_count += 1

        except subprocess.TimeoutExpired:
            logline(f"[{_ts()}] ❌ Precheck Timeout bei '{name}' – Verbindung verloren?")
            logline(f"[{_ts()}] ❌ Precheck FAIL – Deploy abgebrochen")
            return False
        except Exception as e:
            logline(f"[{_ts()}] ❌ Precheck Fehler bei '{name}': {type(e).__name__}")
            logline(f"[{_ts()}] ❌ Precheck FAIL – Deploy abgebrochen")
            return False

    if exit127_count >= 3:
        logline(f"[{_ts()}] ❌ Precheck FAIL: {exit127_count}× exit 127 – Shell nicht nutzbar")
        return False

    if not openwrt_found:
        logline(f"[{_ts()}] ⚠️  Precheck WARN: OpenWrt nicht erkannt in os-release (kein Abbruch)")

    logline(f"[{_ts()}] ✅ Precheck OK")
    return True


def _ssh_push_job(job_id: str, ip: str, user: str, password: str, script: str,
                  mac: str, db_path: str, precheck: bool = False,
                  precheck_only: bool = False):
    """Läuft im Hintergrund-Thread. Optionaler read-only Precheck, dann Deploy.
    Bei precheck_only=True: nur Precheck, kein Upload/Exec, kein DB-Update."""
    log = []
    def logline(msg):
        log.append(msg)
        _ssh_jobs[job_id]["log"] = "\n".join(log)

    try:
        logline(f"[{_ts()}] Starte SSH-Verbindung zu {user}@{ip}...")
        base_ssh = _build_base_ssh(ip, user, password, logline, key_content=_get_saved_ssh_key())

        # Precheck (read-only, kein Schreibzugriff) – bei precheck oder precheck_only
        if precheck or precheck_only:
            if not _run_precheck(base_ssh, logline):
                _ssh_jobs[job_id]["status"] = "done"
                _ssh_jobs[job_id]["success"] = False
                return

        # Precheck-only: hier anhalten, keine Änderungen am Gerät
        if precheck_only:
            logline(f"[{_ts()}] ✅ Precheck-only: beendet ohne Änderungen am Gerät")
            _ssh_jobs[job_id]["status"] = "done"
            _ssh_jobs[job_id]["success"] = True
            return

        # Schritt 1: Verbindung testen
        rc, stdout, stderr = _ssh_exec(base_ssh, "echo CONNECTED", timeout=15)
        if "CONNECTED" not in stdout:
            raise RuntimeError(f"SSH-Verbindung fehlgeschlagen: {stderr.strip() or 'Timeout/Auth-Fehler'}")
        logline(f"[{_ts()}] ✅ SSH-Verbindung OK")

        # Schritt 2: Script übertragen
        logline(f"[{_ts()}] Übertrage Provisioning-Script...")
        rc, stdout, stderr = _ssh_exec(
            base_ssh,
            "tee /etc/uci-defaults/99-provision > /dev/null && chmod +x /etc/uci-defaults/99-provision",
            stdin_data=script.encode(), timeout=20)
        if rc != 0:
            raise RuntimeError(f"Upload fehlgeschlagen: {stderr[:200]}")
        logline(f"[{_ts()}] ✅ Script übertragen")

        # Schritt 3: /etc/provisioned löschen + Script ausführen
        logline(f"[{_ts()}] Führe Provisioning aus...")
        rc, stdout, stderr = _ssh_exec(
            base_ssh,
            "rm -f /etc/provisioned && sh /etc/uci-defaults/99-provision 2>&1 | head -50",
            timeout=60)
        output = (stdout + stderr).strip()
        if output:
            logline(f"Router-Output:\n{output[:500]}")
        # Exitcode prüfen
        if rc != 0:
            raise RuntimeError(f"Provisioning-Script fehlgeschlagen (Exit: {rc}): {(stderr or stdout)[:200]}")
        # Fatal-Output-Erkennung: bekannte Fehlermeldungen auch bei Exit 0
        for pattern in _DEPLOY_FATAL_PATTERNS:
            if pattern in output:
                raise RuntimeError(f"Provisioning fehlgeschlagen: '{pattern}' in Router-Output erkannt")
        logline(f"[{_ts()}] ✅ Provisioning abgeschlossen (Exit: {rc})")

        # DB updaten
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE devices SET status=?,last_log=?,last_seen=? WHERE base_mac=?",
                     ("provisioned", "\n".join(log), now_utc().isoformat(), mac))
        conn.commit(); conn.close()
        _ssh_jobs[job_id]["status"] = "done"
        _ssh_jobs[job_id]["success"] = True

    except subprocess.TimeoutExpired:
        logline(f"[{_ts()}] ❌ Timeout – Router antwortet nicht")
        _ssh_jobs[job_id]["status"] = "done"
        _ssh_jobs[job_id]["success"] = False
    except Exception as e:
        logline(f"[{_ts()}] ❌ Fehler: {e}")
        _ssh_jobs[job_id]["status"] = "done"
        _ssh_jobs[job_id]["success"] = False
    finally:
        _ssh_jobs[job_id]["done"] = True



# ─────────────────────────────────────────────────────────────────────────────
# Geräte-Discovery: Netzwerk-Scan nach OpenWrt-Routern
# ─────────────────────────────────────────────────────────────────────────────
async def _scan_host(ip: str, timeout: float) -> dict:
    """Prüft ob ein Host erreichbar ist und ob LuCI läuft."""
    import time as _time
    result = {"ip": ip, "port22": False, "port80": False, "luci": False, "latency_ms": None}
    t0 = _time.monotonic()
    for port in (80, 22):
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(ip, port), timeout=timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            if port == 80:
                result["port80"] = True
            else:
                result["port22"] = True
        except Exception:
            pass
    result["latency_ms"] = round((_time.monotonic() - t0) * 1000)
    # LuCI-Check: HTTP GET / → prüfe auf OpenWrt-Kennzeichen
    if result["port80"]:
        try:
            import urllib.request as _ur
            req = _ur.Request(f"http://{ip}/", headers={"User-Agent": "OpenWrtProvisioner/0.3"})
            with _ur.urlopen(req, timeout=timeout) as resp:
                body = resp.read(2048).decode("utf-8", errors="replace").lower()
                if "luci" in body or "openwrt" in body or "sysauth" in body:
                    result["luci"] = True
        except Exception:
            pass
    return result

async def _scan_subnet(subnet: str, timeout: float = 1.5) -> list:
    """Scannt Subnet .1-.254 parallel auf OpenWrt-Geräte."""
    subnet = subnet.rstrip(".")
    tasks = [_scan_host(f"{subnet}.{i}", timeout) for i in range(1, 255)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    found = []
    for r in results:
        if isinstance(r, dict) and (r["port22"] or r["port80"]):
            found.append(r)
    found.sort(key=lambda x: int(x["ip"].split(".")[-1]))
    return found

@app.post("/api/discover")
async def api_discover(request: Request, _=Depends(check_admin)):
    """Netzwerk-Scan nach erreichbaren Hosts (OpenWrt-Router)."""
    body = await request.json()
    subnet = str(body.get("subnet", "192.168.10")).strip()
    timeout = float(body.get("timeout", 1.5))
    timeout = max(0.3, min(timeout, 5.0))  # Clamp: 0.3s – 5s
    results = await _scan_subnet(subnet, timeout)
    return {"subnet": subnet, "found": len(results), "results": results}

# ─────────────────────────────────────────────────────────────────────────────
# HTML-Grundgerüst
# ─────────────────────────────────────────────────────────────────────────────
def _page(content: str, title: str = "", active: str = "") -> HTMLResponse:
    t = f" – {title}" if title else ""
    def _nav(href, icon, label):
        cls = " class=\'active\'" if active and href.rstrip("/") == active.rstrip("/") else ""
        return f"<a href=\'{href}\'{cls}>{icon} {label}</a>"
    return HTMLResponse(f"""<!DOCTYPE html>
<html lang="de"><head><meta charset="utf-8">
<title>🛜 OpenWrt Provisioning{t}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{{box-sizing:border-box}}
body{{font-family:monospace;max-width:1200px;margin:0 auto;padding:1em;background:#0d1117;color:#c9d1d9}}
h1{{color:#00d4ff;margin:0}} h2{{color:#58a6ff;border-bottom:1px solid #21262d;padding-bottom:.3em}}
h3{{color:#a8d8ea}} a{{color:#58a6ff;text-decoration:none}} a:hover{{text-decoration:underline}}
nav{{background:#161b22;padding:.6em 1em;border-radius:6px;margin-bottom:1.5em;display:flex;gap:.4em;flex-wrap:wrap;align-items:center}}
nav a{{padding:.3em .65em;border-radius:4px;border:1px solid #30363d;font-size:.88em}}
nav a:hover{{background:#21262d;border-color:#58a6ff}}
.active{{background:#1f6feb!important;border-color:#1f6feb!important;color:#fff!important}}
nav .sep{{color:#30363d;user-select:none;margin:0 .2em}}
table{{width:100%;border-collapse:collapse;margin:.5em 0}}
th,td{{border:1px solid #21262d;padding:.4em .7em;text-align:left;font-size:.9em}}
th{{background:#161b22;color:#8b949e}} tr:hover td{{background:#161b22}}
textarea{{width:100%;font-family:monospace;font-size:.82em;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;padding:.5em;border-radius:4px;min-height:200px}}
input[type=text],input[type=password],select{{background:#161b22;color:#c9d1d9;border:1px solid #30363d;padding:.35em .5em;width:100%;border-radius:4px}}
input[type=submit],button,.btn{{background:#1f6feb;color:#fff;border:none;padding:.4em 1em;cursor:pointer;border-radius:4px;font-family:monospace;font-size:.9em;text-decoration:none;display:inline-block}}
input[type=submit]:hover,button:hover,.btn:hover{{background:#388bfd}}
.btn-green{{background:#238636}} .btn-green:hover{{background:#2ea043}}
.btn-red{{background:#da3633}} .btn-red:hover{{background:#f85149}}
.btn-orange{{background:#9e6a03}} .btn-orange:hover{{background:#d29922}}
.btn-teal{{background:#0d7377}} .btn-teal:hover{{background:#14a085}}
.card{{background:#161b22;padding:1em;margin:.7em 0;border-radius:6px;border:1px solid #21262d}}
.card-blue{{border-left:3px solid #58a6ff}}
.card-green{{border-left:3px solid #3fb950}}
.card-red{{border-left:3px solid #f85149}}
.card-orange{{border-left:3px solid #d29922}}
.card-teal{{border-left:3px solid #14a085}}
.ok{{color:#3fb950}} .err{{color:#f85149}} .warn{{color:#d29922}} .muted{{color:#8b949e}}
.badge{{display:inline-block;padding:.1em .5em;border-radius:10px;font-size:.8em;font-weight:bold}}
.badge-green{{background:#1a4731;color:#3fb950}}
.badge-orange{{background:#3d2b00;color:#d29922}}
.badge-red{{background:#3d0000;color:#f85149}}
.badge-gray{{background:#21262d;color:#8b949e}}
.badge-teal{{background:#0d3d3d;color:#14a085}}
code{{background:#21262d;padding:.1em .3em;border-radius:3px;font-size:.85em}}
pre{{background:#161b22;padding:1em;border-radius:6px;overflow-x:auto;border:1px solid #21262d;font-size:.82em}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:1em}}
.grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:1em}}
@media(max-width:750px){{.grid2,.grid3{{grid-template-columns:1fr}}}}
.tab-btn{{transition:background .15s;font-size:.85em}}
.tab-btn.active{{outline:2px solid #58a6ff}}
</style></head>
<body>
<nav>
  <span style="color:#00d4ff;font-weight:bold;margin-right:.3em">🛜 OpenWrt</span>
  {_nav("/ui/","🏠","Dashboard")}
  <span class="sep">|</span>
  {_nav("/ui/projects","📁","Projekte")}
  {_nav("/ui/devices","🖥️","Geräte")}
  <span class="sep">|</span>
  {_nav("/ui/config-pull","📥","Config-Pull")}
  {_nav("/ui/config-push","📤","Config-Push")}
  {_nav("/ui/discover","🔍","Discovery")}
  <span class="sep">|</span>
  {_nav("/ui/templates","📋","Templates")}
  {_nav("/ui/roles","🎭","Rollen")}
  {_nav("/ui/settings","⚙️","Einstellungen")}
  <span class="sep">|</span>
  {_nav("/ui/setup","🚀","Setup")}
</nav>
{content}
</body></html>""")

def _status_badge(s: str) -> str:
    m = {"provisioned":"badge-green","pending":"badge-orange",
         "error":"badge-red","FAILED":"badge-red"}
    cls = m.get(s,"badge-gray")
    icons = {"provisioned":"✅","pending":"⏳","error":"❌","FAILED":"❌"}
    return f"<span class='badge {cls}'>{icons.get(s,'❓')} {s}</span>"

# ─────────────────────────────────────────────────────────────────────────────
# API: /api/claim  –  akzeptiert JSON *und* application/x-www-form-urlencoded
# (BusyBox-wget sendet form-encoded; curl/Browser senden JSON)
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/claim")
async def api_claim(request: Request, db: sqlite3.Connection = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        # JSON-Body (curl, Browser, JSON-Clients)
        body       = await request.json()
        # base_mac und mac beide akzeptieren
        mac        = str(body.get("base_mac", body.get("mac", ""))).strip()
        board_name = str(body.get("board_name", "unknown"))
        model      = str(body.get("model", "unknown"))
        token      = str(body.get("token", ""))
    else:
        # application/x-www-form-urlencoded (BusyBox-wget --post-data)
        form       = await request.form()
        mac        = str(form.get("base_mac", form.get("mac", ""))).strip()
        board_name = str(form.get("board_name", "unknown"))
        model      = str(form.get("model", "unknown"))
        token      = str(form.get("token", ""))

    if not mac:
        raise HTTPException(400, "MAC fehlt")
    if not secrets.compare_digest(token, ENROLLMENT_TOKEN):
        raise HTTPException(403, "Invalid token")

    # MAC normalisieren: lower() + strip() + "aa:bb:cc" → "aa-bb-cc"
    mac = mac.lower().strip().replace(":", "-")

    now       = now_utc().isoformat()
    client_ip = request.client.host if request.client else None
    existing  = db.execute("SELECT * FROM devices WHERE base_mac=?", (mac,)).fetchone()
    suffix    = mac_suffix(mac)
    hostname  = existing["hostname"] if existing else f"ap-{suffix:03d}"
    role      = existing["role"]     if existing else "node"
    project   = existing["project"]  if existing else "default"

    if existing:
        db.execute("UPDATE devices SET last_seen=?,board_name=?,model=?,claimed=1,status=?,last_ip=? WHERE base_mac=?",
                   (now, board_name, model, "provisioned", client_ip, mac))
    else:
        db.execute("INSERT INTO devices(base_mac,hostname,role,board_name,model,last_seen,claimed,project,status,last_ip) VALUES(?,?,?,?,?,?,1,?,?,?)",
                   (mac, hostname, role, board_name, model, now, project, "provisioned", client_ip))
    db.commit()

    return {"status": "claimed", "mac": mac,
            "hostname": hostname, "role": role, "project": project}

# API: Status-Update vom Client
@app.post("/api/status")
def api_status(request: dict, db: sqlite3.Connection = Depends(get_db)):
    mac    = request.get("base_mac","").lower()
    status = request.get("status","provisioned")
    log    = request.get("log","")
    db.execute("UPDATE devices SET status=?,last_log=?,last_seen=? WHERE base_mac=?",
               (status, log, now_utc().isoformat(), mac))
    db.commit()
    return {"ok": True}

# ─────────────────────────────────────────────────────────────────────────────
# UI: Dashboard
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
def root(): return HTMLResponse('<meta http-equiv="refresh" content="0;url=/ui/">')

@app.get("/ui/", response_class=HTMLResponse)
def ui_dashboard(db: sqlite3.Connection = Depends(get_db), _=Depends(check_admin)):
    devices  = db.execute("SELECT * FROM devices ORDER BY project,last_seen DESC").fetchall()
    projects = db.execute("SELECT name FROM projects ORDER BY name").fetchall()
    total = len(devices)
    prov  = sum(1 for d in devices if d["status"]=="provisioned")
    pend  = sum(1 for d in devices if d["status"]=="pending")
    err   = sum(1 for d in devices if d["status"] in ("error","FAILED"))

    stats = f"""
<div class='grid2'>
  <div class='card card-blue'>
    <b>🖥️ Geräte gesamt</b><br>
    <span style='font-size:2em;color:#58a6ff'>{total}</span>
  </div>
  <div class='card'>
    <span class='badge badge-green'>✅ {prov} provisioniert</span>&nbsp;
    <span class='badge badge-orange'>⏳ {pend} ausstehend</span>&nbsp;
    <span class='badge badge-red'>❌ {err} Fehler</span>
  </div>
</div>
<div class='card card-orange'>
  🔑 <b>Enrollment-Token:</b> <code>{ENROLLMENT_TOKEN}</code>
  <span class='muted' style='margin-left:1em'>– wird beim Claim benötigt</span>
</div>"""

    # Gruppiert nach Projekt
    proj_groups: dict = {}
    for d in devices:
        proj_groups.setdefault(d["project"] or "default", []).append(d)

    tables = ""
    for proj, devs in proj_groups.items():
        rows = ""
        for d in devs:
            ago = ""
            if d["last_seen"]:
                try:
                    dt = parse_dt_utc(d["last_seen"])
                    diff = (now_utc()-dt).total_seconds()
                    ago = f"{int(diff//3600)}h {int((diff%3600)//60)}m" if diff>3600 else f"{int(diff//60)}m"
                except: pass
            rows += f"""<tr>
  <td>{d['base_mac']}</td>
  <td><b>{d['hostname']}</b></td>
  <td><span class='badge badge-gray'>{d['role']}</span></td>
  <td>{d['board_name'] or '-'}</td>
  <td>{_status_badge(d['status'] or 'pending')}</td>
  <td class='muted'>{ago or '-'}</td>
  <td style='font-size:.82em;color:#8b949e;font-family:monospace'>{d['last_ip'] or '-'}</td>
  <td>
    <a class='btn btn-orange' style='font-size:.8em;padding:.2em .6em' href='/ui/devices/{d['base_mac']}'>✏️</a>
    <a class='btn btn-green'  style='font-size:.8em;padding:.2em .6em' href='/ui/deploy/{d['base_mac']}'>🚀</a>
    <a class='btn btn-teal'   style='font-size:.8em;padding:.2em .6em' href='/ui/deploy/{d['base_mac']}/ssh'>📡</a>
  </td>
</tr>"""
        tables += f"""
<h3>📁 {proj} <span class='muted' style='font-size:.8em'>{len(devs)} Geräte</span></h3>
<table>
  <tr><th>MAC</th><th>Hostname</th><th>Rolle</th><th>Board</th><th>Status</th><th>Zuletzt</th><th style='font-size:.82em'>IP</th><th>Aktionen</th></tr>
  {rows or "<tr><td colspan='7' class='muted'>Keine Geräte</td></tr>"}
</table>"""

    if not devices:
        tables = "<div class='card card-orange'>⏳ Noch keine Geräte – beim ersten Boot melden sie sich hier.</div>"

    quickbar = """
<div class='card card-teal' style='padding:.6em 1em;display:flex;gap:.5em;flex-wrap:wrap;align-items:center;margin-bottom:.3em'>
  <span style='color:#14a085;font-weight:bold'>⚡ Schnellzugriff:</span>
  <a class='btn btn-teal' href='/ui/config-pull'>📥 Config-Pull → Push</a>
  <a class='btn btn-green' href='/ui/setup'>🚀 Setup / SSH-Install</a>
  <a class='btn' href='/ui/projects'>📁 Projekte</a>
  <a class='btn' href='/ui/templates'>📋 Templates</a>
</div>"""

    return _page(quickbar + stats + tables, "Dashboard", "/ui/")

# ─────────────────────────────────────────────────────────────────────────────
# UI: Geräte
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/ui/devices", response_class=HTMLResponse)
def ui_devices(db: sqlite3.Connection = Depends(get_db), _=Depends(check_admin)):
    devices = db.execute("SELECT * FROM devices ORDER BY last_seen DESC").fetchall()
    rows = "".join(f"<tr><td>{d['base_mac']}</td><td>{d['hostname']}</td>"
                   f"<td>{d['role']}</td><td>{d['project']}</td>"
                   f"<td>{_status_badge(d['status'] or 'pending')}</td>"
                   f"<td><a href='/ui/devices/{d['base_mac']}'>✏️ Edit</a></td></tr>"
                   for d in devices)
    content = f"""
<h2>🖥️ Alle Geräte</h2>
<table>
  <tr><th>MAC</th><th>Hostname</th><th>Rolle</th><th>Projekt</th><th>Status</th><th></th></tr>
  {rows or "<tr><td colspan='6' class='muted'>Keine Geräte</td></tr>"}
</table>"""
    return _page(content, "Geräte", "/ui/devices")

@app.get("/ui/devices/{mac}", response_class=HTMLResponse)
def ui_device_get(mac: str, db: sqlite3.Connection = Depends(get_db), _=Depends(check_admin)):
    d = db.execute("SELECT * FROM devices WHERE base_mac=?", (mac,)).fetchone()
    if not d: raise HTTPException(404)
    roles    = db.execute("SELECT name,description FROM roles").fetchall()
    projects = db.execute("SELECT name FROM projects ORDER BY name").fetchall()
    role_opts = "".join(f"<option value='{r['name']}' {'selected' if r['name']==d['role'] else ''}>{r['name']} – {r['description']}</option>" for r in roles)
    proj_opts = "".join(f"<option value='{p['name']}' {'selected' if p['name']==(d['project'] or 'default') else ''}>{p['name']}</option>" for p in projects)

    content = f"""
<h2>✏️ Gerät bearbeiten</h2>
<div class='card card-blue'>
  <b>MAC:</b> <code>{mac}</code> &nbsp;|&nbsp;
  <b>Board:</b> {d['board_name'] or '-'} &nbsp;|&nbsp;
  <b>Model:</b> {d['model'] or '-'} &nbsp;|&nbsp;
  <b>IP:</b> <code style='color:#3fb950'>{d['last_ip'] or '–'}</code> &nbsp;|&nbsp;
  <b>Zuletzt:</b> {d['last_seen'] or '-'}
  &nbsp;{_status_badge(d['status'] or 'pending')}
</div>
<div class='grid2'>
<div>
<form method="POST" action="/ui/devices/{mac}">
<div class='card'>
<h3>⚙️ Gerät-Einstellungen</h3>
<table>
<tr><td>📁 Projekt</td><td><select name='project'>{proj_opts}</select></td></tr>
<tr><td>🎭 Rolle</td><td><select name='role'>{role_opts}</select></td></tr>
<tr><td>🏷️ Hostname</td><td><input type='text' name='hostname' value='{d['hostname']}'></td></tr>
<tr><td>📝 Notizen</td><td><input type='text' name='notes' value='{d['notes'] or ''}'></td></tr>
</table>
<br><input type='submit' value='💾 Speichern'>
</div>
<div class='card'>
<h3>🔧 Geräte-Override <span class='muted' style='font-size:.8em'>(uci-batch – überschreibt Template)</span></h3>
<p class='muted' style='font-size:.85em'>Hier nur Zeilen die von diesem Gerät abweichen sollen, z.B. andere IP oder Kanal.</p>
<textarea name='override' rows='8'>{d['override'] or ''}</textarea>
<input type='submit' value='💾 Speichern'>
</div>
</form>
</div>
<div>
<div class='card card-green'>
<h3>🚀 Config ausrollen</h3>
<p>Rendert Template + Rolle + Override und zeigt Vorschau.</p>
<a class='btn btn-green' href='/ui/deploy/{mac}'>🚀 Config ausrollen / Vorschau</a>
</div>
<div class='card card-blue'>
<h3>🩺 Provisioning Diagnose</h3>
<p class='muted' style='font-size:.85em'>Vor Deploy: Server-Checks, optional Router-Checks (SSH read-only).</p>
<a class='btn' href='/ui/diagnose/{mac}'>🩺 Diagnose öffnen</a>
</div>
<div class='card'>
<h3>📋 Letztes Log</h3>
<pre style='min-height:80px;font-size:.8em'>{d['last_log'] or '(noch kein Log)'}</pre>
</div>
<div class='card card-red' style='margin-top:1em'>
<h3>⚠️ Gerät löschen</h3>
<form method="POST" action="/ui/devices/{mac}/delete">
  <input type='submit' class='btn btn-red' value='🗑️ Löschen' onclick="return confirm('Sicher?')">
</form>
</div>
</div>
</div>"""
    return _page(content, d['hostname'], "/ui/devices")

@app.post("/ui/devices/{mac}", response_class=HTMLResponse)
def ui_device_post(mac: str, role: str=Form(...), hostname: str=Form(...),
                   notes: str=Form(""), override: str=Form(""), project: str=Form("default"),
                   db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    db.execute("UPDATE devices SET role=?,hostname=?,notes=?,override=?,project=? WHERE base_mac=?",
               (role, hostname, notes, override or None, project, mac))
    db.commit()
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/ui/devices/{mac}">')

@app.post("/ui/devices/{mac}/delete", response_class=HTMLResponse)
def ui_device_delete(mac: str, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    db.execute("DELETE FROM devices WHERE base_mac=?", (mac,))
    db.commit()
    return HTMLResponse('<meta http-equiv="refresh" content="0;url=/ui/devices">')

# ─────────────────────────────────────────────────────────────────────────────
# UI: Deploy / Vorschau + Validierung
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/ui/deploy/{mac}", response_class=HTMLResponse)
def ui_deploy(mac: str, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    d = db.execute("SELECT * FROM devices WHERE base_mac=?", (mac,)).fetchone()
    if not d: raise HTTPException(404)

    proj_row  = db.execute("SELECT settings FROM projects WHERE name=?", (d["project"],)).fetchone()
    proj_s    = json.loads(proj_row["settings"] if proj_row else "{}")
    glob_s    = get_settings(db)
    merged    = {**glob_s, **proj_s}
    tmpl_name = proj_s.get("template","master")
    tmpl_row  = db.execute("SELECT content FROM templates WHERE name=?", (tmpl_name,)).fetchone()
    role_row  = db.execute("SELECT overrides FROM roles WHERE name=?", (d["role"],)).fetchone()
    vars_     = build_vars(merged, mac, d["hostname"])
    rendered  = render_template(
        tmpl_row["content"] if tmpl_row else "",
        vars_, role_row["overrides"] if role_row else "", d["override"])

    issues  = validate_template(rendered)
    issue_html = ""
    if issues:
        issue_html = "<div class='card card-orange'><b>⚠️ Validierungshinweise:</b><ul>"
        for i in issues: issue_html += f"<li>{i}</li>"
        issue_html += "</ul></div>"
    else:
        issue_html = "<div class='card card-green'>✅ Validierung OK – keine Probleme gefunden.</div>"

    content = f"""
<h2>🚀 Config ausrollen: <b>{d['hostname']}</b></h2>
<div class='card card-blue'>
  <b>Gerät:</b> {mac} &nbsp;|&nbsp; <b>Rolle:</b> {d['role']} &nbsp;|&nbsp;
  <b>Projekt:</b> {d['project']} &nbsp;|&nbsp; <b>Template:</b> {tmpl_name}<br>
  <b>Management-IP:</b> <code>{merged['MGMT_NET']}.{vars_['MGMT_SUFFIX']}</code> &nbsp;|&nbsp;
  <b>Hostname:</b> <code>{d['hostname']}</code>
</div>
{issue_html}
<div class='card'>
<h3>📋 Config-Vorschau (was aufs Gerät kommt)</h3>
<p class='muted' style='font-size:.85em'>Dies ist exakt das uci-batch Script das beim nächsten Claim ausgeführt wird.</p>
<pre>{rendered}</pre>
</div>
<div class='card card-blue'>
<h3>📡 Wie ausrollen?</h3>
<p><b>Option 1 – Gerät neu starten (empfohlen):</b></p>
<pre>ssh root@{merged['MGMT_NET']}.{vars_['MGMT_SUFFIX']} "rm -f /etc/provisioned && reboot"</pre>
<p><b>Option 2 – Sofort ohne Neustart:</b></p>
<pre>ssh root@{merged['MGMT_NET']}.{vars_['MGMT_SUFFIX']} "rm -f /etc/provisioned && sh /etc/uci-defaults/99-provision"</pre>
<p><b>Option 3 – Direkt per SSH installieren (empfohlen):</b></p>
<a class='btn btn-green' href='/ui/deploy/{mac}/ssh' {'style="opacity:.5;pointer-events:none"' if issues else ''}>⚡ SSH-Installer öffnen</a>
{'<span class="warn" style="margin-left:.5em"> – erst Validierungsfehler beheben</span>' if issues else '<span class="muted" style="margin-left:.5em"> – IP + Passwort eingeben, fertig!</span>'}
</div>"""
    return _page(content, f"Deploy {d['hostname']}", "/ui/devices")

@app.get("/ui/deploy/{mac}/ssh", response_class=HTMLResponse)
def ui_deploy_ssh_form(mac: str, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    """SSH-Installer-Formular – IP, User, Passwort eingeben."""
    d = db.execute("SELECT * FROM devices WHERE base_mac=?", (mac,)).fetchone()
    if not d: raise HTTPException(404)
    proj_row  = db.execute("SELECT settings FROM projects WHERE name=?", (d["project"],)).fetchone()
    proj_s    = json.loads(proj_row["settings"] if proj_row else "{}")
    glob_s    = get_settings(db)
    merged    = {**glob_s, **proj_s}
    vars_     = build_vars(merged, mac, d["hostname"])
    # last_ip hat Vorrang vor berechneter MGMT-IP
    default_ip = d["last_ip"] or f"{merged['MGMT_NET']}.{vars_['MGMT_SUFFIX']}"
    ip_hint = " 🔴 aus Datenbank" if d["last_ip"] else " 📐 aus MGMT_NET berechnet"

    content = f"""
<h2>⚡ SSH-Installer: <b>{d['hostname']}</b></h2>
<div class='card card-blue'>
  <b>Was passiert hier?</b><br>
  Du gibst IP, Benutzername und Passwort des Routers ein.
  Der Server verbindet sich per SSH, überträgt das Provisioning-Script und führt es direkt aus.
  Kein manuelles Kopieren nötig!
</div>
<div class='grid2'>
<div>
<div class='card card-green'>
<h3>🔌 Verbindungsdaten</h3>
<form id='ssh-form' onsubmit='startDeploy(event)'>
  <table style='width:100%'>
    <tr>
      <td style='width:130px'>🌐 IP-Adresse</td>
      <td><input type='text' id='ssh-ip' value='{default_ip}' placeholder='192.168.1.1' required style='width:100%'>
      <span class='muted' style='font-size:.78em'>{ip_hint}</span></td>
    </tr>
    <tr>
      <td>👤 Benutzer</td>
      <td><input type='text' id='ssh-user' value='root' placeholder='root' required style='width:100%'></td>
    </tr>
    <tr>
      <td>🔑 Passwort</td>
      <td><input type='password' id='ssh-pass' value='' placeholder='Router-Passwort' style='width:100%'></td>
    </tr>
    <tr>
      <td>🔍 Precheck</td>
      <td><label style='cursor:pointer'>
        <input type='checkbox' id='ssh-precheck' style='width:auto;margin-right:.4em'>
        Precheck aktivieren (read-only: SSH / UCI / OpenWrt prüfen vor Deploy)
      </label></td>
    </tr>
    <tr>
      <td>🔍 Nur Precheck</td>
      <td><label style='cursor:pointer'>
        <input type='checkbox' id='ssh-precheck-only' style='width:auto;margin-right:.4em'>
        Nur Precheck – kein Deploy (SSH / UCI / OpenWrt prüfen, dann stoppen)
      </label></td>
    </tr>
  </table>
  <br>
  <button type='submit' class='btn btn-green' id='start-btn'>⚡ Jetzt installieren</button>
  <a class='btn' href='/ui/deploy/{mac}' style='margin-left:.5em'>↩ Zurück</a>
</form>
</div>

<div class='card card-orange'>
<h3>⚠️ Voraussetzungen</h3>
<ul style='font-size:.9em'>
  <li>Router muss SSH-aktiv sein und erreichbar</li>
  <li>Server muss <code>sshpass</code> installiert haben (für Passwort-Auth)</li>
  <li>Alternativ: SSH-Key-Authentifizierung funktioniert ohne sshpass</li>
  <li>Auf Windows-Servern: WSL oder Git-Bash mit sshpass empfohlen</li>
</ul>
<p class='muted' style='font-size:.85em'>sshpass installieren (Linux): <code>apt install sshpass</code></p>
</div>
</div>

<div>
<div class='card' id='log-card' style='display:none'>
<h3>📟 Live-Log</h3>
<pre id='deploy-log' style='min-height:150px;max-height:400px;overflow-y:auto;font-size:.8em'>Verbinde...</pre>
<div id='deploy-result' style='margin-top:.7em'></div>
</div>
</div>
</div>

<script>
async function startDeploy(e) {{
  e.preventDefault();
  const ip            = document.getElementById('ssh-ip').value;
  const user          = document.getElementById('ssh-user').value;
  const pass          = document.getElementById('ssh-pass').value;
  const precheck      = document.getElementById('ssh-precheck').checked;
  const precheck_only = document.getElementById('ssh-precheck-only').checked;
  document.getElementById('start-btn').disabled = true;
  document.getElementById('start-btn').textContent = '⏳ Läuft...';
  document.getElementById('log-card').style.display = 'block';

  const resp = await fetch('/api/deploy/{mac}/ssh-push', {{
    method: 'POST',
    headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{ip, user, password: pass, precheck, precheck_only}})
  }});
  const data = await resp.json();
  if (!data.job_id) {{
    document.getElementById('deploy-log').textContent = 'Fehler: ' + JSON.stringify(data);
    return;
  }}
  pollJob(data.job_id);
}}

async function pollJob(jobId) {{
  const logEl = document.getElementById('deploy-log');
  const resEl = document.getElementById('deploy-result');
  let done = false;
  while (!done) {{
    await new Promise(r => setTimeout(r, 1200));
    try {{
      const r = await fetch('/api/deploy/job/' + jobId);
      const d = await r.json();
      logEl.textContent = d.log || 'Warte...';
      logEl.scrollTop = logEl.scrollHeight;
      if (d.done) {{
        done = true;
        if (d.success) {{
          resEl.innerHTML = d.precheck_only
            ? "<div class='card card-green' style='padding:.5em'>✅ <b>Precheck erfolgreich – keine Änderungen am Router.</b></div>"
            : "<div class='card card-green' style='padding:.5em'>✅ <b>Erfolgreich deployed!</b> <a href='/ui/deploy/{mac}'>→ Zurück zur Übersicht</a></div>";
        }} else {{
          resEl.innerHTML = "<div class='card card-red' style='padding:.5em'>❌ <b>Fehler beim Deploy.</b> Log oben prüfen. <a href='/ui/deploy/{mac}/ssh'>→ Nochmal versuchen</a></div>";
        }}
        document.getElementById('start-btn').disabled = false;
        document.getElementById('start-btn').textContent = '⚡ Nochmal versuchen';
      }}
    }} catch(err) {{ logEl.textContent += "\n[Polling-Fehler: " + err + "]"; done = true; }}
  }}
}}
</script>
"""
    return _page(content, f"SSH-Install {d['hostname']}", "/ui/devices")


@app.get("/ui/diagnose/{mac}", response_class=HTMLResponse)
def ui_diagnose(mac: str, db: sqlite3.Connection = Depends(get_db), _=Depends(check_admin)):
    d = db.execute("SELECT * FROM devices WHERE base_mac=?", (mac,)).fetchone()
    if not d:
        raise HTTPException(404)

    # Kein f-string für den JS-Block (Template-Literals enthalten ${...} und würden f-strings sprengen).
    content = """
<h2>🩺 Provisioning Diagnose: <b>__HOST__</b></h2>
<div class='card card-blue'>
  Ziel: <b>vor</b> echtem Deploy prüfen, ob der Server eine valide Config erzeugt und (optional) ob der Router alles Nötige hat.<br>
  Router-Checks sind <b>read-only</b>: kein Write, kein UCI-Commit, kein Reboot, kein Upload.
</div>

<div class='grid2'>
  <div class='card card-green'>
    <h3>Server-Diagnose</h3>
    <p class='muted' style='font-size:.85em'>Ohne Router, immer möglich.</p>
    <button class='btn btn-green' onclick='runServer()'>🩺 Server-Diagnose starten</button>
  </div>
  <div class='card card-orange'>
    <h3>Router-Diagnose (SSH, read-only)</h3>
    <p class='muted' style='font-size:.85em'>Strict Host-Key Checking aktiv: Host-Key mismatch wird als FAIL gezeigt.</p>
    <table style='width:100%'>
      <tr><td style='width:110px'>IP</td><td><input type='text' id='ip' placeholder='192.168.1.1'></td></tr>
      <tr><td>User</td><td><input type='text' id='user' value='root'></td></tr>
      <tr><td>Pass</td><td><input type='password' id='pw' placeholder='(optional)'></td></tr>
    </table>
    <button class='btn btn-orange' style='margin-top:.6em' onclick='runSsh()'>🔍 Router-Diagnose starten</button>
  </div>
</div>

<div class='card' id='out' style='display:none'>
  <div style='display:flex;justify-content:space-between;align-items:center'>
    <h3 style='margin:0'>Report</h3>
    <div id='dl'></div>
  </div>
  <div id='overall' style='margin:.6em 0'></div>
  <div id='sections'></div>
</div>

<script>
function badge(st) {{
  const cls = st==='OK' ? 'badge-green' : (st==='WARN' ? 'badge-orange' : 'badge-red');
  return `<span class='badge ${cls}'>${st}</span>`;
}}

function esc(s) {{
  return (s||'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
}}

function render(rep) {{
  document.getElementById('out').style.display='block';
  document.getElementById('overall').innerHTML = `Overall: ${badge(rep.overall_status)} <span class='muted'>${esc(rep.created_at)}</span>`;
  document.getElementById('dl').innerHTML = `
    <a class='btn' href='${rep.downloads.json}'>JSON</a>
    <a class='btn' href='${rep.downloads.text}' style='margin-left:.4em'>Text</a>
    <a class='btn' href='${rep.downloads.config}' style='margin-left:.4em'>Config</a>`;
  let html = '';
  for (const sec of rep.sections) {{
    html += `<details class='card card-blue' open style='margin-top:.7em'>
      <summary style='cursor:pointer'>${badge(sec.status)} <b>${esc(sec.title)}</b> <span class='muted'>(${esc(sec.id)})</span></summary>
      <div style='margin-top:.6em'>`;
    for (const chk of sec.checks) {{
      html += `<details class='card' style='margin:.5em 0'>
        <summary style='cursor:pointer'>${badge(chk.status)} <code>${esc(chk.id)}</code> – ${esc(chk.summary)}</summary>
        ${chk.details ? `<pre>${esc(chk.details)}</pre>` : `<div class='muted' style='padding:.4em'>keine Details</div>`}
      </details>`;
    }}
    html += `</div></details>`;
  }}
  document.getElementById('sections').innerHTML = html;
}}

async function runServer() {{
  const r = await fetch('/api/diagnose/__MAC__');
  const rep = await r.json();
  render(rep);
}}

async function runSsh() {{
  const ip = document.getElementById('ip').value.trim();
  const user = document.getElementById('user').value.trim() || 'root';
  const password = document.getElementById('pw').value;
  const r = await fetch('/api/diagnose/__MAC__/ssh', {{
    method: 'POST',
    headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{ip, user, password}})
  }});
  const rep = await r.json();
  render(rep);
}}
</script>
"""
    content = content.replace("__MAC__", mac).replace("__HOST__", d["hostname"])
    return _page(content, f"Diagnose {d['hostname']}", "/ui/devices")

@app.post("/api/deploy/{mac}/ssh-push")
async def api_ssh_push(mac: str, request: Request,
                        db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    """Startet SSH-Deploy als Hintergrund-Job."""
    body = await request.json()
    ip            = body.get("ip","").strip()
    user          = body.get("user","root").strip()
    password      = body.get("password","")
    precheck      = bool(body.get("precheck", False))
    precheck_only = bool(body.get("precheck_only", False))
    if not ip:
        raise HTTPException(400, "IP fehlt")

    d = db.execute("SELECT * FROM devices WHERE base_mac=?", (mac,)).fetchone()
    if not d: raise HTTPException(404)

    proj_row  = db.execute("SELECT settings FROM projects WHERE name=?", (d["project"],)).fetchone()
    proj_s    = json.loads(proj_row["settings"] if proj_row else "{}")
    glob_s    = get_settings(db)
    merged    = {**glob_s, **proj_s}
    tmpl_name = proj_s.get("template","master")
    tmpl_row  = db.execute("SELECT content FROM templates WHERE name=?", (tmpl_name,)).fetchone()
    role_row  = db.execute("SELECT overrides FROM roles WHERE name=?", (d["role"],)).fetchone()
    vars_     = build_vars(merged, mac, d["hostname"])
    rendered  = render_template(
        tmpl_row["content"] if tmpl_row else "",
        vars_, role_row["overrides"] if role_row else "", d["override"])

    job_id = secrets.token_hex(8)
    _ssh_jobs[job_id] = {"status": "running", "log": "Starte...", "done": False,
                         "success": False, "precheck_only": precheck_only}
    t = threading.Thread(target=_ssh_push_job,
                         args=(job_id, ip, user, password, rendered, mac, DB_PATH,
                               precheck, precheck_only),
                         daemon=True)
    t.start()
    return {"job_id": job_id}

@app.get("/api/deploy/job/{job_id}")
def api_job_status(job_id: str, _=Depends(check_admin)):
    job = _ssh_jobs.get(job_id)
    if not job: raise HTTPException(404, "Job nicht gefunden")
    return job


# ─────────────────────────────────────────────────────────────────────────────
# API: /api/config/{mac} – gerenderte UCI-Config für ein Gerät (Enrollment-Flow)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/config/{mac}", response_class=PlainTextResponse)
def api_config_by_mac(mac: str, token: str = "", db: sqlite3.Connection = Depends(get_db)):
    """Gibt die gerenderte UCI-Config für ein Gerät zurück.
    Authentifizierung via ?token=ENROLLMENT_TOKEN (für Router-Zugriff ohne Basic-Auth).
    Wird vom Bootstrap-Script (99-provision.sh) nach dem Claim aufgerufen."""
    if token != ENROLLMENT_TOKEN:
        raise HTTPException(401, "Unauthorized – falscher Token")
    # MAC normalisieren: AA:BB:CC:DD:EE:FF oder aa-bb-cc-dd-ee-ff → aabbccddeeffe
    mac_norm = mac.replace("-", "").replace(":", "").lower()
    d = db.execute(
        "SELECT * FROM devices WHERE LOWER(REPLACE(REPLACE(base_mac,':',''),'-',''))=?",
        (mac_norm,)).fetchone()
    if not d:
        from fastapi.responses import JSONResponse as _JSONResponse
        return _JSONResponse(
            {"error": "device_not_claimed", "mac": mac,
             "hint": "Geraet zuerst per /api/claim registrieren"},
            status_code=404)
    # Projekt + Template + Rolle laden (gleiche Logik wie /api/deploy/{mac}/ssh-push)
    proj_row = db.execute("SELECT settings FROM projects WHERE name=?", (d["project"],)).fetchone()
    settings = json.loads((proj_row["settings"] if proj_row else None) or "{}")
    # Globale Settings als Fallback einmischen
    global_s = get_settings(db)
    merged   = {**global_s, **settings}
    tpl_name = merged.get("template", "master")
    tpl_row  = db.execute("SELECT content FROM templates WHERE name=?", (tpl_name,)).fetchone()
    role_row = db.execute("SELECT overrides FROM roles WHERE name=?", (d["role"],)).fetchone()
    vars_    = build_vars(merged, d["base_mac"], d["hostname"])
    rendered = render_template(
        tpl_row["content"] if tpl_row else "",
        vars_,
        role_row["overrides"] if role_row else "",
        d["override"])
    return rendered


# ─────────────────────────────────────────────────────────────────────────────
# API: Diagnose (Server + optional Router read-only)
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/api/diagnose/{mac}")
def api_diagnose_server(mac: str, db: sqlite3.Connection = Depends(get_db), _=Depends(check_admin)):
    report, rendered = build_server_diagnose(mac, db)
    # speichern (für Downloads)
    _diag_reports[report.report_id] = {"report": report.dict(), "config": rendered}
    return report


class DiagnoseSshReq(BaseModel):
    ip: str
    user: str = "root"
    password: Optional[str] = None


@app.post("/api/diagnose/{mac}/ssh")
def api_diagnose_router(mac: str, req: DiagnoseSshReq, db: sqlite3.Connection = Depends(get_db), _=Depends(check_admin)):
    report, rendered = build_server_diagnose(mac, db)
    router_secs = build_router_diagnose(req.ip.strip(), (req.user or "root").strip(), req.password or "")
    # ergänzen + re-aggregieren
    secs = list(report.sections) + list(router_secs)
    report.sections = secs
    report.overall_status = _worst_status([s.status for s in report.sections])
    _diag_reports[report.report_id] = {"report": report.dict(), "config": rendered}
    return report


def _report_to_text(rep: dict) -> str:
    lines = []
    lines.append(f"Provisioning Diagnose – {rep.get('mac')} – {rep.get('created_at')}")
    lines.append(f"Overall: {rep.get('overall_status')}")
    if rep.get("config_sha256"):
        lines.append(f"Config SHA256: {rep.get('config_sha256')}")
    if rep.get("config_hmac_ok") is not None:
        lines.append(f"HMAC OK: {rep.get('config_hmac_ok')}")
    lines.append("")
    for sec in rep.get("sections", []):
        lines.append(f"[{sec.get('status')}] {sec.get('title')} ({sec.get('id')})")
        for chk in sec.get("checks", []):
            lines.append(f"  - {chk.get('id')}: {chk.get('status')} – {chk.get('summary')}")
            if chk.get("details"):
                for dl in str(chk.get("details")).splitlines():
                    lines.append(f"      {dl}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


@app.get("/api/diagnose/report/{report_id}.json", response_class=JSONResponse)
def api_diagnose_report_json(report_id: str, _=Depends(check_admin)):
    item = _diag_reports.get(report_id)
    if not item:
        raise HTTPException(404, "Report nicht gefunden")
    return item["report"]


@app.get("/api/diagnose/report/{report_id}.txt", response_class=PlainTextResponse)
def api_diagnose_report_txt(report_id: str, _=Depends(check_admin)):
    item = _diag_reports.get(report_id)
    if not item:
        raise HTTPException(404, "Report nicht gefunden")
    return _report_to_text(item["report"])


@app.get("/api/diagnose/report/{report_id}.config", response_class=PlainTextResponse)
def api_diagnose_report_config(report_id: str, _=Depends(check_admin)):
    item = _diag_reports.get(report_id)
    if not item:
        raise HTTPException(404, "Report nicht gefunden")
    return item.get("config", "")

@app.post("/ui/deploy/{mac}/push", response_class=HTMLResponse)
def ui_deploy_push(mac: str, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/ui/deploy/{mac}/ssh">')

# ─────────────────────────────────────────────────────────────────────────────
# UI: Projekte
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/ui/projects", response_class=HTMLResponse)
def ui_projects(db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    projects = db.execute("SELECT * FROM projects ORDER BY name").fetchall()
    cards = ""
    for p in projects:
        s = json.loads(p["settings"] or "{}")
        total   = db.execute("SELECT COUNT(*) FROM devices WHERE project=?", (p["name"],)).fetchone()[0]
        claimed = db.execute("SELECT COUNT(*) FROM devices WHERE project=? AND claimed=1", (p["name"],)).fetchone()[0]
        tmpl    = s.get("template","master")
        ssid    = s.get("SSID","-")
        mgmt    = s.get("MGMT_NET","-")
        r11     = "<span class='ok'>✅ aktiv</span>" if s.get("ENABLE_11R","0")=="1" else "<span class='muted'>❌ aus</span>"
        mesh    = "<span class='ok'>✅ aktiv</span>" if s.get("ENABLE_MESH","0")=="1" else "<span class='muted'>❌ aus</span>"
        cards += f"""
<div class='card'>
  <div style='display:flex;justify-content:space-between;align-items:flex-start'>
    <div>
      <h3 style='margin:0'>📁 {p['name']}</h3>
      <p class='muted' style='margin:.3em 0'>{p['description']}</p>
    </div>
    <span class='badge {"badge-green" if claimed==total and total>0 else "badge-orange"}'>{claimed}/{total} Geräte</span>
  </div>
  <div style='display:grid;grid-template-columns:repeat(3,1fr);gap:.5em;margin:.7em 0;font-size:.9em'>
    <div>📶 <b>SSID:</b><br>{ssid}</div>
    <div>🏠 <b>Netz:</b><br>{mgmt}.0/24</div>
    <div>📋 <b>Template:</b><br>{tmpl}</div>
    <div>🔄 <b>802.11r:</b><br>{r11}</div>
    <div>🕸️ <b>Mesh:</b><br>{mesh}</div>
    <div>🔍 <b>DNS:</b><br>{s.get('DNS','-')}</div>
  </div>
  <a class='btn' href='/ui/projects/{p['name']}'>✏️ Bearbeiten</a>
</div>"""

    content = f"""
<h2>📁 Projekte</h2>
<div class='card card-blue'>
  <b>ℹ️ Was ist ein Projekt?</b><br>
  Fasst Geräte mit <b>gleicher Konfiguration</b> zusammen – z.B. alle APs bei einem Kunden.<br>
  Jedes Projekt hat eigene SSID, Passwörter, IP-Bereich und Template.
  Neue Geräte landen zunächst im Projekt <code>default</code>.
</div>
{cards}
<hr>
<h3>➕ Neues Projekt anlegen</h3>
<div class='card card-green'>
<form method="POST" action="/ui/projects/new">
<table>
  <tr><th colspan='3'>📋 Projekt-Info</th></tr>
  <tr><td>🏷️ Name</td><td><input type='text' name='name' placeholder='kunde-mueller' required></td><td class='muted'>Keine Leerzeichen, z.B. kunde-mueller</td></tr>
  <tr><td>📝 Beschreibung</td><td><input type='text' name='description' placeholder='Kunde Müller GmbH – 5x WR3000'></td><td></td></tr>
  <tr><th colspan='3'>🌐 Netzwerk</th></tr>
  <tr><td>🏠 MGMT_NET</td><td><input type='text' name='MGMT_NET' value='192.168.50'></td><td class='muted'>Ersten 3 Oktette, z.B. 192.168.50</td></tr>
  <tr><td>🚪 Gateway</td><td><input type='text' name='GW' value='192.168.50.1'></td><td class='muted'>Standard: .1</td></tr>
  <tr><td>🔍 DNS</td><td><input type='text' name='DNS' value='192.168.50.1'></td><td class='muted'>AdGuard, Pi-hole, oder Router</td></tr>
  <tr><th colspan='3'>📡 WLAN</th></tr>
  <tr><td>📶 SSID</td><td><input type='text' name='SSID' placeholder='KundeWLAN' required></td><td class='muted'>Name des Hauptnetzes</td></tr>
  <tr><td>🔑 Passwort</td><td><input type='text' name='WPA_PSK' placeholder='Mind. 8 Zeichen!'></td><td class='muted'>WPA2/WPA3 Passwort</td></tr>
  <tr><td>🔄 802.11r</td><td><select name='ENABLE_11R'><option value='1'>✅ aktiviert (empfohlen bei mehreren APs)</option><option value='0'>❌ deaktiviert</option></select></td><td class='muted'>Fast Roaming zwischen APs</td></tr>
  <tr><td>🕸️ Mesh</td><td><select name='ENABLE_MESH'><option value='0'>❌ deaktiviert</option><option value='1'>✅ aktiviert (batman-adv)</option></select></td><td class='muted'>Nur mit kmod-batman-adv im Image</td></tr>
  <tr><th colspan='3'>📋 Template</th></tr>
  <tr><td>Template</td><td><input type='text' name='template' value='master'></td><td class='muted'>Name des uci-batch Templates</td></tr>
</table>
<br><input type='submit' value='➕ Projekt erstellen'>
</form>
</div>"""
    return _page(content, "Projekte", "/ui/projects")

@app.post("/ui/projects/new", response_class=HTMLResponse)
async def ui_project_new(request: Request, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    form = await request.form()
    name = str(form.get("name","")).strip().replace(" ","-")
    if not name:
        return _page("<div class='card card-red'>❌ Name fehlt.</div><a class='btn' href='/ui/projects'>↩</a>")
    desc = str(form.get("description",""))
    keys = ["MGMT_NET","GW","DNS","SSID","WPA_PSK","ENABLE_11R","ENABLE_MESH","MESH_ID","MESH_PSK","template"]
    s = {k: str(form.get(k,"")) for k in keys if form.get(k)}
    db.execute("INSERT OR IGNORE INTO projects(name,description,created_at,settings) VALUES(?,?,?,?)",
               (name, desc, now_utc().isoformat(), json.dumps(s)))
    db.commit()
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/ui/projects/{name}">')

@app.get("/ui/projects/{name}", response_class=HTMLResponse)
def ui_project_edit(name: str, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    p = db.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
    if not p: raise HTTPException(404)
    s = json.loads(p["settings"] or "{}")
    templates = db.execute("SELECT name FROM templates ORDER BY name").fetchall()
    tmpl_opts = "".join(f"<option value='{t['name']}' {'selected' if t['name']==s.get('template','master') else ''}>{t['name']}</option>" for t in templates)

    # WLANs aus Settings laden (Format: wlans=[{ssid,psk,band,encryption,vlan,r80211,enabled,label}])
    wlans = s.get("wlans", [])
    # Fallback: altes Format (SSID/WPA_PSK) als erstes WLAN
    if not wlans and s.get("SSID"):
        wlans = [{"ssid": s.get("SSID",""), "psk": s.get("WPA_PSK",""),
                  "band": "2g+5g", "encryption": "sae-mixed", "vlan": "lan",
                  "r80211": s.get("ENABLE_11R","1"), "enabled": "1", "label": "Haupt-WLAN"}]
    if not wlans:
        wlans = [{"ssid":"","psk":"","band":"2g+5g","encryption":"sae-mixed",
                  "vlan":"lan","r80211":"1","enabled":"1","label":"Haupt-WLAN"}]

    # Netzwerk-Namen für VLAN-Dropdown ermitteln
    net_names = list(s.get("networks", {}).keys()) if s.get("networks") else []
    if not net_names:
        net_names = ["lan", "Media", "Worls", "Guest"]
    net_names_json = json.dumps(net_names)

    tab_buttons = ""
    tab_panels = ""
    for i, w in enumerate(wlans):
        active = "active" if i == 0 else ""
        label = w.get("label", f"WLAN {i+1}")
        ssid  = w.get("ssid","")
        tab_label = f"{label}: {ssid}" if ssid else f"WLAN {i+1}"
        tab_buttons += f"<button type='button' class='wlan-tab-btn {active}' onclick='switchTab({i})'>{tab_label}</button>"

        band_opts = "".join(f"<option value='{v}' {'selected' if w.get('band','2g+5g')==v else ''}>{l}</option>"
            for v,l in [("2g","2.4 GHz"),("5g","5 GHz"),("2g+5g","2.4 + 5 GHz (beide)")])
        enc_opts = "".join(f"<option value='{v}' {'selected' if w.get('encryption','sae-mixed')==v else ''}>{l}</option>"
            for v,l in [("sae-mixed","SAE-mixed (WPA2+WPA3)"),("psk-mixed","PSK-mixed (WPA2)"),("sae","SAE (WPA3 only)"),("none","Offen")])
        r11_opts = "".join(f"<option value='{v}' {'selected' if w.get('r80211','1')==v else ''}>{l}</option>"
            for v,l in [("1","aktiviert"),("0","deaktiviert")])
        en_opts = "".join(f"<option value='{v}' {'selected' if w.get('enabled','1')==v else ''}>{l}</option>"
            for v,l in [("1","aktiviert"),("0","deaktiviert")])
        vlan_val = w.get('vlan', 'lan')
        vlan_is_custom = vlan_val not in net_names
        vlan_custom_val = vlan_val if vlan_is_custom else ''
        vlan_opts = "".join(f"<option value='{n}' {'selected' if n==vlan_val else ''}>{n}</option>" for n in net_names)
        remove_btn = f"<button type='button' class='btn btn-red' style='font-size:.8em;padding:.2em .6em' onclick='removeWlan({i})'>Entfernen</button>" if len(wlans)>1 else ""

        tab_panels += f"""
<div id='wlan-panel-{i}' class='wlan-panel' style='display:{"block" if i==0 else "none"}'>
  <div class='card' style='margin-top:.5em'>
    <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:.7em'>
      <h3 style='margin:0'>WLAN {i+1}</h3>
      {remove_btn}
    </div>
    <table style='width:100%'>
      <tr><td style='width:170px'>Bezeichnung</td><td><input type='text' name='wlan_{i}_label' value='{w.get("label",f"WLAN {i+1}")}' oninput='updateTabLabel({i},this.value)' placeholder='z.B. Haupt-WLAN'></td></tr>
      <tr><td>SSID</td><td><input type='text' name='wlan_{i}_ssid' value='{w.get("ssid","")}' placeholder='Netzwerkname'></td></tr>
      <tr><td>Passwort</td><td><input type='text' name='wlan_{i}_psk' value='{w.get("psk","")}' placeholder='Mind. 8 Zeichen'></td></tr>
      <tr><td>Frequenzband</td><td><select name='wlan_{i}_band'>{band_opts}</select></td></tr>
      <tr><td>Verschluesselung</td><td><select name='wlan_{i}_encryption'>{enc_opts}</select></td></tr>
      <tr><td>VLAN/Netz</td><td>
        <select name='wlan_{i}_vlan' onchange='onVlanChange({i},this)'>{vlan_opts}<option value='__custom__' {'selected' if vlan_is_custom else ''} style='font-style:italic'>Andere…</option></select>
        <input type='text' id='vlan-c-{i}' placeholder='UCI-Interface-Name'
          style='display:{"block" if vlan_is_custom else "none"};margin-top:.3em;width:100%'
          value='{vlan_custom_val}'>
      </td></tr>
      <tr><td>802.11r Roaming</td><td><select name='wlan_{i}_r80211'>{r11_opts}</select></td></tr>
      <tr><td>Status</td><td><select name='wlan_{i}_enabled'>{en_opts}</select></td></tr>
    </table>
  </div>
</div>"""

    wlan_count = len(wlans)

    field_rows = [
        ("Allgemein",None,None),
        ("Beschreibung","description_special",p["description"]),
        ("Netzwerk",None,None),
        ("MGMT_NET","MGMT_NET","Ersten 3 Oktette z.B. 192.168.10"),
        ("Gateway","GW","z.B. 192.168.10.1"),
        ("DNS","DNS","AdGuard/Pi-hole oder Router-IP"),
        ("Mesh",None,None),
        ("Mesh aktiv","ENABLE_MESH","1=aktiv, 0=aus"),
        ("Mesh SSID","MESH_ID","Name des Mesh-Netzes"),
        ("Mesh Passwort","MESH_PSK","Passwort fuer Mesh"),
        ("Template",None,None),
    ]
    rows = ""
    for label,key,hint in field_rows:
        if key is None:
            rows += f"<tr><th colspan='3' style='color:#58a6ff'>{label}</th></tr>"
        elif key == "description_special":
            rows += f"<tr><td>{label}</td><td><input type='text' name='description' value='{hint}'></td><td></td></tr>"
        else:
            val = s.get(key,"")
            rows += f"<tr><td>{label}</td><td><input type='text' name='{key}' value='{val}' placeholder='{key}'></td><td class='muted' style='font-size:.85em'>{hint}</td></tr>"
    rows += f"<tr><td>Template-Datei</td><td><select name='template'>{tmpl_opts}</select></td><td class='muted'>uci-batch Template</td></tr>"

    # Netzwerke aus Settings laden
    networks = s.get("networks", {})
    if not networks:
        networks = {
            "lan":   {"proto":"static","ipaddr":"192.168.10.X","netmask":"255.255.255.0","gateway":"","vlan":"10"},
            "Media": {"proto":"static","ipaddr":"192.168.20.1","netmask":"255.255.255.0","gateway":"","vlan":"20"},
            "Worls": {"proto":"static","ipaddr":"192.168.30.1","netmask":"255.255.255.0","gateway":"","vlan":"30"},
            "Guest": {"proto":"static","ipaddr":"192.168.40.1","netmask":"255.255.255.0","gateway":"","vlan":"40"},
        }

    net_rows = ""
    for nname, nconf in networks.items():
        proto_opts = "".join(f"<option value='{v}' {'selected' if nconf.get('proto','static')==v else ''}>{v}</option>" for v in ["static","dhcp"])
        net_rows += f"""
<tr>
  <td><input type='text' name='net_name_{nname}' value='{nname}' style='width:80px' placeholder='Name'></td>
  <td><select name='net_proto_{nname}'>{proto_opts}</select></td>
  <td><input type='text' name='net_ipaddr_{nname}' value='{nconf.get("ipaddr","")}' placeholder='192.168.X.1'></td>
  <td><input type='text' name='net_netmask_{nname}' value='{nconf.get("netmask","255.255.255.0")}' placeholder='255.255.255.0' style='width:110px'></td>
  <td><input type='text' name='net_gateway_{nname}' value='{nconf.get("gateway","")}' placeholder='leer=kein GW' style='width:110px'></td>
  <td><input type='text' name='net_vlan_{nname}' value='{nconf.get("vlan","")}' placeholder='10' style='width:50px'></td>
  <td><button type='button' class='btn btn-red' style='font-size:.75em;padding:.15em .4em' onclick='removeNetRow(this)'>−</button></td>
</tr>"""

    net_section = f"""
<div class='card' id='net-config-card'>
  <h3 style='color:#58a6ff;margin-top:0'>🌐 Netzwerk-Interfaces</h3>
  <p class='muted' style='font-size:.85em'>IPs, VLANs und Gateways der Bridge-Interfaces (wird als Dropdown-Quelle für VLAN-Feld verwendet).</p>
  <table style='width:100%'>
    <thead><tr>
      <th style='width:90px'>Name</th><th style='width:80px'>Protokoll</th>
      <th>IP-Adresse</th><th>Netmask</th><th>Gateway</th><th style='width:55px'>VLAN</th><th style='width:35px'></th>
    </tr></thead>
    <tbody id='net-rows'>
{net_rows}
    </tbody>
  </table>
  <button type='button' class='btn btn-green' style='margin-top:.5em;font-size:.85em' onclick='addNetRow()'>+ Interface</button>
  <input type='hidden' name='net_count' id='net_count' value='{len(networks)}'>
  <input type='hidden' name='net_names_list' id='net_names_list' value='{",".join(networks.keys())}'>
</div>
<script>
function removeNetRow(btn) {{
  btn.closest('tr').remove();
  updateNetCount();
}}
function addNetRow() {{
  const tbody = document.getElementById('net-rows');
  const nm = 'new_' + Date.now();
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><input type='text' name='net_name_${{nm}}' value='' style='width:80px' placeholder='Name'></td>
    <td><select name='net_proto_${{nm}}'><option value='static' selected>static</option><option value='dhcp'>dhcp</option></select></td>
    <td><input type='text' name='net_ipaddr_${{nm}}' placeholder='192.168.X.1'></td>
    <td><input type='text' name='net_netmask_${{nm}}' value='255.255.255.0' style='width:110px'></td>
    <td><input type='text' name='net_gateway_${{nm}}' placeholder='leer=kein GW' style='width:110px'></td>
    <td><input type='text' name='net_vlan_${{nm}}' placeholder='10' style='width:50px'></td>
    <td><button type='button' class='btn btn-red' style='font-size:.75em;padding:.15em .4em' onclick='removeNetRow(this)'>−</button></td>`;
  tbody.appendChild(tr);
  const nnl = document.getElementById('net_names_list');
  nnl.value = (nnl.value ? nnl.value+',' : '') + nm;
  updateNetCount();
}}
function updateNetCount() {{
  document.getElementById('net_count').value = document.querySelectorAll('#net-rows tr').length;
}}
</script>"""

    content_html = f"""
<h2>Projekt: {name}</h2>
<div class='card card-blue'>
  Aenderungen gelten beim naechsten Provisioning.<br>
  Reset: <code>ssh root@IP "rm /etc/provisioned &amp;&amp; sh /etc/uci-defaults/99-provision"</code>
</div>
<form method="POST" action="/ui/projects/{name}/save" id='project-form'>
<input type='hidden' name='wlan_count' id='wlan_count' value='{wlan_count}'>

<div class='card'>
  <h3 style='color:#58a6ff;margin-top:0'>WLAN-Netzwerke</h3>
  <p class='muted' style='font-size:.85em'>Jedes WLAN wird als separate AP-Instanz konfiguriert. Du kannst beliebig viele hinzufuegen.</p>
  <div id='wlan-tabs' style='display:flex;gap:.4em;flex-wrap:wrap;margin-bottom:.5em'>
    {tab_buttons}
  </div>
  <div id='wlan-panels'>
    {tab_panels}
  </div>
  <div style='margin-top:.7em'>
    <button type='button' class='btn btn-green' onclick='addWlan()'>+ WLAN hinzufuegen</button>
    <span class='muted' style='font-size:.85em;margin-left:1em'>Aktuell: <span id='wlan-count-display'>{wlan_count}</span> WLAN(s)</span>
  </div>
</div>

{net_section}

<div class='card'>
  <h3 style='color:#58a6ff;margin-top:0'>Netzwerk &amp; Sonstiges</h3>
  <table>{rows}</table>
</div>

<input type='submit' value='Speichern' id='proj-save-btn'>
</form>
<script>
document.getElementById('proj-save-btn').addEventListener('click', function() {{
  for (let i = 0; i < wlanCount; i++) {{
    const sel = document.querySelector('[name="wlan_'+i+'_vlan"]');
    const ci = document.getElementById('vlan-c-'+i);
    if (sel && ci && sel.value === '__custom__' && ci.value.trim()) {{
      const opt = document.createElement('option');
      opt.value = ci.value.trim();
      opt.selected = true;
      sel.appendChild(opt);
    }}
  }}
}});
</script>
<hr>
<div class='card card-red'>
  <b>Projekt loeschen</b><br>
  Alle Geraete werden auf default zurueckgesetzt.
  <form method="POST" action="/ui/projects/{name}/delete" style='margin-top:.5em'>
    <input type='submit' class='btn btn-red' value='Loeschen' onclick="return confirm('Sicher?')">
  </form>
</div>

<style>
.wlan-tab-btn {{
  background:#21262d;color:#c9d1d9;border:1px solid #30363d;
  padding:.3em .8em;border-radius:4px;cursor:pointer;font-family:monospace;font-size:.85em;
  transition:all .15s;
}}
.wlan-tab-btn:hover {{ background:#30363d; }}
.wlan-tab-btn.active {{ background:#1f6feb;border-color:#1f6feb;color:#fff; }}
</style>

<script>
let wlanCount = {wlan_count};
const NET_NAMES = {net_names_json};
function onVlanChange(idx, sel) {{
  const ci = document.getElementById('vlan-c-' + idx);
  if (!ci) return;
  ci.style.display = sel.value === '__custom__' ? 'block' : 'none';
  if (sel.value === '__custom__') ci.focus();
}}

function renderVlanSelect(idx, val) {{
  let opts = NET_NAMES.map(n => `<option value="${{n}}"${{n===val?' selected':''}}>${{n}}</option>`).join('');
  opts += `<option value="__custom__" style="font-style:italic">Andere…</option>`;
  return `<select name="wlan_${{idx}}_vlan" onchange="onVlanChange(${{idx}},this)">${{opts}}</select>` +
         `<input type="text" id="vlan-c-${{idx}}" placeholder="UCI-Interface-Name" style="display:none;margin-top:.3em;width:100%">`;
}}

function switchTab(idx) {{
  document.querySelectorAll('.wlan-panel').forEach(p => p.style.display='none');
  document.querySelectorAll('.wlan-tab-btn').forEach(b => b.classList.remove('active'));
  const panel = document.getElementById('wlan-panel-'+idx);
  const btns = document.querySelectorAll('.wlan-tab-btn');
  if(panel) panel.style.display='block';
  if(btns[idx]) btns[idx].classList.add('active');
}}

function updateTabLabel(idx, val) {{
  const btn = document.querySelectorAll('.wlan-tab-btn')[idx];
  const ssidEl = document.querySelector('[name="wlan_'+idx+'_ssid"]');
  const ssidVal = ssidEl ? ssidEl.value : '';
  if(btn) btn.textContent = val + (ssidVal ? ': '+ssidVal : '');
}}

function addWlan() {{
  const idx = wlanCount;
  wlanCount++;
  document.getElementById('wlan_count').value = wlanCount;
  document.getElementById('wlan-count-display').textContent = wlanCount;

  const tabsDiv = document.getElementById('wlan-tabs');
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'wlan-tab-btn';
  btn.textContent = 'WLAN ' + (idx+1);
  btn.onclick = () => switchTab(idx);
  tabsDiv.appendChild(btn);

  const panelsDiv = document.getElementById('wlan-panels');
  const panel = document.createElement('div');
  panel.id = 'wlan-panel-'+idx;
  panel.className = 'wlan-panel';
  panel.style.display = 'none';
  panel.innerHTML = `
    <div class='card' style='margin-top:.5em'>
      <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:.7em'>
        <h3 style='margin:0'>WLAN ${{idx+1}}</h3>
        <button type='button' class='btn btn-red' style='font-size:.8em;padding:.2em .6em' onclick='removeWlan(${{idx}})'>Entfernen</button>
      </div>
      <table style='width:100%'>
        <tr><td style='width:170px'>Bezeichnung</td><td><input type='text' name='wlan_${{idx}}_label' value='WLAN ${{idx+1}}' oninput='updateTabLabel(${{idx}},this.value)'></td></tr>
        <tr><td>SSID</td><td><input type='text' name='wlan_${{idx}}_ssid'></td></tr>
        <tr><td>Passwort</td><td><input type='text' name='wlan_${{idx}}_psk'></td></tr>
        <tr><td>Frequenzband</td><td><select name='wlan_${{idx}}_band'>
          <option value='2g'>2.4 GHz</option><option value='5g'>5 GHz</option><option value='2g+5g' selected>2.4 + 5 GHz</option>
        </select></td></tr>
        <tr><td>Verschluesselung</td><td><select name='wlan_${{idx}}_encryption'>
          <option value='sae-mixed' selected>SAE-mixed (WPA2+WPA3)</option>
          <option value='psk-mixed'>PSK-mixed (WPA2)</option>
          <option value='sae'>SAE (WPA3 only)</option>
          <option value='none'>Offen</option>
        </select></td></tr>
        <tr><td>VLAN/Netz</td><td>${{renderVlanSelect(idx,'lan')}}</td></tr>
        <tr><td>802.11r Roaming</td><td><select name='wlan_${{idx}}_r80211'>
          <option value='1' selected>aktiviert</option><option value='0'>deaktiviert</option>
        </select></td></tr>
        <tr><td>Status</td><td><select name='wlan_${{idx}}_enabled'>
          <option value='1' selected>aktiviert</option><option value='0'>deaktiviert</option>
        </select></td></tr>
      </table>
    </div>`;
  panelsDiv.appendChild(panel);
  switchTab(idx);
}}

function removeWlan(idx) {{
  if (!confirm('WLAN ' + (idx+1) + ' wirklich entfernen?')) return;
  const panel = document.getElementById('wlan-panel-'+idx);
  if(panel) {{
    panel.style.display = 'none';
    const hidden = document.createElement('input');
    hidden.type='hidden'; hidden.name='wlan_'+idx+'_deleted'; hidden.value='1';
    panel.appendChild(hidden);
  }}
  const btns = [...document.querySelectorAll('.wlan-tab-btn')];
  if(btns[idx]) btns[idx].style.display='none';
  const visibleBtns = btns.filter((b,i) => b.style.display !== 'none');
  if (visibleBtns.length > 0) visibleBtns[0].click();
}}
</script>
"""
    return _page(content_html, f"Projekt {name}", "/ui/projects")

@app.post("/ui/projects/{name}/save", response_class=HTMLResponse)
async def ui_project_save(name: str, request: Request,
                           db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    form = await request.form()
    desc = str(form.get("description",""))
    keys = ["MGMT_NET","GW","DNS","ENABLE_MESH","MESH_ID","MESH_PSK","template"]
    s = {k: str(form.get(k,"")) for k in keys if form.get(k)}
    # WLANs dynamisch einlesen
    wlan_count = int(form.get("wlan_count","0") or 0)
    wlans = []
    for i in range(wlan_count):
        if form.get(f"wlan_{i}_deleted"):
            continue
        w = {}
        for field in ["label","ssid","psk","band","encryption","vlan","r80211","enabled"]:
            val = form.get(f"wlan_{i}_{field}")
            if val is not None:
                w[field] = str(val)
        if w.get("ssid") or w.get("label"):
            wlans.append(w)
    if wlans:
        s["wlans"] = wlans
        s["SSID"] = wlans[0].get("ssid","")
        s["WPA_PSK"] = wlans[0].get("psk","")
        s["ENABLE_11R"] = wlans[0].get("r80211","1")
    # Netzwerk-Interfaces einlesen
    net_names_list = str(form.get("net_names_list", ""))
    new_networks = {}
    for nname_key in [n.strip() for n in net_names_list.split(",") if n.strip()]:
        # Name kann überschrieben worden sein
        actual_name = str(form.get(f"net_name_{nname_key}", nname_key)).strip()
        if not actual_name:
            continue
        new_networks[actual_name] = {
            "proto":   str(form.get(f"net_proto_{nname_key}", "static")),
            "ipaddr":  str(form.get(f"net_ipaddr_{nname_key}", "")),
            "netmask": str(form.get(f"net_netmask_{nname_key}", "255.255.255.0")),
            "gateway": str(form.get(f"net_gateway_{nname_key}", "")),
            "vlan":    str(form.get(f"net_vlan_{nname_key}", "")),
        }
    if new_networks:
        s["networks"] = new_networks
    db.execute("UPDATE projects SET description=?,settings=? WHERE name=?",
               (desc, json.dumps(s), name))
    db.commit()
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/ui/projects/{name}">')

@app.post("/ui/projects/{name}/delete", response_class=HTMLResponse)
def ui_project_delete(name: str, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    if name == "default":
        return _page("<div class='card card-red'>❌ Default kann nicht gelöscht werden.</div>")
    db.execute("UPDATE devices SET project='default' WHERE project=?", (name,))
    db.execute("DELETE FROM projects WHERE name=?", (name,))
    db.commit()
    return HTMLResponse('<meta http-equiv="refresh" content="0;url=/ui/projects">')

# ─────────────────────────────────────────────────────────────────────────────
# UI: Templates
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/ui/templates", response_class=HTMLResponse)
def ui_templates(db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    templates = db.execute("SELECT name,updated_at FROM templates ORDER BY name").fetchall()
    rows = "".join(f"<tr><td>📄 {t['name']}</td><td class='muted'>{t['updated_at'] or '-'}</td>"
                   f"<td><a href='/ui/templates/{t['name']}'>✏️ Bearbeiten</a></td></tr>"
                   for t in templates)
    content = f"""
<h2>📋 Templates</h2>
<div class='card card-blue'>
  ℹ️ Templates sind uci-batch Scripts mit <code>{{{{VAR}}}}</code> Platzhaltern.<br>
  Variablen: <code>{{{{HOSTNAME}}}}</code> <code>{{{{MGMT_NET}}}}</code> <code>{{{{MGMT_SUFFIX}}}}</code>
  <code>{{{{GW}}}}</code> <code>{{{{DNS}}}}</code> <code>{{{{SSID}}}}</code> <code>{{{{WPA_PSK}}}}</code>
  <code>{{{{ENABLE_11R}}}}</code> <code>{{{{MOBILITY_DOMAIN}}}}</code> <code>{{{{MESH_BLOCK}}}}</code>
</div>
<table>
  <tr><th>Name</th><th>Geändert</th><th></th></tr>
  {rows}
</table>
<hr>
<h3>➕ Neues Template</h3>
<form method="POST" action="/ui/templates/new" style='display:flex;gap:.5em'>
  <input type='text' name='name' placeholder='template-name' style='width:200px'>
  <input type='submit' value='➕ Anlegen'>
</form>"""
    return _page(content, "Templates", "/ui/templates")

@app.post("/ui/templates/new", response_class=HTMLResponse)
def ui_template_new(name: str=Form(...), db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    db.execute("INSERT OR IGNORE INTO templates(name,content,updated_at) VALUES(?,?,?)",
               (name.strip(), "# Neues Template\n", now_utc().isoformat()))
    db.commit()
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/ui/templates/{name.strip()}">')

@app.get("/ui/templates/{tname}", response_class=HTMLResponse)
def ui_template_get(tname: str, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    t = db.execute("SELECT * FROM templates WHERE name=?", (tname,)).fetchone()
    if not t: raise HTTPException(404)
    worls_warn = ""
    if "network.Worls" in (t['content'] or ''):
        worls_warn = """<div class='card card-orange'>
  ⚠️ <b>Bekannter Tippfehler:</b> Dieses Template verwendet <code>network.Worls</code> (historisch für "Works/VPN").
  Umbenennung würde bestehende Geräte brechen. Absichtlich beibehalten – siehe CHANGELOG v0.3.0.
</div>"""
    content = f"""
<h2>📄 Template: {tname}</h2>
<div class='card card-blue'>
  Letzte Änderung: {t['updated_at'] or '-'}<br>
  <b>Verfügbare Variablen:</b>
  <code>{{{{HOSTNAME}}}}</code> <code>{{{{MGMT_NET}}}}</code> <code>{{{{MGMT_SUFFIX}}}}</code>
  <code>{{{{GW}}}}</code> <code>{{{{DNS}}}}</code> <code>{{{{SSID}}}}</code> <code>{{{{WPA_PSK}}}}</code>
  <code>{{{{ENABLE_11R}}}}</code> <code>{{{{MOBILITY_DOMAIN}}}}</code> <code>{{{{MESH_BLOCK}}}}</code>
</div>
{worls_warn}
<form method="POST" action="/ui/templates/{tname}">
  <textarea name='content' rows='50'>{t['content'] or ''}</textarea><br><br>
  <input type='submit' value='💾 Speichern'>
  <a class='btn btn-orange' href='/ui/templates/{tname}/validate' style='margin-left:.5em'>🔍 Validieren</a>
</form>"""
    return _page(content, tname, "/ui/templates")

@app.post("/ui/templates/{tname}", response_class=HTMLResponse)
def ui_template_save(tname: str, content: str=Form(...),
                     db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    db.execute("INSERT INTO templates(name,content,updated_at) VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at",
               (tname, content, now_utc().isoformat()))
    db.commit()
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/ui/templates/{tname}">')

@app.get("/ui/templates/{tname}/validate", response_class=HTMLResponse)
def ui_template_validate(tname: str, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    t = db.execute("SELECT content FROM templates WHERE name=?", (tname,)).fetchone()
    if not t: raise HTTPException(404)
    issues = validate_template(t["content"])
    if issues:
        html = "<div class='card card-orange'><b>⚠️ Hinweise:</b><ul>" + "".join(f"<li>{i}</li>" for i in issues) + "</ul></div>"
    else:
        html = "<div class='card card-green'>✅ Keine Probleme gefunden.</div>"
    content = f"<h2>🔍 Validierung: {tname}</h2>{html}<a class='btn' href='/ui/templates/{tname}'>↩ Zurück</a>"
    return _page(content)

# ─────────────────────────────────────────────────────────────────────────────
# UI: Rollen
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/ui/roles", response_class=HTMLResponse)
def ui_roles(db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    roles = db.execute("SELECT * FROM roles").fetchall()
    sections = ""
    for r in roles:
        sections += f"""
<div class='card'>
  <h3>🎭 {r['name']}</h3>
  <form method="POST" action="/ui/roles/{r['name']}">
    <table>
      <tr><td>📝 Beschreibung</td><td><input type='text' name='description' value='{r['description']}'></td></tr>
    </table>
    <p class='muted' style='font-size:.85em'>uci-batch Override – wird NACH dem Template angewendet:</p>
    <textarea name='overrides' rows='6'>{r['overrides'] or ''}</textarea><br>
    <input type='submit' value='💾 Speichern'>
  </form>
</div>"""
    content = f"""
<h2>🎭 Rollen</h2>
<div class='card card-blue'>
  ℹ️ Rollen-Overrides werden <b>nach</b> dem Template angewendet und können beliebige uci-Werte überschreiben.<br>
  <b>ap1</b> = Master-AP mit DHCP-Server &nbsp;|&nbsp;
  <b>node</b> = Client-AP ohne DHCP &nbsp;|&nbsp;
  <b>repeater</b> = Mesh-Leaf ohne DHCP und WAN
</div>
{sections}"""
    return _page(content, "Rollen", "/ui/roles")

@app.post("/ui/roles/{name}", response_class=HTMLResponse)
def ui_roles_save(name: str, description: str=Form(""), overrides: str=Form(""),
                  db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    db.execute("UPDATE roles SET description=?,overrides=? WHERE name=?", (description, overrides, name))
    db.commit()
    return HTMLResponse('<meta http-equiv="refresh" content="0;url=/ui/roles">')

# ─────────────────────────────────────────────────────────────────────────────
# UI: Globale Einstellungen
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/ui/settings", response_class=HTMLResponse)
def ui_settings_get(db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    s = get_settings(db)
    field_defs = [
        ("🌐 Netzwerk",None,None),
        ("🏠 MGMT_NET","MGMT_NET","Ersten 3 Oktette, z.B. 192.168.10"),
        ("🚪 Gateway GW","GW","z.B. 192.168.10.1"),
        ("🔍 DNS","DNS","AdGuard, Pi-hole oder Router"),
        ("📡 WLAN",None,None),
        ("📶 SSID","SSID","Haupt-WLAN Name"),
        ("🔑 WPA_PSK","WPA_PSK","WLAN Passwort"),
        ("🔄 802.11r","ENABLE_11R","1=aktiv, 0=aus"),
        ("🕸️ Mesh","ENABLE_MESH","1=aktiv, 0=aus"),
        ("🕸️ Mesh SSID","MESH_ID","Mesh-Netz Name"),
        ("🔐 Mesh PSK","MESH_PSK","Mesh Passwort"),
        ("🏷️ Sonstiges",None,None),
        ("📡 MGMT VLAN","MGMT_VLAN","VLAN-ID für Management"),
    ]
    rows = ""
    for label,key,hint in field_defs:
        if key is None:
            rows += f"<tr><th colspan='3' style='color:#58a6ff'>{label}</th></tr>"
        else:
            rows += f"<tr><td>{label}</td><td><input type='text' name='{key}' value='{s.get(key,'')}' placeholder='{key}'></td><td class='muted' style='font-size:.85em'>{hint}</td></tr>"

    saved_key = s.get("SSH_PRIVKEY", "").strip()
    key_status = ""
    key_fingerprint = ""
    if saved_key and _HAS_PARAMIKO:
        try:
            import io as _io
            for _kc in (_paramiko.RSAKey, _paramiko.Ed25519Key, _paramiko.ECDSAKey):
                try:
                    pk = _kc.from_private_key(_io.StringIO(saved_key))
                    key_fingerprint = pk.get_fingerprint().hex(":") if hasattr(pk.get_fingerprint(), "hex") else pk.get_fingerprint().hex()
                    key_status = f"<span class='ok'>✅ Konfiguriert ({_kc.__name__.replace('Key','')})</span> <code style='font-size:.78em'>{key_fingerprint[:23]}…</code>"
                    break
                except Exception:
                    continue
            if not key_status:
                key_status = "<span class='warn'>⚠️ Key gespeichert aber nicht lesbar (falsches Format?)</span>"
        except Exception:
            key_status = "<span class='warn'>⚠️ paramiko nicht verfügbar</span>"
    elif saved_key:
        key_status = "<span class='warn'>⚠️ Key gespeichert – paramiko nicht installiert</span>"
    else:
        key_status = "<span class='muted'>Nicht konfiguriert</span>"

    content = f"""
<h2>⚙️ Globale Einstellungen</h2>
<div class='card card-orange'>
  ⚠️ Diese Werte sind <b>Fallback-Defaults</b> – Projekt-Einstellungen haben immer Vorrang!<br>
  Für dein Privat-Setup: Werte unter <a href='/ui/projects/sECUREaP-privat'>📁 Projekt sECUREaP-privat</a> setzen.
</div>
<form method="POST" action="/ui/settings">
  <div class='card'><table>{rows}</table></div>
  <input type='submit' value='💾 Speichern'>
</form>

<div class='card card-teal' style='margin-top:1.5em'>
<h3 style='margin-top:0'>🗝️ SSH-Schlüssel-Verwaltung</h3>
<p class='muted' style='font-size:.87em;margin:.3em 0 .8em'>
  Ein gespeicherter Private-Key wird automatisch genutzt wenn das <b>Passwort-Feld leer</b> gelassen wird
  (in Config-Pull, Config-Push, Deploy, Diagnose). Leere Felder = SSH-Key-Auth.
</p>
<div style='margin-bottom:.7em'>Status: {key_status}</div>

<form method="POST" action="/api/settings/ssh-key" id="ssh-key-form">
  <table style='width:100%'>
    <tr><td style='width:160px;vertical-align:top'>🔐 Private Key</td>
        <td><textarea name='key_content' rows='8' placeholder='-----BEGIN OPENSSH PRIVATE KEY-----&#10;...&#10;-----END OPENSSH PRIVATE KEY-----' style='font-size:.78em'></textarea>
            <span class='muted' style='font-size:.8em'>PEM-Format (OpenSSH RSA, Ed25519 oder ECDSA). Leer lassen um Key zu entfernen.</span></td></tr>
  </table>
  <button type='submit' class='btn btn-teal' style='margin-top:.5em'>💾 SSH-Key speichern</button>
  <span id='key-save-msg' style='margin-left:1em;font-size:.85em'></span>
</form>
<script>
document.getElementById('ssh-key-form').addEventListener('submit', async function(e) {{
  e.preventDefault();
  const btn = this.querySelector('button[type=submit]');
  const msg = document.getElementById('key-save-msg');
  btn.disabled = true; btn.textContent = 'Speichere…';
  try {{
    const fd = new FormData(this);
    const r = await fetch('/api/settings/ssh-key', {{method:'POST', body:fd}});
    const j = await r.json();
    if (j.ok) {{ msg.className='ok'; msg.textContent = '✅ ' + (j.message||'Gespeichert'); }}
    else {{ msg.className='err'; msg.textContent = '❌ ' + (j.detail||'Fehler'); }}
    setTimeout(() => location.reload(), 1500);
  }} catch(ex) {{ msg.className='err'; msg.textContent = '❌ ' + ex; }}
  btn.disabled = false; btn.textContent = '💾 SSH-Key speichern';
}});
</script>

<hr style='margin:1em 0;border-color:#30363d'>
<h4 style='margin:.5em 0'>📤 Public Key auf Router installieren</h4>
<p class='muted' style='font-size:.87em;margin:.3em 0 .8em'>
  Verbindet sich per <b>Passwort</b> zum Router und fügt den Public Key in
  <code>~/.ssh/authorized_keys</code> ein. Danach kann ohne Passwort verbunden werden.
</p>
<div class='grid2' style='gap:.7em'>
<table style='width:100%'>
  <tr><td style='width:120px'>🌐 Router-IP</td><td><input type='text' id='ki-ip' value='192.168.10.1' placeholder='192.168.1.1'></td></tr>
  <tr><td>👤 Benutzer</td><td><input type='text' id='ki-user' value='root'></td></tr>
  <tr><td>🔑 Passwort</td><td><input type='password' id='ki-pass' placeholder='Router-Passwort'></td></tr>
</table>
<div>
  <button class='btn btn-teal' onclick='installKey()'>📤 Key installieren</button>
  <span class='muted' style='font-size:.8em;display:block;margin-top:.5em'>
    Fügt den Public Key zu ~/.ssh/authorized_keys des Routers hinzu.
  </span>
  <pre id='ki-log' style='min-height:40px;max-height:150px;overflow-y:auto;font-size:.78em;display:none;margin-top:.5em'></pre>
</div>
</div>
<script>
async function installKey() {{
  const log = document.getElementById('ki-log');
  log.style.display='block'; log.textContent='Verbinde…';
  const body = {{ip: document.getElementById('ki-ip').value,
                 user: document.getElementById('ki-user').value,
                 password: document.getElementById('ki-pass').value}};
  try {{
    const r = await fetch('/api/settings/ssh-key/install', {{method:'POST',
      headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(body)}});
    const j = await r.json();
    log.textContent = j.log || (j.ok ? '✅ Fertig' : '❌ ' + (j.detail||'Fehler'));
    log.className = j.ok ? 'ok' : 'err';
  }} catch(ex) {{ log.textContent = '❌ ' + ex; log.className='err'; }}
}}
</script>
</div>"""
    return _page(content, "Einstellungen", "/ui/settings")

@app.post("/ui/settings", response_class=HTMLResponse)
async def ui_settings_post(request: Request, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    form = await request.form()
    for k,v in form.items():
        db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, str(v)))
    db.commit()
    return HTMLResponse('<meta http-equiv="refresh" content="0;url=/ui/settings">')

@app.post("/api/settings/ssh-key")
async def api_save_ssh_key(request: Request, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    """Speichert oder löscht den SSH-Private-Key in der DB."""
    form = await request.form()
    key_content = (form.get("key_content") or "").strip()
    if key_content:
        # Grundlegende Validierung
        if "PRIVATE KEY" not in key_content and "BEGIN" not in key_content:
            return {"ok": False, "detail": "Kein gültiges PEM-Format erkannt"}
        if _HAS_PARAMIKO:
            import io as _io
            loaded = False
            for _kc in (_paramiko.RSAKey, _paramiko.Ed25519Key, _paramiko.ECDSAKey):
                try:
                    _kc.from_private_key(_io.StringIO(key_content))
                    loaded = True; break
                except Exception:
                    continue
            if not loaded:
                return {"ok": False, "detail": "Key konnte nicht geladen werden (RSA/Ed25519/ECDSA erwartet)"}
    db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('SSH_PRIVKEY',?)", (key_content,))
    db.commit()
    if key_content:
        return {"ok": True, "message": "SSH-Key gespeichert"}
    return {"ok": True, "message": "SSH-Key entfernt"}

@app.get("/api/settings/ssh-key/status")
def api_ssh_key_status(db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    """Gibt Konfigurationsstatus des SSH-Keys zurück."""
    row = db.execute("SELECT value FROM settings WHERE key='SSH_PRIVKEY'").fetchone()
    key = (row[0] or "").strip() if row else ""
    if not key:
        return {"configured": False}
    if not _HAS_PARAMIKO:
        return {"configured": True, "type": "unknown", "fingerprint_md5": "paramiko nicht verfügbar"}
    import io as _io
    for _kc in (_paramiko.RSAKey, _paramiko.Ed25519Key, _paramiko.ECDSAKey):
        try:
            pk = _kc.from_private_key(_io.StringIO(key))
            fp = pk.get_fingerprint().hex()
            return {"configured": True, "type": _kc.__name__.replace("Key",""), "fingerprint_md5": fp}
        except Exception:
            continue
    return {"configured": True, "type": "unbekannt", "fingerprint_md5": "nicht lesbar"}

@app.post("/api/settings/ssh-key/install")
async def api_ssh_key_install(request: Request, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    """Installiert den gespeicherten Public Key in ~/.ssh/authorized_keys auf einem Router."""
    body = await request.json()
    ip       = body.get("ip","").strip()
    user     = body.get("user","root").strip()
    password = body.get("password","").strip()
    if not ip:
        return {"ok": False, "detail": "IP fehlt"}
    row = db.execute("SELECT value FROM settings WHERE key='SSH_PRIVKEY'").fetchone()
    key = (row[0] or "").strip() if row else ""
    if not key:
        return {"ok": False, "detail": "Kein SSH-Key in Einstellungen gespeichert"}
    if not _HAS_PARAMIKO:
        return {"ok": False, "detail": "paramiko nicht installiert"}
    import io as _io
    pub_key = None
    for _kc in (_paramiko.RSAKey, _paramiko.Ed25519Key, _paramiko.ECDSAKey):
        try:
            pk = _kc.from_private_key(_io.StringIO(key))
            pub_key = f"{pk.get_name()} {pk.get_base64()}"
            break
        except Exception:
            continue
    if not pub_key:
        return {"ok": False, "detail": "Public Key konnte nicht aus Private Key extrahiert werden"}
    log_lines: list = []
    def logline(msg): log_lines.append(msg)
    try:
        logline(f"[{_ts()}] Verbinde mit {user}@{ip} ...")
        base = _build_base_ssh(ip, user, password, logline)
        rc, out, err = _ssh_exec(base, "echo SSH_OK", timeout=10)
        if "SSH_OK" not in out:
            return {"ok": False, "log": "\n".join(log_lines), "detail": "SSH-Verbindung fehlgeschlagen"}
        logline(f"[{_ts()}] ✅ SSH-Verbindung OK")
        safe_key = pub_key.replace("'", "'\\''")
        cmd = f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && echo '{safe_key}' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && echo KEY_INSTALLED"
        rc2, out2, err2 = _ssh_exec(base, cmd, timeout=15)
        if "KEY_INSTALLED" in out2:
            logline(f"[{_ts()}] ✅ Public Key in ~/.ssh/authorized_keys eingetragen")
            return {"ok": True, "log": "\n".join(log_lines)}
        else:
            logline(f"[{_ts()}] ❌ Fehler: {(err2 or out2)[:300]}")
            return {"ok": False, "log": "\n".join(log_lines), "detail": "Key-Installation fehlgeschlagen"}
    except Exception as e:
        logline(f"[{_ts()}] ❌ Exception: {e}")
        return {"ok": False, "log": "\n".join(log_lines), "detail": str(e)}

# ─────────────────────────────────────────────────────────────────────────────
# UI: Setup-Assistent + Script-Generator
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/ui/setup", response_class=HTMLResponse)
def ui_setup(request: Request, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    server_url = str(request.base_url).rstrip("/")
    projects  = db.execute("SELECT name FROM projects ORDER BY name").fetchall()
    proj_opts = "".join(f"<option value='{p['name']}'>{p['name']}</option>" for p in projects)

    content = f"""
<h2>🚀 Setup-Assistent</h2>

<div class='card card-blue'>
  <b>Wie funktioniert das Provisioning?</b><br>
  <ol style='margin:.5em 0;padding-left:1.5em'>
    <li>Router flashen (OpenWrt mit wpad-wolfssl)</li>
    <li>Provisioning-Script + provision.conf auf den Router kopieren</li>
    <li>Script ausführen → Router meldet sich hier an</li>
    <li>Im Dashboard: Rolle zuweisen (ap1=Master, node=Client)</li>
    <li>Fertig – alle APs haben identische WLAN/Firewall-Config</li>
  </ol>
</div>

<div class='grid2'>
<div>
<div class='card card-green'>
<h3>📋 Schritt 1: provision.conf</h3>
<p>Diese Konfigurationsdatei muss auf den Router unter <code>/etc/provision.conf</code>:</p>
<div style='display:flex;align-items:center;gap:.5em;margin-bottom:.4em'>
  <b style='font-size:.9em'>Inhalt:</b>
  <button class='btn' style='padding:.2em .7em;font-size:.8em' onclick='copyConf()'>📋 Kopieren</button>
  <a class='btn' href='/download/provision.conf' style='padding:.2em .7em;font-size:.8em'>⬇️ Download</a>
</div>
<pre id='conf-preview' style='margin:0;user-select:all'>SERVER={server_url}
TOKEN={ENROLLMENT_TOKEN}</pre>
</div>

<div class='card card-green'>
<h3>📤 Schritt 2: Dateien auf Router kopieren</h3>
<p>In der Windows CMD / PowerShell:</p>
<pre>scp 99-provision.sh root@192.168.1.1:/etc/uci-defaults/99-provision
scp provision.conf  root@192.168.1.1:/etc/provision.conf</pre>
<p>Router-IP ist beim ersten Boot meist <code>192.168.1.1</code></p>
</div>

<div class='card card-green'>
<h3>▶️ Schritt 3: Script ausführen</h3>
<pre>ssh root@192.168.1.1 "chmod +x /etc/uci-defaults/99-provision && sh /etc/uci-defaults/99-provision"</pre>
<p>→ Router erscheint danach im <a href='/ui/'>Dashboard</a></p>
</div>

<div class='card card-blue'>
<h3>📦 Benötigte Image-Pakete</h3>
<p class='muted' style='font-size:.85em'>OpenWrt-Image mit diesen Paketen bauen (ImageBuilder):</p>
<div style='display:flex;align-items:center;gap:.5em;margin-bottom:.4em'>
  <b style='font-size:.9em'>PACKAGES:</b>
  <button class='btn' style='padding:.2em .7em;font-size:.8em' onclick='copyPkgs()'>📋 Kopieren</button>
</div>
<pre id='pkg-line' style='margin:0 0 .5em;user-select:all;font-size:.82em'>wpad-wolfssl kmod-batman-adv batctl-full openssh-sftp-server -wpad-basic-mbedtls</pre>
<p class='muted' style='font-size:.8em;margin:0'>
  <b style='color:#c00'>-wpad-basic-mbedtls</b> → aus Image entfernen (Konflikt mit wpad-wolfssl)<br>
  ImageBuilder: <code>make image PACKAGES="..."</code>
</p>
</div>
</div>

<div>
<div class='card'>
<h3>⬇️ Downloads</h3>
<p class='muted' style='font-size:.9em'>Alle Dateien werden vom Server dynamisch generiert – immer aktuell:</p>
<div style='display:flex;flex-direction:column;gap:.6em;margin-top:.4em'>
  <div style='display:flex;align-items:center;gap:.6em'>
    <a class='btn btn-green' href='/download/99-provision.sh' style='white-space:nowrap'>⬇️ 99-provision.sh</a>
    <span class='muted' style='font-size:.8em'>Bootstrap-Script (Enrollment + Config)</span>
  </div>
  <div style='display:flex;align-items:center;gap:.6em'>
    <a class='btn' href='/download/provision.conf' style='white-space:nowrap'>⬇️ provision.conf</a>
    <span class='muted' style='font-size:.8em'>Server-Adresse + Token</span>
  </div>
  <div style='display:flex;align-items:center;gap:.6em'>
    <a class='btn btn-orange' href='/download/start.bat' style='white-space:nowrap'>⬇️ start.bat</a>
    <span class='muted' style='font-size:.8em'>Server-Startskript (Windows)</span>
  </div>
</div>
</div>

<div class='card'>
<h3>🔄 Gerät neu provisionieren</h3>
<p>Wenn du Änderungen übertragen willst:</p>
<pre># Flag löschen + sofort neu provisionieren
ssh root@IP "rm -f /etc/provisioned && sh /etc/uci-defaults/99-provision"

# Flag löschen + neustart
ssh root@IP "rm -f /etc/provisioned && reboot"</pre>
</div>

<div class='card card-orange'>
<h3>⚠️ Häufige Fehler</h3>
<ul style='font-size:.9em'>
  <li><b>wpad-Konflikt:</b> Nur <code>wpad-wolfssl</code> im Image, kein <code>wpad-basic-mbedtls</code></li>
  <li><b>DHCP-Konflikt:</b> Nur ap1-Gerät hat ignore=0, alle anderen ignore=1</li>
  <li><b>SSH-Fehler:</b> Router braucht 30s nach Boot bis SSH aktiv ist</li>
  <li><b>provision.conf nicht gefunden:</b> Datei muss unter <code>/etc/provision.conf</code> liegen</li>
  <li><b>Script läuft nicht:</b> <code>chmod +x /etc/uci-defaults/99-provision</code> nicht vergessen</li>
</ul>
</div>
</div>
</div>

<hr>
<h2>⚡ SSH-Schnellinstaller</h2>
<div class='card card-blue'>
  <b>Frisch geflashter Router?</b> Gib einfach IP, Benutzer und Passwort ein –
  der Server kopiert das Provisioning-Script automatisch und startet es.
  Nach ~10 Sekunden meldet sich das Gerät im Dashboard.
</div>
<div class='grid2'>
<div class='card card-green'>
<h3>🔌 Router direkt provisionieren</h3>
<p class='muted' style='font-size:.85em'>Wähle zuerst das Gerät im Dashboard aus und weise Rolle + Projekt zu.
Dann nutze den SSH-Installer direkt bei jedem Gerät unter "Deploy → SSH-Installer".
Oder hier für einen schnellen Erst-Deploy ohne Gerät in der DB:</p>
<table style='width:100%'>
  <tr><td style='width:130px'>🌐 IP</td><td><input type='text' id='q-ip' value='192.168.1.1' style='width:100%'></td></tr>
  <tr><td>👤 Benutzer</td><td><input type='text' id='q-user' value='root' style='width:100%'></td></tr>
  <tr><td>🔑 Passwort</td><td><input type='password' id='q-pass' style='width:100%'></td></tr>
  <tr><td>🖥️ Server-URL</td><td>
    <input type='text' id='q-server-url' value='{server_url}' style='width:100%'>
    <span class='muted' style='font-size:.78em'>URL die der Router erreichen kann
    (z.B. http://192.168.1.100:8000 wenn Router noch im 192.168.1.x-Netz ist)</span>
  </td></tr>
  <tr><td>🔍 Precheck</td><td><label style='cursor:pointer'>
    <input type='checkbox' id='q-precheck' style='width:auto;margin-right:.4em'>
    Precheck aktivieren (read-only vor Deploy)
  </label></td></tr>
  <tr><td>🔍 Nur Precheck</td><td><label style='cursor:pointer'>
    <input type='checkbox' id='q-precheck-only' style='width:auto;margin-right:.4em'>
    Nur Precheck – kein Deploy (dann stoppen)
  </label></td></tr>
</table>
<br>
<p class='muted' style='font-size:.85em'>Welches Script soll installiert werden?</p>
<select id='q-script' style='width:100%;margin-bottom:.7em'>
  <option value='provision'>Bootstrap-Script (Enrollment + Config-Download)</option>
</select>
<p class='muted' style='font-size:.82em;margin:.3em 0 .8em'>
  Das Script registriert das Gerät, lädt seine UCI-Config vom Server und wendet sie an.
  Nach ~10s erscheint es im <a href='/ui/'>Dashboard</a> – danach Projekt zuweisen falls nötig.
</p>
<button class='btn btn-green' onclick='quickInstall()'>⚡ Script installieren</button>
<div id='q-log-card' style='display:none;margin-top:.7em'>
<pre id='q-log' style='min-height:80px;max-height:250px;overflow-y:auto;font-size:.8em'></pre>
<div id='q-result'></div>
</div>
</div>
<div class='card'>
<h3>📋 Was macht der SSH-Installer?</h3>
<ol style='font-size:.9em;padding-left:1.3em'>
  <li>Verbindet sich per SSH zum Router</li>
  <li>Überträgt <code>99-provision.sh</code></li>
  <li>Löscht <code>/etc/provisioned</code> (Force-Reprovision)</li>
  <li>Führt das Script aus</li>
  <li>Router registriert sich automatisch hier</li>
</ol>
<p class='muted' style='font-size:.85em'>
  Der SSH-Installer nutzt <code>sshpass</code> für Passwort-Auth.<br>
  Falls nicht installiert: <code>apt install sshpass</code> (Linux/WSL).<br>
  Alternativ funktioniert SSH-Key-Auth ohne sshpass.
</p>
</div>
</div>

<script>
async function quickInstall() {{
  const ip            = document.getElementById('q-ip').value;
  const user          = document.getElementById('q-user').value;
  const pass          = document.getElementById('q-pass').value;
  const server_url    = document.getElementById('q-server-url').value;
  const precheck      = document.getElementById('q-precheck').checked;
  const precheck_only = document.getElementById('q-precheck-only').checked;
  document.getElementById('q-log-card').style.display = 'block';
  document.getElementById('q-log').textContent = 'Verbinde zu ' + ip + '...';
  const resp = await fetch('/api/setup/quick-ssh', {{
    method: 'POST',
    headers: {{'Content-Type':'application/json'}},
    body: JSON.stringify({{ip, user, password: pass, server_url, precheck, precheck_only}})
  }});
  const data = await resp.json();
  if (!data.job_id) {{
    document.getElementById('q-log').textContent = 'Fehler: ' + JSON.stringify(data);
    return;
  }}
  let done = false;
  while (!done) {{
    await new Promise(r => setTimeout(r, 1200));
    try {{
      const r = await fetch('/api/deploy/job/' + data.job_id);
      const d = await r.json();
      document.getElementById('q-log').textContent = d.log || 'Warte...';
      if (d.done) {{
        done = true;
        document.getElementById('q-result').innerHTML = d.success
          ? (d.precheck_only
            ? "<div class='card card-green' style='padding:.5em'>✅ Precheck erfolgreich – keine Änderungen am Router.</div>"
            : "<div class='card card-green' style='padding:.5em'>✅ Erfolgreich! Gerät erscheint bald im <a href='/ui/'>Dashboard</a></div>")
          : "<div class='card card-red' style='padding:.5em'>❌ Fehler – Log oben prüfen</div>";
      }}
    }} catch(e) {{ done = true; }}
  }}
}}

function copyConf() {{
  const text = document.getElementById('conf-preview').textContent;
  navigator.clipboard.writeText(text).then(() => {{
    const btns = document.querySelectorAll('[onclick="copyConf()"]');
    btns.forEach(b => {{
      const orig = b.textContent;
      b.textContent = '✅ Kopiert!';
      setTimeout(() => b.textContent = orig, 2000);
    }});
  }}).catch(() => {{
    // Fallback: Text markieren
    const el = document.getElementById('conf-preview');
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
  }});
}}

function copyPkgs() {{
  const text = document.getElementById('pkg-line').textContent;
  navigator.clipboard.writeText(text).then(() => {{
    const btns = document.querySelectorAll('[onclick="copyPkgs()"]');
    btns.forEach(b => {{
      const orig = b.textContent;
      b.textContent = '✅ Kopiert!';
      setTimeout(() => b.textContent = orig, 2000);
    }});
  }}).catch(() => {{
    const el = document.getElementById('pkg-line');
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(el);
    sel.removeAllRanges();
    sel.addRange(range);
  }});
}}
</script>"""
    return _page(content, "Setup", "/ui/setup")

# ─────────────────────────────────────────────────────────────────────────────
# API: Quick-SSH Install (Setup-Seite, ohne Gerät in DB)
# ─────────────────────────────────────────────────────────────────────────────
@app.post("/api/setup/quick-ssh")
async def api_quick_ssh(request: Request, db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    """Installiert 99-provision.sh per SSH auf einen Router ohne DB-Eintrag."""
    body = await request.json()
    ip            = body.get("ip","").strip()
    user          = body.get("user","root").strip()
    password      = body.get("password","")
    precheck      = bool(body.get("precheck", False))
    precheck_only = bool(body.get("precheck_only", False))
    if not ip:
        raise HTTPException(400, "IP fehlt")
    # server_url: kann vom Admin angegeben werden (falls Router anderes Subnet)
    # Fallback: request.base_url (Admin-URL)
    custom_server_url = body.get("server_url", "").strip()
    server_url = custom_server_url if custom_server_url else str(request.base_url).rstrip("/")
    # 99-provision.sh dynamisch generieren (kein statisches File noetig)
    script = _generate_provision_sh(server_url, ENROLLMENT_TOKEN)
    job_id = secrets.token_hex(8)
    _ssh_jobs[job_id] = {"status": "running", "log": "Starte...", "done": False,
                         "success": False, "precheck_only": precheck_only}
    t = threading.Thread(target=_ssh_push_job,
                         args=(job_id, ip, user, password, script, "unknown", DB_PATH,
                               precheck, precheck_only),
                         daemon=True)
    t.start()
    return {"job_id": job_id}

# ─────────────────────────────────────────────────────────────────────────────
# Downloads
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/download/99-provision.sh", response_class=PlainTextResponse)
def dl_provision_sh(request: Request, _=Depends(check_admin)):
    """Liefert das 99-provision.sh Bootstrap-Script dynamisch generiert."""
    server_url = str(request.base_url).rstrip("/")
    return PlainTextResponse(
        _generate_provision_sh(server_url, ENROLLMENT_TOKEN),
        headers={"Content-Disposition": 'attachment; filename="99-provision.sh"'}
    )

@app.get("/download/provision.conf", response_class=PlainTextResponse)
def dl_provision_conf(request: Request, _=Depends(check_admin)):
    """provision.conf dynamisch generiert – kein statisches File noetig."""
    # Server-URL aus request.base_url ermitteln (kein Query-Param mehr noetig)
    server_url = str(request.base_url).rstrip("/")
    # Optionaler Override via ?server=... (Rueckwaertskompatibilitaet)
    server_override = request.query_params.get("server", "")
    if server_override:
        # Falls server ohne Port, Standard-Port 8000 ergaenzen
        if ":" not in server_override:
            server_override = f"{server_override}:8000"
        server_url = f"http://{server_override}"
    return PlainTextResponse(
        f"SERVER={server_url}\nTOKEN={ENROLLMENT_TOKEN}\n",
        headers={"Content-Disposition": 'attachment; filename="provision.conf"'}
    )

@app.get("/download/start.bat", response_class=PlainTextResponse)
def dl_start_bat(_=Depends(check_admin)):
    """start.bat dynamisch generiert mit aktuellen Env-Werten."""
    content = f"""@echo off
REM OpenWrt Provisioning Server – Startskript
REM Anpassen: TOKEN und ADMIN_PASS aendern!

set ENROLLMENT_TOKEN={ENROLLMENT_TOKEN}
set ADMIN_USER={ADMIN_USER}
set ADMIN_PASS={ADMIN_PASS}
set HMAC_SECRET=CHANGE_ME_HMAC_SECRET

echo Starte Provisioning Server auf Port 8000...
echo Admin-UI: http://localhost:8000/ui/
echo.

C:\\Python313\\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8000
pause
"""
    return PlainTextResponse(
        content,
        headers={"Content-Disposition": 'attachment; filename="start.bat"'}
    )

@app.get("/provision.sh", response_class=PlainTextResponse)
def get_provision_sh(request: Request):
    server = str(request.base_url).rstrip("/")
    return f"""#!/bin/sh
SERVER="{server}"
TOKEN="{ENROLLMENT_TOKEN}"
cat > /etc/provision.conf <<EOF
SERVER=$SERVER
TOKEN=$TOKEN
EOF
wget -q -O /etc/uci-defaults/99-provision "$SERVER/download/99-provision.sh"
chmod +x /etc/uci-defaults/99-provision
sh /etc/uci-defaults/99-provision
"""


# ═════════════════════════════════════════════════════════════════════════════
# ██  v0.2.0 – CONFIG PULL → BEARBEITEN → DIRECT PUSH
# ═════════════════════════════════════════════════════════════════════════════
# Workflow:
#   1. Hauptrouter per SSH verbinden → uci export wireless + network (read-only)
#   2. Browser-Editor: WLANs vollständig bearbeiten
#      – VLAN/Netz wählbar aus allen UCI-Interfaces des Quell-Routers
#   3. Optional: Als Projekt + Template speichern
#   4. Direct-Push auf N Client-Router parallel
#      – UCI-direct (uci batch) ODER klassisches 99-provision.sh Script
#      – danach: uci commit wireless + wifi reload | full reboot
# ─────────────────────────────────────────────────────────────────────────────

# ── In-Memory Store: laufende Pull-Jobs ──────────────────────────────────────
_pulled_configs: Dict[str, Dict[str, Any]] = {}


# ─────────────────────────────────────────────────────────────────────────────
# UCI-Parser: uci export → strukturierte Dicts
# ─────────────────────────────────────────────────────────────────────────────

def _parse_uci_export(raw: str) -> Dict[str, Dict]:
    """Parst 'uci export <subsystem>' → {section_name: {_type, _opt{}, _list{}}}."""
    result: Dict[str, Dict] = {}
    current: Optional[str] = None
    anon_idx: Dict[str, int] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("package "):
            continue
        m = re.match(r"^config\s+(\S+)(?:\s+'?([^'\n]+)'?)?$", s)
        if m:
            ctype = m.group(1)
            cname = (m.group(2) or "").strip().strip("'")
            if not cname:
                idx = anon_idx.get(ctype, 0)
                cname = f"@{ctype}[{idx}]"
                anon_idx[ctype] = idx + 1
            current = cname
            result[current] = {"_type": ctype, "_opt": {}, "_list": {}}
            continue
        if current is None:
            continue
        m = re.match(r"^option\s+(\S+)\s+'?(.*?)'?\s*$", s)
        if m:
            result[current]["_opt"][m.group(1)] = m.group(2).strip("'")
            continue
        m = re.match(r"^list\s+(\S+)\s+'?(.*?)'?\s*$", s)
        if m:
            k, v = m.group(1), m.group(2).strip("'")
            result[current]["_list"].setdefault(k, []).append(v)
    return result


def _uci_show_to_export(show_raw: str) -> str:
    """Konvertiert 'uci show' Flat-Format → 'uci export' Sections-Format."""
    sects: Dict[str, Dict] = {}
    stypes: Dict[str, str] = {}
    for line in show_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^[^.]+\.([^.=]+)=(\S+)$", line)
        if m:
            sec, stype = m.group(1), m.group(2)
            stypes[sec] = stype
            sects.setdefault(sec, {})
            continue
        m = re.match(r"^[^.]+\.([^.]+)\.([^=]+)='?(.*?)'?$", line)
        if m:
            sec, opt, val = m.group(1), m.group(2), m.group(3).strip("'")
            sects.setdefault(sec, {})[opt] = val
    out = ["package wireless"]
    for sec, opts in sects.items():
        out.append(f"\nconfig {stypes.get(sec,'unknown')} '{sec}'")
        for k, v in opts.items():
            out.append(f"\toption {k} '{v}'")
    return "\n".join(out)


def _extract_wlans(parsed: Dict) -> List[Dict]:
    """wifi-iface Sektionen → strukturierte WLAN-Liste."""
    wlans = []
    for name, sec in parsed.items():
        if sec.get("_type") != "wifi-iface":
            continue
        o = sec["_opt"]
        wlans.append({
            "uci_name":        name,
            "ssid":            o.get("ssid", ""),
            "key":             o.get("key", ""),
            "encryption":      o.get("encryption", "none"),
            "device":          o.get("device", "radio0"),
            "network":         o.get("network", "lan"),
            "mode":            o.get("mode", "ap"),
            "disabled":        o.get("disabled", "0"),
            "ieee80211r":      o.get("ieee80211r", "0"),
            "ieee80211k":      o.get("ieee80211k", "0"),
            "ieee80211v":      o.get("ieee80211v", "0"),
            "ft_over_ds":      o.get("ft_over_ds", "0"),
            "mobility_domain": o.get("mobility_domain", ""),
            "nasid":           o.get("nasid", ""),
            "ieee80211w":      o.get("ieee80211w", "0"),
            "wds":             o.get("wds", "0"),
            "bss_transition":  o.get("bss_transition", "0"),
        })
    return wlans


def _extract_radios(parsed: Dict) -> List[Dict]:
    """wifi-device Sektionen → Radio-Liste."""
    return [
        {"uci_name": n, **sec["_opt"]}
        for n, sec in parsed.items()
        if sec.get("_type") == "wifi-device"
    ]


def _extract_networks(parsed: Dict) -> Dict[str, Dict]:
    """UCI interface-Sektionen → Netz-Dict (für VLAN-Dropdown im Editor)."""
    ifaces: Dict[str, Dict] = {}
    for name, sec in parsed.items():
        if sec.get("_type") != "interface":
            continue
        o, li = sec["_opt"], sec["_list"]
        ifaces[name] = {
            "proto":  o.get("proto", ""),
            "ipaddr": o.get("ipaddr", ""),
            "device": o.get("device", ""),
            "dns":    li.get("dns", []),
        }
    return ifaces


def _wlans_to_uci_set(wlans: List[Dict]) -> str:
    """WLAN-Dicts → UCI set-Befehle für Direct-Push (uci batch)."""
    lines: List[str] = []
    def s(iface: str, key: str, val: str):
        if val not in ("", None):
            lines.append(f"set wireless.{iface}.{key}='{val}'")
    for w in wlans:
        n = w.get("uci_name", "")
        if not n:
            continue
        s(n, "ssid",           w.get("ssid", ""))
        s(n, "key",            w.get("key", ""))
        s(n, "encryption",     w.get("encryption", ""))
        s(n, "network",        w.get("network", ""))
        s(n, "disabled",       w.get("disabled", "0"))
        s(n, "ieee80211r",     w.get("ieee80211r", "0"))
        s(n, "ieee80211k",     w.get("ieee80211k", "0"))
        s(n, "ieee80211v",     w.get("ieee80211v", "0"))
        s(n, "ieee80211w",     w.get("ieee80211w", "0"))
        s(n, "ft_over_ds",     w.get("ft_over_ds", "0"))
        s(n, "bss_transition", w.get("bss_transition", "0"))
        if w.get("mobility_domain"):
            s(n, "mobility_domain", w["mobility_domain"])
        if w.get("nasid"):
            s(n, "nasid", w["nasid"])
        if w.get("wds"):
            s(n, "wds", w["wds"])
    return "\n".join(lines)


def _wlans_to_uci_template(wlans: List[Dict]) -> str:
    """WLAN-Dicts → UCI-Template mit {{VAR}} Platzhaltern."""
    lines = [
        "# ── WLAN-Config (aus Config-Pull generiert) ─────────────────────",
        "# Variablen: {{HOSTNAME}} {{ENABLE_11R}} {{MOBILITY_DOMAIN}}",
    ]
    for w in wlans:
        n = w.get("uci_name", "")
        if not n:
            continue
        enc = w.get("encryption", "none")
        lines += [
            f"\n# WLAN: {w.get('ssid', n)} [{w.get('device','')}]",
            f"set wireless.{n}=wifi-iface",
            f"set wireless.{n}.device='{w.get('device','radio0')}'",
            f"set wireless.{n}.mode='ap'",
            f"set wireless.{n}.network='{w.get('network','lan')}'",
            f"set wireless.{n}.ssid='{w.get('ssid','')}'",
            f"set wireless.{n}.encryption='{enc}'",
        ]
        if enc not in ("none", "open"):
            lines.append(f"set wireless.{n}.key='{w.get('key','')}'")
        lines.append(f"set wireless.{n}.disabled='{w.get('disabled','0')}'")
        if w.get("ieee80211r") == "1":
            lines += [
                f"set wireless.{n}.ieee80211r='{{{{ENABLE_11R}}}}'",
                f"set wireless.{n}.mobility_domain='{{{{MOBILITY_DOMAIN}}}}'",
                f"set wireless.{n}.ft_over_ds='{w.get('ft_over_ds','0')}'",
                f"set wireless.{n}.ieee80211k='{w.get('ieee80211k','1')}'",
                f"set wireless.{n}.ieee80211v='{w.get('ieee80211v','1')}'",
            ]
        if w.get("ieee80211w", "0") not in ("", "0"):
            lines.append(f"set wireless.{n}.ieee80211w='{w.get('ieee80211w')}'")
        if w.get("wds") == "1":
            lines.append(f"set wireless.{n}.wds='1'")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# SSH-Pull-Job: Config vom Quell-Router lesen (read-only)
# ─────────────────────────────────────────────────────────────────────────────

def _ssh_pull_job(pull_id: str, ip: str, user: str, password: str, pull_mode: str):
    """Hintergrund-Thread: uci export wireless + network vom Router lesen."""
    log: List[str] = []
    def logline(msg: str):
        log.append(msg)
        if pull_id in _pulled_configs:
            _pulled_configs[pull_id]["log"] = "\n".join(log)

    def fail(msg: str):
        logline(f"[{_ts()}] ❌ {msg}")
        _pulled_configs[pull_id].update({
            "done": True, "success": False, "log": "\n".join(log)})

    try:
        logline(f"[{_ts()}] Verbinde mit {user}@{ip} (Modus: uci {pull_mode}) ...")
        base = _build_base_ssh(ip, user, password, logline, key_content=_get_saved_ssh_key())

        rc, out, _ = _ssh_exec(base, "echo PULL_OK", timeout=12)
        if "PULL_OK" not in out:
            return fail("SSH-Verbindung fehlgeschlagen – Passwort/IP prüfen")
        logline(f"[{_ts()}] ✅ SSH-Verbindung OK")

        _, hn, _ = _ssh_exec(base, "uname -n 2>/dev/null || echo unknown", timeout=6)
        hostname_router = hn.strip() or ip

        # Wireless lesen
        logline(f"[{_ts()}] Lese wireless-Config ...")
        cmd_w = "uci export wireless 2>&1" if pull_mode == "export" else "uci show wireless 2>&1"
        rc, raw_w, _ = _ssh_exec(base, cmd_w, timeout=20)
        if rc != 0 and not raw_w.strip():
            return fail(f"uci {pull_mode} wireless fehlgeschlagen (rc={rc})")
        logline(f"[{_ts()}] ✅ wireless: {len(raw_w.splitlines())} Zeilen")

        # Network lesen
        logline(f"[{_ts()}] Lese network-Config ...")
        cmd_n = "uci export network 2>&1" if pull_mode == "export" else "uci show network 2>&1"
        rc, raw_n, _ = _ssh_exec(base, cmd_n, timeout=20)
        if rc != 0 and not raw_n.strip():
            return fail(f"uci {pull_mode} network fehlgeschlagen (rc={rc})")
        logline(f"[{_ts()}] ✅ network: {len(raw_n.splitlines())} Zeilen")

        # Bei uci show: konvertieren
        if pull_mode == "show":
            raw_w = _uci_show_to_export(raw_w)
            raw_n = _uci_show_to_export(raw_n)

        # Parsen
        parsed_w = _parse_uci_export(raw_w)
        parsed_n = _parse_uci_export(raw_n)
        wlans    = _extract_wlans(parsed_w)
        radios   = _extract_radios(parsed_w)
        networks = _extract_networks(parsed_n)

        logline(f"[{_ts()}] 🔍 {len(wlans)} WLAN(s) · {len(radios)} Radio(s) · {len(networks)} Netz-Interface(s)")
        for w in wlans:
            flag = "🔴" if w["disabled"] == "1" else "🟢"
            logline(f"[{_ts()}]   {flag} {w['uci_name']}: SSID={w['ssid']!r} "
                    f"enc={w['encryption']} netz={w['network']}")

        _pulled_configs[pull_id].update({
            "done": True, "success": True,
            "ip": ip, "user": user, "hostname_router": hostname_router,
            "pull_mode": pull_mode,
            "wlans": wlans, "radios": radios, "networks": networks,
            "raw_wireless": raw_w, "raw_network": raw_n,
            "log": "\n".join(log),
        })

    except subprocess.TimeoutExpired:
        fail("Timeout – Router antwortet nicht (12s)")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Direct-Push-Job: UCI-Befehle direkt per SSH anwenden (kein Script-Upload)
# ─────────────────────────────────────────────────────────────────────────────

def _direct_push_job(job_id: str, ip: str, user: str, password: str,
                     uci_cmds: str, do_commit: bool, do_reload: bool,
                     do_reboot: bool, mac: str, db_path: str):
    """Hintergrund-Thread: uci batch → uci commit → wifi reload | reboot."""
    log: List[str] = []
    def logline(msg: str):
        log.append(msg)
        _ssh_jobs[job_id]["log"] = "\n".join(log)

    try:
        logline(f"[{_ts()}] Direct-Push → {user}@{ip}")
        base = _build_base_ssh(ip, user, password, logline, key_content=_get_saved_ssh_key())

        rc, out, _ = _ssh_exec(base, "echo DP_OK", timeout=12)
        if "DP_OK" not in out:
            raise RuntimeError("SSH-Verbindung fehlgeschlagen")
        logline(f"[{_ts()}] ✅ SSH OK")

        n_cmds = len([l for l in uci_cmds.splitlines() if l.strip()])
        logline(f"[{_ts()}] Übertrage {n_cmds} UCI-Befehl(e) via uci batch ...")
        rc, out, err = _ssh_exec(
            base, "uci batch 2>&1",
            stdin_data=(uci_cmds + "\n").encode(), timeout=30)
        combined = (out + err).strip()
        if combined:
            logline(f"[{_ts()}] UCI-Output: {combined[:500]}")
        if rc != 0:
            raise RuntimeError(f"uci batch rc={rc}: {combined[:200]}")
        for pat in _DEPLOY_FATAL_PATTERNS:
            if pat in combined:
                raise RuntimeError(f"Fehler erkannt ({pat!r}) in UCI-Output")
        logline(f"[{_ts()}] ✅ {n_cmds} UCI-Befehle angewendet")

        if do_commit:
            logline(f"[{_ts()}] uci commit wireless ...")
            rc, out, err = _ssh_exec(base, "uci commit wireless 2>&1", timeout=15)
            if rc != 0:
                raise RuntimeError(f"uci commit fehlgeschlagen: {(out+err)[:200]}")
            logline(f"[{_ts()}] ✅ uci commit OK")

        if do_reboot:
            logline(f"[{_ts()}] 🔄 Sende reboot ...")
            _ssh_exec(base, "sleep 2 && reboot &", timeout=6)
            logline(f"[{_ts()}] ✅ Reboot-Befehl gesendet – Router startet in ~2s neu")
        elif do_reload:
            logline(f"[{_ts()}] 📡 wifi reload ...")
            rc, out, err = _ssh_exec(base, "wifi reload 2>&1", timeout=30)
            logline(f"[{_ts()}] {'✅' if rc==0 else '⚠️'} wifi reload "
                    f"{'OK' if rc==0 else f'rc={rc} – '+((out+err)[:100])}")

        # DB-Update wenn MAC bekannt
        if mac and mac not in ("unknown", ""):
            try:
                conn = sqlite3.connect(db_path)
                conn.execute(
                    "UPDATE devices SET status=?,last_log=?,last_seen=? WHERE base_mac=?",
                    ("provisioned", "\n".join(log), now_utc().isoformat(), mac))
                conn.commit(); conn.close()
            except Exception:
                pass

        _ssh_jobs[job_id].update({"status": "done", "success": True})
        logline(f"[{_ts()}] ✅ Direct-Push abgeschlossen")

    except subprocess.TimeoutExpired:
        logline(f"[{_ts()}] ❌ Timeout")
        _ssh_jobs[job_id].update({"status": "done", "success": False})
    except Exception as e:
        logline(f"[{_ts()}] ❌ {e}")
        _ssh_jobs[job_id].update({"status": "done", "success": False})
    finally:
        _ssh_jobs[job_id]["done"] = True


# ─────────────────────────────────────────────────────────────────────────────
# API: Geräte als JSON (für Config-Pull "Aus Geräteliste laden"-Button)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/devices")
def api_devices_json(db: sqlite3.Connection = Depends(get_db), _=Depends(check_admin)):
    """Alle Geräte als JSON-Liste – für Config-Pull UI und externe Tools."""
    rows = db.execute(
        "SELECT base_mac, hostname, role, project, status, last_seen, last_ip, board_name, model "
        "FROM devices ORDER BY project, hostname"
    ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# API: Config-Pull
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/config-pull")
async def api_config_pull(request: Request, _=Depends(check_admin)):
    """Startet einen SSH-Pull-Job vom Quell-Router."""
    body     = await request.json()
    ip       = body.get("ip", "").strip()
    user     = body.get("user", "root").strip()
    password = body.get("password", "")
    mode     = body.get("mode", "export")
    if not ip:
        raise HTTPException(400, "IP fehlt")
    if mode not in ("export", "show"):
        mode = "export"
    pull_id = secrets.token_hex(8)
    _pulled_configs[pull_id] = {
        "done": False, "success": False, "log": "Starte...",
        "ip": ip, "user": user, "pull_mode": mode,
        "wlans": [], "radios": [], "networks": {},
        "raw_wireless": "", "raw_network": "",
        "created_at": now_utc().isoformat(),
    }
    threading.Thread(
        target=_ssh_pull_job,
        args=(pull_id, ip, user, password, mode),
        daemon=True).start()
    return {"pull_id": pull_id}


@app.get("/api/config-pull/{pull_id}")
def api_config_pull_status(pull_id: str, _=Depends(check_admin)):
    """Pull-Job Status + Ergebnis (ohne Passwort)."""
    cfg = _pulled_configs.get(pull_id)
    if not cfg:
        raise HTTPException(404, "Pull-Job nicht gefunden")
    return {k: v for k, v in cfg.items() if k != "password"}


@app.get("/api/config-pull/{pull_id}/raw/{subsystem}",
         response_class=PlainTextResponse)
def api_config_pull_raw(pull_id: str, subsystem: str, _=Depends(check_admin)):
    """Roh-UCI-Output (wireless oder network)."""
    cfg = _pulled_configs.get(pull_id)
    if not cfg:
        raise HTTPException(404)
    return cfg.get(f"raw_{subsystem}", f"# {subsystem} nicht verfügbar")


# ─────────────────────────────────────────────────────────────────────────────
# API: Direct-Push + Batch-Push
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/direct-push")
async def api_direct_push(request: Request, _=Depends(check_admin)):
    """Direkt-Push: UCI-Befehle auf einen Router anwenden (kein Script)."""
    body      = await request.json()
    ip        = body.get("ip", "").strip()
    user      = body.get("user", "root").strip()
    password  = body.get("password", "")
    uci_cmds  = body.get("uci_cmds", "").strip()
    do_commit = bool(body.get("do_commit", True))
    do_reload = bool(body.get("do_reload", True))
    do_reboot = bool(body.get("do_reboot", False))
    mac       = body.get("mac", "unknown")
    if not ip:
        raise HTTPException(400, "IP fehlt")
    if not uci_cmds:
        raise HTTPException(400, "Keine UCI-Befehle")
    job_id = secrets.token_hex(8)
    _ssh_jobs[job_id] = {"status": "running", "log": "Starte...",
                          "done": False, "success": False,
                          "precheck_only": False, "ip": ip}
    threading.Thread(
        target=_direct_push_job,
        args=(job_id, ip, user, password, uci_cmds,
              do_commit, do_reload, do_reboot, mac, DB_PATH),
        daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/batch-push")
async def api_batch_push(request: Request, _=Depends(check_admin)):
    """Batch-Push: gleiche UCI-Befehle auf mehrere Router parallel."""
    body      = await request.json()
    targets   = body.get("targets", [])
    uci_cmds  = body.get("uci_cmds", "").strip()
    do_commit = bool(body.get("do_commit", True))
    do_reload = bool(body.get("do_reload", True))
    do_reboot = bool(body.get("do_reboot", False))
    if not targets:
        raise HTTPException(400, "Keine Ziele angegeben")
    if not uci_cmds:
        raise HTTPException(400, "Keine UCI-Befehle")
    batch_id = secrets.token_hex(8)
    jobs: Dict[str, str] = {}
    for tgt in targets:
        ip = tgt.get("ip", "").strip()
        if not ip:
            continue
        jid = secrets.token_hex(8)
        _ssh_jobs[jid] = {"status": "running", "log": "Starte...",
                           "done": False, "success": False,
                           "precheck_only": False, "ip": ip}
        threading.Thread(
            target=_direct_push_job,
            args=(jid, ip, tgt.get("user", "root"),
                  tgt.get("password", ""), uci_cmds,
                  do_commit, do_reload, do_reboot,
                  tgt.get("mac", "unknown"), DB_PATH),
            daemon=True).start()
        jobs[ip] = jid
    return {"batch_id": batch_id, "jobs": jobs}


# ─────────────────────────────────────────────────────────────────────────────
# API: Pulled Config als Projekt / Template speichern
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/config-pull/{pull_id}/save-project")
async def api_save_as_project(pull_id: str, request: Request,
                               db: sqlite3.Connection = Depends(get_db),
                               _=Depends(check_admin)):
    body      = await request.json()
    proj_name = body.get("project_name", "").strip()
    wlans     = body.get("wlans", [])
    network   = body.get("network", {})
    if not proj_name:
        raise HTTPException(400, "Projektname fehlt")
    cfg = _pulled_configs.get(pull_id)
    if not cfg:
        raise HTTPException(404, "Pull-Job nicht gefunden")
    primary = next((w for w in wlans if w.get("disabled") != "1"),
                   wlans[0] if wlans else {})
    settings_dict = {
        "SSID":        primary.get("ssid", ""),
        "WPA_PSK":     primary.get("key", ""),
        "ENABLE_11R":  primary.get("ieee80211r", "0"),
        "ENABLE_MESH": "0",
        "template":    proj_name,
        "wlans":       wlans,
        "pulled_from": cfg.get("ip", ""),
    }
    # Netz-Infos als Strings hinzufügen
    for k, v in network.items():
        if isinstance(v, str):
            settings_dict[k] = v
    now_ = now_utc().isoformat()
    ex = db.execute("SELECT name FROM projects WHERE name=?", (proj_name,)).fetchone()
    if ex:
        db.execute("UPDATE projects SET settings=? WHERE name=?",
                   (json.dumps(settings_dict), proj_name))
    else:
        db.execute(
            "INSERT INTO projects(name,description,created_at,settings) VALUES(?,?,?,?)",
            (proj_name,
             f"📥 Aus Config-Pull {cfg.get('ip', '')} generiert",
             now_, json.dumps(settings_dict)))
    db.commit()
    return {"ok": True, "project": proj_name}


@app.post("/api/config-pull/{pull_id}/save-template")
async def api_save_as_template(pull_id: str, request: Request,
                                db: sqlite3.Connection = Depends(get_db),
                                _=Depends(check_admin)):
    body     = await request.json()
    tpl_name = body.get("template_name", "").strip()
    wlans    = body.get("wlans", [])
    if not tpl_name:
        raise HTTPException(400, "Template-Name fehlt")
    content  = _wlans_to_uci_template(wlans)
    now_     = now_utc().isoformat()
    ex = db.execute("SELECT id FROM templates WHERE name=?", (tpl_name,)).fetchone()
    if ex:
        db.execute("UPDATE templates SET content=?,updated_at=? WHERE name=?",
                   (content, now_, tpl_name))
    else:
        db.execute("INSERT INTO templates(name,content,updated_at) VALUES(?,?,?)",
                   (tpl_name, content, now_))
    db.commit()
    return {"ok": True, "template": tpl_name, "lines": content.count("\n") + 1}


# ─────────────────────────────────────────────────────────────────────────────
# UI: /ui/config-pull  –  Pull → Editor → Push Gesamtseite
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/ui/config-pull", response_class=HTMLResponse)
def ui_config_pull(_=Depends(check_admin)):
    content = r"""
<h2>📥 Config Pull → Bearbeiten → Push</h2>

<div class='card card-teal'>
  <b>Workflow:</b>
  Ziehe die WLAN-Config vom <b>Hauptrouter</b> per SSH (read-only) &rarr;
  bearbeite WLANs im Browser (SSID · Passwort · Verschlüsselung · Netz/VLAN · Roaming) &rarr;
  push direkt auf alle <b>Client-Router</b> &mdash; kein Script, kein Reboot-Zwang.<br>
  <span class='muted'>Optional: Config als Projekt oder Template für spätere Verwendung speichern.</span>
</div>

<!-- ═══ SCHRITT 1: PULL ══════════════════════════════════════════════════════ -->
<div class='card' id='s1'>
<h3>① Config vom Hauptrouter ziehen</h3>
<div class='grid2' style='gap:.7em;align-items:start'>
<div>
<table style='width:100%'>
  <tr><td style='width:110px'>🌐 Router-IP</td>
      <td><input type='text' id='p-ip' value='192.168.10.1'></td></tr>
  <tr><td>👤 Benutzer</td>
      <td><input type='text' id='p-user' value='root'></td></tr>
  <tr><td>🔑 Passwort</td>
      <td><input type='password' id='p-pass' autocomplete='current-password' placeholder='Leer = gespeicherter SSH-Key'>
          <span class='muted' style='font-size:.78em'>🗝️ Leer lassen um gespeicherten SSH-Key zu nutzen (<a href='/ui/settings'>Einstellungen</a>)</span></td></tr>
</table>
</div>
<div class='card' style='margin:0;padding:.7em'>
  <b>Pull-Methode wählen</b>
  <p class='muted' style='font-size:.82em;margin:.3em 0'>Beide Methoden sind read-only &ndash; kein Schreibzugriff auf den Quell-Router.</p>
  <label style='cursor:pointer;display:block;margin:.4em 0'>
    <input type='radio' name='pull-mode' value='export' checked style='width:auto;margin-right:.3em'>
    <b>uci export</b> <span class='badge badge-green' style='font-size:.75em'>empfohlen</span><br>
    <span class='muted' style='font-size:.82em'>Vollständiges Sections-Format inkl. Listenfelder (dns, ports).
    Beste Kompatibilität mit allen OpenWrt-Versionen.</span>
  </label>
  <label style='cursor:pointer;display:block;margin:.4em 0'>
    <input type='radio' name='pull-mode' value='show' style='width:auto;margin-right:.3em'>
    <b>uci show</b><br>
    <span class='muted' style='font-size:.82em'>Flaches Key=Value-Format. Wird server-seitig automatisch
    konvertiert. Nützlich wenn <code>export</code> nicht verfügbar ist.</span>
  </label>
</div>
</div>
<br>
<button class='btn btn-green' onclick='doPull()'>📥 Config ziehen</button>
<span class='muted' style='margin-left:.7em;font-size:.82em'>Nur Lesezugriff – keine Änderungen am Quell-Router</span>
<div id='p-wrap' style='display:none;margin-top:.7em'>
  <pre id='p-log' style='min-height:60px;max-height:180px;overflow-y:auto;font-size:.8em'></pre>
</div>
</div>

<!-- ═══ SCHRITT 2–4: EDITOR ══════════════════════════════════════════════════ -->
<div id='s2' style='display:none'>

<!-- Editor -->
<div class='card'>
<h3>② WLANs bearbeiten</h3>
<p class='muted' style='font-size:.85em'>
  Jeder Tab = ein WLAN-Interface. Das Netz/VLAN-Dropdown listet alle UCI-Interfaces
  des Quell-Routers. 🟢 = aktiv &middot; 🔴 = deaktiviert.
</p>
<div id='wlan-tabs' style='display:flex;gap:.3em;flex-wrap:wrap;margin-bottom:.7em'></div>
<div id='wlan-panels'></div>
</div>

<!-- Speichern -->
<div class='card'>
<h3>③ Als Projekt / Template speichern <span class='muted' style='font-weight:normal;font-size:.82em'>(optional)</span></h3>
<div class='grid2'>
<div>
  <b>💾 Projekt speichern</b>
  <p class='muted' style='font-size:.82em'>WLANs + Netz-Infos als neues Projekt im Server. Kann danach auf Geräte deployed werden.</p>
  <table style='width:100%'>
    <tr><td style='width:120px'>Projektname</td>
        <td><input type='text' id='sv-proj' placeholder='z.B. sECUREaP-v2'></td></tr>
  </table>
  <button class='btn btn-orange' style='margin-top:.5em' onclick='saveProject()'>💾 Projekt speichern</button>
  <div id='sv-proj-res' style='margin-top:.4em;font-size:.9em'></div>
</div>
<div>
  <b>📋 Template speichern</b>
  <p class='muted' style='font-size:.82em'>Generiert UCI-Template mit <code>{{ENABLE_11R}}</code> / <code>{{MOBILITY_DOMAIN}}</code> Variablen für spätere Deployments.</p>
  <table style='width:100%'>
    <tr><td style='width:120px'>Template-Name</td>
        <td><input type='text' id='sv-tpl' placeholder='z.B. wlan-config-v2'></td></tr>
  </table>
  <button class='btn btn-orange' style='margin-top:.5em' onclick='saveTemplate()'>📋 Template speichern</button>
  <div id='sv-tpl-res' style='margin-top:.4em;font-size:.9em'></div>
</div>
</div>
</div>

<!-- UCI Preview -->
<div class='card'>
<h3>④ UCI-Vorschau</h3>
<p class='muted' style='font-size:.85em'>Zeigt genau die UCI-Befehle, die auf die Client-Router gepusht werden.</p>
<button class='btn' onclick='refreshPreview()'>🔄 Vorschau aktualisieren</button>
<pre id='uci-prev' style='display:none;margin-top:.7em;font-size:.78em;max-height:280px;overflow-y:auto'></pre>
</div>

<!-- Push -->
<div class='card'>
<h3>⑤ Push auf Client-Router</h3>
<p class='muted' style='font-size:.85em'>
  Mehrere Router werden <b>parallel</b> angesteuert. Jeder bekommt einen Live-Log.
  Der Push läuft im Hintergrund &ndash; du kannst die Seite offen lassen.
</p>

<div class='grid2' style='margin-bottom:.7em'>
<div class='card card-blue' style='margin:0'>
  <b>Push-Methode</b>
  <label style='cursor:pointer;display:block;margin:.35em 0'>
    <input type='radio' name='push-m' value='direct' checked style='width:auto;margin-right:.3em'>
    <b>UCI direct</b> <span class='badge badge-teal' style='font-size:.75em'>empfohlen</span><br>
    <span class='muted' style='font-size:.82em'><code>uci batch</code> direkt per SSH. Kein Script-Upload nötig. Schnell.</span>
  </label>
  <label style='cursor:pointer;display:block;margin:.35em 0'>
    <input type='radio' name='push-m' value='script' style='width:auto;margin-right:.3em'>
    <b>99-provision.sh Script</b><br>
    <span class='muted' style='font-size:.82em'>Script übertragen + ausführen. Klassischer Weg, mit Precheck-Unterstützung.</span>
  </label>
</div>
<div class='card' style='margin:0'>
  <b>Nach dem Push</b>
  <label style='cursor:pointer;display:block;margin:.35em 0'>
    <input type='checkbox' id='o-commit' checked style='width:auto;margin-right:.3em'>
    <code>uci commit wireless</code> <span class='muted' style='font-size:.82em'>(empfohlen – macht Änderungen persistent)</span>
  </label>
  <label style='cursor:pointer;display:block;margin:.35em 0'>
    <input type='checkbox' id='o-reload' checked style='width:auto;margin-right:.3em'>
    <code>wifi reload</code> <span class='muted' style='font-size:.82em'>– WLAN neu starten ohne Reboot</span>
  </label>
  <label style='cursor:pointer;display:block;margin:.35em 0'>
    <input type='checkbox' id='o-reboot' style='width:auto;margin-right:.3em'>
    <code>reboot</code> <span class='muted' style='font-size:.82em'>– ganzen Router neu starten (überschreibt wifi reload)</span>
  </label>
</div>
</div>

<div class='card card-blue'>
  <b>🖥️ Ziel-Router</b>
  <p class='muted' style='font-size:.82em'>IP, User und Passwort für jeden Client-Router eingeben.
  Mit "Aus Geräteliste" werden alle bekannten Geräte aus der DB importiert.</p>
  <div id='targets' style='margin:.5em 0'></div>
  <div style='display:flex;gap:.5em;flex-wrap:wrap;margin-top:.4em'>
    <button class='btn' onclick='addTarget()'>➕ Router hinzufügen</button>
    <button class='btn btn-orange' onclick='loadFromDevices()'>📋 Aus Geräteliste laden</button>
  </div>
</div>

<button class='btn btn-green' style='margin-top:.7em;font-size:1em;padding:.5em 1.8em'
        onclick='doBatchPush()'>🚀 Push starten</button>

<div id='push-res' style='margin-top:1em'></div>
</div>

</div><!-- #s2 -->

<script>
// ── State ─────────────────────────────────────────────────────────────────
let pullId = null, pullData = null;
let wlanData = [];
let tgtIdx = 0;

// ── Konstanten ────────────────────────────────────────────────────────────
const ENC = [
  ['none',      '🔓 Offen (kein Passwort)'],
  ['psk',       '🔒 WPA2-PSK'],
  ['psk2',      '🔒 WPA2-PSK (erzwungen)'],
  ['psk-mixed', '🔒 WPA2+WPA3 Mixed (psk-mixed)'],
  ['sae',       '🔒 WPA3-SAE (only)'],
  ['sae-mixed', '🔒 WPA2+WPA3 SAE-Mixed ★'],
];
const MFP = [
  ['0','0 – Aus (Management Frames ungeschützt)'],
  ['1','1 – Optional (MFP möglich)'],
  ['2','2 – Pflicht (WPA3-Voraussetzung)'],
];

function esc(s){ return (s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ── Schritt 1: Pull ───────────────────────────────────────────────────────
async function doPull(){
  const ip   = document.getElementById('p-ip').value.trim();
  const user = document.getElementById('p-user').value.trim();
  const pass = document.getElementById('p-pass').value;
  const mode = document.querySelector('input[name=pull-mode]:checked').value;
  if(!ip){ alert('Bitte IP eingeben'); return; }
  document.getElementById('p-wrap').style.display = 'block';
  document.getElementById('p-log').textContent = 'Verbinde mit '+ip+' ...';
  document.getElementById('s2').style.display = 'none';

  const r = await fetch('/api/config-pull', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ip, user, password:pass, mode})});
  const d = await r.json();
  pullId = d.pull_id;

  let done = false;
  while(!done){
    await new Promise(r=>setTimeout(r,1100));
    try{
      const sr = await fetch('/api/config-pull/'+pullId);
      const sd = await sr.json();
      document.getElementById('p-log').textContent = sd.log || 'Warte...';
      if(sd.done){
        done = true; pullData = sd;
        if(sd.success){
          wlanData = JSON.parse(JSON.stringify(sd.wlans));
          buildEditor(sd.wlans, sd.networks||{});
          document.getElementById('s2').style.display = 'block';
          if(tgtIdx === 0) addTarget(sd.ip||'');
          document.getElementById('s2').scrollIntoView({behavior:'smooth'});
        } else {
          document.getElementById('p-log').textContent += '\n\n❌ Pull fehlgeschlagen – Log oben prüfen';
        }
      }
    } catch(e){ done=true; }
  }
}

// ── Schritt 2: WLAN-Editor ────────────────────────────────────────────────
function buildEditor(wlans, networks){
  const tabs = document.getElementById('wlan-tabs');
  const panels = document.getElementById('wlan-panels');
  tabs.innerHTML = ''; panels.innerHTML = '';

  wlans.forEach((w,i)=>{
    const active = w.disabled !== '1';
    const band   = w.device.includes('radio1') ? '5GHz' : w.device.includes('radio0') ? '2.4GHz' : w.device;

    // Tab-Button
    const tb = document.createElement('button');
    tb.className = 'btn tab-btn' + (i===0?' active':'');
    tb.style.background = active ? '#238636' : '#484f58';
    tb.innerHTML = `${active?'🟢':'🔴'} ${esc(w.ssid)||esc(w.uci_name)} <small>[${esc(band)}]</small>`;
    tb.onclick = ()=>showTab(i);
    tabs.appendChild(tb);

    // Netz-Optionen aus Quell-Router UCI-Interfaces
    const netOpts = Object.entries(networks).map(([n,d])=>
      `<option value="${esc(n)}" ${w.network===n?'selected':''}>${esc(n)}${d.ipaddr?' \u2013 '+d.ipaddr:''}</option>`
    ).join('') + `<option value="__custom__">✏️ Manuell eingeben …</option>`;

    const encOpts = ENC.map(([v,l])=>
      `<option value="${v}" ${w.encryption===v?'selected':''}>${l}</option>`).join('');
    const mfpOpts = MFP.map(([v,l])=>
      `<option value="${v}" ${(w.ieee80211w||'0')===v?'selected':''}>${l}</option>`).join('');

    const panel = document.createElement('div');
    panel.id = `wp-${i}`;
    panel.style.display = i===0 ? 'block' : 'none';
    panel.innerHTML = `
<div class='card' style='border-left:3px solid ${active?"#3fb950":"#484f58"}'>
  <div style='display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.3em;margin-bottom:.6em'>
    <b style='font-size:1.05em'>📡 ${esc(w.uci_name)}</b>
    <span>
      <span class='badge ${active?"badge-green":"badge-gray"}'>${active?'🟢 Aktiv':'🔴 Deaktiviert'}</span>
      <span class='badge badge-gray'>${esc(band)}</span>
      <span class='badge badge-gray'>Device: ${esc(w.device)}</span>
    </span>
  </div>
  <table style='width:100%'>
    <tr>
      <td style='width:170px'>SSID</td>
      <td><input type='text' id='w${i}-ssid' value="${esc(w.ssid)}"
          oninput='wlanData[${i}].ssid=this.value;refreshTab(${i})'></td>
    </tr>
    <tr>
      <td>Passwort / Key</td>
      <td><input type='text' id='w${i}-key' value="${esc(w.key)}"
          placeholder='${w.encryption==="none"?"(kein Passwort – Netz ist offen)":"WLAN-Passwort eingeben"}'
          oninput='wlanData[${i}].key=this.value'></td>
    </tr>
    <tr>
      <td>Verschlüsselung</td>
      <td><select id='w${i}-enc' onchange='wlanData[${i}].encryption=this.value'>${encOpts}</select></td>
    </tr>
    <tr>
      <td>Netz / VLAN</td>
      <td>
        <select id='w${i}-net' style='width:100%' onchange='onNetChange(${i},this)'>${netOpts}</select>
        <input type='text' id='w${i}-netc' placeholder='UCI-Interface-Name (z.B. Worls, IoT, Guest)'
          style='display:none;margin-top:.3em' oninput='wlanData[${i}].network=this.value'>
      </td>
    </tr>
    <tr>
      <td>MFP (ieee80211w)</td>
      <td><select id='w${i}-mfp' onchange='wlanData[${i}].ieee80211w=this.value'>${mfpOpts}</select></td>
    </tr>
    <tr>
      <td>802.11r Roaming</td>
      <td>
        <label style='cursor:pointer'>
          <input type='checkbox' id='w${i}-r' style='width:auto;margin-right:.3em'
            ${w.ieee80211r==='1'?'checked':''}
            onchange='wlanData[${i}].ieee80211r=this.checked?"1":"0";toggleRoam(${i},this.checked)'>
          aktivieren
        </label>
        <span id='w${i}-roam' style='font-size:.88em;${w.ieee80211r==="1"?"":"display:none"}'>
          &nbsp;Mobility-Domain:&nbsp;<input type='text' id='w${i}-md' value="${esc(w.mobility_domain)}"
            style='width:60px;display:inline' placeholder='a1b2'
            oninput='wlanData[${i}].mobility_domain=this.value'>
          &nbsp;NAS-ID:&nbsp;<input type='text' id='w${i}-nas' value="${esc(w.nasid)}"
            style='width:80px;display:inline' placeholder='auto'
            oninput='wlanData[${i}].nasid=this.value'>
        </span>
      </td>
    </tr>
    <tr>
      <td>802.11k / 802.11v</td>
      <td>
        <label style='cursor:pointer;margin-right:1em'>
          <input type='checkbox' style='width:auto;margin-right:.2em'
            ${w.ieee80211k==='1'?'checked':''}
            onchange='wlanData[${i}].ieee80211k=this.checked?"1":"0"'>802.11k (RRM)
        </label>
        <label style='cursor:pointer'>
          <input type='checkbox' style='width:auto;margin-right:.2em'
            ${w.ieee80211v==='1'?'checked':''}
            onchange='wlanData[${i}].ieee80211v=this.checked?"1":"0"'>802.11v (BTM)
        </label>
      </td>
    </tr>
    <tr>
      <td>WDS (Bridge-Modus)</td>
      <td><label style='cursor:pointer'>
        <input type='checkbox' style='width:auto;margin-right:.3em'
          ${w.wds==='1'?'checked':''}
          onchange='wlanData[${i}].wds=this.checked?"1":"0"'>aktiviert
      </label></td>
    </tr>
    <tr>
      <td>WLAN aktiv</td>
      <td><label style='cursor:pointer'>
        <input type='checkbox' style='width:auto;margin-right:.3em'
          ${w.disabled!=='1'?'checked':''}
          onchange='wlanData[${i}].disabled=this.checked?"0":"1";refreshTab(${i})'>
        eingeschaltet (<code>disabled=0</code>)
      </label></td>
    </tr>
  </table>
</div>`;
    panels.appendChild(panel);
  });
  window._wc = wlans.length;
}

function showTab(idx){
  for(let i=0;i<window._wc;i++){
    const p=document.getElementById('wp-'+i);
    if(p) p.style.display = i===idx?'block':'none';
  }
  document.querySelectorAll('.tab-btn').forEach((b,i)=>b.classList.toggle('active',i===idx));
}

function refreshTab(idx){
  const w = wlanData[idx];
  const tabs = document.querySelectorAll('.tab-btn');
  if(!tabs[idx]) return;
  const active = w.disabled !== '1';
  const band = w.device?.includes('radio1')?'5GHz':w.device?.includes('radio0')?'2.4GHz':(w.device||'');
  tabs[idx].style.background = active ? '#238636' : '#484f58';
  tabs[idx].innerHTML = `${active?'🟢':'🔴'} ${esc(w.ssid||w.uci_name)} <small>[${esc(band)}]</small>`;
}

function toggleRoam(i, on){
  const el = document.getElementById('w'+i+'-roam');
  if(el) el.style.display = on ? '' : 'none';
}

function onNetChange(i, sel){
  const c = document.getElementById('w'+i+'-netc');
  if(sel.value === '__custom__'){
    c.style.display = 'block';
    wlanData[i].network = '';
  } else {
    c.style.display = 'none';
    wlanData[i].network = sel.value;
  }
}

// ── UCI-Build ─────────────────────────────────────────────────────────────
function buildUci(){
  const lines = [];
  wlanData.forEach(w=>{
    if(!w.uci_name) return;
    const n = w.uci_name;
    const s = (k,v)=>{ if(v!==''&&v!=null) lines.push(`set wireless.${n}.${k}='${v}'`); };
    s('ssid',           w.ssid||'');
    s('key',            w.key||'');
    s('encryption',     w.encryption||'');
    s('network',        w.network||'');
    s('disabled',       w.disabled||'0');
    s('ieee80211r',     w.ieee80211r||'0');
    s('ieee80211k',     w.ieee80211k||'0');
    s('ieee80211v',     w.ieee80211v||'0');
    s('ieee80211w',     w.ieee80211w||'0');
    s('ft_over_ds',     w.ft_over_ds||'0');
    s('bss_transition', w.bss_transition||'0');
    if(w.mobility_domain) s('mobility_domain', w.mobility_domain);
    if(w.nasid)           s('nasid', w.nasid);
    if(w.wds)             s('wds', w.wds);
  });
  return lines.join('\n');
}

function refreshPreview(){
  const pre = document.getElementById('uci-prev');
  pre.textContent = buildUci() || '# (keine Änderungen – WLANs wurden nicht geändert)';
  pre.style.display = 'block';
}

// ── Speichern ─────────────────────────────────────────────────────────────
async function saveProject(){
  const name = document.getElementById('sv-proj').value.trim();
  if(!name){ alert('Projektname eingeben'); return; }
  const r = await fetch(`/api/config-pull/${pullId}/save-project`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({project_name:name, wlans:wlanData,
                          network: pullData?.networks||{}})});
  const d = await r.json();
  document.getElementById('sv-proj-res').innerHTML = d.ok
    ? `<span class='ok'>✅ Projekt <b>${esc(name)}</b> gespeichert &ndash;
       <a href='/ui/projects/${encodeURIComponent(name)}'>im Editor öffnen →</a></span>`
    : `<span class='err'>❌ Fehler: ${JSON.stringify(d)}</span>`;
}

async function saveTemplate(){
  const name = document.getElementById('sv-tpl').value.trim();
  if(!name){ alert('Template-Name eingeben'); return; }
  const r = await fetch(`/api/config-pull/${pullId}/save-template`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({template_name:name, wlans:wlanData})});
  const d = await r.json();
  document.getElementById('sv-tpl-res').innerHTML = d.ok
    ? `<span class='ok'>✅ Template <b>${esc(name)}</b> gespeichert (${d.lines} Zeilen) &ndash;
       <a href='/ui/templates/${encodeURIComponent(name)}'>Template öffnen →</a></span>`
    : `<span class='err'>❌ Fehler: ${JSON.stringify(d)}</span>`;
}

// ── Ziel-Router ───────────────────────────────────────────────────────────
function addTarget(ip='', mac='', user='root', label=''){
  const i = tgtIdx++;
  const div = document.createElement('div');
  div.id = `tg-${i}`;
  div.style = 'display:flex;gap:.4em;align-items:center;flex-wrap:wrap;margin:.3em 0;padding:.4em;background:#0d1117;border-radius:4px';
  const ipPlaceholder = label ? label+' – IP' : 'IP-Adresse';
  div.innerHTML = `
    <span class='muted' style='min-width:20px;font-size:.82em'>#${i+1}</span>
    <input type='text'     id='tg${i}-ip'   value="${esc(ip)}"   placeholder='${esc(ipPlaceholder)}'  style='width:165px'>
    <input type='text'     id='tg${i}-user' value="${esc(user)}" placeholder='User'                  style='width:65px'>
    <input type='password' id='tg${i}-pass'                      placeholder='Passwort'              style='width:130px'>
    <input type='text'     id='tg${i}-mac'  value="${esc(mac)}"  placeholder='MAC (opt.)'            style='width:140px'>
    <button class='btn btn-red' style='padding:.2em .5em;flex-shrink:0'
      onclick='document.getElementById("tg-${i}").remove()'>✕</button>`;
  document.getElementById('targets').appendChild(div);
}

async function loadFromDevices(){
  try{
    const r = await fetch('/api/devices');
    if(!r.ok) throw new Error('HTTP '+r.status);
    const devs = await r.json();
    if(!devs.length){ alert('Keine Geräte in der Datenbank gefunden.'); return; }
    let added=0, noIp=0;
    devs.forEach(d=>{
      const ip  = d.last_ip || '';
      const mac = (d.base_mac||'').toLowerCase();
      const lbl = d.hostname || d.base_mac;
      addTarget(ip, mac, 'root', lbl);
      added++;
      if(!ip) noIp++;
    });
    let msg = added+' Gerät(e) aus Datenbank geladen.';
    if(noIp>0) msg += '\n⚠️ '+noIp+' ohne bekannte IP – bitte manuell eintragen.';
    else        msg += '\n✅ Alle IPs aus letztem Claim übernommen.';
    alert(msg);
  } catch(e){
    alert('Fehler beim Laden: '+e.message);
  }
}

// ── Batch-Push ────────────────────────────────────────────────────────────
async function doBatchPush(){
  const uci = buildUci();
  if(!uci.trim()){
    alert('Keine UCI-Befehle generiert – WLANs wurden nicht geändert oder Pull fehlt.');
    return;
  }
  const method    = document.querySelector('input[name=push-m]:checked').value;
  const do_commit = document.getElementById('o-commit').checked;
  const do_reload = document.getElementById('o-reload').checked;
  const do_reboot = document.getElementById('o-reboot').checked;

  // Targets sammeln
  const targets = [];
  document.querySelectorAll('[id^="tg"][id$="-ip"]').forEach(el=>{
    const b = el.id.replace('-ip','');
    const ip = el.value.trim();
    if(!ip) return;
    targets.push({
      ip,
      user:     document.getElementById(b+'-user')?.value || 'root',
      password: document.getElementById(b+'-pass')?.value || '',
      mac:      document.getElementById(b+'-mac')?.value  || 'unknown',
    });
  });

  if(!targets.length){ alert('Kein Ziel-Router angegeben – bitte IP-Adresse eintragen.'); return; }

  const resEl = document.getElementById('push-res');
  resEl.innerHTML = `<div class='card card-blue'>🚀 Push läuft auf ${targets.length} Router(n) …</div>`;

  // API aufrufen
  const resp = await fetch('/api/batch-push', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({targets, uci_cmds:uci, do_commit, do_reload, do_reboot})});
  const data = await resp.json();
  const jobs = data.jobs || {};

  // Result-Cards aufbauen
  resEl.innerHTML = '';
  const header = document.createElement('h3');
  header.textContent = `📋 Push-Status (${Object.keys(jobs).length} Router)`;
  resEl.appendChild(header);

  Object.entries(jobs).forEach(([ip, jid])=>{
    const card = document.createElement('div');
    card.className = 'card';
    card.style.marginBottom = '.5em';
    card.innerHTML = `
      <div style='display:flex;align-items:center;gap:.5em;margin-bottom:.4em;flex-wrap:wrap'>
        <b>🖥️ ${esc(ip)}</b>
        <span id='st-${jid}' class='badge badge-gray'>⏳ läuft</span>
        <span id='dur-${jid}' class='muted' style='font-size:.8em'></span>
      </div>
      <pre id='lg-${jid}' style='font-size:.77em;max-height:160px;overflow-y:auto;margin:0'></pre>`;
    resEl.appendChild(card);
  });

  // Alle Jobs parallel pollen
  const t0 = Date.now();
  await Promise.all(Object.entries(jobs).map(async ([ip, jid])=>{
    let done = false;
    while(!done){
      await new Promise(r=>setTimeout(r,1200));
      try{
        const r  = await fetch('/api/deploy/job/'+jid);
        const d  = await r.json();
        const lg = document.getElementById('lg-'+jid);
        const st = document.getElementById('st-'+jid);
        const dr = document.getElementById('dur-'+jid);
        if(lg){ lg.textContent = d.log||''; lg.scrollTop = lg.scrollHeight; }
        if(dr) dr.textContent = `${((Date.now()-t0)/1000).toFixed(0)}s`;
        if(d.done){
          done = true;
          if(st) st.outerHTML = d.success
            ? `<span class='badge badge-green'>✅ Erfolg</span>`
            : `<span class='badge badge-red'>❌ Fehler</span>`;
        }
      } catch(e){ done = true; }
    }
  }));
}
</script>
"""
    return _page(content, "Config Pull", "/ui/config-pull")


# ═════════════════════════════════════════════════════════════════════════════
# ██  CONFIG-PULL → EDIT → DIRECT-PUSH  (v0.2.0)
# ═════════════════════════════════════════════════════════════════════════════
# Workflow:
#  1. SSH-Pull vom Hauptrouter   →  uci export wireless + network (read-only)
#  2. Browser-Editor: WLANs vollständig bearbeiten, Netz/VLAN aus UCI wählbar
#  3. Optional: als Projekt / Template im Server speichern
#  4. Direct-Push: UCI-batch direkt via SSH oder via Script,
#     danach uci commit + wifi reload ODER full reboot
# ─────────────────────────────────────────────────────────────────────────────

# ── JSON-API: Geräteliste (für "Aus Geräteliste laden" in Config-Pull) ───────
@app.get("/api/devices")
def api_devices_list(db: sqlite3.Connection = Depends(get_db),
                     _=Depends(check_admin)):
    """Gibt alle Geräte als JSON zurück (für Config-Pull Batch-Push)."""
    rows = db.execute(
        "SELECT base_mac, hostname, last_ip, role, project, status FROM devices"
        " ORDER BY hostname"
    ).fetchall()
    # last_ip ist nicht im Schema – wir ermitteln es aus last_log (IP steht im ersten Log-Satz)
    result = []
    for row in rows:
        d = dict(row)
        # Versuche IP aus last_log zu extrahieren (Format: "[HH:MM:SS] ... root@1.2.3.4 ...")
        last_ip = ""
        log = d.get("last_log") or ""
        m = re.search(r"root@([\d.]+)", log)
        if m:
            last_ip = m.group(1)
        result.append({
            "mac":      d.get("base_mac", ""),
            "hostname": d.get("hostname", ""),
            "last_ip":  last_ip,
            "role":     d.get("role", ""),
            "project":  d.get("project", ""),
            "status":   d.get("status", ""),
        })
    return result


# ── In-Memory-Store für gezogene Configs ─────────────────────────────────────
# pull_id → { done, success, log, ip, user, wlans[], radios[], networks{}, raw_* }
_pulled_configs: Dict[str, Dict[str, Any]] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Hilfsfunktionen: UCI-Parse + WLAN-Extraktion + UCI-Generierung
# ─────────────────────────────────────────────────────────────────────────────

def _parse_uci_export(raw: str) -> Dict[str, Dict]:
    """Parst 'uci export <subsystem>' Output in ein Dict:
    section_name -> {_type: str, _opt: {key:val}, _list: {key:[vals]}}
    """
    result: Dict[str, Dict] = {}
    current: Optional[str] = None
    anon_count: Dict[str, int] = {}
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("package "):
            continue
        m = re.match(r"^config\s+(\S+)(?:\s+'?([^'\n]+)'?)?\s*$", s)
        if m:
            ctype = m.group(1)
            cname = (m.group(2) or "").strip().strip("'")
            if not cname:
                idx = anon_count.get(ctype, 0)
                cname = f"@{ctype}[{idx}]"
                anon_count[ctype] = idx + 1
            current = cname
            result[current] = {"_type": ctype, "_opt": {}, "_list": {}}
            continue
        if current is None:
            continue
        m = re.match(r"^option\s+(\S+)\s+'?(.*?)'?\s*$", s)
        if m:
            result[current]["_opt"][m.group(1)] = m.group(2).strip("'")
            continue
        m = re.match(r"^list\s+(\S+)\s+'?(.*?)'?\s*$", s)
        if m:
            k, v = m.group(1), m.group(2).strip("'")
            result[current]["_list"].setdefault(k, []).append(v)
    return result


def _uci_show_to_export(show_raw: str) -> str:
    """Konvertiert 'uci show' Flat-Format → 'uci export' Sections-Format.
    uci show liefert: wireless.wifinet0=wifi-iface / wireless.wifinet0.ssid='X'
    """
    sections: Dict[str, Dict] = {}
    section_types: Dict[str, str] = {}
    pkg = "wireless"
    for line in show_raw.splitlines():
        line = line.strip()
        if not line:
            continue
        # Paket ermitteln
        parts = line.split(".")
        if parts:
            pkg = parts[0]
        # Type-Zeile: pkg.section=type
        m = re.match(r"^[^.]+\.([^.=]+)=(\S+)$", line)
        if m:
            sec, stype = m.group(1), m.group(2)
            section_types[sec] = stype
            sections.setdefault(sec, {})
            continue
        # Option-Zeile: pkg.section.option='value'
        m = re.match(r"^[^.]+\.([^.]+)\.([^=]+)='?(.*?)'?\s*$", line)
        if m:
            sec, opt, val = m.group(1), m.group(2), m.group(3).strip("'")
            sections.setdefault(sec, {})[opt] = val
    lines = [f"package {pkg}"]
    for sec, opts in sections.items():
        stype = section_types.get(sec, "unknown")
        lines.append(f"\nconfig {stype} '{sec}'")
        for k, v in opts.items():
            lines.append(f"\toption {k} '{v}'")
    return "\n".join(lines)


def _extract_wlans(parsed: Dict) -> List[Dict]:
    """Extrahiert alle wifi-iface Sektionen als strukturierte WLAN-Dicts."""
    wlans = []
    for name, sec in parsed.items():
        if sec.get("_type") != "wifi-iface":
            continue
        o = sec["_opt"]
        wlans.append({
            "uci_name":        name,
            "ssid":            o.get("ssid", ""),
            "key":             o.get("key", ""),
            "encryption":      o.get("encryption", "none"),
            "device":          o.get("device", "radio0"),
            "network":         o.get("network", "lan"),
            "mode":            o.get("mode", "ap"),
            "disabled":        o.get("disabled", "0"),
            "ieee80211r":      o.get("ieee80211r", "0"),
            "ieee80211k":      o.get("ieee80211k", "0"),
            "ieee80211v":      o.get("ieee80211v", "0"),
            "ft_over_ds":      o.get("ft_over_ds", "0"),
            "mobility_domain": o.get("mobility_domain", ""),
            "nasid":           o.get("nasid", ""),
            "ieee80211w":      o.get("ieee80211w", "0"),
            "wds":             o.get("wds", "0"),
            "bss_transition":  o.get("bss_transition", "0"),
        })
    return wlans


def _extract_radios(parsed: Dict) -> List[Dict]:
    """Extrahiert wifi-device Sektionen (radio0, radio1 …)."""
    radios = []
    for name, sec in parsed.items():
        if sec.get("_type") != "wifi-device":
            continue
        o = sec["_opt"]
        radios.append({
            "uci_name": name,
            "band":     o.get("band", ""),
            "htmode":   o.get("htmode", ""),
            "channel":  o.get("channel", "auto"),
            "disabled": o.get("disabled", "0"),
            "country":  o.get("country", "DE"),
        })
    return radios


def _extract_networks(parsed: Dict) -> Dict[str, Dict]:
    """Extrahiert UCI-Interface-Sektionen (für VLAN-Auswahl im WLAN-Editor)."""
    ifaces: Dict[str, Dict] = {}
    for name, sec in parsed.items():
        if sec.get("_type") != "interface":
            continue
        o  = sec["_opt"]
        li = sec["_list"]
        ifaces[name] = {
            "proto":   o.get("proto", ""),
            "ipaddr":  o.get("ipaddr", ""),
            "netmask": o.get("netmask", ""),
            "gateway": o.get("gateway", ""),
            "device":  o.get("device", ""),
            "dns":     li.get("dns", []),
        }
    return ifaces


def _wlans_to_uci_set(wlans: List[Dict]) -> str:
    """Konvertiert bearbeitete WLANs in UCI-set-Befehle (für Direct-Push / Vorschau)."""
    lines: List[str] = []
    def s(iface: str, key: str, val: str):
        if val not in ("", None):
            lines.append(f"set wireless.{iface}.{key}='{val}'")
    for w in wlans:
        n = w.get("uci_name", "")
        if not n:
            continue
        s(n, "ssid",            w.get("ssid", ""))
        enc = w.get("encryption", "none")
        s(n, "encryption", enc)
        if enc not in ("none", "open", ""):
            s(n, "key", w.get("key", ""))
        s(n, "network",         w.get("network", ""))
        s(n, "disabled",        w.get("disabled", "0"))
        s(n, "ieee80211r",      w.get("ieee80211r", "0"))
        s(n, "ieee80211k",      w.get("ieee80211k", "0"))
        s(n, "ieee80211v",      w.get("ieee80211v", "0"))
        s(n, "ft_over_ds",      w.get("ft_over_ds", "0"))
        s(n, "bss_transition",  w.get("bss_transition", "0"))
        s(n, "ieee80211w",      w.get("ieee80211w", "0"))
        if w.get("mobility_domain"):
            s(n, "mobility_domain", w["mobility_domain"])
        if w.get("nasid"):
            s(n, "nasid", w["nasid"])
        if w.get("wds") == "1":
            s(n, "wds", "1")
    return "\n".join(lines)


def _wlans_to_uci_template(wlans: List[Dict]) -> str:
    """Konvertiert WLANs in ein UCI-Template-Format mit {{VAR}}-Platzhaltern."""
    lines = [
        "# ── WLAN-Config (aus Config-Pull generiert) ──────────────────────",
        "# Variablen: {{HOSTNAME}} {{ENABLE_11R}} {{MOBILITY_DOMAIN}}",
    ]
    for w in wlans:
        n = w.get("uci_name", "")
        if not n:
            continue
        lines.append(f"\n# WLAN: {w.get('ssid', n)}")
        lines.append(f"set wireless.{n}=wifi-iface")
        lines.append(f"set wireless.{n}.device='{w.get('device', 'radio0')}'")
        lines.append(f"set wireless.{n}.mode='ap'")
        lines.append(f"set wireless.{n}.network='{w.get('network', 'lan')}'")
        lines.append(f"set wireless.{n}.ssid='{w.get('ssid', '')}'")
        enc = w.get("encryption", "none")
        lines.append(f"set wireless.{n}.encryption='{enc}'")
        if enc not in ("none", "open", ""):
            lines.append(f"set wireless.{n}.key='{w.get('key', '')}'")
        lines.append(f"set wireless.{n}.disabled='{w.get('disabled', '0')}'")
        if w.get("ieee80211r") == "1":
            lines.append(f"set wireless.{n}.ieee80211r='{{{{ENABLE_11R}}}}'")
            lines.append(f"set wireless.{n}.mobility_domain='{{{{MOBILITY_DOMAIN}}}}'")
            lines.append(f"set wireless.{n}.ft_over_ds='{w.get('ft_over_ds', '0')}'")
            lines.append(f"set wireless.{n}.ieee80211k='{w.get('ieee80211k', '1')}'")
            lines.append(f"set wireless.{n}.ieee80211v='{w.get('ieee80211v', '1')}'")
        if w.get("ieee80211w", "0") not in ("", "0"):
            lines.append(f"set wireless.{n}.ieee80211w='{w.get('ieee80211w')}'")
        if w.get("wds") == "1":
            lines.append(f"set wireless.{n}.wds='1'")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# SSH-Pull-Job: liest uci export wireless + network vom Quell-Router
# ─────────────────────────────────────────────────────────────────────────────

def _ssh_pull_job(pull_id: str, ip: str, user: str, password: str, pull_mode: str):
    """Hintergrund-Thread: Zieht UCI-Config vom Quell-Router. pull_mode: 'export'|'show'"""
    log: List[str] = []

    def logline(msg: str):
        log.append(msg)
        if pull_id in _pulled_configs:
            _pulled_configs[pull_id]["log"] = "\n".join(log)

    def fail(msg: str):
        logline(f"[{_ts()}] ❌ {msg}")
        _pulled_configs[pull_id].update({
            "done": True, "success": False, "log": "\n".join(log)
        })

    try:
        logline(f"[{_ts()}] Verbinde mit {user}@{ip} (Modus: uci {pull_mode}) ...")
        base = _build_base_ssh(ip, user, password, logline, key_content=_get_saved_ssh_key())

        # Verbindungstest
        rc, out, _ = _ssh_exec(base, "echo PULL_OK", timeout=12)
        if "PULL_OK" not in out:
            return fail("SSH-Verbindung fehlgeschlagen – kein Echo zurück")
        logline(f"[{_ts()}] ✅ SSH-Verbindung OK")

        # Hostname vom Quell-Router
        _, hn, _ = _ssh_exec(base, "uname -n 2>/dev/null || echo unknown", timeout=6)
        hostname_router = hn.strip() or ip

        # ── Wireless ─────────────────────────────────────────────────────────
        logline(f"[{_ts()}] Lese wireless-Config (uci {pull_mode} wireless) ...")
        cmd_w = f"uci {pull_mode} wireless 2>&1"
        rc, raw_w, _ = _ssh_exec(base, cmd_w, timeout=20)
        if rc != 0 and not raw_w.strip():
            return fail(f"uci {pull_mode} wireless fehlgeschlagen (rc={rc})")
        logline(f"[{_ts()}] ✅ wireless: {len(raw_w.splitlines())} Zeilen")

        # ── Network ──────────────────────────────────────────────────────────
        logline(f"[{_ts()}] Lese network-Config (uci {pull_mode} network) ...")
        cmd_n = f"uci {pull_mode} network 2>&1"
        rc, raw_n, _ = _ssh_exec(base, cmd_n, timeout=20)
        if rc != 0 and not raw_n.strip():
            return fail(f"uci {pull_mode} network fehlgeschlagen (rc={rc})")
        logline(f"[{_ts()}] ✅ network: {len(raw_n.splitlines())} Zeilen")

        # ── Konvertieren (show → export Format) ───────────────────────────────
        if pull_mode == "show":
            logline(f"[{_ts()}] Konvertiere uci show → export Format ...")
            raw_w = _uci_show_to_export(raw_w)
            raw_n = _uci_show_to_export(raw_n)

        # ── Parsen ────────────────────────────────────────────────────────────
        parsed_w = _parse_uci_export(raw_w)
        parsed_n = _parse_uci_export(raw_n)
        wlans    = _extract_wlans(parsed_w)
        radios   = _extract_radios(parsed_w)
        networks = _extract_networks(parsed_n)

        logline(f"[{_ts()}] 🔍 {len(wlans)} WLAN(s), {len(radios)} Radio(s), "
                f"{len(networks)} Netz-Interface(s) gefunden")
        for w in wlans:
            flag = "🔴" if w["disabled"] == "1" else "🟢"
            logline(f"[{_ts()}]   {flag} {w['uci_name']}: "
                    f"SSID={w['ssid']!r} enc={w['encryption']} net={w['network']}")

        _pulled_configs[pull_id].update({
            "done": True, "success": True,
            "ip": ip, "user": user, "hostname_router": hostname_router,
            "pull_mode": pull_mode,
            "wlans":      wlans,
            "radios":     radios,
            "networks":   networks,
            "raw_wireless": raw_w,
            "raw_network":  raw_n,
            "log": "\n".join(log),
        })

    except subprocess.TimeoutExpired:
        fail("SSH-Timeout – Router antwortet nicht")
    except Exception as e:
        fail(f"{type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Direct-Push-Job: UCI-Befehle direkt per SSH anwenden (kein Script-Upload)
# ─────────────────────────────────────────────────────────────────────────────

def _direct_push_job(job_id: str, ip: str, user: str, password: str,
                     uci_cmds: str, do_commit: bool, do_reload: bool,
                     do_reboot: bool, mac: str, db_path: str):
    """Hintergrund-Thread: UCI-batch → uci commit wireless → wifi reload | reboot"""
    log: List[str] = []

    def logline(msg: str):
        log.append(msg)
        _ssh_jobs[job_id]["log"] = "\n".join(log)

    try:
        logline(f"[{_ts()}] Direct-Push → {user}@{ip}")
        base = _build_base_ssh(ip, user, password, logline, key_content=_get_saved_ssh_key())

        rc, out, _ = _ssh_exec(base, "echo DP_OK", timeout=12)
        if "DP_OK" not in out:
            raise RuntimeError("SSH-Verbindung fehlgeschlagen")
        logline(f"[{_ts()}] ✅ SSH OK")

        # UCI batch ausführen
        n_cmds = len([l for l in uci_cmds.splitlines() if l.strip()])
        logline(f"[{_ts()}] Übertrage {n_cmds} UCI-Befehl(e) via uci batch ...")
        rc, out, err = _ssh_exec(
            base, "uci batch 2>&1",
            stdin_data=(uci_cmds + "\n").encode(),
            timeout=30
        )
        combined = (out + err).strip()
        if combined:
            logline(f"[{_ts()}] UCI-Output: {combined[:500]}")
        if rc != 0:
            raise RuntimeError(f"uci batch fehlgeschlagen (rc={rc}): {combined[:200]}")
        for pat in _DEPLOY_FATAL_PATTERNS:
            if pat in combined:
                raise RuntimeError(f"Fehler-Pattern erkannt: '{pat}' im UCI-Output")
        logline(f"[{_ts()}] ✅ {n_cmds} UCI-Befehle angewendet")

        # uci commit
        if do_commit:
            logline(f"[{_ts()}] uci commit wireless ...")
            rc, out, err = _ssh_exec(base, "uci commit wireless 2>&1", timeout=15)
            if rc != 0:
                raise RuntimeError(f"uci commit fehlgeschlagen: {(out + err)[:200]}")
            logline(f"[{_ts()}] ✅ uci commit OK")

        # Reload oder Reboot
        if do_reboot:
            logline(f"[{_ts()}] 🔄 Sende reboot-Befehl ...")
            _ssh_exec(base, "sleep 2 && reboot &", timeout=8)
            logline(f"[{_ts()}] ✅ Reboot-Befehl gesendet (Router startet in ~2s neu)")
        elif do_reload:
            logline(f"[{_ts()}] 📡 wifi reload ...")
            rc, out, err = _ssh_exec(base, "wifi reload 2>&1", timeout=30)
            status = "OK" if rc == 0 else f"rc={rc} – {(out + err)[:100]}"
            logline(f"[{_ts()}] ✅ wifi reload {status}")

        # DB-Update wenn MAC bekannt
        if mac and mac not in ("unknown", ""):
            try:
                conn = sqlite3.connect(db_path)
                conn.execute(
                    "UPDATE devices SET status=?, last_log=?, last_seen=? WHERE base_mac=?",
                    ("provisioned", "\n".join(log), now_utc().isoformat(), mac)
                )
                conn.commit()
                conn.close()
                logline(f"[{_ts()}] 📝 DB-Status aktualisiert (MAC: {mac})")
            except Exception as db_err:
                logline(f"[{_ts()}] ⚠️ DB-Update fehlgeschlagen: {db_err}")

        _ssh_jobs[job_id].update({"status": "done", "success": True, "done": True})

    except subprocess.TimeoutExpired:
        logline(f"[{_ts()}] ❌ SSH-Timeout")
        _ssh_jobs[job_id].update({"status": "done", "success": False, "done": True})
    except Exception as e:
        logline(f"[{_ts()}] ❌ {type(e).__name__}: {e}")
        _ssh_jobs[job_id].update({"status": "done", "success": False, "done": True})


# ─────────────────────────────────────────────────────────────────────────────
# API-Endpunkte: Config-Pull
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/config-pull")
async def api_config_pull_start(request: Request, _=Depends(check_admin)):
    """Startet einen SSH-Pull-Job vom Quell-Router."""
    body     = await request.json()
    ip       = body.get("ip", "").strip()
    user     = body.get("user", "root").strip()
    password = body.get("password", "")
    mode     = body.get("mode", "export")
    if not ip:
        raise HTTPException(400, "IP fehlt")
    if mode not in ("export", "show"):
        mode = "export"
    pull_id = secrets.token_hex(8)
    _pulled_configs[pull_id] = {
        "done": False, "success": False, "log": "Starte SSH-Pull ...",
        "ip": ip, "user": user, "pull_mode": mode,
        "wlans": [], "radios": [], "networks": {},
        "raw_wireless": "", "raw_network": "",
        "hostname_router": "",
        "created_at": now_utc().isoformat(),
    }
    threading.Thread(
        target=_ssh_pull_job,
        args=(pull_id, ip, user, password, mode),
        daemon=True
    ).start()
    return {"pull_id": pull_id}


@app.get("/api/config-pull/{pull_id}")
def api_config_pull_status(pull_id: str, _=Depends(check_admin)):
    """Gibt Status + Ergebnis eines Pull-Jobs zurück."""
    cfg = _pulled_configs.get(pull_id)
    if not cfg:
        raise HTTPException(404, "Pull-Job nicht gefunden")
    # password niemals zurückgeben
    return {k: v for k, v in cfg.items() if k != "password"}


@app.get("/api/config-pull/{pull_id}/raw/{subsystem}",
         response_class=PlainTextResponse)
def api_config_pull_raw(pull_id: str, subsystem: str, _=Depends(check_admin)):
    """Liefert den Roh-UCI-Output (wireless oder network) als Download."""
    cfg = _pulled_configs.get(pull_id)
    if not cfg:
        raise HTTPException(404)
    key = f"raw_{subsystem}"
    return cfg.get(key, f"# {subsystem} nicht verfügbar")


@app.post("/api/config-pull/{pull_id}/save-project")
async def api_pull_save_project(pull_id: str, request: Request,
                                db: sqlite3.Connection = Depends(get_db),
                                _=Depends(check_admin)):
    """Speichert die bearbeiteten WLANs als neues Projekt."""
    body      = await request.json()
    proj_name = body.get("project_name", "").strip()
    wlans     = body.get("wlans", [])
    network   = body.get("network", {})
    if not proj_name:
        raise HTTPException(400, "Projektname fehlt")
    cfg = _pulled_configs.get(pull_id)
    if not cfg:
        raise HTTPException(404, "Pull-Job nicht gefunden")
    # Erstes aktives WLAN als primäres SSID/PSK (Rückwärtskompatibilität)
    primary = next((w for w in wlans if w.get("disabled") != "1"), wlans[0] if wlans else {})
    settings = {
        "SSID":        primary.get("ssid", ""),
        "WPA_PSK":     primary.get("key", ""),
        "ENABLE_11R":  primary.get("ieee80211r", "0"),
        "ENABLE_MESH": "0",
        "template":    "default",
        "wlans":       wlans,
        "pulled_from": cfg.get("ip", ""),
        **{k: v for k, v in network.items() if isinstance(v, str)},
    }
    now_ = now_utc().isoformat()
    existing = db.execute("SELECT name FROM projects WHERE name=?", (proj_name,)).fetchone()
    if existing:
        db.execute("UPDATE projects SET settings=? WHERE name=?",
                   (json.dumps(settings), proj_name))
    else:
        db.execute(
            "INSERT INTO projects(name, description, created_at, settings) VALUES(?,?,?,?)",
            (proj_name,
             f"📥 Aus Config-Pull {cfg.get('hostname_router', cfg.get('ip', ''))} generiert",
             now_, json.dumps(settings))
        )
    db.commit()
    return {"ok": True, "project": proj_name}


@app.post("/api/config-pull/{pull_id}/save-template")
async def api_pull_save_template(pull_id: str, request: Request,
                                 db: sqlite3.Connection = Depends(get_db),
                                 _=Depends(check_admin)):
    """Speichert die bearbeiteten WLANs als UCI-Template."""
    body     = await request.json()
    tpl_name = body.get("template_name", "").strip()
    wlans    = body.get("wlans", [])
    if not tpl_name:
        raise HTTPException(400, "Template-Name fehlt")
    tpl_content = _wlans_to_uci_template(wlans)
    now_ = now_utc().isoformat()
    existing = db.execute("SELECT id FROM templates WHERE name=?", (tpl_name,)).fetchone()
    if existing:
        db.execute("UPDATE templates SET content=?, updated_at=? WHERE name=?",
                   (tpl_content, now_, tpl_name))
    else:
        db.execute("INSERT INTO templates(name, content, updated_at) VALUES(?,?,?)",
                   (tpl_name, tpl_content, now_))
    db.commit()
    return {"ok": True, "template": tpl_name, "lines": tpl_content.count("\n") + 1}


# ─────────────────────────────────────────────────────────────────────────────
# API: Direct-Push (einzelner Router per UCI-batch)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/direct-push")
async def api_direct_push(request: Request, _=Depends(check_admin)):
    """UCI-Befehle direkt per SSH auf einen einzelnen Router anwenden."""
    body       = await request.json()
    ip         = body.get("ip", "").strip()
    user       = body.get("user", "root").strip()
    password   = body.get("password", "")
    uci_cmds   = body.get("uci_cmds", "").strip()
    do_commit  = bool(body.get("do_commit", True))
    do_reload  = bool(body.get("do_reload", True))
    do_reboot  = bool(body.get("do_reboot", False))
    mac        = body.get("mac", "unknown")
    if not ip:
        raise HTTPException(400, "IP fehlt")
    if not uci_cmds:
        raise HTTPException(400, "Keine UCI-Befehle")
    job_id = secrets.token_hex(8)
    _ssh_jobs[job_id] = {
        "status": "running", "log": "Starte Direct-Push ...",
        "done": False, "success": False, "precheck_only": False, "ip": ip
    }
    threading.Thread(
        target=_direct_push_job,
        args=(job_id, ip, user, password, uci_cmds,
              do_commit, do_reload, do_reboot, mac, DB_PATH),
        daemon=True
    ).start()
    return {"job_id": job_id}


# ─────────────────────────────────────────────────────────────────────────────
# API: Batch-Push (mehrere Router parallel)
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/batch-push")
async def api_batch_push(request: Request, _=Depends(check_admin)):
    """Gleiche UCI-Befehle parallel auf mehrere Ziel-Router anwenden."""
    body      = await request.json()
    targets   = body.get("targets", [])
    uci_cmds  = body.get("uci_cmds", "").strip()
    do_commit = bool(body.get("do_commit", True))
    do_reload = bool(body.get("do_reload", True))
    do_reboot = bool(body.get("do_reboot", False))
    if not targets:
        raise HTTPException(400, "Keine Ziele angegeben")
    if not uci_cmds:
        raise HTTPException(400, "Keine UCI-Befehle")
    batch_id = secrets.token_hex(8)
    jobs: Dict[str, str] = {}
    for tgt in targets:
        ip  = tgt.get("ip", "").strip()
        if not ip:
            continue
        user = tgt.get("user", "root")
        pw   = tgt.get("password", "")
        mac  = tgt.get("mac", "unknown")
        jid  = secrets.token_hex(8)
        _ssh_jobs[jid] = {
            "status": "running", "log": f"Starte Push → {ip} ...",
            "done": False, "success": False, "precheck_only": False, "ip": ip
        }
        threading.Thread(
            target=_direct_push_job,
            args=(jid, ip, user, pw, uci_cmds,
                  do_commit, do_reload, do_reboot, mac, DB_PATH),
            daemon=True
        ).start()
        jobs[ip] = jid
    return {"batch_id": batch_id, "jobs": jobs}


# ─────────────────────────────────────────────────────────────────────────────
# UI: /ui/config-pull  –  Pull → Edit → Push (komplette 5-Schritt-Oberfläche)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/ui/config-pull", response_class=HTMLResponse)
def ui_config_pull(_=Depends(check_admin)):
    content = r"""
<style>
/* ── Config-Pull spezifische Styles ───────────────────────────────── */
.cp-step{display:none}
.cp-step.active{display:block}
.tab-bar{display:flex;gap:.3em;flex-wrap:wrap;margin-bottom:.8em}
.tab-btn{padding:.35em .9em;border-radius:6px;border:none;cursor:pointer;
         font-size:.88em;color:#fff;transition:background .15s,outline .1s}
.tab-btn:hover{filter:brightness(1.2)}
.tab-btn.active-tab{outline:2px solid #58a6ff;outline-offset:2px}
.wlan-panel{display:none}
.wlan-panel.active{display:block}
.push-log-wrap pre{font-size:.77em;max-height:130px;overflow-y:auto;margin:.3em 0}
.status-badge{display:inline-block;padding:.15em .55em;border-radius:4px;
              font-size:.8em;font-weight:bold}
.sb-run{background:#1f4068;color:#90cdf4}
.sb-ok{background:#1a4731;color:#6ee7b7}
.sb-err{background:#4c1a1a;color:#fc8181}
.tgt-row{display:flex;gap:.4em;align-items:center;flex-wrap:wrap;
         padding:.45em;background:#161b22;border-radius:6px;margin:.3em 0}
.tgt-row input{background:#0d1117;border:1px solid #30363d;color:#e6edf3;
               padding:.3em .5em;border-radius:4px;font-size:.85em}
.opt-row{display:flex;gap:1.5em;flex-wrap:wrap;margin:.5em 0}
.opt-row label{display:flex;align-items:center;gap:.4em;cursor:pointer;font-size:.88em}
.opt-row input[type=checkbox]{width:auto}
.radio-card{background:#0d1117;border:1px solid #30363d;border-radius:6px;
            padding:.7em 1em;margin:.4em 0;cursor:pointer}
.radio-card input[type=radio]{width:auto;margin-right:.5em}
.radio-card.selected{border-color:#388bfd}
.cp-info{background:#0d2137;border-left:3px solid #388bfd;padding:.6em 1em;
          border-radius:0 6px 6px 0;margin:.5em 0;font-size:.88em}
.enc-badge{display:inline-block;padding:.1em .4em;border-radius:3px;
           font-size:.78em;background:#1c2128;margin-left:.4em}
</style>

<h2>📥 Config-Pull → Bearbeiten → Push</h2>
<div class='cp-info'>
  <b>Workflow:</b>
  ① Hauptrouter via SSH verbinden → Config ziehen  →
  ② WLANs im Browser bearbeiten (SSID, Passwort, VLAN, 802.11r …) →
  ③ Optional als Projekt / Template speichern  →
  ④ Direkt auf alle Client-Router pushen (parallel, ohne Script-Upload)
</div>

<!-- ═══ SCHRITT 1: PULL ══════════════════════════════════════════════════════ -->
<div class='card'>
<h3>① Config vom Hauptrouter ziehen</h3>
<div class='grid2' style='gap:.8em;align-items:start'>

<div>
<table style='width:100%'>
  <tr><td style='width:110px;padding:.3em 0'>🌐 Router-IP</td>
      <td><input type='text' id='pull-ip' value='192.168.10.1'
          placeholder='z.B. 192.168.10.1'></td></tr>
  <tr><td style='padding:.3em 0'>👤 Benutzer</td>
      <td><input type='text' id='pull-user' value='root'></td></tr>
  <tr><td style='padding:.3em 0'>🔑 Passwort</td>
      <td><input type='password' id='pull-pass'
          placeholder='leer = Key-Auth'></td></tr>
</table>
</div>

<div>
<b style='font-size:.9em'>📋 Pull-Methode</b>
<label class='radio-card selected' id='rc-export' onclick='selectMode("export")'>
  <input type='radio' name='pull-mode' value='export' checked>
  <b>uci export</b> <span class='enc-badge'>empfohlen</span><br>
  <span style='font-size:.82em;color:#8b949e'>
  Vollständige Config im Sections-Format inkl. Listenfelder (DNS, Ports).
  Beste Kompatibilität mit allen OpenWrt-Versionen.</span>
</label>
<label class='radio-card' id='rc-show' onclick='selectMode("show")'>
  <input type='radio' name='pull-mode' value='show'>
  <b>uci show</b><br>
  <span style='font-size:.82em;color:#8b949e'>
  Flaches Key=Value-Format, schneller bei großen Configs.
  Wird server-seitig automatisch in Sections-Format konvertiert.</span>
</label>
</div>
</div>

<div style='margin-top:.8em;display:flex;gap:.6em;align-items:center;flex-wrap:wrap'>
  <button class='btn btn-green' onclick='doPull()' id='btn-pull'>
    📥 Config ziehen
  </button>
  <span id='pull-spinner' style='display:none;color:#8b949e;font-size:.85em'>
    ⏳ Verbinde …
  </span>
</div>

<div id='pull-log-wrap' style='display:none;margin-top:.7em'>
  <pre id='pull-log'
       style='min-height:50px;max-height:200px;overflow-y:auto;font-size:.8em'></pre>
</div>
</div>

<!-- ═══ SCHRITT 2+3: EDITOR + SPEICHERN ══════════════════════════════════════ -->
<div id='editor-section' style='display:none'>

<div class='card'>
<h3>② WLANs bearbeiten</h3>
<div class='cp-info' id='pull-summary'></div>
<div class='tab-bar' id='wlan-tabs'></div>
<div id='wlan-panels'></div>
</div>

<div class='card'>
<h3>③ UCI-Vorschau &amp; Raw-Config</h3>
<div style='display:flex;gap:.5em;flex-wrap:wrap;margin-bottom:.6em'>
  <button class='btn' onclick='refreshPreview()'>🔄 UCI-Preview</button>
  <button class='btn' id='btn-raw-w' onclick='loadRaw("wireless")' style='display:none'>
    📄 Raw wireless
  </button>
  <button class='btn' id='btn-raw-n' onclick='loadRaw("network")' style='display:none'>
    📄 Raw network
  </button>
</div>
<pre id='uci-preview'
     style='display:none;font-size:.8em;max-height:260px;overflow-y:auto'></pre>
</div>

<div class='card'>
<h3>④ Als Projekt / Template speichern <span style='font-size:.8em;opacity:.6'>(optional)</span></h3>
<div class='grid2' style='gap:.8em'>
<div>
  <b style='font-size:.9em'>💾 Als Projekt</b><br>
  <input type='text' id='save-proj' placeholder='Projektname z.B. AP-Config-v2'
         style='width:100%;margin:.4em 0'>
  <button class='btn btn-orange' onclick='saveProject()'>💾 Speichern</button>
  <div id='res-proj' style='margin-top:.4em;font-size:.85em'></div>
</div>
<div>
  <b style='font-size:.9em'>📋 Als Template</b><br>
  <input type='text' id='save-tpl' placeholder='Template-Name z.B. wlan-v2'
         style='width:100%;margin:.4em 0'>
  <button class='btn btn-orange' onclick='saveTemplate()'>📋 Speichern</button>
  <div id='res-tpl' style='margin-top:.4em;font-size:.85em'></div>
</div>
</div>
</div>

</div><!-- /editor-section -->

<!-- ═══ SCHRITT 4: PUSH ══════════════════════════════════════════════════════ -->
<div id='push-section' style='display:none'>
<div class='card'>
<h3>⑤ Push auf Client-Router</h3>

<div class='grid2' style='gap:.8em;align-items:start'>
<div>
<b style='font-size:.9em'>⚙️ Push-Methode</b>
<label class='radio-card selected' id='pm-direct' onclick='selectPushMethod("direct")'>
  <input type='radio' name='push-method' value='direct' checked>
  <b>UCI direct</b> <span class='enc-badge'>empfohlen</span><br>
  <span style='font-size:.82em;color:#8b949e'>
    <code>uci batch</code> direkt via SSH – schnell, kein Script-Upload nötig.
    Ändert nur wireless-Config, nichts anderes.</span>
</label>
<label class='radio-card' id='pm-script' onclick='selectPushMethod("script")'>
  <input type='radio' name='push-method' value='script'>
  <b>Script-Upload</b><br>
  <span style='font-size:.82em;color:#8b949e'>
    99-provision.sh übertragen und ausführen (klassisch).
    Wendet die vollständige Projekt-Config an.</span>
</label>
</div>

<div>
<b style='font-size:.9em'>🔧 Nach dem Push</b>
<div class='opt-row' style='flex-direction:column;gap:.4em;margin-top:.4em'>
  <label>
    <input type='checkbox' id='opt-commit' checked>
    <code>uci commit wireless</code>
    <span style='color:#8b949e;font-size:.8em'>– Änderungen dauerhaft speichern</span>
  </label>
  <label>
    <input type='checkbox' id='opt-reload' checked>
    <code>wifi reload</code>
    <span style='color:#8b949e;font-size:.8em'>– WLAN neu starten, kein Reboot</span>
  </label>
  <label>
    <input type='checkbox' id='opt-reboot'>
    <code>reboot</code>
    <span style='color:#8b949e;font-size:.8em'>– Vollständiger Neustart (überschreibt wifi reload)</span>
  </label>
</div>
</div>
</div>

<div style='margin-top:1em'>
<b style='font-size:.9em'>🖥️ Ziel-Router</b>
<div id='targets-list' style='margin:.5em 0'></div>
<div style='display:flex;gap:.5em;flex-wrap:wrap;margin-top:.4em'>
  <button class='btn' onclick='addTarget()'>➕ Router hinzufügen</button>
  <button class='btn btn-teal' onclick='addFromDevices()'>
    📋 Aus Geräteliste laden
  </button>
</div>
</div>

<div style='margin-top:1em'>
  <button class='btn btn-green' style='font-size:1em;padding:.5em 2em'
          onclick='doBatchPush()' id='btn-push'>
    🚀 Push starten
  </button>
</div>

<div id='push-results' style='margin-top:1em'></div>
</div>
</div>

<script>
'use strict';
// ── State ──────────────────────────────────────────────────────────────────
let pullId   = null;
let pullData = null;
let wlanData = [];   // live WLAN-State aus Editor
let tgtIdx   = 0;

// ── Encrypt-Optionen ───────────────────────────────────────────────────────
const ENC = [
  ['none',       '🔓 Kein Passwort (offen)'],
  ['psk',        '🔒 WPA (PSK)'],
  ['psk2',       '🔒 WPA2-PSK'],
  ['psk-mixed',  '🔒 WPA2+WPA Mixed'],
  ['sae',        '🔒 WPA3-SAE only'],
  ['sae-mixed',  '🔒 WPA2+WPA3 Mixed ★'],
];
const MFP = [
  ['0','Deaktiviert (ieee80211w=0)'],
  ['1','Optional   (ieee80211w=1)'],
  ['2','Erforderlich (ieee80211w=2, WPA3)'],
];

function esc(s){ return (s||'').replace(/&/g,'&amp;').replace(/"/g,'&quot;')
                               .replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ── Pull-Methode Karten ────────────────────────────────────────────────────
function selectMode(m){
  document.querySelector('input[name=pull-mode][value="'+m+'"]').checked=true;
  document.getElementById('rc-export').classList.toggle('selected',m==='export');
  document.getElementById('rc-show').classList.toggle('selected',m==='show');
}
function selectPushMethod(m){
  document.querySelector('input[name=push-method][value="'+m+'"]').checked=true;
  document.getElementById('pm-direct').classList.toggle('selected',m==='direct');
  document.getElementById('pm-script').classList.toggle('selected',m==='script');
}

// ── SCHRITT 1: Pull ────────────────────────────────────────────────────────
async function doPull(){
  const ip   = document.getElementById('pull-ip').value.trim();
  const user = document.getElementById('pull-user').value.trim()||'root';
  const pass = document.getElementById('pull-pass').value;
  const mode = document.querySelector('input[name=pull-mode]:checked').value;
  if(!ip){ alert('Bitte Router-IP eingeben'); return; }

  document.getElementById('btn-pull').disabled = true;
  document.getElementById('pull-spinner').style.display='inline';
  document.getElementById('pull-log-wrap').style.display='block';
  document.getElementById('pull-log').textContent='Verbinde mit '+ip+' …';
  document.getElementById('editor-section').style.display='none';
  document.getElementById('push-section').style.display='none';

  const r = await fetch('/api/config-pull',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ip, user, password:pass, mode})
  });
  const d = await r.json();
  pullId = d.pull_id;

  // Pollen bis fertig
  let done = false;
  while(!done){
    await new Promise(r=>setTimeout(r,1100));
    try{
      const sr = await fetch('/api/config-pull/'+pullId);
      const sd = await sr.json();
      document.getElementById('pull-log').textContent = sd.log||'Warte …';
      if(sd.done){
        done=true; pullData=sd;
        document.getElementById('btn-pull').disabled=false;
        document.getElementById('pull-spinner').style.display='none';
        if(sd.success){
          wlanData = JSON.parse(JSON.stringify(sd.wlans||[]));
          buildEditor(wlanData, sd.networks||{}, sd);
          document.getElementById('editor-section').style.display='block';
          document.getElementById('push-section').style.display='block';
          document.getElementById('btn-raw-w').style.display='inline';
          document.getElementById('btn-raw-n').style.display='inline';
          // Quell-IP als ersten Target vorschlagen
          if((sd.ip||'')){ addTarget(sd.ip); }
          document.getElementById('editor-section').scrollIntoView({behavior:'smooth'});
        }
      }
    }catch(e){done=true; document.getElementById('btn-pull').disabled=false;}
  }
}

// ── SCHRITT 2: WLAN-Editor ─────────────────────────────────────────────────
function buildEditor(wlans, networks, sd){
  const tabs   = document.getElementById('wlan-tabs');
  const panels = document.getElementById('wlan-panels');
  tabs.innerHTML=''; panels.innerHTML='';

  const hn = sd.hostname_router || sd.ip || '';
  document.getElementById('pull-summary').innerHTML =
    `✅ <b>${esc(hn)}</b> – ${wlans.length} WLAN(s), `+
    `${(sd.radios||[]).length} Radio(s), `+
    `${Object.keys(networks).length} UCI-Interface(s) gefunden`;

  wlans.forEach((w,i)=>{
    // Band aus Device ermitteln
    const band = w.device.includes('radio1')?'5G':w.device.includes('radio0')?'2.4G':w.device;
    const active = w.disabled!=='1';

    // Tab
    const tab = document.createElement('button');
    tab.className = 'tab-btn'+(i===0?' active-tab':'');
    tab.style.background = active?'#238636':'#484f58';
    tab.id = 'tab-btn-'+i;
    tab.innerHTML = `${active?'🟢':'🔴'} ${esc(w.ssid)||w.uci_name} <small>[${band}]</small>`;
    tab.onclick = ()=>showTab(i);
    tabs.appendChild(tab);

    // Panel
    const encOpts = ENC.map(([v,l])=>
      `<option value="${v}" ${w.encryption===v?'selected':''}>${esc(l)}</option>`
    ).join('');
    const mfpOpts = MFP.map(([v,l])=>
      `<option value="${v}" ${(w.ieee80211w||'0')===v?'selected':''}>${esc(l)}</option>`
    ).join('');
    const netOpts = Object.entries(networks).map(([n,info])=>
      `<option value="${esc(n)}" ${w.network===n?'selected':''}>`+
      `${esc(n)}${info.ipaddr?' ('+info.ipaddr+')':''}</option>`
    ).join('');

    const p = document.createElement('div');
    p.className = 'wlan-panel'+(i===0?' active':'');
    p.id = 'wp-'+i;
    p.innerHTML = `
<div class='card' style='border-left:3px solid ${active?"#3fb950":"#484f58"};margin-top:.4em'>
  <div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:.6em;flex-wrap:wrap;gap:.3em'>
    <b>📡 ${esc(w.uci_name)}</b>
    <span>
      <span class='status-badge ${active?"sb-ok":"sb-run"}'>${active?'🟢 Aktiv':'🔴 Deaktiviert'}</span>
      <span class='enc-badge'>${band}</span>
      <span class='enc-badge'>Device: ${esc(w.device)}</span>
    </span>
  </div>
  <table style='width:100%'>
    <tr><td style='width:170px;padding:.3em 0'>SSID</td>
        <td><input type='text' id='w${i}-ssid' value="${esc(w.ssid)}"
            oninput='wlanData[${i}].ssid=this.value;refreshTab(${i})'></td></tr>
    <tr><td style='padding:.3em 0'>Passwort / Key</td>
        <td><input type='text' id='w${i}-key' value="${esc(w.key)}"
            placeholder='${w.encryption==="none"?"(kein Passwort nötig)":"WLAN-Passwort"}'
            oninput='wlanData[${i}].key=this.value'></td></tr>
    <tr><td style='padding:.3em 0'>Verschlüsselung</td>
        <td><select id='w${i}-enc'
            onchange='wlanData[${i}].encryption=this.value;updateKeyHint(${i})'>${encOpts}</select></td></tr>
    <tr><td style='padding:.3em 0'>Netz / VLAN</td>
        <td>
          <select id='w${i}-net' onchange='onNetChange(${i},this)'>
            ${netOpts}
            <option value='__custom__'>✏️ Manuell …</option>
          </select>
          <input type='text' id='w${i}-net-c' placeholder='UCI-Interface-Name'
            style='display:none;margin-top:.3em;width:100%'
            oninput='wlanData[${i}].network=this.value'>
        </td></tr>
    <tr><td style='padding:.3em 0'>Management-Schutz</td>
        <td><select id='w${i}-mfp'
            onchange='wlanData[${i}].ieee80211w=this.value'>${mfpOpts}</select></td></tr>
    <tr><td style='padding:.3em 0'>802.11r Fast-Roaming</td>
        <td><label style='cursor:pointer;display:flex;align-items:center;gap:.4em'>
          <input type='checkbox' id='w${i}-r' style='width:auto'
            ${w.ieee80211r==='1'?'checked':''}
            onchange='wlanData[${i}].ieee80211r=this.checked?"1":"0";toggleRoam(${i})'>
          aktiviert
        </label>
        <div id='w${i}-roam' style='${w.ieee80211r==="1"?"":"display:none"};margin-top:.3em;
             background:#0d1117;padding:.4em .6em;border-radius:5px;font-size:.85em'>
          Mobility-Domain:
          <input type='text' id='w${i}-md' value="${esc(w.mobility_domain)}"
            style='width:62px;display:inline;margin:0 .3em'
            oninput='wlanData[${i}].mobility_domain=this.value'>
          NAS-ID:
          <input type='text' id='w${i}-nasid' value="${esc(w.nasid)}"
            style='width:90px;display:inline;margin:0 .3em'
            oninput='wlanData[${i}].nasid=this.value'>
          FT over DS:
          <input type='checkbox' id='w${i}-ft' style='width:auto;margin:0 .3em'
            ${w.ft_over_ds==='1'?'checked':''}
            onchange='wlanData[${i}].ft_over_ds=this.checked?"1":"0"'>
        </div></td></tr>
    <tr><td style='padding:.3em 0'>802.11k/v (RRM/BTM)</td>
        <td><label style='cursor:pointer;display:inline-flex;align-items:center;gap:.3em;margin-right:.8em'>
          <input type='checkbox' id='w${i}-k' style='width:auto'
            ${w.ieee80211k==='1'?'checked':''}
            onchange='wlanData[${i}].ieee80211k=this.checked?"1":"0"'>
          k – Radio Resource Mgmt
        </label>
        <label style='cursor:pointer;display:inline-flex;align-items:center;gap:.3em'>
          <input type='checkbox' id='w${i}-v' style='width:auto'
            ${w.ieee80211v==='1'?'checked':''}
            onchange='wlanData[${i}].ieee80211v=this.checked?"1":"0"'>
          v – BSS Transition (BTM)
        </label></td></tr>
    <tr><td style='padding:.3em 0'>BSS Transition</td>
        <td><label style='cursor:pointer;display:inline-flex;align-items:center;gap:.3em'>
          <input type='checkbox' id='w${i}-bss' style='width:auto'
            ${w.bss_transition==='1'?'checked':''}
            onchange='wlanData[${i}].bss_transition=this.checked?"1":"0"'>
          bss_transition aktiviert
        </label></td></tr>
    <tr><td style='padding:.3em 0'>WDS (Bridge)</td>
        <td><label style='cursor:pointer;display:inline-flex;align-items:center;gap:.3em'>
          <input type='checkbox' id='w${i}-wds' style='width:auto'
            ${w.wds==='1'?'checked':''}
            onchange='wlanData[${i}].wds=this.checked?"1":"0"'>
          WDS aktiviert
        </label></td></tr>
    <tr><td style='padding:.3em 0'>WLAN-Status</td>
        <td><label style='cursor:pointer;display:inline-flex;align-items:center;gap:.3em'>
          <input type='checkbox' id='w${i}-ena' style='width:auto'
            ${w.disabled!=='1'?'checked':''}
            onchange='wlanData[${i}].disabled=this.checked?"0":"1";refreshTab(${i})'>
          <b>Aktiv</b> (disabled=0)
        </label></td></tr>
  </table>
</div>`;
    panels.appendChild(p);
  });
}

function showTab(i){
  document.querySelectorAll('.wlan-panel').forEach((p,j)=>{
    p.classList.toggle('active',j===i);
  });
  document.querySelectorAll('.tab-btn').forEach((b,j)=>{
    b.classList.toggle('active-tab',j===i);
  });
}
function refreshTab(i){
  const w=wlanData[i]; const active=w.disabled!=='1';
  const band=w.device.includes('radio1')?'5G':w.device.includes('radio0')?'2.4G':w.device;
  const b=document.getElementById('tab-btn-'+i);
  if(b){
    b.style.background=active?'#238636':'#484f58';
    b.innerHTML=`${active?'🟢':'🔴'} ${esc(w.ssid)||w.uci_name} <small>[${band}]</small>`;
  }
}
function toggleRoam(i){
  document.getElementById('w'+i+'-roam').style.display=
    wlanData[i].ieee80211r==='1'?'':'none';
}
function onNetChange(i,sel){
  const c=document.getElementById('w'+i+'-net-c');
  if(sel.value==='__custom__'){
    c.style.display='block';
  }else{
    c.style.display='none';
    wlanData[i].network=sel.value;
  }
}
function updateKeyHint(i){
  const kEl=document.getElementById('w'+i+'-key');
  const enc=wlanData[i].encryption;
  if(kEl) kEl.placeholder=enc==='none'?'(kein Passwort nötig)':'WLAN-Passwort';
}

// ── UCI-Preview ────────────────────────────────────────────────────────────
function buildUciCmds(){
  const lines=[];
  wlanData.forEach(w=>{
    if(!w.uci_name) return;
    const n=w.uci_name;
    const S=(k,v)=>{if(v!==''&&v!=null) lines.push(`set wireless.${n}.${k}='${v}'`);};
    S('ssid',w.ssid||'');
    const enc=w.encryption||'none';
    S('encryption',enc);
    if(enc!=='none'&&enc!=='open'&&enc!=='') S('key',w.key||'');
    S('network',w.network||'');
    S('disabled',w.disabled||'0');
    S('ieee80211r',w.ieee80211r||'0');
    S('ieee80211k',w.ieee80211k||'0');
    S('ieee80211v',w.ieee80211v||'0');
    S('ieee80211w',w.ieee80211w||'0');
    S('ft_over_ds',w.ft_over_ds||'0');
    S('bss_transition',w.bss_transition||'0');
    if(w.mobility_domain) S('mobility_domain',w.mobility_domain);
    if(w.nasid)           S('nasid',w.nasid);
    if(w.wds==='1')       S('wds','1');
  });
  return lines.join('\n');
}
function refreshPreview(){
  const cmds=buildUciCmds();
  const pre=document.getElementById('uci-preview');
  pre.textContent=cmds||'# Keine UCI-Befehle generiert (WLANs leer?)';
  pre.style.display='block';
}
async function loadRaw(sub){
  if(!pullId) return;
  const r=await fetch(`/api/config-pull/${pullId}/raw/${sub}`);
  const txt=await r.text();
  const pre=document.getElementById('uci-preview');
  pre.textContent=`# ─── Raw UCI ${sub} ─────────────────────────\n`+txt;
  pre.style.display='block';
}

// ── Projekt / Template speichern ───────────────────────────────────────────
async function saveProject(){
  const name=document.getElementById('save-proj').value.trim();
  if(!name){alert('Projektname eingeben');return;}
  const r=await fetch(`/api/config-pull/${pullId}/save-project`,{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({project_name:name,wlans:wlanData,
      network:pullData?.networks||{}})
  });
  const d=await r.json();
  document.getElementById('res-proj').innerHTML=d.ok
    ?`✅ <a href='/ui/projects/${encodeURIComponent(name)}'>Projekt <b>${esc(name)}</b></a> gespeichert`
    :`❌ Fehler: ${JSON.stringify(d)}`;
}
async function saveTemplate(){
  const name=document.getElementById('save-tpl').value.trim();
  if(!name){alert('Template-Name eingeben');return;}
  const r=await fetch(`/api/config-pull/${pullId}/save-template`,{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({template_name:name,wlans:wlanData})
  });
  const d=await r.json();
  document.getElementById('res-tpl').innerHTML=d.ok
    ?`✅ <a href='/ui/templates/${encodeURIComponent(name)}'>Template <b>${esc(name)}</b></a> gespeichert (${d.lines} Zeilen)`
    :`❌ Fehler: ${JSON.stringify(d)}`;
}

// ── Ziel-Router ────────────────────────────────────────────────────────────
function addTarget(defIp=''){
  const i=tgtIdx++;
  const div=document.createElement('div');
  div.id='tgt-'+i; div.className='tgt-row';
  div.innerHTML=`
    <span style='color:#8b949e;font-size:.8em;min-width:20px'>#${i+1}</span>
    <input type='text'     id='ti-${i}-ip'   value="${esc(defIp)}"   placeholder='IP'         style='width:145px'>
    <input type='text'     id='ti-${i}-user' value='root'            placeholder='User'       style='width:70px'>
    <input type='password' id='ti-${i}-pass'                         placeholder='Passwort'   style='width:130px'>
    <input type='text'     id='ti-${i}-mac'                          placeholder='MAC (opt.)' style='width:145px'>
    <button class='btn btn-red' style='padding:.2em .6em;font-size:.85em'
            onclick='document.getElementById("tgt-${i}").remove()'>✕</button>`;
  document.getElementById('targets-list').appendChild(div);
}

async function addFromDevices(){
  try{
    const r=await fetch('/api/devices');
    if(!r.ok) throw new Error('HTTP '+r.status);
    const ds=await r.json();
    if(!ds.length){alert('Keine Geräte in der Datenbank gefunden.');return;}
    let added=0;
    ds.forEach(d=>{
      const ip=d.last_ip||'';
      if(ip){addTarget(ip);added++;}
    });
    if(!added) alert('Keine IP-Adressen für Geräte gefunden.\nBitte IPs manuell eingeben.');
  }catch(e){
    alert('Geräteliste konnte nicht geladen werden: '+e.message+
          '\nBitte Router manuell hinzufügen.');
  }
}

// ── Batch-Push ─────────────────────────────────────────────────────────────
async function doBatchPush(){
  const uci_cmds = buildUciCmds();
  if(!uci_cmds.trim()){
    alert('Keine UCI-Befehle – bitte WLANs bearbeiten oder Pull durchführen');
    return;
  }
  const method    = document.querySelector('input[name=push-method]:checked').value;
  const do_commit = document.getElementById('opt-commit').checked;
  const do_reload = document.getElementById('opt-reload').checked;
  const do_reboot = document.getElementById('opt-reboot').checked;

  // Targets sammeln
  const targets=[];
  document.querySelectorAll('[id^="tgt-"]').forEach(row=>{
    const base=row.id; // tgt-N
    const n=base.replace('tgt-','');
    const ipEl=document.getElementById('ti-'+n+'-ip');
    if(!ipEl) return;
    const ip=ipEl.value.trim();
    if(!ip) return;
    targets.push({
      ip, method,
      user:     document.getElementById('ti-'+n+'-user')?.value||'root',
      password: document.getElementById('ti-'+n+'-pass')?.value||'',
      mac:      document.getElementById('ti-'+n+'-mac')?.value||'unknown',
    });
  });
  if(!targets.length){alert('Kein Ziel-Router angegeben');return;}

  const resultsEl=document.getElementById('push-results');
  resultsEl.innerHTML=`<div class='card card-blue'>🚀 Push läuft auf ${targets.length} Router parallel …</div>`;

  // API-Aufruf
  let jobMap={};  // ip → job_id
  if(method==='direct'){
    const r=await fetch('/api/batch-push',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({targets,uci_cmds,do_commit,do_reload,do_reboot})
    });
    const d=await r.json();
    jobMap=d.jobs||{};
  } else {
    // Script-Methode: MAC-basiert über /api/deploy/{mac}/ssh-push
    for(const tgt of targets){
      const mac=(tgt.mac||'').replace(/[^a-f0-9]/gi,'').toLowerCase();
      if(!mac||mac==='unknown'||mac.length<12){
        // Fallback: direct-push wenn keine MAC bekannt
        const r=await fetch('/api/direct-push',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({...tgt,uci_cmds,do_commit,do_reload,do_reboot})
        });
        const d=await r.json();
        jobMap[tgt.ip]=d.job_id;
      } else {
        const r=await fetch(`/api/deploy/${mac}/ssh-push`,{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({ip:tgt.ip,user:tgt.user||'root',password:tgt.password||'',precheck:false})
        });
        const d=await r.json();
        jobMap[tgt.ip]=d.job_id;
      }
    }
  }

  // Job-Karten anlegen
  Object.entries(jobMap).forEach(([ip,jid])=>{
    const card=document.createElement('div');
    card.className='card push-log-wrap';
    card.style='margin:.4em 0';
    card.innerHTML=`
      <div style='display:flex;align-items:center;gap:.5em;flex-wrap:wrap'>
        <b>🖥️ ${esc(ip)}</b>
        <span class='status-badge sb-run' id='st-${jid}'>⏳ läuft</span>
      </div>
      <pre id='pl-${jid}'></pre>`;
    resultsEl.appendChild(card);
  });

  // Parallel pollen
  await Promise.all(Object.entries(jobMap).map(([ip,jid])=>pollJob(jid)));
}

async function pollJob(jid){
  let done=false;
  while(!done){
    await new Promise(r=>setTimeout(r,1200));
    try{
      const r=await fetch('/api/deploy/job/'+jid);
      const d=await r.json();
      const logEl=document.getElementById('pl-'+jid);
      const stEl =document.getElementById('st-'+jid);
      if(logEl) logEl.textContent=d.log||'';
      if(d.done){
        done=true;
        if(stEl) stEl.outerHTML=d.success
          ?`<span class='status-badge sb-ok'>✅ Erfolg</span>`
          :`<span class='status-badge sb-err'>❌ Fehler</span>`;
      }
    }catch(e){done=true;}
  }
}
</script>
"""
    return _page(content, "Config Pull", "/ui/config-pull")

# ─────────────────────────────────────────────────────────────────────────────
# UI: Geräte-Discovery
# ─────────────────────────────────────────────────────────────────────────────
@app.get("/ui/discover", response_class=HTMLResponse)
def ui_discover(db: sqlite3.Connection=Depends(get_db), _=Depends(check_admin)):
    settings = {r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings").fetchall()}
    default_subnet = settings.get("MGMT_NET", "192.168.10")
    content = f"""
<h2>🔍 Geräte-Discovery</h2>
<div class='card card-blue'>
  ℹ️ Scannt das Netzwerk auf erreichbare Hosts und erkennt OpenWrt-Router anhand von SSH (Port 22) und LuCI (Port 80).
  Der Scan läuft parallel – dauert ca. Timeout-Sekunden.
</div>
<div class='card'>
  <div style='display:flex;gap:1em;align-items:flex-end;flex-wrap:wrap'>
    <div>
      <label style='display:block;margin-bottom:.3em'>Subnetz (erste 3 Oktette)</label>
      <input type='text' id='subnet-input' value='{default_subnet}' placeholder='192.168.10' style='width:180px'>
    </div>
    <div>
      <label style='display:block;margin-bottom:.3em'>Timeout (Sekunden)</label>
      <input type='number' id='timeout-input' value='1.5' min='0.5' max='5' step='0.5' style='width:80px'>
    </div>
    <button class='btn btn-teal' onclick='startScan()' id='scan-btn'>🔍 Scan starten</button>
  </div>
</div>
<div id='scan-progress' style='display:none' class='card card-orange'>⏳ Scan läuft… bitte warten.</div>
<div id='scan-results'></div>

<script>
async function startScan() {{
  const subnet = document.getElementById('subnet-input').value.trim();
  const timeout = parseFloat(document.getElementById('timeout-input').value)||1.5;
  if(!subnet){{alert('Bitte Subnetz eingeben');return;}}
  document.getElementById('scan-btn').disabled=true;
  document.getElementById('scan-progress').style.display='block';
  document.getElementById('scan-results').innerHTML='';
  try {{
    const r = await fetch('/api/discover', {{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{subnet, timeout}})
    }});
    const d = await r.json();
    document.getElementById('scan-progress').style.display='none';
    if(!d.results||d.results.length===0){{
      document.getElementById('scan-results').innerHTML=`<div class='card card-orange'>⚠️ Keine Geräte in ${{subnet}}.0/24 gefunden.</div>`;
      return;
    }}
    let rows='';
    d.results.forEach(h=>{{
      rows+=`<tr>
        <td><b>${{h.ip}}</b></td>
        <td>${{h.port22?'✅':'—'}}</td>
        <td>${{h.port80?'✅':'—'}}</td>
        <td>${{h.luci?'<span class="ok">✅ OpenWrt LuCI</span>':'—'}}</td>
        <td class='muted'>${{h.latency_ms}}ms</td>
        <td><button class='btn btn-green' style='font-size:.8em;padding:.2em .5em' onclick='openSsh("${{h.ip}}")'>📡 SSH</button></td>
      </tr>`;
    }});
    document.getElementById('scan-results').innerHTML=`
<div class='card card-green' style='margin-bottom:.5em'>✅ ${{d.found}} Gerät(e) gefunden in ${{subnet}}.0/24</div>
<div class='card'>
  <table>
    <thead><tr><th>IP</th><th>SSH (22)</th><th>HTTP (80)</th><th>LuCI</th><th>Latenz</th><th>Aktion</th></tr></thead>
    <tbody>${{rows}}</tbody>
  </table>
</div>`;
  }}catch(e){{
    document.getElementById('scan-progress').style.display='none';
    document.getElementById('scan-results').innerHTML=`<div class='card card-red'>❌ Fehler: ${{e.message}}</div>`;
  }}
  document.getElementById('scan-btn').disabled=false;
}}
function openSsh(ip){{
  window.location='/ui/setup?ip='+encodeURIComponent(ip);
}}
</script>"""
    return _page(content, "Discovery", "/ui/discover")


# ─────────────────────────────────────────────────────────────────────────────
# API: Projekte als JSON + Config-Push Preview
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/projects")
def api_projects_json(db: sqlite3.Connection = Depends(get_db), _=Depends(check_admin)):
    """Alle Projekte als JSON-Liste."""
    rows = db.execute("SELECT name, description, settings FROM projects ORDER BY name").fetchall()
    return [{"name": r["name"], "description": r["description"],
             "settings": json.loads(r["settings"] or "{}")} for r in rows]

@app.post("/api/config-push/preview")
async def api_config_push_preview(request: Request, db: sqlite3.Connection = Depends(get_db), _=Depends(check_admin)):
    """Rendert UCI-Config für ein Projekt + optionales Gerät.
    Body: {project_name, mac?, hostname?}
    Gibt {ok, uci_script, vars_used} zurück."""
    body = await request.json()
    project_name = body.get("project_name", "").strip()
    mac_in  = body.get("mac", "AA:BB:CC:DD:EE:FF").strip() or "AA:BB:CC:DD:EE:FF"
    hostname_in = body.get("hostname", "").strip()
    if not project_name:
        return {"ok": False, "detail": "project_name fehlt"}
    row = db.execute("SELECT settings FROM projects WHERE name=?", (project_name,)).fetchone()
    if not row:
        return {"ok": False, "detail": f"Projekt '{project_name}' nicht gefunden"}
    settings = json.loads(row["settings"] or "{}")
    hostname = hostname_in or f"ap-{mac_in.replace(':','').lower()[-4:]}"
    # Rolle & Template laden
    role_name = settings.get("role", "node")
    role_row  = db.execute("SELECT overrides FROM roles WHERE name=?", (role_name,)).fetchone()
    role_override = (role_row["overrides"] or "") if role_row else ""
    tpl_name = settings.get("template", "master")
    tpl_row  = db.execute("SELECT content FROM templates WHERE name=?", (tpl_name,)).fetchone()
    tpl_content = tpl_row["content"] if tpl_row else ""
    if not tpl_content:
        return {"ok": False, "detail": f"Template '{tpl_name}' nicht gefunden"}
    vars_ = build_vars(settings, mac_in, hostname)
    script = render_template(tpl_content, vars_, role_override, None)
    return {"ok": True, "uci_script": script, "template": tpl_name,
            "hostname": hostname, "vars_used": list(vars_.keys())}


# ─────────────────────────────────────────────────────────────────────────────
# UI: /ui/config-push  –  Projekt → Vorschau → Push
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/ui/config-push", response_class=HTMLResponse)
def ui_config_push(db: sqlite3.Connection = Depends(get_db), _=Depends(check_admin)):
    projects = db.execute("SELECT name, description FROM projects ORDER BY name").fetchall()
    proj_opts = "".join(
        f"<option value='{p['name']}'>{p['name']}" + (f" – {p['description']}" if p['description'] else "") + "</option>"
        for p in projects
    )
    devices = db.execute("SELECT base_mac, hostname, last_ip FROM devices ORDER BY hostname").fetchall()
    dev_opts = "<option value=''>— kein Gerät (generisch) —</option>"
    for d in devices:
        ip_label = f" ({d['last_ip']})" if d['last_ip'] else ""
        dev_opts += f"<option value='{d['base_mac']}' data-ip='{d['last_ip'] or ''}' data-hostname='{d['hostname']}'>{d['hostname']}{ip_label}</option>"

    content = r"""
<h2>📤 Config Push → Vorschau → Push</h2>

<div class='card card-teal'>
  <b>Workflow:</b>
  Wähle ein <b>Projekt</b> → rendere die UCI-Config im Browser (editierbar) →
  pushe direkt per SSH auf einen <b>Router</b> &mdash; ohne Enrollment, ohne Reboot-Zwang.<br>
  <span class='muted'>Gegenstück zu Config-Pull: statt vom Router zu lesen, wird die DB-Config auf den Router geschrieben.</span>
</div>

<!-- ═══ SCHRITT 1: QUELLE ═══════════════════════════════════════════════════ -->
<div class='card' id='s1'>
<h3>① Projekt &amp; Gerät wählen</h3>
<div class='grid2' style='gap:.7em;align-items:start'>
<table style='width:100%'>
  <tr><td style='width:130px'>📁 Projekt</td>
      <td><select id='cp-project' style='width:100%'>""" + proj_opts + r"""</select></td></tr>
  <tr><td>🖥️ Gerät (opt.)</td>
      <td><select id='cp-device' onchange='onDeviceChange()' style='width:100%'>""" + dev_opts + r"""</select>
          <span class='muted' style='font-size:.8em'>Gerät wählen = Hostname + IP automatisch befüllen</span></td></tr>
  <tr><td>🏷️ Hostname</td>
      <td><input type='text' id='cp-hostname' placeholder='z.B. ap-0042 (leer = auto)'></td></tr>
  <tr><td>📟 MAC</td>
      <td><input type='text' id='cp-mac' placeholder='AA:BB:CC:DD:EE:FF (für Suffix-Berechnung)'></td></tr>
</table>
<div>
  <button class='btn btn-teal' onclick='loadPreview()'>🔄 Config rendern</button>
  <span class='muted' style='font-size:.82em;display:block;margin-top:.5em'>Rendert das Template mit den Projekt-Variablen</span>
</div>
</div>
<div id='preview-wrap' style='display:none;margin-top:.8em'>
  <div id='preview-meta' class='muted' style='font-size:.82em;margin-bottom:.4em'></div>
  <pre id='preview-log' style='min-height:30px;max-height:60px;overflow-y:auto;font-size:.78em;color:#d29922;display:none'></pre>
</div>
</div>

<!-- ═══ SCHRITT 2: VORSCHAU ══════════════════════════════════════════════════ -->
<div class='card' id='s2' style='display:none'>
<h3>② UCI-Config Vorschau / Bearbeiten</h3>
<div style='margin-bottom:.5em'>
  <button class='btn' style='font-size:.82em' onclick='loadPreview()'>🔄 Neu rendern</button>
  <span class='muted' style='font-size:.82em;margin-left:.5em'>Direkt editierbar – Änderungen werden gepusht</span>
</div>
<textarea id='cp-script' rows='20' style='font-family:monospace;font-size:.78em;background:#0d1117;color:#c9d1d9;border:1px solid #30363d;padding:.5em;border-radius:4px;width:100%'></textarea>
</div>

<!-- ═══ SCHRITT 3: PUSH ══════════════════════════════════════════════════════ -->
<div class='card' id='s3' style='display:none'>
<h3>③ Push auf Router</h3>
<div class='grid2' style='gap:.7em;align-items:start'>
<table style='width:100%'>
  <tr><td style='width:130px'>🌐 Router-IP</td>
      <td><input type='text' id='cp-ip' value='192.168.10.1' placeholder='192.168.x.x'></td></tr>
  <tr><td>👤 Benutzer</td>
      <td><input type='text' id='cp-user' value='root'></td></tr>
  <tr><td>🔑 Passwort</td>
      <td><input type='password' id='cp-pass' placeholder='Leer = gespeicherter SSH-Key'>
          <span class='muted' style='font-size:.78em'>🗝️ Leer = gespeicherter SSH-Key (<a href='/ui/settings'>Einstellungen</a>)</span></td></tr>
</table>
<div>
  <div style='margin-bottom:.5em'>
    <label><input type='checkbox' id='cp-commit' checked style='width:auto;margin-right:.4em'>uci commit</label><br>
    <label><input type='checkbox' id='cp-reload' checked style='width:auto;margin-right:.4em'>wifi reload</label><br>
    <label><input type='checkbox' id='cp-reboot' style='width:auto;margin-right:.4em'>reboot</label>
  </div>
  <button class='btn btn-green' onclick='doPush()'>📤 Jetzt pushen</button>
</div>
</div>
<div id='push-wrap' style='display:none;margin-top:.7em'>
  <pre id='push-log' style='min-height:80px;max-height:300px;overflow-y:auto;font-size:.8em'></pre>
</div>
</div>

<script>
function onDeviceChange() {
  const sel = document.getElementById('cp-device');
  const opt = sel.options[sel.selectedIndex];
  if (opt && opt.dataset.hostname) {
    document.getElementById('cp-hostname').value = opt.dataset.hostname;
  }
  if (opt && opt.dataset.ip) {
    document.getElementById('cp-ip').value = opt.dataset.ip;
  }
  if (opt && opt.value) {
    document.getElementById('cp-mac').value = opt.value;
  }
}

async function loadPreview() {
  const project_name = document.getElementById('cp-project').value;
  const mac          = document.getElementById('cp-mac').value.trim();
  const hostname     = document.getElementById('cp-hostname').value.trim();
  const log = document.getElementById('preview-log');
  const wrap = document.getElementById('preview-wrap');
  wrap.style.display = 'block';
  log.style.display = 'block';
  log.textContent = 'Rendere…';
  try {
    const r = await fetch('/api/config-push/preview', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({project_name, mac: mac||undefined, hostname: hostname||undefined})
    });
    const d = await r.json();
    if (!d.ok) { log.textContent = '❌ ' + (d.detail||'Fehler'); return; }
    document.getElementById('cp-script').value = d.uci_script || '';
    document.getElementById('preview-meta').textContent =
      `Template: ${d.template} | Hostname: ${d.hostname} | ${d.vars_used?.length||0} Variablen`;
    log.style.display = 'none';
    document.getElementById('s2').style.display = 'block';
    document.getElementById('s3').style.display = 'block';
    document.getElementById('s2').scrollIntoView({behavior:'smooth', block:'nearest'});
  } catch(e) { log.textContent = '❌ ' + e.message; }
}

async function doPush() {
  const script = document.getElementById('cp-script').value.trim();
  if (!script) { alert('Kein UCI-Script – erst rendern'); return; }
  const ip       = document.getElementById('cp-ip').value.trim();
  const user     = document.getElementById('cp-user').value.trim();
  const password = document.getElementById('cp-pass').value;
  const do_commit = document.getElementById('cp-commit').checked;
  const do_reload = document.getElementById('cp-reload').checked;
  const do_reboot = document.getElementById('cp-reboot').checked;
  if (!ip) { alert('Router-IP fehlt'); return; }
  const uci_cmds = script.split('\n').map(l=>l.trim()).filter(l=>l && !l.startsWith('#'));
  const wrap = document.getElementById('push-wrap');
  const log  = document.getElementById('push-log');
  wrap.style.display = 'block';
  log.textContent = 'Push startet…';
  try {
    const r = await fetch('/api/direct-push', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ip, user, password, uci_cmds, do_commit, do_reload, do_reboot})
    });
    const d = await r.json();
    const job_id = d.job_id;
    if (!job_id) { log.textContent = '❌ ' + (d.detail||JSON.stringify(d)); return; }
    // Polling
    const start = Date.now();
    while (true) {
      await new Promise(res=>setTimeout(res,700));
      const jr = await fetch('/api/deploy/job/'+job_id);
      const jd = await jr.json();
      log.textContent = jd.log || '…';
      log.scrollTop = log.scrollHeight;
      if (jd.done) {
        const elapsed = ((Date.now()-start)/1000).toFixed(1);
        if (jd.success) {
          log.textContent += '\n\n✅ Push erfolgreich (' + elapsed + 's)';
        } else {
          log.textContent += '\n\n❌ Push fehlgeschlagen (' + elapsed + 's)';
        }
        break;
      }
      if (Date.now() - start > 120000) { log.textContent += '\n\n⏱️ Timeout'; break; }
    }
  } catch(e) { log.textContent = '❌ ' + e.message; }
}
</script>"""
    return _page(content, "Config-Push", "/ui/config-push")
