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
echo "[1/5] Installing hostapd and dnsmasq..."
apt-get update -qq
apt-get install -y hostapd dnsmasq

# Stop services during configuration
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

# --- Configure static IP for wlan0 ---
echo "[2/5] Configuring static IP for ${WIFI_INTERFACE}..."

# Backup and configure dhcpcd
DHCPCD_CONF="/etc/dhcpcd.conf"
if ! grep -q "# EnvSensor AP Config" "$DHCPCD_CONF" 2>/dev/null; then
    cp "$DHCPCD_CONF" "${DHCPCD_CONF}.backup.envsensor" 2>/dev/null || true
    cat >> "$DHCPCD_CONF" << EOF

# EnvSensor AP Config
interface ${WIFI_INTERFACE}
    static ip_address=${AP_IP}/24
    nohook wpa_supplicant
EOF
fi

# --- Configure dnsmasq (DHCP server) ---
echo "[3/5] Configuring dnsmasq..."

DNSMASQ_CONF="/etc/dnsmasq.conf"
cp "$DNSMASQ_CONF" "${DNSMASQ_CONF}.backup.envsensor" 2>/dev/null || true

cat > "$DNSMASQ_CONF" << EOF
# EnvSensor DHCP Configuration
interface=${WIFI_INTERFACE}
dhcp-range=${DHCP_RANGE_START},${DHCP_RANGE_END},${AP_NETMASK},${DHCP_LEASE}
domain=local
address=/envsensor.local/${AP_IP}
EOF

# --- Configure hostapd (Access Point) ---
echo "[4/5] Configuring hostapd..."

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

# Point hostapd to config file
HOSTAPD_DEFAULT="/etc/default/hostapd"
if [ -f "$HOSTAPD_DEFAULT" ]; then
    sed -i 's|^#DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' "$HOSTAPD_DEFAULT"
    sed -i 's|^DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' "$HOSTAPD_DEFAULT"
fi

# --- Enable and start services ---
echo "[5/5] Enabling services..."

systemctl unmask hostapd
systemctl enable hostapd
systemctl enable dnsmasq

echo ""
echo "========================================"
echo " Setup complete!"
echo "========================================"
echo ""
echo " Reboot to activate the access point:"
echo "   sudo reboot"
echo ""
echo " After reboot:"
echo "   1. Connect to Wi-Fi: ${AP_SSID}"
echo "   2. Password: ${AP_PASSWORD}"
echo "   3. Open browser: http://${AP_IP}"
echo ""
echo " To undo this setup, restore backups:"
echo "   sudo cp ${DHCPCD_CONF}.backup.envsensor ${DHCPCD_CONF}"
echo "   sudo cp ${DNSMASQ_CONF}.backup.envsensor ${DNSMASQ_CONF}"
echo "   sudo systemctl disable hostapd dnsmasq"
echo ""
