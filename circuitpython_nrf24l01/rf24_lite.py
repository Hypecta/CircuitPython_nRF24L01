# see license and copyright information in rf24.py of this directory
# pylint: disable=missing-docstring
__version__ = "0.0.0-auto.0"
__repo__ = "https://github.com/2bndy5/CircuitPython_nRF24L01.git"
import time

try:
    from ubus_device import SPIDevice
except ImportError:
    from adafruit_bus_device.spi_device import SPIDevice

CSN_DELAY = 0.005


class RF24:
    def __init__(self, spi, csn, ce):
        self._spi = SPIDevice(spi, chip_select=csn, baudrate=10000000)
        self.ce_pin = ce
        self.ce_pin.switch_to_output(value=False)
        self._status = 0
        self._reg_write(0, 0x0E)
        if self._reg_read(0) & 3 != 2:
            raise RuntimeError("nRF24L01 Hardware not responding")
        self.power = False
        self._reg_write(3, 3)
        self._reg_write(6, 6)
        self._reg_write(2, 0)
        self._reg_write(0x1C, 0x3F)
        self._reg_write(1, 0x3F)
        self._reg_write(0x1D, 5)
        self._reg_write(4, 0x53)
        self._pipe0_read_addr = None
        self.channel = 76
        self.payload_length = 32
        self.flush_rx()
        self.flush_tx()
        self.clear_status_flags()

    # pylint: disable=no-member
    def _reg_read(self, reg):
        out_buf = bytearray([reg, 0])
        in_buf = bytearray([0, 0])
        with self._spi as spi:
            time.sleep(CSN_DELAY)
            spi.write_readinto(out_buf, in_buf)
        self._status = in_buf[0]
        return in_buf[1]

    def _reg_read_bytes(self, reg, buf_len=5):
        in_buf = bytearray(buf_len + 1)
        out_buf = bytearray([reg]) + b"\x00" * buf_len
        with self._spi as spi:
            time.sleep(CSN_DELAY)
            spi.write_readinto(out_buf, in_buf)
        self._status = in_buf[0]
        return in_buf[1:]

    def _reg_write_bytes(self, reg, out_buf):
        out_buf = bytes([0x20 | reg]) + out_buf
        in_buf = bytearray(len(out_buf))
        with self._spi as spi:
            time.sleep(CSN_DELAY)
            spi.write_readinto(out_buf, in_buf)
        self._status = in_buf[0]

    def _reg_write(self, reg, value=None):
        out_buf = bytes(0)
        if value is None:
            out_buf = bytes([reg])
        else:
            out_buf = bytes([0x20 | reg, value])
        in_buf = bytearray(len(out_buf))
        with self._spi as spi:
            time.sleep(CSN_DELAY)
            spi.write_readinto(out_buf, in_buf)
        self._status = in_buf[0]

    # pylint: enable=no-member
    @property
    def address_length(self):
        return self._reg_read(0x03) + 2

    @address_length.setter
    def address_length(self, length):
        if not 3 <= length <= 5:
            raise ValueError("address_length can only be set in range [3, 5] bytes")
        self._reg_write(0x03, length - 2)

    def open_tx_pipe(self, address):
        if self.arc:
            self._reg_write_bytes(0x0A, address)
            self._reg_write(2, self._reg_read(2) | 1)
        self._reg_write_bytes(0x10, address)

    def close_rx_pipe(self, pipe_number):
        if pipe_number < 0 or pipe_number > 5:
            raise ValueError("pipe number must be in range [0, 5]")
        open_pipes = self._reg_read(2)
        if open_pipes & (1 << pipe_number):
            self._reg_write(2, open_pipes & ~(1 << pipe_number))

    def open_rx_pipe(self, pipe_number, address):
        if not 0 <= pipe_number <= 5:
            raise ValueError("pipe_number must be in range [0, 5]")
        if not address:
            raise ValueError("address length cannot be 0")
        if pipe_number < 2:
            if not pipe_number:
                self._pipe0_read_addr = address
            self._reg_write_bytes(0x0A + pipe_number, address)
        else:
            self._reg_write(0x0A + pipe_number, address[0])
        self._reg_write(2, self._reg_read(2) | (1 << pipe_number))

    @property
    def listen(self):
        return self._reg_read(0) & 3 == 3

    @listen.setter
    def listen(self, is_rx):
        if self.listen != bool(is_rx):
            self.ce_pin.value = 0
            if is_rx:
                if self._pipe0_read_addr is not None:
                    self._reg_write_bytes(0x0A, self._pipe0_read_addr)
                self._reg_write(0, (self._reg_read(0) & 0xFC) | 3)
                time.sleep(0.00015)  # mandatory wait to power up radio
                self.flush_rx()
                self.clear_status_flags(True, False, False)
                self.ce_pin.value = 1  # mandatory pulse is > 130 µs
                time.sleep(0.00013)
            else:
                self._reg_write(0, self._reg_read(0) & 0xFE)
                time.sleep(0.00016)

    def any(self):
        if self._reg_read(0x1D) & 4 and self.pipe is not None:
            return self._reg_read(0x60)
        if self.pipe is not None:
            return self._reg_read(0x11 + self.pipe)
        return 0

    def recv(self):
        curr_pl_size = self.any()
        if not curr_pl_size:
            return None
        result = self._reg_read_bytes(0x61, curr_pl_size)
        self.clear_status_flags(True, False, False)
        return result

    def send(self, buf, ask_no_ack=False, force_retry=0):
        self.ce_pin.value = 0
        if isinstance(buf, (list, tuple)):
            result = []
            for b in buf:
                result.append(self.send(b, ask_no_ack, force_retry))
            return result
        self.flush_tx()
        if self.pipe is not None:
            self.flush_rx()
        self.write(buf, ask_no_ack)
        time.sleep(0.00001)
        self.ce_pin.value = 0
        while not self._status & 0x30:
            self.update()
        result = self.irq_ds
        if self.irq_df:
            for _ in range(force_retry):
                result = self.resend()
                if result is None or result:
                    break
        if self._status & 0x60 == 0x60:
            result = self.recv()
        self.clear_status_flags(False)
        return result

    @property
    def tx_full(self):
        return bool(self._status & 1)

    @property
    def pipe(self):
        result = (self._status & 0x0E) >> 1
        if result <= 5:
            return result
        return None

    @property
    def irq_dr(self):
        return bool(self._status & 0x40)

    @property
    def irq_ds(self):
        return bool(self._status & 0x20)

    @property
    def irq_df(self):
        return bool(self._status & 0x10)

    def clear_status_flags(self, data_recv=True, data_sent=True, data_fail=True):
        config = bool(data_recv) << 6 | bool(data_sent) << 5 | bool(data_fail) << 4
        self._reg_write(7, config)

    def interrupt_config(self, data_recv=True, data_sent=True, data_fail=True):
        config = (not data_recv) << 6 | (not data_fail) << 4 | (not data_sent) << 5
        self._reg_write(0, (self._reg_read(0) & 0x0F) | config)

    @property
    def dynamic_payloads(self):
        return bool(self._reg_read(0x1C)) and self._reg_read(0x1D) & 4 == 4

    @dynamic_payloads.setter
    def dynamic_payloads(self, enable):
        self._reg_write(0x1D, (self._reg_read(0x1D) & 3) | bool(enable) << 2)
        self._reg_write(0x1C, 0x3F if enable else 0)

    @property
    def payload_length(self):
        return self._reg_read(0x11)

    @payload_length.setter
    def payload_length(self, length):
        if not length or length > 32:
            raise ValueError("payload_length must be in range [1, 32] bytes")
        for i in range(6):
            self._reg_write(0x11 + i, length)

    @property
    def arc(self):
        return self._reg_read(4) & 0x0F

    @arc.setter
    def arc(self, count):
        if not 0 <= count <= 15:
            raise ValueError("arc must in range [0, 15]")
        self._reg_write(4, (self._reg_read(4) & 0xF0) | count)

    @property
    def ard(self):
        return ((self._reg_read(4) & 0xF0) >> 4) * 250 + 250

    @ard.setter
    def ard(self, delta):
        if not 250 <= delta <= 4000:
            raise ValueError("ard must be in range [250, 4000]")
        self._reg_write(4, (self._reg_read(4) & 0x0F) | int((delta - 250) / 250) << 4)

    @property
    def ack(self):
        return self._reg_read(0x1D) & 6 == 6 and bool(
            self._reg_read(1) & self._reg_read(0x1C)
        )

    @ack.setter
    def ack(self, enable):
        features = self._reg_read(0x1D) & 5
        if enable:
            self._reg_write(1, 0x3F)
            self._reg_write(0x1C, 0x3F)
            features = (features & 3) | 4
        features |= 2 if enable else 0
        self._reg_write(0x1D, features)

    def load_ack(self, buf, pipe_number):
        if 0 <= pipe_number <= 5 and (not buf or len(buf) > 32):
            if not self._reg_read(0x1D) & 2:
                self.ack = True
            if not self.tx_full:
                self._reg_write_bytes(0xA8 | pipe_number, buf)
                return True
        return False

    @property
    def data_rate(self):
        rf_setup = self._reg_read(6) & 0x28
        return (2 if rf_setup == 8 else 250) if rf_setup else 1

    @data_rate.setter
    def data_rate(self, speed):
        speed = 0 if speed == 1 else (0x20 if speed != 2 else 8)
        self._reg_write(6, (self._reg_read(6) & 0xD7) | speed)

    @property
    def channel(self):
        return self._reg_read(5)

    @channel.setter
    def channel(self, channel):
        if not 0 <= int(channel) <= 125:
            raise ValueError("channel can only be set in range [0, 125]")
        self._reg_write(5, int(channel))

    @property
    def power(self):
        return bool(self._reg_read(0) & 2)

    @power.setter
    def power(self, is_on):
        config = self._reg_read(0)
        if bool(config & 2) != bool(is_on):
            config = config & 0x7D | bool(is_on) << 1
            self._reg_write(0, config)
            time.sleep(0.00016)

    @property
    def pa_level(self):
        return (3 - ((self._reg_read(6) & 6) >> 1)) * -6

    @pa_level.setter
    def pa_level(self, power):
        if not power in (-18, -12, -6, 0):
            raise ValueError("pa_level must be -18, -12, -6, or 0")
        self._reg_write(6, (self._reg_read(6) & 0xF9) | ((3 - int(power / -6)) * 2))

    def update(self):
        self._reg_write(0xFF)

    def resend(self):
        result = False
        if not self._reg_read(0x17) & 0x10:
            if self.pipe is not None:
                self.flush_rx()
            self.clear_status_flags()
            self._reg_write(0xE3)
            self.ce_pin.value = 0
            self.ce_pin.value = 1
            time.sleep(0.00001)
            self.ce_pin.value = 0
            while not self._status & 0x30:
                self.update()
            result = self.irq_ds
            if self._status & 0x60 == 0x60:
                result = self.recv()
            self.clear_status_flags(False)
        return result

    def write(self, buf, ask_no_ack=False):
        if not buf or len(buf) > 32:
            raise ValueError("buffer must have a length in range [1, 32]")
        self.clear_status_flags()
        config = self._reg_read(0)
        if config & 3 != 2:
            self._reg_write(0, (config & 0x7C) | 2)
            time.sleep(0.00016)
        if not self.dynamic_payloads:
            pl_width = self.payload_length
            if len(buf) < pl_width:
                for _ in range(pl_width - len(buf)):
                    buf += b"\x00"
            elif len(buf) > pl_width:
                buf = buf[:pl_width]
        self._reg_write_bytes(0xA0 | (ask_no_ack << 4), buf)
        self.ce_pin.value = 1

    def flush_rx(self):
        self._reg_write(0xE2)

    def flush_tx(self):
        self._reg_write(0xE1)

    @property
    def rpd(self):
        return bool(self._reg_read(0x09))

    def start_carrier_wave(self):
        self.power = 0
        self.ce_pin.value = 0
        self.listen = 0
        self._reg_write(6, self._reg_read(6) | 0x90)
        self.power = 1
        self.ce_pin.value = 1
        time.sleep(0.00028)

    def stop_carrier_wave(self):
        self.ce_pin.value = 0
        self.power = 0
        self._reg_write(6, self._reg_read(6) & ~0x90)
