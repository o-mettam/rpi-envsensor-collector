"""
LTR390-UV-01 - UV and Ambient Light Sensor
I2C Address: 0x53
Datasheet: https://files.waveshare.com/upload/a/ac/1_LTR-390UV_Final_DS_V1_2.pdf

Measures UV index and ambient light intensity.
"""

import time

# Default I2C address
LTR390_ADDR = 0x53

# Registers
_MAIN_CTRL = 0x00
_MEAS_RATE = 0x04
_GAIN = 0x05
_PART_ID = 0x06
_MAIN_STATUS = 0x07
_ALS_DATA_0 = 0x0D
_ALS_DATA_1 = 0x0E
_ALS_DATA_2 = 0x0F
_UVS_DATA_0 = 0x10
_UVS_DATA_1 = 0x11
_UVS_DATA_2 = 0x12
_INT_CFG = 0x19
_INT_PST = 0x1A
_THRESH_UP_0 = 0x21
_THRESH_UP_1 = 0x22
_THRESH_UP_2 = 0x23
_THRESH_LOW_0 = 0x24
_THRESH_LOW_1 = 0x25
_THRESH_LOW_2 = 0x26

# Main control modes
_MODE_ALS = 0x02  # ALS mode, active
_MODE_UVS = 0x0A  # UVS mode, active

# Gain values
GAIN_1X = 0x00
GAIN_3X = 0x01
GAIN_6X = 0x02
GAIN_9X = 0x03
GAIN_18X = 0x04

# Resolution / measurement rate
RES_20BIT_400MS = 0x00
RES_19BIT_200MS = 0x10
RES_18BIT_100MS = 0x20  # default
RES_17BIT_50MS = 0x30
RES_16BIT_25MS = 0x40
RES_13BIT_12_5MS = 0x50

# UV sensitivity coefficient (typical, per datasheet)
# UVI = UVS_DATA * WFAC / (GAIN * INT_TIME)
# WFAC depends on the window/cover factor (1.0 for open air)
_WFAC = 1.0

EXPECTED_PART_ID = 0xB2


class LTR390:
    """Driver for the LTR390 UV and ambient light sensor."""

    def __init__(self, bus, address=LTR390_ADDR,
                 gain=GAIN_3X, resolution=RES_18BIT_100MS):
        self._bus = bus
        self._address = address
        self._gain = gain
        self._resolution = resolution
        self._init_sensor()

    def _init_sensor(self):
        """Initialize and configure the sensor."""
        part_id = self._bus.read_byte_data(self._address, _PART_ID)
        if (part_id >> 4) != 0x0B:
            raise RuntimeError(
                f"LTR390 not found at 0x{self._address:02X}. "
                f"Part ID: 0x{part_id:02X}"
            )

        # Set gain
        self._bus.write_byte_data(self._address, _GAIN, self._gain)
        # Set resolution / measurement rate
        self._bus.write_byte_data(self._address, _MEAS_RATE, self._resolution)

    def _get_gain_factor(self):
        factors = {
            GAIN_1X: 1, GAIN_3X: 3, GAIN_6X: 6,
            GAIN_9X: 9, GAIN_18X: 18
        }
        return factors.get(self._gain, 1)

    def _get_integration_factor(self):
        """Return integration time factor for UV index calculation."""
        factors = {
            RES_20BIT_400MS: 4.0,
            RES_19BIT_200MS: 2.0,
            RES_18BIT_100MS: 1.0,
            RES_17BIT_50MS: 0.5,
            RES_16BIT_25MS: 0.25,
            RES_13BIT_12_5MS: 0.125,
        }
        return factors.get(self._resolution, 1.0)

    def _read_als(self):
        """Read ambient light data (20-bit)."""
        # Switch to ALS mode
        self._bus.write_byte_data(self._address, _MAIN_CTRL, _MODE_ALS)
        time.sleep(0.15)  # Wait for measurement

        d0 = self._bus.read_byte_data(self._address, _ALS_DATA_0)
        d1 = self._bus.read_byte_data(self._address, _ALS_DATA_1)
        d2 = self._bus.read_byte_data(self._address, _ALS_DATA_2)
        return (d2 << 16) | (d1 << 8) | d0

    def _read_uvs(self):
        """Read UV sensor data (20-bit)."""
        # Switch to UVS mode
        self._bus.write_byte_data(self._address, _MAIN_CTRL, _MODE_UVS)
        time.sleep(0.15)  # Wait for measurement

        d0 = self._bus.read_byte_data(self._address, _UVS_DATA_0)
        d1 = self._bus.read_byte_data(self._address, _UVS_DATA_1)
        d2 = self._bus.read_byte_data(self._address, _UVS_DATA_2)
        return (d2 << 16) | (d1 << 8) | d0

    def read(self):
        """
        Read UV and ambient light data.

        Returns:
            dict with keys:
                uv_raw   (int):   Raw UVS sensor reading
                uv_index (float): Estimated UV Index
                als_raw  (int):   Raw ALS sensor reading
                lux      (float): Estimated lux from ALS
        """
        als_raw = self._read_als()
        uvs_raw = self._read_uvs()

        gain = self._get_gain_factor()
        int_factor = self._get_integration_factor()

        # UV Index estimation (per datasheet application note)
        # UVI = UVS / (gain * int_time) * sensitivity_factor
        # The sensitivity factor of 2300 counts per UVI at gain=18x, 100ms
        sensitivity = 2300.0
        if gain > 0 and int_factor > 0:
            uv_index = uvs_raw / (gain * int_factor) * (18.0 / sensitivity)
        else:
            uv_index = 0.0

        # Lux estimation from ALS data
        # Lux = 0.6 * ALS / (gain * int_factor)
        if gain > 0 and int_factor > 0:
            lux = 0.6 * als_raw / (gain * int_factor)
        else:
            lux = 0.0

        return {
            "uv_raw": uvs_raw,
            "uv_index": round(uv_index, 2),
            "als_raw": als_raw,
            "als_lux": round(lux, 2),
        }
