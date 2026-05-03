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

    log.info("Initializing %d sensors...", len(SENSOR_CLASSES))
    for name, cls in SENSOR_CLASSES:
        try:
            log.debug("  Initializing %s (class=%s)...", name.upper(), cls.__name__)
            sensors[name] = cls(bus)
            log.info("  [OK] %s initialized successfully", name.upper())
        except Exception as e:
            log.warning("  [FAIL] %s: %s", name.upper(), e)
            sensors[name] = None
            errors[name] = str(e)

    log.info("Sensor init complete: %d OK, %d failed",
             sum(1 for v in sensors.values() if v is not None),
             len(errors))
    return sensors, errors


def retry_failed_sensors(bus, sensors, errors):
    """Attempt to re-initialize any sensors that previously failed."""
    cls_map = dict(SENSOR_CLASSES)
    log.info("Retrying %d failed sensor(s): %s", len(errors), ", ".join(errors.keys()))
    for name in list(errors.keys()):
        try:
            sensors[name] = cls_map[name](bus)
            log.info("  [RECOVERED] %s is now available", name.upper())
            del errors[name]
        except Exception as e:
            log.debug("  [STILL FAILED] %s: %s", name.upper(), e)
            errors[name] = str(e)
    return sensors, errors


def read_all_sensors(sensors):
    """Read from all initialized sensors and return a flat dict of values."""
    row = {}
    read_ok = []
    read_fail = []

    # BME280 - temperature, humidity, pressure
    if sensors.get("bme280"):
        try:
            data = sensors["bme280"].read()
            row.update(data)
            log.debug("  BME280: temp=%.1f°C hum=%.1f%% press=%.1fhPa",
                      data.get("temperature_c", 0), data.get("humidity_pct", 0), data.get("pressure_hpa", 0))
            read_ok.append("BME280")
        except Exception as e:
            log.error("  BME280 read error: %s", e)
            read_fail.append("BME280")

    # TSL2591 - ambient light
    if sensors.get("tsl2591"):
        try:
            data = sensors["tsl2591"].read()
            row["lux"] = data["lux"]
            row["visible"] = data["visible"]
            row["infrared"] = data["infrared"]
            log.debug("  TSL2591: lux=%.1f vis=%s ir=%s", data["lux"], data["visible"], data["infrared"])
            read_ok.append("TSL2591")
        except Exception as e:
            log.error("  TSL2591 read error: %s", e)
            read_fail.append("TSL2591")

    # LTR390 - UV + ALS
    if sensors.get("ltr390"):
        try:
            data = sensors["ltr390"].read()
            row.update(data)
            log.debug("  LTR390: uv_index=%.2f uv_raw=%s als_lux=%.1f",
                      data.get("uv_index", 0), data.get("uv_raw", 0), data.get("als_lux", 0))
            read_ok.append("LTR390")
        except Exception as e:
            log.error("  LTR390 read error: %s", e)
            read_fail.append("LTR390")

    # SGP40 - VOC (compensated with BME280 temp/humidity if available)
    if sensors.get("sgp40"):
        try:
            temp = row.get("temperature_c", 25.0)
            hum = row.get("humidity_pct", 50.0)
            data = sensors["sgp40"].read(
                humidity_pct=hum, temperature_c=temp
            )
            row.update(data)
            log.debug("  SGP40: voc_raw=%s voc_index=%s (comp: temp=%.1f hum=%.1f)",
                      data.get("voc_raw"), data.get("voc_index"), temp, hum)
            read_ok.append("SGP40")
        except Exception as e:
            log.error("  SGP40 read error: %s", e)
            read_fail.append("SGP40")

    # ICM20948 - motion
    if sensors.get("icm20948"):
        try:
            data = sensors["icm20948"].read()
            # Exclude the ICM die temperature (we use BME280 instead)
            data.pop("temperature_c", None)
            row.update(data)
            log.debug("  ICM20948: accel=(%.2f,%.2f,%.2f)g",
                      data.get("accel_x_g", 0), data.get("accel_y_g", 0), data.get("accel_z_g", 0))
            read_ok.append("ICM20948")
        except Exception as e:
            log.error("  ICM20948 read error: %s", e)
            read_fail.append("ICM20948")

    log.info("Sensor reads: %d OK [%s]%s",
             len(read_ok), ", ".join(read_ok) if read_ok else "none",
             " | %d FAILED [%s]" % (len(read_fail), ", ".join(read_fail)) if read_fail else "")
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
    """Append a data row to the CSV file, flushing to disk to prevent corruption."""
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writerow(row)
        f.flush()
        os.fsync(f.fileno())
    log.info("CSV row written to %s (%d fields)", csv_path, len(row))


def write_status(csv_path, sensors, errors):
    """Write a JSON status file next to the CSV so the web server can report health.

    Uses atomic write (temp file + rename) to prevent the web server from
    reading a partially-written file.
    """
    status_path = Path(csv_path).with_suffix(".status.json")
    tmp_path = status_path.with_suffix(".tmp")
    active = [k for k, v in sensors.items() if v is not None]
    status = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "active_sensors": active,
        "failed_sensors": errors,
        "all_ok": len(errors) == 0,
    }
    try:
        with open(tmp_path, "w") as f:
            json.dump(status, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp_path), str(status_path))
        log.debug("Status file written: %s (active=%d, failed=%d)",
                  status_path, len(active), len(errors))
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

    log.info("========================================")
    log.info(" Environment Sensor Collector")
    log.info("========================================")
    log.info("CSV output:  %s", args.csv)
    log.info("Interval:    %d seconds", args.interval)
    log.info("I2C bus:     %d", args.bus)
    log.info("Single shot: %s", args.once)
    log.info("PID:         %d", os.getpid())
    log.info("User:        uid=%d", os.getuid())
    log.info("========================================")

    log.info("Opening I2C bus %d...", args.bus)
    bus = smbus2.SMBus(args.bus)
    log.info("I2C bus %d opened successfully", args.bus)

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
                log.info("Next reading in %d seconds", args.interval)
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

    except Exception as e:
        log.exception("Unhandled exception in main loop: %s", e)
    finally:
        bus.close()
        log.info("I2C bus closed")
        log.info("Collector stopped.")


if __name__ == "__main__":
    main()
