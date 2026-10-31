import importlib.util
import pathlib
import sys
import types
import unittest


class FakeMCU:
    def create_oid(self):
        return 7

    def register_config_callback(self, cb):
        pass

    def register_serial_response(self, cb, msg, oid):
        pass


sys.modules.setdefault("mcu", types.SimpleNamespace(
    get_printer_mcu=lambda printer, name: FakeMCU()))

MODULE_PATH = (
    pathlib.Path(__file__).parents[1] / "klippy" / "extras" / "k2_z_align.py"
)
SPEC = importlib.util.spec_from_file_location("k2_z_align", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeSection:
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


class FakeButtons:
    def register_buttons(self, pins, callback):
        self.pins = pins
        self.callback = callback


class FakePrinter:
    def __init__(self):
        self.buttons = FakeButtons()

    def get_reactor(self):
        return None

    def lookup_object(self, name, default=None):
        return types.SimpleNamespace(register_command=lambda *a, **k: None)

    def load_object(self, config, name):
        return self.buttons

    def register_event_handler(self, event, cb):
        pass


class FakeConfig(FakeSection):
    def __init__(self, values, sections):
        super().__init__(values)
        self.sections = sections
        self.printer = FakePrinter()

    def get_printer(self):
        return self.printer

    def has_section(self, name):
        return name in self.sections

    def getsection(self, name):
        return self.sections[name]

    def error(self, msg):
        return Exception(msg)


def make(endstop_pin="PA15, PA8"):
    z = FakeSection({"step_pin": "PB6", "dir_pin": "PB5",
                     "rotation_distance": 8, "microsteps": 16,
                     "position_max": 360})
    z1 = FakeSection({"step_pin": "PB15", "dir_pin": "PA1"})
    config = FakeConfig({"endstop_pin": endstop_pin, "zd_up": 0},
                        {"stepper_z": z, "stepper_z1": z1})
    return MODULE.K2ZAlign(config), config.printer.buttons


class SwitchStateTest(unittest.TestCase):
    def test_bed_at_bottom_at_start_reads_triggered(self):
        # Both pins low since boot: buttons never calls back
        align, _ = make()
        self.assertEqual(align.switches, [True, True])
        self.assertTrue(align.switch_triggered)
        self.assertEqual(align._describe_switches(),
                         "PA15 TRIGGERED, PA8 TRIGGERED")

    def test_bed_raised_reads_open(self):
        align, buttons = make()
        buttons.callback(0.0, 0b11)
        self.assertEqual(align.switches, [False, False])
        self.assertFalse(align.switch_triggered)

    def test_one_side_triggered(self):
        align, buttons = make()
        buttons.callback(0.0, 0b01)
        self.assertEqual(align._describe_switches(),
                         "PA15 open, PA8 TRIGGERED")
        self.assertTrue(align.switch_triggered)

    def test_k2_single_switch(self):
        align, buttons = make("PA15")
        self.assertEqual(align.switches, [True])
        buttons.callback(0.0, 0b1)
        self.assertEqual(align._describe_switches(), "PA15 open")


if __name__ == "__main__":
    unittest.main()
