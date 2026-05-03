#!/usr/bin/env python3
"""
Environment Sensor HAT Data Collector

Reads all sensors on the Waveshare Environment Sensor HAT every 5 minutes
and appends the data to a CSV file.

Sensors:
  - BME280:   Temperature, Humidity, Pressure
  - TSL2591:  Ambient Light (Lux, Visible, IR)
  - LTR390:   UV Index, UV Raw, ALS
  - SGP40:    VOC Raw, VOC Index
  - ICM20948: Accelerometer, Gyroscope, Magnetometer

Usage:
    sudo python3 collector.py [--interval SECONDS] [--csv PATH] [--once]
"""

import argparse
import csv
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

import smbus2

from sensors.bme280 import BME280
from sensors.tsl2591 import TSL2591
from sensors.ltr390 import LTR390
from sensors.sgp40 import SGP40
from sensors.icm20948 import ICM20948

DEFAULT_CSV_PATH = os.path.expanduser("~/envdata/sensor_data.csv")
DEFAULT_INTERVAL = 300  # 5 minutes in seconds
I2C_BUS = 1

# CSV column order
CSV_COLUMNS = [
    "timestamp",
    # BME280
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    # TSL2591
    "lux",
    "visible",
    "infrared",
    # LTR390
    "uv_index",
    "uv_raw",
    "als_lux",
    # SGP40
    "voc_raw",
    "voc_index",
    # ICM20948
    "accel_x_g",
    "accel_y_g",
    "accel_z_g",
    "gyro_x_dps",
    "gyro_y_dps",
    "gyro_z_dps",
    "mag_x_ut",
    "mag_y_ut",
    "mag_z_ut",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("collector")

running = True


def signal_handler(signum, frame):
    global running
    log.info("Shutdown signal received, stopping...")
    running = False


SENSOR_CLASSES = [
    ("bme280", BME280),
    ("tsl2591", TSL2591),
    ("ltr390", LTR390),
    ("sgp40", SGP40),
    ("icm20948", ICM20948),
]


def init_sensors(bus):
    """Initialize all sensors. Returns dict of sensor instances (or None on failure)."""
    sensors = {}
    errors = {}

    for name, cls in SENSOR_CLASSES:
        try:
            sensors[name] = cls(bus)
            log.info("Initialized %s", name.upper())
        except Exception as e:
            log.warning("Failed to initialize %s: %s", name.upper(), e)
            sensors[name] = None
            errors[name] = str(e)

    return sensors, errors


def retry_failed_sensors(bus, sensors, errors):
    """Attempt to re-initialize any sensors that previously failed."""
    cls_map = dict(SENSOR_CLASSES)
    for name in list(errors.keys()):
        try:
            sensors[name] = cls_map[name](bus)
            log.info("Recovered %s", name.upper())
            del errors[name]
        except Exception as e:
            errors[name] = str(e)
    return sensors, errors


def read_all_sensors(sensors):
    """Read from all initialized sensors and return a flat dict of values."""
    row = {}

    # BME280 - temperature, humidity, pressure
    if sensors.get("bme280"):
        try:
            data = sensors["bme280"].read()
            row.update(data)
        except Exception as e:
            log.error("BME280 read error: %s", e)

    # TSL2591 - ambient light
    if sensors.get("tsl2591"):
        try:
            data = sensors["tsl2591"].read()
            row["lux"] = data["lux"]
            row["visible"] = data["visible"]
            row["infrared"] = data["infrared"]
        except Exception as e:
            log.error("TSL2591 read error: %s", e)

    # LTR390 - UV + ALS
    if sensors.get("ltr390"):
        try:
            data = sensors["ltr390"].read()
            row.update(data)
        except Exception as e:
            log.error("LTR390 read error: %s", e)

    # SGP40 - VOC (compensated with BME280 temp/humidity if available)
    if sensors.get("sgp40"):
        try:
            temp = row.get("temperature_c", 25.0)
            hum = row.get("humidity_pct", 50.0)
            data = sensors["sgp40"].read(
                humidity_pct=hum, temperature_c=temp
            )
            row.update(data)
        except Exception as e:
            log.error("SGP40 read error: %s", e)

    # ICM20948 - motion
    if sensors.get("icm20948"):
        try:
            data = sensors["icm20948"].read()
            # Exclude the ICM die temperature (we use BME280 instead)
            data.pop("temperature_c", None)
            row.update(data)
        except Exception as e:
            log.error("ICM20948 read error: %s", e)

    return row


def ensure_csv(csv_path):
    """Create CSV file with header if it doesn't exist."""
    path = Path(csv_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()
        log.info("Created CSV file: %s", csv_path)


def append_row(csv_path, row):
    """Append a data row to the CSV file."""
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writerow(row)


def write_status(csv_path, sensors, errors):
    """Write a JSON status file next to the CSV so the web server can report health."""
    status_path = Path(csv_path).with_suffix(".status.json")
    active = [k for k, v in sensors.items() if v is not None]
    status = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "active_sensors": active,
        "failed_sensors": errors,
        "all_ok": len(errors) == 0,
    }
    try:
        with open(status_path, "w") as f:
            json.dump(status, f, indent=2)
    except Exception as e:
        log.error("Failed to write status file: %s", e)


def main():
    parser = argparse.ArgumentParser(
        description="Waveshare Environment Sensor HAT Data Collector"
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help=f"Collection interval in seconds (default: {DEFAULT_INTERVAL})",
    )
    parser.add_argument(
        "--csv",
        type=str,
        default=DEFAULT_CSV_PATH,
        help=f"Path to CSV output file (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Collect a single reading and exit",
    )
    parser.add_argument(
        "--bus",
        type=int,
        default=I2C_BUS,
        help=f"I2C bus number (default: {I2C_BUS})",
    )
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    log.info("Starting Environment Sensor Collector")
    log.info("CSV output: %s", args.csv)
    log.info("Interval: %d seconds", args.interval)

    bus = smbus2.SMBus(args.bus)

    try:
        sensors, errors = init_sensors(bus)
        active = [k for k, v in sensors.items() if v is not None]
        if not active:
            log.warning("No sensors initialized. Will keep retrying...")
        else:
            log.info("Active sensors: %s", ", ".join(s.upper() for s in active))
        if errors:
            log.warning("Failed sensors: %s", ", ".join(errors.keys()))

        ensure_csv(args.csv)
        write_status(args.csv, sensors, errors)

        while running:
            # Retry any failed sensors each cycle
            if errors:
                sensors, errors = retry_failed_sensors(bus, sensors, errors)

            active = [k for k, v in sensors.items() if v is not None]
            if active:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                row = read_all_sensors(sensors)
                row["timestamp"] = timestamp

                append_row(args.csv, row)
                log.info(
                    "Recorded: temp=%.1f°C hum=%.1f%% press=%.1fhPa lux=%.1f uv=%.2f voc=%s",
                    row.get("temperature_c", 0),
                    row.get("humidity_pct", 0),
                    row.get("pressure_hpa", 0),
                    row.get("lux", 0),
                    row.get("uv_index", 0),
                    row.get("voc_raw", "N/A"),
                )
            else:
                log.warning("No active sensors — skipping this cycle")

            write_status(args.csv, sensors, errors)

            if args.once:
                break

            # Sleep in small increments so we can respond to signals
            for _ in range(args.interval):
                if not running:
                    break
                time.sleep(1)

    finally:
        bus.close()
        log.info("Collector stopped.")


if __name__ == "__main__":
    main()
