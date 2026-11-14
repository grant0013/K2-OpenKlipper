"""k2_z_align on the Creality Hi (F018) main-MCU firmware.

The Hi's query_z_align has no safeDist, there is no z_align_force_stop, the
speeds/distances count whole steps, and z_align_up lifts the gantry. The
expected numbers come from the stock Hi klippy.log (2026-11-14):
UP_SAFE_Z with quick 20 / slow 5 mm/s and 15 mm on 8 mm leads at 32
microsteps sent quickSpeed=7500 slowSpeed=30000 risingDist=12000.
"""
import importlib.util
import pathlib
import sys
import types
import unittest


HI_QUERY = ("query_z_align oid=%c enable=%c quickSpeed=%u slowSpeed=%u "
            "risingDist=%u filterCnt=%c")
K2_QUERY = HI_QUERY + " safeDist=%u"
UP_QUERY = ("query_z_align_up oid=%c enable=%c quickSpeed=%u "
            "slowSpeed=%u risingDist=%u filterCnt=%c")


class FakeCmd:
    def __init__(self, mcu, fmt):
        self.mcu, self.fmt = mcu, fmt

    def send(self, args):
        self.mcu.sent.append((self.fmt.split()[0], list(args)))
        if self.mcu.on_send is not None:
            self.mcu.on_send(self.fmt.split()[0], args)


class FakeMCU:
    def __init__(self, commands):
        self.commands = commands
        self.config_cmds = []
        self.sent = []
        self.responses = {}
        self.on_send = None
        self.next_oid = 7

    def create_oid(self):
        self.next_oid += 1
        return self.next_oid

    def register_config_callback(self, cb):
        self.config_cb = cb

    def register_serial_response(self, cb, msg, oid):
        self.responses[oid] = cb

    def add_config_cmd(self, cmd):
        self.config_cmds.append(cmd)

    def lookup_command(self, fmt):
        if fmt not in self.commands:
            raise Exception("Unknown command: %s" % (fmt,))
        return FakeCmd(self, fmt)

    def get_constant_float(self, name):
        assert name == "CLOCK_FREQ"
        return 120000000.


CURRENT_MCU = []
sys.modules["mcu"] = types.SimpleNamespace(
    get_printer_mcu=lambda printer, name: CURRENT_MCU[-1])

MODULE_PATH = (
    pathlib.Path(__file__).parents[1] / "klippy" / "extras" / "k2_z_align.py"
)
SPEC = importlib.util.spec_from_file_location("k2_z_align_hi", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class Section:
    def __init__(self, values):
        self.values = values

    def get(self, name, default=None):
        return self.values.get(name, default)

    def getfloat(self, name, default=None, **kw):
        return float(self.values.get(name, default))

    def getint(self, name, default=None, **kw):
        return int(self.values.get(name, default))

    def getboolean(self, name, default=None):
        return self.values.get(name, default)

    def getlist(self, name, default=None):
        value = self.values.get(name)
        if value is None:
            return default
        return [v.strip() for v in value.split(",")]


class Completion:
    def __init__(self):
        self.value = None

    def complete(self, value):
        self.value = value

    def wait(self, waketime):
        return self.value


class Reactor:
    def __init__(self):
        self.now = 0.

    def monotonic(self):
        return self.now

    def completion(self):
        return Completion()

    def pause(self, t):
        self.now = t


class Kin:
    def __init__(self):
        self.cleared = []
        self.rails = [None, None, types.SimpleNamespace(get_steppers=lambda: [])]

    def clear_homing_state(self, axes):
        self.cleared.append(axes)


class Toolhead:
    def __init__(self):
        self.kin = Kin()
        self.pos = [130., 130., 100., 0.]
        self.homed = []
        self.moves = []

    def wait_moves(self):
        pass

    def get_kinematics(self):
        return self.kin

    def get_position(self):
        return list(self.pos)

    def set_position(self, pos, homing_axes=""):
        self.pos = list(pos)
        self.homed.append(homing_axes)

    def manual_move(self, pos, speed):
        self.moves.append(list(pos))
        self.pos = list(pos)

    def get_status(self, eventtime=None):
        return {"homed_axes": ""}


class Gcode:
    def __init__(self):
        self.scripts = []
        self.commands = {}

    def register_command(self, name, fn, desc=None):
        self.commands[name] = fn

    def run_script_from_command(self, script):
        self.scripts.append(script)


class Printer:
    def __init__(self):
        self.reactor = Reactor()
        self.gcode = Gcode()
        self.toolhead = Toolhead()
        self.buttons = types.SimpleNamespace(
            register_buttons=lambda pins, cb: None)

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=None):
        if name == "gcode":
            return self.gcode
        if name == "toolhead":
            return self.toolhead
        return default

    def load_object(self, config, name):
        return self.buttons

    def register_event_handler(self, event, cb):
        pass


class Config(Section):
    def __init__(self, values, sections):
        super().__init__(values)
        self.sections = sections
        self.printer = Printer()

    def get_printer(self):
        return self.printer

    def has_section(self, name):
        return name in self.sections

    def getsection(self, name):
        return self.sections[name]

    def error(self, msg):
        return Exception(msg)


class GcmdError(Exception):
    pass


class Gcmd:
    def __init__(self, params):
        self.params = params
        self.info = []

    def get_int(self, name, default=None, **kw):
        return int(self.params.get(name, default))

    def respond_info(self, msg):
        self.info.append(msg)

    def error(self, msg):
        return GcmdError(msg)


HI_STOCK = {
    "allow_motion": True,
    "endstop_pin": "PB15, PB14",
    "position_endstop": 294.6,
    "quick_speed": 20, "slow_speed": 5, "rising_dist": 5,
    "filter_count": 10, "timeout": 30, "retract_dist": 10,
    "zd_up": 0, "zes_untrig": 0,
    "step_edges": 1, "retries": 6, "retry_tolerance": 10,
    "up_safe_dist": 15,
}


def make_hi(values=None, commands=None):
    if commands is None:
        commands = {HI_QUERY, UP_QUERY}
    mcu = FakeMCU(commands)
    CURRENT_MCU.append(mcu)
    z = Section({"step_pin": "PB4", "dir_pin": "PA5",
                 "rotation_distance": 8, "microsteps": 32,
                 "position_max": 310})
    z1 = Section({"step_pin": "PB6", "dir_pin": "PB5"})
    config = Config(dict(HI_STOCK, **(values or {})),
                    {"stepper_z": z, "stepper_z1": z1})
    align = MODULE.K2ZAlign(config)
    mcu.config_cb()
    # Switches start open on the Hi (zes_untrig 0, all-low start state)
    return align, mcu, config.printer


def reply_with(mcu, align, deltas):
    """Answer each query with flag 1 and the next delta."""
    seq = list(deltas)

    def on_send(name, args):
        if name in ("query_z_align", "query_z_align_up"):
            delta = seq.pop(0) if seq else 0
            mcu.responses[args[0]]({"flag": 1, "deltaError1": delta})
    mcu.on_send = on_send


class HiFirmwareTest(unittest.TestCase):
    def test_hi_signature_and_config(self):
        align, mcu, _ = make_hi()
        self.assertFalse(align.query_has_safe)
        self.assertIsNone(align.abort_cmd)
        up = [c for c in mcu.config_cmds if "z_align_up" in c]
        self.assertEqual(up[0], "config_z_align_up oid=%d" % align.up_oid)
        self.assertIn("config_z_align_up_add oid=%d z_indx=1 zs_pin=PB6 "
                      "zd_pin=PB5 zd_up=0 zes_pin=PB14 zes_untrig=0"
                      % align.up_oid, up)

    def test_k2_signature_still_preferred(self):
        align, mcu, _ = make_hi(values={"up_safe_dist": 0},
                                commands={K2_QUERY, HI_QUERY,
                                          "z_align_force_stop oid=%c"})
        self.assertTrue(align.query_has_safe)
        self.assertIsNotNone(align.abort_cmd)
        self.assertIsNone(align.up_oid)

    def test_timings_match_stock_hi_log(self):
        align, _, _ = make_hi()
        quick, slow, rising, _ = align._timings()
        self.assertEqual((quick, slow), (7500, 30000))
        self.assertEqual(align._distance_steps(15), 12000)
        self.assertEqual(rising, 4000)

    def test_up_safe_sends_stock_values(self):
        align, mcu, printer = make_hi()
        reply_with(mcu, align, [0])
        gcmd = Gcmd({"CONFIRM": 1})
        align.cmd_KZ_ALIGN_UP_SAFE(gcmd)
        self.assertEqual(mcu.sent, [("query_z_align_up",
                                     [align.up_oid, 1, 7500, 30000,
                                      12000, 10])])
        self.assertEqual(printer.toolhead.kin.cleared, ["z"])
        self.assertFalse(align.active)

    def test_up_safe_skips_when_at_switches(self):
        align, mcu, _ = make_hi()
        align._handle_switch(None, 0b01)
        gcmd = Gcmd({"CONFIRM": 1})
        align.cmd_KZ_ALIGN_UP_SAFE(gcmd)
        self.assertEqual(mcu.sent, [])

    def test_bottom_retries_until_motors_agree(self):
        align, mcu, printer = make_hi()
        reply_with(mcu, align, [25, -12, 3])
        align.cmd_KZ_ALIGN_BOTTOM(Gcmd({"CONFIRM": 1}))
        queries = [a for n, a in mcu.sent if n == "query_z_align"]
        self.assertEqual(len(queries), 3)
        # No safeDist on the Hi: 6 fields
        self.assertEqual(queries[0], [align.oid, 1, 7500, 30000, 4000, 10])
        self.assertEqual(printer.toolhead.homed[-1], "z")
        self.assertAlmostEqual(printer.toolhead.moves[-1][2], 284.6)

    def test_bottom_gives_up_after_retries(self):
        align, mcu, printer = make_hi(values={"retries": 2})
        reply_with(mcu, align, [50, 50, 50, 50])
        with self.assertRaises(GcmdError) as ctx:
            align.cmd_KZ_ALIGN_BOTTOM(Gcmd({"CONFIRM": 1}))
        self.assertIn("still differ by 50 steps after 3 tries",
                      str(ctx.exception))
        self.assertNotIn("z", printer.toolhead.homed)

    def test_timeout_turns_motors_off_without_stop_command(self):
        align, mcu, printer = make_hi()
        with self.assertRaises(GcmdError) as ctx:
            align.cmd_KZ_ALIGN_BOTTOM(Gcmd({"CONFIRM": 1}))
        self.assertIn("motors turned off", str(ctx.exception))
        self.assertIn("M84", printer.gcode.scripts)
        self.assertFalse(align.active)


if __name__ == "__main__":
    unittest.main()
