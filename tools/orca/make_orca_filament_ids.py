#!/usr/bin/env python3
"""Build klippy/extras/hark_orca_ids.json: filament name -> the filament_id
of OrcaSlicer's own preset for it. Creality's K2 presets come first, then the
"<name> @System" presets of the Orca Filament Library (Elegoo, Bambu,
Polymaker, ...), which every printer can use.

OrcaSlicer 2.5.0-beta and later match a Moonraker lane_data lane to a
filament preset by the lane's filament_id, if a compatible preset carries
it ("Hyper PLA" -> "Hyper PLA @K2-all", id OFCZsqXg). hark_cfs_slots writes
these ids into lane_data, so stock Orca picks the filament's own preset
instead of "Generic PLA". Rerun when Orca adds profiles:

    python3 tools/orca/make_orca_filament_ids.py [--ref v2.5.0]
"""

import argparse
import concurrent.futures
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
SYSTEM_SUFFIX = ' @System'


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def library_ids(base):
    """Filament name -> id for the Orca Filament Library. A @System preset
    can leave its id to the @base preset it inherits from."""
    root = base + 'OrcaFilamentLibrary/'
    entries = get_json(base + 'OrcaFilamentLibrary.json')['filament_list']
    paths = dict((e['name'], e['sub_path']) for e in entries)
    presets = {}

    def fetch(name):
        return name, get_json(root + urllib.parse.quote(paths[name]))

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        presets.update(pool.map(fetch, paths))

    ids = {}
    for name, preset in presets.items():
        if not name.endswith(SYSTEM_SUFFIX):
            continue
        if preset.get('compatible_printers'):
            continue
        fid = preset.get('filament_id')
        parent = preset.get('inherits')
        while not fid and parent in presets:
            fid = presets[parent].get('filament_id')
            parent = presets[parent].get('inherits')
        if fid:
            ids[name[:-len(SYSTEM_SUFFIX)]] = fid
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ref', default='v2.5.0')
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
    for name, fid in library_ids(base).items():
        ids.setdefault(name, fid)
    out = {'source': 'OrcaSlicer %s resources/profiles/Creality and '
                     'OrcaFilamentLibrary' % args.ref,
           'ids': dict(sorted(ids.items()))}
    with open(DST, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(out, f, indent=1)
        f.write('\n')
    print("wrote %s (%d filaments)" % (os.path.relpath(DST, ROOT), len(ids)))


if __name__ == '__main__':
    main()
