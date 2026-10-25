# LIS2DW on the Creality K2 toolhead, for mainline Klipper
#
# The K2 nozzle MCU runs Creality's Klipper build, which speaks the 2023
# lis2dw protocol: config_lis2dw takes spi_oid, query_lis2dw carries a
# start clock, and samples arrive as lis2dw_data blocks with their own
# status reply. Mainline's lis2dw.py speaks the newer bus_oid /
# sensor_bulk_data protocol, so this module drives the old commands and
# hands samples to mainline's accelerometer helpers.
#
# Protocol and sample decoding follow Creality's stock lis2dw.py
# (Copyright (C) 2023 Zhou.XianMing, 2020-2023 Kevin O'Connor).
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, threading
from . import bus, adxl345, bulk_sensor

REG_LIS2DW_WHO_AM_I_ADDR = 0x0F
REG_LIS2DW_CTRL_REG1_ADDR = 0x20
REG_LIS2DW_CTRL_REG6_ADDR = 0x25
REG_LIS2DW_FIFO_CTRL = 0x2E
REG_MOD_READ = 0x80

LIS2DW_DEV_ID = 0x44

FREEFALL_ACCEL = 9.80665
SCALE = FREEFALL_ACCEL * 1.952 / 4

MIN_MSG_TIME = 0.100
BATCH_UPDATES = 0.100

BYTES_PER_SAMPLE = 6
SAMPLES_PER_BLOCK = 8

class LIS2DW_K2:
    def __init__(self, config):
        self.printer = config.get_printer()
        adxl345.AccelCommandHelper(config, self)
        self.axes_map = adxl345.read_axes_map(config, SCALE, SCALE, SCALE)
        self.data_rate = 1600
        self.lock = threading.Lock()
        self.raw_samples = []
        self.spi = bus.MCU_SPI_from_config(config, 3, default_speed=5000000)
        self.mcu = mcu = self.spi.get_mcu()
        self.oid = oid = mcu.create_oid()
        self.query_lis2dw_cmd = self.query_lis2dw_end_cmd = None
        self.query_lis2dw_status_cmd = None
        mcu.add_config_cmd("config_lis2dw oid=%d spi_oid=%d"
                           % (oid, self.spi.get_oid()))
        mcu.add_config_cmd("query_lis2dw oid=%d clock=0 rest_ticks=0"
                           % (oid,), on_restart=True)
        mcu.register_config_callback(self._build_config)
        mcu.register_serial_response(
            self._handle_lis2dw_data,
            "lis2dw_data oid=%c sequence=%hu data=%*s", oid)
        self.last_sequence = self.max_query_duration = 0
        self.last_limit_count = self.last_error_count = 0
        self.clock_sync = bulk_sensor.ClockSyncRegression(self.mcu, 640)
        self.batch_bulk = bulk_sensor.BatchBulkHelper(
            self.printer, self._process_batch,
            self._start_measurements, self._finish_measurements,
            BATCH_UPDATES)
        self.name = config.get_name().split()[-1]
        hdr = ('time', 'x_acceleration', 'y_acceleration', 'z_acceleration')
        self.batch_bulk.add_mux_endpoint("lis2dw/dump_lis2dw", "sensor",
                                         self.name, {'header': hdr})
    def _build_config(self):
        cmdqueue = self.spi.get_command_queue()
        self.query_lis2dw_cmd = self.mcu.lookup_command(
            "query_lis2dw oid=%c clock=%u rest_ticks=%u", cq=cmdqueue)
        self.query_lis2dw_end_cmd = self.mcu.lookup_query_command(
            "query_lis2dw oid=%c clock=%u rest_ticks=%u",
            "lis2dw_status oid=%c clock=%u query_ticks=%u next_sequence=%hu"
            " buffered=%c fifo=%c limit_count=%hu", oid=self.oid, cq=cmdqueue)
        self.query_lis2dw_status_cmd = self.mcu.lookup_query_command(
            "query_lis2dw_status oid=%c",
            "lis2dw_status oid=%c clock=%u query_ticks=%u next_sequence=%hu"
            " buffered=%c fifo=%c limit_count=%hu", oid=self.oid, cq=cmdqueue)
    def read_reg(self, reg):
        params = self.spi.spi_transfer([reg | REG_MOD_READ, 0x00])
        return bytearray(params['response'])[1]
    def set_reg(self, reg, val, minclock=0):
        self.spi.spi_send([reg, val & 0xFF], minclock=minclock)
        stored_val = self.read_reg(reg)
        if stored_val != val:
            raise self.printer.command_error(
                "Failed to set LIS2DW register [0x%x] to 0x%x: got 0x%x"
                % (reg, val, stored_val))
    def start_internal_client(self):
        aqh = adxl345.AccelQueryHelper(self.printer)
        self.batch_bulk.add_client(aqh.handle_batch)
        return aqh
    # Measurement collection
    def _handle_lis2dw_data(self, params):
        with self.lock:
            self.raw_samples.append(params)
    def _extract_samples(self, raw_samples):
        (x_pos, x_scale), (y_pos, y_scale), (z_pos, z_scale) = self.axes_map
        last_sequence = self.last_sequence
        time_base, chip_base, inv_freq = self.clock_sync.get_time_translation()
        count = seq = i = 0
        samples = [None] * (len(raw_samples) * SAMPLES_PER_BLOCK)
        for params in raw_samples:
            seq_diff = (last_sequence - params['sequence']) & 0xffff
            seq_diff -= (seq_diff & 0x8000) << 1
            seq = last_sequence - seq_diff
            d = bytearray(params['data'])
            msg_cdiff = seq * SAMPLES_PER_BLOCK - chip_base
            for i in range(len(d) // BYTES_PER_SAMPLE):
                xlow, xhigh, ylow, yhigh, zlow, zhigh = \
                    d[i*BYTES_PER_SAMPLE:(i+1)*BYTES_PER_SAMPLE]
                rx = ((xhigh << 8) | xlow) - ((xhigh & 0x80) << 9)
                ry = ((yhigh << 8) | ylow) - ((yhigh & 0x80) << 9)
                rz = ((zhigh << 8) | zlow) - ((zhigh & 0x80) << 9)
                raw_xyz = (rx, ry, rz)
                x = round(raw_xyz[x_pos] * x_scale, 6)
                y = round(raw_xyz[y_pos] * y_scale, 6)
                z = round(raw_xyz[z_pos] * z_scale, 6)
                ptime = round(time_base + (msg_cdiff + i) * inv_freq, 6)
                samples[count] = (ptime, x, y, z)
                count += 1
        self.clock_sync.set_last_chip_clock(seq * SAMPLES_PER_BLOCK + i)
        del samples[count:]
        return samples
    def _update_clock(self, minclock=0):
        for retry in range(5):
            params = self.query_lis2dw_status_cmd.send([self.oid],
                                                       minclock=minclock)
            fifo = params['fifo'] & 0x1f
            if fifo <= 32:
                break
        else:
            raise self.printer.command_error("Unable to query lis2dw fifo")
        mcu_clock = self.mcu.clock32_to_clock64(params['clock'])
        sequence = (self.last_sequence & ~0xffff) | params['next_sequence']
        if sequence < self.last_sequence:
            sequence += 0x10000
        self.last_sequence = sequence
        buffered = params['buffered']
        limit_count = (self.last_limit_count & ~0xffff) | params['limit_count']
        if limit_count < self.last_limit_count:
            limit_count += 0x10000
        self.last_limit_count = limit_count
        duration = params['query_ticks']
        if duration > self.max_query_duration:
            # A slow query could skew clock tracking; skip it
            self.max_query_duration = max(2 * self.max_query_duration,
                                          self.mcu.seconds_to_clock(.000005))
            return
        self.max_query_duration = 2 * duration
        msg_count = (sequence * SAMPLES_PER_BLOCK
                     + buffered // BYTES_PER_SAMPLE + fifo)
        chip_clock = msg_count + 1
        self.clock_sync.update(mcu_clock + duration // 2, chip_clock)
    def _start_measurements(self):
        dev_id = self.read_reg(REG_LIS2DW_WHO_AM_I_ADDR)
        logging.info("lis2dw_k2 dev_id: %x", dev_id)
        if dev_id != LIS2DW_DEV_ID:
            raise self.printer.command_error(
                "Invalid lis2dw id (got %x vs %x)" % (dev_id, LIS2DW_DEV_ID))
        # ODR/2, +-16g, low-pass filter, low noise
        self.set_reg(REG_LIS2DW_CTRL_REG6_ADDR, 0x34)
        # Continuous FIFO: newest sample overwrites the oldest
        self.set_reg(REG_LIS2DW_FIFO_CTRL, 0xC0)
        # High-performance 1600Hz, 14-bit
        self.set_reg(REG_LIS2DW_CTRL_REG1_ADDR, 0x94)
        with self.lock:
            self.raw_samples = []
        systime = self.printer.get_reactor().monotonic()
        print_time = self.mcu.estimated_print_time(systime) + MIN_MSG_TIME
        reqclock = self.mcu.print_time_to_clock(print_time)
        rest_ticks = self.mcu.seconds_to_clock(4. / self.data_rate)
        self.query_lis2dw_cmd.send([self.oid, reqclock, rest_ticks],
                                   reqclock=reqclock)
        logging.info("lis2dw_k2 starting '%s' measurements", self.name)
        self.last_sequence = 0
        self.last_limit_count = self.last_error_count = 0
        self.clock_sync.reset(reqclock, 0)
        self.max_query_duration = 1 << 31
        self._update_clock(minclock=reqclock)
        self.max_query_duration = 1 << 31
    def _finish_measurements(self):
        self.query_lis2dw_end_cmd.send([self.oid, 0, 0])
        with self.lock:
            self.raw_samples = []
        logging.info("lis2dw_k2 finished '%s' measurements", self.name)
        self.set_reg(REG_LIS2DW_FIFO_CTRL, 0x00)
    def _process_batch(self, eventtime):
        self._update_clock()
        with self.lock:
            raw_samples = self.raw_samples
            self.raw_samples = []
        if not raw_samples:
            return {}
        samples = self._extract_samples(raw_samples)
        if not samples:
            return {}
        return {'data': samples, 'errors': self.last_error_count,
                'overflows': self.last_limit_count}

def load_config(config):
    return LIS2DW_K2(config)

def load_config_prefix(config):
    return LIS2DW_K2(config)
