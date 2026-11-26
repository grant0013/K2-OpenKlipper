#!/usr/bin/env python3
"""Build the K2 filament library (the 'library' list in hark_cfs_slots.json)
from manufacturer filament profiles.

Sources, in order of preference for each filament:
  1. OrcaSlicer's Creality vendor profiles tuned for the K2 family
     ("eSUN PLA+ @K2 Plus-all", "PolySonic PLA @K2 Plus-all", ...).
  2. OrcaSlicer's OrcaFilamentLibrary vendor profiles ("Elegoo PLA @System",
     "JAYO PLA @Creality K2 Pro 0.4 nozzle", ...), maintained with or by the
     filament makers.
  3. A few hand-entered filaments Orca does not have (Deeplee), from the
     maker's own figures in the Open Filament Database; see MANUAL below.

OrcaSlicer's profiles are AGPL-3.0 (https://github.com/OrcaSlicer/OrcaSlicer);
each entry keeps the path of the profile it came from in 'source_ref'.

Pressure advance is measured per spool on this printer, so 'pressure_advance'
is left at 0 and the profile's value is kept as 'pa_start' (a starting point
only). Flow ratio is likewise a starting value until the reel is calibrated.

    python3 tools/filaments/make_filament_library.py            # table
    python3 tools/filaments/make_filament_library.py --write    # JSON
    python3 tools/filaments/make_filament_library.py \\
        --merge hark_cfs_slots.json --out merged.json            # into a store

--merge replaces only this tool's entries (id 'lib-...') in a store's
library, keeps the slots, custom filaments ('u-...') and everything else, and
bumps the revision. Write the result to the printer only while it is idle,
then RESTART so hark_cfs_slots reads it (it keeps the store in memory and
would write its old copy back on the next change).
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DST = os.path.join(HERE, 'filament_library.json')
RAW = ('https://raw.githubusercontent.com/OrcaSlicer/OrcaSlicer/%s/'
       'resources/profiles/')
REF = 'v2.5.0-rc'

CR = 'Creality'
OFL = 'OrcaFilamentLibrary'
# (Orca vendor, Orca preset name, brand, name shown on the K2, type)
CURATED = (
    # Elegoo
    (OFL, 'Elegoo PLA @System', 'Elegoo', 'Elegoo PLA', 'PLA'),
    (OFL, 'Elegoo PLA+ @System', 'Elegoo', 'Elegoo PLA+', 'PLA'),
    (OFL, 'Elegoo PLA PRO @System', 'Elegoo', 'Elegoo PLA PRO', 'PLA'),
    (OFL, 'Elegoo Rapid PLA+ @System', 'Elegoo', 'Elegoo Rapid PLA+', 'PLA'),
    (OFL, 'Elegoo PLA Matte @System', 'Elegoo', 'Elegoo PLA Matte', 'PLA'),
    (OFL, 'Elegoo PLA Silk @System', 'Elegoo', 'Elegoo PLA Silk', 'PLA'),
    (OFL, 'Elegoo PLA-CF @System', 'Elegoo', 'Elegoo PLA-CF', 'PLA-CF'),
    (OFL, 'Elegoo PETG @System', 'Elegoo', 'Elegoo PETG', 'PETG'),
    (OFL, 'Elegoo PETG PRO @System', 'Elegoo', 'Elegoo PETG PRO', 'PETG'),
    (OFL, 'Elegoo Rapid PETG @System', 'Elegoo', 'Elegoo Rapid PETG', 'PETG'),
    (OFL, 'Elegoo PETG-CF @System', 'Elegoo', 'Elegoo PETG-CF', 'PETG-CF'),
    (OFL, 'Elegoo ABS @System', 'Elegoo', 'Elegoo ABS', 'ABS'),
    (OFL, 'Elegoo ASA @System', 'Elegoo', 'Elegoo ASA', 'ASA'),
    (OFL, 'Elegoo TPU 95A @System', 'Elegoo', 'Elegoo TPU 95A', 'TPU'),
    # Bambu Lab
    (OFL, 'Bambu PLA Basic @System', 'Bambu Lab', 'Bambu PLA Basic', 'PLA'),
    (OFL, 'Bambu PLA Matte @System', 'Bambu Lab', 'Bambu PLA Matte', 'PLA'),
    (OFL, 'Bambu PLA Silk+ @System', 'Bambu Lab', 'Bambu PLA Silk+', 'PLA'),
    (OFL, 'Bambu PLA Tough+ @System', 'Bambu Lab', 'Bambu PLA Tough+',
     'PLA'),
    (OFL, 'Bambu PLA-CF @System', 'Bambu Lab', 'Bambu PLA-CF', 'PLA-CF'),
    (OFL, 'Bambu PETG Basic @System', 'Bambu Lab', 'Bambu PETG Basic',
     'PETG'),
    (OFL, 'Bambu PETG HF @System', 'Bambu Lab', 'Bambu PETG HF', 'PETG'),
    (OFL, 'Bambu PETG-CF @System', 'Bambu Lab', 'Bambu PETG-CF', 'PETG-CF'),
    (OFL, 'Bambu ABS @System', 'Bambu Lab', 'Bambu ABS', 'ABS'),
    (OFL, 'Bambu ASA @System', 'Bambu Lab', 'Bambu ASA', 'ASA'),
    (OFL, 'Bambu TPU 95A @System', 'Bambu Lab', 'Bambu TPU 95A', 'TPU'),
    # Polymaker
    (OFL, 'PolyLite PLA @System', 'Polymaker', 'PolyLite PLA', 'PLA'),
    (OFL, 'PolyLite PLA Pro @System', 'Polymaker', 'PolyLite PLA Pro',
     'PLA'),
    (OFL, 'PolyTerra PLA @System', 'Polymaker', 'PolyTerra PLA', 'PLA'),
    (CR, 'PolySonic PLA @K2 Plus-all', 'Polymaker', 'PolySonic PLA', 'PLA'),
    (CR, 'Panchroma PLA Matte @K2 Plus-all', 'Polymaker',
     'Panchroma PLA Matte', 'PLA'),
    (CR, 'Panchroma PLA Satin @K2 Plus-all', 'Polymaker',
     'Panchroma PLA Satin', 'PLA'),
    (OFL, 'Panchroma PLA Silk @System', 'Polymaker', 'Panchroma PLA Silk',
     'PLA'),
    (OFL, 'PolyLite PETG @System', 'Polymaker', 'PolyLite PETG', 'PETG'),
    (OFL, 'Fiberon PETG-rCF @System', 'Polymaker', 'Fiberon PETG-rCF',
     'PETG-CF'),
    (OFL, 'PolyLite ABS @System', 'Polymaker', 'PolyLite ABS', 'ABS'),
    (OFL, 'PolyLite ASA @System', 'Polymaker', 'PolyLite ASA', 'ASA'),
    # eSUN: Creality's K2 tuned profiles
    (OFL, 'eSUN PLA-Basic @System', 'eSUN', 'eSUN PLA-Basic', 'PLA'),
    (CR, 'eSUN PLA+ @K2 Plus-all', 'eSUN', 'eSUN PLA+', 'PLA'),
    (CR, 'eSUN PLA-HS @K2 Plus-all', 'eSUN', 'eSUN PLA-HS', 'PLA'),
    (CR, 'eSUN PLA-Matte @K2 Plus-all', 'eSUN', 'eSUN PLA-Matte', 'PLA'),
    (CR, 'eSUN PLA-Silk @K2 Plus-all', 'eSUN', 'eSUN PLA-Silk', 'PLA'),
    (CR, 'eSUN PLA-CF @K2 Plus-all', 'eSUN', 'eSUN PLA-CF', 'PLA-CF'),
    (CR, 'eSUN PETG @K2 Plus-all', 'eSUN', 'eSUN PETG', 'PETG'),
    (CR, 'eSUN ABS+ @K2 Plus-all', 'eSUN', 'eSUN ABS+', 'ABS'),
    (CR, 'eSUN ASA+ @K2 Plus-all', 'eSUN', 'eSUN ASA+', 'ASA'),
    # SUNLU
    (OFL, 'SUNLU PLA+ @System', 'SUNLU', 'SUNLU PLA+', 'PLA'),
    (OFL, 'SUNLU PLA+ 2.0 @System', 'SUNLU', 'SUNLU PLA+ 2.0', 'PLA'),
    (OFL, 'SUNLU PLA Matte @System', 'SUNLU', 'SUNLU PLA Matte', 'PLA'),
    (OFL, 'SUNLU Silk PLA+ @System', 'SUNLU', 'SUNLU Silk PLA+', 'PLA'),
    (OFL, 'SUNLU PETG @System', 'SUNLU', 'SUNLU PETG', 'PETG'),
    # JAYO: JAYO's own Creality K2 Pro profiles
    (OFL, 'JAYO PLA @Creality K2 Pro 0.4 nozzle', 'JAYO', 'JAYO PLA', 'PLA'),
    (OFL, 'JAYO PLA Classic @Creality K2 Pro 0.4 nozzle', 'JAYO',
     'JAYO PLA Classic', 'PLA'),
    (OFL, 'JAYO PETG @Creality K2 Pro 0.4 nozzle', 'JAYO', 'JAYO PETG',
     'PETG'),
    # Overture
    (OFL, 'Overture PLA @System', 'Overture', 'Overture PLA', 'PLA'),
    (OFL, 'Overture PLA Pro @System', 'Overture', 'Overture PLA Pro', 'PLA'),
    (OFL, 'Overture Super PLA+ @System', 'Overture', 'Overture Super PLA+',
     'PLA'),
    (OFL, 'Overture Matte PLA @System', 'Overture', 'Overture Matte PLA',
     'PLA'),
    (OFL, 'Overture Silk PLA @System', 'Overture', 'Overture Silk PLA',
     'PLA'),
    (OFL, 'Overture ABS Basic @System', 'Overture', 'Overture ABS Basic',
     'ABS'),
    (OFL, 'Overture ASA @System', 'Overture', 'Overture ASA', 'ASA'),
    (OFL, 'Overture TPU @System', 'Overture', 'Overture TPU', 'TPU'),
)

# Filaments Orca has no profile for. Temperature ranges are the maker's
# (DeepLee via the Open Filament Database, shown on
# simplyprint.io/filaments/deeplee); the rest are generic PLA values for a
# K2 at speed, to be checked against the spool label.
DEEPLEE_REF = ('DeepLee figures from the Open Filament Database '
               '(simplyprint.io/filaments/deeplee/%s); other values generic '
               'PLA for the K2')
MANUAL = (
    dict(brand='DeepLee', name='DeepLee PLA+', type='PLA',
         nozzle_temp=215, nozzle_first=220, min_temp=190, max_temp=230,
         bed_temp=45, bed_first=45, fan_min=100, fan_max=100,
         flow_ratio=0.98, retract_length=0, retract_speed=0,
         max_volumetric_speed=15, density=1.24, pa_start=0.04,
         source_ref=DEEPLEE_REF % 'pla-plus-2-0',
         note='Maker gives bed 35-45 C; raise to 50-55 C if the first '
              'layer lifts on the K2 plate'),
    dict(brand='DeepLee', name='DeepLee PLA', type='PLA',
         nozzle_temp=210, nozzle_first=215, min_temp=190, max_temp=220,
         bed_temp=55, bed_first=55, fan_min=100, fan_max=100,
         flow_ratio=0.98, retract_length=0, retract_speed=0,
         max_volumetric_speed=15, density=1.24, pa_start=0.04,
         source_ref=DEEPLEE_REF % 'pla'),
)

# K2 library key <- OrcaSlicer key (cast, see hark_cfs_slots.SETTINGS)
FIELDS = (
    ('nozzle_temp', 'nozzle_temperature', int),
    ('nozzle_first', 'nozzle_temperature_initial_layer', int),
    ('min_temp', 'nozzle_temperature_range_low', int),
    ('max_temp', 'nozzle_temperature_range_high', int),
    ('bed_temp', 'hot_plate_temp', int),
    ('bed_first', 'hot_plate_temp_initial_layer', int),
    ('flow_ratio', 'filament_flow_ratio', float),
    ('retract_length', 'filament_retraction_length', float),
    ('retract_speed', 'filament_retraction_speed', float),
    ('fan_min', 'fan_min_speed', int),
    ('fan_max', 'fan_max_speed', int),
    ('max_volumetric_speed', 'filament_max_volumetric_speed', float),
    ('density', 'filament_density', float),
)
# Keys hark_cfs_slots reads (SETTING_KEYS); all others are kept as notes
SETTING_KEYS = ('nozzle_temp', 'nozzle_first', 'min_temp', 'max_temp',
                'bed_temp', 'pressure_advance', 'flow_ratio',
                'retract_length', 'retract_speed', 'fan_min', 'fan_max')


# Lowest nozzle temperature by type, for presets that do not say
MIN_TEMP = {'PLA': 190, 'PLA-CF': 190, 'PETG': 220, 'PETG-CF': 230,
            'ABS': 240, 'ASA': 240, 'TPU': 200}


class Orca:
    def __init__(self, ref, cache):
        self.ref = ref
        self.cache = os.path.join(cache, ref)
        self.index = {}

    def get(self, rel):
        path = os.path.join(self.cache, rel)
        if not os.path.exists(path):
            url = RAW % self.ref + urllib.parse.quote(rel)
            with urllib.request.urlopen(url, timeout=30) as r:
                data = r.read()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'wb') as f:
                f.write(data)
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    def preset(self, vendor, name):
        """The preset with everything it inherits, plus its file path."""
        if vendor not in self.index:
            v = self.get(vendor + '.json')
            self.index[vendor] = dict((e['name'], e['sub_path'])
                                      for e in v['filament_list'])
        chain, own, rel = [], None, None
        while name:
            sub = self.index[vendor].get(name)
            if sub is None:
                raise KeyError('%s: no filament preset %r' % (vendor, name))
            data = self.get(vendor + '/' + sub)
            if own is None:
                own, rel = data, vendor + '/' + sub
            chain.append(data)
            name = data.get('inherits')
        merged = {}
        for data in reversed(chain):
            merged.update(data)
        # filament_id / setting_id are not inherited the same way in every
        # vendor: take the nearest one up the chain
        for key in ('filament_id', 'setting_id'):
            merged[key] = next((d[key] for d in chain if d.get(key)), '')
        return merged, rel


def first(value):
    if isinstance(value, list):
        value = value[0] if value else None
    return value


def num(cast, value):
    value = first(value)
    if value in (None, '', 'nil'):
        return 0
    try:
        return cast(float(str(value).rstrip('%')))
    except ValueError:
        return 0


def slug(text):
    text = text.lower().replace('+', ' plus')
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')


def make_entry(fields, ref_text):
    entry = {
        'id': 'lib-' + slug(fields['name']),
        'source': 'library',
        'filament_id': '',     # Creality's material id; none for these
        'color': '',
        'pressure_advance': 0,  # measured per spool, never imported
    }
    entry.update(fields)
    entry['source_ref'] = ref_text
    for key in SETTING_KEYS:
        entry.setdefault(key, 0)
    return entry


def build(orca):
    out = []
    for vendor, preset, brand, name, mtype in CURATED:
        p, rel = orca.preset(vendor, preset)
        fields = {'brand': brand, 'name': name, 'type': mtype}
        for key, okey, cast in FIELDS:
            fields[key] = num(cast, p.get(okey))
        # Some presets leave the first layer unset: same as the rest
        fields['nozzle_first'] = fields['nozzle_first'] or \
            fields['nozzle_temp']
        fields['bed_first'] = fields['bed_first'] or fields['bed_temp']
        # A few presets leave the range unset: the type's usual low end
        fields['min_temp'] = fields['min_temp'] or min(
            MIN_TEMP.get(mtype, 190), fields['nozzle_temp'])
        pa = num(float, p.get('pressure_advance'))
        if pa and num(int, p.get('enable_pressure_advance')):
            fields['pa_start'] = round(pa, 4)
        else:
            fields['pa_start'] = 0
        fields['orca_preset'] = p.get('name', preset)
        fields['orca_filament_id'] = p.get('filament_id', '')
        fields['orca_setting_id'] = p.get('setting_id', '')
        fields['orca_type'] = first(p.get('filament_type')) or ''
        out.append(make_entry(fields, 'OrcaSlicer %s resources/profiles/%s '
                                      '(AGPL-3.0)' % (orca.ref, rel)))
    for m in MANUAL:
        m = dict(m)
        ref = m.pop('source_ref')
        out.append(make_entry(m, ref))
    ids = [e['id'] for e in out]
    dup = set(i for i in ids if ids.count(i) > 1)
    if dup:
        raise SystemExit('duplicate ids: %s' % ', '.join(sorted(dup)))
    return out


def table(entries):
    cols = ('brand', 'name', 'type', 'nozzle_temp', 'nozzle_first',
            'min_temp', 'max_temp', 'bed_temp', 'fan_min', 'fan_max',
            'flow_ratio', 'retract_length', 'max_volumetric_speed',
            'pa_start', 'orca_filament_id')
    head = ('Brand', 'Name', 'Type', 'Noz', '1st', 'Min', 'Max', 'Bed',
            'FanMin', 'FanMax', 'Flow', 'Retr', 'MVS', 'PAstart', 'OrcaID')
    rows = [head] + [tuple(str(e.get(c, '')) for c in cols) for e in entries]
    w = [max(len(r[i]) for r in rows) for i in range(len(head))]
    for r in rows:
        print('  '.join(v.ljust(w[i]) for i, v in enumerate(r)).rstrip())


def merge(store_path, entries, out_path):
    with open(store_path, encoding='utf-8') as f:
        store = json.load(f)
    library = [e for e in store.get('library', [])
               if not str(e.get('id', '')).startswith('lib-')]
    taken = set(e['name'].lower() for e in library)
    added = [e for e in entries if e['name'].lower() not in taken]
    store['library'] = library + added
    store['revision'] = int(store.get('revision', 0)) + 1
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(store, f, indent=2, sort_keys=True)
    print('merged %d library entries (%d kept from the store) into %s'
          % (len(added), len(library), out_path))


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
    entries = build(Orca(args.ref, args.cache))
    if args.write:
        with open(DST, 'w', encoding='utf-8', newline='\n') as f:
            json.dump({'source': 'tools/filaments/make_filament_library.py, '
                                 'OrcaSlicer %s' % args.ref,
                       'library': entries}, f, indent=1, sort_keys=True)
            f.write('\n')
        print('wrote %s (%d filaments)' % (DST, len(entries)),
              file=sys.stderr)
    if args.merge:
        merge(args.merge, entries, args.out or args.merge)
    if not args.write and not args.merge:
        table(entries)


if __name__ == '__main__':
    main()
