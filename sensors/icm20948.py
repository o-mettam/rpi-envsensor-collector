"""
ICM20948 - 9-Axis Motion Sensor (Accelerometer, Gyroscope, Magnetometer)
I2C Address: 0x68
Datasheet: https://files.waveshare.com/upload/5/57/ICM-20948-v1.3.pdf

Measures acceleration, angular velocity, and magnetic field.
"""

import time
import struct

# Default I2C address
ICM20948_ADDR = 0x68

# --- User Bank 0 Registers ---
_WHO_AM_I = 0x00
_USER_CTRL = 0x03
_LP_CONFIG = 0x05
_PWR_MGMT_1 = 0x06
_PWR_MGMT_2 = 0x07
_INT_PIN_CFG = 0x0F
_INT_ENABLE = 0x10
_INT_STATUS = 0x19

_ACCEL_XOUT_H = 0x2D
_ACCEL_XOUT_L = 0x2E
_ACCEL_YOUT_H = 0x2F
_ACCEL_YOUT_L = 0x30
_ACCEL_ZOUT_H = 0x31
_ACCEL_ZOUT_L = 0x32
_GYRO_XOUT_H = 0x33
_GYRO_XOUT_L = 0x34
_GYRO_YOUT_H = 0x35
_GYRO_YOUT_L = 0x36
_GYRO_ZOUT_H = 0x37
_GYRO_ZOUT_L = 0x38
_TEMP_OUT_H = 0x39
_TEMP_OUT_L = 0x3A

_EXT_SLV_SENS_DATA_00 = 0x3B

# --- I2C Master Registers (for magnetometer access) ---
_I2C_MST_CTRL = 0x01       # Bank 3
_I2C_SLV0_ADDR = 0x03      # Bank 3
_I2C_SLV0_REG = 0x04       # Bank 3
_I2C_SLV0_CTRL = 0x05      # Bank 3

# --- Bank select ---
_REG_BANK_SEL = 0x7F

# --- Magnetometer (AK09916) registers ---
_AK09916_ADDR = 0x0C
_AK09916_WIA2 = 0x01       # Device ID (should be 0x09)
_AK09916_ST1 = 0x10        # Status 1
_AK09916_HXL = 0x11        # Measurement data
_AK09916_ST2 = 0x18        # Status 2
_AK09916_CNTL2 = 0x31      # Control 2
_AK09916_CNTL3 = 0x32      # Control 3 (reset)

EXPECTED_WHO_AM_I = 0xEA

# Accelerometer full-scale range
ACCEL_FS_2G = 0x00
ACCEL_FS_4G = 0x02
ACCEL_FS_8G = 0x04
ACCEL_FS_16G = 0x06

# Gyroscope full-scale range
GYRO_FS_250DPS = 0x00
GYRO_FS_500DPS = 0x02
GYRO_FS_1000DPS = 0x04
GYRO_FS_2000DPS = 0x06


class ICM20948:
    """Driver for the ICM20948 9-axis motion sensor."""

    def __init__(self, bus, address=ICM20948_ADDR):
        self._bus = bus
        self._address = address
        self._accel_scale = 16384.0  # ±2g default
        self._gyro_scale = 131.0     # ±250dps default
        self._bank = -1
        self._init_sensor()

    def _select_bank(self, bank):
        """Select register bank (0-3)."""
        if self._bank != bank:
            self._bus.write_byte_data(
                self._address, _REG_BANK_SEL, bank << 4
            )
            self._bank = bank

    def _read_reg(self, bank, register):
        self._select_bank(bank)
        return self._bus.read_byte_data(self._address, register)

    def _write_reg(self, bank, register, value):
        self._select_bank(bank)
        self._bus.write_byte_data(self._address, register, value)

    def _read_block(self, bank, register, length):
        self._select_bank(bank)
        return self._bus.read_i2c_block_data(self._address, register, length)

    def _init_sensor(self):
        """Initialize the sensor."""
        # Check WHO_AM_I
        who = self._read_reg(0, _WHO_AM_I)
        if who != EXPECTED_WHO_AM_I:
            raise RuntimeError(
                f"ICM20948 not found at 0x{self._address:02X}. "
                f"WHO_AM_I: 0x{who:02X} (expected 0x{EXPECTED_WHO_AM_I:02X})"
            )

        # Reset
        self._write_reg(0, _PWR_MGMT_1, 0x80)
        time.sleep(0.1)

        # Wake up (auto-select best clock)
        self._write_reg(0, _PWR_MGMT_1, 0x01)
        time.sleep(0.05)

        # Enable all accel and gyro axes
        self._write_reg(0, _PWR_MGMT_2, 0x00)

        # Configure accelerometer: ±2g, DLPF enabled
        self._write_reg(2, 0x14, ACCEL_FS_2G | 0x01)  # ACCEL_CONFIG
        self._accel_scale = 16384.0

        # Configure gyroscope: ±250dps, DLPF enabled
        self._write_reg(2, 0x01, GYRO_FS_250DPS | 0x01)  # GYRO_CONFIG_1
        self._gyro_scale = 131.0

        # Set up I2C master for magnetometer
        self._setup_magnetometer()

    def _setup_magnetometer(self):
        """Configure the ICM20948 I2C master to read the AK09916 magnetometer."""
        # Enable I2C master
        self._write_reg(0, _USER_CTRL, 0x20)

        # I2C master clock = 400 kHz
        self._write_reg(3, _I2C_MST_CTRL, 0x07)

        # Reset magnetometer
        self._write_mag(0x32, 0x01)
        time.sleep(0.1)

        # Set magnetometer to continuous measurement mode 4 (100 Hz)
        self._write_mag(0x31, 0x08)
        time.sleep(0.01)

        # Configure SLV0 to read 8 bytes from magnetometer starting at ST1
        self._write_reg(3, _I2C_SLV0_ADDR, _AK09916_ADDR | 0x80)  # Read
        self._write_reg(3, _I2C_SLV0_REG, _AK09916_ST1)
        self._write_reg(3, _I2C_SLV0_CTRL, 0x89)  # Enable, 9 bytes

    def _write_mag(self, register, value):
        """Write to magnetometer via I2C master."""
        self._write_reg(3, _I2C_SLV0_ADDR, _AK09916_ADDR)  # Write mode
        self._write_reg(3, _I2C_SLV0_REG, register)
        self._write_reg(3, 0x06, value)  # I2C_SLV0_DO
        self._write_reg(3, _I2C_SLV0_CTRL, 0x81)  # Enable, 1 byte
        time.sleep(0.01)

    @staticmethod
    def _to_signed_16(msb, lsb):
        """Convert two bytes to signed 16-bit integer."""
        val = (msb << 8) | lsb
        if val > 32767:
            val -= 65536
        return val

    def read(self):
        """
        Read accelerometer, gyroscope, magnetometer, and temperature.

        Returns:
            dict with keys:
                accel_x, accel_y, accel_z (float): Acceleration in g
                gyro_x, gyro_y, gyro_z   (float): Angular velocity in °/s
                mag_x, mag_y, mag_z      (float): Magnetic field in µT
                temperature_c            (float): Die temperature in °C
        """
        # Read accel, gyro, temp (14 bytes starting at ACCEL_XOUT_H)
        data = self._read_block(0, _ACCEL_XOUT_H, 14)

        accel_x = self._to_signed_16(data[0], data[1]) / self._accel_scale
        accel_y = self._to_signed_16(data[2], data[3]) / self._accel_scale
        accel_z = self._to_signed_16(data[4], data[5]) / self._accel_scale

        gyro_x = self._to_signed_16(data[6], data[7]) / self._gyro_scale
        gyro_y = self._to_signed_16(data[8], data[9]) / self._gyro_scale
        gyro_z = self._to_signed_16(data[10], data[11]) / self._gyro_scale

        temp = self._to_signed_16(data[12], data[13])
        temperature_c = (temp - 21.0) / 333.87 + 21.0

        # Read magnetometer data from EXT_SLV_SENS_DATA (9 bytes)
        mag_data = self._read_block(0, _EXT_SLV_SENS_DATA_00, 9)
        # mag_data[0] = ST1 status
        # mag_data[1..6] = HXL, HXH, HYL, HYH, HZL, HZH
        # mag_data[7] = dummy, mag_data[8] = ST2

        mag_x = self._to_signed_16(mag_data[2], mag_data[1]) * 0.15  # µT
        mag_y = self._to_signed_16(mag_data[4], mag_data[3]) * 0.15
        mag_z = self._to_signed_16(mag_data[6], mag_data[5]) * 0.15

        return {
            "accel_x_g": round(accel_x, 4),
            "accel_y_g": round(accel_y, 4),
            "accel_z_g": round(accel_z, 4),
            "gyro_x_dps": round(gyro_x, 2),
            "gyro_y_dps": round(gyro_y, 2),
            "gyro_z_dps": round(gyro_z, 2),
            "mag_x_ut": round(mag_x, 2),
            "mag_y_ut": round(mag_y, 2),
            "mag_z_ut": round(mag_z, 2),
            "temperature_c": round(temperature_c, 2),
        }
