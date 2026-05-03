#!/bin/bash
# =============================================================================
# Environment Sensor HAT Collector - Update Script
# =============================================================================
#
# Pulls the latest code from the git repository and redeploys the
# application files. Does NOT touch:
#   - Collected CSV data
#   - Wi-Fi AP configuration
#   - systemd service files (re-run install.sh if service config changes)
#
# Usage: sudo bash update.sh
#
# Options:
#   --branch <name>   Pull from a specific branch (default: current branch)
#   --no-restart      Update files but don't restart services
#   --force           Discard local changes before pulling
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/envsensor-collector"
VENV_DIR="${INSTALL_DIR}/venv"
REPO_DIR="${SCRIPT_DIR}"

# --- Parse arguments ---
BRANCH=""
NO_RESTART=false
FORCE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --branch)
            BRANCH="$2"
            shift 2
            ;;
        --no-restart)
            NO_RESTART=true
            shift
            ;;
        --force)
            FORCE=true
            shift
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: sudo bash update.sh [--branch <name>] [--no-restart] [--force]"
            exit 1
            ;;
    esac
done

# --- Checks ---
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: This script must be run as root (sudo)."
    exit 1
fi

if [ ! -d "${INSTALL_DIR}" ]; then
    echo "ERROR: Install directory not found at ${INSTALL_DIR}."
    echo "       Run install.sh first."
    exit 1
fi

if [ ! -d "${REPO_DIR}/.git" ]; then
    echo "ERROR: Not a git repository: ${REPO_DIR}"
    echo "       update.sh must be run from the cloned repo directory."
    exit 1
fi

# Detect the real user for git operations — use the owner of the repo directory
# (SUDO_USER may not be set when called from the web server)
RUNNING_USER="${SUDO_USER:-$(stat -c '%U' "${REPO_DIR}" 2>/dev/null || stat -f '%Su' "${REPO_DIR}" 2>/dev/null || echo root)}"

echo "========================================"
echo " Environment Sensor HAT - Updater"
echo "========================================"
echo ""

# --- Record current version ---
CURRENT_COMMIT=$(sudo -u "${RUNNING_USER}" git -C "${REPO_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo " Current version: ${CURRENT_COMMIT}"

# --- Pull latest code ---
echo ""
echo "[1/4] Pulling latest code..."

if [ "$FORCE" = true ]; then
    echo "  Discarding local changes (--force)..."
    sudo -u "${RUNNING_USER}" git -C "${REPO_DIR}" reset --hard HEAD
    sudo -u "${RUNNING_USER}" git -C "${REPO_DIR}" clean -fd
fi

if [ -n "$BRANCH" ]; then
    echo "  Switching to branch: ${BRANCH}"
    sudo -u "${RUNNING_USER}" git -C "${REPO_DIR}" fetch --all
    sudo -u "${RUNNING_USER}" git -C "${REPO_DIR}" checkout "${BRANCH}"
fi

sudo -u "${RUNNING_USER}" git -C "${REPO_DIR}" pull

NEW_COMMIT=$(sudo -u "${RUNNING_USER}" git -C "${REPO_DIR}" rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo "  Updated: ${CURRENT_COMMIT} -> ${NEW_COMMIT}"

if [ "${CURRENT_COMMIT}" = "${NEW_COMMIT}" ]; then
    echo ""
    echo "  Already up to date. No changes to deploy."
    echo ""
    exit 0
fi

# --- Deploy updated files ---
echo ""
echo "[2/4] Deploying updated files to ${INSTALL_DIR}..."

cp -r "${REPO_DIR}/sensors" "${INSTALL_DIR}/"
cp -r "${REPO_DIR}/templates" "${INSTALL_DIR}/"
cp "${REPO_DIR}/collector.py" "${INSTALL_DIR}/"
cp "${REPO_DIR}/web_server.py" "${INSTALL_DIR}/"
cp "${REPO_DIR}/requirements.txt" "${INSTALL_DIR}/"

echo "  Files copied."

# --- Update Python dependencies ---
echo ""
echo "[3/4] Updating Python dependencies..."

"${VENV_DIR}/bin/pip" install --upgrade pip -q
"${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" -q

echo "  Dependencies up to date."

# --- Restart services ---
echo ""
echo "[4/4] Restarting services..."

if [ "$NO_RESTART" = true ]; then
    echo "  Skipped (--no-restart flag set)."
    echo "  Restart manually:"
    echo "    sudo systemctl restart envsensor-collector envsensor-web"
else
    systemctl daemon-reload
    systemctl restart envsensor-collector.service
    systemctl restart envsensor-web.service
    echo "  Services restarted."
fi

echo ""
echo "========================================"
echo " Update complete!  ${CURRENT_COMMIT} -> ${NEW_COMMIT}"
echo "========================================"
echo ""
echo " Check status:"
echo "   sudo systemctl status envsensor-collector"
echo "   sudo systemctl status envsensor-web"
echo ""
