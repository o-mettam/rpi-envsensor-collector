#!/usr/bin/env python3
"""
Environment Sensor HAT Web Dashboard

Lightweight Flask web server that serves the CSV sensor data as an
interactive HTML dashboard. Designed to run on a Raspberry Pi that
broadcasts its own Wi-Fi network.

Usage:
    python3 web_server.py [--csv PATH] [--port PORT]
"""

import argparse
import csv
import json
import logging
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("envsensor-web")

DEFAULT_CSV_PATH = os.path.expanduser("~/envdata/sensor_data.csv")
DEFAULT_PORT = 80

app = Flask(__name__, template_folder="templates")
csv_path = DEFAULT_CSV_PATH


@app.before_request
def log_request():
    """Log every incoming HTTP request."""
    log.info(
        ">>> %s %s from %s (User-Agent: %s)",
        request.method,
        request.url,
        request.remote_addr,
        request.headers.get("User-Agent", "unknown"),
    )
    if request.method == "POST":
        log.info("    Content-Type: %s, Content-Length: %s",
                 request.content_type, request.content_length)


@app.after_request
def log_response(response):
    """Log every outgoing HTTP response."""
    log.info(
        "<<< %s %s -> %s (%s bytes)",
        request.method,
        request.path,
        response.status,
        response.content_length or 0,
    )
    return response


def read_csv_data(limit=None):
    """Read sensor data from CSV file. Returns list of dicts (newest first)."""
    path = Path(csv_path)
    if not path.exists():
        return []

    rows = []
    with open(path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Convert numeric fields
            for key in row:
                if key == "timestamp":
                    continue
                try:
                    if "." in str(row[key]):
                        row[key] = float(row[key])
                    elif row[key] != "":
                        row[key] = int(row[key])
                except (ValueError, TypeError):
                    pass
            rows.append(row)

    rows.reverse()  # Newest first
    if limit:
        rows = rows[:limit]
    return rows


def get_latest_reading():
    """Get the most recent sensor reading."""
    rows = read_csv_data(limit=1)
    return rows[0] if rows else None


def read_status():
    """Read sensor health status written by the collector."""
    status_path = Path(csv_path).with_suffix(".status.json")
    if not status_path.exists():
        return {
            "all_ok": False,
            "active_sensors": [],
            "failed_sensors": {},
            "updated": None,
            "collector_missing": True,
        }
    try:
        with open(status_path, "r") as f:
            return json.load(f)
    except Exception:
        return {
            "all_ok": False,
            "active_sensors": [],
            "failed_sensors": {},
            "updated": None,
            "collector_missing": True,
        }


@app.route("/")
def index():
    """Main dashboard page."""
    latest = get_latest_reading()
    status = read_status()
    log.info("Dashboard: latest=%s, status_ok=%s, active_sensors=%s",
             "yes" if latest else "no",
             status.get("all_ok"),
             status.get("active_sensors", []))
    return render_template("index.html", latest=latest, status=status)


@app.route("/api/latest")
def api_latest():
    """API endpoint: latest sensor reading as JSON."""
    latest = get_latest_reading()
    if latest:
        log.info("API /api/latest: returning reading from %s", latest.get("timestamp", "?"))
        return jsonify(latest)
    log.warning("API /api/latest: no data available")
    return jsonify({"error": "No data available"}), 404


@app.route("/api/data")
def api_data():
    """API endpoint: all sensor data as JSON (newest first)."""
    rows = read_csv_data()
    log.info("API /api/data: returning %d rows", len(rows))
    return jsonify(rows)


@app.route("/api/data/<int:count>")
def api_data_limited(count):
    """API endpoint: last N sensor readings as JSON."""
    rows = read_csv_data(limit=count)
    log.info("API /api/data/%d: returning %d rows", count, len(rows))
    return jsonify(rows)


@app.route("/csv")
def download_csv():
    """Serve the raw CSV file for download."""
    path = Path(csv_path)
    if not path.exists():
        log.warning("CSV download requested but file not found: %s", path)
        return "No data file found.", 404

    with open(path, "r") as f:
        content = f.read()

    log.info("CSV download: %d bytes", len(content))
    return content, 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=sensor_data.csv",
    }


@app.route("/api/restart", methods=["POST"])
def api_restart():
    """Restart the Raspberry Pi."""
    log.info("Reboot requested from %s", request.remote_addr)
    def do_restart():
        import time
        time.sleep(2)
        log.info("Executing system reboot now...")
        ret = os.system("systemctl reboot")
        if ret != 0:
            log.error("systemctl reboot returned exit code %d", ret)
    threading.Thread(target=do_restart, daemon=True).start()
    return jsonify({"status": "ok", "message": "Device is restarting..."})


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Shut down the Raspberry Pi."""
    log.info("Shutdown requested from %s", request.remote_addr)
    def do_shutdown():
        import time
        time.sleep(2)
        log.info("Executing system shutdown now...")
        ret = os.system("systemctl poweroff")
        if ret != 0:
            log.error("systemctl poweroff returned exit code %d", ret)
    threading.Thread(target=do_shutdown, daemon=True).start()
    return jsonify({"status": "ok", "message": "Device is shutting down..."})


@app.route("/api/update", methods=["POST"])
def api_update():
    """Pull latest code and redeploy via update.sh."""
    # Look for the git repo: check every user's home directory under /home/
    repo_dir = None
    home_base = Path("/home")
    if home_base.is_dir():
        for user_dir in sorted(home_base.iterdir()):
            candidate = user_dir / "rpi-envsensor-collector"
            if candidate.is_dir() and (candidate / ".git").is_dir():
                repo_dir = candidate
                break
    # Also check /root in case cloned there
    if not repo_dir:
        candidate = Path("/root") / "rpi-envsensor-collector"
        if candidate.is_dir() and (candidate / ".git").is_dir():
            repo_dir = candidate

    if not repo_dir:
        log.error("Update requested but git repository not found under /home/*/")
        return jsonify({"status": "error", "message": "Git repository not found."}), 404

    update_script = repo_dir / "update.sh"
    if not update_script.exists():
        log.error("Update requested but update.sh not found at %s", update_script)
        return jsonify({"status": "error", "message": "update.sh not found."}), 404

    log.info("Software update requested from %s — repo: %s", request.remote_addr, repo_dir)
    try:
        result = subprocess.run(
            ["/bin/bash", str(update_script)],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            log.info("Update completed successfully")
        else:
            log.error("Update failed (exit code %d): %s", result.returncode, result.stderr[-500:] if result.stderr else "")
        if result.stdout:
            log.info("Update stdout:\n%s", result.stdout[-2000:])
        if result.stderr:
            log.warning("Update stderr:\n%s", result.stderr[-1000:])
        return jsonify({
            "status": "ok" if result.returncode == 0 else "error",
            "message": result.stdout[-2000:] if result.stdout else "",
            "errors": result.stderr[-1000:] if result.stderr else "",
            "returncode": result.returncode,
        })
    except subprocess.TimeoutExpired:
        log.error("Update timed out after 120 seconds")
        return jsonify({"status": "error", "message": "Update timed out."}), 504
    except Exception as e:
        log.exception("Update failed with exception: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


def main():
    parser = argparse.ArgumentParser(
        description="Environment Sensor HAT Web Dashboard"
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=DEFAULT_CSV_PATH,
        help=f"Path to CSV data file (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Web server port (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Flask debug mode",
    )
    args = parser.parse_args()

    global csv_path
    csv_path = args.csv

    log.info("========================================")
    log.info(" Environment Sensor Web Dashboard")
    log.info("========================================")
    log.info("CSV path:  %s", args.csv)
    log.info("Port:      %d", args.port)
    log.info("Debug:     %s", args.debug)
    log.info("PID:       %d", os.getpid())
    log.info("User:      %s (uid=%d)", os.environ.get('USER', 'unknown'), os.getuid())
    log.info("========================================")
    app.run(host="0.0.0.0", port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
