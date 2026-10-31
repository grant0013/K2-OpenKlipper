#!/usr/bin/env python3
"""Build orca/*.json: OrcaSlicer printer profiles for K2 OpenKlipper, to
import with File > Import > Import Configs.

Each one inherits Creality's own Orca printer profile and changes only what
K2 OpenKlipper needs (the reasons are in config/ORCA-K2-PROFILE.cfg):
  - Machine start G-code: START_PRINT with the first tool and the prime
    tower area,
  - Change filament G-code: no move to X0 and drop to Z0 (Creality's
    template scraped the bed on the first tool selection),
  - Connection: Octo/Klipper and Moonraker on port 7126, in place of
    Creality's own service, which K2 OpenKlipper switches off.
The user only types the printer's IP into the Connection dialog.
    python3 tools/orca/make_orca_printer_profiles.py
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
DST = os.path.join(ROOT, 'orca')

START_GCODE = "\n".join([
    "START_PRINT EXTRUDER_TEMP=[nozzle_temperature_initial_layer]"
    " BED_TEMP=[bed_temperature_initial_layer_single]"
    " TOOL=[initial_no_support_extruder]"
    " TOWER_X={wipe_tower_x[0]} TOWER_Y={wipe_tower_y[0]}"
    " TOWER_W={if enable_prime_tower}[prime_tower_width]{else}0{endif}",
    "T[initial_no_support_extruder]",
    "M204 S2000",
    "M83",
])

CHANGE_FILAMENT_GCODE = "\n".join([
    "SET_GCODE_VARIABLE MACRO=_CFS_TC VARIABLE=next_purge"
    " VALUE={flush_length}",
    "G90",
    "G1 Z{max(z_after_toolchange, 0.6)} F600",
])

# (our profile name, Creality's Orca profile it builds on)
PRINTERS = [
    ("Creality K2 OpenKlipper", "Creality K2 0.4 nozzle"),
    ("Creality K2 Plus OpenKlipper", "Creality K2 Plus 0.4 nozzle"),
]


def profile(name, parent):
    return {
        "from": "User",
        "inherits": parent,
        "name": name,
        "printer_settings_id": name,
        # Orca's import skips a file without a version, silently
        "version": "2.3.1.20",
        "machine_start_gcode": START_GCODE,
        "change_filament_gcode": CHANGE_FILAMENT_GCODE,
        # Octo/Klipper: Moonraker answers OctoPrint's upload API
        "host_type": "octoprint",
        "printer_agent": "moonraker",
        "print_host": "http://PRINTER-IP:7126",
    }


def main():
    os.makedirs(DST, exist_ok=True)
    for name, parent in PRINTERS:
        path = os.path.join(DST, name + ".json")
        with open(path, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(profile(name, parent), f, indent=4)
            f.write('\n')
        print("wrote %s" % os.path.relpath(path, ROOT))


if __name__ == '__main__':
    main()
