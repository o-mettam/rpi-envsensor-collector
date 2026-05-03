#!/bin/bash
# =============================================================================
# Environment Sensor HAT Collector - Installation Script
# =============================================================================
#
# Installs all dependencies, sets up systemd services for:
#   1. Data collection (every 5 minutes to CSV)
#   2. Web dashboard (accessible via Wi-Fi AP or local network)
#
# Usage: sudo bash install.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/envsensor-collector"
VENV_DIR="${INSTALL_DIR}/venv"

# --- Checks ---
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)."
    exit 1
fi

# Detect the real user who invoked sudo
# Fall back to the owner of the script directory if SUDO_USER/logname unavailable
RUNNING_USER="${SUDO_USER:-$(logname 2>/dev/null || stat -c '%U' "${SCRIPT_DIR}" 2>/dev/null || stat -f '%Su' "${SCRIPT_DIR}" 2>/dev/null || echo root)}"
RUNNING_USER_HOME=$(eval echo "~${RUNNING_USER}")
DATA_DIR="${RUNNING_USER_HOME}/envdata"

echo "========================================"
echo " Environment Sensor HAT - Installer"
echo "========================================"
echo ""
echo " Source:  ${SCRIPT_DIR}"
echo " Install: ${INSTALL_DIR}"
echo " Data:    ${DATA_DIR}"
echo ""

# --- Enable I2C ---
echo "[1/7] Enabling I2C interface..."
if ! grep -q "^dtparam=i2c_arm=on" /boot/config.txt 2>/dev/null && \
   ! grep -q "^dtparam=i2c_arm=on" /boot/firmware/config.txt 2>/dev/null; then
    # Try both locations (older vs newer Raspberry Pi OS)
    if [ -f /boot/firmware/config.txt ]; then
        echo "dtparam=i2c_arm=on" >> /boot/firmware/config.txt
    elif [ -f /boot/config.txt ]; then
        echo "dtparam=i2c_arm=on" >> /boot/config.txt
    fi
    echo "  I2C enabled (reboot required for first-time activation)"
else
    echo "  I2C already enabled"
fi

# Load I2C module now if not loaded
modprobe i2c-dev 2>/dev/null || true

# --- Install system dependencies ---
echo "[2/7] Installing system packages..."
apt-get update -qq
apt-get install -y python3 python3-venv python3-pip python3-smbus i2c-tools

# --- Create installation directory ---
echo "[3/7] Copying project files..."
mkdir -p "${INSTALL_DIR}"
mkdir -p "${DATA_DIR}"

# Copy project files
cp -r "${SCRIPT_DIR}/sensors" "${INSTALL_DIR}/"
cp -r "${SCRIPT_DIR}/templates" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/collector.py" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/web_server.py" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/"

# Write version info
if [ -d "${SCRIPT_DIR}/.git" ]; then
    GIT_HASH=$(git -C "${SCRIPT_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")
    GIT_DATE=$(git -C "${SCRIPT_DIR}" log -1 --format=%cd --date=short 2>/dev/null || date +%Y-%m-%d)
    echo "${GIT_HASH} (${GIT_DATE})" > "${INSTALL_DIR}/VERSION"
else
    echo "unknown ($(date +%Y-%m-%d))" > "${INSTALL_DIR}/VERSION"
fi

# Set ownership
chown -R "${RUNNING_USER}:${RUNNING_USER}" "${DATA_DIR}"
chown -R root:root "${INSTALL_DIR}"

# --- Create virtual environment and install Python packages ---
echo "[4/7] Setting up Python virtual environment..."
python3 -m venv "${VENV_DIR}" --system-site-packages
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# --- Install systemd service: collector ---
echo "[5/7] Installing collector service..."
cat > /etc/systemd/system/envsensor-collector.service << EOF
[Unit]
Description=Environment Sensor HAT Data Collector
After=multi-user.target
StartLimitBurst=10
StartLimitIntervalSec=600

[Service]
Type=simple
ExecStartPre=/bin/sleep 15
ExecStart=${VENV_DIR}/bin/python3 ${INSTALL_DIR}/collector.py --csv ${DATA_DIR}/sensor_data.csv --interval 300
WorkingDirectory=${INSTALL_DIR}
Restart=always
RestartSec=10
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# --- Install systemd service: web server ---
echo "[6/7] Installing web dashboard service..."
cat > /etc/systemd/system/envsensor-web.service << EOF
[Unit]
Description=Environment Sensor HAT Web Dashboard
After=multi-user.target envsensor-collector.service
Wants=envsensor-collector.service
StartLimitBurst=10
StartLimitIntervalSec=600

[Service]
Type=simple
ExecStartPre=/bin/sleep 10
ExecStart=${VENV_DIR}/bin/python3 ${INSTALL_DIR}/web_server.py --csv ${DATA_DIR}/sensor_data.csv --port 80
WorkingDirectory=${INSTALL_DIR}
Restart=always
RestartSec=5
User=root
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# --- Enable and start services ---
echo "[7/7] Enabling services..."
systemctl daemon-reload
systemctl enable envsensor-collector.service
systemctl enable envsensor-web.service
systemctl start envsensor-collector.service
systemctl start envsensor-web.service

echo ""
echo "========================================"
echo " Installation complete!"
echo "========================================"
echo ""
echo " Services:"
echo "   sudo systemctl status envsensor-collector"
echo "   sudo systemctl status envsensor-web"
echo ""
echo " Logs:"
echo "   sudo journalctl -u envsensor-collector -f"
echo "   sudo journalctl -u envsensor-web -f"
echo ""
echo " CSV data: ${DATA_DIR}/sensor_data.csv"
echo ""
echo " Verify I2C devices:"
echo "   sudo i2cdetect -y 1"
echo ""
echo " Optional: Set up Wi-Fi access point:"
echo "   sudo bash ${SCRIPT_DIR}/setup_ap.sh"
echo ""
