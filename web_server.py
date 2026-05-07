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
import signal
import socket
import subprocess
import threading
import time
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


def _convert_row(row):
    """Convert numeric string fields in a CSV row dict to int/float."""
    for key in row:
        if key == "timestamp":
            continue
        try:
            val = row[key]
            if val == "":
                continue
            if "." in str(val):
                row[key] = float(val)
            else:
                row[key] = int(val)
        except (ValueError, TypeError):
            pass
    return row


def _tail_lines(filepath, n):
    """Read the last n lines of a file efficiently by seeking from the end."""
    with open(filepath, "rb") as f:
        # Jump to end
        f.seek(0, 2)
        size = f.tell()
        if size == 0:
            return []

        lines = []
        block_size = 4096
        remaining = size
        data = b""

        while remaining > 0 and len(lines) <= n:
            read_size = min(block_size, remaining)
            remaining -= read_size
            f.seek(remaining)
            data = f.read(read_size) + data
            lines = data.split(b"\n")

        # Return last n non-empty lines
        lines = [l for l in lines if l.strip()]
        return [l.decode("utf-8", errors="replace") for l in lines[-n:]]


def read_csv_data(limit=None):
    """Read sensor data from CSV file. Returns list of dicts (newest first)."""
    path = Path(csv_path)
    if not path.exists():
        return []

    try:
        if limit:
            # Efficient: read only the header + last N lines
            with open(path, "r") as f:
                header_line = f.readline().strip()
            if not header_line:
                return []
            fieldnames = header_line.split(",")
            tail = _tail_lines(str(path), limit)
            rows = []
            for line in tail:
                if line.strip() == header_line:
                    continue  # Skip if we happened to grab the header
                try:
                    parsed = dict(zip(fieldnames, line.split(",")))
                    rows.append(_convert_row(parsed))
                except Exception:
                    log.debug("Skipping malformed CSV line: %s", line[:80])
            rows.reverse()  # Newest first
            return rows
        else:
            # Full read (for /api/data and CSV download)
            rows = []
            with open(path, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        rows.append(_convert_row(row))
                    except Exception:
                        log.debug("Skipping malformed CSV row")
            rows.reverse()
            return rows
    except Exception as e:
        log.error("Error reading CSV: %s", e)
        return []


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


def get_version():
    """Read the software version from the VERSION file, or detect from git."""
    version_path = Path(app.root_path) / "VERSION"
    if version_path.exists():
        try:
            return version_path.read_text().strip()
        except Exception:
            pass

    # Fallback: try to read git info directly from the repo
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%h (%cd)", "--date=short"],
            cwd=app.root_path,
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass

    return "unknown"


@app.route("/")
def index():
    """Main dashboard page."""
    latest = get_latest_reading()
    status = read_status()
    version = get_version()
    log.info("Dashboard: latest=%s, status_ok=%s, active_sensors=%s",
             "yes" if latest else "no",
             status.get("all_ok"),
             status.get("active_sensors", []))
    return render_template("index.html", latest=latest, status=status, version=version)


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


@app.route("/api/poll", methods=["POST"])
def api_poll():
    """Trigger an immediate sensor reading by sending SIGUSR1 to the collector."""
    log.info("Manual poll requested from %s", request.remote_addr)
    try:
        result = subprocess.run(
            ["pgrep", "-f", "collector.py"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [p.strip() for p in result.stdout.strip().split("\n") if p.strip()]
        if not pids:
            log.warning("Poll request failed: collector process not found")
            return jsonify({"status": "error", "message": "Collector process not found."}), 404

        for pid in pids:
            os.kill(int(pid), signal.SIGUSR1)
            log.info("Sent SIGUSR1 to collector PID %s", pid)

        return jsonify({"status": "ok", "message": "Poll triggered. New reading will appear shortly."})
    except Exception as e:
        log.error("Failed to trigger poll: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/clear-data", methods=["POST"])
def api_clear_data():
    """Delete all collected sensor data (keeps CSV header)."""
    log.info("Clear data requested from %s", request.remote_addr)
    path = Path(csv_path)
    if not path.exists():
        return jsonify({"status": "ok", "message": "No data file to clear."})

    try:
        # Read the header line
        with open(path, "r") as f:
            header = f.readline()

        # Rewrite file with only the header
        with open(path, "w") as f:
            f.write(header)
            f.flush()
            os.fsync(f.fileno())

        log.info("Sensor data cleared. CSV reset to header only.")
        return jsonify({"status": "ok", "message": "All sensor data has been deleted."})
    except Exception as e:
        log.error("Failed to clear data: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/api/battery")
def api_battery():
    """Get battery percentage from PiSugar 2 power manager."""
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(3)
        sock.connect("/tmp/pisugar-server.sock")

        # Request battery level
        sock.sendall(b"get battery\n")
        response = sock.recv(256).decode("utf-8").strip()
        # Response format: "battery: 75.50"
        battery_pct = None
        if ":" in response:
            try:
                battery_pct = float(response.split(":")[1].strip())
            except (ValueError, IndexError):
                pass

        # Request charging status
        sock.sendall(b"get battery_charging\n")
        charge_resp = sock.recv(256).decode("utf-8").strip()
        charging = "true" in charge_resp.lower()

        sock.close()

        if battery_pct is not None:
            return jsonify({"battery_pct": round(battery_pct, 1), "charging": charging})
        else:
            return jsonify({"status": "error", "message": "Could not parse battery response."}), 500
    except FileNotFoundError:
        return jsonify({"status": "error", "message": "PiSugar daemon not running (socket not found)."}), 503
    except socket.timeout:
        return jsonify({"status": "error", "message": "PiSugar daemon not responding."}), 503
    except Exception as e:
        log.error("Battery check failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


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


# --- Update progress tracking ---
_update_progress = {
    "running": False,
    "step": 0,
    "total_steps": 4,
    "step_label": "",
    "done": False,
    "success": False,
    "error": "",
}
_update_progress_lock = threading.Lock()

_UPDATE_STEPS = {
    "[1/4]": "Pulling latest code...",
    "[2/4]": "Deploying files...",
    "[3/4]": "Updating dependencies...",
    "[4/4]": "Restarting services...",
}


def _run_update(repo_dir, update_script):
    """Run the update script in a thread, tracking progress by parsing output."""
    global _update_progress
    try:
        proc = subprocess.Popen(
            ["/bin/bash", str(update_script)],
            cwd=str(repo_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

        output_lines = []
        for line in proc.stdout:
            output_lines.append(line)
            stripped = line.strip()
            log.info("update.sh: %s", stripped)

            # Detect step markers from update.sh output
            for marker, label in _UPDATE_STEPS.items():
                if marker in stripped:
                    step_num = int(marker[1])
                    with _update_progress_lock:
                        _update_progress["step"] = step_num
                        _update_progress["step_label"] = label
                    break

            # Detect early exit (already up to date)
            if "Already up to date" in stripped:
                with _update_progress_lock:
                    _update_progress["step"] = 4
                    _update_progress["step_label"] = "Already up to date."

        proc.wait(timeout=120)

        with _update_progress_lock:
            _update_progress["running"] = False
            _update_progress["done"] = True
            _update_progress["success"] = proc.returncode == 0
            if proc.returncode == 0:
                _update_progress["step"] = 4
                _update_progress["step_label"] = "Update complete!"
            else:
                _update_progress["error"] = "".join(output_lines[-10:])
                _update_progress["step_label"] = "Update failed."

        if proc.returncode == 0:
            log.info("Update completed successfully")
        else:
            log.error("Update failed (exit code %d)", proc.returncode)

    except subprocess.TimeoutExpired:
        proc.kill()
        with _update_progress_lock:
            _update_progress["running"] = False
            _update_progress["done"] = True
            _update_progress["success"] = False
            _update_progress["error"] = "Update timed out after 120 seconds."
            _update_progress["step_label"] = "Timed out."
        log.error("Update timed out after 120 seconds")
    except Exception as e:
        with _update_progress_lock:
            _update_progress["running"] = False
            _update_progress["done"] = True
            _update_progress["success"] = False
            _update_progress["error"] = str(e)
            _update_progress["step_label"] = "Error."
        log.exception("Update failed with exception: %s", e)


@app.route("/api/update", methods=["POST"])
def api_update():
    """Start the update process (runs in background)."""
    global _update_progress

    with _update_progress_lock:
        if _update_progress["running"]:
            return jsonify({"status": "error", "message": "Update already in progress."}), 409

    repo_dir = _find_repo_dir()
    if not repo_dir:
        log.error("Update requested but git repository not found under /home/*/")
        return jsonify({"status": "error", "message": "Git repository not found."}), 404

    update_script = repo_dir / "update.sh"
    if not update_script.exists():
        log.error("Update requested but update.sh not found at %s", update_script)
        return jsonify({"status": "error", "message": "update.sh not found."}), 404

    log.info("Software update requested from %s — repo: %s", request.remote_addr, repo_dir)

    with _update_progress_lock:
        _update_progress = {
            "running": True,
            "step": 0,
            "total_steps": 4,
            "step_label": "Starting update...",
            "done": False,
            "success": False,
            "error": "",
        }

    threading.Thread(target=_run_update, args=(repo_dir, update_script), daemon=True).start()
    return jsonify({"status": "ok", "message": "Update started."})


@app.route("/api/update-status")
def api_update_status():
    """API endpoint: get current update progress."""
    with _update_progress_lock:
        return jsonify(_update_progress)


def _find_repo_dir():
    """Locate the git repository directory."""
    home_base = Path("/home")
    if home_base.is_dir():
        for user_dir in sorted(home_base.iterdir()):
            candidate = user_dir / "rpi-envsensor-collector"
            if candidate.is_dir() and (candidate / ".git").is_dir():
                return candidate
    candidate = Path("/root") / "rpi-envsensor-collector"
    if candidate.is_dir() and (candidate / ".git").is_dir():
        return candidate
    return None


# --- Update availability checker (background thread) ---
_update_status = {"available": False, "local": None, "remote": None, "checked": None}
_update_lock = threading.Lock()


def _check_for_updates():
    """Background task: periodically check if remote has new commits."""
    import socket
    while True:
        time.sleep(300)  # Check every 5 minutes

        # Quick internet connectivity test (no cloud dependency — just DNS resolve)
        try:
            socket.setdefaulttimeout(5)
            socket.getaddrinfo("github.com", 443)
        except (socket.gaierror, socket.timeout, OSError):
            log.debug("Update check skipped — no internet connectivity")
            continue

        repo_dir = _find_repo_dir()
        if not repo_dir:
            continue

        try:
            # Detect repo owner for git commands
            stat_result = repo_dir.stat()
            import pwd
            try:
                repo_user = pwd.getpwuid(stat_result.st_uid).pw_name
            except KeyError:
                repo_user = "root"

            # Fetch latest remote refs
            fetch = subprocess.run(
                ["sudo", "-u", repo_user, "git", "fetch", "--quiet"],
                cwd=str(repo_dir),
                capture_output=True, text=True, timeout=30,
            )
            if fetch.returncode != 0:
                log.debug("Update check: git fetch failed: %s", fetch.stderr.strip())
                continue

            # Compare local HEAD vs remote tracking branch
            local = subprocess.run(
                ["sudo", "-u", repo_user, "git", "rev-parse", "HEAD"],
                cwd=str(repo_dir),
                capture_output=True, text=True, timeout=5,
            )
            remote = subprocess.run(
                ["sudo", "-u", repo_user, "git", "rev-parse", "@{u}"],
                cwd=str(repo_dir),
                capture_output=True, text=True, timeout=5,
            )

            if local.returncode == 0 and remote.returncode == 0:
                local_hash = local.stdout.strip()
                remote_hash = remote.stdout.strip()
                available = local_hash != remote_hash

                with _update_lock:
                    _update_status["available"] = available
                    _update_status["local"] = local_hash[:7]
                    _update_status["remote"] = remote_hash[:7]
                    _update_status["checked"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                if available:
                    log.info("Update available: local=%s remote=%s", local_hash[:7], remote_hash[:7])
                else:
                    log.debug("Update check: already up to date (%s)", local_hash[:7])
        except subprocess.TimeoutExpired:
            log.debug("Update check: timed out")
        except Exception as e:
            log.debug("Update check failed: %s", e)


@app.route("/api/update-available")
def api_update_available():
    """API endpoint: check if a software update is available."""
    with _update_lock:
        return jsonify(_update_status)


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

    # Start background update checker (only in the main process, not reloader)
    if not args.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        update_thread = threading.Thread(target=_check_for_updates, daemon=True)
        update_thread.start()
        log.info("Background update checker started (interval: 5 min)")

    app.run(host="0.0.0.0", port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
