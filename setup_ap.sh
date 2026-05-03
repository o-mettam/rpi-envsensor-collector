#!/bin/bash
# =============================================================================
# Wi-Fi Access Point Setup for Raspberry Pi
# =============================================================================
#
# This script configures the Raspberry Pi to broadcast its own Wi-Fi network
# so users can connect and access the sensor dashboard.
#
# Network details (configurable below):
#   SSID:      EnvSensor
#   Password:  envsensor123
#   IP:        192.168.4.1
#   DHCP:      192.168.4.10 - 192.168.4.50
#
# The dashboard will be accessible at: http://192.168.4.1
#
# Usage: sudo bash setup_ap.sh
#
# NOTE: This will configure wlan0 as an access point. If you need wlan0 for
#       internet access, consider using a second Wi-Fi adapter or Ethernet.
# =============================================================================

set -e

# --- Configuration ---
AP_SSID="EnvSensor"
AP_PASSWORD="envsensor123"
AP_CHANNEL=7
AP_IP="192.168.4.1"
AP_NETMASK="255.255.255.0"
DHCP_RANGE_START="192.168.4.10"
DHCP_RANGE_END="192.168.4.50"
DHCP_LEASE="24h"
WIFI_INTERFACE="wlan0"

# --- Checks ---
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)."
    exit 1
fi

echo "========================================"
echo " Wi-Fi Access Point Setup"
echo "========================================"
echo ""
echo " SSID:     ${AP_SSID}"
echo " Password: ${AP_PASSWORD}"
echo " IP:       ${AP_IP}"
echo " Interface: ${WIFI_INTERFACE}"
echo ""

# --- Install required packages ---
echo "[1/7] Installing hostapd and dnsmasq..."
apt-get update -qq
apt-get install -y hostapd dnsmasq

# Stop services during configuration
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

# --- Unblock Wi-Fi ---
echo "[2/7] Unblocking Wi-Fi..."
rfkill unblock wlan 2>/dev/null || true

# --- Free port 53 from systemd-resolved ---
echo "[3/7] Checking for port 53 conflicts..."
if systemctl is-active --quiet systemd-resolved 2>/dev/null; then
    echo "  Disabling systemd-resolved stub listener (port 53 conflict)..."
    mkdir -p /etc/systemd/resolved.conf.d
    cat > /etc/systemd/resolved.conf.d/no-stub.conf << EOF
[Resolve]
DNSStubListener=no
EOF
    systemctl restart systemd-resolved
    # Point resolv.conf to resolved's full resolver (not the stub)
    ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf 2>/dev/null || true
    echo "  Done."
else
    echo "  systemd-resolved not active, no conflict."
fi

# --- Configure static IP via a dedicated systemd service ---
echo "[4/7] Configuring static IP for ${WIFI_INTERFACE}..."

# Tell NetworkManager to ignore wlan0 if present (Bookworm)
if systemctl is-enabled --quiet NetworkManager 2>/dev/null; then
    echo "  Telling NetworkManager to ignore ${WIFI_INTERFACE}..."
    mkdir -p /etc/NetworkManager/conf.d
    cat > /etc/NetworkManager/conf.d/99-envsensor-unmanaged.conf << EOF
[keyfile]
unmanaged-devices=interface-name:${WIFI_INTERFACE}
EOF
    systemctl restart NetworkManager
    sleep 2
fi

# Tell dhcpcd to ignore wlan0 if present (Bullseye)
DHCPCD_CONF="/etc/dhcpcd.conf"
if [ -f "$DHCPCD_CONF" ]; then
    if ! grep -q "# EnvSensor AP Config" "$DHCPCD_CONF" 2>/dev/null; then
        cp "$DHCPCD_CONF" "${DHCPCD_CONF}.backup.envsensor" 2>/dev/null || true
        cat >> "$DHCPCD_CONF" << EOF

# EnvSensor AP Config
interface ${WIFI_INTERFACE}
    static ip_address=${AP_IP}/24
    nohook wpa_supplicant
EOF
    fi
fi

# Create a systemd service that reliably assigns the static IP on every boot
# This runs BEFORE hostapd and dnsmasq to eliminate race conditions
cat > /etc/systemd/system/envsensor-ap-ip.service << EOF
[Unit]
Description=Assign static IP to ${WIFI_INTERFACE} for EnvSensor AP
Before=hostapd.service dnsmasq.service
After=sys-subsystem-net-devices-${WIFI_INTERFACE}.device
Wants=sys-subsystem-net-devices-${WIFI_INTERFACE}.device

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/sbin/ip addr flush dev ${WIFI_INTERFACE}
ExecStart=/sbin/ip addr add ${AP_IP}/24 dev ${WIFI_INTERFACE}
ExecStart=/sbin/ip link set ${WIFI_INTERFACE} up

[Install]
WantedBy=multi-user.target
EOF

# Assign the IP right now too
ip addr flush dev "${WIFI_INTERFACE}" 2>/dev/null || true
ip addr add "${AP_IP}/24" dev "${WIFI_INTERFACE}" 2>/dev/null || true
ip link set "${WIFI_INTERFACE}" up

# --- Configure dnsmasq (DHCP server) ---
echo "[5/7] Configuring dnsmasq..."

DNSMASQ_CONF="/etc/dnsmasq.conf"
cp "$DNSMASQ_CONF" "${DNSMASQ_CONF}.backup.envsensor" 2>/dev/null || true

cat > "$DNSMASQ_CONF" << EOF
# EnvSensor DHCP Configuration
# bind-dynamic: binds to wlan0 dynamically — works even if the IP
# isn't assigned yet when dnsmasq starts (avoids race condition)
interface=${WIFI_INTERFACE}
bind-dynamic
no-resolv
no-hosts
dhcp-range=${DHCP_RANGE_START},${DHCP_RANGE_END},${AP_NETMASK},${DHCP_LEASE}
dhcp-option=option:router,${AP_IP}
dhcp-option=option:dns-server,${AP_IP}
dhcp-authoritative
domain=local
address=/envsensor.local/${AP_IP}
address=/#/${AP_IP}
log-queries
log-dhcp
EOF

# Make dnsmasq wait for hostapd + IP assignment
mkdir -p /etc/systemd/system/dnsmasq.service.d
cat > /etc/systemd/system/dnsmasq.service.d/envsensor.conf << EOF
[Unit]
After=hostapd.service envsensor-ap-ip.service
Wants=envsensor-ap-ip.service
EOF

# --- Configure hostapd (Access Point) ---
echo "[6/7] Configuring hostapd..."

HOSTAPD_CONF="/etc/hostapd/hostapd.conf"
cat > "$HOSTAPD_CONF" << EOF
interface=${WIFI_INTERFACE}
driver=nl80211
ssid=${AP_SSID}
hw_mode=g
channel=${AP_CHANNEL}
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=${AP_PASSWORD}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
EOF

# Point hostapd to config file (needed on Bullseye)
HOSTAPD_DEFAULT="/etc/default/hostapd"
if [ -f "$HOSTAPD_DEFAULT" ]; then
    sed -i 's|^#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' "$HOSTAPD_DEFAULT"
    sed -i 's|^DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' "$HOSTAPD_DEFAULT"
fi

# Make hostapd wait for the IP assignment service
mkdir -p /etc/systemd/system/hostapd.service.d
cat > /etc/systemd/system/hostapd.service.d/envsensor.conf << EOF
[Unit]
After=envsensor-ap-ip.service
Wants=envsensor-ap-ip.service
EOF

# --- Enable and start services ---
echo "[7/7] Enabling and starting services..."

systemctl daemon-reload
systemctl unmask hostapd
systemctl enable envsensor-ap-ip.service
systemctl enable hostapd
systemctl enable dnsmasq

# Start in the correct order
echo "  Starting AP IP assignment..."
systemctl restart envsensor-ap-ip.service
sleep 1

echo "  Starting hostapd..."
systemctl restart hostapd
sleep 3

echo "  Starting dnsmasq..."
systemctl restart dnsmasq
sleep 1

# Verify dnsmasq is actually running
if systemctl is-active --quiet dnsmasq; then
    echo "  dnsmasq is running."
else
    echo ""
    echo "  WARNING: dnsmasq failed to start. Checking logs:"
    journalctl -u dnsmasq --no-pager -n 10
    echo ""
fi

# Verify the IP is on the interface
echo ""
echo "  Interface status:"
ip addr show dev "${WIFI_INTERFACE}" | grep "inet "

echo ""
echo "========================================"
echo " Setup complete!"
echo "========================================"
echo ""
echo " The AP and DHCP server should be running now."
echo " If not, reboot: sudo reboot"
echo ""
echo " Connect to Wi-Fi: ${AP_SSID}"
echo " Password:         ${AP_PASSWORD}"
echo " Open browser:     http://${AP_IP}"
echo ""
echo " Verify DHCP is working:"
echo "   sudo journalctl -u dnsmasq -f"
echo ""
echo " To undo this setup:"
echo "   sudo systemctl disable --now hostapd dnsmasq envsensor-ap-ip"
echo "   sudo cp /etc/dnsmasq.conf.backup.envsensor /etc/dnsmasq.conf"
echo "   sudo rm -f /etc/systemd/system/envsensor-ap-ip.service"
echo "   sudo rm -rf /etc/systemd/system/dnsmasq.service.d/envsensor.conf"
echo "   sudo rm -rf /etc/systemd/system/hostapd.service.d/envsensor.conf"
echo "   sudo rm -f /etc/NetworkManager/conf.d/99-envsensor-unmanaged.conf"
echo "   sudo rm -f /etc/systemd/resolved.conf.d/no-stub.conf"
echo "   sudo systemctl daemon-reload && sudo reboot"
echo ""
