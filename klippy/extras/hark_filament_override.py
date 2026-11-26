# The loaded slot's filament record wins over what the file was sliced with.
#
# Every line of the file being printed passes through here (virtual_sdcard's
# lines only: macros, purges, wipes and the screen are not touched). While a
# print runs, for the slot each tool prints from (CFS_TOOL_MAP / T0-T3):
#
#   START_PRINT ... EXTRUDER_TEMP= BED_TEMP= MATERIAL= VOLUMETRIC=
#              -> the start slot's first-layer nozzle, bed, type, max flow
#   M104/M109 S   the file's nozzle temperature for a tool -> that tool's
#                 slot (first-layer value -> nozzle_first); other values
#                 (standby, 0) pass
#   M140/M190 S>0 -> the start slot's bed
#   M106 S        capped at the slot's fan_max (%), as the slicer means it
#   SET_PRESSURE_ADVANCE ADVANCE=  -> the slot's PA (when it has one); a
#                 file that changes PA within one tool is a PA test or
#                 adaptive PA and runs as sliced
#   flow          M221 = slot flow ratio / the file's flow ratio for that
#                 tool, set after START_PRINT and at every tool change
#   retraction    the file's retract / wipe / unretract moves are scaled so
#                 the length is the slot's retract_length (relative E only;
#                 G10/G11 files use [firmware_retraction] via CFS_SLOT_APPLY)
#
# The file's own values come from OrcaSlicer's config block at the end of
# the file (filament_flow_ratio, nozzle_temperature, ...). A slot value of 0
# means "not set": the file's is kept. Calibration prints opt out with a
# FILAMENT_OVERRIDE PRINT=0 line; files named like calibrations (calib,
# tower) print as sliced too.
#
#   FILAMENT_OVERRIDE                 show what is in force
#   FILAMENT_OVERRIDE ENABLE=0|1      switch it off / on (kept over restarts)
#   FILAMENT_OVERRIDE PRINT=0         this print as sliced (calibration files)
#
# Copyright (C) 2026 Grant Harkness (Hark Tech)
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, logging, os, re

TOOLS = ('T0', 'T1', 'T2', 'T3')
CACHE_S = 5.0
# Orca's config block sits at the end of the file
TAIL_BYTES = 768 * 1024
CONFIG_KEYS = ('filament_flow_ratio', 'nozzle_temperature',
               'nozzle_temperature_initial_layer', 'retraction_length',
               'filament_retraction_length', 'fan_max_speed',
               'use_relative_e_distances', 'use_firmware_retraction',
               'filament_type', 'pressure_advance')
CALIB_NAME = re.compile(r'calib|tower', re.I)
WORD = re.compile(r'([A-Za-z])\s*(-?[0-9.]+)')
LIMITS = {'nozzle': (170., 350.), 'bed': (1., 120.), 'flow': (0.5, 1.5),
          'retract': (0., 10.), 'fan': (1, 99)}


def parse_config(text):
    """Orca / Creality Print '; key = value' lines -> {key: [values]}."""
    out = {}
    for line in text.splitlines():
        if not line.startswith('; '):
            continue
        key, sep, value = line[2:].partition(' = ')
        key = key.strip()
        if not sep or key not in CONFIG_KEYS:
            continue
        value = value.strip()
        parts = value.split('";"') if value.startswith('"') else \
            value.split(',')
        out[key] = [p.strip().strip('"') for p in parts]
    return out


def read_file_config(path):
    try:
        size = os.path.getsize(path)
        with open(path, 'rb') as f:
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode('utf-8', 'replace')
            if 'CONFIG_BLOCK_START' not in tail and size > TAIL_BYTES:
                f.seek(0)
                tail = f.read(TAIL_BYTES).decode('utf-8', 'replace')
    except (OSError, IOError):
        logging.exception("hark_filament_override: cannot read %s", path)
        return {}
    return parse_config(tail)


def _num(values, tool, cast=float):
    """The tool's value of a per-filament list (the last one if shorter)."""
    if not values:
        return None
    v = values[min(tool, len(values) - 1)]
    try:
        return cast(float(v))
    except (TypeError, ValueError):
        return None


def family(mtype):
    """PLA for PLA / PLA-CF / PLA Matte / PLA+, PETG for PETG HF ..."""
    m = re.match(r'[A-Z]+', (mtype or '').upper().strip())
    return m.group(0) if m else ''


def file_type(cfg, tool):
    types = cfg.get('filament_type') or []
    if len(types) == 1 and ';' in types[0]:
        types = types[0].split(';')
    if not types:
        return ''
    return types[min(tool, len(types) - 1)].strip().strip('"')


def file_values(cfg, tool):
    first = _num(cfg.get('nozzle_temperature_initial_layer'), tool)
    nozzle = _num(cfg.get('nozzle_temperature'), tool)
    retract = _num(cfg.get('filament_retraction_length'), tool)
    if retract is None:
        # 'nil' = the filament leaves it to the printer profile
        retract = _num(cfg.get('retraction_length'), tool)
    return {'first': first, 'nozzle': nozzle, 'type': file_type(cfg, tool),
            'flow': _num(cfg.get('filament_flow_ratio'), tool),
            'retract': retract,
            'fan': _num(cfg.get('fan_max_speed'), tool)}


def in_range(value, kind):
    lo, hi = LIMITS[kind]
    return value is not None and lo <= value <= hi


def slot_values(rec):
    """The record's values that are set and sane (others: None)."""
    if not rec or not rec.get('loaded'):
        return {}
    nozzle = float(rec.get('nozzle_temp') or 0)
    first = float(rec.get('nozzle_first') or 0) or nozzle
    out = {'nozzle': nozzle, 'first': first,
           'bed': float(rec.get('bed_temp') or 0),
           'flow': float(rec.get('flow_ratio') or 0),
           'retract': float(rec.get('retract_length') or 0),
           'fan': int(rec.get('fan_max') or 0),
           'pa': float(rec.get('pressure_advance') or 0),
           'type': str(rec.get('type') or ''),
           'max_flow': float(rec.get('max_flow') or 0),
           'name': str(rec.get('name') or '')}
    for key, kind in (('nozzle', 'nozzle'), ('first', 'nozzle'),
                      ('bed', 'bed'), ('flow', 'flow'), ('fan', 'fan')):
        if not in_range(out[key], kind):
            out[key] = None
    if not (0. < out['retract'] <= LIMITS['retract'][1]):
        out['retract'] = None
    if not (0. < out['pa'] < 1.):
        out['pa'] = None
    return out


def set_param(line, name, value):
    """line with NAME=value replaced (or added)."""
    pat = re.compile(r'(\b%s=)(\S+)' % name, re.I)
    if pat.search(line):
        return pat.sub(lambda m: m.group(1) + value, line, count=1)
    return line + ' %s=%s' % (name, value)


def get_param(line, name):
    m = re.search(r'\b%s=(\S+)' % name, line, re.I)
    return m.group(1) if m else None


class Move:
    """G0/G1 words of a line: {letter: float}, or None if not a move."""
    @staticmethod
    def parse(code):
        head = code[:3].upper()
        if head not in ('G1 ', 'G0 ') and code.upper() not in ('G1', 'G0'):
            return None
        words = {}
        for m in WORD.finditer(code[2:]):
            words[m.group(1).upper()] = m.group(2)
        return words

    @staticmethod
    def with_e(code, e):
        return re.sub(r'([Ee])\s*-?[0-9.]+', lambda m: '%s%.5f' % (
            m.group(1), e), code, count=1)


class _FileLines:
    """Stands in for virtual_sdcard's gcode object (see hark_flow_cal)."""
    def __init__(self, delegate, owner):
        self._delegate = delegate
        self._owner = owner

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def run_script(self, script):
        try:
            line = self._owner.file_line(script)
        except Exception:
            logging.exception("hark_filament_override: line %r", script)
            line = script
        if line is None:
            return None
        return self._delegate.run_script(line)


class HarkFilamentOverride:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        config_file = self.printer.get_start_args().get('config_file', '')
        self.state_path = config.get(
            'state_path', os.path.join(os.path.dirname(config_file) or '.',
                                       'hark_filament_override.json'))
        self.enabled = self._load_enabled()
        self.vsd = None
        self._reset()
        self.printer.register_event_handler('klippy:ready', self._ready)
        self.gcode.register_command(
            'FILAMENT_OVERRIDE', self.cmd_FILAMENT_OVERRIDE,
            desc="The loaded filament's settings over the file's: "
                 "ENABLE=0|1, PRINT=0 (this print as sliced)")

    # --- state ---------------------------------------------------------
    def _reset(self, path=None):
        self.path = path
        self.last_pos = -1
        self.active = False        # START_PRINT seen, not opted out
        self.skip = ''             # why this print runs as sliced
        self.cfg = None
        self.tool = 0
        self.start_slot = None
        self.relative_e = True
        self.factor = 1.0          # M221 this module set (1 = none)
        self.file_ret = 0.         # file retraction outstanding
        self.new_ret = 0.          # what was sent for it
        self.file_pa = {}
        self.pa_free = False
        self.reported = set()
        self.applied = {}
        self._cache = {}
        self._fv = {}
        self._start_sv = {}

    def _load_enabled(self):
        try:
            with open(self.state_path) as f:
                return bool(json.load(f).get('enabled', 1))
        except (OSError, IOError, ValueError, AttributeError):
            return True

    def _save_enabled(self):
        tmp = self.state_path + '.tmp'
        with open(tmp, 'w') as f:
            json.dump({'enabled': int(self.enabled)}, f)
        os.replace(tmp, self.state_path)

    def _ready(self):
        self.vsd = self.printer.lookup_object('virtual_sdcard', None)
        if self.vsd is not None:
            self.vsd.gcode = _FileLines(self.vsd.gcode, self)

    def get_status(self, eventtime):
        return {'enabled': self.enabled, 'active': self.active,
                'skip': self.skip, 'tool': self.tool,
                'applied': dict(self.applied)}

    # --- lookups -------------------------------------------------------
    def _slot_of(self, tool):
        slots = self.printer.lookup_object('hark_cfs_slots', None)
        if slots is None:
            return None, {}
        try:
            tmap = slots.tool_slots(self.reactor.monotonic())
        except Exception:
            tmap = {}
        slot = tmap.get('T%d' % tool) or tmap.get('T0')
        return slot, slots.slots.get(slot) or {}

    def _sv(self, tool):
        """slot_values for a tool, looked up at most every CACHE_S (a
        print runs thousands of retractions; a slot edited mid-print is
        picked up within that time)."""
        now = self.reactor.monotonic()
        hit = self._cache.get(tool)
        if hit is None or now - hit[0] > CACHE_S:
            slot, rec = self._slot_of(tool)
            hit = (now, self._checked(tool, slot, slot_values(rec)))
            self._cache[tool] = hit
        return hit[1]

    def _checked(self, tool, slot, sv):
        """sv, or {} when the slot holds another kind of filament than the
        file was sliced for (a PLA label on a PETG spool must not print the
        PETG file at PLA temperatures): that tool prints as sliced."""
        if not sv:
            return sv
        want = self._file(tool).get('type')
        if want and sv.get('type') and family(want) != family(sv['type']):
            self._say('type%d' % tool,
                      "!! Filament settings: T%d's file was sliced for %s but"
                      " slot %s holds %s (%s). T%d prints as sliced; fix the "
                      "slot (CFS > Edit) if the file is right"
                      % (tool, want, slot, sv['type'], sv.get('name'), tool))
            return {}
        return sv

    def _file(self, tool):
        if self.cfg is None:
            self.cfg = read_file_config(self.path) if self.path else {}
            rel = (self.cfg.get('use_relative_e_distances') or ['1'])[0]
            self.relative_e = rel not in ('0', 'false')
        if tool not in self._fv:
            self._fv[tool] = file_values(self.cfg, tool)
        return self._fv[tool]

    def _say(self, key, msg):
        if key not in self.reported:
            self.reported.add(key)
            self.gcode.respond_info(msg)

    # --- the file's lines ----------------------------------------------
    def file_line(self, line):
        vsd = self.vsd
        path = vsd.file_path() if vsd is not None else None
        pos = vsd.file_position if vsd is not None else 0
        if path != self.path or pos < self.last_pos:
            self._reset(path)
        self.last_pos = pos
        code = line.split(';', 1)[0].strip()
        if not code:
            return line
        word = code.split(None, 1)[0].upper()
        if word == 'FILAMENT_OVERRIDE':
            return line
        if word == 'START_PRINT':
            return self._start_print(code)
        if not self.active or self.skip:
            return line
        if word in ('M82', 'M83'):
            self.relative_e = word == 'M83'
            return line
        if word in ('T0', 'T1', 'T2', 'T3'):
            return self._tool_change(code, int(word[1]))
        if word in ('G1', 'G0'):
            return self._move(code, line)
        if word in ('M104', 'M109'):
            return self._nozzle(code, line)
        if word in ('M140', 'M190'):
            return self._bed(code, line)
        if word == 'M106':
            return self._fan(code, line)
        if word == 'SET_PRESSURE_ADVANCE':
            return self._pa(code, line)
        if word == 'SET_PRINT_STATS_INFO':
            return self._layer(code, line)
        return line

    def _layer(self, code, line):
        """Layer 2: a file whose first-layer and normal nozzle temperatures
        are the same sends no M104 there, so the slot's own drop (say 225 to
        220) is sent here."""
        if get_param(code, 'CURRENT_LAYER') != '2' or 'layer2' in self.reported:
            return line
        self.reported.add('layer2')
        fv = self._file(self.tool)
        sv = self._sv(self.tool)
        if fv.get('first') is None or fv.get('nozzle') is None or \
                abs(fv['first'] - fv['nozzle']) > 0.05:
            return line
        if not sv.get('nozzle') or not sv.get('first') or \
                abs(sv['first'] - sv['nozzle']) < 0.05:
            return line
        return '%s\nM104 S%d' % (line, sv['nozzle'])

    def _start_print(self, code):
        if not self.enabled:
            self.skip = 'switched off (FILAMENT_OVERRIDE ENABLE=1)'
        elif self.path and CALIB_NAME.search(os.path.basename(self.path)):
            self.skip = 'calibration file'
        if self.skip:
            self.gcode.respond_info("Filament settings: printing as sliced "
                                    "(%s)" % self.skip)
            return code
        try:
            self.tool = int(get_param(code, 'TOOL') or 0)
        except ValueError:
            self.tool = 0
        slot, rec = self._slot_of(self.tool)
        given = get_param(code, 'SLOT')
        if given:
            slots = self.printer.lookup_object('hark_cfs_slots', None)
            slot = given.upper()
            rec = slots.slots.get(slot, {}) if slots else {}
        sv = slot_values(rec)
        if sv:
            sv = self._checked(self.tool, slot, sv)
            if not sv:
                self.skip = 'start slot holds another filament type'
                return code
        if not sv:
            self.skip = 'no filament set for the start slot'
            self.gcode.respond_info("Filament settings: slot %s has no "
                                    "filament set, printing as sliced"
                                    % (slot or '?'))
            return code
        self.active = True
        self.start_slot = slot
        self._start_sv = sv
        fv = self._file(self.tool)
        out = code
        notes = []
        if sv['first']:
            old = get_param(code, 'EXTRUDER_TEMP')
            out = set_param(out, 'EXTRUDER_TEMP', '%d' % sv['first'])
            notes.append('nozzle %d%s' % (sv['first'], (' (file %s)' % old)
                                          if old else ''))
        if sv['bed']:
            old = get_param(code, 'BED_TEMP')
            out = set_param(out, 'BED_TEMP', '%d' % sv['bed'])
            notes.append('bed %d%s' % (sv['bed'], (' (file %s)' % old)
                                       if old else ''))
        if sv['type'] and get_param(code, 'MATERIAL') is not None:
            out = set_param(out, 'MATERIAL', sv['type'].upper()
                            .replace(' ', '-'))
        if sv['max_flow'] and get_param(code, 'VOLUMETRIC') is not None:
            out = set_param(out, 'VOLUMETRIC', '%g' % sv['max_flow'])
        flow = self._flow_line(self.tool, sv, fv)
        if flow:
            notes.append(flow[1])
        if sv['retract'] and fv['retract']:
            notes.append('retraction %.2f (file %.2f)'
                         % (sv['retract'], fv['retract']))
        elif sv['retract']:
            notes.append('retraction %.2f: file has none to scale'
                         % sv['retract'])
        if sv['pa']:
            notes.append('PA %.4f' % sv['pa'])
        if sv['fan']:
            notes.append('fan max %d%%' % sv['fan'])
        self.applied = {'slot': slot, 'tool': self.tool, 'name': sv['name']}
        self.gcode.respond_info(
            "Filament settings from slot %s (%s) over the file: %s"
            % (slot, sv['name'], ', '.join(notes) or 'nothing set'))
        return out + ('\n' + flow[0] if flow else '')

    def _flow_line(self, tool, sv, fv):
        """(M221 line, note) for this tool's slot, or None."""
        if not sv.get('flow') or not fv.get('flow'):
            self.factor = 1.0
            return ('M221 S100', 'flow as sliced') if sv else None
        factor = sv['flow'] / fv['flow']
        if not (0.8 <= factor <= 1.25):
            self._say('flowrange%d' % tool,
                      "Filament settings: slot flow %.2f vs file %.2f is "
                      "too far apart, flow left as sliced"
                      % (sv['flow'], fv['flow']))
            self.factor = 1.0
            return 'M221 S100', 'flow as sliced'
        self.factor = factor
        return ('M221 S%.1f' % (factor * 100.),
                'flow %.2f (file %.2f, M221 %.1f%%)'
                % (sv['flow'], fv['flow'], factor * 100.))

    def _tool_change(self, code, tool):
        # the retraction before the change is balanced by the unretract
        # after it, so file_ret / new_ret carry over
        self.tool = tool
        self._cache.pop(tool, None)
        slot, rec = self._slot_of(tool)
        sv = self._checked(tool, slot, slot_values(rec))
        fv = self._file(tool)
        flow = self._flow_line(tool, sv, fv) or ('M221 S100', '')
        self.applied['tool'] = tool
        self.applied['slot'] = slot
        return 'M221 S100\n%s\n%s' % (code, flow[0])

    # --- temperatures --------------------------------------------------
    def _s(self, code):
        m = re.search(r'\bS\s*(-?[0-9.]+)', code[4:], re.I)
        return float(m.group(1)) if m else None

    def _nozzle(self, code, line):
        s = self._s(code)
        if s is None or s < 170 or re.search(r'\bT\d', code[4:]):
            return line
        # which tool's file temperature is this? the current tool first
        tools = [self.tool] + [t for t in range(4) if t != self.tool]
        for t in tools:
            fv = self._file(t)
            for kind in ('first', 'nozzle'):
                if fv.get(kind) is None or abs(fv[kind] - s) > 0.05:
                    continue
                if kind == 'first' and fv.get('nozzle') is not None and \
                        abs(fv['nozzle'] - s) <= 0.05 and self._past_layer1():
                    kind = 'nozzle'
                sv = self._sv(t)
                new = sv.get(kind) or sv.get('nozzle')
                if not new or abs(new - s) < 0.05:
                    return line
                self._say('noz%d%s' % (t, kind),
                          "Filament settings: T%d nozzle %d, not the file's "
                          "%d" % (t, new, s))
                return '%s S%d' % (code.split()[0], new)
        return line

    def _past_layer1(self):
        ps = self.printer.lookup_object('print_stats', None)
        if ps is None:
            return False
        info = ps.get_status(self.reactor.monotonic()).get('info') or {}
        return (info.get('current_layer') or 0) >= 2

    def _bed(self, code, line):
        s = self._s(code)
        if not s or s <= 0:
            return line
        bed = self._start_sv.get('bed')
        if not bed or abs(bed - s) < 0.05:
            return line
        self._say('bed', "Filament settings: bed %d, not the file's %d"
                  % (bed, s))
        return '%s S%d' % (code.split()[0], bed)

    def _fan(self, code, line):
        if re.search(r'\bP\s*[1-9]', code[4:], re.I):
            return line
        s = self._s(code)
        if s is None:
            return line
        fan = self._sv(self.tool).get('fan')
        if not fan:
            return line
        cap = round(fan * 2.55)
        if s <= cap:
            return line
        self._say('fan%d' % self.tool,
                  "Filament settings: part fan capped at %d%%" % fan)
        rest = re.sub(r'\bS\s*-?[0-9.]+', '', code[4:], flags=re.I).strip()
        return 'M106 S%d%s' % (cap, (' ' + rest) if rest else '')

    def _pa(self, code, line):
        if self.pa_free:
            return line
        value = get_param(code, 'ADVANCE')
        if value is None:
            return line
        try:
            value = float(value)
        except ValueError:
            return line
        slot = self.applied.get('slot')
        pa = self._sv(self.tool).get('pa')
        if not pa:
            return line
        seen = self.file_pa.get(self.tool)
        if seen is not None and abs(seen - value) > 1e-6:
            self.pa_free = True
            self.gcode.respond_info(
                "Filament settings: this file changes pressure advance as "
                "it prints (a PA test or adaptive PA), its own values are "
                "used from here")
            return line
        self.file_pa[self.tool] = value
        self._say('pa%d' % self.tool,
                  "Filament settings: T%d pressure advance %.4f (slot %s), "
                  "not the file's %.4f" % (self.tool, pa, slot, value))
        return set_param(code, 'ADVANCE', '%.4f' % pa)

    # --- retraction ----------------------------------------------------
    def _move(self, code, line):
        words = Move.parse(code)
        if not words or 'E' not in words:
            return line
        if not self.relative_e:
            self._say('abs', "Filament settings: absolute E in this file, "
                             "retraction left as sliced")
            return line
        try:
            e = float(words['E'])
        except ValueError:
            return line
        xyz = any(k in words for k in 'XYZ')
        if e < 0:
            scale = self._retract_scale()
            if scale is None:
                return line
            new = e * scale
            self.file_ret += -e
            self.new_ret += -new
            return Move.with_e(code, new)
        if not xyz and self.file_ret > 0:
            # unretract: put back what was taken, plus the file's extra
            new = e - self.file_ret + self.new_ret
            self.file_ret = self.new_ret = 0.
            return Move.with_e(code, max(0., new))
        return line

    def _retract_scale(self):
        sv = self._sv(self.tool)
        fv = self._file(self.tool)
        if not sv.get('retract') or not fv.get('retract'):
            return None
        # Klipper's M221 scales every E move, retractions too
        return sv['retract'] / fv['retract'] / (self.factor or 1.)

    # --- command -------------------------------------------------------
    def cmd_FILAMENT_OVERRIDE(self, gcmd):
        enable = gcmd.get_int('ENABLE', None, minval=0, maxval=1)
        if enable is not None:
            self.enabled = bool(enable)
            try:
                self._save_enabled()
            except OSError as e:
                raise gcmd.error("Could not save the setting: %s" % (e,))
            gcmd.respond_info("Loaded filament's settings over the file's: %s"
                              % ("ON" if self.enabled else "OFF"))
            return
        if gcmd.get_int('PRINT', 1, minval=0, maxval=1) == 0:
            self.skip = 'this print asked for its own values'
            self.active = False
            gcmd.respond_info("Filament settings: this print runs as "
                              "sliced")
            return
        gcmd.respond_info(
            "Filament settings over the file's: %s; this print: %s%s"
            % ("ON" if self.enabled else "OFF",
               'active' if self.active else (self.skip or 'not started'),
               (' (slot %s, T%d)' % (self.applied.get('slot'), self.tool))
               if self.active else ''))


def load_config(config):
    return HarkFilamentOverride(config)
