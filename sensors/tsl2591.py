"""
TSL2591 - Digital Ambient Light Sensor
I2C Address: 0x29
Datasheet: https://files.waveshare.com/upload/3/31/TSL2591.pdf

Measures visible and infrared light, calculates lux.
"""

import time

# Default I2C address
TSL2591_ADDR = 0x29

# Command bit (must be set for all register accesses)
_COMMAND_BIT = 0xA0
_NORMAL_OP = 0x20

# Register addresses
_ENABLE_REG = 0x00
_CONTROL_REG = 0x01
_AILTL_REG = 0x04
_AILTH_REG = 0x05
_AIHTL_REG = 0x06
_AIHTH_REG = 0x07
_NPAILTL_REG = 0x08
_NPAILTH_REG = 0x09
_NPAIHTL_REG = 0x0A
_NPAIHTH_REG = 0x0B
_PERSIST_REG = 0x0C
_PID_REG = 0x11
_ID_REG = 0x12
_STATUS_REG = 0x13
_C0DATAL_REG = 0x14
_C0DATAH_REG = 0x15
_C1DATAL_REG = 0x16
_C1DATAH_REG = 0x17

# Enable register bits
_ENABLE_PON = 0x01  # Power on
_ENABLE_AEN = 0x02  # ALS enable
_ENABLE_AIEN = 0x10  # ALS interrupt enable
_ENABLE_NPIEN = 0x80  # No-persist interrupt enable

# Gain values
GAIN_LOW = 0x00   # 1x
GAIN_MED = 0x10   # 25x
GAIN_HIGH = 0x20  # 428x
GAIN_MAX = 0x30   # 9876x

# Integration times
INTEGTIME_100MS = 0x00
INTEGTIME_200MS = 0x01
INTEGTIME_300MS = 0x02
INTEGTIME_400MS = 0x03
INTEGTIME_500MS = 0x04
INTEGTIME_600MS = 0x05

# Lux coefficient (from datasheet application note)
_LUX_DF = 408.0
_LUX_COEFB = 1.64
_LUX_COEFC = 0.59
_LUX_COEFD = 0.86


class TSL2591:
    """Driver for the TSL2591 ambient light sensor."""

    def __init__(self, bus, address=TSL2591_ADDR,
                 gain=GAIN_MED, integration_time=INTEGTIME_300MS):
        self._bus = bus
        self._address = address
        self._gain = gain
        self._integration_time = integration_time
        self._init_sensor()

    def _write_reg(self, register, value):
        self._bus.write_byte_data(
            self._address, _COMMAND_BIT | _NORMAL_OP | register, value
        )

    def _read_reg(self, register):
        return self._bus.read_byte_data(
            self._address, _COMMAND_BIT | _NORMAL_OP | register
        )

    def _init_sensor(self):
        """Initialize and configure the sensor."""
        dev_id = self._read_reg(_ID_REG)
        if dev_id != 0x50:
            raise RuntimeError(
                f"TSL2591 not found at 0x{self._address:02X}. "
                f"ID: 0x{dev_id:02X} (expected 0x50)"
            )

        # Power on and enable ALS
        self._write_reg(_ENABLE_REG, _ENABLE_PON | _ENABLE_AEN)
        # Set gain and integration time
        self._write_reg(_CONTROL_REG, self._gain | self._integration_time)

    def _get_gain_factor(self):
        """Return the actual gain multiplier."""
        g = self._gain
        if g == GAIN_LOW:
            return 1.0
        elif g == GAIN_MED:
            return 25.0
        elif g == GAIN_HIGH:
            return 428.0
        elif g == GAIN_MAX:
            return 9876.0
        return 1.0

    def _get_integration_ms(self):
        """Return integration time in milliseconds."""
        return (self._integration_time + 1) * 100.0

    def _read_raw_channels(self):
        """Read raw channel 0 (full spectrum) and channel 1 (IR)."""
        c0_low = self._read_reg(_C0DATAL_REG)
        c0_high = self._read_reg(_C0DATAH_REG)
        c1_low = self._read_reg(_C1DATAL_REG)
        c1_high = self._read_reg(_C1DATAH_REG)
        channel0 = (c0_high << 8) | c0_low
        channel1 = (c1_high << 8) | c1_low
        return channel0, channel1

    def read(self):
        """
        Read light levels from the sensor.

        Returns:
            dict with keys:
                lux      (float): Calculated lux value
                visible  (int):   Visible light raw value
                infrared (int):   Infrared raw value
                full_spectrum (int): Full spectrum raw value (visible + IR)
        """
        full, ir = self._read_raw_channels()

        # Check for overflow
        if full == 0xFFFF or ir == 0xFFFF:
            return {
                "lux": -1.0,
                "visible": 0,
                "infrared": 0,
                "full_spectrum": 0,
            }

        visible = full - ir

        atime = self._get_integration_ms()
        again = self._get_gain_factor()

        # Lux calculation (from datasheet / Adafruit reference)
        if full == 0:
            lux = 0.0
        else:
            cpl = (atime * again) / _LUX_DF
            lux1 = (full - (_LUX_COEFB * ir)) / cpl
            lux2 = ((_LUX_COEFC * full) - (_LUX_COEFD * ir)) / cpl
            lux = max(lux1, lux2, 0.0)

        return {
            "lux": round(lux, 2),
            "visible": visible,
            "infrared": ir,
            "full_spectrum": full,
        }
