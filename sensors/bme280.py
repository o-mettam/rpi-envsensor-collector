"""
BME280 - Temperature, Humidity, and Air Pressure Sensor
I2C Address: 0x76
Datasheet: https://files.waveshare.com/upload/9/91/BME280_datasheet.pdf

Measurement ranges:
  Temperature: -40 ~ 85 °C  (resolution 0.01 °C)
  Humidity:      0 ~ 100 %RH (resolution 0.008 %RH)
  Pressure:    300 ~ 1100 hPa (resolution 0.18 Pa)
"""

import struct
import time

# Default I2C address
BME280_ADDR = 0x76

# Registers
_CHIP_ID_REG = 0xD0
_RESET_REG = 0xE0
_CTRL_HUM_REG = 0xF2
_STATUS_REG = 0xF3
_CTRL_MEAS_REG = 0xF4
_CONFIG_REG = 0xF5
_PRESS_MSB = 0xF7
_TEMP_MSB = 0xFA
_HUM_MSB = 0xFD

# Calibration data registers
_CALIB_00 = 0x88  # 26 bytes (temp + pressure)
_CALIB_26 = 0xE1  # 7 bytes  (humidity)

# Oversampling
OVERSAMPLE_1X = 0x01
OVERSAMPLE_2X = 0x02
OVERSAMPLE_4X = 0x03
OVERSAMPLE_8X = 0x04
OVERSAMPLE_16X = 0x05

# Mode
MODE_SLEEP = 0x00
MODE_FORCED = 0x01
MODE_NORMAL = 0x03

# Standby time (normal mode)
STANDBY_0_5 = 0x00
STANDBY_62_5 = 0x01
STANDBY_125 = 0x02
STANDBY_250 = 0x03
STANDBY_500 = 0x04
STANDBY_1000 = 0x05

# Filter coefficient
FILTER_OFF = 0x00
FILTER_2 = 0x01
FILTER_4 = 0x02
FILTER_8 = 0x03
FILTER_16 = 0x04

EXPECTED_CHIP_ID = 0x60


class BME280:
    """Driver for the BME280 temperature, humidity, and pressure sensor."""

    def __init__(self, bus, address=BME280_ADDR):
        self._bus = bus
        self._address = address
        self._t_fine = 0
        self._calibration = {}
        self._init_sensor()

    def _init_sensor(self):
        """Initialize the sensor and read calibration data."""
        chip_id = self._bus.read_byte_data(self._address, _CHIP_ID_REG)
        if chip_id != EXPECTED_CHIP_ID:
            raise RuntimeError(
                f"BME280 not found at 0x{self._address:02X}. "
                f"Chip ID: 0x{chip_id:02X} (expected 0x{EXPECTED_CHIP_ID:02X})"
            )

        # Soft reset
        self._bus.write_byte_data(self._address, _RESET_REG, 0xB6)
        time.sleep(0.01)

        # Wait for NVM copy
        while self._bus.read_byte_data(self._address, _STATUS_REG) & 0x01:
            time.sleep(0.01)

        self._read_calibration()

        # Configure: humidity oversampling must be set before ctrl_meas
        self._bus.write_byte_data(self._address, _CTRL_HUM_REG, OVERSAMPLE_1X)
        # Config: standby 1000ms, filter coeff 4
        self._bus.write_byte_data(
            self._address, _CONFIG_REG, (STANDBY_1000 << 5) | (FILTER_4 << 2)
        )
        # Ctrl_meas: temp oversample 2x, press oversample 16x, normal mode
        self._bus.write_byte_data(
            self._address,
            _CTRL_MEAS_REG,
            (OVERSAMPLE_2X << 5) | (OVERSAMPLE_16X << 2) | MODE_NORMAL,
        )

    def _read_calibration(self):
        """Read factory calibration data from the sensor."""
        # Temperature and pressure calibration (0x88 .. 0xA1, 26 bytes)
        cal1 = self._bus.read_i2c_block_data(self._address, _CALIB_00, 26)
        # Humidity calibration (0xE1 .. 0xE7, 7 bytes)
        cal2 = self._bus.read_i2c_block_data(self._address, _CALIB_26, 7)
        # Also need dig_H1 from register 0xA1
        dig_H1 = self._bus.read_byte_data(self._address, 0xA1)

        c = self._calibration

        # Temperature
        c["dig_T1"] = struct.unpack_from("<H", bytes(cal1), 0)[0]
        c["dig_T2"] = struct.unpack_from("<h", bytes(cal1), 2)[0]
        c["dig_T3"] = struct.unpack_from("<h", bytes(cal1), 4)[0]

        # Pressure
        c["dig_P1"] = struct.unpack_from("<H", bytes(cal1), 6)[0]
        c["dig_P2"] = struct.unpack_from("<h", bytes(cal1), 8)[0]
        c["dig_P3"] = struct.unpack_from("<h", bytes(cal1), 10)[0]
        c["dig_P4"] = struct.unpack_from("<h", bytes(cal1), 12)[0]
        c["dig_P5"] = struct.unpack_from("<h", bytes(cal1), 14)[0]
        c["dig_P6"] = struct.unpack_from("<h", bytes(cal1), 16)[0]
        c["dig_P7"] = struct.unpack_from("<h", bytes(cal1), 18)[0]
        c["dig_P8"] = struct.unpack_from("<h", bytes(cal1), 20)[0]
        c["dig_P9"] = struct.unpack_from("<h", bytes(cal1), 22)[0]

        # Humidity
        c["dig_H1"] = dig_H1 & 0xFF
        c["dig_H2"] = struct.unpack_from("<h", bytes(cal2), 0)[0]
        c["dig_H3"] = cal2[2] & 0xFF
        c["dig_H4"] = (cal2[3] << 4) | (cal2[4] & 0x0F)
        if c["dig_H4"] > 2047:
            c["dig_H4"] -= 4096
        c["dig_H5"] = (cal2[5] << 4) | ((cal2[4] >> 4) & 0x0F)
        if c["dig_H5"] > 2047:
            c["dig_H5"] -= 4096
        c["dig_H6"] = cal2[6]
        if c["dig_H6"] > 127:
            c["dig_H6"] -= 256

    def _read_raw(self):
        """Read raw sensor data (pressure, temperature, humidity)."""
        data = self._bus.read_i2c_block_data(self._address, _PRESS_MSB, 8)
        raw_press = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
        raw_temp = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
        raw_hum = (data[6] << 8) | data[7]
        return raw_temp, raw_press, raw_hum

    def _compensate_temperature(self, raw_temp):
        """Apply compensation formula for temperature (Bosch datasheet)."""
        c = self._calibration
        var1 = (raw_temp / 16384.0 - c["dig_T1"] / 1024.0) * c["dig_T2"]
        var2 = (
            (raw_temp / 131072.0 - c["dig_T1"] / 8192.0) ** 2
        ) * c["dig_T3"]
        self._t_fine = var1 + var2
        return self._t_fine / 5120.0

    def _compensate_pressure(self, raw_press):
        """Apply compensation formula for pressure (Bosch datasheet)."""
        c = self._calibration
        var1 = self._t_fine / 2.0 - 64000.0
        var2 = var1 * var1 * c["dig_P6"] / 32768.0
        var2 = var2 + var1 * c["dig_P5"] * 2.0
        var2 = var2 / 4.0 + c["dig_P4"] * 65536.0
        var1 = (
            c["dig_P3"] * var1 * var1 / 524288.0 + c["dig_P2"] * var1
        ) / 524288.0
        var1 = (1.0 + var1 / 32768.0) * c["dig_P1"]
        if var1 == 0:
            return 0.0
        pressure = 1048576.0 - raw_press
        pressure = (pressure - var2 / 4096.0) * 6250.0 / var1
        var1 = c["dig_P9"] * pressure * pressure / 2147483648.0
        var2 = pressure * c["dig_P8"] / 32768.0
        pressure = pressure + (var1 + var2 + c["dig_P7"]) / 16.0
        return pressure / 100.0  # Convert Pa to hPa

    def _compensate_humidity(self, raw_hum):
        """Apply compensation formula for humidity (Bosch datasheet)."""
        c = self._calibration
        h = self._t_fine - 76800.0
        if h == 0:
            return 0.0
        h = (raw_hum - (c["dig_H4"] * 64.0 + c["dig_H5"] / 16384.0 * h)) * (
            c["dig_H2"]
            / 65536.0
            * (
                1.0
                + c["dig_H6"]
                / 67108864.0
                * h
                * (1.0 + c["dig_H3"] / 67108864.0 * h)
            )
        )
        h = h * (1.0 - c["dig_H1"] * h / 524288.0)
        return max(0.0, min(100.0, h))

    def read(self):
        """
        Read compensated temperature, pressure, and humidity.

        Returns:
            dict with keys:
                temperature_c (float): Temperature in degrees Celsius
                humidity_pct  (float): Relative humidity in percent
                pressure_hpa  (float): Atmospheric pressure in hPa
        """
        raw_temp, raw_press, raw_hum = self._read_raw()
        temperature = self._compensate_temperature(raw_temp)
        pressure = self._compensate_pressure(raw_press)
        humidity = self._compensate_humidity(raw_hum)
        return {
            "temperature_c": round(temperature, 2),
            "humidity_pct": round(humidity, 2),
            "pressure_hpa": round(pressure, 2),
        }
