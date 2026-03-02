#!/bin/sh
# ============================================================
# BATMAN-ADV MESH SETUP für OpenWrt (MediaTek MT7981)
# Getestet: OpenWrt 23.x, kmod-batman-adv 2024.3
#
# ROLLEN:
#   master   - Haupt-Router mit WAN, DHCP-Server, NAT
#   repeater - Mesh-Knoten MIT eigenem AP (SSID sichtbar)
#   client   - Mesh-Knoten OHNE eigenen AP
#
# ABLAUF:
#   1. Script auf Router kopieren: scp setup-mesh.sh root@192.168.1.1:/root/
#   2. chmod +x /root/setup-mesh.sh && /root/setup-mesh.sh
#   3. Rolle wählen + Nummer eingeben
#   4. Router startet neu
#   5. rc.local baut batman automatisch auf
# ============================================================
set -eu

# --- Einstellungen anpassen ---
COUNTRY="DE"
CH5G="36"
HTMODE5G="HE80"

AP_SSID="<--_-->"
AP_KEY="16051979Cs$"

MESH_ID="BATMESH"
# Mesh OHNE Verschlüsselung (SAE funktioniert nicht auf MT7981 zuverlässig)

DHCP_START="100"
DHCP_LIMIT="100"
DHCP_LEASE="12h"

# ============================================================
die() { echo "FEHLER: $*" >&2; exit 1; }

echo "Rolle wählen: master | repeater | client"
read -r ROLE
case "$ROLE" in
  master|repeater|client) ;;
  *) die "Ungültige Rolle: $ROLE" ;;
esac

echo "Nummer (zweistellig, z.B. 01 für AP1 -> 10.10.1.x):"
read -r NN
echo "$NN" | grep -Eq '^[0-9]{2}$' || die "Nummer muss zweistellig sein (01-99)"

NET="10.10.${NN}"
GW="${NET}.1"
HOST="ap-${NN}"

echo ""
echo "=== KONFIGURATION ==="
echo "ROLLE:    $ROLE"
echo "HOSTNAME: $HOST"
echo "NETZ:     ${NET}.0/24"
echo "GATEWAY:  $GW"
echo "====================="
echo ""

# --- Pakete prüfen (ohne opkg update um Hänger zu vermeiden) ---
if ! modprobe batman-adv 2>/dev/null; then
  echo "batman-adv Modul nicht gefunden, versuche Installation..."
  opkg update 2>/dev/null || true
  opkg install kmod-batman-adv 2>/dev/null || true
  modprobe batman-adv 2>/dev/null || true
fi

# batctl installieren falls nicht vorhanden
if ! which batctl >/dev/null 2>&1; then
  opkg install batctl 2>/dev/null || true
fi

# --- Hostname ---
uci set system.@system[0].hostname="$HOST"
uci commit system

# --- bat0 Interface (batman-adv) ---
uci set network.bat0=interface
uci set network.bat0.proto='batadv'
uci set network.bat0.routing_algo='BATMAN_IV'

# --- br-new Bridge Device bereinigen und neu anlegen ---
# Alle alten br-new device sections entfernen
IDX=0
while uci get "network.@device[$IDX]" >/dev/null 2>&1; do
  NAME=$(uci get "network.@device[$IDX].name" 2>/dev/null || echo "")
  if [ "$NAME" = "br-new" ]; then
    uci delete "network.@device[$IDX]"
    # Nach dem Löschen Index nicht erhöhen (nächste rückt nach)
  else
    IDX=$((IDX + 1))
  fi
done

# br-new Bridge neu anlegen mit bat0 als Port
uci add network device >/dev/null
uci set network.@device[-1].name='br-new'
uci set network.@device[-1].type='bridge'
uci set network.@device[-1].bridge_empty='1'
uci add_list network.@device[-1].ports='bat0'

# --- newnet Interface ---
uci set network.newnet=interface
uci set network.newnet.device='br-new'
if [ "$ROLE" = "master" ]; then
  uci set network.newnet.proto='static'
  uci set network.newnet.ipaddr="$GW"
  uci set network.newnet.netmask='255.255.255.0'
else
  uci set network.newnet.proto='dhcp'
  uci -q delete network.newnet.ipaddr 2>/dev/null || true
  uci -q delete network.newnet.netmask 2>/dev/null || true
fi

# --- DHCP nur auf Master ---
if [ "$ROLE" = "master" ]; then
  uci set dhcp.newnet=dhcp
  uci set dhcp.newnet.interface='newnet'
  uci set dhcp.newnet.start="$DHCP_START"
  uci set dhcp.newnet.limit="$DHCP_LIMIT"
  uci set dhcp.newnet.leasetime="$DHCP_LEASE"
  uci set dhcp.newnet.ignore='0'
else
  uci -q delete dhcp.newnet 2>/dev/null || true
fi

uci commit network
uci commit dhcp

# --- Wireless radio1 (5 GHz) ---
uci set wireless.radio1.country="$COUNTRY"
uci set wireless.radio1.channel="$CH5G"
uci set wireless.radio1.htmode="$HTMODE5G"
uci set wireless.radio1.disabled='0'

# --- Alle alten mesh0 WiFi-Interfaces entfernen ---
IDX=0
while uci get "wireless.@wifi-iface[$IDX]" >/dev/null 2>&1; do
  IFNAME=$(uci get "wireless.@wifi-iface[$IDX].ifname" 2>/dev/null || echo "")
  MODE=$(uci get "wireless.@wifi-iface[$IDX].mode" 2>/dev/null || echo "")
  if [ "$IFNAME" = "mesh0" ] || [ "$MODE" = "mesh" ]; then
    uci delete "wireless.@wifi-iface[$IDX]"
  else
    IDX=$((IDX + 1))
  fi
done

# --- 802.11s Mesh Interface (OHNE Verschlüsselung - SAE ist auf MT7981 instabil) ---
uci add wireless wifi-iface >/dev/null
uci set wireless.@wifi-iface[-1].device='radio1'
uci set wireless.@wifi-iface[-1].mode='mesh'
uci set wireless.@wifi-iface[-1].ifname='mesh0'
uci set wireless.@wifi-iface[-1].mesh_id="$MESH_ID"
uci set wireless.@wifi-iface[-1].encryption='none'
uci set wireless.@wifi-iface[-1].network='bat0'
uci set wireless.@wifi-iface[-1].disabled='0'

# --- batman hardif Interface für mesh0 ---
uci set network.mesh0=interface
uci set network.mesh0.proto='batadv_hardif'
uci set network.mesh0.master='bat0'
uci set network.mesh0.device='mesh0'

uci commit wireless
uci commit network

# --- AP SSID (nur master und repeater) ---
# Alle alten APs mit dieser SSID entfernen
IDX=0
while uci get "wireless.@wifi-iface[$IDX]" >/dev/null 2>&1; do
  SSID=$(uci get "wireless.@wifi-iface[$IDX].ssid" 2>/dev/null || echo "")
  if [ "$SSID" = "$AP_SSID" ]; then
    uci delete "wireless.@wifi-iface[$IDX]"
  else
    IDX=$((IDX + 1))
  fi
done

if [ "$ROLE" = "master" ] || [ "$ROLE" = "repeater" ]; then
  uci add wireless wifi-iface >/dev/null
  uci set wireless.@wifi-iface[-1].device='radio1'
  uci set wireless.@wifi-iface[-1].mode='ap'
  uci set wireless.@wifi-iface[-1].network='newnet'
  uci set wireless.@wifi-iface[-1].ssid="$AP_SSID"
  uci set wireless.@wifi-iface[-1].encryption='sae'
  uci set wireless.@wifi-iface[-1].ieee80211w='2'
  uci set wireless.@wifi-iface[-1].key="$AP_KEY"
  uci set wireless.@wifi-iface[-1].disabled='0'
fi

uci commit wireless

# --- Firewall (nur Master): newnet -> wan + Masquerade ---
if [ "$ROLE" = "master" ]; then
  # Zone newnet anlegen falls nicht vorhanden
  if ! uci show firewall | grep -q "name='newnet'"; then
    uci add firewall zone >/dev/null
    uci set firewall.@zone[-1].name='newnet'
    uci set firewall.@zone[-1].input='ACCEPT'
    uci set firewall.@zone[-1].output='ACCEPT'
    uci set firewall.@zone[-1].forward='REJECT'
    uci add_list firewall.@zone[-1].network='newnet'
  fi

  # Forward newnet -> wan
  if ! uci show firewall | grep -q "src='newnet'"; then
    uci add firewall forwarding >/dev/null
    uci set firewall.@forwarding[-1].src='newnet'
    uci set firewall.@forwarding[-1].dest='wan'
  fi

  # Masquerade auf wan aktivieren
  WAN_ZONE=$(uci show firewall | sed -n "s/^\(firewall\.@zone\[[0-9]*\]\)\.name='wan'.*/\1/p" | head -n1)
  if [ -n "$WAN_ZONE" ]; then
    uci set "${WAN_ZONE}.masq=1"
  fi

  uci commit firewall
fi

# ============================================================
# rc.local erstellen - wird bei jedem Boot ausgeführt
# Baut batman hardif und bridge manuell auf (netifd macht das
# nicht zuverlässig für batman-adv)
# ============================================================
if [ "$ROLE" = "master" ]; then
  cat > /etc/rc.local << 'RCEOF'
#!/bin/sh
# Batman-Mesh Startup (Master)
sleep 15
modprobe batman-adv 2>/dev/null || true

# mesh0 Interface finden (heißt nach wifi reload phy1-mesh0)
MESHIF=""
for iface in mesh0 phy1-mesh0; do
  if ip link show "$iface" >/dev/null 2>&1; then
    MESHIF="$iface"
    break
  fi
done

if [ -n "$MESHIF" ]; then
  ip link set "$MESHIF" mtu 1560 2>/dev/null || true
  batctl meshif bat0 if add "$MESHIF" 2>/dev/null || \
  batctl if add "$MESHIF" 2>/dev/null || true
fi

# bat0 in br-new Bridge einhängen
sleep 2
ip link set bat0 master br-new 2>/dev/null || \
brctl addif br-new bat0 2>/dev/null || true

exit 0
RCEOF
else
  cat > /etc/rc.local << 'RCEOF'
#!/bin/sh
# Batman-Mesh Startup (Repeater/Client)
sleep 15
modprobe batman-adv 2>/dev/null || true

# mesh0 Interface finden (heißt nach wifi reload phy1-mesh0)
MESHIF=""
for iface in mesh0 phy1-mesh0; do
  if ip link show "$iface" >/dev/null 2>&1; then
    MESHIF="$iface"
    break
  fi
done

if [ -n "$MESHIF" ]; then
  ip link set "$MESHIF" mtu 1560 2>/dev/null || true
  batctl if add "$MESHIF" 2>/dev/null || true
  # Mesh joinen falls nicht automatisch
  iw dev "$MESHIF" mesh join BATMESH 2>/dev/null || true
fi

# bat0 in br-new Bridge einhängen
sleep 2
brctl addif br-new bat0 2>/dev/null || \
ip link set bat0 master br-new 2>/dev/null || true

exit 0
RCEOF
fi

chmod +x /etc/rc.local

# ============================================================
echo ""
echo "=== FERTIG ==="
echo "ROLLE:    $ROLE"
echo "HOSTNAME: $HOST"
echo "MESH ID:  $MESH_ID (unverschlüsselt - AP bleibt WPA3)"
if [ "$ROLE" = "master" ] || [ "$ROLE" = "repeater" ]; then
  echo "AP SSID:  $AP_SSID (WPA3/SAE)"
fi
if [ "$ROLE" = "master" ]; then
  echo "GATEWAY:  $GW"
  echo "DHCP:     ${NET}.100 - ${NET}.199"
fi
echo ""
echo "Router wird in 5 Sekunden neu gestartet..."
sleep 5
reboot
