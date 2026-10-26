# BED_MESH_CHECK: refuse a print on an implausible bed mesh
#
# Idea and syntax from Kalico's BED_MESH_CHECK (KalicoCrew/kalico, GPLv3),
# written for mainline bed_mesh. Checks the probed points, not the
# interpolated mesh, so one bad tap shows at its real size.
#
#   BED_MESH_CHECK [MAX_DEVIATION=<mm>] [MAX_SLOPE=<mm per mm>]
#
# Copyright (C) 2026  Hark Tech
#
# This file may be distributed under the terms of the GNU GPLv3 license.


class HarkMeshCheck:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.last = {}
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command('BED_MESH_CHECK', self.cmd_BED_MESH_CHECK,
                               desc=self.cmd_BED_MESH_CHECK_help)

    def measure(self, z_mesh):
        matrix = z_mesh.get_probed_matrix()
        params = z_mesh.get_mesh_params()
        if not matrix or not matrix[0]:
            raise self.printer.command_error("The loaded mesh has no points")
        rows, cols = len(matrix), len(matrix[0])
        flat = [z for row in matrix for z in row]
        result = {'deviation': max(flat) - min(flat), 'slope': 0.,
                  'slope_at': None, 'points': rows * cols}
        x_step = ((params['max_x'] - params['min_x']) / (cols - 1)
                  if cols > 1 else 0.)
        y_step = ((params['max_y'] - params['min_y']) / (rows - 1)
                  if rows > 1 else 0.)
        pairs = []
        if x_step > 0.:
            pairs += [((r, c), (r, c + 1), x_step)
                      for r in range(rows) for c in range(cols - 1)]
        if y_step > 0.:
            pairs += [((r, c), (r + 1, c), y_step)
                      for r in range(rows - 1) for c in range(cols)]
        for (r1, c1), (r2, c2), step in pairs:
            slope = abs(matrix[r2][c2] - matrix[r1][c1]) / step
            if slope > result['slope']:
                result['slope'] = slope
                # Bed coordinates of the steeper pair, for the message
                result['slope_at'] = tuple(
                    (params['min_x'] + c * x_step, params['min_y'] + r * y_step)
                    for r, c in ((r1, c1), (r2, c2)))
        return result

    cmd_BED_MESH_CHECK_help = (
        "Check the loaded bed mesh: MAX_DEVIATION= (mm), MAX_SLOPE= (mm/mm)")

    def cmd_BED_MESH_CHECK(self, gcmd):
        max_dev = gcmd.get_float('MAX_DEVIATION', None, above=0.)
        max_slope = gcmd.get_float('MAX_SLOPE', None, above=0.)
        bed_mesh = self.printer.lookup_object('bed_mesh', None)
        z_mesh = bed_mesh.get_mesh() if bed_mesh is not None else None
        if z_mesh is None:
            raise gcmd.error("BED_MESH_CHECK: no bed mesh is loaded")
        res = self.measure(z_mesh)
        self.last = dict(res, ok=True)
        where = ""
        if res['slope_at']:
            (x1, y1), (x2, y2) = res['slope_at']
            where = " between X%.0f Y%.0f and X%.0f Y%.0f" % (x1, y1, x2, y2)
        msg = ("Bed mesh check (%d points): deviation %.3fmm%s, steepest "
               "slope %.4f mm/mm%s%s"
               % (res['points'], res['deviation'],
                  " (max %.3f)" % max_dev if max_dev else "",
                  res['slope'],
                  " (max %.4f)" % max_slope if max_slope else "", where))
        failed = []
        if max_dev is not None and res['deviation'] > max_dev:
            failed.append("deviation")
        if max_slope is not None and res['slope'] > max_slope:
            failed.append("slope")
        if failed:
            self.last['ok'] = False
            raise gcmd.error("%s\nBed mesh %s over the limit: check the "
                             "nozzle tip and the plate, then re-run"
                             % (msg, " and ".join(failed)))
        gcmd.respond_info(msg)

    def get_status(self, eventtime):
        return dict(self.last)


def load_config(config):
    return HarkMeshCheck(config)
