"""
SGP40 - VOC (Volatile Organic Compound) Sensor
I2C Address: 0x59
Datasheet: https://files.waveshare.com/upload/c/c2/Sensirion_Gas-Sensors_SGP40_Datasheet.pdf

Measures VOC raw signal. The raw signal can be fed into Sensirion's
VOC Algorithm to obtain a VOC Index (0-500).

Note: The sensor requires ~1 minute warm-up for stable readings.
"""

import time
import struct

# Default I2C address
SGP40_ADDR = 0x59

# Commands (16-bit, MSB first)
_CMD_MEASURE_RAW = [0x26, 0x0F]
_CMD_MEASURE_TEST = [0x28, 0x0E]
_CMD_HEATER_OFF = [0x36, 0x15]
_CMD_SOFT_RESET = [0x00, 0x06]
_CMD_GET_SERIAL = [0x36, 0x82]

# Default humidity and temperature parameters for measurement
# These are encoded as fixed-point values with CRC
# Default: 50% RH = 0x8000, 25°C = 0x6666
_DEFAULT_RH = 0x8000
_DEFAULT_TEMP = 0x6666


def _crc8(data):
    """Calculate CRC-8 for SGP40 communication (polynomial 0x31, init 0xFF)."""
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x31
            else:
                crc = crc << 1
            crc &= 0xFF
    return crc


def _encode_param(value):
    """Encode a 16-bit parameter with CRC for SGP40."""
    msb = (value >> 8) & 0xFF
    lsb = value & 0xFF
    crc = _crc8([msb, lsb])
    return [msb, lsb, crc]


class SGP40:
    """Driver for the SGP40 VOC sensor."""

    def __init__(self, bus, address=SGP40_ADDR):
        self._bus = bus
        self._address = address
        self._init_sensor()

    def _init_sensor(self):
        """Initialize the sensor."""
        # Soft reset
        try:
            self._bus.write_i2c_block_data(
                self._address, _CMD_SOFT_RESET[0], [_CMD_SOFT_RESET[1]]
            )
        except OSError:
            pass  # Sensor may NAK during reset, which is expected
        time.sleep(0.01)

    def _measure_raw(self, humidity=_DEFAULT_RH, temperature=_DEFAULT_TEMP):
        """
        Send measure raw signal command with humidity and temperature
        compensation parameters.

        Returns raw VOC signal (uint16).
        """
        rh_param = _encode_param(humidity)
        temp_param = _encode_param(temperature)
        cmd = [_CMD_MEASURE_RAW[1]] + rh_param + temp_param

        self._bus.write_i2c_block_data(
            self._address, _CMD_MEASURE_RAW[0], cmd
        )

        # Measurement takes ~30ms
        time.sleep(0.035)

        # Read 3 bytes: MSB, LSB, CRC
        data = self._bus.read_i2c_block_data(self._address, 0x00, 3)
        raw = (data[0] << 8) | data[1]

        # Verify CRC
        expected_crc = _crc8([data[0], data[1]])
        if data[2] != expected_crc:
            raise RuntimeError(
                f"SGP40 CRC mismatch: got 0x{data[2]:02X}, "
                f"expected 0x{expected_crc:02X}"
            )

        return raw

    def read(self, humidity_pct=50.0, temperature_c=25.0):
        """
        Read VOC raw signal with optional temperature/humidity compensation.

        Args:
            humidity_pct:  Current relative humidity (%) for compensation
            temperature_c: Current temperature (°C) for compensation

        Returns:
            dict with keys:
                voc_raw   (int): Raw VOC signal (higher = more VOC)
                voc_index (int): Simple VOC index estimate (0 = clean, 500 = heavy)
        """
        # Convert humidity and temperature to SGP40 fixed-point format
        # RH: value = RH% * 65535 / 100
        # Temp: value = (T + 45) * 65535 / 175
        rh_fixed = int(max(0, min(100, humidity_pct)) * 65535 / 100)
        temp_fixed = int((max(-45, min(130, temperature_c)) + 45) * 65535 / 175)

        raw = self._measure_raw(rh_fixed, temp_fixed)

        # Simple VOC index estimate
        # The raw signal typically ranges from ~0 to ~60000
        # Lower raw values indicate more VOC, baseline is around 25000-35000
        # A proper VOC index requires Sensirion's algorithm library
        # This is a simplified linear mapping for basic indication
        baseline = 30000
        if raw >= baseline:
            voc_index = max(0, int((raw - baseline) / 100))
        else:
            voc_index = min(500, int((baseline - raw) / 60))

        return {
            "voc_raw": raw,
            "voc_index": voc_index,
        }
