# rpi-envsensor-collector

Raspberry Pi data collector for the [Waveshare Environment Sensor HAT](https://www.waveshare.com/wiki/Environment_Sensor_HAT). Automatically reads all onboard sensors every 5 minutes, logs to CSV, and serves a live web dashboard — optionally over the Pi's own Wi-Fi network.

## Sensors

| Sensor | Measurements | I2C Address |
|--------|-------------|-------------|
| **BME280** | Temperature, Humidity, Pressure | 0x76 |
| **TSL2591** | Visible Light, Infrared, Lux | 0x29 |
| **LTR390** | UV Index, UV Raw, Ambient Light | 0x53 |
| **SGP40** | VOC Raw Signal, VOC Index | 0x59 |
| **ICM20948** | Accelerometer, Gyroscope, Magnetometer | 0x68 |

## Quick Start

### 1. Clone to your Raspberry Pi

```bash
git clone https://github.com/YOUR_USERNAME/rpi-envsensor-collector.git
cd rpi-envsensor-collector
```

### 2. Run the installer

```bash
sudo bash install.sh
```

This will:
- Enable the I2C interface
- Install Python dependencies (`smbus2`, `flask`)
- Copy project files to `/opt/envsensor-collector/`
- Create a data directory at `/home/pi/envdata/`
- Set up and start two systemd services (collector + web dashboard)

### 3. Verify

```bash
# Check I2C devices are detected
sudo i2cdetect -y 1

# Check service status
sudo systemctl status envsensor-collector
sudo systemctl status envsensor-web

# View live logs
sudo journalctl -u envsensor-collector -f
```

### 4. (Optional) Set up Wi-Fi Access Point

To let the Pi broadcast its own network so you can connect from any device:

```bash
sudo bash setup_ap.sh
sudo reboot
```

After reboot:
1. Connect to Wi-Fi network **EnvSensor** (password: `envsensor123`)
2. Open a browser to **http://192.168.4.1**

## Project Structure

```
rpi-envsensor-collector/
├── collector.py          # Main data collection loop
├── web_server.py         # Flask web dashboard
├── sensors/
│   ├── bme280.py         # Temperature, humidity, pressure
│   ├── tsl2591.py        # Ambient light (visible + IR)
│   ├── ltr390.py         # UV sensor
│   ├── sgp40.py          # VOC sensor
│   └── icm20948.py       # 9-axis motion sensor
├── templates/
│   └── index.html        # Dashboard HTML template
├── install.sh            # One-step installer
├── setup_ap.sh           # Wi-Fi AP configuration
└── requirements.txt      # Python dependencies
```

## Usage

### Collector

```bash
# Run manually (single reading)
sudo /opt/envsensor-collector/venv/bin/python3 collector.py --once

# Run with custom interval (e.g. every 60 seconds)
sudo /opt/envsensor-collector/venv/bin/python3 collector.py --interval 60

# Custom CSV path
sudo /opt/envsensor-collector/venv/bin/python3 collector.py --csv /tmp/test.csv
```

### Web Dashboard

```bash
# Run on a different port
python3 web_server.py --port 8080 --csv /home/pi/envdata/sensor_data.csv
```

### Services

```bash
# Start / stop / restart
sudo systemctl start envsensor-collector
sudo systemctl stop envsensor-collector
sudo systemctl restart envsensor-web

# Disable auto-start
sudo systemctl disable envsensor-collector
sudo systemctl disable envsensor-web
```

## CSV Output

Data is written to `/home/pi/envdata/sensor_data.csv` with these columns:

```
timestamp, temperature_c, humidity_pct, pressure_hpa, lux, visible,
infrared, uv_index, uv_raw, als_lux, voc_raw, voc_index,
accel_x_g, accel_y_g, accel_z_g, gyro_x_dps, gyro_y_dps, gyro_z_dps,
mag_x_ut, mag_y_ut, mag_z_ut
```

## Web Dashboard Features

- **Live cards** showing latest temperature, humidity, pressure, light, UV, VOC, and motion data
- **History table** with the last 24 hours of readings (288 entries at 5-min intervals)
- **CSV download** button
- **JSON API** endpoints:
  - `GET /api/latest` — most recent reading
  - `GET /api/data` — all data (newest first)
  - `GET /api/data/<N>` — last N readings
- Auto-refreshes every 60 seconds

## Wi-Fi Access Point Details

| Setting | Value |
|---------|-------|
| SSID | `EnvSensor` |
| Password | `envsensor123` |
| Pi IP | `192.168.4.1` |
| DHCP Range | `192.168.4.10` – `192.168.4.50` |

Edit `setup_ap.sh` variables to customize these settings before running.

> **Note:** Setting up the AP dedicates `wlan0` to the access point. If you need internet on the Pi, connect via Ethernet or use a second Wi-Fi adapter.

## Requirements

- Raspberry Pi (any model with 40-pin GPIO header)
- Waveshare Environment Sensor HAT
- Raspberry Pi OS (Bookworm or Bullseye)
- I2C enabled (`raspi-config` → Interface Options → I2C)
- Python 3.9+

## Troubleshooting

**No sensors detected:**
```bash
sudo i2cdetect -y 1
# Should show devices at 0x29, 0x53, 0x59, 0x68, 0x76
```

**Permission errors:** The collector needs root for I2C access. The systemd services run as root by default.

**Temperature reads high:** The BME280 can be affected by Pi CPU heat. Adding a fan or standoff helps.

**VOC readings unstable at startup:** The SGP40 needs ~60 seconds to warm up. Early readings may be inaccurate.

## License

MIT
