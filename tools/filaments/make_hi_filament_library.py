#!/usr/bin/env python3
"""Build the Creality Hi filament library (the 'library' list in
hark_cfs_slots.json) from EVERY filament profile OrcaSlicer ships for it.

The K2 tool next to this one (make_filament_library.py) takes a curated list.
The Hi takes all of them, so whatever is picked in Orca exists on the printer
too and the two match by name on a filament sync:

  1. Creality's own Hi profiles in OrcaSlicer ("Generic PLA Matte @Creality
     Hi-all", "Generic PLA Silk @Creality Hi-all", ...) that the Hi's
     material database does not already have.
  2. Every "@System" profile in OrcaSlicer's OrcaFilamentLibrary (Bambu,
     Elegoo, eSUN, Polymaker, SUNLU, Overture, JAYO, COEX, ...), the ones
     OrcaSlicer offers for any printer.

Names are shown without the "@..." suffix. A name the Hi's own material
database (Creality's 23) already has is skipped: that one stays, with its
Creality id. Each entry keeps the profile it came from in 'source_ref'
(OrcaSlicer profiles are AGPL-3.0), its finish ('Matte', 'Silk', ...) for the
screen's picker, and pressure advance 0 (measured per spool on the printer;
the profile's own value is kept as 'pa_start').

    python3 tools/filaments/make_hi_filament_library.py              # table
    python3 tools/filaments/make_hi_filament_library.py --write      # JSON
    python3 tools/filaments/make_hi_filament_library.py \\
        --merge hark_cfs_slots.json --out merged.json

--merge replaces only library entries made by these tools (id 'lib-...'),
keeps the slots, your own filaments ('u-...') and everything else. Write the
result to the printer only while it is idle, then RESTART Klipper.
"""

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_filament_library as k2lib  # noqa: E402

DST = os.path.join(HERE, 'filament_library_hi.json')
REF = 'v2.5.2'
CR = 'Creality'
OFL = 'OrcaFilamentLibrary'
# Creality's profiles written for the Hi (both suffixes are in use)
HI_SUFFIXES = ('@Creality Hi-all', '@Hi-all')
# The Hi's material database (Creality firmware 1.1.0.x, 23 profiles): these
# names are already on the printer with Creality's ids
HI_DATABASE = (
    'Hyper PLA', 'Hyper PLA-CF', 'Hyper PETG', 'Hyper ABS', 'ENDER FAST PLA',
    'CR-PLA', 'CR-Silk', 'CR-PETG', 'CR-ABS', 'Generic PLA',
    'Generic PLA-Silk', 'Generic PETG', 'Generic ABS', 'Ender-PLA', 'HP-TPU',
    'Generic BVOH', 'Generic PVA', 'Soleyin Ultra PLA', 'Hyper Stardust',
    'Hyper L-W PLA', 'Hyper Marble', 'Hyper Luminous', 'Soleyin Basic PETG')
# Creality's profiles for its other CoreXY printers, nearest the Hi first:
# used for filaments the two sources above lack (CR-PLA Matte, eSUN PLA+,
# PolySonic, HP-ASA, Hyper PETG-CF, ...)
FAMILY_SUFFIXES = ('@K2 Plus-all', '@K2-all', '@K2 Pro-all', '@K2 SE-all',
                   '@Ender-5Max-all', '@Creality', '@Creality HF')
# Orca's filament_type spellings the printer treats as the plain type
TYPE_FIX = {'TPU-AMS': 'TPU', 'PETG HF': 'PETG', 'PLA SILK': 'PLA',
            'PLA HF': 'PLA'}
# Finish words, in the order they are looked for in a name
FINISHES = (
    ('Matte', ('matte', 'matt ')), ('Silk', ('silk', 'satin')),
    ('High Speed', ('high speed', 'rapid', 'hyper speed', ' hs', '-hs',
                    'hf ', ' hf')),
    ('Wood', ('wood',)), ('Marble', ('marble',)), ('Glow', ('luminous',
                                                            'glow')),
    ('Sparkle', ('sparkle', 'galaxy', 'stardust', 'twinkl', 'glitter')),
    ('Metal', ('metal',)), ('Translucent', ('translucent', 'clear',
                                            'transparent')),
    ('Tough', ('tough',)), ('Lightweight', ('lw', 'light weight',
                                            'lightweight', 'aero')),
)


def display_name(preset):
    return preset.split(' @')[0].strip()


def finish_of(name):
    low = ' %s ' % name.lower()
    for finish, words in FINISHES:
        if any(w in low for w in words):
            return finish
    return ''


def brand_of(preset, name):
    first_word = name.split()[0] if name else ''
    vend = preset.get('filament_vendor')
    vend = k2lib.first(vend) if vend else ''
    if vend and vend.lower() not in ('generic', 'orcaslicer'):
        return vend
    return 'Generic' if first_word.lower() == 'generic' else first_word


def presets_to_import(orca):
    """(vendor, preset name) of every profile to bring in: the Hi's own
    first, then the library's, then Creality's K2-family ones for names
    neither has (build() keeps the first profile of each name)."""
    wanted = []
    for vendor in (CR, OFL):
        if vendor not in orca.index:
            v = orca.get(vendor + '.json')
            orca.index[vendor] = dict((e['name'], e['sub_path'])
                                      for e in v['filament_list'])
    cr = sorted(orca.index[CR])
    wanted += [(CR, n) for n in cr if n.endswith(HI_SUFFIXES)]
    wanted += [(OFL, n) for n in sorted(orca.index[OFL])
               if n.endswith('@System')]
    for suffix in FAMILY_SUFFIXES:
        wanted += [(CR, n) for n in cr if n.endswith(suffix)]
    return wanted


def clean_type(mtype):
    mtype = mtype.upper().strip()
    return TYPE_FIX.get(mtype, mtype)


def build(orca):
    out, names = [], {n.lower() for n in HI_DATABASE}
    skipped = []
    for vendor, preset in presets_to_import(orca):
        name = display_name(preset)
        if name.lower() in names:
            skipped.append(preset)
            continue
        p, rel = orca.preset(vendor, preset)
        if str(k2lib.first(p.get('instantiation', 'true'))).lower() == \
                'false':
            continue
        mtype = clean_type(k2lib.first(p.get('filament_type')) or 'PLA')
        fields = {'brand': brand_of(p, name), 'name': name, 'type': mtype,
                  'finish': finish_of(name)}
        for key, okey, cast in k2lib.FIELDS:
            fields[key] = k2lib.num(cast, p.get(okey))
        fields['nozzle_first'] = fields['nozzle_first'] or \
            fields['nozzle_temp']
        fields['bed_first'] = fields['bed_first'] or fields['bed_temp']
        fields['min_temp'] = fields['min_temp'] or min(
            k2lib.MIN_TEMP.get(mtype, 190), fields['nozzle_temp'] or 190)
        # hark_cfs_slots reads the volumetric limit as max_flow
        fields['max_flow'] = fields.get('max_volumetric_speed', 0)
        pa = k2lib.num(float, p.get('pressure_advance'))
        fields['pa_start'] = round(pa, 4) if pa and k2lib.num(
            int, p.get('enable_pressure_advance')) else 0
        fields['orca_preset'] = p.get('name', preset)
        fields['orca_filament_id'] = p.get('filament_id', '')
        fields['orca_setting_id'] = p.get('setting_id', '')
        fields['orca_type'] = k2lib.first(p.get('filament_type')) or ''
        out.append(k2lib.make_entry(
            fields, 'OrcaSlicer %s resources/profiles/%s (AGPL-3.0)'
            % (orca.ref, rel)))
        names.add(name.lower())
    ids = {}
    for e in out:
        # two presets with names that slug the same: number the second
        base, n = e['id'], 2
        while e['id'] in ids:
            e['id'], n = '%s-%d' % (base, n), n + 1
        ids[e['id']] = e
    if skipped:
        print('skipped %d profiles whose name is already in (the Hi '
              'database or an earlier source)' % len(skipped),
              file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--ref', default=REF, help='OrcaSlicer git ref')
    ap.add_argument('--cache', default=os.path.expanduser(
        '~/.cache/hark-orca-profiles'))
    ap.add_argument('--write', action='store_true',
                    help='write %s' % os.path.relpath(DST))
    ap.add_argument('--merge', metavar='STORE',
                    help='a hark_cfs_slots.json to merge into')
    ap.add_argument('--out', help='where --merge writes (default: STORE)')
    args = ap.parse_args()
    entries = build(k2lib.Orca(args.ref, args.cache))
    if args.write:
        with open(DST, 'w', encoding='utf-8', newline='\n') as f:
            json.dump({'source': 'tools/filaments/make_hi_filament_library.py'
                                 ', OrcaSlicer %s' % args.ref,
                       'library': entries}, f, indent=1, sort_keys=True)
            f.write('\n')
        print('wrote %s (%d filaments)' % (DST, len(entries)),
              file=sys.stderr)
    if args.merge:
        k2lib.merge(args.merge, entries, args.out or args.merge)
    if not args.write and not args.merge:
        k2lib.table(entries)


if __name__ == '__main__':
    main()
