# CFS slot filament records and filament library for the K2 open stack.
#
# Stores what is loaded in each CFS slot and a library of user filament
# profiles in a JSON file, exposes both through get_status for Moonraker
# clients (Mainsail/Fluidd panels, the K2 Companion app), publishes the slots
# to Moonraker's lane_data namespace so OrcaSlicer can sync them, applies a
# slot's pressure advance when it is armed for a print, and keeps the
# button-driven prompt editor for UIs without the CFS panel.
#
# Copyright (C) 2026 Grant Harkness (Hark Tech)
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, logging, os, re, threading, time, urllib.request

SLOTS = ('A', 'B', 'C', 'D')
TYPE_ORDER = ('PLA', 'PETG', 'ABS', 'ASA', 'TPU', 'PLA-CF', 'PETG-CF')
COLOURS = (
    ('Black', '000000'), ('White', 'FFFFFF'), ('Grey', '808080'),
    ('Silver', 'C0C0C0'), ('Red', 'E53935'), ('Orange', 'FB8C00'),
    ('Yellow', 'FDD835'), ('Gold', 'FFC107'), ('Green', '43A047'),
    ('Lime', 'C0CA33'), ('Cyan', '26EEEE'), ('Blue', '1E88E5'),
    ('Navy', '1A237E'), ('Purple', '8E24AA'), ('Pink', 'EC407A'),
    ('Brown', '6D4C41'), ('Beige', 'D7CCC8'),
)
# Filament settings: (key, gcode parameter, type, OrcaSlicer preset key).
# Keys follow the K2/Orca preset fields so profiles move between them as is.
SETTINGS = (
    ('nozzle_temp', 'NOZZLE', int, 'nozzle_temperature'),
    ('nozzle_first', 'NOZZLE_FIRST', int, 'nozzle_temperature_initial_layer'),
    ('min_temp', 'MIN_TEMP', int, 'nozzle_temperature_range_low'),
    ('max_temp', 'MAX_TEMP', int, 'nozzle_temperature_range_high'),
    ('bed_temp', 'BED', int, 'hot_plate_temp'),
    ('pressure_advance', 'PA', float, 'pressure_advance'),
    ('flow_ratio', 'FLOW', float, 'filament_flow_ratio'),
    ('retract_length', 'RETRACT', float, 'filament_retraction_length'),
    ('retract_speed', 'RETRACT_SPEED', float, 'filament_retraction_speed'),
    ('fan_min', 'FAN_MIN', int, 'fan_min_speed'),
    ('fan_max', 'FAN_MAX', int, 'fan_max_speed'),
)
SETTING_KEYS = tuple(s[0] for s in SETTINGS)
# Fluidd closes a prompt when the command behind a prompt button completes,
# which would also close a prompt sent by that command. Send the next dialog
# just after the command has finished.
PROMPT_DELAY = 0.4
EMPTY = dict({'loaded': False, 'filament': '', 'type': '', 'name': '',
              'brand': '', 'color': '', 'filament_id': '',
              'source': 'empty'}, **{k: 0 for k in SETTING_KEYS})


def _norm_colour(value):
    """Accept a colour name, RRGGBB, #RRGGBB or Creality's #0RRGGBB."""
    value = value.strip()
    for name, hexval in COLOURS:
        if value.lower() == name.lower():
            return hexval
    value = value.lstrip('#')
    if len(value) == 7 and value[0] == '0':
        value = value[1:]
    if len(value) == 8:
        value = value[:6]
    if len(value) != 6:
        raise ValueError("colour must be a name or RRGGBB")
    int(value, 16)
    return value.upper()


def _colour_name(hexval):
    if not hexval:
        return ''
    for name, h in COLOURS:
        if h == hexval.upper():
            return name
    return '#' + hexval.upper()


def _quote(value):
    return '"%s"' % value.replace('"', '')


def _cast(cast, value):
    if isinstance(value, list):
        value = value[0] if value else None
    if value in (None, '', 'nil'):
        return 0
    try:
        return cast(float(value)) if cast is int else cast(value)
    except (TypeError, ValueError):
        return 0


class HarkCfsSlots:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')
        self.reactor = self.printer.get_reactor()
        self.store_path = config.get(
            'store_path',
            '/mnt/UDISK/printer_data_mainline/config/hark_cfs_slots.json')
        self.creality_dir = config.get(
            'creality_dir', '/mnt/UDISK/creality/userdata/box')
        self.moonraker_url = config.get(
            'moonraker_url', 'http://127.0.0.1:7126').rstrip('/')
        self.materials = []
        self.library = {}
        self.slots = {}
        self.revision = 0
        self._load_database()
        self._load_store()
        self.printer.register_event_handler('klippy:ready',
                                            self._publish_lanes)
        for cmd, desc in (
                ('CFS_SLOTS', "Show the CFS slots and edit what is loaded"),
                ('CFS_SLOT_EDIT', "Step through the CFS slot editor"),
                ('CFS_SLOT_SET', "Set a CFS slot: SLOT= FILAMENT= or NAME=, "
                                 "COLOR=, setting overrides"),
                ('CFS_SLOT_CLEAR', "Mark a CFS slot as empty: SLOT="),
                ('CFS_SLOT_APPLY', "Apply a slot's pressure advance: SLOT="),
                ('CFS_SLOTS_CLOSE', "Close the CFS slot dialog"),
                ('CFS_FILAMENT_SAVE', "Create or update a filament profile: "
                                      "[ID=] [BASE=] NAME= TYPE= BRAND= "
                                      "COLOR= NOZZLE= BED= PA= FLOW= ..."),
                ('CFS_FILAMENT_DELETE', "Delete a custom filament: ID=")):
            self.gcode.register_command(
                cmd, getattr(self, 'cmd_' + cmd), desc=desc)

    # --- persistence ---------------------------------------------------
    def _load_database(self):
        path = os.path.join(self.creality_dir, 'material_database.json')
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            seen = set()
            for entry in data['result']['list']:
                base = entry.get('base', entry)
                kv = entry.get('kvParam', {})
                if not base.get('name') or base['name'] in seen:
                    continue
                seen.add(base['name'])
                mat = {
                    'id': 'cr-' + str(base.get('id', '')),
                    'source': 'creality',
                    'filament_id': base.get('id', ''),
                    'name': base.get('name', ''),
                    'brand': base.get('brand', ''),
                    'type': base.get('meterialType', ''),
                    'color': '',
                }
                for key, _, cast, orca in SETTINGS:
                    mat[key] = _cast(cast, kv.get(orca))
                mat['min_temp'] = mat['min_temp'] or _cast(
                    int, base.get('minTemp'))
                mat['max_temp'] = mat['max_temp'] or _cast(
                    int, base.get('maxTemp'))
                self.materials.append(mat)
        except Exception:
            logging.exception("hark_cfs_slots: material database not loaded")

    def _seed_from_creality(self):
        path = os.path.join(self.creality_dir, 'material_box_info.json')
        slots = {s: dict(EMPTY) for s in SLOTS}
        try:
            with open(path, encoding='utf-8') as f:
                info = json.load(f)['Material']['info'][0]['list']
            for item in info:
                slot = item.get('materialId')
                if slot in slots:
                    slots[slot] = self._slot_record(
                        self._find_filament(item.get('name', '')),
                        name=item.get('name', ''),
                        colour=_norm_colour(item.get('color', '#0000000')),
                        source='creality_rfid' if item.get('rfid')
                        else 'creality')
        except Exception:
            logging.exception("hark_cfs_slots: Creality slot seed failed")
        return slots

    def _load_store(self):
        try:
            with open(self.store_path, encoding='utf-8') as f:
                data = json.load(f)
            self.slots = {s: dict(EMPTY, **data['slots'].get(s, {}))
                          for s in SLOTS}
            self.library = {f['id']: f for f in data.get('library', [])}
            self.revision = int(data.get('revision', 0))
        except (IOError, OSError, ValueError, KeyError):
            self.slots = self._seed_from_creality()
            self._save()
            return
        # Slots saved before filament settings existed: fill them in from
        # the matching profile once.
        upgraded = False
        for slot, rec in self.slots.items():
            if rec['loaded'] and not rec.get('filament'):
                fil = self._find_filament(rec['name'])
                if fil:
                    self.slots[slot] = self._slot_record(
                        fil, colour=rec['color'], source=rec['source'])
                    upgraded = True
        if upgraded:
            self._save()

    def _save(self):
        self.revision += 1
        tmp = self.store_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'revision': self.revision, 'slots': self.slots,
                       'library': list(self.library.values())},
                      f, indent=2, sort_keys=True)
        os.replace(tmp, self.store_path)
        self._publish_lanes()

    def get_status(self, eventtime):
        # Klipper sends subscribers only the fields whose value changed since
        # the last report, comparing against the object it was given before.
        # Return copies so in-place edits are seen as changes. The static
        # 'materials' list is therefore sent once.
        return {'slots': {k: dict(v) for k, v in self.slots.items()},
                'library': [dict(f) for f in self.library.values()],
                'revision': self.revision, 'materials': self.materials}

    # --- OrcaSlicer sync through Moonraker ------------------------------
    def _lane_values(self):
        lanes = {}
        for index, slot in enumerate(SLOTS):
            rec = self.slots[slot]
            loaded = rec.get('loaded')
            lanes['lane%d' % (index + 1)] = {
                'lane': str(index),
                'name': rec['name'] if loaded else '',
                'material': rec['type'] if loaded else '',
                'vendor': rec['brand'] if loaded else '',
                'color': '#' + rec['color'] if loaded and rec['color']
                         else '',
                'nozzle_temp': rec.get('nozzle_temp', 0) if loaded else 0,
                'bed_temp': rec.get('bed_temp', 0) if loaded else 0,
                'pressure_advance': rec.get('pressure_advance', 0)
                                    if loaded else 0,
                'flow_ratio': rec.get('flow_ratio', 0) if loaded else 0,
                'filament_id': rec.get('filament', '') if loaded else '',
            }
        return lanes

    def _publish_lanes(self):
        # Moonraker's lane_data namespace is read by OrcaSlicer's Klipper
        # agent. Post from a thread so Klipper's reactor never waits on HTTP.
        lanes = self._lane_values()
        url = self.moonraker_url + '/server/database/item'

        def post():
            for key, value in lanes.items():
                body = json.dumps({'namespace': 'lane_data', 'key': key,
                                   'value': value}).encode()
                req = urllib.request.Request(
                    url, data=body, method='POST',
                    headers={'Content-Type': 'application/json'})
                for attempt in range(5):
                    try:
                        urllib.request.urlopen(req, timeout=3).read()
                        break
                    except Exception as e:
                        if attempt == 4:
                            logging.info("hark_cfs_slots: lane_data %s not "
                                         "published: %s", key, e)
                        time.sleep(3)
        threading.Thread(target=post, daemon=True).start()

    # --- helpers -------------------------------------------------------
    def _slot_param(self, gcmd):
        slot = gcmd.get('SLOT').upper()
        if slot in ('0', '1', '2', '3'):
            slot = SLOTS[int(slot)]
        if slot not in SLOTS:
            raise gcmd.error("SLOT must be A, B, C or D")
        return slot

    def _all_filaments(self):
        return list(self.library.values()) + self.materials

    def _find_filament(self, key):
        if not key:
            return None
        for fil in self._all_filaments():
            if fil['id'] == key:
                return fil
        for fil in self._all_filaments():
            if fil['name'].lower() == key.lower():
                return fil
        return None

    def _slot_record(self, fil, name='', colour='', source='manual'):
        rec = dict(EMPTY)
        if fil:
            rec.update({k: fil.get(k, 0) for k in SETTING_KEYS})
            rec.update({'filament': fil['id'], 'name': fil['name'],
                        'type': fil['type'], 'brand': fil['brand'],
                        'filament_id': fil.get('filament_id', '')})
            colour = colour or fil.get('color', '')
        else:
            rec['name'] = name
        rec.update({'loaded': True, 'color': colour or 'FFFFFF',
                    'source': source})
        return rec

    def _apply_setting_params(self, gcmd, rec):
        for key, param, cast, _ in SETTINGS:
            if cast is int:
                value = gcmd.get_int(param, None, minval=0, maxval=500)
            else:
                value = gcmd.get_float(param, None, minval=0., maxval=10.)
            if value is not None:
                rec[key] = value

    def _types(self):
        present = sorted({m['type'] for m in self._all_filaments()
                          if m['type']})
        ordered = [t for t in TYPE_ORDER if t in present]
        return ordered + [t for t in present if t not in ordered]

    def _describe(self, slot):
        rec = self.slots[slot]
        if not rec.get('loaded'):
            return "%s: empty" % slot
        out = "%s: %s %s (%s" % (slot, _colour_name(rec['color']),
                                 rec['name'], rec['type'])
        if rec.get('nozzle_temp'):
            out += ", %dC" % rec['nozzle_temp']
        if rec.get('pressure_advance'):
            out += ", PA %.3f" % rec['pressure_advance']
        return out + ")"

    def _prompt(self, title, lines, buttons, footer):
        out = ['action:prompt_end', 'action:prompt_begin %s' % title]
        out += ['action:prompt_text %s' % l for l in lines]
        if buttons:
            out.append('action:prompt_button_group_start')
            for i, (label, gcode, style) in enumerate(buttons):
                if i and i % 3 == 0:
                    out.append('action:prompt_button_group_end')
                    out.append('action:prompt_button_group_start')
                out.append('action:prompt_button %s|%s|%s'
                           % (label, gcode, style))
            out.append('action:prompt_button_group_end')
        for label, gcode, style in footer:
            out.append('action:prompt_footer_button %s|%s|%s'
                       % (label, gcode, style))
        out.append('action:prompt_show')
        def emit(eventtime):
            for line in out:
                self.gcode.respond_raw('// ' + line)
        self.reactor.register_callback(
            emit, self.reactor.monotonic() + PROMPT_DELAY)

    # --- slot commands -------------------------------------------------
    def cmd_CFS_SLOT_SET(self, gcmd):
        slot = self._slot_param(gcmd)
        rec = dict(self.slots[slot])
        key = gcmd.get('FILAMENT', None) or gcmd.get('NAME', None)
        colour = gcmd.get('COLOR', None)
        try:
            colour = _norm_colour(colour) if colour is not None else ''
        except ValueError as e:
            raise gcmd.error(str(e))
        if key is not None:
            fil = self._find_filament(key)
            if fil is None and gcmd.get('FILAMENT', None):
                raise gcmd.error("Unknown filament '%s'" % key)
            rec = self._slot_record(fil, name=key,
                                    colour=colour or rec.get('color', ''))
        elif colour:
            rec['color'] = colour
        for param, field in (('TYPE', 'type'), ('BRAND', 'brand')):
            value = gcmd.get(param, None)
            if value is not None:
                rec[field] = value
        self._apply_setting_params(gcmd, rec)
        if not rec.get('name') or not rec.get('type'):
            raise gcmd.error("Slot %s needs a filament, or NAME and TYPE"
                             % slot)
        rec['loaded'] = True
        rec['source'] = 'manual'
        self.slots[slot] = rec
        self._save()
        gcmd.respond_info("CFS slot updated: %s" % self._describe(slot))
        if gcmd.get_int('MENU', 0):
            self.cmd_CFS_SLOTS(gcmd)

    def cmd_CFS_SLOT_CLEAR(self, gcmd):
        slot = self._slot_param(gcmd)
        self.slots[slot] = dict(EMPTY, source='manual')
        self._save()
        gcmd.respond_info("CFS slot %s marked empty" % slot)
        if gcmd.get_int('MENU', 0):
            self.cmd_CFS_SLOTS(gcmd)

    def cmd_CFS_SLOT_APPLY(self, gcmd):
        # Called when a slot is armed for printing. A pressure advance set
        # later by the sliced G-code still takes precedence.
        slot = self._slot_param(gcmd)
        rec = self.slots[slot]
        pa = rec.get('pressure_advance') or 0
        if rec.get('loaded') and pa > 0:
            self.gcode.run_script_from_command(
                "SET_PRESSURE_ADVANCE ADVANCE=%.4f" % pa)
            gcmd.respond_info("CFS slot %s: pressure advance %.4f (%s)"
                              % (slot, pa, rec['name']))
        # Same for G10/G11 firmware retraction; 0 means "not set", which
        # keeps the [firmware_retraction] defaults.
        length = rec.get('retract_length') or 0
        speed = rec.get('retract_speed') or 0
        fwr = self.printer.lookup_object('firmware_retraction', None)
        if rec.get('loaded') and length > 0 and fwr is not None:
            cmd = "SET_RETRACTION RETRACT_LENGTH=%.3f" % length
            if speed > 0:
                cmd += " RETRACT_SPEED=%.1f" % speed
            # SET_RETRACTION clears the retracted flag. A T command arrives
            # with the slicer's G10 still in force, so undo it at the old
            # length and redo it at the new one, keeping G11 balanced.
            if getattr(fwr, 'is_retracted', False):
                cmd = "G11\n%s\nG10" % cmd
            self.gcode.run_script_from_command(cmd)
            gcmd.respond_info("CFS slot %s: retraction %.2fmm%s (%s)"
                              % (slot, length,
                                 " at %.0fmm/s" % speed if speed > 0 else "",
                                 rec['name']))

    # --- filament library ---------------------------------------------
    def _new_id(self, name):
        slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')[:24]
        base = 'u-' + (slug or 'filament')
        fid, n = base, 2
        while fid in self.library:
            fid, n = '%s-%d' % (base, n), n + 1
        return fid

    def cmd_CFS_FILAMENT_SAVE(self, gcmd):
        fid = gcmd.get('ID', None)
        existing = self.library.get(fid) if fid else None
        base = self._find_filament(gcmd.get('BASE', '')) or \
            (self._find_filament(fid) if fid and not existing else None)
        fil = dict(existing or base or {})
        name = gcmd.get('NAME', fil.get('name', ''))
        if not name:
            raise gcmd.error("NAME is required")
        if existing is None:
            fid = self._new_id(name)
        fil.update({'id': fid, 'name': name, 'source': fil.get(
            'source') if existing else 'custom', 'filament_id': ''})
        if existing is None and base is not None:
            fil['based_on'] = base['name']
        for param, field in (('TYPE', 'type'), ('BRAND', 'brand')):
            fil[field] = gcmd.get(param, fil.get(field, ''))
        colour = gcmd.get('COLOR', None)
        if colour is not None:
            try:
                fil['color'] = _norm_colour(colour)
            except ValueError as e:
                raise gcmd.error(str(e))
        fil.setdefault('color', '')
        for key in SETTING_KEYS:
            fil.setdefault(key, 0)
        self._apply_setting_params(gcmd, fil)
        if not fil.get('type'):
            raise gcmd.error("TYPE is required")
        self.library[fid] = fil
        # Slots using this profile pick up its new settings; keep each
        # slot's own spool colour.
        for slot, rec in self.slots.items():
            if rec.get('loaded') and rec.get('filament') == fid:
                self.slots[slot] = self._slot_record(
                    fil, colour=rec['color'], source=rec['source'])
        self._save()
        gcmd.respond_info("Filament saved: %s (id %s)" % (name, fid))

    def cmd_CFS_FILAMENT_DELETE(self, gcmd):
        fid = gcmd.get('ID')
        if fid not in self.library:
            raise gcmd.error("No custom filament with id '%s'" % fid)
        name = self.library.pop(fid)['name']
        self._save()
        gcmd.respond_info("Filament deleted: %s" % name)

    # --- prompt dialogs (UIs without the CFS panel) ---------------------
    def cmd_CFS_SLOTS(self, gcmd):
        self._prompt(
            'CFS slots',
            [self._describe(s) for s in SLOTS],
            [('Edit %s' % s, 'CFS_SLOT_EDIT SLOT=%s' % s, 'primary')
             for s in SLOTS],
            [('Close', 'CFS_SLOTS_CLOSE', 'secondary')])

    def cmd_CFS_SLOTS_CLOSE(self, gcmd):
        self.gcode.respond_raw('// action:prompt_end')

    def cmd_CFS_SLOT_EDIT(self, gcmd):
        slot = self._slot_param(gcmd)
        mtype = gcmd.get('TYPE', None)
        name = gcmd.get('NAME', None)
        back = [('Back', 'CFS_SLOTS', 'secondary')]
        if mtype is None:
            buttons = [(t, 'CFS_SLOT_EDIT SLOT=%s TYPE=%s' % (slot, t),
                        'primary') for t in self._types()]
            buttons.append(('Empty', 'CFS_SLOT_CLEAR SLOT=%s MENU=1' % slot,
                            'warning'))
            self._prompt('Slot %s: material type' % slot,
                         [self._describe(slot)], buttons, back)
        elif name is None:
            mats = [m for m in self._all_filaments() if m['type'] == mtype]
            mats.sort(key=lambda m: (m['source'] == 'creality',
                                     m['brand'] != 'Creality', m['name']))
            buttons = [(m['name'], 'CFS_SLOT_EDIT SLOT=%s TYPE=%s NAME=%s'
                        % (slot, mtype, _quote(m['name'])), 'primary')
                       for m in mats]
            self._prompt('Slot %s: %s material' % (slot, mtype),
                         [self._describe(slot)], buttons, back)
        else:
            buttons = [(c, 'CFS_SLOT_SET SLOT=%s NAME=%s COLOR=%s MENU=1'
                        % (slot, _quote(name), h), 'primary')
                       for c, h in COLOURS]
            self._prompt(
                'Slot %s: %s colour' % (slot, name),
                ['Other colours: CFS_SLOT_SET SLOT=%s COLOR=RRGGBB' % slot],
                buttons, back)


def load_config(config):
    return HarkCfsSlots(config)
