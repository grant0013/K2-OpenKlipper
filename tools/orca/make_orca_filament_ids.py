#!/usr/bin/env python3
"""Build klippy/extras/hark_orca_ids.json: Creality filament name -> the
filament_id OrcaSlicer's own Creality K2 presets carry.

OrcaSlicer 2.5.0-beta and later match a Moonraker lane_data lane to a
filament preset by the lane's filament_id, if a compatible preset carries
it ("Hyper PLA" -> "Hyper PLA @K2-all", id OFCZsqXg). hark_cfs_slots writes
these ids into lane_data, so stock Orca picks Creality's own preset for a
Creality filament instead of "Generic PLA". Rerun when Orca adds profiles:

    python3 tools/orca/make_orca_filament_ids.py [--ref v2.5.0-beta]
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
DST = os.path.join(ROOT, 'klippy', 'extras', 'hark_orca_ids.json')
RAW = 'https://raw.githubusercontent.com/OrcaSlicer/OrcaSlicer/%s/resources/profiles/'
# Orca names the K2 family's presets "<name> @K2-all", "@K2 Plus-all", ...
SUFFIXES = (' @K2-all', ' @K2 Plus-all', ' @K2 Pro-all', ' @K2 SE-all')


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', default='v2.5.0-beta')
    args = ap.parse_args()
    base = RAW % args.ref
    vendor = get_json(base + 'Creality.json')
    ids = {}
    for entry in vendor.get('filament_list', []):
        name = entry['name']
        suffix = next((s for s in SUFFIXES if name.endswith(s)), None)
        if suffix is None:
            continue
        short = name[:-len(suffix)]
        preset = get_json(base + 'Creality/' + urllib.parse.quote(
            entry['sub_path']))
        fid = preset.get('filament_id')
        if not fid:
            continue
        if short in ids and ids[short] != fid:
            print("warning: %s has ids %s and %s; keeping the K2 one"
                  % (short, ids[short], fid), file=sys.stderr)
            if suffix != ' @K2-all':
                continue
        ids[short] = fid
    out = {'source': 'OrcaSlicer %s resources/profiles/Creality' % args.ref,
           'ids': dict(sorted(ids.items()))}
    with open(DST, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, indent=1)
        f.write('\n')
    print("wrote %s (%d filaments)" % (os.path.relpath(DST, ROOT), len(ids)))


if __name__ == '__main__':
    main()
