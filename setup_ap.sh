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
echo "[1/6] Installing hostapd and dnsmasq..."
apt-get update -qq
apt-get install -y hostapd dnsmasq

# Stop services during configuration
systemctl stop hostapd 2>/dev/null || true
systemctl stop dnsmasq 2>/dev/null || true

# --- Unblock Wi-Fi ---
echo "[2/6] Unblocking Wi-Fi..."
rfkill unblock wlan 2>/dev/null || true

# --- Configure static IP for wlan0 ---
echo "[3/6] Configuring static IP for ${WIFI_INTERFACE}..."

# Detect whether we're on Bookworm (NetworkManager) or Bullseye (dhcpcd)
if systemctl is-active --quiet NetworkManager 2>/dev/null; then
    echo "  Detected NetworkManager (Bookworm) - configuring via nmcli"

    # Tell NetworkManager to ignore wlan0 so hostapd can manage it
    NM_UNMANAGED_CONF="/etc/NetworkManager/conf.d/99-envsensor-unmanaged.conf"
    cat > "$NM_UNMANAGED_CONF" << EOF
[keyfile]
unmanaged-devices=interface-name:${WIFI_INTERFACE}
EOF
    systemctl restart NetworkManager
    sleep 2
else
    echo "  Detected dhcpcd (Bullseye) - configuring via dhcpcd.conf"

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
fi

# Always assign the static IP directly to ensure it's set now
ip addr flush dev "${WIFI_INTERFACE}" 2>/dev/null || true
ip addr add "${AP_IP}/24" dev "${WIFI_INTERFACE}" 2>/dev/null || true
ip link set "${WIFI_INTERFACE}" up

# --- Configure dnsmasq (DHCP server) ---
echo "[4/6] Configuring dnsmasq..."

DNSMASQ_CONF="/etc/dnsmasq.conf"
cp "$DNSMASQ_CONF" "${DNSMASQ_CONF}.backup.envsensor" 2>/dev/null || true

cat > "$DNSMASQ_CONF" << EOF
# EnvSensor DHCP Configuration
interface=${WIFI_INTERFACE}
bind-interfaces
listen-address=${AP_IP}
dhcp-range=${DHCP_RANGE_START},${DHCP_RANGE_END},${AP_NETMASK},${DHCP_LEASE}
dhcp-option=option:router,${AP_IP}
dhcp-option=option:dns-server,${AP_IP}
domain=local
address=/envsensor.local/${AP_IP}
log-queries
log-dhcp
EOF

# Prevent dnsmasq from reading /etc/resolv.conf (avoids port 53 conflicts)
mkdir -p /etc/systemd/system/dnsmasq.service.d
cat > /etc/systemd/system/dnsmasq.service.d/envsensor.conf << EOF
[Unit]
After=hostapd.service
BindsTo=hostapd.service
EOF

# --- Configure hostapd (Access Point) ---
echo "[5/6] Configuring hostapd..."

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

# --- Enable and start services ---
echo "[6/6] Enabling and starting services..."

systemctl daemon-reload
systemctl unmask hostapd
systemctl enable hostapd
systemctl enable dnsmasq

# Start now so the user can test without rebooting
systemctl start hostapd
sleep 2
systemctl start dnsmasq

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
echo "   sudo systemctl disable hostapd dnsmasq"
echo "   sudo systemctl stop hostapd dnsmasq"
echo "   sudo cp /etc/dnsmasq.conf.backup.envsensor /etc/dnsmasq.conf"
echo "   sudo rm -f /etc/NetworkManager/conf.d/99-envsensor-unmanaged.conf"
echo "   sudo rm -f /etc/systemd/system/dnsmasq.service.d/envsensor.conf"
echo "   sudo reboot"
echo ""
