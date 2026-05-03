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
import os
from datetime import datetime
from pathlib import Path

from flask import Flask, render_template, jsonify

DEFAULT_CSV_PATH = os.path.expanduser("~/envdata/sensor_data.csv")
DEFAULT_PORT = 80

app = Flask(__name__, template_folder="templates")
csv_path = DEFAULT_CSV_PATH


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
    return render_template("index.html", latest=latest, status=status)


@app.route("/api/latest")
def api_latest():
    """API endpoint: latest sensor reading as JSON."""
    latest = get_latest_reading()
    if latest:
        return jsonify(latest)
    return jsonify({"error": "No data available"}), 404


@app.route("/api/data")
def api_data():
    """API endpoint: all sensor data as JSON (newest first)."""
    rows = read_csv_data()
    return jsonify(rows)


@app.route("/api/data/<int:count>")
def api_data_limited(count):
    """API endpoint: last N sensor readings as JSON."""
    rows = read_csv_data(limit=count)
    return jsonify(rows)


@app.route("/csv")
def download_csv():
    """Serve the raw CSV file for download."""
    path = Path(csv_path)
    if not path.exists():
        return "No data file found.", 404

    with open(path, "r") as f:
        content = f.read()

    return content, 200, {
        "Content-Type": "text/csv",
        "Content-Disposition": "attachment; filename=sensor_data.csv",
    }


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

    print(f"Starting web dashboard on port {args.port}")
    print(f"Reading data from: {args.csv}")
    app.run(host="0.0.0.0", port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
