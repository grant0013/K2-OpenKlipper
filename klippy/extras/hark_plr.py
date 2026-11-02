# Power-loss recovery for the K2
#
# Stock K2 firmware could resume a print after the power went off; this is
# the open stack's version, after Jacob10383's power_loss_recovery (GPLv3,
# github.com/Jacob10383/k2-plus-custom-firmware) and Prusa's power panic.
#
# While printing, the position in the G-code file and the print's state
# (G-code positions and offsets, temperatures, fans, pressure advance, speed
# limits, the bed mesh, excluded objects, the CFS slot) are noted right
# after a plain move line (G0-G3) has been read, at most every
# candidate_interval. Never inside a macro: in the middle of a colour change
# the G-code position is the chute. A note only counts once the toolhead
# has actually made the moves before it, and the newest such note is
# written to the journal (state_path) every checkpoint_interval. A finished or cancelled
# print deletes the journal; an error (power loss: the MCUs went away)
# keeps it.
#
# After a restart with a journal the console and touchscreen offer
# PLR_RESUME CONFIRM=1 (or PLR_DISCARD). Resuming never taps the nozzle on
# the bed (the part is in the way): Z comes back from the bottom switches.
# k2_z_align notes each Z motor's step count at the switches, and the
# journal holds where that point lies in the nozzle-tap frame the print
# used, so KZ_ALIGN_BOTTOM followed by that offset gives the print's own Z.
# Then: home X and Y, heat the bed, raise the bed to clear the part with the
# nozzle over the chute, heat and prime (the CFS slot loaded and purged, or
# the rear spool primed), wipe, go back over the part, down to the saved
# height, restore the print's state and carry on from the saved place in
# the file. Up to checkpoint_interval seconds of the print are printed
# twice; nothing is skipped.
#
# Supervise the first resumes. On a K2 Plus a Z tilt correction made after
# the switches is only kept on average (warned when the two Z motors
# differ by more than tilt_warn).
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import json
import logging
import math
import os
import time
import uuid


class _LineWatch:
    """Stands in for virtual_sdcard's gcode object: runs each line as
    before, then tells hark_plr. Everything else passes straight through."""
    def __init__(self, delegate, owner):
        self._delegate = delegate
        self._owner = owner

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def run_script(self, script):
        result = self._delegate.run_script(script)
        self._owner.after_line(script)
        return result


class HarkPLR:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object('gcode')
        config_file = self.printer.get_start_args().get('config_file', '')
        self.state_path = config.get(
            'state_path', os.path.join(
                os.path.dirname(config_file)
                or '/mnt/UDISK/printer_data_mainline/config',
                'hark_plr.json'))
        self.enabled = config.getboolean('enabled', True)
        self.candidate_interval = config.getfloat(
            'candidate_interval', .5, minval=.1, maxval=5.)
        self.checkpoint_interval = config.getfloat(
            'checkpoint_interval', 10., minval=.5, maxval=120.)
        self.recovery_lift = config.getfloat(
            'recovery_lift', 3., minval=1., maxval=20.)
        self.nozzle_standby = config.getfloat(
            'nozzle_standby', 140., minval=0., maxval=200.)
        self.travel_speed = config.getfloat(
            'travel_speed', 100., above=0., maxval=300.)
        self.z_speed = config.getfloat('z_speed', 10., above=0., maxval=30.)
        self.prime_length = config.getfloat(
            'prime_length', 40., minval=0., maxval=150.)
        self.tilt_warn = config.getfloat(
            'tilt_warn', .05, minval=0., maxval=1.)
        # The print's own moves are in the toolhead queue for about this
        # long before they are made (lookahead); a note matures after that
        self.mature_margin = config.getfloat(
            'mature_margin', 1., minval=0., maxval=5.)
        self.last_note = 0.
        self.session = None
        self.candidates = []
        self.safe = None
        self.last_written = None
        self.last_write_time = 0.
        self.max_z = 0.
        self.was_state = None
        self.resuming = False
        self.journal = None
        self.timer = None
        self.status = {'enabled': self.enabled, 'available': False,
                       'filename': '', 'z': 0., 'saved_at': 0.,
                       'checkpoints': 0, 'last_error': ''}
        self.printer.register_event_handler('klippy:ready', self._ready)
        for name, fn, desc in (
                ('PLR_STATUS', self.cmd_PLR_STATUS,
                 "Report the power-loss recovery journal"),
                ('PLR_RESUME', self.cmd_PLR_RESUME,
                 "Resume the print a power cut stopped (CONFIRM=1)"),
                ('PLR_DISCARD', self.cmd_PLR_DISCARD,
                 "Forget the print a power cut stopped")):
            self.gcode.register_command(name, fn, desc=desc)

    # --- journal ---------------------------------------------------------

    def _read_journal(self):
        try:
            with open(self.state_path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, IOError, ValueError):
            return None
        if not isinstance(data, dict) or data.get('version') != 1:
            return None
        return data

    def _write_journal(self, data):
        tmp = self.state_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path)

    def _delete_journal(self):
        for path in (self.state_path, self.state_path + '.tmp'):
            try:
                os.remove(path)
            except OSError:
                pass
        self.journal = None
        self.status.update(available=False, filename='', z=0.,
                           saved_at=0.)

    def _offer(self, journal):
        self.journal = journal
        self.status.update(
            available=True, filename=journal['file']['path'],
            z=round(journal['gcode']['last_position'][2], 2),
            saved_at=journal.get('saved_at', 0.))

    # --- following the print -------------------------------------------

    def _ready(self):
        self.print_stats = self.printer.lookup_object('print_stats')
        self.v_sd = self.printer.lookup_object('virtual_sdcard')
        self.gcode_move = self.printer.lookup_object('gcode_move')
        self.toolhead = self.printer.lookup_object('toolhead')
        journal = self._read_journal()
        if journal is not None:
            self._offer(journal)
            self.gcode.respond_info(
                "Power-loss recovery: %s stopped at Z%.2f. PLR_RESUME"
                " CONFIRM=1 carries on from there (bed clear around the"
                " part, filament loaded); PLR_DISCARD forgets it."
                % (journal['file']['path'],
                   journal['gcode']['last_position'][2]))
        if self.enabled and self.timer is None:
            # See every line virtual_sdcard runs, after it has run
            if not isinstance(self.v_sd.gcode, _LineWatch):
                self.v_sd.gcode = _LineWatch(self.v_sd.gcode, self)
            self.timer = self.reactor.register_timer(
                self._tick, self.reactor.monotonic() + 2.)

    def _tick(self, eventtime):
        try:
            self._follow(eventtime)
        except Exception as exc:
            logging.exception("hark_plr: checkpoint failed")
            self.status['last_error'] = str(exc)[:200]
        return eventtime + self.candidate_interval

    def _follow(self, eventtime):
        state = self.print_stats.get_status(eventtime).get('state')
        previous, self.was_state = self.was_state, state
        if state == 'printing' and previous != 'printing':
            if self.resuming:
                self.resuming = False
            elif previous != 'paused':
                # A new print: the old journal no longer applies
                self._delete_journal()
                self.session = uuid.uuid4().hex
                self.candidates = []
                self.safe = None
                self.max_z = 0.
        if previous in ('printing', 'paused') and (
                state in ('complete', 'cancelled', 'standby')
                or (state == 'error' and not self.printer.is_shutdown())):
            # Finished, cancelled, or stopped by an error of its own. A
            # shutdown (the MCUs lost power) keeps the journal.
            self._delete_journal()
            self.session = None
            return
        if state != 'printing' or self.session is None:
            return
        if not self.v_sd.is_active():
            return
        self._promote(eventtime)
        if (self.safe is not None and self.safe is not self.last_written
                and eventtime - self.last_write_time
                >= self.checkpoint_interval):
            self._checkpoint(eventtime)

    def after_line(self, line):
        # Called by _LineWatch after each line from the file has run
        if self.session is None or self.resuming:
            return
        word = line.lstrip()[:3].upper()
        if word[:2] not in ('G0', 'G1', 'G2', 'G3') or word[2:3].isdigit():
            return
        eventtime = self.reactor.monotonic()
        if eventtime - self.last_note < self.candidate_interval:
            return
        self.last_note = eventtime
        try:
            self._note(eventtime)
        except Exception:
            logging.exception("hark_plr: note failed")

    def _note(self, eventtime):
        # After a move line: the G-code state includes its move, and the
        # file resumes at the next line (next_file_position). The move is
        # queued in the toolhead and done by print_time (plus the
        # lookahead, mature_margin).
        gm = self.gcode_move
        status = self.toolhead.get_status(eventtime)
        candidate = {
            'file_position': self.v_sd.next_file_position,
            'done_by': status['print_time'] + self.mature_margin,
            'gcode': {
                'absolute_coord': gm.absolute_coord,
                'absolute_extrude': gm.absolute_extrude,
                'base_position': list(gm.base_position),
                'last_position': list(gm.last_position),
                'homing_position': list(gm.homing_position),
                'speed': gm.speed, 'speed_factor': gm.speed_factor,
                'extrude_factor': gm.extrude_factor},
        }
        self.max_z = max(self.max_z, gm.last_position[2])
        self.candidates.append(candidate)
        del self.candidates[:-200]
        self._promote(eventtime)

    def _promote(self, eventtime):
        now = self.toolhead.get_status(eventtime)['estimated_print_time']
        while self.candidates and self.candidates[0]['done_by'] <= now:
            self.safe = self.candidates.pop(0)

    def _checkpoint(self, eventtime):
        sd_path = self.v_sd.file_path()
        root = os.path.realpath(self.v_sd.sdcard_dirname)
        real = os.path.realpath(sd_path)
        st = os.stat(real)
        safe = self.safe
        data = {
            'version': 1, 'session': self.session,
            'saved_at': time.time(),
            'file': {'path': os.path.relpath(real, root),
                     'size': st.st_size, 'mtime': st.st_mtime,
                     'position': safe['file_position']},
            'gcode': safe['gcode'],
            'max_z': self.max_z,
        }
        data.update(self._machine_state(eventtime))
        self._write_journal(data)
        self.last_written = safe
        self.last_write_time = eventtime
        if not self.status['checkpoints']:
            logging.info("hark_plr: first checkpoint of %s at byte %d, Z%.2f",
                         data['file']['path'], safe['file_position'],
                         safe['gcode']['last_position'][2])
        self.status['checkpoints'] += 1

    def _machine_state(self, eventtime):
        out = {}
        heaters = {}
        for name in ('extruder', 'heater_bed', 'heater_generic '
                     'chamber_heater'):
            obj = self.printer.lookup_object(name, None)
            if obj is not None:
                heaters[name] = obj.get_status(eventtime).get('target', 0.)
        out['heaters'] = heaters
        fans = {}
        for name in ('fan', 'fan_generic auxiliary_fan'):
            obj = self.printer.lookup_object(name, None)
            if obj is not None:
                fans[name] = obj.get_status(eventtime).get('speed', 0.)
        out['fans'] = fans
        ext = self.printer.lookup_object('extruder').get_status(eventtime)
        out['pressure_advance'] = [ext.get('pressure_advance', 0.),
                                   ext.get('smooth_time', .04)]
        th = self.toolhead.get_status(eventtime)
        out['limits'] = [th['max_velocity'], th['max_accel'],
                         th.get('minimum_cruise_ratio', .5),
                         th['square_corner_velocity']]
        out['mesh'] = None
        bed_mesh = self.printer.lookup_object('bed_mesh', None)
        mesh = bed_mesh.get_mesh() if bed_mesh is not None else None
        if mesh is not None:
            out['mesh'] = {'name': mesh.get_profile_name(),
                           'params': dict(mesh.get_mesh_params()),
                           'matrix': mesh.get_probed_matrix()}
        excl = self.printer.lookup_object('exclude_object', None)
        out['excluded'] = list(excl.get_status(eventtime).get(
            'excluded_objects', [])) if excl is not None else []
        cfs = self.printer.lookup_object('k2_cfs', None)
        slot = None
        if cfs is not None and cfs.fitted:
            cs = cfs.get_status(eventtime)
            slot = cs.get('feed_slot') or cs.get('active_slot')
        out['cfs_slot'] = slot
        out['z_frame'] = self._z_frame()
        return out

    def _z_frame(self):
        # Where the bottom switches are in the nozzle-tap frame, per Z motor
        z_align = self.printer.lookup_object('k2_z_align', None)
        bottom = getattr(z_align, 'bottom_mcu', None)
        if not bottom:
            return None
        frame = {}
        for s in self.toolhead.get_kinematics().rails[2].get_steppers():
            if s.get_name() not in bottom:
                return None
            frame[s.get_name()] = s.mcu_to_commanded_position(
                bottom[s.get_name()])
        return {'motors': frame,
                'position_endstop': z_align.position_endstop}

    # --- commands ------------------------------------------------------

    def cmd_PLR_STATUS(self, gcmd):
        journal = self.journal or self._read_journal()
        if journal is None:
            gcmd.respond_info(
                "Power-loss recovery: %s, no interrupted print (%d"
                " checkpoints this session)"
                % ("on" if self.enabled else "off",
                   self.status['checkpoints']))
            return
        g = journal['gcode']['last_position']
        gcmd.respond_info(
            "Power-loss recovery: %s at byte %d of %d, X%.1f Y%.1f Z%.2f,"
            " slot %s, saved %s"
            % (journal['file']['path'], journal['file']['position'],
               journal['file']['size'], g[0], g[1], g[2],
               journal.get('cfs_slot') or '-',
               time.strftime('%Y-%m-%d %H:%M:%S',
                             time.localtime(journal.get('saved_at', 0)))))

    def cmd_PLR_DISCARD(self, gcmd):
        self._delete_journal()
        gcmd.respond_info("Power-loss recovery: interrupted print forgotten")

    def _validate(self, journal):
        state = self.print_stats.get_status(
            self.reactor.monotonic()).get('state')
        if state in ('printing', 'paused'):
            raise ValueError("a print is running")
        root = os.path.realpath(self.v_sd.sdcard_dirname)
        path = os.path.realpath(os.path.join(root, journal['file']['path']))
        if os.path.commonpath((root, path)) != root:
            raise ValueError("the saved file is outside the G-code folder")
        try:
            st = os.stat(path)
        except OSError:
            raise ValueError("%s is gone" % journal['file']['path'])
        if (st.st_size != journal['file']['size']
                or abs(st.st_mtime - journal['file']['mtime']) > 1.):
            raise ValueError("%s has changed since the print"
                             % journal['file']['path'])
        pos = journal['file']['position']
        if not 0 < pos <= st.st_size:
            raise ValueError("bad file position %r" % (pos,))
        values = (journal['gcode']['last_position']
                  + journal['gcode']['base_position']
                  + journal['gcode']['homing_position'])
        if not all(isinstance(v, (int, float)) and math.isfinite(v)
                   for v in values):
            raise ValueError("bad saved coordinates")
        if journal['heaters'].get('extruder', 0.) < 150.:
            raise ValueError("the nozzle was not at print temperature")
        frame = journal.get('z_frame')
        if not frame or not frame.get('motors'):
            raise ValueError(
                "no Z reference was saved (the print did not start from"
                " the bottom switches in that session)")
        return journal['file']['path']

    def cmd_PLR_RESUME(self, gcmd):
        if gcmd.get_int('CONFIRM', 0, minval=0, maxval=1) != 1:
            raise gcmd.error(
                "PLR_RESUME moves the bed and heats up: check the part is"
                " still stuck down and nothing is on the bed around it,"
                " then PLR_RESUME CONFIRM=1")
        journal = self.journal or self._read_journal()
        if journal is None:
            raise gcmd.error("No interrupted print to resume")
        try:
            path = self._validate(journal)
        except ValueError as exc:
            raise gcmd.error("Cannot resume: %s" % exc)
        try:
            self._restore(gcmd, journal)
        except Exception as exc:
            logging.exception("hark_plr: resume failed")
            self.status['last_error'] = str(exc)[:200]
            try:
                self._run("M104 S0")
            except Exception:
                pass
            raise gcmd.error(
                "Resume stopped: %s. The journal is kept: sort it out and"
                " PLR_RESUME CONFIRM=1 again, or PLR_DISCARD." % (exc,))
        # Carry on from the saved place in the file
        self.resuming = True
        self.session = journal['session']
        self.max_z = journal.get('max_z', 0.)
        self.candidates = []
        self.safe = None
        self.last_written = None
        self._run("M23 %s" % (path,))
        self._run("M26 S%d" % (journal['file']['position'],))
        self.status['available'] = False
        gcmd.respond_info("Power-loss recovery: carrying on with %s" % path)
        self._run("M24")

    # --- the resume itself ---------------------------------------------

    def _run(self, script):
        self.gcode.run_script_from_command(script)

    def _restore(self, gcmd, journal):
        heaters = journal['heaters']
        g = journal['gcode']
        saved = [float(v) for v in g['last_position'][:3]]
        nozzle = float(heaters.get('extruder', 0.))
        bed = float(heaters.get('heater_bed', 0.))
        chamber = float(heaters.get('heater_generic chamber_heater', 0.))
        # Everything a resume needs from the slicer's start is in the
        # journal; start from plain defaults
        self._run("M107\nM220 S100\nM221 S100\nG90\nM83")
        self._run("M140 S%.1f\nM104 S%.1f"
                  % (bed, min(nozzle, self.nozzle_standby)))
        if chamber > 40. and self.printer.lookup_object(
                'heater_generic chamber_heater', None) is not None:
            self._run("CHAMBER_HEAT TARGET=%.1f" % chamber)
        # Z from the bottom switches (the bed goes away from the nozzle),
        # then into the frame the print used
        gcmd.respond_info("Power-loss recovery: Z from the bottom switches")
        self._run("KZ_ALIGN_BOTTOM CONFIRM=1")
        self._apply_z_frame(gcmd, journal['z_frame'])
        self._run("G28 X Y")
        if bed > 0.:
            gcmd.respond_info("Power-loss recovery: heating the bed")
            self._run("M190 S%.1f" % bed)
        self._restore_mesh(journal.get('mesh'))
        # Over the chute, raise the bed to clear the part
        clear_z = max(saved[2], float(journal.get('max_z', 0.))) \
            + self.recovery_lift
        cfs = self.printer.lookup_object('k2_cfs', None)
        purge_min_z = getattr(cfs, 'purge_min_z', 0.)
        self._run("CFS_PURGE_POSITION CONFIRM=1")
        self._run("G90\nG1 Z%.3f F%.1f"
                  % (max(clear_z, purge_min_z), self.z_speed * 60.))
        # Heat and prime: the print's filament fresh at the nozzle
        gcmd.respond_info("Power-loss recovery: heating the nozzle to %.0fC"
                          % nozzle)
        self._run("M109 S%.1f" % nozzle)
        slot = journal.get('cfs_slot')
        if cfs is not None and cfs.fitted and slot:
            self._run("CFS_CHANGE_SLOT SLOT=%s CONFIRM=1" % slot)
        elif self.prime_length:
            self._run("M83\nG1 E%.1f F180\nM400" % self.prime_length)
        self._run("G90\nG1 Z%.3f F%.1f"
                  % (max(clear_z, purge_min_z), self.z_speed * 60.))
        # The print's own settings
        pa = journal.get('pressure_advance')
        if pa:
            self._run("SET_PRESSURE_ADVANCE ADVANCE=%.6f SMOOTH_TIME=%.6f"
                      % (pa[0], pa[1]))
        lim = journal.get('limits')
        if lim:
            self._run("SET_VELOCITY_LIMIT VELOCITY=%.3f ACCEL=%.3f"
                      " MINIMUM_CRUISE_RATIO=%.3f"
                      " SQUARE_CORNER_VELOCITY=%.3f" % tuple(lim))
        for name in journal.get('excluded') or []:
            self._run("EXCLUDE_OBJECT NAME=%s" % name)
        # Over the part at the clear height, then straight down
        self._run("G1 X%.3f Y%.3f F%.1f\nG1 Z%.3f F%.1f\nM400"
                  % (saved[0], saved[1], self.travel_speed * 60.,
                     saved[2], self.z_speed * 60.))
        self._restore_gcode_state(g)
        for name, speed in (journal.get('fans') or {}).items():
            if name == 'fan':
                self._run("M106 S%d" % round(float(speed) * 255.))
            else:
                self._run("SET_FAN_SPEED FAN=%s SPEED=%.3f"
                          % (name.split()[-1], float(speed)))

    def _apply_z_frame(self, gcmd, frame):
        motors = frame['motors']
        names = [s.get_name() for s in
                 self.toolhead.get_kinematics().rails[2].get_steppers()]
        if set(names) != set(motors):
            raise ValueError("the Z motors differ from the saved ones")
        offsets = [float(motors[n]) for n in names]
        spread = max(offsets) - min(offsets)
        if spread > self.tilt_warn:
            gcmd.respond_info(
                "Power-loss recovery: the Z motors were %.3f mm apart in the"
                " print (Z tilt); the resume uses their average. Watch the"
                " first layer." % spread)
        bottom_in_print = sum(offsets) / len(offsets)
        pos = self.toolhead.get_position()
        # The switches are at position_endstop now and at bottom_in_print
        # in the print's frame
        pos[2] = pos[2] - float(frame['position_endstop']) + bottom_in_print
        self.toolhead.set_position(pos, homing_axes="z")

    def _restore_mesh(self, state):
        bed_mesh = self.printer.lookup_object('bed_mesh', None)
        if bed_mesh is None:
            return
        if not state:
            bed_mesh.set_mesh(None)
            return
        from . import bed_mesh as bed_mesh_module
        mesh = bed_mesh_module.ZMesh(dict(state['params']), state['name'])
        mesh.build_mesh(state['matrix'])
        bed_mesh.set_mesh(mesh)

    def _restore_gcode_state(self, state):
        name = "_HARK_PLR"
        self.gcode_move.saved_states[name] = {
            'absolute_coord': state['absolute_coord'],
            'absolute_extrude': state['absolute_extrude'],
            'base_position': list(state['base_position']),
            'last_position': list(state['last_position']),
            'homing_position': list(state['homing_position']),
            'speed': state['speed'],
            'speed_factor': state['speed_factor'],
            'extrude_factor': state['extrude_factor'],
        }
        try:
            self._run("RESTORE_GCODE_STATE NAME=%s MOVE=0" % name)
        finally:
            self.gcode_move.saved_states.pop(name, None)

    def get_status(self, eventtime):
        return dict(self.status)


def load_config(config):
    return HarkPLR(config)
